"""Results -> payouts settlement: worked example, exact reconciliation, pay-once."""
from __future__ import annotations

from sqlalchemy import select

from app import database
from app.models import Customer, Payout, Placement, Player, Team, Tournament
from app.models.enums import PayoutStatus
from app.services.payouts import unclaimed_placements
from tests.conftest import make_tournament, place_wager


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
    place_wager(client, cid, team_id, player_id, qty)


def test_other_team_not_flagged_unclaimed_before_generation(client):
    """Recording results for a team and generating payouts for ONE team must not
    make the other (ungenerated) team's pools show as unclaimed."""
    make_tournament(client)
    with database.SessionLocal() as s:
        teams = s.scalars(select(Team).order_by(Team.id)).all()
        ta_id, tb_id = teams[0].id, teams[1].id
        pa = [p.id for p in s.scalars(select(Player).where(Player.team_id == ta_id).order_by(Player.id)).all()]
        pb = [p.id for p in s.scalars(select(Player).where(Player.team_id == tb_id).order_by(Player.id)).all()]
    cid = _customer(client, "Backer")
    for pid in pa:
        _wager(client, cid, ta_id, pid, 2)
    for pid in pb:
        _wager(client, cid, tb_id, pid, 2)

    # Record results for BOTH teams, but generate payouts for team A only.
    client.post(f"/results/{ta_id}/placements",
                data={"first_player_id": pa[0], "second_player_id": pa[1], "third_player_id": pa[2]})
    client.post(f"/results/{tb_id}/placements",
                data={"first_player_id": pb[0], "second_player_id": pb[1], "third_player_id": pb[2]})
    client.post(f"/results/{ta_id}/generate")

    # Team B is pending payout generation — NOT unclaimed.
    r = client.get("/payouts")
    assert "Unclaimed pools" not in r.text
    with database.SessionLocal() as s:
        tid = s.scalars(select(Tournament)).first().id
        assert unclaimed_placements(s, tid) == []


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

    # The payouts screen surfaces the unclaimed pools needing a decision.
    r = client.get("/payouts")
    assert "Unclaimed pools" in r.text

    # Record a disposition for each unclaimed placement.
    with database.SessionLocal() as s:
        unclaimed = s.scalars(
            select(Placement).where(Placement.team_id == team_id, Placement.position.in_([2, 3]))
        ).all()
        unclaimed_ids = [p.id for p in unclaimed]
    for pid in unclaimed_ids:
        r = client.post(f"/payouts/placements/{pid}/dispose",
                        data={"disposition": "return_to_club", "note": ""}, follow_redirects=False)
        assert r.status_code == 303

    with database.SessionLocal() as s:
        for pid in unclaimed_ids:
            assert s.get(Placement, pid).disposition == "return_to_club"
