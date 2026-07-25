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


def update_details(session: Session, tournament: Tournament, *, name: str,
                   event_date: str | None, operator: str) -> None:
    """Edit the tournament name and date. Unlike price/percentages, these carry
    no financial weight, so they stay editable even after sales have started."""
    name = (name or "").strip()
    if not name:
        raise SetupError("Please give the tournament a name.")
    before = {"name": tournament.name, "event_date": tournament.event_date}
    tournament.name = name
    tournament.event_date = event_date or None
    audit.record(session, action_type="tournament_details_changed", actor=operator,
                 tournament_id=tournament.id, entity_type="tournament", entity_id=tournament.id,
                 before=before, after={"name": name, "event_date": event_date or None})


def rename_team(session: Session, tournament: Tournament, team: Team, new_name: str) -> None:
    """Rename a team (fix a typo) — allowed any time; touches no money. Audited."""
    new_name = (new_name or "").strip()
    if not new_name:
        raise SetupError("Please enter a team name.")
    clash = session.scalar(
        select(func.count(Team.id)).where(
            Team.tournament_id == tournament.id, Team.id != team.id,
            func.lower(Team.name) == new_name.lower())
    )
    if clash:
        raise SetupError(f"There is already a team called '{new_name}'.")
    before = team.name
    team.name = new_name
    audit.record(session, action_type="team_renamed", actor=None, tournament_id=tournament.id,
                 entity_type="team", entity_id=team.id, before={"name": before}, after={"name": new_name})


def rename_player(session: Session, player: Player, new_name: str) -> None:
    """Rename a player (fix a misspelling) — allowed even after they have wagers,
    since it changes no financial record. Audited; guards duplicates on the team."""
    new_name = (new_name or "").strip()
    if not new_name:
        raise SetupError("Please enter a player name.")
    clash = session.scalar(
        select(func.count(Player.id)).where(
            Player.team_id == player.team_id, Player.id != player.id,
            func.lower(Player.name) == new_name.lower())
    )
    if clash:
        raise SetupError(f"There is already a player called '{new_name}' on this team.")
    before = player.name
    player.name = new_name
    audit.record(session, action_type="player_renamed", actor=None,
                 entity_type="player", entity_id=player.id,
                 before={"name": before}, after={"name": new_name})


def move_player(session: Session, player: Player, target_team: Team) -> None:
    """Move a player to another team. Blocked once they have wagers, because those
    wagers belong to the team they were sold under (moving would misattribute money)."""
    if player.team_id == target_team.id:
        return
    count = session.scalar(select(func.count(Wager.id)).where(Wager.player_id == player.id)) or 0
    if count:
        raise SetupError(f"'{player.name}' already has wagers, so they can't be moved. Void those wagers first.")
    clash = session.scalar(
        select(func.count(Player.id)).where(
            Player.team_id == target_team.id, func.lower(Player.name) == player.name.lower())
    )
    if clash:
        raise SetupError(f"'{player.name}' is already on {target_team.name}.")
    before = player.team_id
    start = session.scalar(
        select(func.coalesce(func.max(Player.display_order), 0)).where(Player.team_id == target_team.id)
    ) or 0
    player.team_id = target_team.id
    player.display_order = start + 1
    audit.record(session, action_type="player_moved", actor=None,
                 entity_type="player", entity_id=player.id,
                 before={"team_id": before}, after={"team_id": target_team.id})


def clone_tournament(session: Session, source: Tournament, *, name: str,
                     event_date: str | None, operator: str) -> Tournament:
    """Start a fresh DRAFT from an existing event: copy the team names, their
    active players and all financial settings — but NOT wagers, customers,
    results or payouts (spec SET-05). Perfect for next year's event."""
    name = (name or "").strip()
    if not name:
        raise SetupError("Please give the new tournament a name.")
    active = session.scalar(
        select(func.count(Tournament.id)).where(Tournament.status != TournamentStatus.ARCHIVED)
    )
    if active:
        raise SetupError("Archive the current tournament before starting a new one from a past event.")

    clone = Tournament(
        name=name, event_date=event_date or None, status=TournamentStatus.DRAFT,
        entry_price_cents=source.entry_price_cents, club_bps=source.club_bps,
        first_bps=source.first_bps, second_bps=source.second_bps, third_bps=source.third_bps,
    )
    session.add(clone)
    session.flush()
    for team in session.scalars(select(Team).where(Team.tournament_id == source.id).order_by(Team.id)).all():
        new_team = Team(tournament_id=clone.id, name=team.name, wagering_status=WageringStatus.CLOSED)
        session.add(new_team)
        session.flush()
        players = session.scalars(
            select(Player).where(Player.team_id == team.id, Player.active.is_(True))
            .order_by(Player.display_order, Player.id)
        ).all()
        for p in players:
            session.add(Player(team_id=new_team.id, name=p.name,
                               display_order=p.display_order, active=True))
    session.flush()
    audit.record(session, action_type="tournament_cloned", actor=operator, tournament_id=clone.id,
                 entity_type="tournament", entity_id=clone.id,
                 after={"name": name, "cloned_from": source.id})
    return clone


def add_team(session: Session, tournament: Tournament, name: str) -> Team:
    name = (name or "").strip()
    if not name:
        raise SetupError("Please enter a team name.")
    exists = session.scalar(
        select(func.count(Team.id)).where(
            Team.tournament_id == tournament.id, func.lower(Team.name) == name.lower()
        )
    )
    if exists:
        raise SetupError(f"There is already a team called '{name}'. Please use a different name.")
    team = Team(tournament_id=tournament.id, name=name, wagering_status=WageringStatus.CLOSED)
    session.add(team)
    session.flush()
    return team


def add_players(session: Session, team: Team, names: list[str]) -> tuple[list[Player], list[str]]:
    """Add players to a team, skipping names that already exist on it (or repeat
    within this batch). Returns (created, skipped_duplicate_names)."""
    start = session.scalar(
        select(func.coalesce(func.max(Player.display_order), 0)).where(Player.team_id == team.id)
    ) or 0
    existing = {
        p.name.lower()
        for p in session.scalars(select(Player).where(Player.team_id == team.id)).all()
    }
    created: list[Player] = []
    skipped: list[str] = []
    offset = 0
    for raw in (n.strip() for n in names):
        if not raw:
            continue
        if raw.lower() in existing:
            skipped.append(raw)
            continue
        existing.add(raw.lower())
        offset += 1
        player = Player(team_id=team.id, name=raw, display_order=start + offset, active=True)
        session.add(player)
        created.append(player)
    session.flush()
    return created, skipped


def import_players_csv(session: Session, tournament: Tournament, csv_text: str,
                       *, create_teams: bool = False) -> dict:
    """Import players from CSV text with columns: team, player[, order] (spec SET-02).

    Teams are matched by name (case-insensitive). If create_teams is set, team
    names in the file that don't exist yet are created automatically — so an
    80-golfer / 4-team spreadsheet imports in one step. Otherwise unknown teams
    are reported, not created. Duplicate players (per team) are skipped.
    """
    import csv
    import io
    from collections import defaultdict

    rows = list(csv.reader(io.StringIO(csv_text)))
    if rows and rows[0] and rows[0][0].strip().lower() in ("team", "team name"):
        rows = rows[1:]

    teams = {t.name.lower(): t for t in
             session.scalars(select(Team).where(Team.tournament_id == tournament.id)).all()}
    by_team: dict[int, list[str]] = defaultdict(list)
    unknown: list[str] = []
    created_teams: list[str] = []
    for row in rows:
        if len(row) < 2:
            continue
        team_name, player_name = row[0].strip(), row[1].strip()
        if not player_name:
            continue
        team = teams.get(team_name.lower())
        if team is None:
            if create_teams and team_name:
                team = add_team(session, tournament, team_name)
                teams[team_name.lower()] = team
                created_teams.append(team.name)
            else:
                unknown.append(team_name)
                continue
        by_team[team.id].append(player_name)

    added = skipped = 0
    for team_id, names in by_team.items():
        created, dupes = add_players(session, session.get(Team, team_id), names)
        added += len(created)
        skipped += len(dupes)
    return {"added": added, "skipped": skipped,
            "created_teams": sorted(set(created_teams)),
            "unknown_teams": sorted(set(u for u in unknown if u))}


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


def setup_checklist(session: Session, tournament: Tournament) -> list[dict]:
    """Completeness items shown before wagering can open (spec SET-03)."""
    teams = session.scalars(select(Team).where(Team.tournament_id == tournament.id)).all()
    teams_with_players = 0
    for team in teams:
        count = session.scalar(select(func.count(Player.id)).where(Player.team_id == team.id)) or 0
        if count >= 1:
            teams_with_players += 1
    items = [
        {"label": "Tournament is named", "done": bool((tournament.name or "").strip())},
        {"label": "At least one team added", "done": len(teams) >= 1},
        {"label": "Every team has at least one player",
         "done": bool(teams) and teams_with_players == len(teams)},
    ]
    return items


def team_player_counts(session: Session, tournament: Tournament) -> dict[int, int]:
    rows = session.execute(
        select(Player.team_id, func.count(Player.id))
        .join(Team, Player.team_id == Team.id)
        .where(Team.tournament_id == tournament.id)
        .group_by(Player.team_id)
    ).all()
    return {int(tid): int(n) for tid, n in rows}


def setup_advisories(session: Session, tournament: Tournament) -> list[str]:
    """Non-blocking heads-up shown during setup (SET-03). A team with fewer than
    three players can't fill 1st/2nd/3rd, which otherwise only bites at settlement."""
    counts = team_player_counts(session, tournament)
    msgs: list[str] = []
    for team in session.scalars(select(Team).where(Team.tournament_id == tournament.id).order_by(Team.id)).all():
        n = counts.get(team.id, 0)
        if 0 < n < 3:
            msgs.append(f"{team.name} has only {n} player(s) — a team needs 3 to award 1st, 2nd and 3rd.")
    return msgs


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
