"""Display-only localization. Never rewrite paths, rule IDs or stored values."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import re
from functools import lru_cache
import weakref

_language = 'zh'
_widgets = weakref.WeakSet()
_catalog = None
HAN = re.compile(r'[\u3400-\u9fff]')
PATH = re.compile(r'(?:[A-Za-z]:[\\/]|\\\\|%[A-Za-z_]+%[\\/])[^\n，；]*')


class OriginalText(str):
    """An actual name/path that must retain its original spelling."""


class DisplayName(str):
    """Translate a known complete product name, never fragments of an identity."""


def display_name(text):
    return DisplayName(text)


def original(text):
    return OriginalText(text)


def detect_language():
    """Use the user's Windows UI language, not region or input locale."""
    if os.name == 'nt':
        try:
            language_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            return 'zh' if language_id & 0x3ff == 0x04 else 'en'
        except (AttributeError, OSError):
            pass
    return 'en'


def language():
    return _language


def register(widget):
    _widgets.add(widget)


def set_language(value):
    global _language
    selected = value if value in ('zh', 'en') else detect_language()
    if selected == _language:
        return
    _language = selected
    _translate.cache_clear()
    for widget in list(_widgets):
        try:
            if widget.winfo_exists():
                widget._language_changed()
        except Exception as exc:
            # Destroyed widgets can still be awaiting Python garbage collection.
            import tkinter
            if not isinstance(exc, tkinter.TclError):
                raise


def _load():
    global _catalog
    if _catalog is None:
        data = json.loads((Path(__file__).resolve().parents[2] / 'locales/en.json').read_text(encoding='utf-8'))
        templates, numeric_segments = [], []
        for entry in data['templates']:
            fields, pieces, literals = 0, [], []
            for part in entry['parts']:
                if part is None:
                    pieces.append(f'(?P<p{fields}>.*?)')
                    fields += 1
                else:
                    pieces.append(re.escape(part))
                    if part.strip():
                        literals.append(part)
            templates.append((sum(map(len, literals)), re.compile('^' + ''.join(pieces) + '$', re.S), entry['english'], literals))
            if fields:
                # Scan notes concatenate complete sentences and numeric suffixes.
                # Only numeric fields may match within a larger message, so an
                # arbitrary name, filename or Windows error cannot be swallowed.
                numeric = ''.join(
                    re.escape(part) if part is not None else r'(\d[\d,]*(?:\.\d+)?)'
                    for part in entry['parts'])
                if entry['parts'][0] is None:
                    numeric = r'(?<![\w.])' + numeric
                numeric_segments.append((sum(map(len, literals)), re.compile(numeric), entry['english']))
        templates.sort(key=lambda item: -item[0])
        numeric_segments.sort(key=lambda item: -item[0])
        messages = data['messages']
        # Longer complete phrases take precedence over small units in composites.
        keys = sorted((k for k in messages if HAN.search(k) and '\n' not in k), key=len, reverse=True)
        fragments = re.compile('|'.join(re.escape(k) for k in keys))
        _catalog = messages, templates, fragments, numeric_segments
    return _catalog


def tr(value):
    if isinstance(value, OriginalText) or not isinstance(value, str) or _language == 'zh' or not HAN.search(value):
        return value
    if isinstance(value, DisplayName):
        return _load()[0].get(value, value)
    return _translate(value)


@lru_cache(maxsize=8192)
def _translate(value):
    messages, templates, fragments, numeric_segments = _load()
    if value in messages:
        return messages[value]
    # Paths and filenames are data, including those containing known UI words.
    if PATH.fullmatch(value) or re.fullmatch(r'[^\n\\/]+\.(?:exe|dll|lnk|log|tmp|zip|7z|pdf|docx?|xlsx?|png|jpe?g)', value, re.I):
        return value
    drive = re.fullmatch(r'([A-Za-z])盘(空间)?', value)
    if drive:
        return 'Drive ' + drive[1].upper() + (' space' if drive[2] else '')
    count = re.fullmatch(r'(\d[\d,]*)(条|项|款|次)', value)
    if count:
        singular, plural = {'条': ('entry', 'entries'), '项': ('item', 'items'),
                            '款': ('app', 'apps'), '次': ('time', 'times')}[count[2]]
        return count[1] + ' ' + (singular if int(count[1].replace(',', '')) == 1 else plural)
    for _, pattern, english, literals in templates:
        if not all(part in value for part in literals):
            continue
        match = pattern.fullmatch(value)
        if match:
            # Exact category/enum aliases are safe; arbitrary captured paths and
            # Windows-provided error text are retained as data.
            fields = {key: _translate(item) if HAN.search(item) and len(item) < len(value) else item
                      for key, item in match.groupdict().items()}
            return re.sub(r'\{(\d+)\}', lambda m: fields['p' + m[1]], english)
    if '\n' in value:
        return '\n'.join(_translate(line) if line else '' for line in value.split('\n'))

    def translate_segment(segment):
        for _, pattern, english in numeric_segments:
            def render(match):
                raw = match[0]
                if re.fullmatch(r'\d[\d,]*(条|项|款|次)', raw):
                    return _translate(raw)
                translated = re.sub(r'\{(\d+)\}', lambda field: match[int(field[1])+1], english)
                leading = raw[:len(raw)-len(raw.lstrip())]
                trailing = raw[len(raw.rstrip()):]
                if translated.lstrip().startswith((';', ',', ':')):
                    leading = ''
                return leading + translated.strip() + trailing
            segment = pattern.sub(render, segment)

        def render_static(match):
            translated = messages[match[0]]
            # Chinese sentences need no separating space; English sentences do.
            if (match[0].endswith(('。', '！', '？')) and translated.endswith(('.', '!', '?'))
                    and match.end() < len(segment) and not segment[match.end()].isspace()):
                translated += ' '
            return translated
        return fragments.sub(render_static, segment)

    result, start = [], 0
    for path in PATH.finditer(value):
        result.append(translate_segment(value[start:path.start()]))
        result.append(path[0])
        start = path.end()
    result.append(translate_segment(value[start:]))
    return ''.join(result)
