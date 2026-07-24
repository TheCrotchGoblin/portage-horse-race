"""SQLite backup / restore (spec FR-122..124).

Uses SQLite's online backup API so a consistent copy is taken even while the
app holds the database open (WAL mode). Phase 7 adds restore + retention UI;
this module already provides the primitives used at startup.
"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def backup_database(db_path: Path, backup_dir: Path, *, reason: str = "manual") -> Path | None:
    """Create a timestamped consistent copy of the database.

    Returns the backup path, or None if there is no database to back up yet.
    """
    db_path = Path(db_path)
    backup_dir = Path(backup_dir)
    if not db_path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)

    safe_reason = "".join(c for c in reason if c.isalnum() or c in "-_") or "backup"
    dest = backup_dir / f"horse_race_{_timestamp()}_{safe_reason}.sqlite3"

    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)  # atomic, consistent online backup
        finally:
            dst.close()
    finally:
        src.close()
    return dest


def list_backups(backup_dir: Path) -> list[Path]:
    backup_dir = Path(backup_dir)
    if not backup_dir.exists():
        return []
    return sorted(backup_dir.glob("horse_race_*.sqlite3"), reverse=True)


def backup_health(backup_dir: Path) -> dict:
    """Summary of backup state for the dashboard/admin (spec BKP-02)."""
    from datetime import datetime

    backups = list_backups(backup_dir)
    if not backups:
        return {"last_at": None, "count": 0, "dir": str(backup_dir), "name": None}
    newest = backups[0]
    return {
        "last_at": datetime.fromtimestamp(newest.stat().st_mtime),
        "count": len(backups),
        "dir": str(backup_dir),
        "name": newest.name,
    }


def validate_backup(path: Path) -> bool:
    """Confirm a file is a readable SQLite DB with the expected core table."""
    try:
        conn = sqlite3.connect(str(path))
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            if not row or row[0] != "ok":
                return False
            conn.execute("SELECT 1 FROM tournaments LIMIT 1")
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    return True


def restore_database(source: Path, db_path: Path, backup_dir: Path) -> Path | None:
    """Replace the live DB with a validated backup, backing up current first.

    Returns the safety backup created from the pre-restore database (if any).
    Raises ValueError if the source is not a valid database.
    """
    source = Path(source)
    if not validate_backup(source):
        raise ValueError("selected backup is not a valid database")

    safety = backup_database(db_path, backup_dir, reason="pre_restore")
    # Copy WAL-checkpointed source over the live file.
    tmp = sqlite3.connect(str(source))
    try:
        dst = sqlite3.connect(str(db_path))
        try:
            tmp.backup(dst)
        finally:
            dst.close()
    finally:
        tmp.close()
    # Remove stale WAL/SHM sidecars so the restored DB is authoritative.
    for suffix in ("-wal", "-shm"):
        side = Path(str(db_path) + suffix)
        if side.exists():
            side.unlink()
    return safety
