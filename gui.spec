# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []
import basic_pitch
import tensorflow

for package in ("basic_pitch", "imageio_ffmpeg", "librosa", "soundfile"):
    package_data, package_binaries, package_hidden = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hiddenimports += package_hidden

a = Analysis(
    ["gui.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
# Poppler on the host PATH exposes an incompatible ICU DLL. QtCore needs the
# Windows ICU export names, so do not place Poppler's copy beside the EXE.
a.binaries = [item for item in a.binaries if item[0].casefold() != "icuuc.dll"]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="VP Autoplayer",
    icon="assets/vp_autoplayer.ico",
    version="version_info.txt",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)

# -- by Larsiopuw
