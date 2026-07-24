"""Desktop launcher: a native app window hosting the local server.

Double-clicking runs this. It:
  * gives the process real stdout/stderr when launched without a console,
  * starts the FastAPI app on 127.0.0.1 at a fixed port (unless one is already
    running — single-instance),
  * opens a native desktop window (WebView2) showing the app — no browser, no
    address bar,
  * falls back to the default browser if a native window can't be created,
  * shuts the server down cleanly when the window closes.
"""
from __future__ import annotations

import logging
import socket
import sys
import threading
import time
import webbrowser

import uvicorn

from app import APP_NAME
from app.config import settings

logger = logging.getLogger("horse_race.launcher")

WINDOW_TITLE = APP_NAME


def _ensure_std_streams() -> None:
    """Give the process real stdout/stderr when launched without a console.

    A windowed build (double-clicked) has ``sys.stdout`` / ``sys.stderr`` = None;
    uvicorn's log formatter calls ``sys.stdout.isatty()`` and crashes otherwise.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    settings.ensure_dirs()
    log = open(settings.log_dir / "console.log", "a", buffering=1, encoding="utf-8", errors="replace")
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


def _start_server(app, host: str, port: int) -> uvicorn.Server:
    """Run uvicorn in a background thread and wait until it is serving."""
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):  # up to ~10s
        if getattr(server, "started", False) or not thread.is_alive():
            break
        time.sleep(0.05)
    return server


def main() -> int:
    _ensure_std_streams()
    settings.ensure_dirs()
    host, port = settings.host, settings.port
    url = f"http://{host}:{port}/"

    # Single instance: only start a server if one isn't already running.
    server: uvicorn.Server | None = None
    if not _port_in_use(host, port):
        from app.main import app as fastapi_app

        server = _start_server(fastapi_app, host, port)
    else:
        logger.info("An instance is already serving; opening another window to it.")

    try:
        import webview

        webview.create_window(WINDOW_TITLE, url, width=1240, height=840, min_size=(960, 680))
        webview.start()  # blocks until the window is closed
    except Exception:
        logger.exception("Native window unavailable; opening the default browser instead")
        webbrowser.open(url)
        if server is not None:
            try:
                while getattr(server, "started", False):
                    time.sleep(0.5)
            except KeyboardInterrupt:
                pass
    finally:
        if server is not None:
            server.should_exit = True
    return 0


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    sys.exit(main())
