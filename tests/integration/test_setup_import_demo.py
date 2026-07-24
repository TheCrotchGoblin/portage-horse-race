"""Setup batch: CSV player import (SET-02), checklist (SET-03), demo (UX-07)."""
from __future__ import annotations

from sqlalchemy import func, select

from app import database
from app.models import Player, Tournament, Wager


def _new_tournament(client, name="Import Test"):
    client.post("/setup/new", data={
        "name": name, "entry_price": "5.00", "club_percent": "15",
        "first_percent": "60", "second_percent": "30", "third_percent": "10"})


def test_import_players_csv(client):
    _new_tournament(client)
    client.post("/setup/teams", data={"team_name": "Front Nine"})
    client.post("/setup/teams", data={"team_name": "Back Nine"})
    csv = "team,player\nFront Nine,Mike\nFront Nine,Dave\nfront nine,Mike\nBack Nine,Chris\nNope Team,Ghost\n"
    r = client.post("/setup/import-players",
                    files={"file": ("p.csv", csv.encode(), "text/csv")}, follow_redirects=True)
    assert "Imported 3" in r.text          # Mike, Dave, Chris (case-insensitive dup skipped)
    assert "Nope Team" in r.text            # unknown team reported
    with database.SessionLocal() as s:
        assert s.scalar(select(func.count(Player.id))) == 3


def test_setup_checklist_progress(client):
    _new_tournament(client)
    r = client.get("/setup")
    assert "Before you open wagering" in r.text
    assert "At least one team added" in r.text
    assert "In progress" in r.text  # no teams yet -> not ready


def test_load_demo_tournament(client):
    r = client.post("/setup/demo", follow_redirects=True)
    assert r.status_code == 200
    with database.SessionLocal() as s:
        t = s.scalars(select(Tournament)).first()
        assert t.name.startswith("DEMO")
        assert (s.scalar(select(func.count(Wager.id))) or 0) > 0  # sample wagers seeded
    assert "DEMO" in client.get("/").text


def test_demo_blocked_when_active(client):
    _new_tournament(client)
    r = client.post("/setup/demo", follow_redirects=True)
    assert "Archive the current tournament" in r.text
    with database.SessionLocal() as s:
        assert s.scalar(select(func.count(Tournament.id))) == 1  # no second tournament
