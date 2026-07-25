"""Home / dashboard routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from sqlalchemy import select

from app.database import get_session
from app.formatting import dollars_to_cents
from app.models import CashCount, Team, Tournament
from app.models.enums import CashCountKind, TournamentStatus
from app.routes.deps import base_context, get_active_tournament, operator_name
from app.services import audit
from app.services import dashboard as dashboard_service
from app.templating import flash, render

router = APIRouter()


@router.get("/")
def home(request: Request, session: Session = Depends(get_session)):
    """The welcome / home landing — always shown for the brand logo, never a
    redirect. Its primary action adapts to whether a tournament is active."""
    ctx = base_context(request, session, "")
    tournament = ctx["tournament"]
    if tournament is None:
        from datetime import datetime
        # Offer any past (archived) tournaments to reopen or clone.
        ctx["archived"] = session.scalars(
            select(Tournament).where(Tournament.status == TournamentStatus.ARCHIVED)
            .order_by(Tournament.created_at.desc())
        ).all()
        ctx["next_year"] = datetime.now().year + 1
    return render(request, "welcome.html", ctx)


@router.get("/help")
def help_page(request: Request, session: Session = Depends(get_session)):
    return render(request, "help/index.html", base_context(request, session, "help"))


@router.get("/dashboard")
def dashboard(request: Request, session: Session = Depends(get_session)):
    tournament = get_active_tournament(session)
    if tournament is None:
        return RedirectResponse("/", status_code=303)
    ctx = base_context(request, session, "dashboard")
    ctx["board"] = dashboard_service.build_dashboard(session, tournament)
    ctx["backup_health"] = _backup_health(request)
    # Show the crash-recovery all-clear once, then clear it.
    ctx["recovered_unclean"] = getattr(request.app.state, "recovered_unclean", False)
    request.app.state.recovered_unclean = False
    return render(request, "dashboard.html", ctx)


def _backup_health(request: Request) -> dict:
    from datetime import datetime

    from app.services import backups

    settings = request.app.state.settings
    health = backups.backup_health(settings.backup_dir)
    if health["last_at"] is None:
        health["stale"] = True
        health["age"] = None
    else:
        seconds = (datetime.now() - health["last_at"]).total_seconds()
        health["stale"] = seconds > 7200  # older than 2 hours
        health["age"] = health["last_at"]
    return health


@router.post("/teams/{team_id}/cash-count")
def record_cash_count(
    request: Request,
    team_id: int,
    session: Session = Depends(get_session),
    counted: str = Form(...),
    kind: str = Form(CashCountKind.COUNT),
    note: str = Form(""),
):
    tournament = get_active_tournament(session)
    team = session.get(Team, team_id)
    if team is None or team.tournament_id != tournament.id:
        flash(request, "Team not found.", "danger")
        return RedirectResponse("/dashboard", status_code=303)
    if kind not in (CashCountKind.COUNT, CashCountKind.FLOAT):
        kind = CashCountKind.COUNT
    try:
        counted_cents = dollars_to_cents(counted)
    except ValueError:
        flash(request, "That is not a valid dollar amount.", "danger")
        return RedirectResponse("/dashboard", status_code=303)

    operator = operator_name(request)
    note = (note or "").strip() or None
    session.add(CashCount(team_id=team_id, kind=kind, counted_cents=counted_cents,
                          counted_by=operator, note=note))
    session.flush()
    if kind == CashCountKind.FLOAT:
        audit.record(session, action_type="cash_float_set", actor=operator,
                     tournament_id=tournament.id, entity_type="team", entity_id=team.id,
                     after={"float_cents": counted_cents})
        flash(request, f"Opening float set for {team.name}.")
    else:
        audit.record(session, action_type="cash_count", actor=operator,
                     tournament_id=tournament.id, entity_type="team", entity_id=team.id,
                     after={"counted_cents": counted_cents}, reason=note)
        flash(request, f"Cash count recorded for {team.name}.")
    return RedirectResponse("/dashboard", status_code=303)
