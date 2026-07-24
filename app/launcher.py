"""Desktop launcher: single-instance, localhost server, auto-open browser.

This is what a double-clicked shortcut runs. It:
  * ensures data directories exist,
  * refuses to start a second copy against the same database (spec §10.2),
  * starts the FastAPI app on 127.0.0.1 at a fixed port,
  * opens the default browser to the app once it is listening,
  * writes logs locally.

The end user never sees a command prompt or types a URL.
"""
from __future__ import annotations

import logging
import socket
import sys
import threading
import time
import webbrowser

import uvicorn

from app.config import settings

logger = logging.getLogger("horse_race.launcher")


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


def _open_browser_when_ready(host: str, port: int, timeout: float = 15.0) -> None:
    url = f"http://{host}:{port}/"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_in_use(host, port):
            webbrowser.open(url)
            return
        time.sleep(0.25)
    # Open anyway as a last resort so the user isn't stuck.
    webbrowser.open(url)


def _ensure_std_streams() -> None:
    """Give the process real stdout/stderr when launched without a console.

    A windowed PyInstaller build (double-clicked, no console) has
    ``sys.stdout`` / ``sys.stderr`` set to None. uvicorn's log formatter calls
    ``sys.stdout.isatty()``, which then crashes. Point the streams at a log file
    so logging works and nothing sees a None stream.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    settings.ensure_dirs()
    log = open(settings.log_dir / "console.log", "a", buffering=1, encoding="utf-8", errors="replace")
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log


def main() -> int:
    _ensure_std_streams()
    settings.ensure_dirs()
    host, port = settings.host, settings.port

    # Single-instance guard: if the app is already serving, just focus it.
    if _port_in_use(host, port):
        logger.info("An instance is already running; opening the browser.")
        webbrowser.open(f"http://{host}:{port}/")
        return 0

    threading.Thread(target=_open_browser_when_ready, args=(host, port), daemon=True).start()

    # Import here so create_app() (and its startup backup) runs now, after the
    # single-instance check. Pass the app object (not an import string) so the
    # frozen PyInstaller build does not rely on re-importing by name.
    from app.main import app as fastapi_app

    uvicorn.run(fastapi_app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()  # safe no-op in dev; required in frozen builds
    sys.exit(main())
