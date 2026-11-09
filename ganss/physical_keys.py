"""Map standard Linux evdev key codes to keyboard HID usages.

GTK on the Linux XKB backends reports hardware_keycode as evdev + 8.
The mapping follows physical positions, independent of keyboard layout.
"""
from .protocol import BY_INDEX

EVDEV_TO_USAGE = {
    1: 0x29, 14: 0x2a, 15: 0x2b, 28: 0x28, 29: 0xe0,
    42: 0xe1, 54: 0xe5, 56: 0xe2, 57: 0x2c, 58: 0x39,
    69: 0x53, 70: 0x47, 71: 0x5f, 72: 0x60, 73: 0x61,
    74: 0x56, 75: 0x5c, 76: 0x5d, 77: 0x5e, 78: 0x57,
    79: 0x59, 80: 0x5a, 81: 0x5b, 82: 0x62, 83: 0x63,
    87: 0x44, 88: 0x45, 96: 0x58, 97: 0xe4, 98: 0x54,
    99: 0x46, 100: 0xe6, 102: 0x4a, 103: 0x52, 104: 0x4b,
    105: 0x50, 106: 0x4f, 107: 0x4d, 108: 0x51, 109: 0x4e,
    110: 0x49, 111: 0x4c, 119: 0x48, 125: 0xe3, 126: 0xe7,
    127: 0x65,
    12: 0x2d, 13: 0x2e, 26: 0x2f, 27: 0x30, 39: 0x33,
    40: 0x34, 41: 0x35, 43: 0x31, 51: 0x36, 52: 0x37,
    53: 0x38, 55: 0x55,
}
EVDEV_TO_USAGE.update({code: 0x1e + offset for offset, code in enumerate(range(2, 11))})
EVDEV_TO_USAGE[11] = 0x27
EVDEV_TO_USAGE.update({code: 0x3a + offset for offset, code in enumerate(range(59, 69))})
for codes, letters in ((range(16, 26), 'QWERTYUIOP'),
                       (range(30, 39), 'ASDFGHJKL'),
                       (range(44, 51), 'ZXCVBNM')):
    EVDEV_TO_USAGE.update({code: 0x04 + ord(letter) - ord('A') for code, letter in zip(codes, letters)})

USAGE_TO_INDEX = {int(key['code'], 16): index for index, key in BY_INDEX.items()
                  if 0x04 <= int(key['code'], 16) <= 0xe7}


def index_from_hardware_keycode(keycode):
    usage = EVDEV_TO_USAGE.get(keycode - 8)
    return USAGE_TO_INDEX.get(usage)
