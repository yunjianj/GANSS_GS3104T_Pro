import unittest

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
