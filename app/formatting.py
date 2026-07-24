"""Display helpers used by templates (money, phones, dates).

Kept jargon-free for a non-technical audience: money always renders as
``$1,275.00``; percentages render from basis points as ``15%``.
"""
from __future__ import annotations

import re
from datetime import datetime


def cents_to_dollars(cents: int | None) -> str:
    if cents is None:
        cents = 0
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    return f"{sign}${cents // 100:,}.{cents % 100:02d}"


def dollars_to_cents(value: str | float | int | None) -> int:
    """Parse a user-entered dollar amount into integer cents.

    Accepts ``"12"``, ``"12.5"``, ``"$1,275.00"``. Raises ValueError on garbage.
    """
    if value is None or value == "":
        raise ValueError("amount is required")
    if isinstance(value, (int, float)):
        return int(round(float(value) * 100))
    cleaned = value.strip().replace("$", "").replace(",", "")
    if not re.fullmatch(r"-?\d+(\.\d{1,2})?", cleaned):
        raise ValueError(f"'{value}' is not a valid dollar amount")
    neg = cleaned.startswith("-")
    cleaned = cleaned.lstrip("-")
    if "." in cleaned:
        whole, frac = cleaned.split(".")
        frac = (frac + "00")[:2]
    else:
        whole, frac = cleaned, "00"
    cents = int(whole) * 100 + int(frac)
    return -cents if neg else cents


def int_or_none(value: str | int | None) -> int | None:
    """Parse an optional numeric query param; empty/blank strings become None.

    Query params bound to HTML forms arrive as strings, and 'All' options submit
    an empty string — which FastAPI cannot coerce to int. Accept them as strings
    and convert here instead.
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if value.strip() == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def bps_to_percent(bps: int | None) -> str:
    if bps is None:
        bps = 0
    if bps % 100 == 0:
        return f"{bps // 100}%"
    return f"{bps / 100:.2f}".rstrip("0").rstrip(".") + "%"


def format_datetime(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.strftime("%Y-%m-%d %H:%M")
