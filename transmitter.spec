# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['transmitter.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('dsktp_app.ico', '.')  # Packages the desktop icon into the build folder
    ],
    hiddenimports=[
        'numpy',
        'sounddevice',
        'websockets',
        'pystray',
        'PIL',
        'win32timezone'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='transmitter',
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
    icon='am-fm_app.ico',  # Main App / Taskbar Icon
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='transmitter',
)