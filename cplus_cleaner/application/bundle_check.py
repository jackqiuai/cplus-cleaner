"""Read-only bundle check using isolated settings and disabled background work."""
from contextlib import contextmanager, ExitStack
import json
from pathlib import Path
import tempfile
import time
import traceback


@contextmanager
def temporary_attribute(owner, name, value):
    previous = getattr(owner, name)
    setattr(owner, name, value)
    try:
        yield
    finally:
        setattr(owner, name, previous)


def check():
    from cplus_cleaner.common import core, runtime
    from cplus_cleaner.application import preferences, updates
    from cplus_cleaner.localization import i18n
    from cplus_cleaner.features.system import cdrive_system
    from cplus_cleaner.ui import desktop
    from cplus_cleaner.ui.ui_widgets import ASSETS
    assert ASSETS == core.APP_DIR/'assets'
    assert (core.APP_DIR/'LICENSE').is_file()
    for directory in ('assets', 'rules', 'locales'):
        for path in (core.APP_DIR/directory).rglob('*.json'):
            json.loads(path.read_text(encoding='utf-8'))
    images = json.loads((ASSETS/'runtime-assets.json').read_text(encoding='utf-8'))['files']
    for image in images:
        assert (ASSETS/image['path']).is_file(), image['path']
    errors = []
    pages = []
    with tempfile.TemporaryDirectory(prefix='cplus-cleaner-check-') as directory, ExitStack() as stack:
        stack.enter_context(temporary_attribute(core, 'DATA_DIR', Path(directory)/'CCLEANNER'))
        stack.enter_context(temporary_attribute(preferences, 'read_autostart', lambda: False))
        stack.enter_context(temporary_attribute(updates, 'check_latest', lambda current:
            updates.ReleaseInfo(current, False, updates.RELEASES_URL)))
        stack.enter_context(temporary_attribute(cdrive_system, 'read_system_items', lambda: []))
        stack.enter_context(temporary_attribute(desktop, 'disk_snapshot',
            lambda: dict(root='C:\\', used=50, free=50, total=100)))
        app = desktop.DesktopApplication()
        app.withdraw()
        app.start = lambda *args, **kwargs: False
        app.report_callback_exception = lambda *args: errors.append(''.join(traceback.format_exception(*args)))
        try:
            for image in images:
                if image['path'].endswith('.png'):
                    photo = desktop.tk.PhotoImage(master=app, file=str(ASSETS/image['path']))
                    assert photo.width() > 0
            for key, _, _ in app.NAV:
                app.navigate(key, use_existing=True)
                app.update_idletasks()
                assert app.current_page == key and app.notebook.select() == str(app.views[key]), key
                pages.append(key)
            assert not app.views['popup'].scanning
            assert not app.views['popup']._loaded
            assert not app.busy
            donation = app.open_donation()
            donation.withdraw()
            assert set(donation.panel.qr_images) == {'wechat'}
            for language in ('zh', 'en'):
                i18n.set_language(language)
                app.update_language_layout()
                app.update_idletasks()
                assert donation.panel.current_view == ('china' if language == 'zh' else 'international')
            assert donation.panel.coffee_button.caption == 'Buy me a coffee'
            donation.destroy()
            about = app.open_about()
            about.withdraw()
            assert about.panel.logo_image is not None
            assert about.panel.logo_image.width() > 0
            assert about.panel.version_label.cget('text') == f'v{preferences.VERSION}'
            assert app.open_about() is about
            about.withdraw()
            for language, title, caption in (
                    ('zh', '关于Cplus Cleaner', '给项目点个 Star'),
                    ('en', 'About Cplus Cleaner', 'Give me a star')):
                i18n.set_language(language)
                app.update_idletasks()
                assert about.title() == title
                assert i18n.tr(about.panel.star_button.caption) == caption
            about.destroy()
            update_window = app.open_updates()
            update_window.withdraw()
            deadline = time.monotonic()+5
            while update_window.panel.checking and time.monotonic() < deadline:
                app.update()
                time.sleep(.01)
            assert not update_window.panel.checking
            assert update_window.panel.latest.get() == f'v{preferences.VERSION}'
            assert i18n.tr(update_window.panel.status.get()) == 'You are up to date.'
            update_window.destroy()
            assert i18n.tr('开始扫描') == 'Scan'
            assert not errors, errors
            command = runtime.launch_command('--background')
            if runtime.frozen():
                assert command[1:] == ['--background']
        finally:
            for timer in app.tk.call('after', 'info'):
                app.tk.call('after', 'cancel', timer)
            app.destroy()
    return dict(frozen=runtime.frozen(), version=preferences.VERSION, pages=pages,
                image_count=len(images), languages=['zh', 'en'], donation_dialog=True, about_dialog=True,
                popup_auto_scan=False, update_dialog=True)


def main(output):
    report = dict(ok=False)
    try:
        report.update(check(), ok=True)
    except Exception:
        report['error'] = traceback.format_exc()
    with Path(output).open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return 0 if report['ok'] else 1

# Purpose: Isolated executable resource and feature-page smoke checks
