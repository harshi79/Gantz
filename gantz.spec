# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build definition for Gantz.

Produces a single self-contained ``Gantz.exe`` (no installer, no Python
required on the target machine, no network access at runtime).

    pyinstaller --noconfirm gantz.spec
"""

import os
from pathlib import Path

ROOT = Path(SPECPATH)  # noqa: F821 - injected by PyInstaller
ONE_DIR = os.environ.get("GANTZ_ONEDIR") == "1"
ICON = ROOT / "assets" / "gantz.ico"
VERSION_FILE = ROOT / "version_info.txt"

a = Analysis(  # noqa: F821
    ["gantz_gui.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        ("assets/gantz.png", "assets"),
        ("assets/gantz.ico", "assets"),
    ],
    hiddenimports=["tkinter", "tkinter.ttk", "tkinter.filedialog", "tkinter.messagebox",
                   "tkinter.font"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Keep the binary small: nothing here is used by Gantz.
    excludes=[
        "unittest", "doctest", "pydoc", "pdb", "test",
        "distutils", "setuptools", "pip", "pkg_resources",
        "numpy", "pandas", "PIL", "matplotlib", "scipy",
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries if not ONE_DIR else [],
    a.datas if not ONE_DIR else [],
    [],
    name="Gantz",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX compression is a common antivirus false-positive trigger
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # GUI app: no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON) if ICON.is_file() else None,
    version=str(VERSION_FILE) if VERSION_FILE.is_file() else None,
)

if ONE_DIR:
    coll = COLLECT(  # noqa: F821
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="Gantz",
    )
