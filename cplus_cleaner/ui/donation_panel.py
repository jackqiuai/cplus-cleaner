"""Language-aware voluntary donation dialog; no payment processing or tracking."""
from __future__ import annotations

import shutil
import webbrowser

from cplus_cleaner.application import donations
from cplus_cleaner.common import core
from cplus_cleaner.localization import i18n
from cplus_cleaner.localization import localized_tk as tk
from cplus_cleaner.localization.localized_tk import filedialog
from cplus_cleaner.ui.desktop_panels import Panel, label, WHITE, INK, MUTED, MINT, LINE, FONT
from cplus_cleaner.ui.ui_widgets import single_window


def fit_image(image, width, height):
    """Use nearest-neighbor integer reduction so QR modules remain sharp."""
    factor = max(1, (image.width()+width-1)//width, (image.height()+height-1)//height)
    return image.subsample(factor) if factor > 1 else image


class DonationPanel(Panel):
    def __init__(self, parent, app):
        super().__init__(parent, app, "捐赠支持 CPlus Cleaner",
                         "软件免费使用，捐赠完全自愿。感谢支持项目的开发与维护。")
        self.return_button.destroy()
        self.return_button = self.button(self.header, "关闭", parent.winfo_toplevel().destroy,
                                         width=110, height=36, light=True)
        self.return_button.pack(side="right")
        self.qr_images, self.preview_images, self.qr_buttons = {}, {}, {}
        self.message = tk.StringVar()

        self.domestic = tk.Frame(self.body, bg=WHITE)
        self.domestic.grid_columnconfigure(tuple(range(len(donations.QR_CODES))), weight=1, uniform="payment")
        for column, code in enumerate(donations.QR_CODES):
            card = tk.Frame(self.domestic, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
            card.grid(row=0, column=column, sticky="ns", padx=10 if len(donations.QR_CODES) > 1 else 0)
            label(card, code.name, 13, "#229f78").pack(pady=(14, 4))
            holder = tk.Frame(card, bg=WHITE, width=300, height=300)
            holder.pack(padx=12, pady=4)
            holder.pack_propagate(False)
            source = app.images.get(code.asset)
            if source is not None:
                x1, y1, x2, y2 = code.viewport
                image = tk.PhotoImage(master=self, width=x2-x1, height=y2-y1)
                image.tk.call(str(image), 'copy', str(source), '-from', x1, y1, x2, y2)
                self.qr_images[code.key] = image
                self.preview_images[code.key] = fit_image(image, 290, 290)
                preview = tk.Label(holder, image=self.preview_images[code.key], bg=WHITE,
                                   cursor="hand2", takefocus=True)
                preview.place(relx=.5, rely=.5, anchor="center")
                for event in ("<Button-1>", "<Return>", "<space>"):
                    preview.bind(event, lambda event, key=code.key: self.open_qr(key))
            else:
                label(holder, "收款二维码无法读取", 10, MUTED).place(relx=.5, rely=.5, anchor="center")
            controls = tk.Frame(card, bg=WHITE)
            controls.pack(pady=(5, 16))
            enlarge = self.button(controls, "放大", lambda key=code.key: self.open_qr(key),
                                  width=105, height=35, light=True)
            enlarge.pack(side="left", padx=(0, 8))
            save = self.button(controls, "保存图片", lambda key=code.key: self.save_qr(key),
                               width=120, height=35)
            save.pack(side="left")
            for button in (enlarge, save):
                button.set_enabled(code.key in self.qr_images)
            self.qr_buttons[code.key] = enlarge, save

        self.international = tk.Frame(self.body, bg=WHITE)
        art = tk.Canvas(self.international, width=150, height=115, bg=WHITE, highlightthickness=0)
        art.pack(pady=(18, 4))
        art.create_oval(25, 4, 125, 104, fill="#ecfaf5", outline="")
        art.create_text(75, 53, text="♡", fill=MINT, font=("Segoe UI Symbol", 48))
        label(self.international, "支持 CPlus Cleaner 持续改进", 16).pack(pady=(4, 14))
        self.coffee_button = self.button(self.international, "Buy me a coffee", self.open_kofi,
                                         width=245, height=46)
        self.coffee_button.pack(pady=(0, 24))

        self.switch_button = tk.Button(self.body, command=self.switch_region, bg=WHITE, fg="#229f78",
                                       activebackground="#ecfaf5", relief="flat", bd=0,
                                       cursor="hand2", font=(FONT, 10), pady=6)
        self.switch_button.pack(side="bottom", pady=(10, 0))
        tk.Label(self.body, textvariable=self.message, bg=WHITE, fg="#d34f58", font=(FONT, 9),
                 wraplength=680, justify="left").pack(side="bottom", fill="x", pady=(8, 0))
        self.show_region('china' if i18n.language() == 'zh' else 'international')
        i18n.register(self)

    @staticmethod
    def button(parent, text, command, **options):
        from cplus_cleaner.ui.desktop import PillButton
        button = PillButton(parent, text, command, font=(FONT, 10, "bold"), **options)
        button.configure(takefocus=True)
        for event in ("<Return>", "<space>"):
            button.bind(event, lambda event: button.invoke())
        return button

    def show_region(self, region):
        self.current_view = region
        self.domestic.pack_forget()
        self.international.pack_forget()
        (self.domestic if region == 'china' else self.international).pack(fill="both", expand=True)
        self.switch_button.configure(text="其他赞助方式" if region == 'china' else "微信支付")
        self.message.set("")

    def switch_region(self):
        self.show_region('international' if self.current_view == 'china' else 'china')

    def _language_changed(self):
        if hasattr(self, 'switch_button'):
            self.show_region('china' if i18n.language() == 'zh' else 'international')

    def open_kofi(self):
        try:
            opened = webbrowser.open_new_tab(donations.KOFI_URL)
        except (OSError, webbrowser.Error):
            opened = False
        self.message.set("" if opened else "无法打开浏览器，请稍后重试。")

    def open_qr(self, key):
        if key not in self.qr_images:
            return
        code = next(code for code in donations.QR_CODES if code.key == key)
        owner = self.winfo_toplevel()
        window, created = single_window(owner, f"_qr_{key}_window", f"CPlus Cleaner · {code.name}")
        if not created:
            return window
        window.withdraw()
        window.configure(bg=WHITE)
        window.transient(owner)
        window.resizable(False, False)
        image = fit_image(self.qr_images[key], min(760, self.winfo_screenwidth()-100),
                          min(760, self.winfo_screenheight()-180))
        window.qr_image = image
        tk.Label(window, image=image, bg=WHITE).pack(padx=20, pady=(18, 12))
        footer = tk.Frame(window, bg=WHITE)
        footer.pack(pady=(0, 15))
        self.button(footer, "保存图片", lambda: self.save_qr(key), width=130, height=36).pack(side="left")
        self.button(footer, "关闭", window.destroy, width=110, height=36, light=True).pack(side="left", padx=(10, 0))
        window.bind("<Escape>", lambda event: window.destroy())
        window.update_idletasks()
        width, height = window.winfo_reqwidth(), window.winfo_reqheight()
        x = max(0, min(owner.winfo_rootx()+(owner.winfo_width()-width)//2, window.winfo_screenwidth()-width))
        y = max(0, min(owner.winfo_rooty()+(owner.winfo_height()-height)//2, window.winfo_screenheight()-height-50))
        window.geometry(f"{width}x{height}+{x}+{y}")
        window.deiconify()
        window.lift()
        window.focus_force()
        return window

    def save_qr(self, key):
        code = next(code for code in donations.QR_CODES if code.key == key)
        try:
            destination = filedialog.asksaveasfilename(
                parent=self.winfo_toplevel(), title="保存收款二维码", defaultextension=".png",
                initialfile=f"CPlusCleaner-{code.key}.png", filetypes=[("PNG image", "*.png")])
            if destination:
                shutil.copyfile(core.APP_DIR/'assets'/code.asset, destination)
            self.message.set("")
        except (OSError, tk.TclError):
            self.message.set("无法保存二维码，请选择其他位置。")

# Purpose: Bilingual donation dialog, QR previews and the public Ko-fi link
