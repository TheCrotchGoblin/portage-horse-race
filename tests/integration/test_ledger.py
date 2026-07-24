"""Ledger filtering and audit-log rendering."""
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


def test_ledger_lists_and_filters(client):
    make_tournament(client)
    team_id, player_id = _ids(client)
    client.post("/customers/new", data={"name": "Dana"})
    with database.SessionLocal() as s:
        cid = s.scalars(select(Customer)).first().id
    place_wager(client, cid, team_id, player_id, 3)

    r = client.get("/ledger")
    assert r.status_code == 200
    assert "Dana" in r.text

    # Filter by the other team -> should show no rows for Dana's wager.
    with database.SessionLocal() as s:
        other = s.scalars(select(Team).order_by(Team.id)).all()[1]
    r = client.get(f"/ledger?team_id={other.id}")
    assert "No transactions match" in r.text


def test_audit_log_records_actions(client):
    make_tournament(client)
    r = client.get("/ledger/audit")
    assert r.status_code == 200
    # Setup actions are audited.
    assert "tournament_created" in r.text
    assert "wagering_opened" in r.text
