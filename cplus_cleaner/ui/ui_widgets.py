"""Shared checkmark assets and reusable desktop windows."""
from pathlib import Path
import re
from cplus_cleaner.localization import localized_tk as tk
from cplus_cleaner.localization.i18n import tr
from cplus_cleaner.common.runtime import resource_root


MINT = "#46d6a0"
ASSETS = resource_root() / "assets"


def scroll_canvas(canvas, content, delta):
    """A short result list stays pinned to the top instead of drifting down."""
    if content.winfo_reqheight() > canvas.winfo_height():
        canvas.yview_scroll(int(-delta / 120), 'units')
    else:
        canvas.yview_moveto(0)
    return 'break'


class MetricLabel(tk.Canvas):
    """Statistics with bold red numbers; wording retains its original font."""
    def __init__(self, master, text='', textvariable=None, fg='#263b40',
                 font=('Microsoft YaHei UI', 10), anchor='w', numbercolor='#ff6269',
                 padx=1, pady=1, wraplength=0, **kwargs):
        kwargs.setdefault('bg', '#ffffff')
        super().__init__(master, highlightthickness=0, bd=0, takefocus=False, **kwargs)
        self._caption, self._variable, self._trace = str(text), textvariable, None
        self._foreground, self._font, self._anchor = fg, font, anchor
        self._numbercolor, self._padx, self._pady = numbercolor, padx, pady
        self._wraplength = wraplength
        self.bind('<Configure>', lambda event: self._paint())
        self.bind('<Destroy>', self._release, add='+')
        if textvariable is not None:
            self._trace = textvariable.trace_add('write', self._changed)
            self._caption = textvariable.get()
        self._changed()

    def _release(self, event):
        if event.widget is self and self._trace:
            try:
                self._variable.trace_remove('write', self._trace)
            except tk.TclError:
                pass
            self._trace = None

    def _measure(self, text):
        return sum(int(self.tk.call('font', 'measure',
            self._numberfont if part[0].isdigit() else self._font, part))
            for part in re.split(r'(\d[\d,]*(?:\.\d+)?)', text) if part)

    def _changed(self, *args):
        if self._variable is not None:
            self._caption = self._variable.get()
        actual = self.tk.splitlist(self.tk.call('font', 'actual', self._font))
        options = dict(zip(actual[::2], actual[1::2]))
        styles = ['bold', options['-slant']]
        for style in ('underline', 'overstrike'):
            if int(options['-'+style]):
                styles.append(style)
        self._numberfont = (options['-family'], int(options['-size']), *styles)
        self._lines = []
        for line in tr(self._caption).split('\n'):
            current = ''
            for char in line:
                if self._wraplength and current and self._measure(current+char) > self._wraplength:
                    self._lines.append(current)
                    current = ''
                current += char
            self._lines.append(current)
        self._lineheight = max(int(self.tk.call('font', 'metrics', font, '-linespace'))
                               for font in (self._font, self._numberfont))
        self._natural_width = max((self._measure(line) for line in self._lines), default=0)
        super().configure(width=max(1, self._natural_width+2*self._padx),
                          height=self._lineheight*len(self._lines)+2*self._pady)
        self._paint()

    def _language_changed(self):
        self._changed()

    def _paint(self):
        self.delete('all')
        width = self.winfo_width() if self.winfo_width() > 1 else self._natural_width+2*self._padx
        for row, line in enumerate(self._lines):
            x = self._padx
            if self._anchor in ('e', 'ne', 'se'):
                x = width-self._measure(line)-self._padx
            elif self._anchor in ('center', 'n', 's'):
                x = (width-self._measure(line))/2
            for part in re.split(r'(\d[\d,]*(?:\.\d+)?)', line):
                if not part:
                    continue
                numeric = part[0].isdigit()
                self.create_text(x, self._pady+row*self._lineheight, text=part, anchor='nw',
                    font=self._numberfont if numeric else self._font,
                    fill=self._numbercolor if numeric else self._foreground,
                    tags=('metric-number' if numeric else 'metric-word',))
                x += self._measure(part)

    def configure(self, cnf=None, **kwargs):
        if isinstance(cnf, dict):
            kwargs = dict(cnf, **kwargs)
        changed = False
        for option, attr in [('text', '_caption'), ('fg', '_foreground'), ('foreground', '_foreground'),
                             ('font', '_font'), ('anchor', '_anchor'), ('wraplength', '_wraplength')]:
            if option in kwargs:
                setattr(self, attr, kwargs.pop(option))
                changed = True
        result = super().configure(**kwargs)
        if changed:
            self._changed()
        return result

    config = configure

    def cget(self, key):
        values = {'text': self._caption, 'textvariable': str(self._variable) if self._variable is not None else '',
                  'fg': self._foreground, 'foreground': self._foreground, 'font': self._font}
        return values[key] if key in values else super().cget(key)

    def keys(self):
        return list(super().keys()) + ['text', 'textvariable', 'fg', 'foreground', 'font']


def check_images(master):
    """One set per Tcl interpreter, using the large-file page's exact artwork."""
    root = master._root()
    cached = getattr(root, "_shared_check_images", None)
    if cached is not None:
        return cached
    images = {}
    for state in ("off", "on", "mixed"):
        path = ASSETS / f"check-{state}.png"
        if path.is_file():
            icon = tk.PhotoImage(master=root, file=str(path))
        else:
            icon = tk.PhotoImage(master=root, width=16, height=16)
            icon.put(MINT if state != "off" else "#92b5bc", to=(0, 0, 16, 16))
            icon.put(MINT if state != "off" else "#ffffff", to=(1, 1, 15, 15))
            if state == "mixed":
                icon.put("#ffffff", to=(4, 7, 12, 9))
            elif state == "on":
                for x, y in ((3, 8), (4, 9), (5, 10), (6, 9), (7, 8), (8, 7), (9, 6), (10, 5), (11, 4)):
                    icon.put("#ffffff", to=(x, y, x+2, y+2))
        images[state] = icon
    root._shared_check_images = images

    def release(event):
        if event.widget is root:
            # Release Tcl image handles on the UI thread, including when tests
            # keep Python widget references after destroying their interpreter.
            for icon in images.values():
                icon.__del__()
                icon.name = None
            root._shared_check_images = None
    root.bind("<Destroy>", release, add="+")
    return images


class Checkbutton(tk.Checkbutton):
    """Native variable/keyboard behavior with the shared bitmap indicator."""
    def __init__(self, master, **kwargs):
        images = check_images(master)
        background = kwargs.get("bg", kwargs.get("background"))
        if background is None:
            try:
                background = master.cget("background")
            except tk.TclError:
                background = "#ffffff"
        options = dict(indicatoron=False, image=images["off"], selectimage=images["on"],
                       tristateimage=images["mixed"], compound="left", relief="flat",
                       offrelief="flat", overrelief="flat", bd=0, highlightthickness=0,
                       takefocus=True, cursor="hand2", padx=4, pady=3,
                       bg=background, activebackground=background)
        options.update(kwargs)
        options["selectcolor"] = background
        super().__init__(master, **options)


def single_window(owner, attribute, title):
    """Return (window, created); repeated opens raise and refresh one window."""
    window = getattr(owner, attribute, None)
    if window is not None:
        try:
            exists = window.winfo_exists()
        except tk.TclError:
            exists = False
        if exists:
            # A refresh/focus error must never create a second live window.
            window.deiconify()
            window.lift()
            window.focus_force()
            refresh = getattr(window, "reload_records", None)
            if refresh:
                refresh()
            return window, False
    window = tk.Toplevel(owner)
    setattr(owner, attribute, window)
    window.title(title)

    def release(event):
        if event.widget is window:
            window.reload_records = None
            if getattr(owner, attribute, None) is window:
                setattr(owner, attribute, None)
    window.bind("<Destroy>", release, add="+")
    return window, True

# Purpose: Shared buttons, selection, scrolling and icons
