"""Native desktop file dialogs via pywebview (spec BKP-04).

The launcher stores the WebView2 window on ``app.state.window``. These helpers
let routes open real "Open…" / "Save…" dialogs so a volunteer can move an event
to a USB stick or a spare laptop. When there's no native window (browser
fallback, tests) every helper returns None and the caller degrades gracefully.
"""
from __future__ import annotations

from typing import Any


def get_window(request) -> Any | None:
    return getattr(request.app.state, "window", None)


def pick_open_file(window, *, file_types=()) -> str | None:
    try:
        import webview
        result = window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False,
                                            file_types=file_types)
    except Exception:
        return None
    if not result:
        return None
    return result[0] if isinstance(result, (list, tuple)) else result


def pick_save_path(window, *, default_name: str = "") -> str | None:
    try:
        import webview
        result = window.create_file_dialog(webview.SAVE_DIALOG, save_filename=default_name)
    except Exception:
        return None
    if not result:
        return None
    return result[0] if isinstance(result, (list, tuple)) else result


def pick_folder(window) -> str | None:
    try:
        import webview
        result = window.create_file_dialog(webview.FOLDER_DIALOG)
    except Exception:
        return None
    if not result:
        return None
    return result[0] if isinstance(result, (list, tuple)) else result
