import os
from pathlib import Path
import subprocess
import sys
import time
import tkinter as tk
import unittest
import uuid

from cplus_cleaner.application.single_instance import SingleInstance


@unittest.skipUnless(os.name == 'nt', 'Windows named kernel objects')
class SingleInstanceTests(unittest.TestCase):
    def setUp(self):
        self.name = 'Local\\CCCLEANER.Test.'+uuid.uuid4().hex

    def test_repeated_process_launch_exits_before_desktop_import_and_signals_primary(self):
        with SingleInstance(self.name) as primary:
            self.assertTrue(primary.primary)
            script = "import sys; from cplus_cleaner.application.single_instance import run; run(name=sys.argv[1]); print('cplus_cleaner.ui.desktop' in sys.modules)"
            children = [subprocess.Popen([sys.executable, '-c', script, self.name],
                cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, creationflags=subprocess.CREATE_NO_WINDOW) for _ in range(6)]
            for child in children:
                out, err = child.communicate(timeout=15)
                self.assertEqual(child.returncode, 0, err)
                self.assertEqual(out.strip(), 'False')
            self.assertTrue(primary.activation_pending())
            self.assertFalse(primary.activation_pending())

    def test_exit_releases_guard_and_background_duplicate_does_not_restore(self):
        with SingleInstance(self.name) as first:
            with SingleInstance(self.name, activate=False) as second:
                self.assertFalse(second.primary)
                self.assertFalse(first.activation_pending())
        with SingleInstance(self.name) as next_launch:
            self.assertTrue(next_launch.primary)

    def test_pending_activation_survives_startup_and_restores_hidden_window_once(self):
        with SingleInstance(self.name) as primary:
            with SingleInstance(self.name):
                pass
            root = tk.Tk()
            root.withdraw()
            calls = []
            def restore():
                calls.append(True)
                root.deiconify()
            try:
                primary.attach(root, restore)
                deadline = time.monotonic()+2
                while not calls and time.monotonic()<deadline:
                    root.update()
                    time.sleep(.01)
                self.assertEqual(calls, [True])
                self.assertEqual(root.state(), 'normal')
                self.assertFalse(primary.activation_pending())
            finally:
                root.destroy()
            with SingleInstance(self.name) as released:
                self.assertTrue(released.primary)

# Purpose: Regression tests for single-instance launch and activation
