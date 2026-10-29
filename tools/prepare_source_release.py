"""Export an explicit source allowlist; optionally prune unchanged managed files."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

from verify_source_tree import verify


ROOT_FILES = (
    '.gitattributes', '.gitignore', 'start.bat', 'requirements.txt', 'README.md', 'README.zh-CN.md', 'README.zh-TW.md',
    'GITHUB-ABOUT.md', 'CONTRIBUTING.md', 'LICENSE', 'CPlusCleaner.spec', 'requirements-build.txt',
)
DIRECTORIES = ('cplus_cleaner', 'assets', 'rules', 'locales', 'tests', 'docs', '.github')
TOOLS = ('prepare_source_release.py', 'verify_source_tree.py', 'build_windows.py', 'README.md')
TEXT_SUFFIXES = {'.py', '.md', '.json', '.txt', '.yml', '.yaml', '.xml', '.svg', '.bat', '.spec'}
PROVENANCE = {'assets/asset-sources.json', 'assets/home/source-manifest.json', 'assets/privacy/sources.json'}


def selected_files(root):
    root = Path(root).resolve()
    paths = {'app.py'}
    paths.update(ROOT_FILES)
    paths.update('tools/' + name for name in TOOLS)
    for directory in DIRECTORIES:
        for path in (root / directory).rglob('*'):
            relative_parts = path.relative_to(root).parts
            excluded = {'__pycache__', '.git', 'output', 'manual', 'private'}
            if path.is_file() and not any(part in excluded for part in relative_parts):
                if path.suffix not in {'.pyc', '.pyo', '.log', '.jsonl'}:
                    paths.add(path.relative_to(root).as_posix())
    return sorted(paths)


def portable_metadata(data, source_root):
    """Keep archive/member/hash provenance while removing workstation prefixes."""
    workspace = source_root.parent.as_posix() + '/'
    source = source_root.as_posix() + '/'
    def convert(value):
        if isinstance(value, dict):
            return {key: convert(child) for key, child in value.items()}
        if isinstance(value, list):
            return [convert(child) for child in value]
        if isinstance(value, str):
            path = value.replace('\\', '/')
            if path.casefold().startswith(source.casefold()):
                return path[len(source):]
            if path.casefold().startswith(workspace.casefold()):
                return 'source-analysis/' + path[len(workspace):]
        return value
    return convert(data)


def export(source, destination, *, prune_managed=False):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or destination in source.parents:
        raise ValueError('Destination must be a separate source checkout or export directory')
    paths = selected_files(source)
    # Validate every input and output path before copying any files.
    for relative in paths:
        path = source / relative
        target = destination / relative
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(source):
            raise ValueError(f'Invalid source file: {relative}')
        if not target.resolve().is_relative_to(destination):
            raise ValueError(f'Destination escapes the export directory: {relative}')
    obsolete = []
    previous = destination/'SOURCE-MANIFEST.json'
    if prune_managed and previous.is_file():
        for entry in json.loads(previous.read_text(encoding='utf-8'))['files']:
            relative = entry['path']
            path = destination/relative
            if relative in paths or not path.exists():
                continue
            if not path.resolve().is_relative_to(destination):
                raise ValueError(f'Obsolete path escapes destination: {relative}')
            data = path.read_bytes()
            if entry['text']:
                data = data.replace(b'\r\n', b'\n')
            if hashlib.sha256(data).hexdigest() != entry['sha256']:
                raise ValueError(f'Refusing to remove a locally modified obsolete file: {relative}')
            obsolete.append(path)
    destination.mkdir(parents=True, exist_ok=True)
    for relative in paths:
        path, target = source / relative, destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if relative in PROVENANCE:
            data = portable_metadata(json.loads(path.read_text(encoding='utf-8')), source)
            target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        else:
            shutil.copyfile(path, target)
    for path in obsolete:
        path.unlink()  # Only unchanged files owned by the previous manifest.
    entries = write_manifest(destination, paths)
    problems = verify(destination)
    if problems:
        raise ValueError('\n'.join(problems))
    return entries


def write_manifest(root, paths):
    root = Path(root).resolve()
    entries = []
    for relative in paths:
        path = root / relative
        is_text = path.suffix.lower() in TEXT_SUFFIXES or path.name in {'.gitignore', '.gitattributes', 'LICENSE'}
        data = path.read_bytes()
        if is_text:
            data = data.replace(b'\r\n', b'\n')
        entries.append(dict(path=relative, bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), text=is_text))
    manifest = dict(schema_version=1, text_hashing='UTF-8 bytes with CRLF normalized to LF',
                    files=entries)
    (root / 'SOURCE-MANIFEST.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--destination', type=Path)
    mode.add_argument('--manifest-only', action='store_true', help='Refresh hashes after intentional source edits')
    parser.add_argument('--prune-managed', action='store_true', help='Remove unchanged obsolete files listed in the destination manifest')
    args = parser.parse_args()
    if args.manifest_only:
        entries = write_manifest(args.source, selected_files(args.source))
        problems = verify(args.source)
        if problems:
            parser.exit(1, '\n'.join(problems) + '\n')
    else:
        entries = export(args.source, args.destination, prune_managed=args.prune_managed)
    action = 'Refreshed' if args.manifest_only else 'Exported'
    print(f'{action} and verified {len(entries)} source/resource files and SOURCE-MANIFEST.json.')


if __name__ == '__main__':
    main()

# Purpose: Allowlisted export, integrity manifest and managed obsolete-file pruning
