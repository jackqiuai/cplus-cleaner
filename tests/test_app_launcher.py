"""Every supported launcher targets the sole current desktop."""
import unittest
from unittest.mock import patch

import app
from cplus_cleaner.ui import desktop


class AppLauncherTests(unittest.TestCase):
    def test_launcher_routes_foreground_and_background_through_single_instance_guard(self):
        with patch('cplus_cleaner.application.single_instance.run') as run:
            app.main([])
            run.assert_called_once_with(background=False)
            run.reset_mock()
            app.main(['--background'])
            run.assert_called_once_with(background=True)

    def test_launcher_uses_command_line_when_no_arguments_are_supplied(self):
        with patch('sys.argv', ['app.py', '--background']), patch('cplus_cleaner.application.single_instance.run') as run:
            app.main()
            run.assert_called_once_with(background=True)

    def test_retired_window_and_page_classes_are_not_exposed(self):
        for name in ('Application', 'FilePage', 'CDrivePage', 'ToolsPage', 'MigrationPage', 'UninstallPage', 'PopupPage'):
            self.assertFalse(hasattr(app, name), name)
        self.assertFalse(hasattr(desktop, 'legacy'))
        self.assertFalse(hasattr(desktop.DesktopApplication, 'show_result'))

    def test_component_helper_dispatch_does_not_start_a_desktop(self):
        from cplus_cleaner.features.system import system_analysis_helper
        with patch.object(system_analysis_helper, 'helper_main') as analyze, \
                patch('cplus_cleaner.application.single_instance.run') as run:
            app.main(['--component-analysis-job', 'fixture-job'])
        analyze.assert_called_once_with('fixture-job')
        run.assert_not_called()

    def test_component_helper_requires_exactly_one_job_argument(self):
        with self.assertRaises(SystemExit) as error:
            app.main(['--component-analysis-job'])
        self.assertEqual(error.exception.code, 2)


if __name__ == '__main__':
    unittest.main()

# Purpose: Regression tests for app launcher
