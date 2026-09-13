# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for the Video Editor app.

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

datas = [
    ("settings/llm_base.json", "settings"),
]
datas += collect_data_files("customtkinter")

a = Analysis(
    ["main.py"],
    pathex=[str(Path(".").resolve())],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # GUI (customtkinter)
        "customtkinter",
        "darkdetect",
        "PIL",
        "PIL.Image",
        "PIL.ImageTk",
        # HTTP / LLM providers
        "requests",
        "urllib3",
        "charset_normalizer",
        "certifi",
        "idna",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Python Video Editor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="VideoEditor",
)
app = BUNDLE(
    coll,
    name="Python Video Editor.app",
    icon=None,
    bundle_identifier="com.jblanked.python-video-editor",
    version="1.0.0",
    info_plist={
        "NSHighResolutionCapable": True,
    },
)
