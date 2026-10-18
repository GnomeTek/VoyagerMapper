# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for voymap. Build from the repo root:
#     pyinstaller packaging/voymap.spec --noconfirm
# Output: dist/voymap/  (one-folder build; QtWebEngine needs its own process + resources,
# which a one-file build would have to unpack on every start).
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).parent
VERSION = {}
exec((ROOT / "voymap" / "__init__.py").read_text(), VERSION)

datas = [
    (str(ROOT / "voymap" / "gui" / "web"), "voymap/gui/web"),
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "README.md"), "."),
]

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineCore", "PySide6.QtWebChannel",
        "PySide6.QtNetwork", "PySide6.QtPrintSupport", "PySide6.QtPositioning",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # trim Qt modules the app doesn't use
        "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtQuick", "PySide6.QtQuick3D",
        "PySide6.QtQml", "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtBluetooth", "PySide6.QtNfc",
        "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtXml", "PySide6.QtSvgWidgets",
        "PySide6.QtRemoteObjects", "PySide6.QtSensors", "PySide6.QtTextToSpeech", "PySide6.QtWebSockets",
        "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools", "PySide6.QtScxml", "PySide6.QtStateMachine",
        "tkinter", "unittest", "pydoc",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="voymap",
    icon=str(ROOT / "packaging" / "voymap.ico") if (ROOT / "packaging" / "voymap.ico").exists() else None,
    console=False,           # GUI app: no console window
    debug=False, strip=False, upx=False,
)
cli = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="voymap-cli",
    console=True,            # same code, console attached: `voymap-cli export ...`
    debug=False, strip=False, upx=False,
)
coll = COLLECT(exe, cli, a.binaries, a.datas, strip=False, upx=False, name="voymap")
