"""Dashboard reconciliation and cash-count variance (spec §7.3, FR-082, FR-083)."""
from __future__ import annotations

from sqlalchemy import select

from app import database
from app.models import Customer, Player, Team
from tests.conftest import make_tournament, place_wager


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

    place_wager(client, cid, team_id, player_id, 300)

    r = client.get("/dashboard")
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
    place_wager(client, cid, team_id, player_id, 10)
    # Expected cash = $50.00; count $45 -> $5 short.
    client.post(f"/teams/{team_id}/cash-count", data={"counted": "45.00"})

    r = client.get("/dashboard")
    assert "short" in r.text


def test_drawer_balances_with_float_and_cash_payout(client):
    """The box balances when counted = float + cash in - cash paid out.

    Guards the FIN-05 fix: the old variance was counted - gross, which ignored
    the opening float and any prize money already paid from the same box.
    """
    from app.services import cash as cash_service
    from app.models import CashCount, Payout, Placement
    from app.models.enums import CashCountKind, PayoutStatus

    make_tournament(client)
    with database.SessionLocal() as s:
        team = s.scalars(select(Team).order_by(Team.id)).first()
        player = s.scalars(select(Player).where(Player.team_id == team.id).order_by(Player.id)).first()
        team_id, player_id = team.id, player.id
    client.post("/customers/new", data={"name": "C"})
    with database.SessionLocal() as s:
        cid = s.scalars(select(Customer)).first().id
    place_wager(client, cid, team_id, player_id, 10)  # $50 taken in

    # $100 opening float for making change.
    client.post(f"/teams/{team_id}/cash-count", data={"kind": "float", "counted": "100.00"})

    with database.SessionLocal() as s:
        team = s.get(Team, team_id)
        d = cash_service.team_drawer(s, team, 5000)
        assert d.opening_float_cents == 10000
        assert d.expected_cents == 15000  # 100 float + 50 in - 0 out

        # Simulate a $20 cash prize paid from the box.
        placement = Placement(team_id=team_id, position=1, player_id=player_id, allocated_pool_cents=2000)
        s.add(placement)
        s.flush()
        s.add(Payout(placement_id=placement.id, customer_id=cid, winning_entries=4,
                     amount_cents=2000, status=PayoutStatus.PAID, payment_method="cash"))
        s.flush()
        d = cash_service.team_drawer(s, team, 5000)
        assert d.cash_paid_out_cents == 2000
        assert d.expected_cents == 13000  # 100 + 50 - 20

        # Counting exactly that => balances (no phantom short).
        s.add(CashCount(team_id=team_id, kind=CashCountKind.COUNT, counted_cents=13000))
        s.flush()
        d = cash_service.team_drawer(s, team, 5000)
        assert d.variance_cents == 0
