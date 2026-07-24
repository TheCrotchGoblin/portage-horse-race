"""End-to-end wager flow: create customer, record wagers, void, totals reverse."""
from __future__ import annotations

from sqlalchemy import select

from app import database
from app.models import Customer, Player, Team, Wager
from app.services import teams as team_service
from tests.conftest import make_tournament


def new_session():
    """A fresh session bound to the CURRENT engine (avoids stale-import + WAL snapshot)."""
    return database.SessionLocal()


def totals(team_id):
    with new_session() as s:
        return team_service.team_totals(s, team_id)


def _first_player(session, team_id):
    return session.scalars(select(Player).where(Player.team_id == team_id).order_by(Player.id)).first()


def test_record_and_void_reverses_totals(client):
    make_tournament(client)
    with new_session() as s:
        team_a = s.scalars(select(Team).order_by(Team.id)).first()
        player = _first_player(s, team_a.id)
        team_a_id, player_id = team_a.id, player.id

    client.post("/customers/new", data={"name": "Pat Backer", "return_to": "cashier"})
    with new_session() as s:
        customer_id = s.scalars(select(Customer)).first().id

    r = client.post("/wagers", data={
        "customer_id": customer_id, "team_id": team_a_id,
        "player_id": player_id, "quantity": "4", "received": "20.00",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert totals(team_a_id) == (4, 2000)  # 4 x $5.00

    client.post("/wagers", data={
        "customer_id": customer_id, "team_id": team_a_id,
        "player_id": player_id, "quantity": "2", "received": "10.00",
    })
    assert totals(team_a_id) == (6, 3000)

    with new_session() as s:
        first_wager_id = s.scalars(select(Wager).order_by(Wager.id)).first().id
    r = client.post(f"/wagers/{first_wager_id}/void", data={"reason": "wrong player"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert totals(team_a_id) == (2, 1000)  # reversed exactly


def test_wager_blocked_when_team_closed(client):
    make_tournament(client, open_wagering=False)
    with new_session() as s:
        team_a = s.scalars(select(Team).order_by(Team.id)).first()
        player = _first_player(s, team_a.id)
        team_a_id, player_id = team_a.id, player.id
    client.post("/customers/new", data={"name": "Sam"})
    with new_session() as s:
        customer_id = s.scalars(select(Customer)).first().id

    client.post("/wagers", data={
        "customer_id": customer_id, "team_id": team_a_id, "player_id": player_id, "quantity": "1",
    })
    assert totals(team_a_id) == (0, 0)  # blocked, nothing recorded


def test_player_must_belong_to_team(client):
    make_tournament(client)
    with new_session() as s:
        teams = s.scalars(select(Team).order_by(Team.id)).all()
        team_a_id = teams[0].id
        player_b = _first_player(s, teams[1].id)
        player_b_id = player_b.id
    client.post("/customers/new", data={"name": "Mix Up"})
    with new_session() as s:
        customer_id = s.scalars(select(Customer)).first().id

    client.post("/wagers", data={
        "customer_id": customer_id, "team_id": team_a_id, "player_id": player_b_id, "quantity": "1",
    })
    assert totals(team_a_id) == (0, 0)


def test_duplicate_customer_warning(client):
    make_tournament(client)
    client.post("/customers/new", data={"name": "Jamie", "phone": "204-555-1212"})
    r = client.post("/customers/new", data={"name": "Jamie R", "phone": "(204) 555 1212"})
    assert "might already be a customer" in r.text
    with new_session() as s:
        assert len(s.scalars(select(Customer)).all()) == 1
