"""CSV exports (spec FR-121). Values match the DB view and include stable IDs.

Money is exported as plain dollar strings (e.g. 12.50) AND cents, so a spreadsheet
reconciliation is unambiguous.
"""
from __future__ import annotations

import csv
import io

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, Customer, Payout, Placement, Player, Team, Tournament, Wager
from app.services import teams as team_service


def _csv(header: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue()


def _dollars(cents: int) -> str:
    return f"{cents / 100:.2f}"


def customers_csv(session: Session) -> str:
    rows = []
    for c in session.scalars(select(Customer).order_by(Customer.id)):
        rows.append([c.id, c.name, c.phone_raw or "", c.email or "", c.notes or "", c.created_at])
    return _csv(["customer_id", "name", "phone", "email", "notes", "created_at"], rows)


def wagers_csv(session: Session, tournament_id: int) -> str:
    rows = []
    stmt = select(Wager).where(Wager.tournament_id == tournament_id).order_by(Wager.id)
    for w in session.scalars(stmt):
        rows.append([
            w.id, w.created_at, w.operator_id or "", w.customer_id, w.customer.name,
            w.team.name, w.player.name, w.quantity, _dollars(w.unit_price_cents),
            _dollars(w.amount_cents), w.amount_cents, w.status, w.void_reason or "",
        ])
    return _csv(
        ["wager_id", "created_at", "operator", "customer_id", "customer", "team", "player",
         "quantity", "unit_price", "amount", "amount_cents", "status", "void_reason"],
        rows,
    )


def player_totals_csv(session: Session, tournament_id: int) -> str:
    rows = []
    teams = session.scalars(select(Team).where(Team.tournament_id == tournament_id).order_by(Team.id))
    for team in teams:
        for pt in team_service.player_totals(session, team.id):
            rows.append([team.name, pt.player.id, pt.player.name, pt.entries,
                         _dollars(pt.gross_cents), pt.gross_cents])
    return _csv(["team", "player_id", "player", "active_entries", "gross", "gross_cents"], rows)


def payouts_csv(session: Session, tournament_id: int) -> str:
    rows = []
    stmt = (
        select(Payout).join(Placement, Payout.placement_id == Placement.id)
        .join(Team, Placement.team_id == Team.id)
        .where(Team.tournament_id == tournament_id).order_by(Payout.id)
    )
    for p in session.scalars(stmt):
        rows.append([
            p.id, p.customer_id, p.customer.name, p.customer.phone_raw or "", p.customer.email or "",
            p.placement.player.team.name, p.placement.position, p.placement.player.name,
            p.winning_entries, _dollars(p.amount_cents), p.amount_cents, p.status,
            p.paid_at or "", p.paid_by or "", p.payment_method or "",
        ])
    return _csv(
        ["payout_id", "customer_id", "customer", "phone", "email", "team", "position", "player",
         "winning_entries", "amount", "amount_cents", "status", "paid_at", "paid_by", "method"],
        rows,
    )


def audit_csv(session: Session, tournament_id: int) -> str:
    rows = []
    stmt = select(AuditLog).order_by(AuditLog.id)
    for e in session.scalars(stmt):
        rows.append([e.id, e.created_at, e.actor or "", e.action_type, e.entity_type or "",
                     e.entity_id or "", e.reason or "", e.before_json or "", e.after_json or ""])
    return _csv(
        ["audit_id", "created_at", "actor", "action", "entity_type", "entity_id", "reason", "before", "after"],
        rows,
    )


EXPORTERS = {
    "customers": customers_csv,
    "wagers": wagers_csv,
    "player-totals": player_totals_csv,
    "payouts": payouts_csv,
    "audit": audit_csv,
}


def build_csv(kind: str, session: Session, tournament_id: int) -> str:
    fn = EXPORTERS.get(kind)
    if fn is None:
        raise KeyError(kind)
    if kind == "customers":
        return fn(session)
    return fn(session, tournament_id)
