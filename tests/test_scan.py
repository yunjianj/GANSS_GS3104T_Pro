from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ganss.linux import scan_hardware, select_hardware, select_transport, supported_hardware
from ganss.protocol import ConfigError
from test_transport import SHORT, FEATURE


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sysfs = self.root / 'class/hidraw'
        self.sysfs.mkdir(parents=True)

    def endpoint(self, number, unit, descriptor=SHORT, name='GS3104T-PRO 2.4G', vid='05ac', pid='024f'):
        physical = self.root / 'usb' / unit
        physical.mkdir(parents=True, exist_ok=True)
        (physical / 'idVendor').write_text(vid)
        (physical / 'idProduct').write_text(pid)
        hid = physical / f'arbitrary-interface-{number}' / 'hid'
        hid.mkdir(parents=True)
        (hid / 'uevent').write_text(f'HID_ID=0003:{vid}:{pid}\nHID_NAME={name}\n')
        (hid / 'report_descriptor').write_bytes(descriptor)
        entry = self.sysfs / f'hidraw{number}'
        entry.mkdir()
        (entry / 'device').symlink_to(hid)

    def test_multiple_interfaces_are_one_hardware(self):
        self.endpoint(10, 'unit1')
        self.endpoint(2, 'unit1', SHORT.replace(b'\x95\x20', b'\x95\x40'))
        self.endpoint(4, 'unit1', b'\x05\x01')
        result = scan_hardware(self.sysfs)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]['endpoints']), 3)
        self.assertEqual(result[0]['connections'], ['wireless'])
        self.assertEqual(select_transport(result[0]), ('/dev/hidraw10', 'wireless'))

    def test_interface_numbers_do_not_affect_selection(self):
        self.endpoint(99, 'unit1', FEATURE, name='GS3104T PRO')
        h = scan_hardware(self.sysfs)[0]
        self.assertEqual(select_transport(h), ('/dev/hidraw99', 'usb'))

    def test_real_wired_manufacturer_prefix(self):
        # Captured wired configuration descriptor: FF13, unnumbered 64-byte
        # Input, Output and Feature. The kernel HID name includes manufacturer.
        descriptor = bytes.fromhex('06 13 ff 09 01 a1 01 15 00 26 ff 00 75 08 95 40 '
                                   '09 02 81 02 09 03 91 02 0a 00 ff b1 02 c0')
        self.endpoint(3, 'wired', b'\x05\x01', name='HFD GS3104T PRO')
        self.endpoint(4, 'wired', b'\x05\x01', name='HFD GS3104T PRO')
        self.endpoint(5, 'wired', descriptor, name='HFD GS3104T PRO')
        result = scan_hardware(self.sysfs)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['name'], 'HFD GS3104T PRO')
        self.assertEqual(result[0]['connections'], ['usb'])
        self.assertEqual(select_transport(result[0]), ('/dev/hidraw5', 'usb'))
        self.assertTrue(all(e['name'] == 'HFD GS3104T PRO' for e in result[0]['endpoints']))

    def test_reopen_identity_accepts_observed_prefix_only(self):
        self.assertTrue(supported_hardware(3, 0x05AC, 0x024F, 'HFD GS3104T PRO'))
        self.assertFalse(supported_hardware(3, 0x1234, 0x024F, 'HFD GS3104T PRO'))
        self.assertFalse(supported_hardware(3, 0x05AC, 0x024F, 'Other GS3104T PRO'))
        self.assertFalse(supported_hardware(3, 0x05AC, 0x024F, 'HFD Other Keyboard'))

    def test_isp_candidates_keep_identity_and_physical_port_filter(self):
        from ganss.firmware import boot_endpoints
        self.endpoint(0, 'selected', FEATURE, name='HFD GS3104T PRO')
        self.endpoint(1, 'other-port', FEATURE, name='HFD GS3104T PRO')
        self.endpoint(2, 'selected', FEATURE, name='Unrelated keyboard')
        self.assertEqual(boot_endpoints(str(self.root / 'usb/selected'), self.sysfs), ['/dev/hidraw0'])

    def test_2003_template_identity_only_on_selected_port(self):
        from ganss.firmware import boot_endpoints
        self.endpoint(0, 'selected', FEATURE, vid='0c45', pid='7040', name='ISP')
        self.endpoint(1, 'other-port', FEATURE, vid='0c45', pid='7040', name='ISP')
        self.assertEqual(boot_endpoints(str(self.root / 'usb/selected'), self.sysfs), ['/dev/hidraw0'])
        # Generic ISP identities are private update candidates, never ordinary
        # keyboards shown in the user's configuration device list.
        self.assertEqual(scan_hardware(self.sysfs), [])

    def test_other_manufacturers_and_same_vid_pid_other_model_hidden(self):
        self.endpoint(0, 'other-vendor', vid='1234', descriptor=b'\xfe')
        self.endpoint(1, 'apple', name='Apple Keyboard')
        self.endpoint(2, 'bad-other', name='Other Keyboard', descriptor=b'\xfe')
        self.assertEqual(scan_hardware(self.sysfs), [])

    def test_matching_model_descriptor_error_keeps_hardware(self):
        self.endpoint(0, 'unit1', descriptor=b'\xfe')
        result = scan_hardware(self.sysfs)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['connections'], [])
        self.assertFalse(result[0]['available'])

    def test_duplicate_names_have_distinct_stable_ids(self):
        self.endpoint(0, 'unit1')
        self.endpoint(1, 'unit2')
        result = scan_hardware(self.sysfs)
        self.assertEqual(len({h['name'] for h in result}), 2)
        self.assertEqual(len({h['id'] for h in result}), 2)
        self.assertEqual([h['id'] for h in result], [h['id'] for h in scan_hardware(self.sysfs)])
        with patch('ganss.linux.scan_hardware', return_value=result):
            with self.assertRaises(ConfigError):
                select_hardware('GS3104T-PRO 2.4G')
            self.assertEqual(select_hardware(result[0]['name']), result[0])
            self.assertEqual(select_hardware(result[1]['id']), result[1])

    def test_unreadable_unknown_identity_not_shown(self):
        (self.sysfs / 'hidraw0').mkdir()
        self.assertEqual(scan_hardware(self.sysfs), [])

    def test_no_config_channel_rejected(self):
        self.endpoint(0, 'unit1', descriptor=b'\x05\x01')
        with self.assertRaises(ConfigError):
            select_transport(scan_hardware(self.sysfs)[0])
