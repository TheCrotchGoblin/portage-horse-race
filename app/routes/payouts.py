"""Payout settlement routes (spec §6.6, §7.5)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_session
from app.models import Payout, Placement, Team
from app.models.enums import PayoutStatus
from app.routes.deps import admin_pin_ok, base_context, operator_name
from app.services import payouts as payout_service
from app.services.payouts import PayoutError
from app.templating import flash, render

router = APIRouter(prefix="/payouts")


@router.get("")
def payouts(request: Request, session: Session = Depends(get_session)):
    ctx = base_context(request, session, "payouts")
    tournament = ctx["tournament"]
    if tournament is None:
        return RedirectResponse("/setup/new", status_code=303)
    rows = session.scalars(
        select(Payout)
        .join(Placement, Payout.placement_id == Placement.id)
        .join(Team, Placement.team_id == Team.id)
        .where(Team.tournament_id == tournament.id)
        .order_by(Payout.status, Payout.amount_cents.desc())
    ).all()
    ctx.update({
        "unpaid": [p for p in rows if p.status == PayoutStatus.UNPAID],
        "settled": [p for p in rows if p.status != PayoutStatus.UNPAID],
        "unpaid_total": sum(p.amount_cents for p in rows if p.status == PayoutStatus.UNPAID),
        "paid_total": sum(p.amount_cents for p in rows if p.status == PayoutStatus.PAID),
        "unclaimed": payout_service.unclaimed_placements(session, tournament.id),
        "dispositions": payout_service.DISPOSITIONS,
        "contact_statuses": payout_service.CONTACT_STATUSES,
        "event_settled": tournament.is_settled,
        "settle_blockers": payout_service.settlement_status_blockers(session, tournament),
    })
    return render(request, "payouts/index.html", ctx)


@router.post("/{payout_id}/contact")
def contact(request: Request, payout_id: int, session: Session = Depends(get_session),
            contact_status: str = Form(...), note: str = Form("")):
    payout = session.get(Payout, payout_id)
    if payout is None:
        flash(request, "Payout not found.", "danger")
        return RedirectResponse("/payouts", status_code=303)
    try:
        payout_service.set_contact(session, payout, status=contact_status, note=note,
                                   operator=operator_name(request))
    except PayoutError as exc:
        flash(request, str(exc), "danger")
        return RedirectResponse("/payouts", status_code=303)
    flash(request, "Contact recorded.")
    return RedirectResponse("/payouts", status_code=303)


@router.post("/reopen")
def reopen(request: Request, session: Session = Depends(get_session),
           reason: str = Form(...), admin_pin: str = Form("")):
    ctx = base_context(request, session, "payouts")
    tournament = ctx["tournament"]
    if tournament is None:
        return RedirectResponse("/setup/new", status_code=303)
    if not admin_pin_ok(session, admin_pin):
        flash(request, "Incorrect administrator PIN.", "danger")
        return RedirectResponse("/payouts", status_code=303)
    try:
        payout_service.reopen_settlement(session, tournament, operator=operator_name(request), reason=reason)
    except PayoutError as exc:
        flash(request, str(exc), "danger")
        return RedirectResponse("/payouts", status_code=303)
    flash(request, "Event reopened for corrections. Remember to re-settle when done.")
    return RedirectResponse("/payouts", status_code=303)


@router.post("/placements/{placement_id}/dispose")
def dispose(
    request: Request,
    placement_id: int,
    session: Session = Depends(get_session),
    disposition: str = Form(...),
    note: str = Form(""),
    admin_pin: str = Form(""),
):
    ctx = base_context(request, session, "payouts")
    placement = session.get(Placement, placement_id)
    if placement is None:
        flash(request, "Placement not found.", "danger")
        return RedirectResponse("/payouts", status_code=303)
    if not admin_pin_ok(session, admin_pin):
        flash(request, "Incorrect administrator PIN.", "danger")
        return RedirectResponse("/payouts", status_code=303)
    try:
        # set_disposition enforces the settlement lock (raises PayoutError if locked).
        payout_service.set_disposition(
            session, placement, disposition=disposition, note=note, operator=operator_name(request)
        )
        payout_service.check_settled(session, ctx["tournament"])
    except PayoutError as exc:
        flash(request, str(exc), "danger")
        return RedirectResponse("/payouts", status_code=303)
    flash(request, "Unclaimed pool recorded.")
    return RedirectResponse("/payouts", status_code=303)


@router.post("/{payout_id}/pay")
def pay(
    request: Request,
    payout_id: int,
    session: Session = Depends(get_session),
    method: str = Form("cash"),
    note: str = Form(""),
):
    ctx = base_context(request, session, "payouts")
    payout = session.get(Payout, payout_id)
    if payout is None:
        flash(request, "Payout not found.", "danger")
        return RedirectResponse("/payouts", status_code=303)
    try:
        payout_service.pay_payout(session, payout, method=method, operator=operator_name(request), note=note)
        payout_service.check_settled(session, ctx["tournament"])
    except PayoutError as exc:
        flash(request, str(exc), "danger")
        return RedirectResponse("/payouts", status_code=303)
    flash(request, "Marked as paid.")
    return RedirectResponse("/payouts", status_code=303)


@router.post("/{payout_id}/reverse")
def reverse(
    request: Request,
    payout_id: int,
    session: Session = Depends(get_session),
    reason: str = Form(...),
    admin_pin: str = Form(""),
):
    payout = session.get(Payout, payout_id)
    if payout is None:
        flash(request, "Payout not found.", "danger")
        return RedirectResponse("/payouts", status_code=303)
    if not admin_pin_ok(session, admin_pin):
        flash(request, "Incorrect administrator PIN.", "danger")
        return RedirectResponse("/payouts", status_code=303)
    try:
        # reverse_payout enforces the settlement lock (raises PayoutError if locked).
        payout_service.reverse_payout(session, payout, reason=reason, operator=operator_name(request))
    except PayoutError as exc:
        flash(request, str(exc), "danger")
        return RedirectResponse("/payouts", status_code=303)
    flash(request, "Payment reversed.")
    return RedirectResponse("/payouts", status_code=303)
