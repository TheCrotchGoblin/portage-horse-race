"""Deleting a customer and clearing unused customers (keeps anyone with wagers)."""
from __future__ import annotations

from sqlalchemy import select

from app import database
from app.models import Customer, Player, Team
from tests.conftest import make_tournament, place_wager


def _ids(client):
    with database.SessionLocal() as s:
        team = s.scalars(select(Team).order_by(Team.id)).first()
        player = s.scalars(select(Player).where(Player.team_id == team.id).order_by(Player.id)).first()
        return team.id, player.id


def _new(client, name):
    client.post("/customers/new", data={"name": name})
    with database.SessionLocal() as s:
        return s.scalars(select(Customer).where(Customer.name == name)).first().id


def test_delete_customer_without_wagers(client):
    make_tournament(client)
    cid = _new(client, "Deletable")
    r = client.post(f"/customers/{cid}/delete", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/customers"
    with database.SessionLocal() as s:
        assert s.get(Customer, cid) is None


def test_delete_customer_with_wagers_blocked(client):
    make_tournament(client)
    team_id, player_id = _ids(client)
    cid = _new(client, "Has Wagers")
    place_wager(client, cid, team_id, player_id, 2)
    r = client.post(f"/customers/{cid}/delete", follow_redirects=True)
    assert "can't be deleted" in r.text.lower() or "void those wagers" in r.text.lower()
    with database.SessionLocal() as s:
        assert s.get(Customer, cid) is not None  # kept


def test_customer_history_is_lifetime_and_labelled(client):
    make_tournament(client)  # creates "Test Open"
    team_id, player_id = _ids(client)
    cid = _new(client, "Historian")
    place_wager(client, cid, team_id, player_id, 2)
    r = client.get(f"/customers/{cid}")
    assert "all tournaments" in r.text.lower()  # scope stated
    assert "Test Open" in r.text                # each wager labelled with its tournament


def test_clear_unused_customers(client):
    make_tournament(client)
    team_id, player_id = _ids(client)
    unused = _new(client, "Nobody")
    active = _new(client, "Bettor")
    place_wager(client, active, team_id, player_id, 1)

    r = client.post("/customers/clear", follow_redirects=True)
    assert "removed 1 unused" in r.text.lower()
    with database.SessionLocal() as s:
        assert s.get(Customer, unused) is None      # wager-free -> removed
        assert s.get(Customer, active) is not None  # has wagers -> kept
