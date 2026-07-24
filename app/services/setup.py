"""Tournament / team / player setup and lifecycle (spec §6.1, Appendix A).

Guards financial configuration once the first sale exists (FR-005) and records
audit entries for material lifecycle actions.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Player, Team, Tournament, Wager
from app.models.enums import TournamentStatus, WageringStatus
from app.services import audit


class SetupError(ValueError):
    """Raised for invalid setup input; message is safe to show the user."""


def percent_to_bps(value: str | float) -> int:
    """Convert a percentage like '15' or '15.5' into basis points."""
    try:
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        raise SetupError(f"'{value}' is not a valid percentage")


def validate_config(*, club_bps: int, first_bps: int, second_bps: int, third_bps: int) -> None:
    if not (0 <= club_bps <= 10000):
        raise SetupError("Club share must be between 0% and 100%.")
    if first_bps + second_bps + third_bps != 10000:
        raise SetupError("The 1st/2nd/3rd payout split must add up to exactly 100%.")
    for label, bps in (("1st", first_bps), ("2nd", second_bps), ("3rd", third_bps)):
        if bps < 0:
            raise SetupError(f"The {label} place percentage cannot be negative.")


def has_sales(session: Session, tournament_id: int) -> bool:
    return bool(
        session.scalar(select(func.count(Wager.id)).where(Wager.tournament_id == tournament_id))
    )


def create_tournament(
    session: Session,
    *,
    name: str,
    event_date: str | None,
    entry_price_cents: int,
    club_bps: int,
    first_bps: int,
    second_bps: int,
    third_bps: int,
    operator: str,
) -> Tournament:
    name = (name or "").strip()
    if not name:
        raise SetupError("Please give the tournament a name.")
    if entry_price_cents <= 0:
        raise SetupError("Entry price must be greater than zero.")
    validate_config(club_bps=club_bps, first_bps=first_bps, second_bps=second_bps, third_bps=third_bps)

    tournament = Tournament(
        name=name,
        event_date=event_date or None,
        status=TournamentStatus.DRAFT,
        entry_price_cents=entry_price_cents,
        club_bps=club_bps,
        first_bps=first_bps,
        second_bps=second_bps,
        third_bps=third_bps,
    )
    session.add(tournament)
    session.flush()
    audit.record(
        session,
        action_type="tournament_created",
        actor=operator,
        tournament_id=tournament.id,
        entity_type="tournament",
        entity_id=tournament.id,
        after={"name": name, "entry_price_cents": entry_price_cents, "club_bps": club_bps},
    )
    return tournament


def update_config(
    session: Session,
    tournament: Tournament,
    *,
    name: str,
    event_date: str | None,
    entry_price_cents: int,
    club_bps: int,
    first_bps: int,
    second_bps: int,
    third_bps: int,
    operator: str,
) -> None:
    """Edit configuration. Blocked once any sale exists (FR-005)."""
    if has_sales(session, tournament.id):
        raise SetupError(
            "Sales have already been recorded, so the price and percentages are locked. "
            "Use an administrator correction if a change is truly required."
        )
    name = (name or "").strip()
    if not name:
        raise SetupError("Please give the tournament a name.")
    if entry_price_cents <= 0:
        raise SetupError("Entry price must be greater than zero.")
    validate_config(club_bps=club_bps, first_bps=first_bps, second_bps=second_bps, third_bps=third_bps)

    before = {
        "entry_price_cents": tournament.entry_price_cents,
        "club_bps": tournament.club_bps,
        "split": [tournament.first_bps, tournament.second_bps, tournament.third_bps],
    }
    tournament.name = name
    tournament.event_date = event_date or None
    tournament.entry_price_cents = entry_price_cents
    tournament.club_bps = club_bps
    tournament.first_bps = first_bps
    tournament.second_bps = second_bps
    tournament.third_bps = third_bps
    audit.record(
        session,
        action_type="tournament_config_changed",
        actor=operator,
        tournament_id=tournament.id,
        entity_type="tournament",
        entity_id=tournament.id,
        before=before,
        after={"entry_price_cents": entry_price_cents, "club_bps": club_bps},
    )


def add_team(session: Session, tournament: Tournament, name: str) -> Team:
    name = (name or "").strip()
    if not name:
        raise SetupError("Please enter a team name.")
    team = Team(tournament_id=tournament.id, name=name, wagering_status=WageringStatus.CLOSED)
    session.add(team)
    session.flush()
    return team


def add_players(session: Session, team: Team, names: list[str]) -> list[Player]:
    start = session.scalar(
        select(func.coalesce(func.max(Player.display_order), 0)).where(Player.team_id == team.id)
    ) or 0
    created: list[Player] = []
    for offset, raw in enumerate((n.strip() for n in names), start=1):
        if not raw:
            continue
        player = Player(team_id=team.id, name=raw, display_order=start + offset, active=True)
        session.add(player)
        created.append(player)
    session.flush()
    return created


def delete_player(session: Session, tournament: Tournament, player: Player) -> None:
    # Adding players is always allowed; removal is blocked only if THIS player
    # already has wagers (their financial records must be kept).
    count = session.scalar(select(func.count(Wager.id)).where(Wager.player_id == player.id)) or 0
    if count:
        raise SetupError(f"'{player.name}' already has wagers, so they can't be removed. Void those wagers first.")
    session.delete(player)


def delete_team(session: Session, tournament: Tournament, team: Team) -> None:
    count = session.scalar(select(func.count(Wager.id)).where(Wager.team_id == team.id)) or 0
    if count:
        raise SetupError(f"'{team.name}' already has wagers, so it can't be removed. Void those wagers first.")
    session.delete(team)


def _ready_to_open(session: Session, tournament: Tournament) -> None:
    # A tournament runs with one or more teams. Extra teams simply spread the
    # betting across more players so wagers aren't all piled on a few favourites.
    teams = session.scalars(select(Team).where(Team.tournament_id == tournament.id)).all()
    if not teams:
        raise SetupError("Add at least one team before opening wagering.")
    for team in teams:
        count = session.scalar(select(func.count(Player.id)).where(Player.team_id == team.id)) or 0
        if count < 1:
            raise SetupError(f"Team '{team.name}' needs at least one player before wagering can open.")


def open_wagering(session: Session, tournament: Tournament, operator: str, team_id: int | None = None) -> None:
    _ready_to_open(session, tournament)
    query = select(Team).where(Team.tournament_id == tournament.id)
    if team_id is not None:
        query = query.where(Team.id == team_id)
    for team in session.scalars(query).all():
        team.wagering_status = WageringStatus.OPEN
    if tournament.status in (TournamentStatus.DRAFT, TournamentStatus.CLOSED):
        tournament.status = TournamentStatus.OPEN
    audit.record(
        session,
        action_type="wagering_opened",
        actor=operator,
        tournament_id=tournament.id,
        entity_type="tournament",
        entity_id=tournament.id,
        after={"team_id": team_id or "all"},
    )


def archive_tournament(session: Session, tournament: Tournament, operator: str) -> None:
    before = {"status": tournament.status}
    tournament.status = TournamentStatus.ARCHIVED
    for team in session.scalars(select(Team).where(Team.tournament_id == tournament.id)).all():
        team.wagering_status = WageringStatus.CLOSED
    audit.record(
        session,
        action_type="tournament_archived",
        actor=operator,
        tournament_id=tournament.id,
        entity_type="tournament",
        entity_id=tournament.id,
        before=before,
        after={"status": TournamentStatus.ARCHIVED},
    )


def reopen_tournament(session: Session, tournament: Tournament, operator: str) -> None:
    active = session.scalar(
        select(func.count(Tournament.id)).where(Tournament.status != TournamentStatus.ARCHIVED)
    )
    if active:
        raise SetupError("Archive the current tournament before reopening an older one.")
    before = {"status": tournament.status}
    tournament.status = TournamentStatus.CLOSED
    audit.record(
        session,
        action_type="tournament_reopened",
        actor=operator,
        tournament_id=tournament.id,
        entity_type="tournament",
        entity_id=tournament.id,
        before=before,
        after={"status": TournamentStatus.CLOSED},
    )


def close_wagering(session: Session, tournament: Tournament, operator: str, team_id: int | None = None) -> None:
    query = select(Team).where(Team.tournament_id == tournament.id)
    if team_id is not None:
        query = query.where(Team.id == team_id)
    for team in session.scalars(query).all():
        team.wagering_status = WageringStatus.CLOSED

    all_closed = all(
        t.wagering_status == WageringStatus.CLOSED
        for t in session.scalars(select(Team).where(Team.tournament_id == tournament.id)).all()
    )
    if all_closed and tournament.status == TournamentStatus.OPEN:
        tournament.status = TournamentStatus.CLOSED
    audit.record(
        session,
        action_type="wagering_closed",
        actor=operator,
        tournament_id=tournament.id,
        entity_type="tournament",
        entity_id=tournament.id,
        after={"team_id": team_id or "all"},
    )
