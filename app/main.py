"""FastAPI application factory and startup wiring.

On startup it ensures data directories exist, opens the SQLite database with the
correct pragmas, creates the schema on first run, and takes an automatic backup
(spec FR-123). Binds to localhost only.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import APP_NAME, __version__
from app.config import Settings, settings as default_settings
from app.database import create_all, init_engine
from app.paths import STATIC_DIR
from app.services import backups

logger = logging.getLogger("horse_race")


def _configure_logging(settings: Settings) -> None:
    settings.ensure_dirs()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(settings.log_dir / "app.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or default_settings
    _configure_logging(settings)

    settings.ensure_dirs()
    init_engine(settings.db_url)

    first_run = not settings.db_path.exists()
    create_all()  # idempotent; safe on every start until Alembic baseline lands
    if not first_run:
        backups.backup_database(settings.db_path, settings.backup_dir, reason="startup")
        logger.info("Startup backup complete")
    else:
        logger.info("First run — initialised new database at %s", settings.db_path)

    app = FastAPI(title=APP_NAME, version=__version__)
    app.state.settings = settings

    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret(), same_site="lax")
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Routers are registered here as each feature phase lands.
    from app.routes import pages, setup as setup_routes, customers, cashier, ledger, results, payouts, reports, admin

    app.include_router(pages.router)
    app.include_router(setup_routes.router)
    app.include_router(customers.router)
    app.include_router(cashier.router)
    app.include_router(ledger.router)
    app.include_router(results.router)
    app.include_router(payouts.router)
    app.include_router(reports.router)
    app.include_router(admin.router)

    @app.get("/healthz", response_class=HTMLResponse)
    def healthz(_: Request) -> str:  # pragma: no cover - trivial
        return "ok"

    logger.info("%s v%s ready on http://%s:%s", APP_NAME, __version__, settings.host, settings.port)
    return app


app = create_app()
