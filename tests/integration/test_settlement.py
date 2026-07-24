"""Results -> payouts settlement: worked example, exact reconciliation, pay-once."""
from __future__ import annotations

from sqlalchemy import select

from app import database
from app.models import Customer, Payout, Placement, Player, Team
from app.models.enums import PayoutStatus
from tests.conftest import make_tournament


def _setup_players(client):
    with database.SessionLocal() as s:
        team = s.scalars(select(Team).order_by(Team.id)).first()
        players = s.scalars(select(Player).where(Player.team_id == team.id).order_by(Player.id)).all()
        return team.id, [p.id for p in players]


def _customer(client, name):
    client.post("/customers/new", data={"name": name})
    with database.SessionLocal() as s:
        return s.scalars(select(Customer).where(Customer.name == name)).first().id


def _wager(client, cid, team_id, player_id, qty):
    client.post("/wagers", data={"customer_id": cid, "team_id": team_id, "player_id": player_id, "quantity": str(qty)})


def test_worked_example_settlement_reconciles(client):
    make_tournament(client)
    team_id, (alice, bob, carol) = _setup_players(client)

    c1 = _customer(client, "C1")
    c2 = _customer(client, "C2")
    c3 = _customer(client, "C3")

    # 5 winning entries on Alice (1st): C1 owns 2, C2 owns 3.
    _wager(client, c1, team_id, alice, 2)
    _wager(client, c2, team_id, alice, 3)
    # 295 more entries so team gross = $1,500 (300 x $5).
    _wager(client, c3, team_id, bob, 200)
    _wager(client, c3, team_id, carol, 95)

    # Set placements: Alice 1st, Bob 2nd, Carol 3rd.
    r = client.post(f"/results/{team_id}/placements", data={
        "first_player_id": alice, "second_player_id": bob, "third_player_id": carol,
    }, follow_redirects=False)
    assert r.status_code == 303

    r = client.post(f"/results/{team_id}/generate", follow_redirects=False)
    assert r.status_code == 303

    with database.SessionLocal() as s:
        payouts = s.scalars(
            select(Payout).join(Placement, Payout.placement_id == Placement.id)
            .where(Placement.team_id == team_id)
        ).all()
        by_customer_first = {}
        total = 0
        for p in payouts:
            total += p.amount_cents
            if p.placement.position == 1:
                by_customer_first[p.customer_id] = p.amount_cents

        # First pool $765 across 5 entries -> C1 (2) = $306, C2 (3) = $459.
        assert by_customer_first[c1] == 30600
        assert by_customer_first[c2] == 45900
        # Whole prize pool reconciles exactly: 765 + 382.50 + 127.50 = $1,275.00.
        assert total == 127500


def test_pay_once_blocks_duplicate(client):
    make_tournament(client)
    team_id, (alice, bob, carol) = _setup_players(client)
    c1 = _customer(client, "Winner")
    _wager(client, c1, team_id, alice, 4)
    _wager(client, c1, team_id, bob, 1)
    _wager(client, c1, team_id, carol, 1)
    client.post(f"/results/{team_id}/placements", data={
        "first_player_id": alice, "second_player_id": bob, "third_player_id": carol})
    client.post(f"/results/{team_id}/generate")

    with database.SessionLocal() as s:
        payout = s.scalars(select(Payout)).first()
        pid = payout.id

    client.post(f"/payouts/{pid}/pay", data={"method": "cash"})
    with database.SessionLocal() as s:
        assert s.get(Payout, pid).status == PayoutStatus.PAID

    # Second attempt is blocked; status stays PAID and no double count.
    r = client.post(f"/payouts/{pid}/pay", data={"method": "cash"}, follow_redirects=True)
    assert "already been paid" in r.text
    with database.SessionLocal() as s:
        assert s.get(Payout, pid).status == PayoutStatus.PAID


def test_unclaimed_pool_when_placed_player_has_no_wagers(client):
    make_tournament(client)
    team_id, (alice, bob, carol) = _setup_players(client)
    c1 = _customer(client, "Only Alice")
    _wager(client, c1, team_id, alice, 10)  # Bob & Carol get no wagers

    client.post(f"/results/{team_id}/placements", data={
        "first_player_id": alice, "second_player_id": bob, "third_player_id": carol})
    r = client.post(f"/results/{team_id}/generate", follow_redirects=True)
    assert "UNCLAIMED" in r.text

    with database.SessionLocal() as s:
        payouts = s.scalars(
            select(Payout).join(Placement, Payout.placement_id == Placement.id)
            .where(Placement.team_id == team_id)
        ).all()
        # Only Alice's first-place pool is paid out; 2nd and 3rd are unclaimed.
        assert all(p.placement.position == 1 for p in payouts)
