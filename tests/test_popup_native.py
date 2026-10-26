import ctypes as ct
from ctypes import wintypes as wt
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import popup_native as extended
import popup_windows as windows
from test_popup_windows import FakeWin32


class ExtendedWin32Tests(unittest.TestCase):
    def setUp(self):
        self.base=FakeWin32()
        self.base.user.SetThreadDpiAwarenessContext=Mock(return_value=777)
        self.base.user.MonitorFromWindow=Mock(return_value=555)
        self.base.user.GetMonitorInfoW=Mock(side_effect=self.monitor)
        self.base.user.WindowFromPoint=Mock(return_value=self.base.hwnd)
        self.base.user.GetDC=Mock(return_value=888)
        self.base.user.ReleaseDC=Mock(return_value=1)
        self.base.user.ShowWindow=Mock(side_effect=self.hide)
        self.base.user.GetWindowLongW.side_effect=lambda hwnd,index: -0x80000000 if index==-16 else 8
        self.shcore=SimpleNamespace(GetScaleFactorForMonitor=Mock(side_effect=self.scale))
        self.gdi=SimpleNamespace(GetPixel=Mock(return_value=0x00332211))
        self.termination=SimpleNamespace(TerminateProcess=Mock(return_value=True))
        self.native=extended.Native(self.base.user,self.shcore,self.gdi,self.termination)
        self.window_backend=patch.object(windows,'_backend',return_value=self.base.api)
        self.details_backend=patch.object(extended,'backend',return_value=self.native)
        self.window_backend.start();self.details_backend.start()
        self.addCleanup(self.window_backend.stop);self.addCleanup(self.details_backend.stop)
        self.item=windows.describe(self.base.hwnd)

    @staticmethod
    def monitor(handle, output):
        info=output._obj
        info.rcMonitor.left=info.rcMonitor.top=0
        info.rcMonitor.right,info.rcMonitor.bottom=1920,1080
        return True

    @staticmethod
    def scale(handle, output):
        output._obj.value=150
        return 0

    def hide(self, hwnd, command):
        self.base.window['visible']=False
        return True

    def test_enrichment_keeps_base_schema_and_returns_unsigned_styles_scaled_size_pixels(self):
        result=windows.enrich(self.item,((5,5),))
        self.assertEqual({k:result[k] for k in windows._FIELDS},self.item)
        self.assertEqual((result['styles'],result['exstyles']),(0x80000000,8))
        self.assertEqual((result['logical_width'],result['logical_height']),(280,173))
        self.assertEqual(result['position'],1)
        self.assertEqual(result['pixels'],{(5,5):0xff112233})
        self.base.user.ReleaseDC.assert_called_once_with(None,888)
        self.assertEqual(self.base.user.SetThreadDpiAwarenessContext.call_args.args,(777,))

    def test_occluded_points_api_errors_and_changed_identity_cannot_be_matched(self):
        self.base.user.WindowFromPoint.return_value=222
        self.base.user.GetAncestor.side_effect=lambda hwnd,flags:hwnd
        self.assertIsNone(windows.enrich(self.item,((5,5),)))
        self.base.user.ReleaseDC.assert_called_once()
        self.assertEqual(self.base.user.SetThreadDpiAwarenessContext.call_args.args,(777,))
        self.base.user.GetMonitorInfoW.side_effect=OSError('monitor unavailable')
        self.assertIsNone(windows.enrich(self.item))
        self.assertEqual(self.base.user.SetThreadDpiAwarenessContext.call_args.args,(777,))
        self.base.created+=1
        self.assertIsNone(windows.enrich(self.item))
        self.base.user.PostMessageW.assert_not_called()
        self.termination.TerminateProcess.assert_not_called()

    def test_zero_style_is_valid_but_last_error_and_invalid_scale_are_unknown(self):
        self.base.user.GetWindowLongW.side_effect=lambda hwnd,index:0
        self.item=windows.describe(self.base.hwnd)
        self.assertEqual(windows.enrich(self.item)['styles'],0)
        def bad(hwnd,index):ct.set_last_error(5);return 0
        self.base.user.GetWindowLongW.side_effect=bad
        self.assertIsNone(windows.enrich(self.item))
        self.base.user.GetWindowLongW.side_effect=lambda hwnd,index:0
        self.shcore.GetScaleFactorForMonitor.side_effect=lambda handle,out:0
        self.assertIsNone(windows.enrich(self.item))

    def test_actions_require_fresh_full_validator_and_explicit_valid_action(self):
        validate=Mock(return_value=False)
        self.assertFalse(windows.apply_vendor(self.item,'close',validate))
        validate.assert_called_once()
        self.base.user.PostMessageW.assert_not_called()
        self.assertFalse(windows.apply_vendor(self.item,'colse',lambda item:True))
        self.assertTrue(windows.apply_vendor(self.item,'close',lambda item:item['position']==1))
        self.base.user.PostMessageW.assert_called_once_with(self.base.hwnd,0x10,0,0)
        self.assertTrue(windows.apply_vendor(self.item,'hide',lambda item:True))
        self.base.user.ShowWindow.assert_called_once_with(self.base.hwnd,0)

    def test_kill_rechecks_creation_time_image_and_owner_on_actual_handle(self):
        self.base.kernel.CloseHandle.reset_mock()
        self.assertTrue(self.native.kill(self.base.api,self.item))
        self.termination.TerminateProcess.assert_called_once_with(self.base.handle,0)
        self.base.kernel.OpenProcess.assert_called_with(0x101001,False,self.item['pid'])
        self.base.kernel.CloseHandle.assert_called_once_with(self.base.handle)
        self.termination.TerminateProcess.reset_mock()
        self.base.created+=1
        self.assertFalse(self.native.kill(self.base.api,self.item))
        self.termination.TerminateProcess.assert_not_called()
        self.base.created-=1
        self.base.window['image']=r'C:\Other\different.exe'
        self.assertFalse(self.native.kill(self.base.api,self.item))
        self.termination.TerminateProcess.assert_not_called()

    def test_invalid_snapshot_types_do_not_reach_extended_action(self):
        for key,value in (('pid',True),('created',1.0),('hwnd',True),('popup',1),('image',None)):
            self.assertIsNone(windows.enrich(dict(self.item,**{key:value})))
        self.base.user.ShowWindow.assert_not_called()
        self.termination.TerminateProcess.assert_not_called()


class GeometryTests(unittest.TestCase):
    def test_position_enums_center_column_not_vertical_center_and_negative_monitors(self):
        monitor=(0,0,1920,1080)
        expected=[((0,0,1920,1080),0),((10,10,310,210),1),((10,800,310,1000),2),
                  ((700,0,1000,200),3),((1500,10,1800,210),4),((1500,800,1800,1000),5)]
        for rect,result in expected:
            self.assertEqual(extended.position(monitor,rect),result)
            negative=tuple(v-1920 if i%2==0 else v for i,v in enumerate(rect))
            self.assertEqual(extended.position((-1920,0,0,1080),negative),result)
        self.assertEqual(extended.logical_size(450,150),300)
        self.assertEqual(extended.logical_size(251,125),201)
        with self.assertRaises(ValueError):extended.logical_size(100,0)


if __name__=='__main__':unittest.main()
