"""Wager recording and voiding (spec §6.3, §6.4).

Records an immutable snapshot of price/quantity/amount at time of sale. Totals
are always derived from wager status, so a void is a status change plus an audit
entry — never a delete (BR-10).
"""
from __future__ import annotations

import secrets

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Payout, Placement, Player, Team, Tournament, Wager
from app.models.enums import PayoutStatus, TournamentStatus, WageringStatus, WagerStatus
from app.models.entities import utcnow
from app.services import audit

MAX_QUANTITY = 1000  # configurable practical maximum (spec FR-041)

# Unambiguous alphabet (no I/L/O/0/1) for human-readable order references.
_REF_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ"


def new_reference() -> str:
    """A short, human-readable order reference, e.g. A7F-0241 (spec POS-05)."""
    letters = "".join(secrets.choice(_REF_ALPHABET) for _ in range(3))
    return f"{letters}-{secrets.randbelow(10000):04d}"


class WagerError(ValueError):
    """User-safe validation error for wager entry."""


def record_wager(
    session: Session,
    *,
    tournament: Tournament,
    team_id: int,
    player_id: int,
    customer_id: int,
    quantity: int,
    received_cents: int | None = None,
    operator: str | None = None,
    note: str | None = None,
    reference: str | None = None,
) -> Wager:
    if quantity is None or quantity < 1:
        raise WagerError("Quantity must be at least 1 entry.")
    if quantity > MAX_QUANTITY:
        raise WagerError(f"Quantity cannot exceed {MAX_QUANTITY} entries in one wager.")

    team = session.get(Team, team_id)
    if team is None or team.tournament_id != tournament.id:
        raise WagerError("Please choose a valid team.")
    if tournament.status not in (TournamentStatus.OPEN,) or team.wagering_status != WageringStatus.OPEN:
        raise WagerError(f"Wagering is closed for {team.name}, so no new wagers can be taken.")

    player = session.get(Player, player_id)
    if player is None or player.team_id != team_id:
        raise WagerError("That player does not belong to the selected team.")

    unit_price = tournament.entry_price_cents
    amount = unit_price * quantity
    if received_cents is not None and received_cents < amount:
        raise WagerError("Amount received is less than the amount due.")

    wager = Wager(
        tournament_id=tournament.id,
        team_id=team_id,
        player_id=player_id,
        customer_id=customer_id,
        quantity=quantity,
        unit_price_cents=unit_price,
        amount_cents=amount,
        received_cents=received_cents,
        status=WagerStatus.ACTIVE,
        operator_id=operator,
        note=note,
        reference=reference,
    )
    session.add(wager)
    session.flush()
    audit.record(
        session,
        action_type="wager_recorded",
        actor=operator,
        tournament_id=tournament.id,
        entity_type="wager",
        entity_id=wager.id,
        after={
            "team_id": team_id,
            "player_id": player_id,
            "customer_id": customer_id,
            "quantity": quantity,
            "amount_cents": amount,
        },
    )
    return wager


def void_wager(
    session: Session,
    wager: Wager,
    *,
    reason: str,
    operator: str | None = None,
) -> Wager:
    if wager.status == WagerStatus.VOID:
        raise WagerError("This wager is already voided.")
    if not (reason or "").strip():
        raise WagerError("A reason is required to void a wager.")

    # Guard: if payouts have been generated for this team, voiding would make
    # them stale. Require the administrator to reverse/regenerate first (FR-062).
    payout_exists = session.scalar(
        select(func.count(Payout.id))
        .join(Placement, Payout.placement_id == Placement.id)
        .where(Placement.team_id == wager.team_id)
    )
    if payout_exists:
        raise WagerError(
            "Payouts have already been generated for this team. Reverse the payouts "
            "before voiding a wager, then regenerate them."
        )

    before = {"status": wager.status, "amount_cents": wager.amount_cents, "quantity": wager.quantity}
    wager.status = WagerStatus.VOID
    wager.voided_at = utcnow()
    wager.void_reason = reason.strip()
    audit.record(
        session,
        action_type="wager_voided",
        actor=operator,
        tournament_id=wager.tournament_id,
        entity_type="wager",
        entity_id=wager.id,
        before=before,
        after={"status": WagerStatus.VOID},
        reason=reason.strip(),
    )
    return wager


def recent(session: Session, tournament_id: int, limit: int = 10) -> list[Wager]:
    return list(
        session.scalars(
            select(Wager)
            .where(Wager.tournament_id == tournament_id)
            .order_by(Wager.created_at.desc(), Wager.id.desc())
            .limit(limit)
        ).all()
    )
