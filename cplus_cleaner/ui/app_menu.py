"""Header dropdown styled from the original application's home_menu_list."""
from cplus_cleaner.localization import localized_tk as tk
from cplus_cleaner.localization import i18n
from tkinter import font as tkfont


class HeaderMenu:
    BACKGROUND = "#ffffff"
    HOVER = "#e9f9f4"
    TEXT = "#6c8787"
    WIDTH = 208
    ROW_HEIGHT = 36
    TOP = 13

    def __init__(self, owner, anchor, entries):
        self.owner, self.anchor = owner, anchor
        self.entries = entries  # (caption, callback); None callbacks are informational.
        self.window = None
        self.canvas = None
        self.active = None
        self.width = self.WIDTH
        owner.bind("<Configure>", self._owner_changed, add="+")
        owner.bind("<Unmap>", self._owner_changed, add="+")

    @property
    def visible(self):
        return self.window is not None

    @staticmethod
    def _rounded(canvas, box, radius=4, **options):
        x1, y1, x2, y2 = box
        return canvas.create_polygon(
            x1+radius, y1, x2-radius, y1, x2, y1, x2, y1+radius,
            x2, y2-radius, x2, y2, x2-radius, y2, x1+radius, y2,
            x1, y2, x1, y2-radius, x1, y1+radius, x1, y1,
            smooth=True, splinesteps=16, **options)

    def toggle(self, event=None):
        if self.visible:
            self.close(restore_focus=True)
        else:
            self.show(keyboard=event is not None and getattr(event, "keysym", "") in {"Return", "space"})
        return "break"

    def show(self, *, keyboard=False):
        if self.visible:
            return
        window = self.window = tk.Toplevel(self.owner)
        window.withdraw()
        window.overrideredirect(True)
        window.transient(self.owner)
        window.attributes("-topmost", True)
        transparent = "#ff00fe"
        window.configure(bg=transparent)
        try:
            window.attributes("-transparentcolor", transparent)
        except tk.TclError:
            transparent = "#ffffff"
        metrics = tkfont.Font(self.owner, family="Microsoft YaHei UI", size=-14)
        self.width = max(self.WIDTH, max((metrics.measure(i18n.tr(caption)) for caption, _ in self.entries), default=0)+72)
        height = self.TOP + len(self.entries)*self.ROW_HEIGHT + 12
        self.canvas = tk.Canvas(window, width=self.width, height=height,
                                bg=transparent, bd=0, highlightthickness=0,
                                takefocus=True)
        self.canvas.pack()
        anchor_x = self.anchor.winfo_rootx() + self.anchor.winfo_width()//2
        x = max(0, min(anchor_x - (self.width-26), self.owner.winfo_screenwidth()-self.width))
        y = self.anchor.winfo_rooty() + self.anchor.winfo_height() + 1
        y = min(y, self.owner.winfo_screenheight()-height)
        self._arrow_x = max(17, min(anchor_x-x, self.width-17))
        window.geometry(f"{self.width}x{height}+{x}+{y}")
        self.active = next((i for i, (_, action) in enumerate(self.entries) if action), None) if keyboard else None
        self._paint()
        self.canvas.bind("<Motion>", self._motion)
        self.canvas.bind("<Leave>", lambda event: self._highlight(None))
        window.bind("<Button-1>", self._click)
        window.bind("<Escape>", lambda event: self.close(restore_focus=True))
        window.bind("<Up>", lambda event: self._step(-1))
        window.bind("<Down>", lambda event: self._step(1))
        window.bind("<Return>", lambda event: self.invoke(self.active))
        window.bind("<space>", lambda event: self.invoke(self.active))
        window.bind("<FocusOut>", lambda event: self.close())
        window.deiconify()
        window.lift()
        window.grab_set()
        self.canvas.focus_force()

    def _paint(self):
        canvas = self.canvas
        canvas.delete("all")
        bottom = self.TOP + len(self.entries)*self.ROW_HEIGHT + 4
        for inset, color in ((0, "#f4f5f6"), (2, "#eaedef"), (4, "#e1e5e8")):
            self._rounded(canvas, (inset, 8+inset/2, self.width-inset, bottom+9-inset), fill=color, outline="")
        self._rounded(canvas, (6, 9, self.width-6, bottom),
                      fill=self.BACKGROUND, outline="#dadddf", width=1)
        ax = self._arrow_x
        canvas.create_polygon(ax-7, 9, ax, 2, ax+7, 9, fill="#ffffff", outline="#dadddf")
        canvas.create_line(ax-6, 9, ax+6, 9, fill="#ffffff")
        for i, (caption, action) in enumerate(self.entries):
            top = self.TOP + i*self.ROW_HEIGHT
            if i == self.active and action:
                canvas.create_rectangle(7, top, self.width-7, top+self.ROW_HEIGHT, fill=self.HOVER, outline="")
            if action is None:
                canvas.create_line(18, top, self.width-18, top, fill="#edf2f2")
            glyph = {"设置": "⚙", "问题反馈": "✉", "捐赠": "♡", "退出软件": "⇥"}.get(caption, "ⓘ")
            canvas.create_text(28, top+self.ROW_HEIGHT//2, text=glyph,
                               font=("Segoe UI Symbol", -17), fill="#719397")
            canvas.create_text(47, top+self.ROW_HEIGHT//2, anchor="w", text=caption,
                               font=("Microsoft YaHei UI", -14),
                               fill=self.TEXT if action else "#a7b3b7")

    def _highlight(self, index):
        if index != self.active:
            self.active = index
            self._paint()
        self.canvas.configure(cursor="hand2" if index is not None else "")

    def _row_at(self, x, y):
        index = (y-self.TOP)//self.ROW_HEIGHT
        if 7 <= x < self.width-7 and self.TOP <= y and 0 <= index < len(self.entries):
            return index
        return None

    def _motion(self, event):
        index = self._row_at(event.x, event.y)
        self._highlight(index if index is not None and self.entries[index][1] else None)

    def _click(self, event):
        x = event.x_root-self.window.winfo_rootx()
        y = event.y_root-self.window.winfo_rooty()
        if not (6 <= x < self.width-6 and 2 <= y < self.canvas.winfo_height()-9):
            self.close(restore_focus=True)
        else:
            self.invoke(self._row_at(x, y))
        return "break"

    def _step(self, direction):
        enabled = [i for i, (_, action) in enumerate(self.entries) if action]
        if enabled:
            current = enabled.index(self.active) if self.active in enabled else (-1 if direction > 0 else 0)
            self._highlight(enabled[(current+direction) % len(enabled)])
        return "break"

    def invoke(self, index):
        if index is not None and self.entries[index][1]:
            action = self.entries[index][1]
            self.close(restore_focus=True)
            action()
        return "break"

    def _owner_changed(self, event):
        if event.widget is self.owner:
            self.close()

    def close(self, *, restore_focus=False):
        window, self.window = self.window, None
        self.canvas = None
        self.active = None
        if window is not None:
            window.grab_release()
            window.destroy()
            if restore_focus and self.anchor.winfo_exists():
                self.anchor.focus_set()

# Purpose: Application dropdown menu
