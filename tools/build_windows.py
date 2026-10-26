"""Build and verify the Windows x64 EXE, portable ZIP and SHA-256 checksums."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    if os.name != 'nt' or struct.calcsize('P') != 8:
        raise SystemExit('Use 64-bit Python on Windows to build the Windows x64 executable.')
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                    str(ROOT/'CPlusCleaner.spec')], cwd=ROOT, check=True)
    dist = ROOT/'dist'
    executable = dist/'CPlusCleaner.exe'
    data = executable.read_bytes()
    offset = struct.unpack_from('<I', data, 0x3c)[0]
    assert data[:2] == b'MZ' and data[offset:offset+4] == b'PE\0\0'
    assert struct.unpack_from('<H', data, offset+4)[0] == 0x8664, 'Expected x64 executable'
    assert struct.unpack_from('<H', data, offset+24+68)[0] == 2, 'Expected a windowed executable'
    report = ROOT/'build/smoke-test.json'
    report.unlink(missing_ok=True)
    completed = subprocess.run([str(executable), '--smoke-test', str(report)], cwd=dist, timeout=120)
    result = json.loads(report.read_text(encoding='utf-8'))
    if completed.returncode or not result.get('ok') or not result.get('frozen'):
        raise RuntimeError('Executable smoke check failed: '+json.dumps(result))
    archive = dist/'CPlusCleaner-windows-x64.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(executable, executable.name)
        bundle.write(ROOT/'LICENSE', 'LICENSE')
        bundle.writestr('README.txt',
            'CPlus Cleaner - Windows x64\n\nRun CPlusCleaner.exe; Python is not required.\n'
            'Project and source: https://github.com/jackqiuai/cplus-cleaner\n'
            'Feedback: https://github.com/jackqiuai/cplus-cleaner/issues\n'
            'Original code and documentation: MIT License.\n'
            'Third-party artwork and rule data retain their applicable rights.\n'
            'Their provenance records are included in the executable resources and source repository.\n')
    (dist/'SHA256SUMS.txt').write_text(''.join(
        f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n'
        for path in (executable, archive)), encoding='utf-8')
    print(json.dumps(result, indent=2))
    print('Verified Windows x64 EXE, portable ZIP and SHA256SUMS.txt are in dist/.')


if __name__ == '__main__':
    main()

# Purpose: Windows executable building, isolated smoke checks and distribution archives
