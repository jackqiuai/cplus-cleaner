"""Build and verify the Windows x64 EXE, portable ZIP and SHA-256 checksums."""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


# Read public signature and version metadata only. The file path is passed in the
# environment, never interpolated as PowerShell source. No trust stores are changed.
SIGNATURE_QUERY = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
# A PowerShell 7 parent can expose incompatible modules to Windows PowerShell 5.1.
# Load this Windows PowerShell installation's own built-in modules explicitly.
foreach ($module in @('Security', 'Management', 'Utility')) {
    Import-Module "$PSHOME\Modules\Microsoft.PowerShell.$module\Microsoft.PowerShell.$module.psd1"
}
$signature = Get-AuthenticodeSignature -LiteralPath $env:CPLUS_SIGNATURE_FILE
$version = (Get-Item -LiteralPath $env:CPLUS_SIGNATURE_FILE).VersionInfo
$publisher = if ($signature.SignerCertificate) {
    $signature.SignerCertificate.GetNameInfo([System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false)
} else { '' }
[pscustomobject]@{
    status = [string]$signature.Status
    signature_type = [string]$signature.SignatureType
    publisher = $publisher
    timestamped = [bool]$signature.TimeStamperCertificate
    product_name = $version.ProductName
    product_version = $version.ProductVersion
    file_version = $version.FileVersion
    original_filename = $version.OriginalFilename
} | ConvertTo-Json -Compress
'''


def application_version(root=ROOT):
    tree = ast.parse((Path(root)/'cplus_cleaner/application/preferences.py').read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == 'VERSION' for target in node.targets):
            value = ast.literal_eval(node.value)
            parts = value.split('.') if isinstance(value, str) else []
            if len(parts) in (3, 4) and all(part.isascii() and part.isdecimal()
                                          and int(part) <= 65535 for part in parts):
                return value
            raise ValueError('VERSION must contain three or four Windows version numbers.')
    raise ValueError('Application VERSION is missing.')


def validate_executable(executable):
    data = executable.read_bytes()
    try:
        offset = struct.unpack_from('<I', data, 0x3c)[0]
        if data[:2] != b'MZ' or data[offset:offset+4] != b'PE\0\0':
            raise ValueError('Expected a Windows PE executable.')
        if struct.unpack_from('<H', data, offset+4)[0] != 0x8664:
            raise ValueError('Expected x64 executable.')
        if struct.unpack_from('<H', data, offset+24+68)[0] != 2:
            raise ValueError('Expected a windowed executable.')
    except struct.error as error:
        raise ValueError('Truncated Windows executable.') from error


def verify_signature(executable, *, expected_publisher, root=ROOT):
    if not expected_publisher.strip():
        raise ValueError('A certificate publisher is required for signature verification.')
    environment = dict(os.environ, CPLUS_SIGNATURE_FILE=str(executable.resolve()))
    completed = subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', SIGNATURE_QUERY],
        capture_output=True, text=True, encoding='utf-8', env=environment,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=120)
    if completed.returncode:
        raise RuntimeError('Windows signature verification could not run: '+completed.stderr.strip())
    signature = json.loads(completed.stdout.lstrip('\ufeff'))
    expected = dict(status='Valid', signature_type='Authenticode',
                    publisher=expected_publisher, timestamped=True,
                    product_name='CPlus Cleaner', product_version=application_version(root),
                    file_version=application_version(root), original_filename='CPlusCleaner.exe')
    for key, value in expected.items():
        if signature.get(key) != value:
            raise ValueError(f'Signed executable verification failed: {key} must be {value!r}, '
                             f'got {signature.get(key)!r}.')
    return signature


def smoke_test(executable, *, root=ROOT, signed=False):
    report = Path(root)/('build/signed-smoke-test.json' if signed else 'build/smoke-test.json')
    report.parent.mkdir(parents=True, exist_ok=True)
    report.unlink(missing_ok=True)
    completed = subprocess.run([str(executable), '--smoke-test', str(report)],
                               cwd=executable.parent, timeout=120)
    result = json.loads(report.read_text(encoding='utf-8'))
    if completed.returncode or not result.get('ok') or not result.get('frozen'):
        raise RuntimeError('Executable smoke check failed: '+json.dumps(result))
    return result


def package_distribution(executable, *, root=ROOT, require_signed=False,
                         expected_publisher='SignPath Foundation'):
    root, executable = Path(root).resolve(), Path(executable).resolve()
    validate_executable(executable)
    # Reject an invalid signing response before launching it or replacing artifacts.
    signature = (verify_signature(executable, expected_publisher=expected_publisher, root=root)
                 if require_signed else None)
    result = smoke_test(executable, root=root, signed=require_signed)
    dist = root/'dist'
    dist.mkdir(parents=True, exist_ok=True)
    destination = dist/'CPlusCleaner.exe'
    if executable != destination:
        shutil.copyfile(executable, destination)
    archive = dist/'CPlusCleaner-windows-x64.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(destination, destination.name)
        bundle.write(root/'LICENSE', 'LICENSE')
        bundle.writestr('README.txt',
            'CPlus Cleaner - Windows x64\n\nRun CPlusCleaner.exe; Python is not required.\n'
            'Project and source: https://github.com/jackqiuai/cplus-cleaner\n'
            'Feedback: https://github.com/jackqiuai/cplus-cleaner/issues\n'
            'Original code and documentation: MIT License.\n'
            'Third-party artwork and rule data retain their applicable rights.\n'
            'Their provenance records are included in the executable resources and source repository.\n')
    (dist/'SHA256SUMS.txt').write_text(''.join(
        f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n'
        for path in (destination, archive)), encoding='utf-8')
    if signature:
        report = root/'build/signature-verification.json'
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            json.dumps(signature, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
    label = 'Signed and verified' if require_signed else 'Unsigned development'
    print(f'{label} Windows x64 EXE, portable ZIP and SHA256SUMS.txt are in dist/.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package-only', action='store_true',
                        help='Verify and package an existing EXE without rebuilding it')
    parser.add_argument('--executable', type=Path, default=ROOT/'dist/CPlusCleaner.exe')
    parser.add_argument('--require-signed', action='store_true',
                        help='Reject unsigned, untrusted, untimestamped or unexpected EXEs')
    parser.add_argument('--expected-publisher', default='SignPath Foundation',
                        help='Expected certificate common name, not CompanyName metadata')
    args = parser.parse_args()
    if os.name != 'nt' or struct.calcsize('P') != 8:
        parser.error('Use 64-bit Python on Windows to build the Windows x64 executable.')
    if not args.package_only:
        if args.require_signed or args.executable != ROOT/'dist/CPlusCleaner.exe':
            parser.error('--require-signed and custom --executable require --package-only.')
        application_version()
        subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                        str(ROOT/'CPlusCleaner.spec')], cwd=ROOT, check=True)
    package_distribution(args.executable, require_signed=args.require_signed,
                         expected_publisher=args.expected_publisher)


if __name__ == '__main__':
    main()

# Purpose: Windows executable building, isolated smoke checks and distribution archives
