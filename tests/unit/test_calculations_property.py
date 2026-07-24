"""Property-style tests (TST-01): random valid inputs must always reconcile exactly.

Uses a seeded RNG so runs are reproducible while still covering a wide input space.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from app.services.calculations import WagerUnit, allocate_placement, compute_team_financials

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_team_financials_always_reconcile():
    rng = random.Random(20260724)
    for _ in range(5000):
        gross = rng.randint(0, 5_000_000)          # up to $50,000
        club = rng.randint(0, 10000)               # 0–100%
        a = rng.randint(0, 10000)
        b = rng.randint(0, 10000 - a)
        c = 10000 - a - b                          # split always totals 100%
        fin = compute_team_financials(
            gross_cents=gross, active_entries=0,
            club_bps=club, first_bps=a, second_bps=b, third_bps=c,
        )
        # Exact reconciliation, every time.
        assert fin.gross_cents == fin.club_share_cents + fin.prize_pool_cents
        assert fin.prize_pool_cents == fin.first_pool_cents + fin.second_pool_cents + fin.third_pool_cents
        assert fin.club_share_cents >= 0
        assert fin.prize_pool_cents >= 0
        assert fin.reconciles()


def test_placement_allocation_always_reconciles():
    rng = random.Random(4242)
    for _ in range(5000):
        pool = rng.randint(0, 2_000_000)
        wagers = []
        for i in range(rng.randint(1, 15)):
            wagers.append(WagerUnit(
                wager_id=i, customer_id=rng.randint(1, 6),
                quantity=rng.randint(1, 25), created_at=BASE + timedelta(seconds=i),
            ))
        total_entries = sum(w.quantity for w in wagers)
        result = allocate_placement(pool, wagers)

        assert result.total_entries == total_entries
        # The whole pool is distributed to the exact cent — no money created or lost.
        assert result.total_allocated_cents == pool
        assert result.reconciles()
        # base*entries + remainder == pool, and each customer amount is non-negative.
        assert result.base_cents_per_entry * total_entries + result.remainder_cents == pool
        assert all(cp.amount_cents >= 0 for cp in result.customer_payouts)
        # Every customer's amount is between base*entries and (base+1)*entries.
        for cp in result.customer_payouts:
            lo = result.base_cents_per_entry * cp.winning_entries
            hi = lo + cp.winning_entries
            assert lo <= cp.amount_cents <= hi


def test_no_winners_is_fully_unclaimed():
    rng = random.Random(7)
    for _ in range(500):
        pool = rng.randint(0, 1_000_000)
        result = allocate_placement(pool, [])
        assert result.total_allocated_cents == 0
        assert result.unclaimed_cents == pool
        assert result.reconciles()
