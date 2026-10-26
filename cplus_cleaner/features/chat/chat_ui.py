"""Unified, manual-scan chat cleanup page; no account content is displayed."""
from __future__ import annotations

import base64
from cplus_cleaner.localization import localized_tk as tk
from cplus_cleaner.localization.localized_tk import ttk, filedialog

from cplus_cleaner.features.chat import chat_cleanup as backend
from cplus_cleaner.ui import desktop
from cplus_cleaner.features.drive_cleanup.cleanup_model import CleanupSession
from cplus_cleaner.ui.desktop import WHITE, INK, MUTED, MINT, CORAL, FONT, PillButton, DiskBar, size_text, text_button, rounded
from cplus_cleaner.ui.ui_widgets import Checkbutton, MetricLabel, check_images


STATUS = dict(missing='未检测到安装或数据', unsupported='已检测到软件或数据，目录结构待适配',
              unreadable='部分目录无法访问', detected='已检测到数据', ready='已识别支持的数据目录',
              data='发现本地数据，安装状态未确认')


class ChatCard(tk.Canvas):
    def __init__(self, parent, page, category, app_id):
        super().__init__(parent, width=124, height=126, bg=WHITE, highlightthickness=0, cursor='hand2')
        self.page, self.category, self.app_id = page, category, app_id
        self.bind('<Button-1>', lambda event: self.toggle())
        self.bind('<space>', lambda event: self.toggle())
        self.paint()

    def toggle(self):
        if not self.page.app.busy and self.category.bytes_count > 0:
            self.page.session.set_selected(self.category.key, not self.category.selected)
            self.page.update_totals()

    def paint(self):
        self.delete('all')
        category = self.category
        # Keep the existing WeChat asset for its cards as well as its group.
        image = self.page.app.images.get('icon-wechat.png') if self.app_id == 'wechat' else self.page.app.images.icon(category.name)
        if image:
            self.create_image(62, 32, image=image)
        self.create_image(82, 42, anchor='nw', image=check_images(self)['on' if category.selected else 'off'])
        self.create_text(62, 76, text=category.name, fill=INK, font=(FONT, 9), width=120)
        rounded(self, 22, 98, 102, 119, 10, fill=CORAL if category.bytes_count else MINT, outline='')
        self.create_text(62, 108, text=size_text(category.bytes_count), fill=WHITE, font=(FONT, 9, 'bold'))


class ChatPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=WHITE)
        self.app, self.session, self.snapshot = app, None, None
        self.scanning = self.processing = self._destroyed = False
        self.cards, self.groups, self.icons, self.expanded = {}, {}, {}, set(backend.APPS)
        self.heading = tk.StringVar(value='聊天清理')
        self.subtitle = tk.StringVar(value='按软件和类型整理聊天数据，扫描后自动勾选有容量的分类')
        self.note = tk.StringVar(value='支持微信、QQ、WhatsApp、Telegram、Discord、Slack、Microsoft Teams、Signal。')
        self.progress_text, self.progress_percent = tk.StringVar(), tk.StringVar()
        self.progress_value = tk.DoubleVar(value=0)
        header = tk.Frame(self, bg=WHITE)
        header.pack(fill='x', padx=32, pady=(24, 10))
        actions = tk.Frame(header, bg=WHITE)
        actions.pack(side='right', anchor='n', padx=(12, 0))
        self.scan_button = PillButton(actions, '开始扫描', self.scan, width=150)
        self.scan_button.pack(side='left', padx=(0, 10))
        self.clean_button = PillButton(actions, '立即清理', self.clean, width=150)
        self.clean_button.pack(side='left')
        self.clean_button.set_enabled(False)
        self.heading_label = MetricLabel(header, textvariable=self.heading, font=(FONT, 15))
        self.heading_label.pack(fill='x', anchor='w')
        self.subtitle_label = MetricLabel(self, textvariable=self.subtitle, font=(FONT, 9), fg=MUTED)
        self.subtitle_label.pack(fill='x', padx=33, pady=(0, 12))
        self.disk = DiskBar(self, wide=500)
        self.disk.pack(fill='x', padx=34, pady=(16, 7))
        self.volume = tk.StringVar(value='进入页面不会自动扫描；可在各软件分组中指定数据目录。')
        tk.Label(self, textvariable=self.volume, bg=WHITE, fg=MUTED, font=(FONT, 9), anchor='w',
                 wraplength=680, justify='left').pack(fill='x', padx=34, pady=(0, 8))
        self.progress_frame = tk.Frame(self, bg=WHITE)
        tk.Label(self.progress_frame, textvariable=self.progress_text, bg=WHITE, fg=MUTED,
                 font=(FONT, 9), anchor='w').pack(side='top', fill='x')
        self.progress = ttk.Progressbar(self.progress_frame, variable=self.progress_value, maximum=100,
                                       mode='determinate', style='Mint.Horizontal.TProgressbar')
        self.progress.pack(fill='x', pady=(4, 0))
        bottom = tk.Frame(self, bg=WHITE)
        bottom.pack(side='bottom', fill='x', padx=32, pady=(8, 12))
        self.select_all_button = text_button(bottom, '全选', lambda: self.select_all(True))
        self.select_all_button.pack(side='left')
        text_button(bottom, '全不选', lambda: self.select_all(False)).pack(side='left', padx=8)
        tk.Label(bottom, textvariable=self.note, bg=WHITE, fg=MUTED, font=(FONT, 8), justify='left',
                 anchor='w', wraplength=430).pack(side='right', fill='x', expand=True, padx=(8, 0))
        self.area = tk.Frame(self, bg=WHITE)
        self.area.pack(fill='both', expand=True, padx=(30, 24), pady=(4, 0))
        self.canvas = tk.Canvas(self.area, bg=WHITE, bd=0, highlightthickness=0)
        scroll = ttk.Scrollbar(self.area, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.inner = tk.Frame(self.canvas, bg=WHITE)
        self.inner_window = self.canvas.create_window(0, 0, window=self.inner, anchor='nw')
        self.canvas.bind('<Configure>', self.resize_canvas)
        self.inner.bind('<Configure>', self.sync_scroll)
        self._wheel_binding = self.bind_all('<MouseWheel>', self.mousewheel, add='+')
        self.refresh()
        self.render()

    def refresh(self):
        try:
            self.disk.update_info(desktop.disk_snapshot())
        except OSError:
            pass
        self.update_totals()

    def choose_folder(self, key):
        if self.app.busy:
            return
        folder = filedialog.askdirectory(parent=self, title=f"选择{backend.APPS[key]['name']}数据目录")
        if folder:
            try:
                backend.save_path(key, folder)
                self.note.set('数据目录已保存，重新扫描后生效；只扫描受支持的子目录。')
            except (OSError, ValueError) as exc:
                self.note.set(str(exc))

    def reset_folder(self, key):
        if self.app.busy:
            return
        try:
            backend.save_path(key)
            self.note.set('已恢复自动检测目录，重新扫描后生效。')
        except OSError as exc:
            self.note.set(str(exc))

    def resize_canvas(self, event):
        self.canvas.itemconfigure(self.inner_window, width=event.width)
        columns = max(1, event.width//130)
        for group in self.groups.values():
            for index, card in enumerate(group['cards']):
                card.grid(row=index//columns, column=index%columns, padx=2, pady=3)
        self.sync_scroll()

    def sync_scroll(self, event=None):
        height = max(self.canvas.winfo_height(), self.inner.winfo_reqheight())
        self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(), height))
        if self.inner.winfo_reqheight() <= self.canvas.winfo_height():
            self.canvas.yview_moveto(0)

    def mousewheel(self, event):
        target = self.winfo_containing(event.x_root, event.y_root)
        while target is not None:
            if target == self.canvas:
                if self.inner.winfo_reqheight() > self.canvas.winfo_height():
                    self.canvas.yview_scroll(int(-event.delta/120), 'units')
                return 'break'
            target = getattr(target, 'master', None)

    def app_icon(self, state):
        key = state['id']
        if key not in self.icons:
            image = self.app.images.get('icon-wechat.png' if key == 'wechat' else 'icon-qq.png') if key in ('wechat', 'qq') else None
            if image is None and state.get('icon'):
                try:
                    image = tk.PhotoImage(master=self, data=base64.b64encode(state['icon']))
                except tk.TclError:
                    pass
            self.icons[key] = image
        return self.icons[key]

    def render(self):
        for child in self.inner.winfo_children():
            child.destroy()
        self.cards, self.groups = {}, {}
        states = self.snapshot.get('apps', []) if self.snapshot else [dict(id=spec['id'], name=spec['name'], status='idle') for spec in backend.CATALOG]
        for state in states:
            key, spec = state['id'], backend.APPS[state['id']]
            frame = tk.Frame(self.inner, bg=WHITE)
            frame.pack(fill='x', pady=(4, 9))
            row = tk.Frame(frame, bg=WHITE)
            row.pack(fill='x', pady=(0, 3))
            image = self.app_icon(state)
            if image:
                tk.Label(row, image=image, bg=WHITE).pack(side='left', padx=(3, 8))
            else:
                tk.Label(row, text=spec['initial'], bg=spec['color'], fg=WHITE,
                         font=(FONT, 12, 'bold'), width=2).pack(side='left', padx=(3, 8))
            categories = self.session.groups.get(spec['name'], []) if self.session else []
            value = tk.BooleanVar(value=bool(categories) and all(c.selected for c in categories if c.bytes_count > 0)
                                  and any(c.bytes_count > 0 for c in categories))
            toggle = Checkbutton(row, text=spec['name'], bg=WHITE, fg=INK, font=(FONT, 10),
                                  variable=value, command=lambda name=spec['name']: self.toggle_group(name))
            toggle.pack(side='left')
            if not any(c.bytes_count > 0 for c in categories):
                toggle.configure(state='disabled')
            amount = MetricLabel(row, font=(FONT, 9), fg=MUTED)
            amount.pack(side='right', padx=(3, 6))
            collapse = text_button(row, '⌄' if key in self.expanded else '›', lambda key=key: self.collapse(key))
            collapse.pack(side='right')
            status = '尚未扫描' if state['status'] == 'idle' else STATUS[state['status']]
            if state['status'] == 'unsupported' and state.get('variant') == 'web':
                status = '检测到网页版，独立缓存规则待适配；不清理浏览器共享数据'
            if state.get('version'):
                status += f" · v{state['version']}"
            status_row = tk.Frame(frame, bg=WHITE)
            status_row.pack(fill='x', padx=(44, 6))
            folder_button = text_button(status_row, '指定目录', lambda key=key: self.choose_folder(key))
            folder_button.configure(font=(FONT, 8), padx=4, pady=2)
            folder_button.pack(side='right')
            reset_button = text_button(status_row, '恢复自动目录', lambda key=key: self.reset_folder(key))
            reset_button.configure(font=(FONT, 8), padx=4, pady=2)
            reset_button.pack(side='right', padx=(5, 8))
            tk.Label(status_row, text=status, bg=WHITE, fg=MUTED, font=(FONT, 8), anchor='w',
                     wraplength=460).pack(side='left', fill='x', expand=True)
            body = tk.Frame(frame, bg=WHITE)
            if key in self.expanded:
                body.pack(fill='x', pady=(4, 0))
            cards = []
            for index, category in enumerate(categories):
                card = ChatCard(body, self, category, key)
                columns = max(1, self.canvas.winfo_width()//130)
                card.grid(row=index//columns, column=index%columns, padx=2, pady=3)
                self.cards[category.key] = card
                cards.append(card)
            if self.snapshot and not categories and state['status'] != 'missing':
                tk.Label(body, text='未识别到可清理目录；聊天记录、登录信息和附件存储保持原样。',
                         bg=WHITE, fg=MUTED, font=(FONT, 9), anchor='w').pack(fill='x', padx=44, pady=7)
            self.groups[key] = dict(frame=frame, body=body, cards=cards, amount=amount,
                                    toggle=toggle, value=value, collapse=collapse, folder_button=folder_button)
        self.update_totals()
        self.inner.update_idletasks()
        self.sync_scroll()

    def collapse(self, key):
        self.expanded.symmetric_difference_update({key})
        self.render()

    def toggle_group(self, name):
        if self.session and not self.app.busy:
            available = [category for category in self.session.groups[name] if category.bytes_count > 0]
            checked = bool(available) and all(category.selected for category in available)
            for category in self.session.groups[name]:
                self.session.set_selected(category.key, not checked and category.bytes_count > 0)
        self.update_totals()

    def select_all(self, value):
        if self.session and not self.app.busy:
            for category in self.session.categories:
                self.session.set_selected(category.key, bool(value) and category.bytes_count > 0)
            self.update_totals()

    def update_totals(self):
        self.scan_button.set_enabled(not self.app.busy and not self.scanning)
        self.clean_button.set_enabled(bool(self.session and self.session.selected_count) and not self.app.busy)
        if not self.session:
            return
        self.snapshot['items'] = [item for category in self.session.categories for item in category.items]
        self.heading.set(f"发现可清理 {size_text(self.session.total_bytes)}")
        self.subtitle.set(f"已选 {size_text(self.session.selected_bytes)} · 扫描后自动勾选有容量的分类，可自行取消")
        self.app.set_nav_metric('chat', self.session.total_bytes)
        totals = backend.volume_totals(self.snapshot['items'])
        self.volume.set('可清理空间：'+'；'.join(f"{drive.rstrip(':')}盘 {size_text(size)}" for drive, size in sorted(totals.items()))
                        if totals else '未发现可清理文件；未安装、无法访问和待适配的状态在各软件下方显示。')
        for key, group in self.groups.items():
            data = self.session.group_totals.get(backend.APPS[key]['name'])
            if data:
                group['amount'].configure(text=f"共 {size_text(data['bytes_count'])}，已选 {size_text(data['selected_bytes'])}")
                available = [category for category in self.session.groups[backend.APPS[key]['name']] if category.bytes_count > 0]
                group['value'].set(bool(available) and all(category.selected for category in available))
        for card in self.cards.values():
            card.paint()

    update_summary = update_totals

    def consume_progress(self, data):
        if self._destroyed:
            return
        self.progress_text.set(str(data.get('message', '正在扫描…')))
        if data.get('total'):
            self.progress_value.set(100*data.get('completed', 0)/data['total'])

    def adopt(self, payload, message='扫描完成'):
        self.snapshot = payload
        self.session = CleanupSession(payload['rules'], payload['manifest_items'])
        self.session.consume(payload['items'])
        self.scanning = False
        self.icons.clear()
        self.select_all(True)
        self.scan_button.caption = '扫描'
        self.scan_button.paint()
        self.note.set(message + (f" · {len(payload.get('errors', []))}项无法读取或已跳过" if payload.get('errors') else '') +
                      (' · 部分安装信息不可读，已按数据目录继续检测' if payload.get('discovery_warnings') else ''))
        self.render()
        self.refresh()

    def scan(self):
        if self.app.busy:
            return
        self.scanning = True
        self.scan_button.set_enabled(False)
        self.clean_button.set_enabled(False)
        self.progress_value.set(0)
        self.progress_text.set('正在检测聊天软件和数据目录…')
        self.progress_frame.pack(fill='x', padx=34, pady=(0, 8), before=self.area)
        def work():
            return backend.scan(self.app.cancel,
                lambda data: self.app.events.put(('batch', (self.consume_progress, data))), days=0)
        def done(result):
            self.adopt(result, '扫描已取消，保留部分结果' if result['cancelled'] else '扫描完成')
            if not result['cancelled']:
                self.progress_value.set(100)
            self.progress_text.set('扫描已取消' if result['cancelled'] else '扫描完成')
            self.app.home_page.refresh()
        def failed(error):
            self.scanning = False
            self.progress_text.set('扫描未完成')
            self.note.set(str(error))
            self.update_totals()
        self.app.start('聊天清理扫描', work, done, failed)

    def clean(self):
        if self.app.busy or not self.session or not self.session.selected_count:
            return
        items = self.session.selected_items()
        self.clean_button.set_enabled(False)
        self.scan_button.set_enabled(False)
        def work():
            return backend.delete(self.snapshot, items, self.app.cancel,
                                  lambda text: self.app.report_progress('正在清理所选聊天类型…'))
        def done(result):
            self.session.apply_delete_result(result)
            self.update_totals()
            self.note.set(f"已清理 {size_text(result['bytes'])}，跳过/失败 {len(result['errors'])} 项。"+
                          ('运行中的软件或变化的文件会跳过，退出软件后可重试。' if result['errors'] else ''))
            from cplus_cleaner.features.home.home_results import refresh_cleanup
            refresh_cleanup(self.app)
            self.app.home_page.refresh()
        def failed(error):
            self.note.set(str(error))
            self.update_totals()
        self.app.start('聊天清理', work, done, failed)

    def destroy(self):
        self._destroyed = True
        if self._wheel_binding:
            self._root()._unbind(('bind', 'all', '<MouseWheel>'), self._wheel_binding)
        super().destroy()

# Purpose: Chat Cleanup page
