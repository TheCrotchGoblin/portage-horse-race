"""Filtered wager ledger and audit-log queries (spec §6.4, §14)."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, Customer, Player, Team, Wager


@dataclass
class LedgerFilters:
    team_id: int | None = None
    player_id: int | None = None
    customer_id: int | None = None
    customer_name: str | None = None
    operator: str | None = None
    status: str | None = None
    date_from: str | None = None  # ISO date (inclusive)
    date_to: str | None = None    # ISO date (inclusive)


def query_wagers(
    session: Session,
    tournament_id: int,
    filters: LedgerFilters,
    *,
    limit: int = 200,
    offset: int = 0,
) -> list[Wager]:
    stmt = select(Wager).where(Wager.tournament_id == tournament_id)
    if filters.team_id:
        stmt = stmt.where(Wager.team_id == filters.team_id)
    if filters.player_id:
        stmt = stmt.where(Wager.player_id == filters.player_id)
    if filters.customer_id:
        stmt = stmt.where(Wager.customer_id == filters.customer_id)
    if filters.customer_name:
        stmt = stmt.where(
            Wager.customer_id.in_(
                select(Customer.id).where(Customer.name.ilike(f"%{filters.customer_name}%"))
            )
        )
    if filters.operator:
        stmt = stmt.where(Wager.operator_id == filters.operator)
    if filters.status:
        stmt = stmt.where(Wager.status == filters.status)
    if filters.date_from:
        stmt = stmt.where(Wager.created_at >= f"{filters.date_from} 00:00:00")
    if filters.date_to:
        stmt = stmt.where(Wager.created_at <= f"{filters.date_to} 23:59:59")
    stmt = stmt.order_by(Wager.created_at.desc(), Wager.id.desc()).limit(limit).offset(offset)
    return list(session.scalars(stmt).all())


def teams_for(session: Session, tournament_id: int) -> list[Team]:
    return list(session.scalars(select(Team).where(Team.tournament_id == tournament_id).order_by(Team.id)).all())


def players_for(session: Session, tournament_id: int) -> list[Player]:
    return list(
        session.scalars(
            select(Player).join(Team, Player.team_id == Team.id)
            .where(Team.tournament_id == tournament_id)
            .order_by(Player.name)
        ).all()
    )


def _audit_scope(stmt, tournament_id: int | None):
    if tournament_id is not None:
        stmt = stmt.where((AuditLog.tournament_id == tournament_id) | (AuditLog.tournament_id.is_(None)))
    return stmt


def audit_entries(session: Session, tournament_id: int | None, action_type: str | None = None,
                  limit: int = 300) -> list[AuditLog]:
    # Filter is applied IN THE QUERY (before the limit), so selecting an action
    # can't miss older matching rows.
    stmt = _audit_scope(select(AuditLog), tournament_id)
    if action_type:
        stmt = stmt.where(AuditLog.action_type == action_type)
    stmt = stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit)
    return list(session.scalars(stmt).all())


def audit_action_types(session: Session, tournament_id: int | None) -> list[str]:
    """Distinct action types across the WHOLE log (not just the current page)."""
    from sqlalchemy import distinct

    stmt = _audit_scope(select(distinct(AuditLog.action_type)), tournament_id)
    return sorted(a for a in session.scalars(stmt).all() if a)
