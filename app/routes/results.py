"""Results (placement) routes (spec §6.6, §7.4)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_session
from app.formatting import int_or_none
from app.models import Placement, Player, Team
from app.routes.deps import admin_pin_ok, base_context, operator_name
from app.services import payouts as payout_service
from app.services.payouts import PayoutError
from app.templating import flash, render

router = APIRouter(prefix="/results")


def _teams(session, tournament_id):
    return session.scalars(select(Team).where(Team.tournament_id == tournament_id).order_by(Team.id)).all()


@router.get("")
def results(request: Request, session: Session = Depends(get_session), team_id: str | None = None):
    ctx = base_context(request, session, "results")
    tournament = ctx["tournament"]
    if tournament is None:
        return RedirectResponse("/setup/new", status_code=303)
    teams = _teams(session, tournament.id)
    if not teams:
        flash(request, "Add teams and players first.", "warning")
        return RedirectResponse("/setup", status_code=303)
    team_id_i = int_or_none(team_id)
    team = session.get(Team, team_id_i) if team_id_i else teams[0]
    if team is None or team.tournament_id != tournament.id:
        team = teams[0]

    players = session.scalars(
        select(Player).where(Player.team_id == team.id).order_by(Player.display_order, Player.id)
    ).all()
    placements = {p.position: p for p in session.scalars(select(Placement).where(Placement.team_id == team.id)).all()}
    ctx.update({
        "teams": teams,
        "team": team,
        "players": players,
        "placements": placements,
        "previews": payout_service.placement_previews(session, tournament, team),
        "has_payouts": payout_service.has_payouts(session, team.id),
    })
    return render(request, "results/index.html", ctx)


@router.post("/{team_id}/placements")
def set_placements(
    request: Request,
    team_id: int,
    session: Session = Depends(get_session),
    first_player_id: str = Form(""),
    second_player_id: str = Form(""),
    third_player_id: str = Form(""),
    admin_pin: str = Form(""),
):
    ctx = base_context(request, session, "results")
    tournament = ctx["tournament"]
    team = session.get(Team, team_id)
    if team is None or team.tournament_id != tournament.id:
        flash(request, "Team not found.", "danger")
        return RedirectResponse("/results", status_code=303)
    if not admin_pin_ok(session, admin_pin):
        flash(request, "Incorrect administrator PIN.", "danger")
        return RedirectResponse(f"/results?team_id={team_id}", status_code=303)
    first_i, second_i, third_i = int_or_none(first_player_id), int_or_none(second_player_id), int_or_none(third_player_id)
    if not (first_i and second_i and third_i):
        flash(request, "Please choose a player for 1st, 2nd and 3rd place.", "danger")
        return RedirectResponse(f"/results?team_id={team_id}", status_code=303)
    try:
        payout_service.set_placements(
            session, tournament, team,
            first_player_id=first_i, second_player_id=second_i,
            third_player_id=third_i, operator=operator_name(request), finalize=True,
        )
    except PayoutError as exc:
        flash(request, str(exc), "danger")
        return RedirectResponse(f"/results?team_id={team_id}", status_code=303)
    flash(request, f"Results saved for {team.name}. Review the preview, then generate payouts.")
    return RedirectResponse(f"/results?team_id={team_id}", status_code=303)


@router.post("/{team_id}/generate")
def generate(request: Request, team_id: int, session: Session = Depends(get_session), admin_pin: str = Form("")):
    ctx = base_context(request, session, "results")
    tournament = ctx["tournament"]
    team = session.get(Team, team_id)
    if not admin_pin_ok(session, admin_pin):
        flash(request, "Incorrect administrator PIN.", "danger")
        return RedirectResponse(f"/results?team_id={team_id}", status_code=303)
    try:
        summary = payout_service.generate_payouts(session, tournament, team, operator=operator_name(request))
    except PayoutError as exc:
        flash(request, str(exc), "danger")
        return RedirectResponse(f"/results?team_id={team_id}", status_code=303)

    # Automatic backup after a material settlement event (spec FR-123).
    session.commit()
    settings = request.app.state.settings
    from app.services import backups as backup_service
    backup_service.backup_database(settings.db_path, settings.backup_dir, reason="after_payouts")
    msg = f"Generated {summary['created']} payout(s) for {team.name}."
    if summary["unclaimed_cents"]:
        from app.formatting import cents_to_dollars
        msg += f" {cents_to_dollars(summary['unclaimed_cents'])} is UNCLAIMED (a placed player had no wagers) — decide what to do with it."
    flash(request, msg, "warning" if summary["unclaimed_cents"] else "success")
    return RedirectResponse("/payouts", status_code=303)


@router.post("/{team_id}/regenerate")
def regenerate(request: Request, team_id: int, session: Session = Depends(get_session), admin_pin: str = Form("")):
    ctx = base_context(request, session, "results")
    tournament = ctx["tournament"]
    team = session.get(Team, team_id)
    if not admin_pin_ok(session, admin_pin):
        flash(request, "Incorrect administrator PIN.", "danger")
        return RedirectResponse(f"/results?team_id={team_id}", status_code=303)
    try:
        payout_service.regenerate_payouts(session, tournament, team, operator=operator_name(request))
    except PayoutError as exc:
        flash(request, str(exc), "danger")
        return RedirectResponse(f"/results?team_id={team_id}", status_code=303)
    flash(request, "Payouts cleared. You can now change the results and generate again.")
    return RedirectResponse(f"/results?team_id={team_id}", status_code=303)
