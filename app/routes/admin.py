"""Backup / restore and local settings (spec §6.7, §4)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app import __version__, database
from app.database import get_session
from app.routes.deps import (
    admin_pin_ok,
    base_context,
    clear_admin_pin,
    has_admin_pin,
    operator_name,
    recovery_code_ok,
    set_admin_pin,
)
from app.services import audit
from app.services import backups as backup_service
from app.templating import flash, render

router = APIRouter(prefix="/admin")

REPO = "TheCrotchGoblin/portage-horse-race"


def _admin_context(request: Request, session: Session) -> dict:
    ctx = base_context(request, session, "admin")
    settings = request.app.state.settings
    backups = backup_service.list_backups(settings.backup_dir)
    ctx.update({
        "backups": [{"name": b.name, "size_kb": round(b.stat().st_size / 1024, 1),
                     "verified": backup_service.validate_backup(b)} for b in backups],
        "native_dialogs": getattr(request.app.state, "window", None) is not None,
        "operator": operator_name(request),
        "has_pin": has_admin_pin(session),
        "backup_dir": str(settings.backup_dir),
        "data_dir": str(settings.data_dir),
        "last_backup": backup_service.backup_health(settings.backup_dir)["last_at"],
        "app_version": __version__,
    })
    return ctx


def _version_tuple(v: str) -> tuple:
    parts = []
    for chunk in v.lstrip("vV").split("."):
        num = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts)


@router.get("")
def admin(request: Request, session: Session = Depends(get_session)):
    return render(request, "admin/index.html", _admin_context(request, session))


@router.get("/check-updates")
def check_updates(request: Request, session: Session = Depends(get_session)):
    """Optional, on-demand update check (spec REL-04). Offline-safe."""
    import httpx

    ctx = _admin_context(request, session)
    try:
        resp = httpx.get(
            f"https://api.github.com/repos/{REPO}/releases/latest",
            headers={"Accept": "application/vnd.github+json"}, timeout=6,
        )
        resp.raise_for_status()
        data = resp.json()
        latest = data.get("tag_name", "").lstrip("vV")
        url = data.get("html_url") or f"https://github.com/{REPO}/releases/latest"
        if latest and _version_tuple(latest) > _version_tuple(__version__):
            ctx["update"] = {"status": "newer", "latest": latest, "url": url}
        else:
            ctx["update"] = {"status": "current", "latest": latest or __version__}
    except Exception:
        ctx["update"] = {"status": "error"}
    return render(request, "admin/index.html", ctx)


@router.post("/backup")
def create_backup(request: Request, session: Session = Depends(get_session), return_to: str = Form("/admin")):
    settings = request.app.state.settings
    try:
        path = backup_service.backup_database(settings.db_path, settings.backup_dir,
                                              reason="manual", verify=True)
    except backup_service.BackupError as exc:
        flash(request, f"Backup FAILED verification — {exc}. Try again or check disk space.", "danger")
        return RedirectResponse(return_to if return_to.startswith("/") else "/admin", status_code=303)
    if path is None:
        flash(request, "There is no database to back up yet.", "warning")
    else:
        audit.record(session, action_type="backup_created", actor=operator_name(request),
                     entity_type="backup", entity_id=path.name)
        flash(request, f"Backup created and verified: {path.name}")
    return RedirectResponse(return_to if return_to.startswith("/") else "/admin", status_code=303)


@router.post("/restore")
def restore(request: Request, backup_name: str = Form(...), admin_pin: str = Form("")):
    # Note: this route deliberately does NOT use the get_session dependency —
    # it disposes the engine and replaces the database file, so it manages its
    # own short-lived session for the PIN check and closes it first.
    settings = request.app.state.settings
    with database.SessionLocal() as check_session:
        pin_ok = admin_pin_ok(check_session, admin_pin)
    if not pin_ok:
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


@router.post("/backup-to-usb")
def backup_to_usb(request: Request, session: Session = Depends(get_session)):
    """Copy the most recent backup to a folder the user picks (e.g. a USB stick)."""
    import shutil

    from app import native

    settings = request.app.state.settings
    window = native.get_window(request)
    if window is None:
        flash(request, "Saving to a drive needs the desktop app window. Open the backups folder "
                       f"manually instead: {settings.backup_dir}", "warning")
        return RedirectResponse("/admin", status_code=303)
    latest = backup_service.list_backups(settings.backup_dir)
    if not latest:
        flash(request, "There is no backup to copy yet — make one first.", "warning")
        return RedirectResponse("/admin", status_code=303)
    folder = native.pick_folder(window)
    if not folder:
        return RedirectResponse("/admin", status_code=303)  # cancelled
    from pathlib import Path
    dest = Path(folder) / latest[0].name
    try:
        shutil.copyfile(latest[0], dest)
    except OSError as exc:
        flash(request, f"Could not copy to that location — {exc}.", "danger")
        return RedirectResponse("/admin", status_code=303)
    audit.record(session, action_type="backup_exported", actor=operator_name(request),
                 entity_type="backup", entity_id=latest[0].name, after={"to": str(dest)})
    flash(request, f"Copied {latest[0].name} to {folder}. Keep this stick somewhere separate from the laptop.")
    return RedirectResponse("/admin", status_code=303)


@router.post("/restore-from-file")
def restore_from_file(request: Request, admin_pin: str = Form("")):
    """Restore from a .sqlite3 the user picks off a USB/Downloads (BKP-04)."""
    from app import native

    settings = request.app.state.settings
    with database.SessionLocal() as check_session:
        pin_ok = admin_pin_ok(check_session, admin_pin)
    if not pin_ok:
        flash(request, "Incorrect administrator PIN — restore cancelled.", "danger")
        return RedirectResponse("/admin", status_code=303)
    window = native.get_window(request)
    if window is None:
        flash(request, "Restoring from a file needs the desktop app window. In a browser, copy the "
                       f"file into the backups folder first: {settings.backup_dir}", "warning")
        return RedirectResponse("/admin", status_code=303)
    from pathlib import Path
    picked = native.pick_open_file(window, file_types=("Database (*.sqlite3;*.db)", "All files (*.*)"))
    if not picked:
        return RedirectResponse("/admin", status_code=303)  # cancelled
    if database.engine is not None:
        database.engine.dispose()
    try:
        safety = backup_service.restore_database(Path(picked), settings.db_path, settings.backup_dir)
    except ValueError as exc:
        flash(request, f"Restore aborted — {exc}. Your current data is unchanged.", "danger")
        return RedirectResponse("/admin", status_code=303)
    flash(request, f"Restored from {Path(picked).name}. (A safety copy of the previous data was saved: "
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
        if has_admin_pin(session) and not admin_pin_ok(session, current_pin):
            flash(request, "To change the PIN you must enter the current one.", "danger")
            return RedirectResponse("/admin", status_code=303)
        recovery = set_admin_pin(session, new_pin.strip())
        flash(request,
              f"Administrator PIN saved. RECOVERY CODE: {recovery} — write this down and keep it safe. "
              f"You'll need it to reset the PIN if you forget it.", "warning")
    else:
        flash(request, "Settings saved.")
    return RedirectResponse("/admin", status_code=303)


@router.post("/forgot-pin")
def forgot_pin(request: Request, session: Session = Depends(get_session), recovery_code: str = Form("")):
    if not has_admin_pin(session):
        flash(request, "No administrator PIN is set.", "info")
        return RedirectResponse("/admin", status_code=303)
    if not recovery_code_ok(session, recovery_code):
        flash(request, "That recovery code is not correct.", "danger")
        return RedirectResponse("/admin", status_code=303)
    clear_admin_pin(session)
    flash(request, "Recovery code accepted — the administrator PIN has been cleared. Set a new one below.")
    return RedirectResponse("/admin", status_code=303)
