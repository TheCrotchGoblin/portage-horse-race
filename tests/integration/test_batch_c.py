"""Cashier/settlement batch: reference codes, repeat-customer, settlement lock, contact."""
from __future__ import annotations

from sqlalchemy import select

from app import database
from app.models import Customer, Payout, Player, Team, Tournament, Wager
from app.models.enums import TournamentStatus
from tests.conftest import make_tournament, place_wager


def _first_team_players(client):
    with database.SessionLocal() as s:
        team = s.scalars(select(Team).order_by(Team.id)).first()
        players = [p.id for p in s.scalars(select(Player).where(Player.team_id == team.id).order_by(Player.id)).all()]
        return team.id, players


def _customer(client, name):
    client.post("/customers/new", data={"name": name})
    with database.SessionLocal() as s:
        return s.scalars(select(Customer).where(Customer.name == name)).first().id


def test_order_reference_and_repeat_customer(client):
    make_tournament(client)
    team_id, players = _first_team_players(client)
    cid = _customer(client, "Ref Buyer")
    client.get(f"/cashier?customer_id={cid}")
    client.post("/cashier/cart/add", data={"team_id": team_id, "player_id": players[0], "quantity": "2"})
    client.post("/cashier/cart/add", data={"team_id": team_id, "player_id": players[1], "quantity": "1"})
    r = client.post("/cashier/checkout", data={"received": "15.00"}, follow_redirects=True)
    assert "Reference" in r.text  # flash includes the order reference

    with database.SessionLocal() as s:
        refs = {w.reference for w in s.scalars(select(Wager)).all()}
        assert len(refs) == 1 and next(iter(refs))  # one shared, non-empty reference

    # POS-03: the just-served customer is offered for a quick repeat order.
    assert "Another order for Ref Buyer" in client.get("/cashier").text


def test_contact_tracking(client):
    make_tournament(client)
    team_id, players = _first_team_players(client)
    cid = _customer(client, "Winner")
    for pid in players[:3]:
        place_wager(client, cid, team_id, pid, 1)
    client.post(f"/results/{team_id}/placements",
                data={"first_player_id": players[0], "second_player_id": players[1], "third_player_id": players[2]})
    client.post("/setup/close")
    client.post(f"/results/{team_id}/generate")
    with database.SessionLocal() as s:
        pid = s.scalars(select(Payout)).first().id
    client.post(f"/payouts/{pid}/contact", data={"contact_status": "called", "note": "left a message"})
    with database.SessionLocal() as s:
        p = s.get(Payout, pid)
        assert p.contact_status == "called" and p.contact_note == "left a message" and p.contacted_at is not None


def _single_team_settled(client):
    client.post("/setup/new", data={
        "name": "Solo Settle", "entry_price": "5.00", "club_percent": "15",
        "first_percent": "60", "second_percent": "30", "third_percent": "10"})
    client.post("/setup/teams", data={"team_name": "The Field"})
    with database.SessionLocal() as s:
        team = s.scalars(select(Team)).first()
        team_id = team.id
        client.post(f"/setup/teams/{team_id}/players", data={"players": "A\nB\nC"})
    client.post("/setup/open")
    with database.SessionLocal() as s:
        players = [p.id for p in s.scalars(select(Player).order_by(Player.id)).all()]
    cid = _customer(client, "Solo Cust")
    for pid in players:
        place_wager(client, cid, team_id, pid, 1)
    client.post(f"/results/{team_id}/placements",
                data={"first_player_id": players[0], "second_player_id": players[1], "third_player_id": players[2]})
    client.post("/setup/close")
    client.post(f"/results/{team_id}/generate")
    with database.SessionLocal() as s:
        pids = [p.id for p in s.scalars(select(Payout)).all()]
    for pid in pids:
        client.post(f"/payouts/{pid}/pay", data={"method": "cash"})
    return pids


def test_settlement_locks_then_reopens(client):
    pids = _single_team_settled(client)
    with database.SessionLocal() as s:
        assert s.scalars(select(Tournament)).first().status == TournamentStatus.SETTLED

    # Reversal is blocked while settled.
    r = client.post(f"/payouts/{pids[0]}/reverse", data={"reason": "oops"}, follow_redirects=True)
    assert "settled and locked" in r.text
    with database.SessionLocal() as s:
        from app.models.enums import PayoutStatus
        assert s.get(Payout, pids[0]).status == PayoutStatus.PAID  # unchanged

    # Reopen (audited), then reversal works.
    client.post("/payouts/reopen", data={"reason": "correction needed"})
    with database.SessionLocal() as s:
        assert s.scalars(select(Tournament)).first().status == TournamentStatus.PAYOUTS_GENERATED
    client.post(f"/payouts/{pids[0]}/reverse", data={"reason": "wrong person"})
    with database.SessionLocal() as s:
        from app.models.enums import PayoutStatus
        assert s.get(Payout, pids[0]).status == PayoutStatus.REVERSED


def test_reversed_payout_blocks_resettle_until_resolved(client):
    from app.models.enums import PayoutStatus
    from app.services.payouts import check_settled

    pids = _single_team_settled(client)  # SETTLED
    client.post("/payouts/reopen", data={"reason": "fix"})
    client.post(f"/payouts/{pids[0]}/reverse", data={"reason": "clawed back"})

    # A reversed payout is money still owed -> check_settled must NOT re-settle.
    with database.SessionLocal() as s:
        t = s.scalars(select(Tournament)).first()
        check_settled(s, t)
        s.flush()
        assert t.status == TournamentStatus.PAYOUTS_GENERATED

    # The payouts screen offers a "Mark paid" affordance on the reversed row.
    assert f'/payouts/{pids[0]}/pay' in client.get("/payouts").text

    # Re-paying the reversed winner resolves it -> the event settles again.
    client.post(f"/payouts/{pids[0]}/pay", data={"method": "cash"})
    with database.SessionLocal() as s:
        assert s.get(Payout, pids[0]).status == PayoutStatus.PAID
        assert s.scalars(select(Tournament)).first().status == TournamentStatus.SETTLED


def test_dispose_blocked_when_settled(client):
    """Changing an unclaimed-pool disposition is locked once the event is settled."""
    # Single team; 1st-place player has wagers, 2nd/3rd do not -> unclaimed pools.
    client.post("/setup/new", data={
        "name": "Dispose Lock", "entry_price": "5.00", "club_percent": "15",
        "first_percent": "60", "second_percent": "30", "third_percent": "10"})
    client.post("/setup/teams", data={"team_name": "Solo"})
    with database.SessionLocal() as s:
        team_id = s.scalars(select(Team)).first().id
    client.post(f"/setup/teams/{team_id}/players", data={"players": "A\nB\nC"})
    client.post("/setup/open")
    with database.SessionLocal() as s:
        players = [p.id for p in s.scalars(select(Player).order_by(Player.id)).all()]
    cid = _customer(client, "Only First")
    place_wager(client, cid, team_id, players[0], 4)  # only the 1st-place player has wagers
    client.post(f"/results/{team_id}/placements",
                data={"first_player_id": players[0], "second_player_id": players[1], "third_player_id": players[2]})
    client.post("/setup/close")
    client.post(f"/results/{team_id}/generate")
    # Dispose the two unclaimed pools, then pay the one real payout -> SETTLED.
    with database.SessionLocal() as s:
        from app.models import Placement
        unclaimed_ids = [p.id for p in s.scalars(
            select(Placement).where(Placement.team_id == team_id, Placement.position.in_([2, 3]))).all()]
        payout_id = s.scalars(select(Payout)).first().id
    for pl in unclaimed_ids:
        client.post(f"/payouts/placements/{pl}/dispose", data={"disposition": "return_to_club", "note": ""})
    client.post(f"/payouts/{payout_id}/pay", data={"method": "cash"})
    with database.SessionLocal() as s:
        assert s.scalars(select(Tournament)).first().status == TournamentStatus.SETTLED

    # Now attempting to change a disposition is blocked (locked).
    r = client.post(f"/payouts/placements/{unclaimed_ids[0]}/dispose",
                    data={"disposition": "carryover", "note": ""}, follow_redirects=True)
    assert "settled and locked" in r.text
    with database.SessionLocal() as s:
        from app.models import Placement
        assert s.get(Placement, unclaimed_ids[0]).disposition == "return_to_club"  # unchanged
