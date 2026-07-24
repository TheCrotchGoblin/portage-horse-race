"""Dashboard reconciliation and cash-count variance (spec §7.3, FR-082, FR-083)."""
from __future__ import annotations

from sqlalchemy import select

from app import database
from app.models import Customer, Player, Team
from tests.conftest import make_tournament


def test_dashboard_matches_reference_scenario(client):
    """300 entries x $5 -> $1500 gross, $225 club, $1275 pool, 765/382.50/127.50."""
    make_tournament(client)
    with database.SessionLocal() as s:
        team = s.scalars(select(Team).order_by(Team.id)).first()
        player = s.scalars(select(Player).where(Player.team_id == team.id).order_by(Player.id)).first()
        team_id, player_id = team.id, player.id
    client.post("/customers/new", data={"name": "Big Backer"})
    with database.SessionLocal() as s:
        cid = s.scalars(select(Customer)).first().id

    client.post("/wagers", data={"customer_id": cid, "team_id": team_id, "player_id": player_id, "quantity": "300"})

    r = client.get("/")
    assert r.status_code == 200
    for amount in ["$1,500.00", "$225.00", "$1,275.00", "$765.00", "$382.50", "$127.50"]:
        assert amount in r.text, f"missing {amount} on dashboard"


def test_cash_count_variance(client):
    make_tournament(client)
    with database.SessionLocal() as s:
        team = s.scalars(select(Team).order_by(Team.id)).first()
        player = s.scalars(select(Player).where(Player.team_id == team.id).order_by(Player.id)).first()
        team_id, player_id = team.id, player.id
    client.post("/customers/new", data={"name": "C"})
    with database.SessionLocal() as s:
        cid = s.scalars(select(Customer)).first().id
    client.post("/wagers", data={"customer_id": cid, "team_id": team_id, "player_id": player_id, "quantity": "10"})
    # Expected cash = $50.00; count $45 -> $5 short.
    client.post(f"/teams/{team_id}/cash-count", data={"counted": "45.00"})

    r = client.get("/")
    assert "short" in r.text
