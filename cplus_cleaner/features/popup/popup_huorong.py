"""Independent adapter for the retained, versioned Huorong popup definitions.

Every matching field is validated. Unknown/malformed conditions disable the
entire rule, never turn into a wildcard. No vendor executable is used at runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re

from cplus_cleaner.common import core
from cplus_cleaner.features.popup import popup_inventory as inventory
from cplus_cleaner.features.popup.popup_models import Rule, Software

CATALOG = core.APP_DIR / 'assets' / 'popup-huorong.json'
SOURCE = 'huorong'
SCAN_FIELDS = frozenset(('procname', 'procname_rx', 'class', 'class_rx', 'caption', 'caption_rx',
                         'styles', 'exstyles', 'size', 'varsize', 'include_size', 'exclude_size',
                         'pos', 'image', 'comment'))
ACTION_LABELS = {'close': '关闭', 'hide': '隐藏', 'kill': '结束进程', 'close_hide': '关闭并隐藏'}


class Unsupported(ValueError):
    pass


def _id(value):
    return SOURCE + ':' + hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]


@lru_cache(maxsize=2)
def _load(path, modified):
    data = json.loads(Path(path).read_text('utf-8-sig'))
    if data.get('schema_version') != 1 or not isinstance(data.get('groups'), list):
        raise ValueError('火绒规则库格式无效')
    return data


def asset():
    return _load(str(CATALOG), CATALOG.stat().st_mtime_ns)


def _variables(root, environ=None):
    env = {k.upper(): v for k, v in (os.environ if environ is None else environ).items()}
    aliases = {'CSIDL_APPDATA': 'APPDATA', 'CSIDL_LOCAL_APPDATA': 'LOCALAPPDATA',
               'CSIDL_WINDOWS': 'SYSTEMROOT', 'CSIDL_COMMON_APPDATA': 'PROGRAMDATA',
               'CSIDL_PROGRAM_FILES': 'PROGRAMFILES', 'CSIDL_PROGRAM_FILESX86': 'PROGRAMFILES(X86)'}
    values = dict(env)
    for token, name in aliases.items():
        if name in env:
            values[token] = env[name]
    if 'PROGRAMW6432' in env:
        values['CSIDL_PROGRAM_FILES'] = env['PROGRAMW6432']
    if root:
        values['INSTDIR'] = root.rstrip('\\/')
    return values


def expand(text, root='', *, regex=False, environ=None):
    if not isinstance(text, str):
        raise Unsupported('路径/匹配值不是字符串')
    variables = _variables(root, environ)
    def substitute(match):
        value = variables.get(match[1].upper())
        if not value or not ntpath.isabs(value):
            raise Unsupported('无法解析路径变量 ' + match[0])
        return re.escape(value.rstrip('\\/')) if regex else value.rstrip('\\/')
    return re.sub(r'%([^%]+)%', substitute, text)


def _integer(value, name, low=0, high=0xffffffff):
    if type(value) is not int or not low <= value <= high:
        raise Unsupported(name + ' 数值无效')
    return value


def _size(value, *, variable=False, allow_range=False):
    if not isinstance(value, str) or not re.fullmatch(r'\s*-?\d+(?:\s*,\s*-?\d+){1,3}\s*', value):
        raise Unsupported('尺寸条件格式无效')
    result = tuple(int(s.strip()) for s in value.split(','))
    if len(result) not in ((4,) if variable else ((2, 4) if allow_range else (2,))):
        raise Unsupported('尺寸条件维数无效')
    if any(v < 0 or v > 65535 for v in result):
        raise Unsupported('尺寸条件超出范围')
    if len(result) == 4 and (result[0] > result[2] or result[1] > result[3]):
        raise Unsupported('尺寸范围上下界颠倒')
    return result


def _regex(pattern):
    if len(pattern) > 2048 or '\x00' in pattern:
        raise Unsupported('正则过长或包含空字符')
    # Python-only syntax and engine-dependent constructs are not imported.
    if (re.search(r'\(\?(?![:=!])', pattern) or re.search(r'(?<!\\)\\[1-9]', pattern)
            or re.search(r'\([^)]*[*+][^)]*\)[*+{]', pattern)):
        raise Unsupported('正则含尚未适配的扩展或反向引用')
    try:
        # ECMAScript dot excludes all four line terminators; Python's default
        # dot would otherwise also accept CR and the Unicode separators.
        escaped, in_class, translated = False, False, []
        for character in pattern:
            if escaped:
                if character.isalpha() and character not in 'bBdDsSwWfnrtvcxu':
                    raise Unsupported('正则转义语义尚未适配：\\' + character)
                translated.append(character); escaped = False
            elif character == '\\':
                translated.append(character); escaped = True
            elif character == '[':
                in_class = True; translated.append(character)
            elif character == ']':
                in_class = False; translated.append(character)
            elif character == '.' and not in_class:
                translated.append('[^\\r\\n\\u2028\\u2029]')
            else:
                translated.append(character)
        return re.compile(''.join(translated), re.ASCII)
    except re.error as error:
        raise Unsupported('正则无法编译：' + str(error)) from error


@dataclass(frozen=True)
class Matcher:
    contexts: tuple
    numbers: dict
    sizes: dict
    points: tuple

    def match(self, window, *, check_pixels=True):
        for key, expected in self.numbers.items():
            actual = window.get(key)
            if type(actual) is not int:
                return False
            if key == 'position':
                if expected != -1 and actual != expected:
                    return False
            elif actual != expected and not (actual & expected):
                return False
        width, height = window.get('logical_width'), window.get('logical_height')
        if self.sizes:
            if type(width) is not int or type(height) is not int:
                return False
            def hit(size):
                return ((width, height) == size if len(size) == 2 else
                        size[0] <= width <= size[2] and size[1] <= height <= size[3])
            if any(hit(s) for s in self.sizes.get('exclude_size', ())):
                return False
            if self.sizes.get('include_size') and not any(hit(s) for s in self.sizes['include_size']):
                return False
            for key in ('size', 'varsize'):
                if key in self.sizes and not hit(self.sizes[key]):
                    return False
        def context_matches(context):
            for field, expected, regular in context:
                actual = window.get(field)
                if not isinstance(actual, str) or len(actual) > 4096:
                    return False
                if regular:
                    if any(ord(character) > 0xffff for character in actual):
                        return False
                    if expected.fullmatch(actual) is None:
                        return False
                elif ((ntpath.normpath(actual).lower() if field == 'image' else actual.lower())
                      != expected.lower()):
                    return False
            return True
        if not any(context_matches(c) for c in self.contexts):
            return False
        if not check_pixels:
            return True
        pixels = window.get('pixels', {})
        for x, y, color, kind in self.points:
            actual = pixels.get((x, y))
            if type(actual) is not int or (actual == color) != (kind == 0):
                return False
        return True


def compile_scan(scan, roots=('',), *, environ=None):
    if not isinstance(scan, dict) or not scan:
        raise Unsupported('缺少窗口匹配条件')
    unknown = set(scan) - SCAN_FIELDS
    if unknown:
        raise Unsupported('未知匹配条件：' + ', '.join(sorted(unknown)))
    numbers, sizes = {'styles': 0, 'exstyles': 0}, {}
    for key in ('styles', 'exstyles'):
        if key in scan:
            numbers[key] = _integer(scan[key], key, -0x80000000) & 0xffffffff
    if 'pos' in scan:
        numbers['position'] = _integer(scan['pos'], 'pos', -1, 5)
    for key in ('size', 'varsize'):
        if key in scan:
            sizes[key] = _size(scan[key], variable=key == 'varsize')
    for key in ('include_size', 'exclude_size'):
        if key in scan:
            if not isinstance(scan[key], list):
                raise Unsupported(key + ' 不是列表')
            sizes[key] = tuple(_size(v, allow_range=True) for v in scan[key])
    image = scan.get('image', {})
    if not isinstance(image, dict) or set(image) - {'points'}:
        raise Unsupported('未知图像匹配条件')
    points = image.get('points', [])
    if not isinstance(points, list) or len(points) > 256:
        raise Unsupported('图像点条件无效')
    parsed = []
    for point in points:
        if not isinstance(point, dict) or set(point) != {'x', 'y', 'color', 'type'}:
            raise Unsupported('图像点字段缺失或未知')
        parsed.append((_integer(point['x'], 'x', 0, 16384), _integer(point['y'], 'y', 0, 16384),
                       _integer(point['color'], 'color'), _integer(point['type'], 'type', 0, 1)))
    contexts = []
    for root in roots:
        context = []
        for key, field in (('procname', 'image'), ('class', 'class'), ('caption', 'title')):
            for regular in (False, True):
                name = key + ('_rx' if regular else '')
                if name not in scan:
                    continue
                value = expand(scan[name], root, regex=regular and key == 'procname', environ=environ)
                if not regular and field == 'image' and not ntpath.isabs(value):
                    raise Unsupported('进程路径不是绝对路径')
                if not regular and field == 'image':
                    value = ntpath.normpath(value)
                context.append((field, _regex(value) if regular else value, regular))
        contexts.append(tuple(context))
    if not contexts:
        raise Unsupported('无法确认软件目录')
    return Matcher(tuple(contexts), numbers, sizes, tuple(parsed))


def bind_rule(rule, roots, *, environ=None, reason=''):
    try:
        if reason:
            raise Unsupported(reason)
        if set(rule.definition) - {'name', 'scan', 'display_name', 'treat'}:
            raise Unsupported('未知窗口规则字段')
        if rule.raw_treat not in ('close', 'hide', 'kill', ''):
            raise Unsupported('未知处理动作：' + rule.raw_treat)
        matcher = compile_scan(rule.definition.get('scan'), roots, environ=environ)
        literals = [value for context in matcher.contexts for name, value, regex in context
                    if name == 'image' and not regex]
        def admitted(image):
            for root in roots:
                try:
                    if ntpath.commonpath((ntpath.normcase(image), ntpath.normcase(root))) == ntpath.normcase(root):
                        return True
                except ValueError:
                    pass
            return False
        if literals and not any(admitted(image) for image in literals):
            raise Unsupported('规则进程位于已确认的软件目录之外')
        return replace(rule, supported=True, reason='', matcher=matcher, default_enabled=rule.raw_treat == 'close')
    except (Unsupported, TypeError, ValueError) as error:
        return replace(rule, supported=False, reason=str(error), matcher=None, default_enabled=False)


def catalog():
    data = asset()
    result = []
    # Template validation here is separate from per-installation binding.
    template_env = {key: 'C:\\__validation__' for key in (
        'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA', 'SYSTEMROOT', 'PROGRAMFILES', 'PROGRAMFILES(X86)')}
    for group in data['groups']:
        key = _id('group:' + group['name'])
        occurrences, rules = {}, []
        for window in group['windows']:
            occurrence = occurrences.get(window['name'], 0)
            occurrences[window['name']] = occurrence + 1
            identity = _id(group['name'] + ':' + window['name'] + ':' + str(occurrence) + ':' +
                           json.dumps(window, sort_keys=True, ensure_ascii=False))
            raw = window.get('treat', '')
            action = {'close': 'close', 'hide': 'hide', 'kill': 'kill', '': 'close_hide'}.get(raw, '')
            scan = window['scan']
            rule = Rule(identity, scan.get('caption', ''), scan.get('class', ''), scan.get('procname', ''),
                        0, 0, scan.get('pos', -1), raw == 'close', SOURCE, action, raw,
                        description=data['labels'].get(window.get('display_name'), window['name']),
                        definition=window)
            rules.append(bind_rule(rule, ('C:\\__validation__',), environ=template_env))
        result.append(Software(key, data['labels'].get(group['display_name'], group['name']), (),
                               tuple(rules), source=SOURCE, definition=group))
    return result


def _name_matches(pattern, value):
    return inventory.name_matches(pattern, value)


def _keyed_version_records(pattern, directory, records, roots_reader):
    """Project compatibility: a literal app name plus version, anchored by key.

    Not a claimed native substring matcher. The unmodified Huorong dnplayer9
    definition says '雷电模拟器' but the registered display name is '雷电模拟器9'.
    Never use this fallback without the same rule's exact installation key,
    a matching version suffix and an admitted directory.
    """
    key = directory.get('appkey', '')
    if (not isinstance(pattern, str) or not isinstance(key, str) or not key
            or any(c in pattern+key for c in '*?>')):
        return []
    major = re.search(r'(\d+)$', key)
    if not major:
        return []
    expression = re.escape(pattern) + r'\s*(?:v\s*)?' + re.escape(major[1]) + r'(?:\.\d+)*'
    return [record for record in records if _name_matches(key, inventory.key_name(record))
            and re.fullmatch(expression, str(record.get('DisplayName', '')), re.I)
            and roots_reader(record)]


def read_regval(definition):
    """Read only explicit HKCU/HKLM string values in the requested registry view."""
    if os.name != 'nt':
        return ()
    import winreg
    if not isinstance(definition, dict) or set(definition) - {'regkey', 'regkey64', 'valuename'}:
        raise Unsupported('安装目录注册表条件无效')
    result = []
    for name, view in (('regkey64', winreg.KEY_WOW64_64KEY), ('regkey', winreg.KEY_WOW64_32KEY)):
        value = definition.get(name)
        if not value:
            continue
        hive, separator, subkey = value.partition('\\')
        if not separator or hive not in ('HKEY_LOCAL_MACHINE', 'HKEY_CURRENT_USER'):
            raise Unsupported('不支持的注册表位置')
        try:
            with winreg.OpenKey(getattr(winreg, hive), subkey, 0, winreg.KEY_READ | view) as key:
                value, kind = winreg.QueryValueEx(key, definition['valuename'])
            if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) or not isinstance(value, str):
                raise Unsupported('安装目录注册表值不是字符串')
            result.append(os.path.expandvars(value).strip('" '))
        except FileNotFoundError:
            pass
    return tuple(result)


def discover(definition, records, roots_reader, *, regval_reader=read_regval, environ=None):
    """Returns None if absent, otherwise a possibly disabled visible rule group."""
    group = definition.definition
    condition = group.get('exist', {})
    unknown = set(group) - {'name', 'display_name', 'display_icon', 'exist', 'instdir', 'windows'}
    reason = ('未知应用规则字段：' + ', '.join(sorted(unknown))) if unknown else ''
    if not isinstance(condition, dict) or not condition:
        return None
    matched = list(records)
    directory = group.get('instdir', {})
    if not isinstance(directory, dict):
        return disable_group(definition, '安装目录条件不是对象')
    if 'appkey' in directory:
        key_choices = [r for r in records if _name_matches(directory['appkey'], inventory.key_name(r))]
        # A name wildcard can also match another installed major version.
        # Do not present absent-version definitions as an unusable duplicate.
        if not key_choices:
            return None
    for key, value in condition.items():
        if key == 'appname':
            candidates = matched
            matched = [r for r in candidates if _name_matches(value, r.get('DisplayName', ''))]
            if not matched:
                matched = _keyed_version_records(value, directory, candidates, roots_reader)
                if not matched:
                    return None
        elif key == 'appkey':
            matched = [r for r in matched if _name_matches(value, inventory.key_name(r))]
            if not matched:
                return None
        elif key not in ('file_exist', 'alway'):
            reason = '暂未适配应用存在条件：' + key
    roots = set()
    try:
        if len(directory) > 1 or set(directory) - {'appname', 'appkey', 'regval', 'file_path'}:
            raise Unsupported('未知安装目录条件')
        if 'regval' in directory:
            candidates = regval_reader(directory['regval'])
        elif 'file_path' in directory:
            candidates = (expand(directory['file_path'], environ=environ),)
        elif 'appname' in directory or 'appkey' in directory:
            name_key = 'appname' if 'appname' in directory else 'appkey'
            choices = [r for r in records if _name_matches(directory[name_key],
                       r.get('DisplayName', '') if name_key == 'appname' else inventory.key_name(r))]
            candidates = tuple(p for r in choices for p in roots_reader(r))
        else:
            candidates = ()
        for candidate in candidates:
            # Reuse the project's admission checks, including system/shared roots.
            roots.update(roots_reader({'InstallLocation': candidate}))
        if 'file_exist' in condition:
            paths = [expand(condition['file_exist'], root, environ=environ) for root in (tuple(roots) or ('',))]
            if not any(Path(path).exists() for path in paths):
                return None
        elif not any(k in condition for k in ('appname', 'appkey', 'alway')):
            if not roots:
                return None
        if not roots:
            reason = reason or '无法确认软件目录'
    except (OSError, Unsupported, TypeError, KeyError) as error:
        if not roots and not any(k in condition for k in ('appname', 'appkey')):
            return None
        reason = reason or str(error)
    bound = tuple(bind_rule(rule, tuple(sorted(roots)), environ=environ, reason=reason) for rule in definition.rules)
    return replace(definition, roots=tuple(sorted(roots)), rules=bound)


def protected(window):
    data = asset()
    names = set(data.get('sysclass', ())) | set(data.get('whitelist', {}).get('whitelist', ()))
    return window.get('class', '').lower() in {name.lower() for name in names}


def disable_group(group, reason):
    return replace(group, rules=tuple(replace(rule, supported=False, default_enabled=False,
                                              matcher=None, reason=reason) for rule in group.rules))


def matches(rule, window, *, check_pixels=True):
    return bool(rule.supported and rule.matcher is not None and not protected(window)
                and rule.matcher.match(window, check_pixels=check_pixels))
