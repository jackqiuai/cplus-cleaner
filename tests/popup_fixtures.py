"""Synthetic Huorong-shaped groups; no retired catalog data is retained here."""
from pathlib import Path

import popup


def definition(name, key, count=1, pattern=None):
    pattern = pattern or f'*{name}*'
    rules = []
    for index in range(count):
        scan = dict(procname=r'%INSTDIR%\Thunder.exe', caption='Fixture popup',
                    **{'class': 'FixturePopup'}, styles=0x80000000, exstyles=0, size='400,300', pos=5)
        raw = dict(name=f'{key}-{index}', scan=scan, treat='close')
        rule = popup.Rule(f'huorong-fixture:{key}:{index}', 'Fixture popup', 'FixturePopup', 'Thunder.exe',
                          400, 300, 5, True, 'huorong', 'close', 'close', definition=raw)
        rules.append(rule)
    return popup.Software(f'huorong-fixture:{key}', name, (), tuple(rules), source='huorong',
                          definition=dict(name=key, exist=dict(appname=pattern),
                                          instdir=dict(appname=pattern), display_icon=dict(appname=pattern)))


def groups(root):
    result = []
    for key, name, count in [('thunder', '迅雷', 15), ('music', '酷狗音乐', 2), ('archive', 'WinRAR', 1)]:
        item = definition(name, key, count)
        folder = str(Path(root)/key)
        result.append(popup.Software(item.key, item.name, (folder,),
                      tuple(popup.huorong.bind_rule(rule, (folder,)) for rule in item.rules),
                      source='huorong', definition=item.definition))
    return result
