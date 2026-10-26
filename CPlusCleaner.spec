# Purpose: Single-file Windows executable with bundled runtime resources
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH)
a = Analysis(
    [str(root/'app.py')],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root/name), name) for name in ('assets', 'rules', 'locales')]
          + [(str(root/'LICENSE'), '.')],
    hiddenimports=collect_submodules('cplus_cleaner'),
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='CPlusCleaner',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(root/'assets/tray.ico'),
)
