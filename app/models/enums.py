"""String status constants used across the data model and services.

Plain string values (not Python enums) keep them trivially serialisable to
SQLite text columns, CSV exports and audit JSON.
"""
from __future__ import annotations


class TournamentStatus:
    DRAFT = "DRAFT"
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    RESULTS_ENTERED = "RESULTS_ENTERED"
    PAYOUTS_GENERATED = "PAYOUTS_GENERATED"
    SETTLED = "SETTLED"
    ARCHIVED = "ARCHIVED"

    ALL = (DRAFT, OPEN, CLOSED, RESULTS_ENTERED, PAYOUTS_GENERATED, SETTLED, ARCHIVED)
    # Human-friendly labels for the UI (no jargon).
    LABELS = {
        DRAFT: "Draft",
        OPEN: "Open",
        CLOSED: "Closed",
        RESULTS_ENTERED: "Results entered",
        PAYOUTS_GENERATED: "Payouts generated",
        SETTLED: "Settled",
        ARCHIVED: "Archived",
    }


class WageringStatus:
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    LABELS = {OPEN: "Open", CLOSED: "Closed"}


class WagerStatus:
    ACTIVE = "active"
    VOID = "void"


class PayoutStatus:
    UNPAID = "UNPAID"
    PAID = "PAID"
    REVERSED = "REVERSED"
    HELD = "HELD"
    # A winner who can't be reached / declines the money: the amount is waived
    # (kept by the club or donated). A formally resolved outcome, so — like HELD —
    # it is NOT outstanding and does not block settlement.
    WAIVED = "WAIVED"
    LABELS = {UNPAID: "Unpaid", PAID: "Paid", REVERSED: "Reversed", HELD: "Held", WAIVED: "Waived"}
    # Money still owed / to resolve — blocks settlement. (HELD and WAIVED are
    # formally parked/resolved outcomes per Appendix A, so they are NOT outstanding.)
    OUTSTANDING = (UNPAID, REVERSED)


class CashCountKind:
    """Distinguishes an opening change float from an actual cash count so the
    drawer can be reconciled as float + cash-in − cash-out (not just sales)."""
    FLOAT = "float"
    COUNT = "count"


class Position:
    FIRST = 1
    SECOND = 2
    THIRD = 3
    ALL = (FIRST, SECOND, THIRD)
    LABELS = {FIRST: "1st place", SECOND: "2nd place", THIRD: "3rd place"}
