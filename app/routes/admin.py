"""Backup / restore and local settings (spec §6.7, §4)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app import database
from app.database import get_session
from app.routes.deps import base_context, get_setting, operator_name, set_setting
from app.services import audit
from app.services import backups as backup_service
from app.templating import flash, render

router = APIRouter(prefix="/admin")


@router.get("")
def admin(request: Request, session: Session = Depends(get_session)):
    ctx = base_context(request, session, "admin")
    settings = request.app.state.settings
    backups = backup_service.list_backups(settings.backup_dir)
    ctx.update({
        "backups": [{"name": b.name, "size_kb": round(b.stat().st_size / 1024, 1)} for b in backups],
        "operator": operator_name(request),
        "has_pin": bool(get_setting(session, "admin_pin")),
        "backup_dir": str(settings.backup_dir),
    })
    return render(request, "admin/index.html", ctx)


@router.post("/backup")
def create_backup(request: Request, session: Session = Depends(get_session)):
    settings = request.app.state.settings
    path = backup_service.backup_database(settings.db_path, settings.backup_dir, reason="manual")
    if path is None:
        flash(request, "There is no database to back up yet.", "warning")
    else:
        audit.record(session, action_type="backup_created", actor=operator_name(request),
                     entity_type="backup", entity_id=path.name)
        flash(request, f"Backup created: {path.name}")
    return RedirectResponse("/admin", status_code=303)


@router.post("/restore")
def restore(request: Request, backup_name: str = Form(...), admin_pin: str = Form("")):
    # Note: this route deliberately does NOT use the get_session dependency —
    # it disposes the engine and replaces the database file, so it manages its
    # own short-lived session for the PIN check and closes it first.
    settings = request.app.state.settings
    with database.SessionLocal() as check_session:
        configured_pin = get_setting(check_session, "admin_pin")
    if configured_pin and admin_pin != configured_pin:
        flash(request, "Incorrect administrator PIN — restore cancelled.", "danger")
        return RedirectResponse("/admin", status_code=303)

    source = settings.backup_dir / backup_name
    if not source.exists():
        flash(request, "That backup file was not found.", "danger")
        return RedirectResponse("/admin", status_code=303)

    # Close all live DB connections so the file can be replaced safely.
    if database.engine is not None:
        database.engine.dispose()
    try:
        safety = backup_service.restore_database(source, settings.db_path, settings.backup_dir)
    except ValueError as exc:
        flash(request, f"Restore aborted — {exc}. Your current data is unchanged.", "danger")
        return RedirectResponse("/admin", status_code=303)

    flash(request, f"Restored from {backup_name}. (A safety copy of the previous data was saved: "
                   f"{safety.name if safety else 'n/a'}.)")
    return RedirectResponse("/", status_code=303)


@router.post("/settings")
def save_settings(
    request: Request,
    session: Session = Depends(get_session),
    operator: str = Form(""),
    new_pin: str = Form(""),
    current_pin: str = Form(""),
):
    if operator.strip():
        request.session["operator"] = operator.strip()

    if new_pin.strip():
        existing = get_setting(session, "admin_pin")
        if existing and current_pin != existing:
            flash(request, "To change the PIN you must enter the current one.", "danger")
            return RedirectResponse("/admin", status_code=303)
        set_setting(session, "admin_pin", new_pin.strip())
        flash(request, "Settings saved. Administrator PIN updated.")
    else:
        flash(request, "Settings saved.")
    return RedirectResponse("/admin", status_code=303)
