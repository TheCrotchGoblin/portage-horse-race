"""Shared route helpers: active tournament, operator identity, admin PIN, context."""
from __future__ import annotations

import socket

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Setting, Tournament
from app.models.enums import TournamentStatus


def get_active_tournament(session: Session) -> Tournament | None:
    """The current working tournament: newest that isn't archived."""
    stmt = (
        select(Tournament)
        .where(Tournament.status != TournamentStatus.ARCHIVED)
        .order_by(Tournament.created_at.desc(), Tournament.id.desc())
        .limit(1)
    )
    return session.scalars(stmt).first()


def operator_name(request: Request) -> str:
    """Name/station recorded on every financial action (spec §4)."""
    name = request.session.get("operator")
    if name:
        return name
    return socket.gethostname()


def get_setting(session: Session, key: str, default: str | None = None) -> str | None:
    row = session.get(Setting, key)
    return row.value if row and row.value is not None else default


def set_setting(session: Session, key: str, value: str) -> None:
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value


def admin_pin_ok(session: Session, pin: str | None) -> bool:
    """Validate an admin PIN. If none is configured, admin actions are open
    (single-volunteer setups) — but the operator is still recorded."""
    configured = get_setting(session, "admin_pin")
    if not configured:
        return True
    return bool(pin) and pin == configured


def base_context(request: Request, session: Session, active_nav: str = "") -> dict:
    """Common template context: current tournament + active nav highlight."""
    return {
        "tournament": get_active_tournament(session),
        "active_nav": active_nav,
        "operator": operator_name(request),
    }
