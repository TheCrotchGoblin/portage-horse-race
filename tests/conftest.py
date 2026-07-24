"""Shared pytest fixtures: an app + TestClient backed by a throwaway database."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture()
def settings():
    import shutil
    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.mkdtemp(prefix="hr_test_"))
    try:
        yield Settings(data_dir=tmp / "data")
    finally:
        # ignore_errors so any Windows-locked WAL/log file never blocks teardown
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture()
def client(settings):
    import logging

    app = create_app(settings)
    client = TestClient(app)
    try:
        yield client
    finally:
        # Release DB connections and log-file handles so the temp dir can be
        # cleaned on Windows (open files otherwise block teardown).
        client.close()
        from app import database

        if database.engine is not None:
            database.engine.dispose()
        root = logging.getLogger()
        for handler in list(root.handlers):
            handler.close()
            root.removeHandler(handler)


@pytest.fixture()
def db(settings):
    """A session bound to the same engine the client uses (call after create_app)."""
    from app.database import SessionLocal

    assert SessionLocal is not None
    return SessionLocal()


def make_tournament(client: TestClient, *, open_wagering: bool = True):
    """Helper: create a tournament with two teams and players, optionally opened."""
    client.post("/setup/new", data={
        "name": "Test Open", "entry_price": "5.00", "club_percent": "15",
        "first_percent": "60", "second_percent": "30", "third_percent": "10",
    })
    client.post("/setup/teams", data={"team_name": "Team A"})
    client.post("/setup/teams", data={"team_name": "Team B"})
    from app.database import SessionLocal
    from app.models import Team
    session = SessionLocal()
    teams = session.scalars(__import__("sqlalchemy").select(Team).order_by(Team.id)).all()
    for t in teams:
        client.post(f"/setup/teams/{t.id}/players", data={"players": "Alice\nBob\nCarol"})
    if open_wagering:
        client.post("/setup/open")
    session.close()
    return teams
