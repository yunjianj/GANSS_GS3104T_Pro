from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ganss.cli import main


def hardware(connection='usb'):
    return {'id': 'keyboard-id', 'name': 'GS3104T-PRO 2.4G', 'serial': '',
            'available': True, 'connections': [connection], 'endpoints': [
                {'path': '/dev/hidraw42', 'node_available': True,
                 'usb_supported': connection == 'usb',
                 'wireless_supported': connection == 'wireless'}]}


class CliTests(unittest.TestCase):
    def run_cli(self, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = main(args)
        return result, stdout.getvalue(), stderr.getvalue()

    def setUp(self):
        self.scan = patch('ganss.linux.scan_hardware', return_value=[hardware()]).start()
        self.addCleanup(patch.stopall)

    def test_dry_run_does_not_open_hardware_and_names_target(self):
        with patch('ganss.cli.Hidraw') as hid:
            code, out, err = self.run_cli(['light', '--device', 'keyboard-id', '--dry-run', '--color', 'FF8000'])
            self.assertEqual(code, 0, err)
            plan = json.loads(out)
            self.assertEqual(len(plan['steps']), 5)
            self.assertEqual(plan['device']['name'], 'GS3104T-PRO 2.4G')
            hid.assert_not_called()

    def test_wireless_detected_without_connection_argument(self):
        self.scan.return_value = [hardware('wireless')]
        code, out, err = self.run_cli(['light', '--device', 'GS3104T-PRO 2.4G', '--dry-run'])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)['connection'], 'wireless')
        self.assertEqual(len(json.loads(out)['steps']), 1)

    def test_device_name_required(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(['light', '--dry-run'])

    def test_arbitrary_hidraw_path_not_accepted(self):
        with patch('ganss.cli.Hidraw') as hid:
            code, _, err = self.run_cli(['light', '--device', '/dev/hidraw42'])
            self.assertEqual(code, 1)
            self.assertIn('scan', err)
            hid.assert_not_called()

    def test_invalid_color_no_open(self):
        with patch('ganss.cli.Hidraw') as hid:
            code, _, err = self.run_cli(['light', '--device', 'keyboard-id', '--color', 'bad'])
            self.assertEqual(code, 1)
            self.assertIn('RRGGBB', err)
            hid.assert_not_called()

    def test_duplicate_json_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'keys.json'
            path.write_text('{"version":1,"layer":"normal","mappings":{"A":"B","A":"C"}}')
            with patch('ganss.cli.Hidraw') as hid:
                code, _, err = self.run_cli(['keymap', 'apply', str(path), '--device', 'keyboard-id', '--dry-run'])
                self.assertEqual(code, 1)
                self.assertIn('重复', err)
                hid.assert_not_called()

    def test_keymap_init_roundtrip(self):
        self.scan.return_value = [hardware('wireless')]
        code, out, err = self.run_cli(['keymap', 'init', '--layer', 'fn'])
        self.assertEqual(code, 0, err)
        with patch('sys.stdin', io.StringIO(out)):
            code, out, err = self.run_cli(['keymap', 'apply', '-', '--device', 'keyboard-id', '--dry-run'])
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)['replaces_entire_layer'])
        self.assertEqual(len(json.loads(out)['steps']), 21)

    def test_permission_error_has_hardware_name(self):
        with patch('ganss.cli.Hidraw') as hid:
            hid.return_value.__enter__.side_effect = PermissionError('denied')
            code, _, err = self.run_cli(['light', '--device', 'GS3104T-PRO 2.4G'])
        self.assertEqual(code, 1)
        self.assertIn('GS3104T-PRO 2.4G', err)
        self.assertIn('udev', err)

    def test_scan_public_output_groups_hardware(self):
        with patch('ganss.cli.scan_hardware', return_value=[hardware('wireless')]):
            code, out, _ = self.run_cli(['scan', '--json'])
        self.assertEqual(code, 0)
        devices = json.loads(out)
        self.assertEqual(len(devices), 1)
        self.assertNotIn('endpoints', devices[0])
        self.assertNotIn('hidraw', out)

    def test_trace_and_success_include_hardware_name(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'trace.jsonl')
            with patch('ganss.cli.Hidraw') as hid, patch('ganss.cli.execute') as execute:
                def perform(plan, transport):
                    hid.call_args.args[2]({'direction': 'send', 'hex': '00'})
                    return len(plan)
                execute.side_effect = perform
                code, out, err = self.run_cli(['light', '--device', 'keyboard-id', '--trace', path])
            self.assertEqual(code, 0, err)
            self.assertIn('GS3104T-PRO 2.4G', out)
            self.assertEqual(json.loads(Path(path).read_text())['device'], 'GS3104T-PRO 2.4G')
