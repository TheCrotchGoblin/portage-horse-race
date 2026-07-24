"""Reports, print views and CSV exports (spec §6.7, §14)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_session
from app.models import Payout, Placement, Team
from app.models.enums import PayoutStatus
from app.routes.deps import base_context
from app.services import dashboard as dashboard_service
from app.services import exports as export_service
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
    generated = sum(v["cents"] for v in by_status.values())
    paid = by_status.get(PayoutStatus.PAID, {}).get("cents", 0)
    unpaid = by_status.get(PayoutStatus.UNPAID, {}).get("cents", 0)
    return {"generated": generated, "paid": paid, "unpaid": unpaid, "by_status": by_status}


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
        ctx["grand"] = {
            "gross": sum(c.financials.gross_cents for c in board.cards),
            "club": sum(c.financials.club_share_cents for c in board.cards),
            "pool": sum(c.financials.prize_pool_cents for c in board.cards),
            "generated": sum(_payout_totals(session, c.team.id)["generated"] for c in board.cards),
            "paid": sum(_payout_totals(session, c.team.id)["paid"] for c in board.cards),
            "unpaid": sum(_payout_totals(session, c.team.id)["unpaid"] for c in board.cards),
        }
        return render(request, "reports/reconciliation.html", ctx)

    if kind == "results":
        from app.services import payouts as payout_service
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

    # default: team summary
    return render(request, "reports/team_summary.html", ctx)


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
