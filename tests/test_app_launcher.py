"""Every supported launcher targets the sole current desktop."""
import unittest
from unittest.mock import patch

import app
import desktop


class AppLauncherTests(unittest.TestCase):
    def test_launcher_routes_foreground_and_background_through_single_instance_guard(self):
        with patch('single_instance.run') as run:
            app.main([])
            run.assert_called_once_with(background=False)
            run.reset_mock()
            app.main(['--background'])
            run.assert_called_once_with(background=True)

    def test_launcher_uses_command_line_when_no_arguments_are_supplied(self):
        with patch('sys.argv', ['app.py', '--background']), patch('single_instance.run') as run:
            app.main()
            run.assert_called_once_with(background=True)

    def test_retired_window_and_page_classes_are_not_exposed(self):
        for name in ('Application', 'FilePage', 'CDrivePage', 'ToolsPage', 'MigrationPage', 'UninstallPage', 'PopupPage'):
            self.assertFalse(hasattr(app, name), name)
        self.assertFalse(hasattr(desktop, 'legacy'))
        self.assertFalse(hasattr(desktop.DesktopApplication, 'show_result'))


if __name__ == '__main__':
    unittest.main()
