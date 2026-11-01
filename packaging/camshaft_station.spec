# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build for Windows — ONEDIR layout (folder + exe), same as cut-mark-station.
# One-file mode unpacks to %TEMP% on every start and is often quarantined by Defender.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
APP_NAME = "CamShaft_Inspection_Station"

hiddenimports = [
    "multipart",
    "python_multipart",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "anyio._backends._asyncio",
    "reportlab.pdfbase._fontdata",
    "reportlab.graphics.barcode",
    "encodings.idna",
    "tkinter",
    "tkinter.filedialog",
    "zxingcpp",
    "joblib",
]
# The thread model is a pickled scikit-learn pipeline: bundle the sklearn modules it references.
for package in ("sklearn", "scipy._lib", "scipy.sparse", "scipy.linalg", "scipy.special"):
    try:
        hiddenimports += collect_submodules(package)
    except Exception:
        pass

excludes = [
    "uvloop", "httptools", "watchfiles", "matplotlib", "pandas", "IPython", "jupyter", "notebook",
    "pytest", "PyQt5", "PyQt6", "PySide2", "PySide6", "wx", "idlelib", "lib2to3",
]

datas = [
    (str(ROOT / "frontend" / "out"), "frontend/out"),
    (str(ROOT / "models" / "thread_classifier.joblib"), "models"),
    (str(ROOT / "assets"), "assets"),
]
for package in ("reportlab", "sklearn"):
    try:
        datas += collect_data_files(package)
    except Exception:
        pass

version_file = ROOT / "packaging" / "version_info.txt"

a = Analysis(
    [str(ROOT / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=None,
    version=str(version_file) if version_file.is_file() else None,
    exclude_binaries=True,
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=APP_NAME)
