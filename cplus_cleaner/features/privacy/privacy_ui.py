"""Privacy summary cards, using the original artwork and shared checkmarks."""
from __future__ import annotations

import os
from pathlib import Path
from cplus_cleaner.localization import localized_tk as tk
from cplus_cleaner.localization.i18n import tr
from tkinter import font as tkfont
from cplus_cleaner.localization.localized_tk import ttk, messagebox

from cplus_cleaner.features.privacy import privacy
from cplus_cleaner.application import preferences
from cplus_cleaner.ui.desktop import WHITE, INK, MUTED, MINT, PALE, LINE, CORAL, FONT, PillButton, rounded, text_button
from cplus_cleaner.ui.ui_widgets import check_images, single_window, MetricLabel, scroll_canvas


class PrivacyCard(tk.Canvas):
    def __init__(self, parent, page, category):
        super().__init__(parent, width=120, height=118, bg=WHITE, highlightthickness=0, cursor='hand2')
        self.page, self.category = page, category
        self.art = page.app.images.get('privacy/' + category.key + '.png')
        self.bind('<Button-1>', self.click)
        self.bind('<Enter>', lambda e: self.paint(True))
        self.bind('<Leave>', lambda e: self.paint())
        self.paint()

    def click(self, event=None):
        if self.page.app.busy:
            return
        if self.category.view_only:
            self.page.view_recent()
        else:
            self.page.session.toggle(self.category.key)
            self.page.update_totals()

    def paint(self, hover=False):
        self.delete('all')
        c = self.category
        if hover:
            rounded(self, 1, 0, 119, 116, 10, fill='#f6fcfa', outline='')
        if self.art:
            self.create_image(60, 30, image=self.art)
        if not c.view_only:
            self.create_image(79, 48, image=self.page.checks['on' if c.selected else 'off'])
        self.create_text(60, 75, text=c.name, width=118, fill=INK, font=(FONT, 9), justify='center')
        value = '查看' if c.view_only else ('读取失败' if c.problems and not c.count else f'{c.count}条') if self.page.session.scanned else '待扫描'
        color = CORAL if (c.count or c.problems) and not c.view_only else MINT
        width = max(68, min(116, tkfont.Font(self, font=(FONT, 9, 'bold')).measure(tr(value))+14))
        rounded(self, 60-width/2, 96, 60+width/2, 116, 10, fill=color, outline='')
        self.create_text(60, 106, text=value, fill=WHITE, font=(FONT, 9, 'bold'))

    def _language_changed(self):
        self.paint()


class PrivacyPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=WHITE)
        self.app = app
        self.session = privacy.Session()
        self.checks = check_images(self)
        self.groups, self.cards = {}, {}
        self.columns = 6
        header = tk.Frame(self, bg=WHITE)
        header.pack(fill='x', padx=30, pady=(27, 10))
        self.clean_button = PillButton(header, '立即清理', self.clean, width=142)
        self.clean_button.pack(side='right')
        self.scan_button = PillButton(header, '开始扫描', self.scan, width=142)
        self.scan_button.pack(side='right', padx=(0, 8))
        self.headline = MetricLabel(header, bg=WHITE, fg=INK, font=(FONT, 17), anchor='w')
        self.headline.pack(side='left', fill='x', expand=True)
        self.summary = tk.StringVar()
        MetricLabel(self, textvariable=self.summary, bg=WHITE, fg=MUTED, font=(FONT, 9), anchor='w').pack(fill='x', padx=31, pady=(0, 15))
        tk.Frame(self, bg=LINE, height=1).pack(fill='x', padx=30)
        footer = tk.Frame(self, bg=WHITE)
        footer.pack(side='bottom', fill='x', padx=25, pady=8)
        self.status = tk.StringVar(value='全部时间 · 按隐私记录条数统计')
        tk.Label(footer, textvariable=self.status, bg=WHITE, fg=MUTED, font=(FONT, 9), anchor='w').pack(side='left', fill='x', expand=True)
        content = tk.Frame(self, bg=WHITE)
        content.pack(fill='both', expand=True, padx=(25, 18), pady=(8, 0))
        self.canvas = tk.Canvas(content, bg=WHITE, highlightthickness=0)
        scrollbar = ttk.Scrollbar(content, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.inner = tk.Frame(self.canvas, bg=WHITE)
        self.inner_window = self.canvas.create_window((0, 0), window=self.inner, anchor='nw')
        self.inner.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', self.resize)
        self.bind('<MouseWheel>', self.wheel)
        self.build_groups()
        self.update_totals()

    def wheel(self, event):
        return scroll_canvas(self.canvas, self.inner, event.delta)

    def resize(self, event):
        self.canvas.itemconfigure(self.inner_window, width=event.width)
        columns = max(1, min(6, (event.width-8)//124))
        if columns != self.columns:
            self.columns = columns
            self.layout_cards()

    def layout_cards(self):
        for data in self.groups.values():
            for index, card in enumerate(data['cards']):
                card.grid(row=index//self.columns, column=index%self.columns, padx=2, pady=3, sticky='n')

    def build_groups(self):
        for widget in self.inner.winfo_children():
            widget.destroy()
        self.groups, self.cards = {}, {}
        for key, title in privacy.GROUPS:
            frame = tk.Frame(self.inner, bg=WHITE)
            frame.pack(fill='x', padx=5, pady=(6, 2))
            header = tk.Frame(frame, bg=WHITE)
            header.pack(fill='x')
            icon = self.app.images.get('privacy/' + key + '.png')
            tk.Label(header, image=icon or '', bg=WHITE).pack(side='left', padx=(2, 7))
            toggle = tk.Button(header, image=self.checks['on'], bg=WHITE, activebackground=WHITE,
                relief='flat', bd=0, command=lambda k=key: self.toggle_group(k), cursor='hand2')
            toggle.pack(side='left', padx=(0, 6))
            tk.Label(header, text=title, bg=WHITE, fg=INK, font=(FONT, 10)).pack(side='left')
            collapse = text_button(header, '⌄', lambda k=key: self.collapse(k))
            collapse.configure(pady=0)
            collapse.pack(side='right')
            amount = MetricLabel(header, bg=WHITE, fg=MUTED, font=(FONT, 9))
            amount.pack(side='right', padx=6)
            body = tk.Frame(frame, bg=WHITE)
            body.pack(fill='x', pady=(5, 0))
            cards = []
            for category in self.session.group(key):
                card = PrivacyCard(body, self, category)
                card.bind('<MouseWheel>', self.wheel)
                cards.append(card)
                self.cards[category.key] = card
            self.groups[key] = dict(cards=cards, toggle=toggle, amount=amount, body=body, collapse=collapse, collapsed=False)
        self.layout_cards()

    def toggle_group(self, key):
        if self.app.busy:
            return
        categories = [c for c in self.session.group(key) if not c.view_only]
        self.session.set_group(key, not all(c.selected for c in categories))
        self.update_totals()

    def collapse(self, key):
        group = self.groups[key]
        group['collapsed'] = not group['collapsed']
        if group['collapsed']:
            group['body'].pack_forget()
        else:
            group['body'].pack(fill='x', pady=(5, 0))
        group['collapse'].configure(text='›' if group['collapsed'] else '⌄')

    def update_totals(self):
        total, selected = self.session.total_count, self.session.selected_count
        self.headline.configure(text=f'发现隐私 {total} 条，已选 {selected} 条' if self.session.scanned else '隐私清理')
        self.summary.set(f'已选择{selected}条隐私，清理后隐私更安全！' if self.session.scanned else '全面扫描 Windows、软件和浏览器使用痕迹')
        for key, data in self.groups.items():
            categories = [c for c in self.session.group(key) if not c.view_only]
            amount = sum(c.count for c in categories)
            picked = sum(c.count for c in categories if c.selected)
            data['amount'].configure(text=f'共 {amount}条，已选 {picked}条' if self.session.scanned else '')
            n = sum(c.selected for c in categories)
            data['toggle'].configure(image=self.checks['on' if n == len(categories) else 'mixed' if n else 'off'])
        for card in self.cards.values():
            card.paint()
        self.clean_button.set_enabled(self.session.scanned and selected > 0 and not self.app.busy)
        self.scan_button.caption = '扫描' if self.session.scanned else '开始扫描'
        self.scan_button.set_enabled(not self.app.busy)
        if self.session.scanned:
            self.app.set_nav_metric('privacy', total, unit='条')

    def scan(self):
        if self.app.busy:
            return
        self.clean_button.set_enabled(False)
        self.scan_button.set_enabled(False)
        self.status.set('正在扫描隐私记录…')
        self.clean_errors = []
        def done(session):
            self.session = session
            self.build_groups()
            self.update_totals()
            problems = len(session.problems)
            self.status.set(('扫描已取消 · 已读取部分记录' if session.cancelled else '全部时间 · 扫描完成') +
                            (f' · {problems}项资料无法完整读取，已跳过' if problems else ' · 按记录条数统计'))
        def failed(error):
            self.status.set('扫描失败：' + str(error))
            self.update_totals()
        self.app.start('扫描隐私记录', lambda: privacy.scan(self.app.cancel, self.report_progress), done, failed)

    def report_progress(self, update):
        self.app.report_progress(update.get('message', '正在处理隐私记录') if isinstance(update, dict) else update)

    def clean(self):
        if self.app.busy or not self.session.selected_count:
            return
        self.clean_button.set_enabled(False)
        self.scan_button.set_enabled(False)
        def done(result):
            self.update_totals()
            errors = result['errors']
            self.status.set(f"已清理{result['cleaned_count']}条分类记录" +
                (f' · {len(errors)}项未能清理，已保留' if errors else ''))
            self.clean_errors = errors
        def failed(error):
            self.status.set('清理失败：' + str(error))
            self.update_totals()
        def work():
            result = privacy.clean(self.session, self.app.cancel, self.report_progress)
            preferences.append_activity('privacy_cleanup', ok=not result['errors'],
                summary=f"已清理{result['cleaned_count']}条分类记录，{len(result['errors'])}项保留",
                count=result['cleaned_count'], failed_count=len(result['errors']))
            return result
        self.clean_errors = []
        self.app.start('清理隐私记录', work, done, failed)

    def view_recent(self):
        path = Path(os.environ.get('APPDATA', Path.home()/'AppData/Roaming')) / 'Microsoft/Windows/Recent'
        try:
            os.startfile(path)
        except OSError as error:
            messagebox.showerror('无法打开最近项目', str(error), parent=self)

    def show_diagnostics(self):
        window, created = single_window(self, '_diagnostics_window', '隐私扫描说明')
        if not created:
            return
        window.geometry('660x470')
        text = tk.Text(window, wrap='word', font=(FONT, 10), padx=18, pady=18, relief='flat')
        text.pack(fill='both', expand=True)
        lines = ['扫描范围：全部时间。每条浏览URL、下载记录、Windows运行记录分别计数。',
            '网购痕迹：从浏览历史匹配8个原版网购域名，按完整URL去重。',
            '文件敏感隐私：下载历史中的11类文档后缀，按文件名去重。',
            '这两类是浏览器记录的子集，分类合计可能重复；清理时同一条记录只处理一次。',
            '开始菜单最近运行的程序：点击“查看”打开最近项目。',
            '清理只移除选中历史条目；保留下载文件、文档、书签、密码及账号资料。',
            '清理浏览器/迅雷记录前请先退出对应程序。', '']
        for category in self.session.categories.values():
            lines.append(category.name + ('：查看' if category.view_only else f'：{category.count}条'))
            for diagnostic in category.diagnostics:
                if diagnostic.get('status') != 'ok':
                    lines.append('  ' + diagnostic.get('error', '暂无记录'))
        for error in getattr(self, 'clean_errors', []):
            lines.append('清理保留：' + error.get('error', '未知错误'))
        text.insert('1.0', '\n'.join(lines))
        text.configure(state='disabled')

# Purpose: Privacy Cleanup page
