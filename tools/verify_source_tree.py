"""Read-only verification of a published source tree; standard library only."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys


REQUIRED = (
    'app.py', 'core.py', 'desktop.py', 'single_instance.py', 'start.bat',
    'system_analysis_helper.py', 'assets/app-icon.png', 'assets/tray.ico',
    'assets/category-icons.json', 'assets/chat-rules.json', 'assets/popup-huorong.json',
    'rules/cdrive-private.json', 'locales/en.json',
)


def verify(root, *, require_manifest=True):
    root = Path(root).resolve()
    problems = []
    for relative in REQUIRED:
        if not (root / relative).is_file():
            problems.append(f'Missing required file: {relative}')

    modules = {path.stem for path in root.glob('*.py')}
    for path in root.glob('*.py'):
        try:
            tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
            for node in ast.walk(tree):
                names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                         else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                for name in names:
                    module = name.split('.')[0]
                    if module not in modules and module not in sys.stdlib_module_names:
                        problems.append(f'{path.name}: missing or third-party module {module}')
        except (OSError, SyntaxError, UnicodeError) as error:
            problems.append(f'{path.name}: {error}')

    for directory in ('assets', 'rules', 'locales'):
        for path in (root / directory).rglob('*.json'):
            try:
                json.loads(path.read_text(encoding='utf-8-sig'))
            except (OSError, ValueError) as error:
                problems.append(f'{path.relative_to(root)}: {error}')

    mapping = root / 'assets/category-icons.json'
    if mapping.is_file():
        try:
            def check(value):
                if isinstance(value, dict):
                    for child in value.values():
                        check(child)
                elif isinstance(value, list):
                    for child in value:
                        check(child)
                elif isinstance(value, str) and value.lower().endswith(('.png', '.ico', '.svg')):
                    if not (root / 'assets' / value).is_file():
                        problems.append(f'Missing mapped icon: {value}')
            check(json.loads(mapping.read_text(encoding='utf-8')))
        except (OSError, ValueError) as error:
            problems.append(f'Icon mapping: {error}')

    manifest = root / 'SOURCE-MANIFEST.json'
    if require_manifest and not manifest.is_file():
        problems.append('Missing SOURCE-MANIFEST.json')
    if manifest.is_file():
        try:
            value = json.loads(manifest.read_text(encoding='utf-8'))
            if not isinstance(value, dict) or value.get('schema_version') != 1 or not isinstance(value.get('files'), list):
                raise ValueError('Unsupported source manifest')
            seen = set()
            for entry in value['files']:
                relative = entry['path']
                if not isinstance(relative, str) or Path(relative).is_absolute():
                    raise ValueError(f'Invalid manifest path: {relative}')
                path = (root / relative).resolve()
                if not path.is_relative_to(root) or path == root or relative in seen:
                    raise ValueError(f'Invalid or repeated manifest path: {relative}')
                seen.add(relative)
                if not path.is_file():
                    problems.append(f'Missing manifest file: {relative}')
                    continue
                data = path.read_bytes().replace(b'\r\n', b'\n') if entry.get('text') else path.read_bytes()
                if len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
                    problems.append(f'Changed manifest file: {relative}')
            for relative in REQUIRED:
                if relative not in seen:
                    problems.append(f'Required file absent from manifest: {relative}')
        except (OSError, ValueError, KeyError, TypeError) as error:
            problems.append(f'Source manifest: {error}')
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    problems = verify(args.root)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    print('Source modules, rules, icons, language resources and manifest checks passed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
