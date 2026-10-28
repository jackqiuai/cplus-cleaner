"""Responsive, bilingual update checks; downloads remain a user browser action."""
import queue
import threading
import webbrowser

from cplus_cleaner.application import preferences, updates
from cplus_cleaner.localization import localized_tk as tk
from cplus_cleaner.localization.localized_tk import ttk
from cplus_cleaner.ui.desktop_panels import FONT, MUTED, WHITE, label


class UpdatePanel(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=WHITE)
        self._destroyed, self._timer, self.checking = False, None, False
        self.results = queue.Queue()
        self.release_url = updates.RELEASES_URL
        self.logo_image = app.images.get('brand/sidebar.png')
        tk.Label(self, image=self.logo_image, bg=WHITE).pack(pady=(25, 10))
        label(self, '检查更新', 19).pack()
        versions = tk.Frame(self, bg=WHITE)
        versions.pack(pady=(20, 12))
        label(versions, '当前版本', 10, MUTED).grid(row=0, column=0, sticky='w', padx=(0, 30), pady=4)
        label(versions, f'v{preferences.VERSION}', 11).grid(row=0, column=1, sticky='w')
        label(versions, '最新版本', 10, MUTED).grid(row=1, column=0, sticky='w', pady=4)
        self.latest = tk.StringVar(value='—')
        tk.Label(versions, textvariable=self.latest, bg=WHITE, font=(FONT, 11)).grid(row=1, column=1, sticky='w')
        self.status = tk.StringVar()
        self.status_label = tk.Label(self, textvariable=self.status, bg=WHITE, fg=MUTED,
                                    font=(FONT, 10), wraplength=510, justify='center')
        self.status_label.pack(padx=30, pady=(0, 12))
        self.progress = ttk.Progressbar(self, mode='indeterminate', length=420)
        self.progress.pack(pady=(0, 18))
        from cplus_cleaner.ui.desktop import PillButton
        controls = tk.Frame(self, bg=WHITE)
        self.controls = controls
        controls.pack(pady=(0, 25))
        self.check_button = PillButton(controls, '检查更新', self.check, width=155, height=40, font=(FONT, 10, 'bold'))
        self.download_button = PillButton(controls, '查看发布页', self.open_release, width=155, height=40,
                                          font=(FONT, 10, 'bold'))
        self.return_button = PillButton(controls, '关闭', parent.winfo_toplevel().destroy, width=105,
                                        height=40, light=True, font=(FONT, 10, 'bold'))
        for button in (self.check_button, self.download_button, self.return_button):
            button.pack(side='left', padx=5)
            button.configure(takefocus=True)
            for event in ('<Return>', '<space>'):
                button.bind(event, lambda event, control=button: control.invoke())
        self.check()

    def check(self):
        if self.checking or self._destroyed:
            return
        self.checking = True
        self.check_button.set_enabled(False)
        self.status.set('正在检查更新…')
        if not self.progress.winfo_manager():
            self.progress.pack(pady=(0, 18), before=self.controls)
        self.progress.start(12)
        threading.Thread(target=self._fetch, args=(preferences.VERSION, self.results),
                         name='CPlus-update-check', daemon=True).start()
        self._timer = self.after(100, self.poll)

    @staticmethod
    def _fetch(current, results):
        try:
            results.put((updates.check_latest(current), ''))
        except updates.UpdateError as exception:
            results.put((None, str(exception)))
        except Exception:
            results.put((None, '检查更新失败，请稍后重试。'))

    def poll(self):
        self._timer = None
        if self._destroyed:
            return
        try:
            release, failure = self.results.get_nowait()
        except queue.Empty:
            self._timer = self.after(100, self.poll)
            return
        self.progress.stop()
        self.progress.pack_forget()
        self.checking = False
        self.check_button.set_enabled(True)
        if failure:
            self.status.set(failure)
            return
        self.latest.set(f'v{release.version}')
        self.release_url = release.url
        self.status.set('发现新版本，点击前往下载。' if release.available else '当前已是最新版本。')
        self.download_button.caption = '前往下载' if release.available else '查看发布页'
        self.download_button.paint()

    def open_release(self):
        try:
            opened = webbrowser.open_new_tab(self.release_url)
        except (OSError, webbrowser.Error):
            opened = False
        if not opened:
            self.status.set('无法打开浏览器，请稍后重试。')

    def destroy(self):
        self._destroyed = True
        self.progress.stop()
        if self._timer is not None:
            self.after_cancel(self._timer)
            self._timer = None
        super().destroy()


# Purpose: Bilingual update dialog, background requests and release-page navigation
