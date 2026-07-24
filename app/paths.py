"""Resolve bundled resource directories in both dev and PyInstaller builds.

When frozen by PyInstaller, data files live under ``sys._MEIPASS``; in a normal
checkout they live next to this package.
"""
from __future__ import annotations

import sys
from pathlib import Path

if getattr(sys, "frozen", False):  # running inside a PyInstaller bundle
    APP_ROOT = Path(sys._MEIPASS) / "app"  # type: ignore[attr-defined]
else:
    APP_ROOT = Path(__file__).resolve().parent

TEMPLATES_DIR = APP_ROOT / "templates"
STATIC_DIR = APP_ROOT / "static"
