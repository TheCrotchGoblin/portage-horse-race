"""Batch 4: checkout receipt, order lookup, winner notices/call sheet, waive/donate."""
from __future__ import annotations

from sqlalchemy import select

from app import database
from app.models import Customer, Payout, Player, Team, Tournament
from app.models.enums import PayoutStatus, TournamentStatus
from tests.conftest import make_tournament, place_wager


def _players(team_id):
    with database.SessionLocal() as s:
        return [p.id for p in s.scalars(select(Player).where(Player.team_id == team_id).order_by(Player.id)).all()]


def _customer(client, name):
    client.post("/customers/new", data={"name": name})
    with database.SessionLocal() as s:
        return s.scalars(select(Customer).where(Customer.name == name)).first().id


def _generate_one_team_payouts(client):
    """Two winners on team A; returns (team_id, player_ids)."""
    make_tournament(client)
    with database.SessionLocal() as s:
        team = s.scalars(select(Team).order_by(Team.id)).first()
        tid = team.id
    p = _players(tid)
    c1 = _customer(client, "Winner One")
    c2 = _customer(client, "Winner Two")
    place_wager(client, c1, tid, p[0], 3)   # 1st place -> both share
    place_wager(client, c2, tid, p[0], 2)
    place_wager(client, c1, tid, p[1], 1)   # 2nd place
    place_wager(client, c2, tid, p[2], 1)   # 3rd place
    client.post(f"/results/{tid}/placements",
                data={"first_player_id": p[0], "second_player_id": p[1], "third_player_id": p[2]})
    client.post("/setup/close")
    client.post(f"/results/{tid}/generate")
    return tid, p


def test_checkout_receipt_and_order_lookup(client):
    make_tournament(client)
    with database.SessionLocal() as s:
        tid = s.scalars(select(Team).order_by(Team.id)).first().id
    p = _players(tid)
    cid = _customer(client, "Receipt Backer")
    client.get(f"/cashier?customer_id={cid}")
    client.post("/cashier/cart/add", data={"team_id": tid, "player_id": p[0], "quantity": "3"})
    client.post("/cashier/checkout", data={"received": "20.00"})

    with database.SessionLocal() as s:
        from app.models import Wager
        ref = s.scalars(select(Wager)).first().reference
    # Receipt renders with the reference and total.
    r = client.get(f"/cashier/receipt/{ref}")
    assert r.status_code == 200
    assert ref in r.text and "$15.00" in r.text and "Receipt Backer" in r.text
    # Order lookup finds the whole order.
    r2 = client.get(f"/orders?reference={ref}")
    assert r2.status_code == 200 and "Receipt Backer" in r2.text


def test_waive_closes_out_and_clears_outstanding(client):
    """A waived payout becomes a resolved outcome — WAIVED, no longer counted as
    money owed, so it stops blocking settlement (FIN-08)."""
    tid, p = _generate_one_team_payouts(client)
    with database.SessionLocal() as s:
        unpaid = s.scalars(select(Payout).where(Payout.status == PayoutStatus.UNPAID)).all()
        target = unpaid[-1].id
        outstanding_before = sum(1 for x in unpaid if x.status in PayoutStatus.OUTSTANDING)

    r = client.post(f"/payouts/{target}/waive",
                    data={"kind": "donated", "reason": "unreachable after 3 calls"},
                    follow_redirects=True)
    assert r.status_code == 200
    with database.SessionLocal() as s:
        waived = s.get(Payout, target)
        assert waived.status == PayoutStatus.WAIVED
        assert waived.payment_method == "donated"
        outstanding_now = s.scalars(
            select(Payout).where(Payout.status.in_(PayoutStatus.OUTSTANDING))).all()
        assert target not in [x.id for x in outstanding_now]  # no longer owed
        assert len(outstanding_now) == outstanding_before - 1
    # It shows in the settled section, not the to-pay list.
    page = client.get("/payouts").text
    assert "Waived" in page


def test_waived_money_shows_in_reconciliation_not_unclaimed(client):
    """Regression: a waived payout must appear as 'Waived/donated', NOT be
    mislabelled as an unclaimed pool needing a decision."""
    tid, p = _generate_one_team_payouts(client)
    with database.SessionLocal() as s:
        target = s.scalars(select(Payout).where(Payout.status == PayoutStatus.UNPAID)).first().id
    client.post(f"/payouts/{target}/waive", data={"kind": "donated", "reason": "unreachable"})
    recon = client.get("/reports/print/reconciliation").text
    assert "Waived / donated" in recon
    hand = client.get("/reports/print/handover").text
    assert "Waived / donated" in hand


def test_winner_notices_and_call_sheet(client):
    _generate_one_team_payouts(client)
    r = client.get("/reports/print/winner-notices")
    assert r.status_code == 200 and "You won" in r.text
    r2 = client.get("/reports/print/call-sheet")
    assert r2.status_code == 200 and "Outstanding winners" in r2.text
    r3 = client.get("/reports/export/call-sheet")
    assert r3.status_code == 200 and "customer,phone,email" in r3.text
