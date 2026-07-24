# PyInstaller spec for Portage Horse Race (one-folder build).
# Build:  pyinstaller packaging/horse_race.spec --noconfirm
from PyInstaller.utils.hooks import collect_all, collect_submodules

hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("anyio")
    + ["app.main", "clr"]
)

datas = [
    ("../app/templates", "app/templates"),
    ("../app/static", "app/static"),
]

# pywebview (WebView2 via pythonnet) ships backends + .NET assets to bundle.
_binaries = []
for _pkg in ("webview", "pythonnet", "clr_loader"):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    _binaries += _b
    hiddenimports += _h
hiddenimports += ["clr", "clr_loader", "pythonnet"]

a = Analysis(
    ["../run.py"],
    pathex=["."],
    binaries=_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "playwright"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PortageHorseRace",
    console=False,          # no console window for a desktop app
    icon="icon.ico",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="PortageHorseRace",
)
