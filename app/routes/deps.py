"""Shared route helpers: active tournament, operator identity, admin PIN, context."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
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


def _hash_pin(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 120_000).hex()


def _normalize_code(code: str) -> str:
    return "".join(ch for ch in (code or "") if ch.isdigit())


def set_admin_pin(session: Session, pin: str) -> str:
    """Store a salted hash of the admin PIN (never the PIN itself, NFR-08) and a
    fresh recovery code. Returns the plaintext recovery code to show once."""
    salt = get_setting(session, "pin_salt")
    if not salt:
        salt = os.urandom(16).hex()
        set_setting(session, "pin_salt", salt)
    set_setting(session, "admin_pin_hash", _hash_pin(pin, salt))

    digits = f"{secrets.randbelow(10**8):08d}"
    recovery = f"{digits[:4]}-{digits[4:]}"
    set_setting(session, "recovery_code_hash", _hash_pin(digits, salt))

    legacy = session.get(Setting, "admin_pin")  # drop any legacy plaintext value
    if legacy is not None:
        session.delete(legacy)
    return recovery


def has_admin_pin(session: Session) -> bool:
    return bool(get_setting(session, "admin_pin_hash") or get_setting(session, "admin_pin"))


def recovery_code_ok(session: Session, code: str) -> bool:
    salt = get_setting(session, "pin_salt")
    stored = get_setting(session, "recovery_code_hash")
    if not salt or not stored:
        return False
    return hmac.compare_digest(_hash_pin(_normalize_code(code), salt), stored)


def clear_admin_pin(session: Session) -> None:
    """Remove the admin PIN and its recovery code (used by recovery flows)."""
    for key in ("admin_pin_hash", "admin_pin", "recovery_code_hash", "pin_salt"):
        row = session.get(Setting, key)
        if row is not None:
            session.delete(row)


def admin_pin_ok(session: Session, pin: str | None) -> bool:
    """Validate an admin PIN. If none is configured, admin actions are open
    (single-volunteer setups) — but the operator is still recorded."""
    pin_hash = get_setting(session, "pin_salt"), get_setting(session, "admin_pin_hash")
    salt, stored_hash = pin_hash
    if salt and stored_hash:
        if not pin:
            return False
        return hmac.compare_digest(_hash_pin(pin, salt), stored_hash)
    legacy = get_setting(session, "admin_pin")  # back-compat, pre-hash installs
    if legacy:
        return bool(pin) and hmac.compare_digest(pin, legacy)
    return True


def base_context(request: Request, session: Session, active_nav: str = "") -> dict:
    """Common template context: current tournament + active nav highlight."""
    return {
        "tournament": get_active_tournament(session),
        "active_nav": active_nav,
        "operator": operator_name(request),
    }
