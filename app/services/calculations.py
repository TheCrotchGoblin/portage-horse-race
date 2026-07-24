"""Pure money math — the correctness core of the application (spec §8).

Everything here operates on **integer cents** and **basis points** (1500 = 15%).
No floating-point currency, no database, no I/O — so it is fully unit-testable.

Reconciliation guarantees:
  * gross = club_share + prize_pool                     (exactly)
  * prize_pool = first_pool + second_pool + third_pool  (exactly; third is the remainder)
  * sum(customer payouts) = placement_pool              (exactly; deterministic remainder cents)
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Sequence


def round_half_up(numerator: int, denominator: int) -> int:
    """Integer division rounding halves away from zero (banker-free, cash-friendly)."""
    if denominator == 0:
        raise ZeroDivisionError("denominator must be non-zero")
    if denominator < 0:
        numerator, denominator = -numerator, -denominator
    if numerator >= 0:
        return (numerator + denominator // 2) // denominator
    return -((-numerator + denominator // 2) // denominator)


def apply_bps(amount_cents: int, bps: int) -> int:
    """Apply a basis-point rate to a cents amount, rounding half up."""
    return round_half_up(amount_cents * bps, 10000)


@dataclass(frozen=True)
class TeamFinancials:
    active_entries: int
    gross_cents: int
    club_share_cents: int
    prize_pool_cents: int
    first_pool_cents: int
    second_pool_cents: int
    third_pool_cents: int

    def reconciles(self) -> bool:
        return (
            self.gross_cents == self.club_share_cents + self.prize_pool_cents
            and self.prize_pool_cents
            == self.first_pool_cents + self.second_pool_cents + self.third_pool_cents
        )


def compute_team_financials(
    *,
    gross_cents: int,
    active_entries: int,
    club_bps: int,
    first_bps: int,
    second_bps: int,
    third_bps: int,
) -> TeamFinancials:
    """Compute a team's independent pool breakdown (spec §8.1 & §8.2).

    ``third_bps`` is accepted for validation symmetry but the third pool is
    always computed as the remainder so the three pools reconcile exactly.
    """
    if first_bps + second_bps + third_bps != 10000:
        raise ValueError("placement split must total 10000 basis points (100%)")

    club_share = apply_bps(gross_cents, club_bps)
    prize_pool = gross_cents - club_share
    first_pool = apply_bps(prize_pool, first_bps)
    second_pool = apply_bps(prize_pool, second_bps)
    third_pool = prize_pool - first_pool - second_pool  # remainder → exact reconciliation

    return TeamFinancials(
        active_entries=active_entries,
        gross_cents=gross_cents,
        club_share_cents=club_share,
        prize_pool_cents=prize_pool,
        first_pool_cents=first_pool,
        second_pool_cents=second_pool,
        third_pool_cents=third_pool,
    )


@dataclass(frozen=True)
class WagerUnit:
    """One winning wager row, decoupled from the ORM for pure testing.

    ``quantity`` is the number of $5 entries this row represents. Ordering for
    deterministic remainder-cent allocation is by (created_at, wager_id).
    """

    wager_id: int
    customer_id: int
    quantity: int
    created_at: datetime


@dataclass(frozen=True)
class CustomerPayout:
    customer_id: int
    winning_entries: int
    amount_cents: int


@dataclass(frozen=True)
class AllocationResult:
    placement_pool_cents: int
    total_entries: int
    base_cents_per_entry: int
    remainder_cents: int
    customer_payouts: list[CustomerPayout] = field(default_factory=list)
    unclaimed_cents: int = 0

    @property
    def total_allocated_cents(self) -> int:
        return sum(c.amount_cents for c in self.customer_payouts)

    def reconciles(self) -> bool:
        return self.total_allocated_cents + self.unclaimed_cents == self.placement_pool_cents


def allocate_placement(
    placement_pool_cents: int,
    winning_wagers: Iterable[WagerUnit],
) -> AllocationResult:
    """Distribute a placement pool across winning entries to the exact cent (spec §8.3).

    Rule: base = pool // entries, remainder = pool % entries. Expand each wager
    into its individual $5 units, order units by (created_at, wager_id), and give
    one extra cent to the first ``remainder`` units. Aggregate per customer.

    If there are zero winning entries the whole pool is reported as *unclaimed*
    for administrator disposition (spec FR-108) rather than silently lost.
    """
    ordered = sorted(winning_wagers, key=lambda w: (w.created_at, w.wager_id))

    # Expand to one entry (unit) per $5, preserving order.
    unit_customers: list[int] = []
    for w in ordered:
        if w.quantity <= 0:
            continue
        unit_customers.extend([w.customer_id] * w.quantity)

    total_entries = len(unit_customers)
    if total_entries == 0:
        return AllocationResult(
            placement_pool_cents=placement_pool_cents,
            total_entries=0,
            base_cents_per_entry=0,
            remainder_cents=0,
            customer_payouts=[],
            unclaimed_cents=placement_pool_cents,
        )

    base = placement_pool_cents // total_entries
    remainder = placement_pool_cents % total_entries

    amounts: dict[int, int] = defaultdict(int)
    entries: dict[int, int] = defaultdict(int)
    for i, customer_id in enumerate(unit_customers):
        cents = base + (1 if i < remainder else 0)
        amounts[customer_id] += cents
        entries[customer_id] += 1

    # Preserve first-seen customer order for stable, explainable reports.
    seen: list[int] = []
    for customer_id in unit_customers:
        if customer_id not in seen:
            seen.append(customer_id)

    payouts = [
        CustomerPayout(
            customer_id=cid,
            winning_entries=entries[cid],
            amount_cents=amounts[cid],
        )
        for cid in seen
    ]

    result = AllocationResult(
        placement_pool_cents=placement_pool_cents,
        total_entries=total_entries,
        base_cents_per_entry=base,
        remainder_cents=remainder,
        customer_payouts=payouts,
        unclaimed_cents=0,
    )
    assert result.reconciles(), "placement allocation failed to reconcile"
    return result


def sum_customer_payouts(results: Sequence[AllocationResult]) -> dict[int, int]:
    """Combine multiple placement allocations into per-customer totals (spec FR-104)."""
    totals: dict[int, int] = defaultdict(int)
    for r in results:
        for cp in r.customer_payouts:
            totals[cp.customer_id] += cp.amount_cents
    return dict(totals)
