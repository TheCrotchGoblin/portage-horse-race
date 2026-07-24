"""Home / dashboard routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from sqlalchemy import select

from app.database import get_session
from app.formatting import dollars_to_cents
from app.models import CashCount, Team, Tournament
from app.models.enums import TournamentStatus
from app.routes.deps import base_context, get_active_tournament, operator_name
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
        # Offer any past (archived) tournaments to reopen.
        ctx["archived"] = session.scalars(
            select(Tournament).where(Tournament.status == TournamentStatus.ARCHIVED)
            .order_by(Tournament.created_at.desc())
        ).all()
    return render(request, "welcome.html", ctx)


@router.get("/dashboard")
def dashboard(request: Request, session: Session = Depends(get_session)):
    tournament = get_active_tournament(session)
    if tournament is None:
        return RedirectResponse("/", status_code=303)
    ctx = base_context(request, session, "dashboard")
    ctx["board"] = dashboard_service.build_dashboard(session, tournament)
    return render(request, "dashboard.html", ctx)


@router.post("/teams/{team_id}/cash-count")
def record_cash_count(
    request: Request,
    team_id: int,
    session: Session = Depends(get_session),
    counted: str = Form(...),
):
    tournament = get_active_tournament(session)
    team = session.get(Team, team_id)
    if team is None or team.tournament_id != tournament.id:
        flash(request, "Team not found.", "danger")
        return RedirectResponse("/dashboard", status_code=303)
    try:
        counted_cents = dollars_to_cents(counted)
    except ValueError:
        flash(request, "That is not a valid dollar amount.", "danger")
        return RedirectResponse("/dashboard", status_code=303)
    session.add(CashCount(team_id=team_id, counted_cents=counted_cents, counted_by=operator_name(request)))
    flash(request, f"Cash count recorded for {team.name}.")
    return RedirectResponse("/dashboard", status_code=303)
