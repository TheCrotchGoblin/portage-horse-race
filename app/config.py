"""Application configuration and local data directories.

All user data lives OUTSIDE the installation directory (per spec §10.2), under
``%LOCALAPPDATA%\\PortageHorseRace`` on Windows. The location can be overridden
with the ``HORSERACE_DATA_DIR`` environment variable (used by tests and dev).

Directories are created automatically on first launch so a non-technical user
never has to set anything up by hand.
"""
from __future__ import annotations

import os
from pathlib import Path


def _default_data_dir() -> Path:
    override = os.environ.get("HORSERACE_DATA_DIR")
    if override:
        return Path(override)
    # Windows: %LOCALAPPDATA%\PortageHorseRace ; fall back to home dir elsewhere.
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "PortageHorseRace"
    return Path.home() / ".portage-horse-race"


class Settings:
    """Resolved, filesystem-backed configuration for one running instance."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir: Path = Path(data_dir) if data_dir else _default_data_dir()
        self.db_dir: Path = self.data_dir / "db"
        self.backup_dir: Path = self.data_dir / "backups"
        self.export_dir: Path = self.data_dir / "exports"
        self.log_dir: Path = self.data_dir / "logs"

        # Networking — localhost only, never exposed to the LAN (spec NFR-08).
        self.host: str = os.environ.get("HORSERACE_HOST", "127.0.0.1")
        self.port: int = int(os.environ.get("HORSERACE_PORT", "8765"))

        # Signed-session secret. Stored in the data dir so it survives restarts
        # but stays on the local machine only.
        self._secret_file = self.data_dir / "session.secret"

    @property
    def db_path(self) -> Path:
        return self.db_dir / "horse_race.sqlite3"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path.as_posix()}"

    @property
    def lock_file(self) -> Path:
        return self.data_dir / "instance.lock"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.db_dir, self.backup_dir, self.export_dir, self.log_dir):
            d.mkdir(parents=True, exist_ok=True)

    def session_secret(self) -> str:
        """Return a stable per-installation secret, generating it once."""
        if self._secret_file.exists():
            return self._secret_file.read_text(encoding="utf-8").strip()
        self.ensure_dirs()
        secret = os.urandom(32).hex()
        self._secret_file.write_text(secret, encoding="utf-8")
        return secret


# Module-level singleton used by the app. Tests construct their own Settings.
settings = Settings()
