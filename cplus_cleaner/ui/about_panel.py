"""Application identity, current version and an optional GitHub project link."""
from __future__ import annotations

import webbrowser

from cplus_cleaner.application import preferences
from cplus_cleaner.localization import localized_tk as tk
from cplus_cleaner.ui.desktop_panels import FONT, LINE, MUTED, WHITE, label


PROJECT_URL = "https://github.com/jackqiuai/cplus-cleaner"


class AboutPanel(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=WHITE)
        self.app = app
        self.logo_image = app.images.get("app-icon.png")
        self.logo_label = tk.Label(self, image=self.logo_image, bg=WHITE)
        self.logo_label.pack(pady=(30, 12))
        self.product_label = label(self, "Cplus Cleaner", 22)
        self.product_label.configure(font=(FONT, 22, "bold"))
        self.product_label.pack()
        self.version_label = label(self, f"v{preferences.VERSION}", 11, MUTED)
        self.version_label.pack(pady=(6, 18))
        self.description = label(self, "清理磁盘，管理软件，释放更多可用空间。", 11,
                                 wraplength=480, justify="center")
        self.description.pack(padx=40)
        tk.Frame(self, bg=LINE, height=1).pack(fill="x", padx=45, pady=24)
        self.support_text = label(self,
            "如果 Cplus Cleaner 对你有帮助，欢迎在 GitHub 点一颗 Star，支持项目持续改进。",
            10, MUTED, wraplength=480, justify="center")
        self.support_text.pack(padx=40)

        from cplus_cleaner.ui.desktop import PillButton
        controls = tk.Frame(self, bg=WHITE)
        controls.pack(pady=(24, 12))
        self.star_button = PillButton(controls, "给项目点个 Star", self.open_project,
                                      width=215, height=43, font=(FONT, 11, "bold"))
        self.return_button = PillButton(controls, "关闭", parent.winfo_toplevel().destroy,
                                        width=115, height=43, light=True,
                                        font=(FONT, 11, "bold"))
        self.star_button.pack(side="left", padx=(0, 12))
        self.return_button.pack(side="left")
        for button in (self.star_button, self.return_button):
            button.configure(takefocus=True)
            for event in ("<Return>", "<space>"):
                button.bind(event, lambda event, control=button: control.invoke())
        self.message = tk.StringVar()
        tk.Label(self, textvariable=self.message, bg=WHITE, fg="#d34f58", font=(FONT, 9),
                 wraplength=480, justify="center").pack(padx=40, pady=(0, 20))

    def open_project(self):
        try:
            opened = webbrowser.open_new_tab(PROJECT_URL)
        except (OSError, webbrowser.Error):
            opened = False
        self.message.set("" if opened else "无法打开浏览器，请稍后重试。")


# Purpose: Bilingual About dialog with the application logo, version and GitHub star link
