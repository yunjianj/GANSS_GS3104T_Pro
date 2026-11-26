import unittest
from functools import partial
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ganss.api import dispatch
from ganss.gui_state import set_active_silently


class FakeButton:
    def __init__(self, fail=False):
        self.blocked = False
        self.clicks = 0
        self.fail = fail

    def handler_block(self, handler):
        self.blocked = True

    def handler_unblock(self, handler):
        self.blocked = False

    def set_active(self, active):
        if self.fail:
            raise RuntimeError('failed')
        if not self.blocked:
            self.clicks += 1
        self.active = active


class GuiStateTest(unittest.TestCase):
    def test_change_does_not_emit_click(self):
        button = FakeButton()
        set_active_silently(button, 1, True)
        self.assertTrue(button.active)
        self.assertEqual(button.clicks, 0)
        self.assertFalse(button.blocked)

    def test_handler_restored_after_error(self):
        button = FakeButton(fail=True)
        with self.assertRaises(RuntimeError):
            set_active_silently(button, 1, True)
        self.assertFalse(button.blocked)


class FirmwarePollingTest(unittest.TestCase):
    def setUp(self):
        try:
            from ganss import gui
        except SystemExit as exc:
            self.skipTest(str(exc))
        self.gui = gui
        self.window = SimpleNamespace(updating=True, tabs=Mock(), scan_button=Mock(),
                                      device=Mock(), status=Mock(), progress=Mock(),
                                      poll_firmware=Mock())
        self.window.finish_firmware_polling = partial(gui.Window.finish_firmware_polling, self.window)

    def test_unknown_job_stops_polling_and_allows_close(self):
        job_id = 'missing-job'
        response = dispatch({'api_version': 1, 'method': 'firmware.status',
                             'params': {'job_id': job_id}})
        self.assertEqual(response['error']['code'], 'invalid_request')
        self.assertTrue(self.gui.Window.on_close(self.window))
        with patch.object(self.gui.GLib, 'timeout_add') as schedule:
            self.assertFalse(self.gui.Window.firmware_status(self.window, job_id, response))
        schedule.assert_not_called()
        self.assertFalse(self.gui.Window.on_close(self.window))
        for control in (self.window.tabs, self.window.scan_button, self.window.device):
            control.set_sensitive.assert_called_once_with(True)
        self.assertIn('更新结果未知', self.window.status.set_text.call_args.args[0])

    def test_running_job_keeps_polling_and_blocks_close(self):
        response = {'ok': True, 'result': {'status': 'running', 'stage': 'programming',
                                           'total_bytes': 64, 'written_bytes': 32}}
        with patch.object(self.gui.GLib, 'timeout_add') as schedule:
            self.assertFalse(self.gui.Window.firmware_status(self.window, 'job', response))
        schedule.assert_called_once_with(500, self.window.poll_firmware, 'job')
        self.window.progress.set_fraction.assert_called_once_with(0.5)
        self.assertTrue(self.gui.Window.on_close(self.window))
