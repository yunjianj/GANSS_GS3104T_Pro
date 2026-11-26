"""Dialog responses should return immediately and perform only confirmed actions."""
import unittest
from unittest.mock import Mock, patch

try:
    from ganss import gui
    GTK_AVAILABLE = bool(gui.Gtk.init_check()[0])
except (ImportError, SystemExit):
    GTK_AVAILABLE = False


@unittest.skipUnless(GTK_AVAILABLE, 'GTK 3 display unavailable')
class DialogResponseTest(unittest.TestCase):
    def setUp(self):
        self.patches = [patch.object(gui.profiles, 'load', return_value={}),
                        patch.object(gui.Window, 'scan')]
        for item in self.patches:
            item.start()
        self.addCleanup(lambda: [item.stop() for item in reversed(self.patches)])
        self.window = gui.Window()
        self.window.persist_profiles = Mock()
        self.window.call = Mock()
        self.addCleanup(self.window.destroy)

    def dialog(self):
        dialogs = [window for window in gui.Gtk.Window.list_toplevels()
                   if isinstance(window, gui.Gtk.Dialog) and window.get_visible()]
        self.assertEqual(len(dialogs), 1)
        return dialogs[0]

    def test_create_profile_is_deferred_and_cancel_is_safe(self):
        self.window.new_profile()
        dialog = self.dialog()
        self.window.persist_profiles.assert_not_called()
        dialog.response(gui.Gtk.ResponseType.CANCEL)
        self.window.persist_profiles.assert_not_called()

        self.window.new_profile()
        dialog = self.dialog()
        entry = next(child for child in dialog.get_content_area().get_children()
                     if isinstance(child, gui.Gtk.Entry))
        entry.set_text('工作')
        dialog.response(gui.Gtk.ResponseType.OK)
        self.window.persist_profiles.assert_called_once_with(
            {'工作': {'version': 1, 'layer': 'normal', 'mappings': {}}}, '工作')

    def test_delete_and_firmware_require_confirmation(self):
        self.window.profiles['工作'] = {'version': 1, 'layer': 'normal', 'mappings': {}}
        self.window.refresh_profiles('工作')
        self.window.delete_profile()
        self.dialog().response(gui.Gtk.ResponseType.CANCEL)
        self.window.persist_profiles.assert_not_called()
        self.window.delete_profile()
        self.dialog().response(gui.Gtk.ResponseType.OK)
        self.window.persist_profiles.assert_called_once_with({}, gui.profiles.DEFAULT_NAME)

        info = {'device': {'name': '键盘'}, 'firmware': {'version_label': 'V1.20',
                'build_date_label': '2025-07-22'}, 'warnings': []}
        self.window.confirm_firmware('test-id', info)
        self.dialog().response(gui.Gtk.ResponseType.CANCEL)
        self.window.call.assert_not_called()
        self.assertFalse(self.window.updating)
        self.window.confirm_firmware('test-id', info)
        self.assertFalse(self.window.updating)
        self.dialog().response(gui.Gtk.ResponseType.OK)
        self.assertTrue(self.window.updating)
        self.window.call.assert_called_once_with('firmware.update', {'device': 'test-id'},
                                                 self.window.firmware_started)
