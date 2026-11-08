import unittest
from ganss.protocol import ConfigError, keymap_single_plan


class SingleKeyTests(unittest.TestCase):
    def test_pause_to_b_golden(self):
        step = keymap_single_plan('pause', 'B')[0]
        self.assertEqual(step.data, bytes.fromhex('00 03 04 73 02 00 05 00') + bytes(24) + b'\x81')
        self.assertTrue(step.ack)

    def test_restore_pause(self):
        self.assertEqual(keymap_single_plan('pause', 'default')[0].data[4:8], b'\x02\x00\x48\x00')

    def test_fn_single_command(self):
        self.assertEqual(keymap_single_plan('A', 'B', 'fn')[0].data[1], 0x0D)

    def test_unsupported_default_and_transport_rejected(self):
        for args in [('Fn', 'A'), ('MUTE', 'default'), ('A', 'default', 'fn'), ('A', 'B', 'normal', 'usb')]:
            with self.subTest(args=args), self.assertRaises(ConfigError):
                keymap_single_plan(*args)
