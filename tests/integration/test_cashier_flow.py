"""End-to-end wager flow: create customer, record wagers, void, totals reverse."""
from __future__ import annotations

from sqlalchemy import select

from app import database
from app.models import Customer, Player, Team, Wager
from app.services import teams as team_service
from tests.conftest import make_tournament, place_wager


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

    place_wager(client, customer_id, team_a_id, player_id, 4)
    assert totals(team_a_id) == (4, 2000)  # 4 x $5.00

    place_wager(client, customer_id, team_a_id, player_id, 2)
    assert totals(team_a_id) == (6, 3000)

    with new_session() as s:
        first_wager_id = s.scalars(select(Wager).order_by(Wager.id)).first().id
    r = client.post(f"/wagers/{first_wager_id}/void", data={"reason": "wrong player"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert totals(team_a_id) == (2, 1000)  # reversed exactly


def test_cart_takes_one_payment_for_multiple_players(client):
    make_tournament(client)
    with new_session() as s:
        team = s.scalars(select(Team).order_by(Team.id)).first()
        players = s.scalars(select(Player).where(Player.team_id == team.id).order_by(Player.id)).all()
        tid = team.id
        p1, p2, p3 = [p.id for p in players]
    client.post("/customers/new", data={"name": "Multi Backer"})
    with new_session() as s:
        cid = s.scalars(select(Customer)).first().id

    # Build one order across three players, then pay once.
    client.get(f"/cashier?customer_id={cid}")
    client.post("/cashier/cart/add", data={"team_id": tid, "player_id": p1, "quantity": "2"})
    client.post("/cashier/cart/add", data={"team_id": tid, "player_id": p2, "quantity": "1"})
    client.post("/cashier/cart/add", data={"team_id": tid, "player_id": p3, "quantity": "3"})
    r = client.post("/cashier/checkout", data={"received": "30.00"}, follow_redirects=True)
    assert "Recorded 6 entrie" in r.text

    with new_session() as s:
        wagers = s.scalars(select(Wager).where(Wager.customer_id == cid)).all()
        assert len(wagers) == 3  # three separate wager rows...
        assert sum(w.quantity for w in wagers) == 6  # ...from one payment
    assert totals(tid) == (6, 3000)


def test_cart_merges_repeat_player_and_removes_line(client):
    make_tournament(client)
    with new_session() as s:
        team = s.scalars(select(Team).order_by(Team.id)).first()
        players = s.scalars(select(Player).where(Player.team_id == team.id).order_by(Player.id)).all()
        tid, p1, p2 = team.id, players[0].id, players[1].id
    client.post("/customers/new", data={"name": "Cart Editor"})
    with new_session() as s:
        cid = s.scalars(select(Customer)).first().id
    client.get(f"/cashier?customer_id={cid}")
    client.post("/cashier/cart/add", data={"team_id": tid, "player_id": p1, "quantity": "2"})
    client.post("/cashier/cart/add", data={"team_id": tid, "player_id": p1, "quantity": "3"})  # merges -> 5
    client.post("/cashier/cart/add", data={"team_id": tid, "player_id": p2, "quantity": "1"})
    client.post("/cashier/cart/remove", data={"index": "1"})  # remove the p2 line
    r = client.post("/cashier/checkout", data={"received": "25.00"}, follow_redirects=True)
    assert "Recorded 5 entrie" in r.text
    assert totals(tid) == (5, 2500)


def test_htmx_add_returns_order_partial(client):
    """An Add with the HX-Request header returns just the order card, not a full page."""
    make_tournament(client)
    with new_session() as s:
        team = s.scalars(select(Team).order_by(Team.id)).first()
        tid, pid = team.id, _first_player(s, team.id).id
    client.post("/customers/new", data={"name": "Htmx Backer"})
    with new_session() as s:
        cid = s.scalars(select(Customer)).first().id
    client.get(f"/cashier?customer_id={cid}")
    r = client.post("/cashier/cart/add",
                    data={"team_id": tid, "player_id": pid, "quantity": "2"},
                    headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert 'id="order-card"' in r.text
    assert "<html" not in r.text.lower()  # partial, not the whole page
    assert "total due" in r.text


def test_undo_whole_order_by_reference(client):
    """One click voids every entry sharing an order reference."""
    make_tournament(client)
    with new_session() as s:
        team = s.scalars(select(Team).order_by(Team.id)).first()
        players = s.scalars(select(Player).where(Player.team_id == team.id).order_by(Player.id)).all()
        tid, p1, p2 = team.id, players[0].id, players[1].id
    client.post("/customers/new", data={"name": "Undo Backer"})
    with new_session() as s:
        cid = s.scalars(select(Customer)).first().id
    client.get(f"/cashier?customer_id={cid}")
    client.post("/cashier/cart/add", data={"team_id": tid, "player_id": p1, "quantity": "2"})
    client.post("/cashier/cart/add", data={"team_id": tid, "player_id": p2, "quantity": "3"})
    client.post("/cashier/checkout", data={"received": "25.00"})
    assert totals(tid) == (5, 2500)

    with new_session() as s:
        ref = s.scalars(select(Wager).order_by(Wager.id)).first().reference
    assert ref
    r = client.post("/cashier/void-order", data={"reference": ref}, follow_redirects=True)
    assert "undone" in r.text
    assert totals(tid) == (0, 0)  # both entries reversed atomically


def test_wager_blocked_when_team_closed(client):
    make_tournament(client, open_wagering=False)
    with new_session() as s:
        team_a = s.scalars(select(Team).order_by(Team.id)).first()
        player = _first_player(s, team_a.id)
        team_a_id, player_id = team_a.id, player.id
    client.post("/customers/new", data={"name": "Sam"})
    with new_session() as s:
        customer_id = s.scalars(select(Customer)).first().id

    place_wager(client, customer_id, team_a_id, player_id, 1)
    assert totals(team_a_id) == (0, 0)  # blocked (team closed) — nothing recorded


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

    # Player from team B added under team A must be rejected by the cart.
    place_wager(client, customer_id, team_a_id, player_b_id, 1)
    assert totals(team_a_id) == (0, 0)


def test_duplicate_customer_warning(client):
    make_tournament(client)
    client.post("/customers/new", data={"name": "Jamie", "phone": "204-555-1212"})
    r = client.post("/customers/new", data={"name": "Jamie R", "phone": "(204) 555 1212"})
    assert "might already be a customer" in r.text
    with new_session() as s:
        assert len(s.scalars(select(Customer)).all()) == 1


def test_duplicate_customer_name_warns(client):
    make_tournament(client)
    client.post("/customers/new", data={"name": "John Smith"})
    # Same name (case-insensitive), no contact info -> still surfaces the chooser.
    r = client.post("/customers/new", data={"name": "john smith"})
    assert "might already be a customer" in r.text
    with new_session() as s:
        assert len(s.scalars(select(Customer)).all()) == 1
    # Creating anyway (confirm_duplicate) is still allowed for genuine namesakes.
    client.post("/customers/new", data={"name": "john smith", "confirm_duplicate": "1"})
    with new_session() as s:
        assert len(s.scalars(select(Customer)).all()) == 2
