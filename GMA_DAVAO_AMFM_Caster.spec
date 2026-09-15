# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all


# Collect package data/binaries that PyInstaller may otherwise miss.
datas = []
binaries = []
hiddenimports = [
    "websocket",
    "websocket._abnf",
    "websocket._app",
    "websocket._core",
    "websocket._exceptions",
    "websocket._handshake",
    "websocket._http",
    "websocket._logging",
    "websocket._socket",
    "websocket._ssl_compat",
    "websocket._url",
    "websocket._utils",
    "win32gui",
    "win32con",
    "win32api",
]

for package in [
    "av",
    "customtkinter",
    "sounddevice",
    "pystray",
    "PIL",
]:
    try:
        tmp_datas, tmp_binaries, tmp_hiddenimports = collect_all(package)
        datas += tmp_datas
        binaries += tmp_binaries
        hiddenimports += tmp_hiddenimports
    except Exception:
        pass


a = Analysis(
    ["amfmCaster.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib",
        "pandas",
        "scipy",
        "jupyter",
        "pytest",
        "IPython",
        "notebook",
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
    name="GMA_DAVAO_AMFM_Caster",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="am-fm_app.ico",
)