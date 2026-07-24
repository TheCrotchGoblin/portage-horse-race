"""Results (placements) and payout generation/settlement (spec §6.6, §8).

Placement pools come straight from the tested money engine; payouts are the
deterministic per-entry allocation. Nothing here mutates a finalized payout
amount directly — results changes go through regenerate_payouts (§9.1).
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Payout, Placement, Player, Team, Tournament, Wager
from app.models.enums import PayoutStatus, Position, TournamentStatus, WagerStatus
from app.models.entities import utcnow
from app.services import audit
from app.services import teams as team_service
from app.services.calculations import WagerUnit, allocate_placement


class PayoutError(ValueError):
    """User-safe error for results/payout operations."""


@dataclass
class PlacementPreview:
    position: int
    player: Player | None
    pool_cents: int
    winning_entries: int
    per_entry_cents: int
    unclaimed: bool


def _pool_for_position(fin, position: int) -> int:
    return {
        Position.FIRST: fin.first_pool_cents,
        Position.SECOND: fin.second_pool_cents,
        Position.THIRD: fin.third_pool_cents,
    }[position]


def _winning_units(session: Session, team_id: int, player_id: int) -> list[WagerUnit]:
    rows = session.scalars(
        select(Wager).where(
            Wager.team_id == team_id,
            Wager.player_id == player_id,
            Wager.status == WagerStatus.ACTIVE,
        )
    ).all()
    return [WagerUnit(wager_id=w.id, customer_id=w.customer_id, quantity=w.quantity, created_at=w.created_at) for w in rows]


def has_payouts(session: Session, team_id: int) -> bool:
    return bool(
        session.scalar(
            select(func.count(Payout.id))
            .join(Placement, Payout.placement_id == Placement.id)
            .where(Placement.team_id == team_id)
        )
    )


def has_paid_payouts(session: Session, team_id: int) -> bool:
    return bool(
        session.scalar(
            select(func.count(Payout.id))
            .join(Placement, Payout.placement_id == Placement.id)
            .where(Placement.team_id == team_id, Payout.status == PayoutStatus.PAID)
        )
    )


def set_placements(
    session: Session,
    tournament: Tournament,
    team: Team,
    *,
    first_player_id: int,
    second_player_id: int,
    third_player_id: int,
    operator: str,
    finalize: bool = False,
) -> list[Placement]:
    ids = [first_player_id, second_player_id, third_player_id]
    if len({*ids}) != 3:
        raise PayoutError("A player can only take one placement — pick three different players.")
    for pid in ids:
        player = session.get(Player, pid)
        if player is None or player.team_id != team.id:
            raise PayoutError("Every placed player must belong to this team.")

    if has_payouts(session, team.id):
        raise PayoutError(
            "Payouts already exist for this team. Reverse/clear them before changing results, "
            "then regenerate."
        )

    fin = team_service.team_financials(session, team.id, tournament)
    existing = {p.position: p for p in session.scalars(select(Placement).where(Placement.team_id == team.id)).all()}
    before = {pos: p.player_id for pos, p in existing.items()}

    placements: list[Placement] = []
    for position, pid in zip(Position.ALL, ids):
        pool = _pool_for_position(fin, position)
        placement = existing.get(position)
        if placement is None:
            placement = Placement(team_id=team.id, position=position)
            session.add(placement)
        placement.player_id = pid
        placement.allocated_pool_cents = pool
        if finalize:
            placement.finalized_at = utcnow()
            placement.finalized_by = operator
        placements.append(placement)
    session.flush()

    audit.record(
        session,
        action_type="placements_set" if not finalize else "placements_finalized",
        actor=operator,
        tournament_id=tournament.id,
        entity_type="team",
        entity_id=team.id,
        before=before,
        after={"first": first_player_id, "second": second_player_id, "third": third_player_id},
    )
    if tournament.status in (TournamentStatus.OPEN, TournamentStatus.CLOSED):
        tournament.status = TournamentStatus.RESULTS_ENTERED
    return placements


def placement_previews(session: Session, tournament: Tournament, team: Team) -> list[PlacementPreview]:
    fin = team_service.team_financials(session, team.id, tournament)
    placements = {p.position: p for p in session.scalars(select(Placement).where(Placement.team_id == team.id)).all()}
    previews: list[PlacementPreview] = []
    for position in Position.ALL:
        placement = placements.get(position)
        pool = _pool_for_position(fin, position)
        if placement is None:
            previews.append(PlacementPreview(position, None, pool, 0, 0, False))
            continue
        units = _winning_units(session, team.id, placement.player_id)
        result = allocate_placement(pool, units)
        previews.append(
            PlacementPreview(
                position=position,
                player=session.get(Player, placement.player_id),
                pool_cents=pool,
                winning_entries=result.total_entries,
                per_entry_cents=result.base_cents_per_entry,
                unclaimed=result.total_entries == 0,
            )
        )
    return previews


def generate_payouts(session: Session, tournament: Tournament, team: Team, *, operator: str) -> dict:
    placements = session.scalars(select(Placement).where(Placement.team_id == team.id)).all()
    if len(placements) < 3:
        raise PayoutError("Enter 1st, 2nd and 3rd place before generating payouts.")
    if has_payouts(session, team.id):
        raise PayoutError("Payouts have already been generated for this team.")

    unclaimed_total = 0
    unclaimed_positions: list[int] = []
    created = 0
    for placement in placements:
        placement.finalized_at = placement.finalized_at or utcnow()
        placement.finalized_by = placement.finalized_by or operator
        result = allocate_placement(placement.allocated_pool_cents, _winning_units(session, team.id, placement.player_id))
        if result.total_entries == 0:
            unclaimed_total += result.unclaimed_cents
            unclaimed_positions.append(placement.position)
            continue
        for cp in result.customer_payouts:
            session.add(
                Payout(
                    placement_id=placement.id,
                    customer_id=cp.customer_id,
                    winning_entries=cp.winning_entries,
                    amount_cents=cp.amount_cents,
                    status=PayoutStatus.UNPAID,
                )
            )
            created += 1
    session.flush()

    tournament.status = TournamentStatus.PAYOUTS_GENERATED
    audit.record(
        session,
        action_type="payouts_generated",
        actor=operator,
        tournament_id=tournament.id,
        entity_type="team",
        entity_id=team.id,
        after={"payouts": created, "unclaimed_cents": unclaimed_total, "unclaimed_positions": unclaimed_positions},
    )
    return {"created": created, "unclaimed_cents": unclaimed_total, "unclaimed_positions": unclaimed_positions}


def regenerate_payouts(session: Session, tournament: Tournament, team: Team, *, operator: str) -> None:
    """Clear UNPAID payouts so results can be changed (spec §9.1, FR-102)."""
    if has_paid_payouts(session, team.id):
        raise PayoutError(
            "Some winners have already been paid. Reverse those payments first before changing results."
        )
    payouts = session.scalars(
        select(Payout).join(Placement, Payout.placement_id == Placement.id).where(Placement.team_id == team.id)
    ).all()
    for p in payouts:
        session.delete(p)
    session.flush()
    audit.record(
        session,
        action_type="payouts_cleared",
        actor=operator,
        tournament_id=tournament.id,
        entity_type="team",
        entity_id=team.id,
        reason="results change / regenerate",
    )


def pay_payout(session: Session, payout: Payout, *, method: str, operator: str, note: str | None = None) -> Payout:
    if payout.status == PayoutStatus.PAID:
        raise PayoutError("This payout has already been paid.")
    if payout.status == PayoutStatus.REVERSED:
        raise PayoutError("This payout was reversed and cannot be paid without regenerating.")
    payout.status = PayoutStatus.PAID
    payout.paid_at = utcnow()
    payout.paid_by = operator
    payout.payment_method = method or "cash"
    payout.note = note or None
    audit.record(
        session,
        action_type="payout_paid",
        actor=operator,
        entity_type="payout",
        entity_id=payout.id,
        after={"amount_cents": payout.amount_cents, "method": payout.payment_method},
    )
    return payout


def reverse_payout(session: Session, payout: Payout, *, reason: str, operator: str) -> Payout:
    if not (reason or "").strip():
        raise PayoutError("A reason is required to reverse a payment.")
    before = {"status": payout.status}
    payout.status = PayoutStatus.REVERSED
    audit.record(
        session,
        action_type="payout_reversed",
        actor=operator,
        entity_type="payout",
        entity_id=payout.id,
        before=before,
        after={"status": PayoutStatus.REVERSED},
        reason=reason.strip(),
    )
    return payout


def check_settled(session: Session, tournament: Tournament) -> None:
    """Advance to SETTLED when no unpaid payouts remain across the tournament."""
    unpaid = session.scalar(
        select(func.count(Payout.id))
        .join(Placement, Payout.placement_id == Placement.id)
        .join(Team, Placement.team_id == Team.id)
        .where(Team.tournament_id == tournament.id, Payout.status == PayoutStatus.UNPAID)
    )
    total = session.scalar(
        select(func.count(Payout.id))
        .join(Placement, Payout.placement_id == Placement.id)
        .join(Team, Placement.team_id == Team.id)
        .where(Team.tournament_id == tournament.id)
    )
    if total and not unpaid and tournament.status == TournamentStatus.PAYOUTS_GENERATED:
        tournament.status = TournamentStatus.SETTLED
