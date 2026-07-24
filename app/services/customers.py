"""Customer create / search / duplicate detection (spec §6.2)."""
from __future__ import annotations

import re

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Customer, Payout, Placement, Player, Team, Wager
from app.services import audit


def normalize_phone(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    return digits or None


def normalize_email(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = raw.strip().lower()
    return cleaned or None


class CustomerError(ValueError):
    pass


def create_customer(
    session: Session,
    *,
    name: str,
    phone: str | None = None,
    email: str | None = None,
    notes: str | None = None,
    operator: str | None = None,
) -> Customer:
    name = (name or "").strip()
    if not name:
        raise CustomerError("A customer name is required.")
    customer = Customer(
        name=name,
        phone_raw=(phone or "").strip() or None,
        phone_normalized=normalize_phone(phone),
        email=(email or "").strip() or None,
        email_normalized=normalize_email(email),
        notes=(notes or "").strip() or None,
    )
    session.add(customer)
    session.flush()
    audit.record(
        session,
        action_type="customer_created",
        actor=operator,
        entity_type="customer",
        entity_id=customer.id,
        after={"name": name},
    )
    return customer


def update_customer(
    session: Session,
    customer: Customer,
    *,
    name: str,
    phone: str | None,
    email: str | None,
    notes: str | None,
    operator: str | None = None,
) -> Customer:
    name = (name or "").strip()
    if not name:
        raise CustomerError("A customer name is required.")
    customer.name = name
    customer.phone_raw = (phone or "").strip() or None
    customer.phone_normalized = normalize_phone(phone)
    customer.email = (email or "").strip() or None
    customer.email_normalized = normalize_email(email)
    customer.notes = (notes or "").strip() or None
    audit.record(
        session,
        action_type="customer_updated",
        actor=operator,
        entity_type="customer",
        entity_id=customer.id,
        after={"name": name},
    )
    return customer


def find_duplicates(
    session: Session, *, phone: str | None, email: str | None, exclude_id: int | None = None
) -> list[Customer]:
    """Customers sharing the same normalized phone or email (spec FR-022)."""
    phone_norm = normalize_phone(phone)
    email_norm = normalize_email(email)
    if not phone_norm and not email_norm:
        return []
    conditions = []
    if phone_norm:
        conditions.append(Customer.phone_normalized == phone_norm)
    if email_norm:
        conditions.append(Customer.email_normalized == email_norm)
    stmt = select(Customer).where(or_(*conditions))
    if exclude_id is not None:
        stmt = stmt.where(Customer.id != exclude_id)
    return list(session.scalars(stmt).all())


def search(session: Session, query: str, limit: int = 15) -> list[Customer]:
    """Match by partial name, normalized phone digits, or email substring."""
    query = (query or "").strip()
    if not query:
        return list(
            session.scalars(select(Customer).order_by(Customer.updated_at.desc()).limit(limit)).all()
        )
    like = f"%{query.lower()}%"
    phone_digits = normalize_phone(query)
    conditions = [
        Customer.name.ilike(like),
        Customer.email_normalized.ilike(like),
    ]
    if phone_digits:
        conditions.append(Customer.phone_normalized.like(f"%{phone_digits}%"))
    stmt = select(Customer).where(or_(*conditions)).order_by(Customer.name).limit(limit)
    return list(session.scalars(stmt).all())


def history(session: Session, customer_id: int) -> dict:
    """All wagers and payouts for a customer (spec FR-024)."""
    wagers = session.scalars(
        select(Wager).where(Wager.customer_id == customer_id).order_by(Wager.created_at.desc())
    ).all()
    payouts = session.scalars(
        select(Payout).where(Payout.customer_id == customer_id)
    ).all()
    return {"wagers": list(wagers), "payouts": list(payouts)}
