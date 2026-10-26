"""Tk presentation adapters; model variables and native matching data stay raw.

Only UI modules import this facade. No global Tk monkey-patching is performed.
"""
import tkinter as native
from tkinter import *  # Keep the ordinary Tk API for non-text widgets.
from tkinter import ttk as native_ttk, messagebox as native_messagebox, filedialog as native_filedialog

from cplus_cleaner.localization.i18n import tr, register
from tkinter import font as tkfont


class DisplayOptions:
    """Retain source captions so existing controls can switch both ways."""
    def __init__(self, master=None, cnf=None, **kwargs):
        self._source_options = {}
        self._display_binding = None
        options = dict(cnf or {}, **kwargs)
        textvariable = options.pop('textvariable', None)
        super().__init__(master, **self._options_for_display(options))
        register(self)
        if textvariable is not None:
            self._bind_display_variable(textvariable)
        self.bind('<Destroy>', self._release_display, add='+')

    def _options_for_display(self, options):
        result = dict(options)
        for key in ('text', 'label'):
            if key in result:
                self._source_options[key] = result[key]
                result[key] = self._display_text(result[key])
        return result

    def _display_text(self, value):
        text = tr(value)
        if getattr(self, '_fit_width', None) is not None:
            metrics = tkfont.Font(self, font=self.cget('font'))
            shortened = text
            while shortened and metrics.measure(shortened + '…') > self._fit_width:
                shortened = shortened[:-1]
            return text if shortened == text else shortened + '…'
        return text

    def fit_text(self, source, width):
        self._fit_width = max(10, width)
        self.configure(text=source)

    def _bind_display_variable(self, source):
        self._unbind_display_variable()
        if not isinstance(source, native.Variable):
            source = native.StringVar(self, name=str(source))
        proxy = native.StringVar(self, value=tr(source.get()))
        def update(*args):
            proxy.set(tr(source.get()))
        token = source.trace_add('write', update)
        self._display_binding = source, proxy, token
        super().configure(textvariable=proxy)

    def _unbind_display_variable(self):
        if self._display_binding:
            source, _, token = self._display_binding
            source.trace_remove('write', token)
            self._display_binding = None

    def _release_display(self, event):
        if event.widget is self:
            self._unbind_display_variable()

    def configure(self, cnf=None, **kwargs):
        if cnf is not None and not isinstance(cnf, dict):
            return super().configure(cnf, **kwargs)
        options = dict(cnf or {}, **kwargs)
        source = options.pop('textvariable', None)
        result = super().configure(self._options_for_display(options))
        if source is not None:
            self._bind_display_variable(source)
        return result

    config = configure

    def _language_changed(self):
        super().configure({key: self._display_text(value) for key, value in self._source_options.items()})
        if self._display_binding:
            source, proxy, _ = self._display_binding
            proxy.set(tr(source.get()))


class Label(DisplayOptions, native.Label):
    pass


class Button(DisplayOptions, native.Button):
    pass


class Checkbutton(DisplayOptions, native.Checkbutton):
    pass


class Radiobutton(DisplayOptions, native.Radiobutton):
    pass


class LabelFrame(DisplayOptions, native.LabelFrame):
    pass


class Canvas(native.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._source_items = {}
        register(self)

    def create_text(self, *args, **kwargs):
        source = kwargs.get('text', '')
        kwargs['text'] = tr(source)
        item = super().create_text(*args, **kwargs)
        self._source_items[item] = source
        return item

    def itemconfigure(self, tagOrId, cnf=None, **kwargs):
        if cnf is not None and not isinstance(cnf, dict):
            return super().itemconfigure(tagOrId, cnf, **kwargs)
        options = dict(cnf or {}, **kwargs)
        if 'text' in options:
            for item in self.find_withtag(tagOrId):
                self._source_items[item] = options['text']
            options['text'] = tr(options['text'])
        return super().itemconfigure(tagOrId, options)

    itemconfig = itemconfigure

    def delete(self, *args):
        for tag in args:
            for item in self.find_withtag(tag):
                self._source_items.pop(item, None)
        return super().delete(*args)

    def _language_changed(self):
        for item, source in self._source_items.items():
            native.Canvas.itemconfigure(self, item, text=tr(source))


class WindowTitle:
    def title(self, string=None):
        if string is None:
            return super().title()
        self._source_title = string
        register(self)
        return super().title(tr(string))

    wm_title = title

    def _language_changed(self):
        if hasattr(self, '_source_title'):
            super().title(tr(self._source_title))
        refresh = getattr(self, 'reload_records', None)
        if refresh:
            refresh()


class Tk(WindowTitle, native.Tk):
    pass


class Toplevel(WindowTitle, native.Toplevel):
    pass


class Menu(native.Menu):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._source_entries = {}
        register(self)

    def add(self, itemType, cnf=None, **kwargs):
        options = dict(cnf or {}, **kwargs)
        raw = options.get('label')
        if raw is not None:
            options['label'] = tr(raw)
        result = super().add(itemType, options)
        if raw is not None:
            self._source_entries[self.index('end')] = raw
        return result

    def entryconfigure(self, index, cnf=None, **kwargs):
        if cnf is not None and not isinstance(cnf, dict):
            return super().entryconfigure(index, cnf, **kwargs)
        options = dict(cnf or {}, **kwargs)
        if 'label' in options:
            self._source_entries[self.index(index)] = options['label']
            options['label'] = tr(options['label'])
        return super().entryconfigure(index, options)

    entryconfig = entryconfigure

    def delete(self, index1, index2=None):
        first = self.index(index1)
        last = self.index(index2) if index2 is not None else first
        result = super().delete(index1, index2)
        if first is not None and last is not None:
            self._source_entries = {i if i < first else i-(last-first+1): text
                                    for i, text in self._source_entries.items() if i < first or i > last}
        return result

    def _language_changed(self):
        for index, label in self._source_entries.items():
            native.Menu.entryconfigure(self, index, label=tr(label))


class TtkLabel(DisplayOptions, native_ttk.Label):
    pass


class TtkButton(DisplayOptions, native_ttk.Button):
    pass


class TtkCheckbutton(DisplayOptions, native_ttk.Checkbutton):
    pass


class TtkLabelframe(DisplayOptions, native_ttk.Labelframe):
    pass


class Combobox(native_ttk.Combobox):
    """Translate choices without changing the Chinese values used by filters."""
    def __init__(self, master=None, **kwargs):
        self._source_values = tuple(kwargs.pop('values', ()))
        source = kwargs.pop('textvariable', None)
        super().__init__(master, **kwargs)
        self._source_variable = source if source is not None else native.StringVar(self)
        self._display_variable = native.StringVar(self)
        self._syncing = False
        super().configure(textvariable=self._display_variable)
        self._source_trace = self._source_variable.trace_add('write', self._from_source)
        self._display_trace = self._display_variable.trace_add('write', self._from_display)
        self.bind('<Destroy>', self._release, add='+')
        register(self)
        self._language_changed()

    def _from_source(self, *args):
        if not self._syncing:
            self._syncing = True
            try:
                value = self._source_variable.get()
                source = next((item for item in self._source_values if str(item) == value), value)
                self._display_variable.set(tr(source))
            finally:
                self._syncing = False

    def _from_display(self, *args):
        if not self._syncing:
            self._syncing = True
            try:
                value = self._display_variable.get()
                raw = next((source for source in self._source_values if tr(source) == value), value)
                self._source_variable.set(raw)
            finally:
                self._syncing = False

    def _release(self, event):
        if event.widget is self:
            self._source_variable.trace_remove('write', self._source_trace)
            self._display_variable.trace_remove('write', self._display_trace)

    def configure(self, cnf=None, **kwargs):
        if cnf is not None and not isinstance(cnf, dict):
            return super().configure(cnf, **kwargs)
        options = dict(cnf or {}, **kwargs)
        if 'values' in options:
            self._source_values = tuple(options['values'])
            options['values'] = tuple(tr(value) for value in self._source_values)
        return super().configure(options)

    config = configure

    def _language_changed(self):
        super().configure(values=tuple(tr(value) for value in self._source_values))
        self._from_source()


class Treeview(native_ttk.Treeview):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._source_headings, self._source_rows = {}, {}
        register(self)

    def heading(self, column, option=None, **kwargs):
        if 'text' in kwargs:
            self._source_headings[column] = kwargs['text']
            kwargs['text'] = tr(kwargs['text'])
        return super().heading(column, option, **kwargs)

    @staticmethod
    def _display_row(options):
        return {key: tuple(tr(value) for value in values) if key == 'values' else tr(values)
                for key, values in options.items()}

    def insert(self, parent, index, iid=None, **kwargs):
        item = super().insert(parent, index, iid, **self._display_row(kwargs))
        self._source_rows[item] = {key: value for key, value in kwargs.items() if key in ('text', 'values')}
        return item

    def item(self, item, option=None, **kwargs):
        if kwargs:
            self._source_rows.setdefault(item, {}).update({key: value for key, value in kwargs.items() if key in ('text', 'values')})
        result = super().item(item, option, **self._display_row(kwargs))
        if not kwargs:
            raw = self._source_rows.get(item, {})
            if option in raw:
                return raw[option]
            if option is None and isinstance(result, dict):
                result.update(raw)
        return result

    def set(self, item, column=None, value=None):
        if value is not None:
            columns = self.cget('columns')
            index = int(column[1:])-1 if str(column).startswith('#') else tuple(columns).index(column)
            row = self._source_rows.setdefault(item, {})
            values = list(row.get('values', super().item(item, 'values')))
            values += [''] * max(0, index+1-len(values))
            values[index] = value
            row['values'] = tuple(values)
            value = tr(value)
        return super().set(item, column, value)

    def delete(self, *items):
        def release(item):
            for child in self.get_children(item):
                release(child)
            self._source_rows.pop(item, None)
        for item in items:
            release(item)
        return super().delete(*items)

    def _language_changed(self):
        for column, title in self._source_headings.items():
            super().heading(column, text=tr(title))
        for item, options in self._source_rows.items():
            super().item(item, **self._display_row(options))


class Text(native.Text):
    # Read-only detail panes are rendered from their source content on switching.
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._source_content = ''
        register(self)

    def insert(self, index, chars, *args):
        self._source_content += str(chars)
        return super().insert(index, tr(chars), *args)

    def delete(self, index1, index2=None):
        self._source_content = ''
        return super().delete(index1, index2)

    def _language_changed(self):
        if str(self.cget('state')) == 'disabled':
            super().configure(state='normal')
            super().delete('1.0', 'end')
            super().insert('1.0', tr(self._source_content))
            super().configure(state='disabled')


class Namespace:
    def __init__(self, source, **overrides):
        self.source, self.overrides = source, overrides

    def __getattr__(self, name):
        return self.overrides.get(name, getattr(self.source, name))


ttk = Namespace(native_ttk, Label=TtkLabel, Button=TtkButton, Checkbutton=TtkCheckbutton,
                Labelframe=TtkLabelframe, LabelFrame=TtkLabelframe, Combobox=Combobox, Treeview=Treeview)


class Dialogs:
    def __init__(self, source):
        self.source = source

    def __getattr__(self, name):
        function = getattr(self.source, name)
        def show(*args, **kwargs):
            options = dict(kwargs)
            for key in ('title', 'message', 'detail'):
                if key in options:
                    options[key] = tr(options[key])
            return function(*(tr(arg) for arg in args), **options)
        return show


messagebox = Dialogs(native_messagebox)
filedialog = Dialogs(native_filedialog)
