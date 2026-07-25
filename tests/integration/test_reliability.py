"""Batch 5: verified backups, retention, settlement manifest/fingerprint, crash marker."""
from __future__ import annotations

import hashlib
import io
import zipfile

import pytest
from sqlalchemy import select

from app import database
from app.models import Team
from app.services import backups as backup_service
from tests.conftest import make_tournament


def test_backup_verify_and_prune(client, settings):
    make_tournament(client)
    bdir = settings.backup_dir
    # A verified manual backup succeeds.
    path = backup_service.backup_database(settings.db_path, bdir, reason="manual", verify=True)
    assert path is not None and path.exists()

    # Retention keeps milestones but caps routine (startup/autosave) backups.
    for _ in range(30):
        backup_service.backup_database(settings.db_path, bdir, reason="autosave", prune=True)
    routine = [p for p in backup_service.list_backups(bdir)
               if backup_service._reason_of(p) not in backup_service.MILESTONE_REASONS]
    assert len(routine) <= 20                       # pruned to the cap
    milestones = [p for p in backup_service.list_backups(bdir)
                  if backup_service._reason_of(p) == "manual"]
    assert len(milestones) == 1                      # milestone kept


def test_backup_verify_raises_on_corrupt(client, settings):
    make_tournament(client)
    corrupt = settings.backup_dir / "horse_race_20200101_000000_manual.sqlite3"
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    corrupt.write_bytes(b"not a database")
    assert backup_service.validate_backup(corrupt) is False


def test_settlement_package_manifest_and_fingerprint(client, settings):
    make_tournament(client)
    r = client.post("/reports/settlement-package")
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    assert {"reconciliation.txt", "MANIFEST.sha256", "OPEN_ME.html"} <= names

    # Every checksum in the manifest matches the file it names.
    manifest = z.read("MANIFEST.sha256").decode()
    for line in manifest.strip().splitlines():
        digest, name = line.split("  ", 1)
        assert hashlib.sha256(z.read(name)).hexdigest() == digest

    # The fingerprint is printed in reconciliation.txt and echoed in OPEN_ME.
    recon = z.read("reconciliation.txt").decode()
    assert "Package fingerprint:" in recon
    fp = recon.split("Package fingerprint:")[1].strip().split()[0]
    assert fp in z.read("OPEN_ME.html").decode()


def test_crash_marker_detects_unclean_shutdown(settings):
    """A leftover running.flag on a non-first run flags an unclean shutdown."""
    from app.main import create_app
    from fastapi.testclient import TestClient

    # First run creates the DB and writes the marker.
    app1 = create_app(settings)
    c1 = TestClient(app1)
    make_tournament(c1)
    assert (settings.data_dir / "running.flag").exists()
    assert app1.state.recovered_unclean is False   # first run is never "unclean"
    c1.close()
    database.engine.dispose()

    # Marker still present (no clean shutdown) -> next start flags recovery.
    app2 = create_app(settings)
    assert app2.state.recovered_unclean is True
    c2 = TestClient(app2)
    # The dashboard shows the all-clear once, then clears it.
    body = c2.get("/dashboard").text
    assert "didn't close normally" in body
    assert app2.state.recovered_unclean is False
    c2.close()
    database.engine.dispose()
