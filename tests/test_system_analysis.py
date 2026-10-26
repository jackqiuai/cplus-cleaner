"""DISM streams and UAC are synthetic; tests never service the host system."""
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cplus_cleaner.common import core
from cplus_cleaner.features.system import cdrive_system
from cplus_cleaner.features.system import system_analysis as analysis
from cplus_cleaner.features.uninstall import uninstall_windows


class SystemAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=core.APP_DIR.parent)
        self.base = Path(self.directory.name)
        self.patcher = patch.object(core, 'DATA_DIR', self.base/'CCLEANNER')
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.directory.cleanup()

    def test_percent_stream_handles_carriage_returns_utf16_and_waiting_without_percent(self):
        progress = Mock()
        last = analysis.emit_output(b'Preparing DISM\r\n', progress, None)
        self.assertIsNone(last)
        progress.assert_not_called()
        last = analysis.emit_output(b'[=== 12.5% ===]\r', progress, last)
        self.assertEqual(last, 12.5)
        last = analysis.emit_output('[==== 63.0% ====]\r'.encode('utf-16-le'), progress, last)
        self.assertEqual(last, 63)
        analysis.emit_output(b'12.5%', progress, last)
        self.assertEqual([call.args[0]['percent'] for call in progress.call_args_list], [12.5, 63])

    def test_streamed_process_is_hidden_returns_report_and_preserves_error_code(self):
        report = b'Component Store information:\r\nActual Size : 4.75 GB\r\n'
        def launch(command, **kwargs):
            writer = kwargs['stdout']
            states = iter([None, None, 0])
            process = Mock(returncode=5)
            def poll():
                state = next(states)
                writer.write(b'[==== 42.0% ====]\r' if state is None else report)
                writer.flush()
                return state
            process.poll.side_effect = poll
            return process
        progress = Mock()
        with patch.object(analysis.subprocess, 'Popen', side_effect=launch) as popen, patch.object(analysis.time, 'sleep'):
            result = analysis.run_streamed(['fixture-Dism.exe', '/AnalyzeComponentStore'], progress)
        self.assertEqual(result.returncode, 5)
        self.assertIn(report, result.stdout)
        self.assertTrue(any(call.args[0].get('percent') == 42 for call in progress.call_args_list))
        self.assertFalse(popen.call_args.kwargs['shell'])
        self.assertEqual(popen.call_args.kwargs['creationflags'], 0x08000000)
        self.assertEqual(list(core.DATA_DIR.iterdir()), [])
        self.assertNotIn('42.0%', analysis.report_text(result.stdout.decode()))
        self.assertIn('Actual Size : 4.75 GB', analysis.report_text(result.stdout.decode()))

    def test_helper_runs_only_fixed_analysis_and_rejects_non_job_paths(self):
        core.DATA_DIR.mkdir()
        job = core.DATA_DIR/'component-analysis-fixture'
        job.mkdir()
        def run(command, **kwargs):
            kwargs['stdout'].write(b'Actual Size : 4.75 GB')
            return subprocess.CompletedProcess(command, 0)
        with patch.object(cdrive_system, '_is_admin', return_value=True), \
             patch.object(cdrive_system, '_system_directory', return_value=r'C:\Windows\System32'), \
             patch.object(analysis.subprocess, 'run', side_effect=run) as execute:
            analysis.helper_main(str(job))
        self.assertEqual(execute.call_args.args[0], [r'C:\Windows\System32\Dism.exe', '/Online', '/Cleanup-Image', '/AnalyzeComponentStore'])
        self.assertTrue(json.loads((job/'result.json').read_text(encoding='utf-8'))['ok'])
        with self.assertRaises(ValueError):
            analysis.helper_main(str(job))
        with self.assertRaises(ValueError):
            analysis.helper_main(str(self.base))

    def test_elevation_is_explicit_and_only_analysis_is_eligible(self):
        progress = Mock()
        with patch.object(cdrive_system, '_is_admin', return_value=False), \
             patch.object(analysis, 'run_elevated', return_value=dict(ok=True, output='Actual Size : 4.75 GB', message='组件分析已完成。')) as elevated:
            denied = cdrive_system.run_action('analyze_component_store', progress=progress)
            self.assertFalse(denied['ok'])
            elevated.assert_not_called()
            result = cdrive_system.run_action('analyze_component_store', progress=progress, allow_elevation=True)
            self.assertTrue(result['ok'])
            self.assertIn('4.75 GB', result['output'])
            elevated.assert_called_once_with(progress)
            self.assertFalse(cdrive_system.run_action('cleanup_component_store', allow_elevation=True)['ok'])
            self.assertEqual(elevated.call_count, 1)

    def test_cancelled_uac_does_not_start_dism_or_leave_temp_jobs(self):
        error = OSError('cancelled')
        error.winerror = 1223
        progress = Mock()
        with patch.object(uninstall_windows, '_kernel'), \
             patch.object(uninstall_windows, '_elevated', side_effect=error) as launch:
            result = analysis.run_elevated(progress)
        self.assertFalse(result['ok'])
        self.assertTrue(result['cancelled'])
        self.assertEqual(launch.call_args.kwargs['show'], 0)
        self.assertEqual(list(core.DATA_DIR.iterdir()), [])

    def test_elevated_helper_result_and_real_progress_are_read_before_handle_closed(self):
        kernel = Mock()
        process = SimpleNamespace(kernel=kernel, handle=123, close=Mock())
        def launch(native, executable, arguments, **kwargs):
            job = next(core.DATA_DIR.glob('component-analysis-*'))
            (job/'stdout.bin').write_bytes(b'[==== 75.0% ====]\rActual Size : 4.75 GB\n')
            (job/'result.json').write_text(json.dumps(dict(ok=True, returncode=0, message='组件分析已完成。')), encoding='utf-8')
            return process
        kernel.WaitForSingleObject.side_effect = [258, 0]
        progress = Mock()
        with patch.object(uninstall_windows, '_kernel', return_value=kernel), \
             patch.object(uninstall_windows, '_elevated', side_effect=launch):
            result = analysis.run_elevated(progress)
        self.assertTrue(result['ok'])
        self.assertIn('Actual Size : 4.75 GB', result['output'])
        self.assertTrue(any(isinstance(call.args[0], dict) and call.args[0].get('percent') == 75 for call in progress.call_args_list))
        process.close.assert_called_once()
        self.assertEqual(list(core.DATA_DIR.iterdir()), [])


if __name__ == '__main__':
    unittest.main()

# Purpose: Regression tests for component analysis, progress and elevated read-only jobs
