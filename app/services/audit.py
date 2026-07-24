"""Append-only audit log helper (spec FR-064, NFR-09).

Every material action (setup change, open/close, void, result change, payout
change, restore) records an immutable entry with actor, before/after snapshots
and reason. Entries are only ever inserted, never updated or deleted.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog


def _dump(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, default=str, sort_keys=True)


def record(
    session: Session,
    *,
    action_type: str,
    actor: str | None = None,
    tournament_id: int | None = None,
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    before: Any = None,
    after: Any = None,
    reason: str | None = None,
) -> AuditLog:
    """Insert an audit entry. Caller controls the surrounding transaction."""
    entry = AuditLog(
        action_type=action_type,
        actor=actor,
        tournament_id=tournament_id,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        before_json=_dump(before),
        after_json=_dump(after),
        reason=reason,
    )
    session.add(entry)
    return entry
