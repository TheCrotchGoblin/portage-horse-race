"""Ledger and audit-log routes (spec §6.4)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_session
from app.models.enums import WagerStatus
from app.routes.deps import base_context
from app.services import ledger as ledger_service
from app.services.ledger import LedgerFilters
from app.templating import render

router = APIRouter()


@router.get("/ledger")
def ledger(
    request: Request,
    session: Session = Depends(get_session),
    team_id: int | None = None,
    player_id: int | None = None,
    customer_id: int | None = None,
    customer_name: str | None = None,
    operator: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    ctx = base_context(request, session, "ledger")
    tournament = ctx["tournament"]
    if tournament is None:
        return RedirectResponse("/setup/new", status_code=303)

    filters = LedgerFilters(
        team_id=team_id, player_id=player_id, customer_id=customer_id,
        customer_name=customer_name or None,
        operator=operator or None, status=status or None,
        date_from=date_from or None, date_to=date_to or None,
    )
    ctx.update({
        "wagers": ledger_service.query_wagers(session, tournament.id, filters),
        "teams": ledger_service.teams_for(session, tournament.id),
        "players": ledger_service.players_for(session, tournament.id),
        "filters": filters,
        "statuses": [WagerStatus.ACTIVE, WagerStatus.VOID],
    })
    return render(request, "ledger/index.html", ctx)


@router.get("/ledger/audit")
def audit(request: Request, session: Session = Depends(get_session)):
    ctx = base_context(request, session, "ledger")
    tournament = ctx["tournament"]
    ctx["entries"] = ledger_service.audit_entries(session, tournament.id if tournament else None)
    return render(request, "ledger/audit.html", ctx)
