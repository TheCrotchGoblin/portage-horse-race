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
from app.models.enums import PayoutStatus, Position, TournamentStatus, WageringStatus, WagerStatus
from app.models.entities import utcnow
from app.services import audit
from app.services import teams as team_service
from app.services.calculations import WagerUnit, allocate_placement


class PayoutError(ValueError):
    """User-safe error for results/payout operations."""


def _ensure_not_locked(tournament: Tournament | None) -> None:
    """A settled event is financially locked (spec FIN-04). Enforced here so
    every payout/disposition mutation is guarded regardless of the caller."""
    if tournament is not None and tournament.is_settled:
        raise PayoutError("This event is settled and locked. Reopen it before making changes.")


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


def settlement_blockers(session: Session, tournament: Tournament, team: Team) -> list[str]:
    """Human-readable reasons this team is not ready for payout generation (FIN-02).
    Empty list means it is ready."""
    reasons: list[str] = []
    if team.wagering_status != WageringStatus.CLOSED:
        reasons.append(f"Close wagering for {team.name} first (no more bets can be taken).")
    placements = session.scalars(select(Placement).where(Placement.team_id == team.id)).all()
    if len(placements) < 3:
        reasons.append("Enter 1st, 2nd and 3rd place.")
    if tournament.first_bps + tournament.second_bps + tournament.third_bps != 10000:
        reasons.append("The 1st/2nd/3rd payout split must total 100%.")
    if has_payouts(session, team.id):
        reasons.append("Payouts have already been generated for this team.")
    return reasons


def generate_payouts(session: Session, tournament: Tournament, team: Team, *, operator: str) -> dict:
    blockers = settlement_blockers(session, tournament, team)
    if blockers:
        raise PayoutError(" ".join(blockers))
    placements = session.scalars(select(Placement).where(Placement.team_id == team.id)).all()

    unclaimed_total = 0
    unclaimed_positions: list[int] = []
    created = 0
    for placement in placements:
        placement.finalized_at = placement.finalized_at or utcnow()
        placement.finalized_by = placement.finalized_by or operator
        placement.payouts_generated_at = utcnow()  # mark that payouts were generated for this team
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
    # Un-flag the team's placements so they're no longer treated as generated.
    for placement in session.scalars(select(Placement).where(Placement.team_id == team.id)).all():
        placement.payouts_generated_at = None
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


DISPOSITIONS = {
    "return_to_club": "Return to the club",
    "carryover": "Carry over to a future event",
    "manual": "Handle manually (see note)",
}

CONTACT_STATUSES = {
    "called": "Called",
    "emailed": "Emailed",
    "texted": "Texted",
    "no_response": "No response",
}


def set_contact(session: Session, payout: Payout, *, status: str, note: str, operator: str) -> Payout:
    """Record a contact action for an unpaid winner (spec RPT-04)."""
    if status not in CONTACT_STATUSES:
        raise PayoutError("Choose how the winner was contacted.")
    payout.contact_status = status
    payout.contact_note = (note or "").strip() or None
    payout.contacted_at = utcnow()
    payout.contacted_by = operator
    audit.record(session, action_type="payout_contact", actor=operator,
                 entity_type="payout", entity_id=payout.id, after={"contact": status})
    return payout


def unclaimed_placements(session: Session, tournament_id: int) -> list[Placement]:
    """Placements whose pool went to nobody (placed player had no wagers).

    Only considers placements whose team has actually been through payout
    generation — a team with results recorded but payouts not yet generated is
    NOT unclaimed.
    """
    placements = session.scalars(
        select(Placement).join(Team, Placement.team_id == Team.id)
        .where(Team.tournament_id == tournament_id, Placement.payouts_generated_at.is_not(None),
               Placement.allocated_pool_cents > 0)
    ).all()
    result = []
    for p in placements:
        payout_count = session.scalar(select(func.count(Payout.id)).where(Payout.placement_id == p.id)) or 0
        if payout_count == 0:
            result.append(p)
    return result


def set_disposition(session: Session, placement: Placement, *, disposition: str, note: str, operator: str) -> Placement:
    _ensure_not_locked(placement.player.team.tournament)
    if disposition not in DISPOSITIONS:
        raise PayoutError("Choose how the unclaimed pool should be handled.")
    if disposition == "manual" and not (note or "").strip():
        raise PayoutError("Please add a note explaining how the unclaimed pool is handled.")
    placement.disposition = disposition
    placement.disposition_note = (note or "").strip() or None
    placement.disposition_by = operator
    placement.disposition_at = utcnow()
    audit.record(
        session,
        action_type="unclaimed_disposition",
        actor=operator,
        entity_type="placement",
        entity_id=placement.id,
        after={"disposition": disposition, "pool_cents": placement.allocated_pool_cents},
        reason=placement.disposition_note,
    )
    return placement


def pay_payout(session: Session, payout: Payout, *, method: str, operator: str, note: str | None = None) -> Payout:
    if payout.status == PayoutStatus.PAID:
        raise PayoutError("This payout has already been paid.")
    # UNPAID or a previously REVERSED payout (owed again) may be paid; only PAID is blocked.
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


WAIVE_KINDS = {
    "waived": "Waived by winner",
    "donated": "Donated to the club",
}


def waive_payout(session: Session, payout: Payout, *, kind: str, reason: str, operator: str) -> Payout:
    """Close out a winner who declines or can't be reached: the amount is waived
    or donated to the club (FIN-08). A resolved outcome — no longer outstanding,
    so it stops blocking settlement without falsely marking anyone 'paid'."""
    _ensure_not_locked(payout.placement.player.team.tournament)
    if payout.status == PayoutStatus.PAID:
        raise PayoutError("This payout has already been paid — reverse it first if it must be waived.")
    if kind not in WAIVE_KINDS:
        raise PayoutError("Choose whether the amount is waived or donated to the club.")
    if not (reason or "").strip():
        raise PayoutError("Please add a short reason (e.g. 'unreachable after 3 calls').")
    before = {"status": payout.status}
    payout.status = PayoutStatus.WAIVED
    payout.paid_at = utcnow()
    payout.paid_by = operator
    payout.payment_method = kind
    payout.note = reason.strip()
    audit.record(session, action_type="payout_waived", actor=operator,
                 entity_type="payout", entity_id=payout.id,
                 before=before, after={"status": PayoutStatus.WAIVED, "kind": kind},
                 reason=reason.strip())
    return payout


def reverse_payout(session: Session, payout: Payout, *, reason: str, operator: str) -> Payout:
    _ensure_not_locked(payout.placement.player.team.tournament)
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


def reopen_settlement(session: Session, tournament: Tournament, *, operator: str, reason: str) -> None:
    """Reopen a SETTLED event for corrections (spec FIN-04). Requires a reason;
    the caller enforces the administrator PIN. Audited."""
    if not tournament.is_settled:
        raise PayoutError("This event is not settled, so there is nothing to reopen.")
    if not (reason or "").strip():
        raise PayoutError("A reason is required to reopen a settled event.")
    tournament.status = TournamentStatus.PAYOUTS_GENERATED
    audit.record(
        session,
        action_type="settlement_reopened",
        actor=operator,
        tournament_id=tournament.id,
        entity_type="tournament",
        entity_id=tournament.id,
        before={"status": TournamentStatus.SETTLED},
        after={"status": TournamentStatus.PAYOUTS_GENERATED},
        reason=reason.strip(),
    )


def check_settled(session: Session, tournament: Tournament) -> None:
    """Advance to SETTLED when no unpaid payouts remain across the tournament."""
    session.flush()  # ensure a just-marked-paid payout is visible to the queries below
    # Outstanding = money still owed. A REVERSED payout is neither paid nor
    # (currently) unpaid, but the winner is owed again, so it must block SETTLED —
    # otherwise reopen -> reverse -> pay-another could re-settle with money owed.
    outstanding = session.scalar(
        select(func.count(Payout.id))
        .join(Placement, Payout.placement_id == Placement.id)
        .join(Team, Placement.team_id == Team.id)
        .where(Team.tournament_id == tournament.id,
               Payout.status.in_(PayoutStatus.OUTSTANDING))
    )
    total = session.scalar(
        select(func.count(Payout.id))
        .join(Placement, Payout.placement_id == Placement.id)
        .join(Team, Placement.team_id == Team.id)
        .where(Team.tournament_id == tournament.id)
    )
    # An unclaimed pool must be formally disposed before the event is SETTLED (FR-108).
    undisposed = [p for p in unclaimed_placements(session, tournament.id) if p.disposition is None]

    # An over/short cash box must be explained before settling (FIN-05).
    from app.services import cash as cash_service
    unexplained_cash = cash_service.unexplained_variance_teams(session, tournament.id)

    # Every team must have been through payout generation before settling —
    # otherwise fully paying one team would prematurely settle the whole event.
    teams_total = session.scalar(
        select(func.count(Team.id)).where(Team.tournament_id == tournament.id)
    ) or 0
    teams_generated = session.scalar(
        select(func.count(func.distinct(Placement.team_id)))
        .join(Team, Placement.team_id == Team.id)
        .where(Team.tournament_id == tournament.id, Placement.payouts_generated_at.is_not(None))
    ) or 0
    all_generated = teams_total > 0 and teams_total == teams_generated

    if (total and not outstanding and not undisposed and not unexplained_cash and all_generated
            and tournament.status == TournamentStatus.PAYOUTS_GENERATED):
        tournament.status = TournamentStatus.SETTLED


def settlement_status_blockers(session: Session, tournament: Tournament) -> list[str]:
    """Human-readable reasons a fully-generated event has not settled yet, shown
    on the Payouts screen so the operator knows what's left."""
    from app.services import cash as cash_service
    reasons: list[str] = []
    outstanding = session.scalar(
        select(func.count(Payout.id))
        .join(Placement, Payout.placement_id == Placement.id)
        .join(Team, Placement.team_id == Team.id)
        .where(Team.tournament_id == tournament.id, Payout.status.in_(PayoutStatus.OUTSTANDING))
    ) or 0
    if outstanding:
        reasons.append(f"{outstanding} winner(s) still to pay or resolve.")
    undisposed = [p for p in unclaimed_placements(session, tournament.id) if p.disposition is None]
    if undisposed:
        reasons.append(f"{len(undisposed)} unclaimed pool(s) need a decision.")
    for team in cash_service.unexplained_variance_teams(session, tournament.id):
        reasons.append(f"{team.name}'s cash box is over/short — count it and note why.")
    return reasons
