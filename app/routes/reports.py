"""Reports, print views and CSV exports (spec §6.7, §14)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_session
from app.models import Payout, Placement, Team
from app.models.enums import PayoutStatus
from app.routes.deps import base_context
from app.services import dashboard as dashboard_service
from app.services import event_package
from app.services import exports as export_service
from app.services import payouts as payout_service
from app.templating import render

router = APIRouter(prefix="/reports")


def _payout_totals(session: Session, team_id: int) -> dict:
    rows = session.execute(
        select(Payout.status, func.count(Payout.id), func.coalesce(func.sum(Payout.amount_cents), 0))
        .join(Placement, Payout.placement_id == Placement.id)
        .where(Placement.team_id == team_id)
        .group_by(Payout.status)
    ).all()
    by_status = {r[0]: {"count": int(r[1]), "cents": int(r[2])} for r in rows}
    paid = by_status.get(PayoutStatus.PAID, {}).get("cents", 0)
    unpaid = by_status.get(PayoutStatus.UNPAID, {}).get("cents", 0)
    reversed_ = by_status.get(PayoutStatus.REVERSED, {}).get("cents", 0)
    held = by_status.get(PayoutStatus.HELD, {}).get("cents", 0)
    return {
        "generated": paid + unpaid + reversed_ + held,  # self-consistent breakdown
        "paid": paid, "unpaid": unpaid, "reversed": reversed_, "held": held,
        "outstanding": unpaid + reversed_ + held,
        "by_status": by_status,
    }


def _handover_context(session: Session, tournament, board, ctx: dict) -> dict:
    """One-page Club Handover Statement: what the club keeps, what winners were
    paid, what's still owed, and the physical cash that should be in hand."""
    totals = [_payout_totals(session, c.team.id) for c in board.cards]
    gross = sum(c.financials.gross_cents for c in board.cards)
    club = sum(c.financials.club_share_cents for c in board.cards)
    pool = sum(c.financials.prize_pool_cents for c in board.cards)
    paid = sum(t["paid"] for t in totals)
    outstanding = sum(t["outstanding"] for t in totals)
    cash_paid = sum(c.drawer.cash_paid_out_cents for c in board.cards)
    float_total = sum(c.drawer.opening_float_cents for c in board.cards)
    any_counted = any(c.drawer.counted for c in board.cards)
    ctx["hand"] = {
        "gross": gross,
        "club": club,               # club's share, to deposit
        "pool": pool,
        "paid": paid,               # total prize money paid (any method)
        "outstanding": outstanding,  # still owed to winners
        "float": float_total,
        "cash_paid": cash_paid,      # prize money paid from the box in cash
        # Cash that should physically be in hand right now:
        # float + everything taken in − cash already paid out.
        "cash_on_hand_expected": float_total + gross - cash_paid,
        "counted": sum((c.drawer.counted_cents or 0) for c in board.cards) if any_counted else None,
        "any_counted": any_counted,
    }
    return ctx


@router.get("")
def index(request: Request, session: Session = Depends(get_session)):
    ctx = base_context(request, session, "reports")
    if ctx["tournament"] is None:
        return RedirectResponse("/setup/new", status_code=303)
    return render(request, "reports/index.html", ctx)


@router.get("/print/{kind}")
def print_view(request: Request, kind: str, session: Session = Depends(get_session)):
    ctx = base_context(request, session, "reports")
    tournament = ctx["tournament"]
    if tournament is None:
        return RedirectResponse("/setup/new", status_code=303)

    board = dashboard_service.build_dashboard(session, tournament)
    ctx["board"] = board

    if kind == "reconciliation":
        recon = []
        for card in board.cards:
            totals = _payout_totals(session, card.team.id)
            unclaimed = card.financials.prize_pool_cents - totals["generated"] if totals["generated"] else 0
            recon.append({"card": card, "totals": totals, "unclaimed": max(unclaimed, 0)})
        ctx["recon"] = recon
        any_counted = any(c.drawer.counted for c in board.cards)
        ctx["grand"] = {
            "gross": sum(c.financials.gross_cents for c in board.cards),
            "club": sum(c.financials.club_share_cents for c in board.cards),
            "pool": sum(c.financials.prize_pool_cents for c in board.cards),
            "generated": sum(r["totals"]["generated"] for r in recon),
            "paid": sum(r["totals"]["paid"] for r in recon),
            "unpaid": sum(r["totals"]["unpaid"] for r in recon),
            "reversed": sum(r["totals"]["reversed"] for r in recon),
            "outstanding": sum(r["totals"]["outstanding"] for r in recon),
            # Cash drawer roll-up
            "float": sum(c.drawer.opening_float_cents for c in board.cards),
            "cash_paid": sum(c.drawer.cash_paid_out_cents for c in board.cards),
            "expected": sum(c.drawer.expected_cents for c in board.cards),
            "any_counted": any_counted,
            "counted": sum((c.drawer.counted_cents or 0) for c in board.cards) if any_counted else None,
            "variance": sum((c.drawer.variance_cents or 0) for c in board.cards) if any_counted else None,
        }
        return render(request, "reports/reconciliation.html", ctx)

    if kind == "handover":
        return render(request, "reports/handover.html", _handover_context(session, tournament, board, ctx))

    if kind == "results":
        teams = session.scalars(
            select(Team).where(Team.tournament_id == tournament.id).order_by(Team.id)
        ).all()
        results = []
        for team in teams:
            previews = payout_service.placement_previews(session, tournament, team)
            rows = []
            for pv in previews:
                # Disclose the deterministic remainder-cent allocation (BR-12 / §8.3):
                # `remainder` entries (the earliest by time) each receive one extra cent.
                remainder = pv.pool_cents - pv.per_entry_cents * pv.winning_entries if pv.winning_entries else 0
                rows.append({"pv": pv, "remainder": remainder})
            results.append({"team": team, "rows": rows})
        ctx["results"] = results
        return render(request, "reports/results.html", ctx)

    if kind == "payouts":
        ctx["payouts"] = session.scalars(
            select(Payout).join(Placement, Payout.placement_id == Placement.id)
            .join(Team, Placement.team_id == Team.id)
            .where(Team.tournament_id == tournament.id)
            .order_by(Team.id, Placement.position)
        ).all()
        return render(request, "reports/payout_register.html", ctx)

    if kind in ("winner-notices", "call-sheet"):
        unpaid = session.scalars(
            select(Payout).join(Placement, Payout.placement_id == Placement.id)
            .join(Team, Placement.team_id == Team.id)
            .where(Team.tournament_id == tournament.id, Payout.status == PayoutStatus.UNPAID)
            .order_by(Payout.amount_cents.desc())
        ).all()
        ctx["winners"] = unpaid
        ctx["contact_statuses"] = payout_service.CONTACT_STATUSES
        template = "reports/winner_notices.html" if kind == "winner-notices" else "reports/call_sheet.html"
        return render(request, template, ctx)

    # default: team summary
    return render(request, "reports/team_summary.html", ctx)


@router.post("/settlement-package")
def settlement_package(request: Request, session: Session = Depends(get_session)):
    ctx = base_context(request, session, "reports")
    tournament = ctx["tournament"]
    if tournament is None:
        return RedirectResponse("/setup/new", status_code=303)
    path = event_package.build_settlement_package(session, tournament, request.app.state.settings)
    return FileResponse(path, filename=path.name, media_type="application/zip")


@router.get("/export/{kind}")
def export_csv(request: Request, kind: str, session: Session = Depends(get_session)):
    ctx = base_context(request, session, "reports")
    tournament = ctx["tournament"]
    if tournament is None:
        return RedirectResponse("/setup/new", status_code=303)
    try:
        content = export_service.build_csv(kind, session, tournament.id)
    except KeyError:
        return PlainTextResponse("Unknown export type", status_code=404)
    filename = f"{kind}.csv"
    return PlainTextResponse(
        content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
