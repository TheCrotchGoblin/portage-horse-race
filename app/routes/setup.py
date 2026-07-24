"""Setup wizard routes (spec §6.1)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_session
from app.formatting import dollars_to_cents
from app.models import Player, Team, Tournament
from app.models.enums import TournamentStatus
from app.routes.deps import admin_pin_ok, base_context, get_active_tournament, operator_name
from app.services import setup as setup_service
from app.services.setup import SetupError
from app.templating import flash, render

router = APIRouter(prefix="/setup")


def _redirect(url: str = "/setup") -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _invalid_fields(msg: str) -> list[str]:
    """Map a validation message to the form field(s) to highlight."""
    m = msg.lower()
    fields = []
    if "name" in m:
        fields.append("name")
    if "price" in m:
        fields.append("price")
    if "club" in m:
        fields.append("club")
    if "split" in m or "100%" in m or "percentage" in m:
        fields.append("split")
    return fields


def _overview_context(request: Request, session: Session, tournament: Tournament) -> dict:
    ctx = base_context(request, session, "setup")
    ctx["teams"] = session.scalars(
        select(Team).where(Team.tournament_id == tournament.id).order_by(Team.id)
    ).all()
    ctx["locked"] = setup_service.has_sales(session, tournament.id)
    ctx["archived"] = session.scalars(
        select(Tournament).where(Tournament.status == TournamentStatus.ARCHIVED)
        .order_by(Tournament.created_at.desc())
    ).all()
    return ctx


@router.get("")
def overview(request: Request, session: Session = Depends(get_session)):
    tournament = get_active_tournament(session)
    if tournament is None:
        return _redirect("/setup/new")
    return render(request, "setup/overview.html", _overview_context(request, session, tournament))


@router.get("/new")
def new_form(request: Request, session: Session = Depends(get_session)):
    ctx = base_context(request, session, "setup")
    return render(request, "setup/new.html", ctx)


@router.post("/new")
def create(
    request: Request,
    session: Session = Depends(get_session),
    name: str = Form(...),
    event_date: str = Form(""),
    entry_price: str = Form("5.00"),
    club_percent: str = Form("15"),
    first_percent: str = Form("60"),
    second_percent: str = Form("30"),
    third_percent: str = Form("10"),
):
    try:
        tournament = setup_service.create_tournament(
            session,
            name=name,
            event_date=event_date,
            entry_price_cents=dollars_to_cents(entry_price),
            club_bps=setup_service.percent_to_bps(club_percent),
            first_bps=setup_service.percent_to_bps(first_percent),
            second_bps=setup_service.percent_to_bps(second_percent),
            third_bps=setup_service.percent_to_bps(third_percent),
            operator=operator_name(request),
        )
    except (SetupError, ValueError) as exc:
        ctx = base_context(request, session, "setup")
        ctx.update({
            "error": str(exc),
            "invalid_fields": _invalid_fields(str(exc)),
            "values": {
                "name": name, "event_date": event_date, "entry_price": entry_price,
                "club_percent": club_percent, "first_percent": first_percent,
                "second_percent": second_percent, "third_percent": third_percent,
            },
        })
        return render(request, "setup/new.html", ctx, status_code=400)
    flash(request, f"Tournament '{tournament.name}' created. Now add your teams and players.")
    return _redirect("/setup")


@router.post("/config")
def update_config(
    request: Request,
    session: Session = Depends(get_session),
    name: str = Form(...),
    event_date: str = Form(""),
    entry_price: str = Form(...),
    club_percent: str = Form(...),
    first_percent: str = Form(...),
    second_percent: str = Form(...),
    third_percent: str = Form(...),
):
    tournament = get_active_tournament(session)
    try:
        setup_service.update_config(
            session,
            tournament,
            name=name,
            event_date=event_date,
            entry_price_cents=dollars_to_cents(entry_price),
            club_bps=setup_service.percent_to_bps(club_percent),
            first_bps=setup_service.percent_to_bps(first_percent),
            second_bps=setup_service.percent_to_bps(second_percent),
            third_bps=setup_service.percent_to_bps(third_percent),
            operator=operator_name(request),
        )
    except (SetupError, ValueError) as exc:
        ctx = _overview_context(request, session, tournament)
        ctx.update({
            "config_error": str(exc),
            "config_invalid": _invalid_fields(str(exc)),
            "config_values": {
                "name": name, "event_date": event_date, "entry_price": entry_price,
                "club_percent": club_percent, "first_percent": first_percent,
                "second_percent": second_percent, "third_percent": third_percent,
            },
        })
        return render(request, "setup/overview.html", ctx, status_code=400)
    flash(request, "Settings saved.")
    return _redirect("/setup")


@router.post("/teams")
def add_team(request: Request, session: Session = Depends(get_session), team_name: str = Form(...)):
    tournament = get_active_tournament(session)
    try:
        setup_service.add_team(session, tournament, team_name)
    except SetupError as exc:
        flash(request, str(exc), "danger")
        return _redirect("/setup")
    flash(request, f"Team '{team_name}' added.")
    return _redirect("/setup")


@router.post("/teams/{team_id}/players")
def add_players(request: Request, team_id: int, session: Session = Depends(get_session), players: str = Form("")):
    tournament = get_active_tournament(session)
    team = session.get(Team, team_id)
    if team is None or team.tournament_id != tournament.id:
        flash(request, "That team was not found.", "danger")
        return _redirect("/setup")
    names = [line for line in players.replace(",", "\n").splitlines()]
    created, skipped = setup_service.add_players(session, team, names)
    msg = f"Added {len(created)} player(s) to {team.name}."
    if skipped:
        msg += f" Skipped {len(skipped)} name(s) already on the team: {', '.join(skipped)}."
    flash(request, msg, "warning" if skipped else "success")
    return _redirect("/setup")


@router.post("/players/{player_id}/delete")
def delete_player(request: Request, player_id: int, session: Session = Depends(get_session)):
    tournament = get_active_tournament(session)
    player = session.get(Player, player_id)
    if player is not None:
        try:
            setup_service.delete_player(session, tournament, player)
            flash(request, "Player removed.")
        except SetupError as exc:
            flash(request, str(exc), "danger")
    return _redirect("/setup")


@router.post("/teams/{team_id}/delete")
def delete_team(request: Request, team_id: int, session: Session = Depends(get_session)):
    tournament = get_active_tournament(session)
    team = session.get(Team, team_id)
    if team is not None:
        try:
            setup_service.delete_team(session, tournament, team)
            flash(request, "Team removed.")
        except SetupError as exc:
            flash(request, str(exc), "danger")
    return _redirect("/setup")


@router.post("/open")
def open_wagering(request: Request, session: Session = Depends(get_session), team_id: str = Form("")):
    tournament = get_active_tournament(session)
    try:
        setup_service.open_wagering(
            session, tournament, operator_name(request), int(team_id) if team_id else None
        )
    except SetupError as exc:
        flash(request, str(exc), "danger")
        return _redirect("/setup")
    flash(request, "Wagering is now OPEN. Head to the Cashier screen to take wagers.")
    return _redirect("/setup")


@router.post("/close")
def close_wagering(request: Request, session: Session = Depends(get_session), team_id: str = Form("")):
    tournament = get_active_tournament(session)
    setup_service.close_wagering(
        session, tournament, operator_name(request), int(team_id) if team_id else None
    )
    flash(request, "Wagering closed. No new wagers will be accepted.")
    return _redirect("/setup")


@router.post("/archive")
def archive(request: Request, session: Session = Depends(get_session), admin_pin: str = Form("")):
    tournament = get_active_tournament(session)
    if tournament is None:
        return _redirect("/setup/new")
    if not admin_pin_ok(session, admin_pin):
        flash(request, "Incorrect administrator PIN — nothing was archived.", "danger")
        return _redirect("/setup")
    setup_service.archive_tournament(session, tournament, operator_name(request))
    flash(request, f"'{tournament.name}' archived. You can start a new tournament now.")
    return _redirect("/setup/new")


@router.post("/{tournament_id}/reopen")
def reopen(request: Request, tournament_id: int, session: Session = Depends(get_session), admin_pin: str = Form("")):
    tournament = session.get(Tournament, tournament_id)
    if tournament is None:
        flash(request, "Tournament not found.", "danger")
        return _redirect("/setup")
    if not admin_pin_ok(session, admin_pin):
        flash(request, "Incorrect administrator PIN.", "danger")
        return _redirect("/setup")
    try:
        setup_service.reopen_tournament(session, tournament, operator_name(request))
    except SetupError as exc:
        flash(request, str(exc), "danger")
        return _redirect("/setup")
    flash(request, f"'{tournament.name}' reopened.")
    return _redirect("/setup")
