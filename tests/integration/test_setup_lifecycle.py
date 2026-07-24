"""Tournament lifecycle: archive, start-new, reopen, per-team wagering (FR-001, FR-006)."""
from __future__ import annotations

from sqlalchemy import select

from app import database
from app.models import Team, Tournament
from app.models.enums import TournamentStatus, WageringStatus
from tests.conftest import make_tournament


def test_archive_then_start_new_tournament(client):
    make_tournament(client)
    with database.SessionLocal() as s:
        first = s.scalars(select(Tournament)).first()
        first_id = first.id

    # Archive current, then the welcome/new flow is available again.
    r = client.post("/setup/archive", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/setup/new"
    with database.SessionLocal() as s:
        assert s.get(Tournament, first_id).status == TournamentStatus.ARCHIVED

    # Home now shows the welcome screen (no active tournament).
    assert "Welcome" in client.get("/").text

    # Create a second tournament; it becomes the active one.
    client.post("/setup/new", data={
        "name": "Second Open", "entry_price": "5.00", "club_percent": "15",
        "first_percent": "60", "second_percent": "30", "third_percent": "10"})
    with database.SessionLocal() as s:
        active = s.scalars(
            select(Tournament).where(Tournament.status != TournamentStatus.ARCHIVED)).all()
        assert len(active) == 1 and active[0].name == "Second Open"


def test_reopen_archived_from_welcome(client):
    """After archiving, the past tournament is reachable to reopen from the welcome
    screen — you don't have to create a new one first."""
    make_tournament(client)
    with database.SessionLocal() as s:
        tid = s.scalars(select(Tournament)).first().id
    client.post("/setup/archive")

    home = client.get("/").text
    assert "Welcome" in home
    assert "Reopen a past tournament" in home  # archived tournament is offered here

    r = client.post(f"/setup/{tid}/reopen", follow_redirects=True)
    assert r.status_code == 200
    with database.SessionLocal() as s:
        assert s.get(Tournament, tid).status == TournamentStatus.CLOSED  # active again


def test_reopen_blocked_while_another_active(client):
    make_tournament(client)
    with database.SessionLocal() as s:
        t = s.scalars(select(Tournament)).first()
        tid = t.id
    client.post("/setup/archive")
    client.post("/setup/new", data={
        "name": "New One", "entry_price": "5.00", "club_percent": "15",
        "first_percent": "60", "second_percent": "30", "third_percent": "10"})
    # Reopening the archived one while another is active is refused.
    r = client.post(f"/setup/{tid}/reopen", follow_redirects=True)
    assert "Archive the current tournament" in r.text


def test_can_add_team_after_wagering_opened(client):
    """Teams/players can be added mid-event (spread the betting), and pages aren't cached."""
    make_tournament(client)  # creates 2 teams, players, opens wagering
    # Add a third team AFTER wagering is open — the button/flow must still work.
    r = client.post("/setup/teams", data={"team_name": "The Ringers"}, follow_redirects=True)
    assert r.status_code == 200
    with database.SessionLocal() as s:
        names = [t.name for t in s.scalars(select(Team).order_by(Team.id)).all()]
        assert "The Ringers" in names
    # The setup page still offers the add-team control (not hidden after open/sales).
    assert "Add a team" in client.get("/setup").text
    # HTML responses must not be cached (prevents stale "closed" screens).
    assert "no-store" in client.get("/cashier").headers.get("cache-control", "")


def test_duplicate_team_name_rejected(client):
    client.post("/setup/new", data={
        "name": "Dup Teams", "entry_price": "5.00", "club_percent": "15",
        "first_percent": "60", "second_percent": "30", "third_percent": "10"})
    client.post("/setup/teams", data={"team_name": "Front Nine"})
    r = client.post("/setup/teams", data={"team_name": "front nine"}, follow_redirects=True)  # case-insensitive
    assert "already a team called" in r.text
    with database.SessionLocal() as s:
        assert len(s.scalars(select(Team)).all()) == 1


def test_duplicate_player_names_skipped(client):
    from app.models import Player
    client.post("/setup/new", data={
        "name": "Dup Players", "entry_price": "5.00", "club_percent": "15",
        "first_percent": "60", "second_percent": "30", "third_percent": "10"})
    client.post("/setup/teams", data={"team_name": "A"})
    with database.SessionLocal() as s:
        tid = s.scalars(select(Team)).first().id
    # 'al' repeats 'Al' (case-insensitive) and the batch has 'Bob' twice.
    client.post(f"/setup/teams/{tid}/players", data={"players": "Al\nBob\nal\nBob\nCarol"})
    with database.SessionLocal() as s:
        names = sorted(p.name for p in s.scalars(select(Player).where(Player.team_id == tid)).all())
        assert names == ["Al", "Bob", "Carol"]
    # Adding an existing name again is skipped, not duplicated.
    client.post(f"/setup/teams/{tid}/players", data={"players": "Carol"})
    with database.SessionLocal() as s:
        assert len(s.scalars(select(Player).where(Player.team_id == tid)).all()) == 3


def test_single_team_can_open(client):
    """A tournament may run with just one team (per the tournament organiser)."""
    client.post("/setup/new", data={
        "name": "Solo Team Open", "entry_price": "5.00", "club_percent": "15",
        "first_percent": "60", "second_percent": "30", "third_percent": "10"})
    client.post("/setup/teams", data={"team_name": "The Field"})
    with database.SessionLocal() as s:
        team = s.scalars(select(Team)).first()
        client.post(f"/setup/teams/{team.id}/players", data={"players": "A\nB\nC"})
    r = client.post("/setup/open", follow_redirects=True)
    assert "now OPEN" in r.text
    with database.SessionLocal() as s:
        assert s.scalars(select(Team)).first().wagering_status == WageringStatus.OPEN


def test_three_teams_supported(client):
    """The app must not assume exactly two teams (spec: two OR MORE)."""
    client.post("/setup/new", data={
        "name": "Three Team Open", "entry_price": "5.00", "club_percent": "15",
        "first_percent": "60", "second_percent": "30", "third_percent": "10"})
    for name in ("Front Nine", "Back Nine", "The Ringers"):
        client.post("/setup/teams", data={"team_name": name})
    with database.SessionLocal() as s:
        teams = s.scalars(select(Team).order_by(Team.id)).all()
        assert len(teams) == 3
        for t in teams:
            client.post(f"/setup/teams/{t.id}/players", data={"players": "P1\nP2\nP3"})
    r = client.post("/setup/open", follow_redirects=True)
    assert "now OPEN" in r.text
    # Dashboard shows all three team cards.
    board = client.get("/dashboard").text
    for name in ("Front Nine", "Back Nine", "The Ringers"):
        assert name in board


def test_per_team_open_close(client):
    make_tournament(client, open_wagering=False)
    with database.SessionLocal() as s:
        team = s.scalars(select(Team).order_by(Team.id)).first()
        team_id = team.id
    client.post("/setup/open", data={"team_id": team_id})
    with database.SessionLocal() as s:
        teams = s.scalars(select(Team).order_by(Team.id)).all()
        assert teams[0].wagering_status == WageringStatus.OPEN
        assert teams[1].wagering_status == WageringStatus.CLOSED
