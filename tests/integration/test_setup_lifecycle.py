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
