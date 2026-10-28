# Purpose: Single-file Windows executable with bundled runtime resources
import ast
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo,
    VarStruct, VSVersionInfo,
)

root = Path(SPECPATH)
preferences = ast.parse((root/'cplus_cleaner/application/preferences.py').read_text(encoding='utf-8'))
version = next(ast.literal_eval(node.value) for node in preferences.body
               if isinstance(node, ast.Assign) and any(
                   isinstance(target, ast.Name) and target.id == 'VERSION' for target in node.targets))
parts = tuple(int(part) for part in version.split('.'))
if len(parts) not in (3, 4) or any(part < 0 or part > 65535 for part in parts):
    raise ValueError('VERSION must contain three or four Windows version numbers.')
version_tuple = parts + (0,) * (4 - len(parts))
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=version_tuple, prodvers=version_tuple,
                      mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable('040904B0', [
        StringStruct('CompanyName', 'CPlus Cleaner'),
        StringStruct('FileDescription', 'CPlus Cleaner - Drive Cleanup Assistant'),
        StringStruct('FileVersion', version),
        StringStruct('InternalName', 'CPlusCleaner'),
        StringStruct('LegalCopyright', 'Copyright (c) 2026 jackqiuai'),
        StringStruct('OriginalFilename', 'CPlusCleaner.exe'),
        StringStruct('ProductName', 'CPlus Cleaner'),
        StringStruct('ProductVersion', version),
    ])]), VarFileInfo([VarStruct('Translation', [0x0409, 1200])])],
)
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
    version=version_info,
)
