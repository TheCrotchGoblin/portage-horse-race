"""Cashier fast-entry screen and wager recording (spec §6.3, §7.2)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_session
from app.formatting import dollars_to_cents
from app.models import Customer, Player, Team
from app.models.enums import TournamentStatus, WageringStatus
from app.routes.deps import admin_pin_ok, base_context, operator_name
from app.services import customers as customer_service
from app.services import teams as team_service
from app.services import wagers as wager_service
from app.services.wagers import WagerError
from app.templating import flash, render

router = APIRouter()


def _players_in_order(session: Session, team_id: int) -> list[team_service.PlayerTotal]:
    totals = team_service.player_totals(session, team_id)
    return sorted(totals, key=lambda t: (t.player.display_order, t.player.id))


@router.get("/cashier")
def cashier(
    request: Request,
    session: Session = Depends(get_session),
    customer_id: int | None = None,
    team_id: int | None = None,
    player_id: int | None = None,
    qty: int = 1,
):
    ctx = base_context(request, session, "cashier")
    tournament = ctx["tournament"]
    if tournament is None:
        return RedirectResponse("/setup/new", status_code=303)

    teams = session.scalars(
        select(Team).where(Team.tournament_id == tournament.id).order_by(Team.id)
    ).all()

    selected_customer = session.get(Customer, customer_id) if customer_id else None
    selected_team = session.get(Team, team_id) if team_id else None
    if selected_team and selected_team.tournament_id != tournament.id:
        selected_team = None
    selected_player = session.get(Player, player_id) if player_id else None
    if selected_player and selected_team and selected_player.team_id != selected_team.id:
        selected_player = None

    qty = max(1, min(qty, wager_service.MAX_QUANTITY))
    amount_due = qty * tournament.entry_price_cents

    ctx.update(
        {
            "teams": teams,
            "any_open": any(t.wagering_status == WageringStatus.OPEN for t in teams),
            "tournament_open": tournament.status == TournamentStatus.OPEN,
            "selected_customer": selected_customer,
            "selected_team": selected_team,
            "selected_player": selected_player,
            "team_players": _players_in_order(session, selected_team.id) if selected_team else [],
            "qty": qty,
            "amount_due": amount_due,
            "recent": wager_service.recent(session, tournament.id, limit=8),
            "WageringStatus": WageringStatus,
        }
    )
    return render(request, "cashier/index.html", ctx)


@router.get("/cashier/search")
def cashier_search(request: Request, session: Session = Depends(get_session), q: str = ""):
    """HTMX partial: customer matches with a 'select for cashier' action."""
    ctx = base_context(request, session, "cashier")
    ctx["results"] = customer_service.search(session, q, limit=12)
    ctx["q"] = q
    return render(request, "cashier/_search_results.html", ctx)


@router.post("/wagers")
def record_wager(
    request: Request,
    session: Session = Depends(get_session),
    customer_id: int = Form(...),
    team_id: int = Form(...),
    player_id: int = Form(...),
    quantity: int = Form(...),
    received: str = Form(""),
):
    ctx = base_context(request, session, "cashier")
    tournament = ctx["tournament"]
    received_cents = None
    if received.strip():
        try:
            received_cents = dollars_to_cents(received)
        except ValueError:
            flash(request, "Amount received is not a valid dollar amount.", "danger")
            return _back(customer_id, team_id, player_id, quantity)
    try:
        wager = wager_service.record_wager(
            session,
            tournament=tournament,
            team_id=team_id,
            player_id=player_id,
            customer_id=customer_id,
            quantity=quantity,
            received_cents=received_cents,
            operator=operator_name(request),
        )
    except WagerError as exc:
        flash(request, str(exc), "danger")
        return _back(customer_id, team_id, player_id, quantity)

    flash(request, f"Recorded {wager.quantity} entrie(s) — {_dollars(wager.amount_cents)}. Ready for the next customer.")
    # Clear selection and return focus to customer search (spec FR-046).
    return RedirectResponse("/cashier", status_code=303)


@router.post("/wagers/{wager_id}/void")
def void_wager(
    request: Request,
    wager_id: int,
    session: Session = Depends(get_session),
    reason: str = Form(...),
    admin_pin: str = Form(""),
    return_to: str = Form("/cashier"),
):
    from app.models import Wager

    if not admin_pin_ok(session, admin_pin):
        flash(request, "Incorrect administrator PIN — the wager was not voided.", "danger")
        return RedirectResponse(return_to, status_code=303)
    wager = session.get(Wager, wager_id)
    if wager is None:
        flash(request, "Wager not found.", "danger")
        return RedirectResponse(return_to, status_code=303)
    try:
        wager_service.void_wager(session, wager, reason=reason, operator=operator_name(request))
    except WagerError as exc:
        flash(request, str(exc), "danger")
        return RedirectResponse(return_to, status_code=303)
    flash(request, "Wager voided. Totals have been updated.")
    return RedirectResponse(return_to, status_code=303)


def _back(customer_id, team_id, player_id, quantity) -> RedirectResponse:
    return RedirectResponse(
        f"/cashier?customer_id={customer_id}&team_id={team_id}&player_id={player_id}&qty={quantity}",
        status_code=303,
    )


def _dollars(cents: int) -> str:
    from app.formatting import cents_to_dollars

    return cents_to_dollars(cents)
