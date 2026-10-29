"""Display-only localization. Never rewrite paths, rule IDs or stored values."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import re
from functools import lru_cache
import weakref
from cplus_cleaner.common.runtime import resource_root

_language = 'zh'
_widgets = weakref.WeakSet()
LANGUAGES = {'zh': '简体中文', 'zh-TW': '繁體中文', 'en': 'English'}
_catalogs = {}
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
            if language_id & 0x3ff == 0x04:
                return 'zh-TW' if language_id in (0x0404, 0x0c04, 0x1404, 0x7c04) else 'zh'
            return 'en'
        except (AttributeError, OSError):
            pass
    return 'en'


def language():
    return _language


def register(widget):
    _widgets.add(widget)


def set_language(value):
    global _language
    selected = value if value in LANGUAGES else detect_language()
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
    if _language not in _catalogs:
        root = resource_root() / 'locales'
        english = json.loads((root / 'en.json').read_text(encoding='utf-8'))
        data = english
        if _language != 'en':
            # A missing translation falls back to English per complete message
            # or template. Runtime data is never passed through a script converter.
            try:
                translated = json.loads((root / f'{_language}.json').read_text(encoding='utf-8'))
                messages = dict(english['messages'], **translated.get('messages', {}))
                entries = {tuple(entry['parts']): entry for entry in english['templates']}
                entries.update({tuple(entry['parts']): entry for entry in translated.get('templates', [])})
                data = dict(messages=messages, templates=list(entries.values()))
            except (OSError, ValueError, TypeError, KeyError):
                data = english
        templates, numeric_segments = [], []
        for entry in data['templates']:
            translation = entry.get('translation', entry.get('english'))
            fields, pieces, literals = 0, [], []
            for part in entry['parts']:
                if part is None:
                    pieces.append(f'(?P<p{fields}>.*?)')
                    fields += 1
                else:
                    pieces.append(re.escape(part))
                    if part.strip():
                        literals.append(part)
            templates.append((sum(map(len, literals)), re.compile('^' + ''.join(pieces) + '$', re.S), translation, literals))
            if fields:
                # Scan notes concatenate complete sentences and numeric suffixes.
                # Only numeric fields may match within a larger message, so an
                # arbitrary name, filename or Windows error cannot be swallowed.
                numeric = ''.join(
                    re.escape(part) if part is not None else r'(\d[\d,]*(?:\.\d+)?)'
                    for part in entry['parts'])
                if entry['parts'][0] is None:
                    numeric = r'(?<![\w.])' + numeric
                numeric_segments.append((sum(map(len, literals)), re.compile(numeric), translation))
        templates.sort(key=lambda item: -item[0])
        numeric_segments.sort(key=lambda item: -item[0])
        messages = data['messages']
        # Longer complete phrases take precedence over small units in composites.
        keys = sorted((k for k in messages if HAN.search(k) and '\n' not in k), key=len, reverse=True)
        fragments = re.compile('|'.join(re.escape(k) for k in keys))
        _catalogs[_language] = messages, templates, fragments, numeric_segments
    return _catalogs[_language]


def tr(value):
    if isinstance(value, OriginalText) or not isinstance(value, str) or _language == 'zh' or not HAN.search(value):
        return value
    if isinstance(value, DisplayName):
        return _load()[0].get(value, value) if _language == 'en' else value
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
        if _language == 'zh-TW':
            return drive[1].upper() + '盤' + ('空間' if drive[2] else '')
        return 'Drive ' + drive[1].upper() + (' space' if drive[2] else '')
    count = re.fullmatch(r'(\d[\d,]*)(条|项|款|次)', value)
    if count:
        if _language == 'zh-TW':
            return count[1] + {'条': '條', '项': '項', '款': '款', '次': '次'}[count[2]]
        singular, plural = {'条': ('entry', 'entries'), '项': ('item', 'items'),
                            '款': ('app', 'apps'), '次': ('time', 'times')}[count[2]]
        return count[1] + ' ' + (singular if int(count[1].replace(',', '')) == 1 else plural)
    for _, pattern, translation, literals in templates:
        if not all(part in value for part in literals):
            continue
        match = pattern.fullmatch(value)
        if match:
            # Exact category/enum aliases are safe; arbitrary captured paths and
            # Windows-provided error text are retained as data.
            fields = {key: _translate(item) if HAN.search(item) and len(item) < len(value) else item
                      for key, item in match.groupdict().items()}
            return re.sub(r'\{(\d+)\}', lambda m: fields['p' + m[1]], translation)
    if '\n' in value:
        return '\n'.join(_translate(line) if line else '' for line in value.split('\n'))

    def translate_segment(segment):
        for _, pattern, translation in numeric_segments:
            def render(match):
                raw = match[0]
                if re.fullmatch(r'\d[\d,]*(条|项|款|次)', raw):
                    return _translate(raw)
                translated = re.sub(r'\{(\d+)\}', lambda field: match[int(field[1])+1], translation)
                leading = raw[:len(raw)-len(raw.lstrip())]
                trailing = raw[len(raw.rstrip()):]
                if translated.lstrip().startswith((';', ',', ':')):
                    leading = ''
                return leading + translated.strip() + trailing
            segment = pattern.sub(render, segment)

        def render_static(match):
            translated = messages[match[0]]
            # Chinese sentences need no separating space; English sentences do.
            if (_language == 'en' and match[0].endswith(('。', '！', '？')) and translated.endswith(('.', '!', '?'))
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

# Purpose: Language selection, catalogs and dynamic text
