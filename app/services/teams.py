"""Team-level aggregation shared by the dashboard, results and reports.

All totals derive from **active** (non-voided) wagers only, keeping each team a
financially independent pool (spec BR-02, FR-081).
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Player, Tournament, Wager
from app.models.enums import WagerStatus
from app.services.calculations import TeamFinancials, compute_team_financials


@dataclass(frozen=True)
class PlayerTotal:
    player: Player
    entries: int
    gross_cents: int


def team_totals(session: Session, team_id: int) -> tuple[int, int]:
    """Return (active_entries, gross_cents) for a team."""
    row = session.execute(
        select(
            func.coalesce(func.sum(Wager.quantity), 0),
            func.coalesce(func.sum(Wager.amount_cents), 0),
        ).where(Wager.team_id == team_id, Wager.status == WagerStatus.ACTIVE)
    ).one()
    return int(row[0]), int(row[1])


def team_financials(session: Session, team_id: int, tournament: Tournament) -> TeamFinancials:
    entries, gross = team_totals(session, team_id)
    return compute_team_financials(
        gross_cents=gross,
        active_entries=entries,
        club_bps=tournament.club_bps,
        first_bps=tournament.first_bps,
        second_bps=tournament.second_bps,
        third_bps=tournament.third_bps,
    )


def player_totals(session: Session, team_id: int) -> list[PlayerTotal]:
    """Entries and gross per player, ranked by entries sold (desc)."""
    rows = session.execute(
        select(
            Player,
            func.coalesce(func.sum(Wager.quantity).filter(Wager.status == WagerStatus.ACTIVE), 0),
            func.coalesce(func.sum(Wager.amount_cents).filter(Wager.status == WagerStatus.ACTIVE), 0),
        )
        .select_from(Player)
        .outerjoin(Wager, Wager.player_id == Player.id)
        .where(Player.team_id == team_id)
        .group_by(Player.id)
        .order_by(Player.display_order, Player.id)
    ).all()
    totals = [PlayerTotal(player=r[0], entries=int(r[1]), gross_cents=int(r[2])) for r in rows]
    return sorted(totals, key=lambda t: (-t.entries, t.player.display_order, t.player.id))
