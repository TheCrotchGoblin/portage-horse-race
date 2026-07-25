"""Setup reuse: clone event, rename/move players, CSV creates teams, editable name."""
from __future__ import annotations

from sqlalchemy import select

from app import database
from app.models import Customer, Player, Team, Tournament, Wager
from app.models.enums import TournamentStatus
from tests.conftest import make_tournament, place_wager


def _teams():
    with database.SessionLocal() as s:
        return list(s.scalars(select(Team).order_by(Team.id)).all())


def test_clone_copies_teams_players_config_not_wagers(client):
    make_tournament(client)  # 2 teams, 3 players each, price/percent defaults
    teams = _teams()
    client.post("/customers/new", data={"name": "Backer"})
    with database.SessionLocal() as s:
        cid = s.scalars(select(Customer)).first().id
        pid = s.scalars(select(Player).where(Player.team_id == teams[0].id)).first().id
    place_wager(client, cid, teams[0].id, pid, 3)
    client.post("/setup/archive")

    with database.SessionLocal() as s:
        src = s.scalars(select(Tournament)).first()
        src_id = src.id
    r = client.post(f"/setup/{src_id}/clone",
                    data={"name": "Portage Men's Open 2099", "event_date": "2099-07-01"},
                    follow_redirects=True)
    assert r.status_code == 200
    with database.SessionLocal() as s:
        clone = s.scalars(select(Tournament).where(Tournament.name == "Portage Men's Open 2099")).first()
        assert clone is not None and clone.status == TournamentStatus.DRAFT
        assert clone.entry_price_cents == src.entry_price_cents
        clone_teams = s.scalars(select(Team).where(Team.tournament_id == clone.id)).all()
        assert len(clone_teams) == 2
        players = s.scalars(select(Player).join(Team).where(Team.tournament_id == clone.id)).all()
        assert len(players) == 6            # players copied
        wagers = s.scalars(select(Wager).where(Wager.tournament_id == clone.id)).all()
        assert wagers == []                  # wagers NOT copied


def test_rename_player_after_sales(client):
    make_tournament(client)
    teams = _teams()
    client.post("/customers/new", data={"name": "Backer"})
    with database.SessionLocal() as s:
        cid = s.scalars(select(Customer)).first().id
        p = s.scalars(select(Player).where(Player.team_id == teams[0].id)).first()
        pid = p.id
    place_wager(client, cid, teams[0].id, pid, 2)  # player now has wagers
    r = client.post(f"/setup/players/{pid}/rename", data={"player_name": "Alice Fixed"},
                    follow_redirects=True)
    assert r.status_code == 200
    with database.SessionLocal() as s:
        assert s.get(Player, pid).name == "Alice Fixed"  # allowed despite wagers


def test_move_player_blocked_after_wagers(client):
    make_tournament(client)
    teams = _teams()
    client.post("/customers/new", data={"name": "Backer"})
    with database.SessionLocal() as s:
        cid = s.scalars(select(Customer)).first().id
        pid = s.scalars(select(Player).where(Player.team_id == teams[0].id)).first().id
    place_wager(client, cid, teams[0].id, pid, 1)
    client.post(f"/setup/players/{pid}/move", data={"target_team_id": teams[1].id})
    with database.SessionLocal() as s:
        assert s.get(Player, pid).team_id == teams[0].id  # unchanged — blocked


def test_csv_import_creates_missing_teams(client):
    # Fresh tournament, no teams yet.
    client.post("/setup/new", data={"name": "CSV Open", "entry_price": "5.00",
                "club_percent": "15", "first_percent": "60", "second_percent": "30", "third_percent": "10"})
    csv = "team,player\nWildcats,Jane Doe\nWildcats,John Smith\nSharks,Amy Lin\n"
    r = client.post("/setup/import-players",
                    files={"file": ("roster.csv", csv, "text/csv")},
                    data={"create_teams": "1"}, follow_redirects=True)
    assert r.status_code == 200
    with database.SessionLocal() as s:
        teams = s.scalars(select(Team)).all()
        assert {t.name for t in teams} == {"Wildcats", "Sharks"}
        assert len(s.scalars(select(Player)).all()) == 3


def test_event_name_editable_after_sales(client):
    make_tournament(client)
    teams = _teams()
    client.post("/customers/new", data={"name": "Backer"})
    with database.SessionLocal() as s:
        cid = s.scalars(select(Customer)).first().id
        pid = s.scalars(select(Player).where(Player.team_id == teams[0].id)).first().id
    place_wager(client, cid, teams[0].id, pid, 1)  # sales started -> money locked
    r = client.post("/setup/details", data={"name": "Renamed After Sales", "event_date": "2030-01-01"},
                    follow_redirects=True)
    assert r.status_code == 200
    with database.SessionLocal() as s:
        t = s.scalars(select(Tournament)).first()
        assert t.name == "Renamed After Sales" and t.event_date == "2030-01-01"
