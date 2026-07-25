"""Cashier fast-entry screen with a per-customer order cart (spec §6.3, §7.2).

One customer can have several entries against several players tallied into a
single order, paid once. Players are chosen from a searchable list so the screen
scales to large fields (80+ players per team).
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_session
from app.formatting import cents_to_dollars, dollars_to_cents, int_or_none
from app.models import Customer, Player, Team
from app.models.enums import TournamentStatus, WageringStatus
from app.routes.deps import admin_pin_ok, base_context, get_active_tournament, operator_name
from app.services import customers as customer_service
from app.services import teams as team_service
from app.services import wagers as wager_service
from app.services.wagers import WagerError
from app.templating import flash, render

router = APIRouter()


# --- cart helpers (stored in the signed session cookie) --------------------

def _get_cart(request: Request) -> dict:
    cart = request.session.get("cart")
    if not isinstance(cart, dict):
        cart = {"customer_id": None, "lines": []}
    cart.setdefault("customer_id", None)
    cart.setdefault("lines", [])
    return cart


def _save_cart(request: Request, cart: dict) -> None:
    request.session["cart"] = cart


@dataclass
class OpenPlayer:
    player: Player
    team: Team
    entries: int


def _open_players(session: Session, tournament_id: int, query: str = "") -> list[OpenPlayer]:
    """Players on OPEN teams (optionally name-filtered), with entries sold."""
    result: list[OpenPlayer] = []
    teams = session.scalars(
        select(Team).where(Team.tournament_id == tournament_id, Team.wagering_status == WageringStatus.OPEN)
        .order_by(Team.id)
    ).all()
    q = (query or "").strip().lower()
    for team in teams:
        for pt in team_service.player_totals(session, team.id):
            if q and q not in pt.player.name.lower():
                continue
            result.append(OpenPlayer(player=pt.player, team=team, entries=pt.entries))
    # Sort by name for a predictable list.
    result.sort(key=lambda op: op.player.name.lower())
    return result


def _cart_view(session: Session, tournament, cart: dict) -> tuple[list[dict], int, int]:
    """Expand cart lines into display rows; return (rows, total_cents, total_entries)."""
    rows: list[dict] = []
    total = 0
    entries = 0
    for i, ln in enumerate(cart["lines"]):
        player = session.get(Player, ln["player_id"])
        team = session.get(Team, ln["team_id"])
        if player is None or team is None:
            continue
        subtotal = ln["quantity"] * tournament.entry_price_cents
        total += subtotal
        entries += ln["quantity"]
        rows.append({"index": i, "player": player, "team": team,
                     "quantity": ln["quantity"], "subtotal": subtotal})
    return rows, total, entries


def _order_partial(request: Request, session: Session, tournament, cart: dict):
    """Render just the Order card — the HTMX swap target on add/remove so the
    player search box keeps its focus, text and scroll (no full page reload)."""
    selected_customer = session.get(Customer, cart["customer_id"]) if cart["customer_id"] else None
    rows, total, entries = _cart_view(session, tournament, cart)
    ctx = base_context(request, session, "cashier")
    ctx.update({
        "selected_customer": selected_customer,
        "cart_rows": rows, "cart_total": total, "cart_entries": entries,
    })
    return render(request, "cashier/_order.html", ctx)


# --- screen ----------------------------------------------------------------

@router.get("/cashier")
def cashier(request: Request, session: Session = Depends(get_session), customer_id: str | None = None):
    ctx = base_context(request, session, "cashier")
    tournament = ctx["tournament"]
    if tournament is None:
        return RedirectResponse("/setup/new", status_code=303)

    cart = _get_cart(request)
    # Selecting a customer (from search or new-customer) via query param.
    cid = int_or_none(customer_id)
    if cid is not None and cid != cart["customer_id"]:
        cart = {"customer_id": cid, "lines": []}  # a new customer starts a fresh order
        _save_cart(request, cart)

    selected_customer = session.get(Customer, cart["customer_id"]) if cart["customer_id"] else None
    if cart["customer_id"] and selected_customer is None:
        cart = {"customer_id": None, "lines": []}
        _save_cart(request, cart)

    rows, total, entries = _cart_view(session, tournament, cart)
    teams = session.scalars(select(Team).where(Team.tournament_id == tournament.id).order_by(Team.id)).all()

    ctx.update({
        "tournament_open": tournament.status == TournamentStatus.OPEN,
        "any_open": any(t.wagering_status == WageringStatus.OPEN for t in teams),
        "selected_customer": selected_customer,
        "players": _open_players(session, tournament.id) if selected_customer else [],
        "cart_rows": rows,
        "cart_total": total,
        "cart_entries": entries,
        "recent": wager_service.recent(session, tournament.id, limit=8),
        "last_customer": request.session.get("last_customer") if selected_customer is None else None,
        "last_order": request.session.get("last_order"),
    })
    return render(request, "cashier/index.html", ctx)


@router.get("/cashier/search")
def cashier_search(request: Request, session: Session = Depends(get_session), q: str = ""):
    ctx = base_context(request, session, "cashier")
    ctx["results"] = customer_service.search(session, q, limit=12)
    ctx["q"] = q
    return render(request, "cashier/_search_results.html", ctx)


@router.get("/cashier/players")
def player_search(request: Request, session: Session = Depends(get_session), q: str = ""):
    ctx = base_context(request, session, "cashier")
    tournament = ctx["tournament"]
    ctx["players"] = _open_players(session, tournament.id, q) if tournament else []
    ctx["q"] = q
    return render(request, "cashier/_players.html", ctx)


# --- cart mutations --------------------------------------------------------

@router.post("/cashier/cart/add")
def cart_add(
    request: Request,
    session: Session = Depends(get_session),
    team_id: str = Form(""),
    player_id: str = Form(""),
    quantity: str = Form("1"),
):
    cart = _get_cart(request)
    if not cart["customer_id"]:
        flash(request, "Choose a customer first.", "danger")
        return RedirectResponse("/cashier", status_code=303)
    tid, pid, qty = int_or_none(team_id), int_or_none(player_id), int_or_none(quantity)
    if not (tid and pid and qty and qty >= 1):
        flash(request, "Pick a player and a quantity of at least 1.", "danger")
        return RedirectResponse("/cashier", status_code=303)

    team = session.get(Team, tid)
    player = session.get(Player, pid)
    if team is None or player is None or player.team_id != tid:
        flash(request, "That player was not found on that team.", "danger")
        return RedirectResponse("/cashier", status_code=303)
    if team.wagering_status != WageringStatus.OPEN:
        flash(request, f"Wagering is closed for {team.name}.", "danger")
        return RedirectResponse("/cashier", status_code=303)

    for ln in cart["lines"]:  # merge repeat picks of the same player
        if ln["player_id"] == pid:
            ln["quantity"] += qty
            break
    else:
        cart["lines"].append({"team_id": tid, "player_id": pid, "quantity": qty})
    _save_cart(request, cart)
    if request.headers.get("HX-Request"):
        return _order_partial(request, session, get_active_tournament(session), cart)
    return RedirectResponse("/cashier", status_code=303)


@router.post("/cashier/cart/remove")
def cart_remove(request: Request, session: Session = Depends(get_session), index: str = Form("")):
    cart = _get_cart(request)
    i = int_or_none(index)
    if i is not None and 0 <= i < len(cart["lines"]):
        cart["lines"].pop(i)
    _save_cart(request, cart)
    if request.headers.get("HX-Request"):
        tournament = get_active_tournament(session)
        return _order_partial(request, session, tournament, cart)
    return RedirectResponse("/cashier", status_code=303)


@router.post("/cashier/reset")
def cart_reset(request: Request):
    request.session["cart"] = {"customer_id": None, "lines": []}
    return RedirectResponse("/cashier", status_code=303)


@router.post("/cashier/checkout")
def checkout(request: Request, session: Session = Depends(get_session), received: str = Form("")):
    ctx = base_context(request, session, "cashier")
    tournament = ctx["tournament"]
    cart = _get_cart(request)
    if not cart["customer_id"] or not cart["lines"]:
        flash(request, "The order is empty — add at least one entry.", "danger")
        return RedirectResponse("/cashier", status_code=303)

    _, total, entries = _cart_view(session, tournament, cart)
    if received.strip():
        try:
            received_cents = dollars_to_cents(received)
        except ValueError:
            flash(request, "Amount received is not a valid dollar amount.", "danger")
            return RedirectResponse("/cashier", status_code=303)
        if received_cents < total:
            flash(request, "Amount received is less than the total due.", "danger")
            return RedirectResponse("/cashier", status_code=303)

    reference = wager_service.new_reference()
    try:
        for ln in cart["lines"]:
            wager_service.record_wager(
                session,
                tournament=tournament,
                team_id=ln["team_id"],
                player_id=ln["player_id"],
                customer_id=cart["customer_id"],
                quantity=ln["quantity"],
                operator=operator_name(request),
                reference=reference,
            )
    except WagerError as exc:
        session.rollback()  # keep the whole order atomic — nothing is recorded
        flash(request, f"{exc} Nothing was recorded — please review the order.", "danger")
        return RedirectResponse("/cashier", status_code=303)

    # Remember the customer for a quick repeat order (POS-03), but clear the cart
    # so a stray refresh can't duplicate the sale.
    customer = session.get(Customer, cart["customer_id"])
    request.session["cart"] = {"customer_id": None, "lines": []}
    request.session["last_customer"] = {"id": cart["customer_id"], "name": customer.name if customer else ""}
    # Remember the just-placed order so it can be undone in one click (POS-08).
    request.session["last_order"] = {
        "reference": reference, "entries": entries, "total_cents": total,
        "customer": customer.name if customer else "",
    }
    flash(request, f"Recorded {entries} entrie(s) — {cents_to_dollars(total)}. Reference {reference}. Ready for the next customer.")
    return RedirectResponse("/cashier", status_code=303)


@router.get("/cashier/receipt/{reference}")
def receipt(request: Request, reference: str, session: Session = Depends(get_session)):
    """A print-ready proof-of-purchase slip for one order (POS-05)."""
    tournament = get_active_tournament(session)
    if tournament is None:
        return RedirectResponse("/", status_code=303)
    wagers = wager_service.order_wagers(session, tournament.id, reference)
    active = [w for w in wagers if w.status == "active"]
    if not wagers:
        flash(request, f"No order found for reference {reference}.", "danger")
        return RedirectResponse("/cashier", status_code=303)
    ctx = base_context(request, session, "cashier")
    ctx.update({
        "reference": reference,
        "order_wagers": wagers,
        "customer": wagers[0].customer,
        "total_cents": sum(w.amount_cents for w in active),
        "entries": sum(w.quantity for w in active),
        "placed_at": min(w.created_at for w in wagers),
        "operator": wagers[0].operator_id,
        "all_void": not active,
    })
    return render(request, "cashier/receipt.html", ctx)


@router.get("/orders")
def order_lookup(request: Request, session: Session = Depends(get_session), reference: str = ""):
    """Look up a whole order by its reference code to settle a dispute (POS-05)."""
    tournament = get_active_tournament(session)
    if tournament is None:
        return RedirectResponse("/setup/new", status_code=303)
    ref = (reference or "").strip().upper()
    ctx = base_context(request, session, "ledger")
    ctx["reference"] = ref
    ctx["order_wagers"] = wager_service.order_wagers(session, tournament.id, ref) if ref else []
    ctx["searched"] = bool(ref)
    return render(request, "cashier/order_lookup.html", ctx)


@router.post("/cashier/void-order")
def void_order(
    request: Request,
    session: Session = Depends(get_session),
    reference: str = Form(...),
    reason: str = Form("Order cancelled at the till"),
    admin_pin: str = Form(""),
):
    """Undo a whole order (all entries under one reference) in one click."""
    tournament = get_active_tournament(session)
    if tournament is None:
        return RedirectResponse("/setup/new", status_code=303)
    if not admin_pin_ok(session, admin_pin):
        flash(request, "Incorrect administrator PIN — the order was not undone.", "danger")
        return RedirectResponse("/cashier", status_code=303)
    try:
        count = wager_service.void_by_reference(
            session, tournament.id, reference,
            reason=(reason or "Order cancelled at the till"), operator=operator_name(request))
    except WagerError as exc:
        flash(request, str(exc), "danger")
        return RedirectResponse("/cashier", status_code=303)
    last = request.session.get("last_order")
    if last and last.get("reference") == reference:
        request.session.pop("last_order", None)
    flash(request, f"Order {reference} undone — {count} entrie(s) voided. Totals updated.")
    return RedirectResponse("/cashier", status_code=303)


# --- void from recent activity (unchanged) ---------------------------------

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
