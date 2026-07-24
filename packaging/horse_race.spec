# PyInstaller spec for Portage Horse Race (one-folder build).
# Build:  pyinstaller packaging/horse_race.spec --noconfirm
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("anyio")
    + ["app.main"]
)

datas = [
    ("../app/templates", "app/templates"),
    ("../app/static", "app/static"),
]

a = Analysis(
    ["../run.py"],
    pathex=["."],
    binaries=[],
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
