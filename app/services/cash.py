"""Cash-drawer reconciliation (FIN-05).

The physical cash box holds: the opening change float, plus the cash taken in
for wagers, minus any prize money already paid out of that same box in cash.
Reconciliation compares that *expected* figure to a physical *count* — so a
volunteer can count the box at any time (even after paying winners) and see a
real over/short instead of a phantom one that ignores the float and payouts.

    expected = opening_float + cash_wagers_in - cash_payouts_out
    variance = counted - expected      (+ over, - short)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CashCount, Payout, Placement, Team
from app.models.enums import CashCountKind, PayoutStatus
from app.services import teams as team_service


@dataclass(frozen=True)
class DrawerRecon:
    team_id: int
    opening_float_cents: int
    cash_wagers_cents: int      # gross taken in (cash stays in the box; change is returned)
    cash_paid_out_cents: int    # prize money paid from the box in cash
    expected_cents: int         # float + wagers - paid out
    counted_cents: int | None   # latest physical count, or None
    counted_at: datetime | None
    variance_cents: int | None  # counted - expected; None if never counted

    @property
    def counted(self) -> bool:
        return self.counted_cents is not None


def opening_float(session: Session, team_id: int) -> int:
    row = session.execute(
        select(CashCount.counted_cents)
        .where(CashCount.team_id == team_id, CashCount.kind == CashCountKind.FLOAT)
        .order_by(CashCount.counted_at.desc(), CashCount.id.desc())
        .limit(1)
    ).first()
    return int(row[0]) if row else 0


def latest_count(session: Session, team_id: int) -> CashCount | None:
    return session.scalars(
        select(CashCount)
        .where(CashCount.team_id == team_id, CashCount.kind == CashCountKind.COUNT)
        .order_by(CashCount.counted_at.desc(), CashCount.id.desc())
        .limit(1)
    ).first()


def cash_paid_out(session: Session, team_id: int) -> int:
    """Prize money paid from this team's box in cash (cheque/other don't touch it)."""
    return int(session.scalar(
        select(func.coalesce(func.sum(Payout.amount_cents), 0))
        .join(Placement, Payout.placement_id == Placement.id)
        .where(Placement.team_id == team_id,
               Payout.status == PayoutStatus.PAID,
               Payout.payment_method == "cash")
    ) or 0)


def unexplained_variance_teams(session: Session, tournament_id: int) -> list[Team]:
    """Teams whose latest cash count is over/short with no reason noted.

    An over/short box must be explained before the event can be settled — a
    silent discrepancy is exactly what a treasurer needs flagged (FIN-05)."""
    teams = session.scalars(select(Team).where(Team.tournament_id == tournament_id)).all()
    flagged: list[Team] = []
    for team in teams:
        count = latest_count(session, team.id)
        if count is None:
            continue
        gross = team_service.team_totals(session, team.id)[1]
        expected = opening_float(session, team.id) + gross - cash_paid_out(session, team.id)
        if count.counted_cents != expected and not (count.note or "").strip():
            flagged.append(team)
    return flagged


def team_drawer(session: Session, team: Team, gross_cents: int) -> DrawerRecon:
    """gross_cents is the team's active-wager gross (cash taken in)."""
    fl = opening_float(session, team.id)
    paid = cash_paid_out(session, team.id)
    expected = fl + gross_cents - paid
    count = latest_count(session, team.id)
    counted = None if count is None else count.counted_cents
    variance = None if counted is None else counted - expected
    return DrawerRecon(
        team_id=team.id,
        opening_float_cents=fl,
        cash_wagers_cents=gross_cents,
        cash_paid_out_cents=paid,
        expected_cents=expected,
        counted_cents=counted,
        counted_at=None if count is None else count.counted_at,
        variance_cents=variance,
    )
