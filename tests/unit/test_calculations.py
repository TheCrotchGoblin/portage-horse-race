"""Critical calculation tests (spec §15.1). These lock the money engine.

If any of these fail, the application must not ship — every one maps directly to
an acceptance criterion in the specification.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.calculations import (
    WagerUnit,
    allocate_placement,
    apply_bps,
    compute_team_financials,
    round_half_up,
    sum_customer_payouts,
)

BASE_TIME = datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc)


def unit(wager_id: int, customer_id: int, quantity: int, seconds: int = 0) -> WagerUnit:
    return WagerUnit(
        wager_id=wager_id,
        customer_id=customer_id,
        quantity=quantity,
        created_at=BASE_TIME + timedelta(seconds=seconds),
    )


# --- round_half_up ---------------------------------------------------------

@pytest.mark.parametrize(
    "num,den,expected",
    [
        (150000 * 1500, 10000, 22500),  # 15% of $1500 = $225.00
        (5, 2, 3),                      # 2.5 -> 3 (half up)
        (7, 2, 4),                      # 3.5 -> 4
        (1, 3, 0),                      # 0.33 -> 0
        (2, 3, 1),                      # 0.66 -> 1
        (0, 10000, 0),
    ],
)
def test_round_half_up(num, den, expected):
    assert round_half_up(num, den) == expected


# --- Worked example (spec §8.4 / §15.1) ------------------------------------

def test_worked_example_team_financials():
    fin = compute_team_financials(
        gross_cents=150000,       # 300 entries x $5.00 = $1,500.00
        active_entries=300,
        club_bps=1500,            # 15%
        first_bps=6000,
        second_bps=3000,
        third_bps=1000,
    )
    assert fin.gross_cents == 150000
    assert fin.club_share_cents == 22500       # $225.00
    assert fin.prize_pool_cents == 127500      # $1,275.00
    assert fin.first_pool_cents == 76500       # $765.00
    assert fin.second_pool_cents == 38250      # $382.50
    assert fin.third_pool_cents == 12750       # $127.50
    assert fin.reconciles()


def test_five_first_place_entries_are_exactly_153_each():
    # First pool $765.00 across 5 winning entries -> $153.00 each.
    wagers = [unit(1, customer_id=10, quantity=5, seconds=0)]
    result = allocate_placement(76500, wagers)
    assert result.total_entries == 5
    assert result.base_cents_per_entry == 15300  # $153.00
    assert result.remainder_cents == 0
    assert result.reconciles()


def test_customer_owning_two_of_five_entries_gets_306():
    # Customer 10 buys 2 entries, customer 20 buys 3 entries -> pool $765.
    wagers = [unit(1, 10, 2, seconds=0), unit(2, 20, 3, seconds=1)]
    result = allocate_placement(76500, wagers)
    payouts = {p.customer_id: p.amount_cents for p in result.customer_payouts}
    assert payouts[10] == 30600  # $306.00
    assert payouts[20] == 45900  # $459.00
    assert result.reconciles()


# --- Team isolation --------------------------------------------------------

def test_teams_are_financially_isolated():
    team_a = compute_team_financials(
        gross_cents=150000, active_entries=300,
        club_bps=1500, first_bps=6000, second_bps=3000, third_bps=1000,
    )
    team_b = compute_team_financials(
        gross_cents=5000, active_entries=10,
        club_bps=1500, first_bps=6000, second_bps=3000, third_bps=1000,
    )
    # Team B's tiny pool never perturbs Team A.
    assert team_a.first_pool_cents == 76500
    assert team_b.gross_cents == 5000
    assert team_b.prize_pool_cents == 4250


# --- Odd-cent remainder allocation -----------------------------------------

def test_odd_cent_pool_reconciles_exactly_and_is_deterministic():
    # Pool of 100 cents across 3 entries: 34, 33, 33 (first entry gets the extra).
    wagers = [unit(1, 10, 1, 0), unit(2, 20, 1, 5), unit(3, 30, 1, 10)]
    result = allocate_placement(100, wagers)
    amounts = [p.amount_cents for p in result.customer_payouts]
    assert amounts == [34, 33, 33]
    assert result.base_cents_per_entry == 33
    assert result.remainder_cents == 1
    assert result.total_allocated_cents == 100
    assert result.reconciles()


def test_remainder_follows_transaction_order_not_id_order():
    # Later timestamp but lower id must still be ordered by time first.
    early = WagerUnit(wager_id=99, customer_id=10, quantity=1, created_at=BASE_TIME)
    late = WagerUnit(wager_id=1, customer_id=20, quantity=1, created_at=BASE_TIME + timedelta(seconds=60))
    result = allocate_placement(3, [late, early])  # 3 cents / 2 entries = 2,1
    payouts = {p.customer_id: p.amount_cents for p in result.customer_payouts}
    assert payouts[10] == 2  # earliest entry gets the extra cent
    assert payouts[20] == 1
    assert result.reconciles()


def test_larger_odd_pool_reconciles():
    # 1000 cents across 7 entries -> base 142, remainder 6.
    wagers = [unit(i, customer_id=i, quantity=1, seconds=i) for i in range(7)]
    result = allocate_placement(1000, wagers)
    assert result.base_cents_per_entry == 142
    assert result.remainder_cents == 6
    assert sum(p.amount_cents for p in result.customer_payouts) == 1000
    # First 6 entries get 143, last gets 142.
    assert [p.amount_cents for p in result.customer_payouts] == [143, 143, 143, 143, 143, 143, 142]


# --- Unclaimed pool (placing player with zero entries, spec FR-108) --------

def test_zero_entries_reports_whole_pool_unclaimed():
    result = allocate_placement(76500, [])
    assert result.total_entries == 0
    assert result.customer_payouts == []
    assert result.unclaimed_cents == 76500
    assert result.reconciles()


# --- Multiple placements combine per customer (spec FR-104) -----------------

def test_customer_across_multiple_placing_players_gets_sum():
    first = allocate_placement(76500, [unit(1, 10, 5, 0)])        # customer 10: $765
    second = allocate_placement(38250, [unit(2, 10, 1, 1),        # customer 10: 1 of 3
                                        unit(3, 20, 2, 2)])
    totals = sum_customer_payouts([first, second])
    # Customer 10 wins all of first pool plus 1/3 of second pool.
    second_share = allocate_placement(38250, [unit(2, 10, 1, 1), unit(3, 20, 2, 2)])
    c10_second = next(p.amount_cents for p in second_share.customer_payouts if p.customer_id == 10)
    assert totals[10] == 76500 + c10_second


def test_apply_bps_matches_percentages():
    assert apply_bps(127500, 6000) == 76500
    assert apply_bps(127500, 3000) == 38250
    assert apply_bps(127500, 1000) == 12750
