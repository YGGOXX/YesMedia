# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.dirname(os.path.abspath(SPECPATH))  # noqa: F821  (SPECPATH = <repo>/desktop)

hiddenimports = collect_submodules("yt_dlp")
hiddenimports += ["pystray._win32", "PIL.Image", "PIL.ImageDraw"]

datas = [
    (os.path.join(ROOT, "core", "webgrab", "static"), "webgrab/static"),
    (os.path.join(ROOT, "third_party", "ffmpeg", "bin", "ffmpeg.exe"), "third_party/ffmpeg/bin"),
    (os.path.join(ROOT, "third_party", "LICENSE-ffmpeg.txt"), "third_party"),
    (os.path.join(ROOT, "third_party", "LICENSE-chaquopy.txt"), "third_party"),
]

icon = os.path.join(ROOT, "build", "app.ico")
if not os.path.exists(icon):
    icon = None

a = Analysis(
    [os.path.join(ROOT, "desktop", "launcher.py")],
    pathex=[os.path.join(ROOT, "core")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter", "unittest", "pydoc", "test", "playwright", "pytest",
        "PyInstaller", "setuptools", "pip",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="WebGrab",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
    version=os.path.join(ROOT, "desktop", "version_info.txt"),
)
