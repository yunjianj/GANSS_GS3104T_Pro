import unittest

from ganss.physical_keys import index_from_hardware_keycode
from ganss.protocol import BY_INDEX


class PhysicalKeysTest(unittest.TestCase):
    def test_physical_positions_and_keypad(self):
        for evdev, name, usage in [(30, 'A', 4), (16, 'Q', 0x14),
                                   (28, 'Enter', 0x28), (96, 'Enter', 0x58),
                                   (103, '↑', 0x52)]:
            index = index_from_hardware_keycode(evdev + 8)
            self.assertEqual(BY_INDEX[index]['name'], name)
            self.assertEqual(int(BY_INDEX[index]['code'], 16), usage)

    def test_unsupported(self):
        self.assertIsNone(index_from_hardware_keycode(9999))
