"""Dashboard assembly (spec §7.3): per-team cards + reconciliation + warnings."""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CashCount, Payout, Placement, Player, Team, Tournament
from app.models.enums import PayoutStatus, TournamentStatus
from app.services import teams as team_service
from app.services.calculations import TeamFinancials


@dataclass
class TeamCard:
    team: Team
    financials: TeamFinancials
    players: list[team_service.PlayerTotal]
    counted_cents: int | None
    variance_cents: int | None  # counted - expected (gross); None if not counted
    placements_set: int
    warnings: list[str] = field(default_factory=list)


@dataclass
class Dashboard:
    tournament: Tournament
    cards: list[TeamCard]
    warnings: list[str] = field(default_factory=list)

    @property
    def total_gross_cents(self) -> int:
        return sum(c.financials.gross_cents for c in self.cards)

    @property
    def total_entries(self) -> int:
        return sum(c.financials.active_entries for c in self.cards)


def _latest_cash_count(session: Session, team_id: int) -> int | None:
    row = session.execute(
        select(CashCount.counted_cents)
        .where(CashCount.team_id == team_id)
        .order_by(CashCount.counted_at.desc(), CashCount.id.desc())
        .limit(1)
    ).first()
    return int(row[0]) if row else None


def build_dashboard(session: Session, tournament: Tournament) -> Dashboard:
    teams = session.scalars(
        select(Team).where(Team.tournament_id == tournament.id).order_by(Team.id)
    ).all()

    cards: list[TeamCard] = []
    global_warnings: list[str] = []

    for team in teams:
        fin = team_service.team_financials(session, team.id, tournament)
        players = team_service.player_totals(session, team.id)
        counted = _latest_cash_count(session, team.id)
        variance = None if counted is None else counted - fin.gross_cents
        placements_set = session.scalar(
            select(func.count(Placement.id)).where(Placement.team_id == team.id)
        ) or 0

        warnings: list[str] = []
        if not players:
            warnings.append("No players have been added to this team yet.")
        if tournament.status in (TournamentStatus.CLOSED, TournamentStatus.RESULTS_ENTERED) and placements_set < 3:
            warnings.append("Results are not fully entered (needs 1st, 2nd and 3rd).")
        if variance is not None and variance != 0:
            direction = "over" if variance > 0 else "short"
            warnings.append(f"Cash count is {direction} by the amount shown.")

        cards.append(
            TeamCard(
                team=team,
                financials=fin,
                players=players,
                counted_cents=counted,
                variance_cents=variance,
                placements_set=int(placements_set),
                warnings=warnings,
            )
        )

    # Tournament-wide warnings.
    if not teams:
        global_warnings.append("No teams have been created yet. Open Setup to add teams and players.")

    unpaid = session.scalar(
        select(func.count(Payout.id))
        .join(Placement, Payout.placement_id == Placement.id)
        .join(Team, Placement.team_id == Team.id)
        .where(Team.tournament_id == tournament.id, Payout.status == PayoutStatus.UNPAID)
    ) or 0
    if unpaid:
        global_warnings.append(f"{unpaid} winner(s) still need to be paid.")

    return Dashboard(tournament=tournament, cards=cards, warnings=global_warnings)
