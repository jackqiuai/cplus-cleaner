"""Independent system-maintenance page using the existing Windows actions."""
from cplus_cleaner.localization import localized_tk as tk
from cplus_cleaner.localization.localized_tk import ttk
from tkinter import Text as NativeText

from cplus_cleaner.features.system import cdrive_system
from cplus_cleaner.features.system import system_analysis
from cplus_cleaner.localization.i18n import original
from cplus_cleaner.ui.desktop import WHITE, INK, MUTED, LINE, FONT, PillButton, size_text
from cplus_cleaner.features.system.system_actions_ui import SYSTEM_ACTIONS, run_system_action


class SystemManagementPage(tk.Frame):
    allow_analysis_elevation = True
    SYSTEM_ACTIONS = SYSTEM_ACTIONS
    run_system_action = run_system_action
    ITEMS = (
        ('recycle_bin', '系统盘回收站', ('empty_recycle_bin',)),
        ('hibernation', '系统休眠文件', ('hibernate_off', 'hibernate_on')),
        ('pagefile', '系统虚拟内存', ('pagefile_settings',)),
        ('swapfile', '应用交换文件', ()),
        ('component_store', 'Windows 更新与组件存储', ('analyze_component_store', 'cleanup_component_store')),
    )
    CAPTIONS = dict(empty_recycle_bin='清空回收站', hibernate_off='关闭休眠', hibernate_on='恢复休眠',
                    pagefile_settings='虚拟内存设置', analyze_component_store='组件分析', cleanup_component_store='组件清理')

    def __init__(self, parent, app):
        super().__init__(parent, bg=WHITE)
        self.app, self.system_items, self.reading, self.loaded = app, [], False, False
        self.running_action, self.analysis_result = None, None
        self.progress_value = tk.DoubleVar(value=0)
        self.progress_text, self.progress_percent = tk.StringVar(), tk.StringVar()
        self.note = tk.StringVar()
        header = tk.Frame(self, bg=WHITE)
        header.pack(fill='x', padx=32, pady=(27, 12))
        self.refresh_button = PillButton(header, '刷新', self.refresh_system, width=145)
        self.refresh_button.pack(side='right')
        tk.Label(header, text='系统瘦身', bg=WHITE, fg=INK, font=(FONT, 20, 'bold')).pack(anchor='w')
        tk.Label(self, text='管理回收站、休眠、虚拟内存与 Windows 组件存储。', bg=WHITE, fg=MUTED,
                 font=(FONT, 10), anchor='w', justify='left', wraplength=760).pack(fill='x', padx=33, pady=(0, 8))
        tk.Label(self, text='以下由 Windows 管理，未计入可清理空间。', bg=WHITE, fg=MUTED,
                 font=(FONT, 9), anchor='w').pack(fill='x', padx=33, pady=(0, 15))
        tk.Frame(self, bg=LINE, height=1).pack(fill='x', padx=32)
        note_label = tk.Label(self, textvariable=self.note, bg=WHITE, fg=MUTED, font=(FONT, 9), anchor='w',
                             justify='left', wraplength=800)
        note_label.pack(side='bottom', fill='x', padx=33, pady=(10, 12))
        note_label.bind('<Configure>', lambda event: note_label.configure(wraplength=max(200, event.width)))
        self.progress_frame = tk.Frame(self, bg=WHITE)
        progress_heading = tk.Frame(self.progress_frame, bg=WHITE)
        progress_heading.pack(fill='x', pady=(0, 5))
        tk.Label(progress_heading, textvariable=self.progress_text, bg=WHITE, fg=INK,
                 font=(FONT, 10), anchor='w', justify='left', wraplength=700).pack(side='left', fill='x', expand=True)
        tk.Label(progress_heading, textvariable=self.progress_percent, bg=WHITE, fg='#39b68a', font=(FONT, 10, 'bold')).pack(side='right')
        style = ttk.Style(self)
        style.configure('System.Horizontal.TProgressbar', background='#46d6a0', troughcolor='#edf7f2', borderwidth=0, thickness=10)
        style.configure('SystemError.Horizontal.TProgressbar', background='#ff6269', troughcolor='#fff0f1', borderwidth=0, thickness=10)
        self.operation_progress = ttk.Progressbar(self.progress_frame, variable=self.progress_value,
                                                maximum=100, mode='indeterminate', style='System.Horizontal.TProgressbar')
        self.operation_progress.pack(fill='x')
        self.area = area = tk.Frame(self, bg=WHITE)
        area.pack(fill='both', expand=True, padx=32, pady=(12, 0))
        self.canvas = tk.Canvas(area, bg=WHITE, highlightthickness=0)
        scroll = ttk.Scrollbar(area, orient='vertical', command=self.canvas.yview)
        scroll.pack(side='right', fill='y')
        self.canvas.pack(fill='both', expand=True)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.inner = tk.Frame(self.canvas, bg=WHITE)
        self.inner_window = self.canvas.create_window(0, 0, window=self.inner, anchor='nw')
        self.inner.bind('<Configure>', self.update_region)
        self.canvas.bind('<Configure>', self.resize)
        self.bind_all('<MouseWheel>', self.wheel, add='+')
        self.rows, self.system_buttons = {}, {}
        for item_id, title, actions in self.ITEMS:
            row = tk.Frame(self.inner, bg=WHITE)
            row.pack(fill='x', pady=(0, 10))
            row.columnconfigure(0, weight=1)
            text = tk.Frame(row, bg=WHITE)
            text.grid(row=0, column=0, sticky='ew', padx=(8, 16), pady=(10, 12))
            tk.Label(text, text=title, bg=WHITE, fg=INK, font=(FONT, 11, 'bold'), anchor='w').pack(fill='x')
            amount = tk.Label(text, text='待读取', bg=WHITE, fg='#39b68a', font=(FONT, 10), anchor='w')
            amount.pack(fill='x', pady=(5, 0))
            status = tk.Label(text, bg=WHITE, fg=MUTED, font=(FONT, 9), justify='left', anchor='w', wraplength=350)
            status.pack(fill='x', pady=(5, 0))
            text.bind('<Configure>', lambda event, label=status: label.configure(wraplength=max(180, event.width)))
            controls = tk.Frame(row, bg=WHITE)
            controls.grid(row=0, column=1, sticky='e', padx=(0, 12), pady=12)
            for action in actions:
                button = PillButton(controls, self.CAPTIONS[action], lambda action=action: self.run_system_action(action),
                                    width=142, height=36, font=(FONT, 10, 'bold'))
                button.pack(side='left', padx=(8, 0))
                button.set_enabled(False)
                self.system_buttons[action] = button
            tk.Frame(row, bg=LINE, height=1).grid(row=1, column=0, columnspan=2, sticky='ew')
            self.rows[item_id] = (amount, status)
        self.analysis_frame = tk.Frame(self.inner, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
        tk.Label(self.analysis_frame, text='组件分析结果', bg=WHITE, fg=INK, font=(FONT, 11, 'bold')).pack(anchor='w', padx=12, pady=(10, 5))
        report_area = tk.Frame(self.analysis_frame, bg=WHITE)
        report_area.pack(fill='both', expand=True, padx=12, pady=(0, 12))
        self.analysis_text = NativeText(report_area, height=12, bg=WHITE, fg=INK, font=(FONT, 9),
                                     relief='flat', wrap='word', state='disabled')
        report_scroll = ttk.Scrollbar(report_area, command=self.analysis_text.yview)
        self.analysis_text.configure(yscrollcommand=report_scroll.set)
        report_scroll.pack(side='right', fill='y')
        self.analysis_text.pack(fill='both', expand=True)
        self.bind('<Destroy>', self.release_progress, add='+')

    def release_progress(self, event):
        if event.widget is self:
            try:
                self.operation_progress.stop()
            except tk.TclError:
                pass

    def begin_system_action(self, action_id, label):
        self.running_action = action_id
        self.note.set('')
        if action_id == 'analyze_component_store':
            self.analysis_result = None
            self.analysis_frame.pack_forget()
        self.operation_progress.stop()
        self.operation_progress.configure(mode='indeterminate', style='System.Horizontal.TProgressbar')
        self.progress_value.set(0)
        self.progress_percent.set('…')
        self.progress_text.set('正在分析 Windows 组件存储…' if action_id == 'analyze_component_store' else label)
        self.progress_frame.pack(fill='x', padx=33, pady=(12, 4), before=self.area)
        self.operation_progress.start(12)
        self.update_summary()

    def report_system_progress(self, update):
        # The worker only queues data. All widget changes happen on Tk's thread.
        self.app.events.put(('batch', (self.receive_system_progress, update)))

    def receive_system_progress(self, update):
        if self.running_action is None:
            return
        if isinstance(update, dict):
            value = update.get('percent')
            if isinstance(value, (int, float)):
                self.operation_progress.stop()
                self.operation_progress.configure(mode='determinate')
                self.progress_value.set(max(0, min(100, value)))
                self.progress_percent.set(f'{value:.1f}%')
            if update.get('message'):
                self.progress_text.set(update['message'])
        else:
            self.progress_text.set(str(update))

    def finish_system_action(self, action_id, result):
        self.running_action = None
        self.operation_progress.stop()
        self.operation_progress.configure(mode='determinate', style='System.Horizontal.TProgressbar' if result.get('ok') else 'SystemError.Horizontal.TProgressbar')
        self.progress_value.set(100 if result.get('ok') else 0)
        self.progress_percent.set('100%' if result.get('ok') else '')
        self.progress_text.set(result.get('message') or ('系统操作已完成' if result.get('ok') else '系统操作未完成'))
        if action_id == 'analyze_component_store':
            self.analysis_result = dict(result)
            output = system_analysis.report_text(result.get('output') or result.get('stdout'))
            if output:
                self.analysis_text.configure(state='normal')
                self.analysis_text.delete('1.0', 'end')
                # DISM output is returned by Windows and retains its OS language.
                self.analysis_text.insert('1.0', original(output))
                self.analysis_text.configure(state='disabled')
                self.analysis_frame.pack(fill='x', padx=8, pady=(5, 16), before=self.inner.winfo_children()[0])
                self.update_idletasks()
                self.canvas.yview_moveto(0)
        self.update_summary()

    def on_open(self):
        if self.app.busy:
            self.note.set('当前任务结束后，请点击刷新。')
            self.update_summary()
        else:
            self.refresh_system()

    def refresh_system(self):
        if self.app.busy:
            return
        self.reading = True
        self.update_summary()

        def done(items):
            self.reading = False
            self.set_system_items(items)

        def failed(error):
            self.reading = False
            self.note.set(error)
            self.update_summary()

        self.app.start('读取系统项目', cdrive_system.read_system_items, done, failed)

    def set_system_items(self, items):
        self.system_items, self.loaded = list(items), True
        observed = {item['id']: item for item in self.system_items}
        for key, (amount, status) in self.rows.items():
            item = observed.get(key)
            size = item.get('size') if item else None
            amount.configure(text='未知' if size is None else size_text(size))
            status.configure(text=(item.get('status') or item.get('action', '')) if item else '未读取到此系统项目')
        if self.analysis_result:
            amount, status = self.rows['component_store']
            if self.analysis_result.get('ok'):
                amount.configure(text='已分析')
            status.configure(text=self.analysis_result.get('message', ''))
        self.app.cdrive_page.set_system_items(self.system_items)
        self.app.cdrive_page.refresh()
        self.app.home_page.refresh()
        self.update_summary()

    def update_summary(self):
        available = {action for item in self.system_items for action in item.get('actions', ())}
        self.refresh_button.caption = '读取中…' if self.reading else '刷新'
        self.refresh_button.set_enabled(not self.app.busy and not self.reading and self.running_action is None)
        for action, button in self.system_buttons.items():
            button.set_enabled(not self.app.busy and not self.reading and self.running_action is None and action in available)

    def update_region(self, event=None):
        self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(),
                                           max(self.inner.winfo_reqheight(), self.canvas.winfo_height())))

    def resize(self, event):
        self.canvas.itemconfigure(self.inner_window, width=event.width)
        self.update_region()

    def wheel(self, event):
        if self.canvas.winfo_ismapped() and self.inner.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(-int(event.delta/120), 'units')

# Purpose: System Slim page
