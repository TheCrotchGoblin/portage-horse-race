"""Jinja2 template environment with money/percent/status filters and flash helper."""
from __future__ import annotations

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.formatting import bps_to_percent, cents_to_dollars, format_datetime
from app.models.enums import PayoutStatus, Position, TournamentStatus, WageringStatus
from app.paths import TEMPLATES_DIR

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["dollars"] = cents_to_dollars
templates.env.filters["percent"] = bps_to_percent
templates.env.filters["datetime"] = format_datetime

# Globals available to every template (jargon-free status labels, etc.).
templates.env.globals["status_label"] = lambda s: TournamentStatus.LABELS.get(s, s)
templates.env.globals["wagering_label"] = lambda s: WageringStatus.LABELS.get(s, s)
templates.env.globals["payout_label"] = lambda s: PayoutStatus.LABELS.get(s, s)
templates.env.globals["position_label"] = lambda p: Position.LABELS.get(p, str(p))


def render(request: Request, name: str, context: dict | None = None, status_code: int = 200):
    """Render a template, injecting flash messages from the session (PRG pattern)."""
    ctx = dict(context or {})
    ctx["request"] = request
    session = request.session if hasattr(request, "session") else {}
    if "flash" in session:
        ctx.setdefault("flash", session.pop("flash"))
        ctx.setdefault("flash_kind", session.pop("flash_kind", "info"))
    return templates.TemplateResponse(request=request, name=name, context=ctx, status_code=status_code)


def flash(request: Request, message: str, kind: str = "success") -> None:
    """Queue a one-shot message shown after the next redirect (spec: POST/Redirect/GET)."""
    request.session["flash"] = message
    request.session["flash_kind"] = kind
