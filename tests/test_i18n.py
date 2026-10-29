"""Localization checks use fixture data and never invoke cleanup or Windows actions."""
import ctypes
import json
from pathlib import Path
import tempfile
import tkinter as native
from tkinter import ttk as native_ttk
import unittest
from unittest.mock import patch
from contextlib import ExitStack
from dataclasses import replace

from cplus_cleaner.common import core
from cplus_cleaner.localization import i18n
from cplus_cleaner.localization import localized_tk as tk
from cplus_cleaner.application import preferences
from cplus_cleaner.ui.ui_widgets import MetricLabel


class LanguageTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_language('zh')

    def test_windows_ui_language_and_override(self):
        for code, expected in ((0x0804, 'zh'), (0x0404, 'zh'), (0x0c04, 'zh'), (0x0409, 'en'), (0x0411, 'en')):
            with patch.object(ctypes.windll.kernel32, 'GetUserDefaultUILanguage', return_value=code):
                self.assertEqual(i18n.detect_language(), expected)
                i18n.set_language('')
                self.assertEqual(i18n.language(), expected)
                i18n.set_language('en' if expected == 'zh' else 'zh')
                self.assertNotEqual(i18n.language(), expected)

    def test_reviewed_captions_names_paths_and_dynamic_numbers(self):
        i18n.set_language('en')
        for text, english in (('开始扫描', 'Scan'), ('扫描', 'Scan'), ('一键扫描', 'Scan'),
                              ('立即清理', 'Delete'), ('立即搬家', 'Move'), ('D盘空间', 'Drive D space')):
            self.assertEqual(i18n.tr(text), english)
        self.assertEqual(i18n.tr(i18n.original('鲁大师')), '鲁大师')
        for path in (r'C:\用户\缓存文件\日志.tmp', '缓存文件.exe'):
            self.assertEqual(i18n.tr(path), path)
        self.assertEqual(i18n.tr('发现隐私 2480 条，已选 2480 条'), 'Privacy records found: 2480; selected: 2480.')
        self.assertEqual(i18n.tr('软件推荐'), 'App recommendations')
        self.assertEqual(i18n.tr('右下窗口'), 'Bottom-right window')
        self.assertEqual(i18n.tr('2480条'), '2480 entries')
        self.assertEqual(i18n.tr('1款'), '1 app')
        self.assertEqual(i18n.tr(i18n.display_name('示例软件')), '示例软件')
        for source, expected in [('大文件分区迁移', 'Move large files'),
                                 ('整理多款聊天软件空间', 'Clean Chat relative'),
                                 ('软件智能迁移', 'Move installed APP')]:
            self.assertEqual(i18n.tr(source), expected)
        self.assertEqual(i18n.tr('自动拦截软件弹窗'), 'Automatically block')
        for days in (7, 30, 180):
            self.assertEqual(i18n.tr(f'{days}天内'), f'Within {days} days')

    def test_every_dynamic_template_retains_fields(self):
        i18n.set_language('en')
        catalog = json.loads((core.APP_DIR / 'locales/en.json').read_text(encoding='utf-8'))
        self.assertGreater(len(catalog['messages']), 1000)
        for entry in catalog['templates']:
            number, parts = 0, []
            for part in entry['parts']:
                if part is None:
                    parts.append('FIELD' + str(number))
                    number += 1
                else:
                    parts.append(part)
            expected = entry['english']
            for n in range(number):
                expected = expected.replace('{' + str(n) + '}', 'FIELD' + str(n))
            with self.subTest(source=''.join(parts)):
                self.assertEqual(i18n.tr(''.join(parts)), expected)

    def test_setting_roundtrip_old_settings_and_invalid_value(self):
        with tempfile.TemporaryDirectory(dir=core.APP_DIR.parent) as directory, patch.object(core, 'DATA_DIR', Path(directory)):
            (core.DATA_DIR / 'settings.json').write_text('{"cleanup_time":"09:00"}', encoding='utf-8')
            self.assertEqual(preferences.load()['language'], '')
            for language in ('zh', 'en'):
                preferences.save(dict(preferences.DEFAULTS, language=language))
                self.assertEqual(preferences.load()['language'], language)
            before = (core.DATA_DIR / 'settings.json').read_bytes()
            with self.assertRaises(ValueError):
                preferences.save(dict(preferences.DEFAULTS, language='fr'))
            self.assertEqual((core.DATA_DIR / 'settings.json').read_bytes(), before)

    def test_compression_disk_usage_and_legacy_history_are_fully_localized(self):
        i18n.set_language('en')
        self.assertEqual(i18n.tr('容量按实际磁盘占用统计'),'Sizes show actual disk usage.')
        for text in ('压缩前占用 13.2GB  ·  压缩后占用 9.1GB  ·  本次节省 4.1GB  ·  当前占用 12.29GB',
                     '旧记录：未记录操作前的实际占用  ·  当前占用 13.2GB',
                     '当前占用读取失败'):
            translated=i18n.tr(text)
            self.assertNotRegex(translated,i18n.HAN)
            if '4.1GB' in text:self.assertIn('saved this time: 4.1GB',translated)

    def test_compression_activity_keeps_file_names_and_translates_elapsed_time(self):
        i18n.set_language('en')
        path=r'D:\软件\data.vmdk'
        text=f'压缩：Example（24%） · 已用时 8 秒 · 当前文件：{path}'
        translated=i18n.tr(text)
        self.assertIn(path,translated)
        self.assertIn('Elapsed: 8s',translated)
        self.assertIn('Current file:',translated)
        self.assertNotRegex(translated.replace(path,''),i18n.HAN)

    def test_concatenated_scan_summaries_translate_numeric_suffixes(self):
        i18n.set_language('en')
        self.assertEqual(i18n.tr('选择其他位置'), 'Choose folder')
        for cancelled in (False, True):
            for services, skipped in ((0, 0), (7, 1), (12, 34)):
                mover = ('扫描已取消，显示已完成部分。' if cancelled else '扫描完成。') + \
                    '请先退出待搬家的软件；复制校验后保留原路径入口，并清除已校验的旧副本。' + \
                    (f' 已排除 {services} 个含系统服务的软件。' if services else '') + \
                    (f' {skipped} 个目录无法完整读取或含链接，已跳过。' if skipped else '')
                english = i18n.tr(mover)
                self.assertNotRegex(english, i18n.HAN)
                if services:
                    self.assertIn(f'Applications containing system services excluded: {services}.', english)
                if skipped:
                    self.assertIn(f'links: {skipped}.', english)
                self.assertIn('. Close the applications', english)
                for problems in (0, 1, 17):
                    privacy = ('扫描已取消 · 已读取部分记录' if cancelled else '全部时间 · 扫描完成') + \
                        (f' · {problems}项资料无法完整读取，已跳过' if problems else ' · 按记录条数统计')
                    english = i18n.tr(privacy)
                    self.assertNotRegex(english, i18n.HAN)
                    if problems:
                        self.assertIn(f'; incomplete data sources skipped: {problems}', english)
                    i18n.set_language('zh')
                    self.assertEqual(i18n.tr(privacy), privacy)
                    i18n.set_language('en')
                i18n.set_language('zh')
                self.assertEqual(i18n.tr(mover), mover)
                i18n.set_language('en')
        path = r'C:\用户\已排除 7 个含系统服务的软件。\缓存文件.tmp'
        self.assertEqual(i18n.tr(path), path)
        self.assertIn(path, i18n.tr('扫描完成。' + path))


class PresentationTests(unittest.TestCase):
    def setUp(self):
        i18n.set_language('zh')
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()
        i18n.set_language('zh')

    def test_live_switch_and_filter_selection_keep_raw_model_values(self):
        variable = tk.StringVar(self.root, value='发现隐私 2480 条，已选 2480 条')
        label = tk.Label(self.root, textvariable=variable)
        choice = tk.StringVar(self.root, value='全部')
        combo = tk.ttk.Combobox(self.root, values=('全部', '成功', '失败'), textvariable=choice)
        preserved = tk.Label(self.root, text=i18n.original('鲁大师'))
        canvas = tk.Canvas(self.root)
        item = canvas.create_text(0, 0, text='立即搬家')
        self.root.title('CPlus Cleaner · C盘清理助手')
        i18n.set_language('en')
        self.assertEqual(variable.get(), '发现隐私 2480 条，已选 2480 条')
        displayed = self.root.getvar(label.cget('textvariable'))
        self.assertIn('2480', displayed)
        self.assertNotRegex(displayed, i18n.HAN)
        self.assertEqual(preserved.cget('text'), '鲁大师')
        self.assertEqual(canvas.itemcget(item, 'text'), 'Move')
        self.assertIn('Drive Cleanup Assistant', self.root.title())
        combo.set('Success')
        self.assertEqual(choice.get(), '成功')
        i18n.set_language('zh')
        self.assertEqual(combo.get(), '成功')
        self.assertEqual(canvas.itemcget(item, 'text'), '立即搬家')

    def test_tree_rendering_metrics_and_marker_choices(self):
        tree = tk.ttk.Treeview(self.root, columns=('status',))
        tree.heading('status', text='结果')
        row = tree.insert('', 'end', text='压缩包', values=('成功',))
        metric = MetricLabel(self.root, text='发现隐私 2480 条，已选 2480 条')
        source = tk.StringVar(self.root, value='中文')
        combo = tk.ttk.Combobox(self.root, textvariable=source, values=(i18n.original('中文'), i18n.original('English')))
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label='删除此文件')
        i18n.set_language('en')
        self.assertEqual(tree.item(row, 'text'), '压缩包')
        self.assertEqual(native_ttk.Treeview.item(tree, row, 'text'), 'Archives')
        self.assertEqual(native_ttk.Treeview.item(tree, row, 'values'), ('Success',))
        self.assertEqual(combo.get(), '中文')
        self.assertEqual(menu.entrycget(0, 'label'), 'Delete this file')
        numbers = metric.find_withtag('metric-number')
        self.assertEqual([metric.itemcget(n, 'text') for n in numbers], ['2480', '2480'])
        self.assertTrue(all(metric.itemcget(n, 'fill') == '#ff6269' for n in numbers))
        tree.delete(row)
        label = tk.Label(self.root, textvariable=source)
        label.destroy()
        source.set('English')
        i18n.set_language('zh')

    def test_scan_notes_update_in_both_languages_without_changing_source(self):
        mover = '扫描完成。 已排除 7 个含系统服务的软件。 1 个目录无法完整读取或含链接，已跳过。'
        privacy = '全部时间 · 扫描完成 · 3项资料无法完整读取，已跳过'
        values = [tk.StringVar(self.root, value=text) for text in (mover, privacy)]
        labels = [tk.Label(self.root, textvariable=value) for value in values]
        for language in ('en', 'zh', 'en'):
            i18n.set_language(language)
            for source, value, label in zip((mover, privacy), values, labels):
                displayed = self.root.getvar(label.cget('textvariable'))
                self.assertEqual(value.get(), source)
                if language == 'en':
                    self.assertNotRegex(displayed, i18n.HAN)
                else:
                    self.assertEqual(displayed, source)


class DesktopLanguageTests(unittest.TestCase):
    def test_settings_switch_existing_pages_and_preserve_selection_and_popup_names(self):
        from cplus_cleaner.ui import desktop
        from tests.popup_fixtures import groups
        with tempfile.TemporaryDirectory(dir=core.APP_DIR.parent) as directory, ExitStack() as stack:
            base = Path(directory)
            stack.enter_context(patch.object(core, 'DATA_DIR', base / 'state'))
            stack.enter_context(patch.object(preferences, 'read_autostart', return_value=False))
            stack.enter_context(patch.object(i18n, 'detect_language', return_value='en'))
            stack.enter_context(patch.object(desktop, 'disk_snapshot', return_value=dict(root='C:\\', total=100, used=70, free=30)))
            app = desktop.DesktopApplication()
            app.withdraw()
            try:
                app.after_cancel(app._popup_resume)
                app._popup_resume = None
                errors = []
                app.report_callback_exception = lambda kind, error, trace: errors.append(str(error))
                for key in ('chat', 'large', 'migrate', 'compress', 'duplicates', 'privacy', 'uninstall', 'popup'):
                    app.ensure_view(key)
                migration = app.views['migrate']
                migration.selected.add('fixture-selection')
                app.cdrive_page.days.set('30')
                item = groups(base)[0]
                item = replace(item, name='鲁大师', rules=(replace(item.rules[0], description='软件推荐'),))
                popup = app.views['popup']
                popup.items = [item]
                popup.render()
                row = popup.rows[item.key]
                self.assertEqual(row.name._source_options['text'], '鲁大师')
                self.assertEqual(i18n.tr(row.name._source_options['text']), '鲁大师')
                window = app.open_settings()
                window.withdraw()
                settings = window.panel
                self.assertEqual(settings.language.get(), 'English')
                for choice, code in (('中文', 'zh'), ('English', 'en')):
                    settings.language.set(choice)
                    settings.save()
                    app.update_idletasks()
                    self.assertEqual(app.settings['language'], code)
                    self.assertEqual(preferences.load()['language'], code)
                    self.assertEqual(migration.selected, {'fixture-selection'})
                    self.assertIs(app.views['migrate'], migration)
                    self.assertEqual(app.cdrive_page.days.get(), '30')
                    self.assertEqual(i18n.tr(row.name._source_options['text']), '鲁大师')
                self.assertEqual(app.nav_widgets['popup'][2].cget('text'), 'Popup Blocker')
                self.assertEqual(errors, [])
            finally:
                for timer in app.tk.call('after', 'info'):
                    app.tk.call('after', 'cancel', timer)
                app.destroy()
                i18n.set_language('zh')


if __name__ == '__main__':
    unittest.main()

# Purpose: Regression tests for language selection, catalogs and dynamic text
