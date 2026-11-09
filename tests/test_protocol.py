import unittest

from ganss.protocol import (ConfigError, key_record, keymap_plan, light_plan,
                            parse_profile, resolve_key, short_packet)


class ProtocolTests(unittest.TestCase):
    def test_feature_light_golden_from_42b124(self):
        plan = light_plan(11, 4, 2, '123456', True)
        self.assertEqual([s.data[1:3] for s in (plan[0], plan[1], plan[3], plan[4])],
                         [b'\x04\x18', b'\x04\x13', b'\x04\x02', b'\x04\xf0'])
        expected = bytes.fromhex('00 0b 12 34 56 00 00 00 00 01 04 02 00 00 00 aa 55') + bytes(48)
        self.assertEqual(plan[2].data, expected)
        self.assertEqual([s.ack for s in plan], [True, True, False, True, False])

    def test_wireless_light_golden_from_42b288(self):
        data = light_plan(11, 4, 2, '123456', True, 'wireless')[0].data
        expected = bytes.fromhex('00 05 10 00 0b 12 34 56 00 00 00 00 01 04 02 00 00 00 aa 55') + bytes(12)
        self.assertEqual(data[:32], expected)
        self.assertEqual(data[32], 0xC2)

    def test_light_controls_change_only_their_own_fields(self):
        # Native struct base = ebp-0x88: +0x10 brightness, +0x14 speed,
        # +0x18 direction. Wired payload offsets 9/10/11, wireless 13/14/15.
        for connection, step, brightness_offset, speed_offset in (
                ('usb', 2, 10, 11), ('wireless', 0, 13, 14)):
            def packet(**args):
                return light_plan(connection=connection, **args)[step].data

            baseline = packet(brightness=0, speed=0)
            for field, offset in (('brightness', brightness_offset), ('speed', speed_offset)):
                for level in range(1, 6):
                    with self.subTest(connection=connection, field=field, level=level):
                        params = dict(brightness=0, speed=0)
                        params[field] = level
                        changed = packet(**params)
                        differences = {i for i, (a, b) in enumerate(zip(baseline, changed)) if a != b}
                        self.assertEqual(differences, {offset, 32} if connection == 'wireless' else {offset})
                        self.assertEqual(changed[offset], level)
                        self.assertEqual(changed[speed_offset + 1], 0)  # direction
                        if connection == 'wireless':
                            self.assertEqual(changed[32], sum(changed[:32]) & 255)

    def test_off_keeps_marker_and_zeros(self):
        feature = light_plan(0)[2].data
        self.assertEqual(feature, bytes(15) + b'\xaa\x55' + bytes(48))

    def test_key_record_ordinary_and_modifiers(self):
        self.assertEqual(key_record('B'), b'\x02\x00\x05\x00')
        self.assertEqual(key_record('CTRL+SHIFT+C'), b'\x02\x03\x06\x00')
        self.assertEqual(key_record('CTRL_R'), b'\x02\x10\x00\x00')
        self.assertEqual(key_record('CTRL_L+SHIFT_R'), b'\x02\x21\x00\x00')
        self.assertEqual(key_record('default'), bytes(4))

    def test_indexed_key_table_and_footer(self):
        plan = keymap_plan({'version': 1, 'layer': 'normal', 'mappings': {'A': 'B'}})
        self.assertEqual(len(plan), 13)
        self.assertEqual(plan[1].data[1:10], bytes.fromhex('04 11 00 00 00 00 00 00 09'))
        table = b''.join(s.data[1:] for s in plan[2:11])
        self.assertEqual(len(table), 576)
        self.assertEqual(table[224:228], b'\x02\0\x05\0')  # A index=56, no -1.
        self.assertEqual(table[220:224], bytes(4))
        self.assertEqual(table[-2:], b'\xaa\x55')
        self.assertEqual(table[:224], bytes(224))

    def test_fn_selection(self):
        plan = keymap_plan({'version': 1, 'layer': 'fn', 'mappings': {'H': 'LEFT'}})
        self.assertEqual(plan[1].data[2], 0x27)

    def test_wireless_table_same_effective_bytes(self):
        profile = {'version': 1, 'layer': 'fn', 'mappings': {'A': 'B'}}
        wired = b''.join(s.data[1:] for s in keymap_plan(profile)[2:11])
        wireless = keymap_plan(profile, 'wireless')
        reconstructed = b''.join(s.data[4:4 + s.data[2]] for s in wireless)
        self.assertEqual(wired, reconstructed)
        self.assertEqual(len(wireless), 21)
        self.assertEqual(wireless[-1].data[1:4], bytes((0x12, 16, 20)))
        self.assertTrue(all(len(s.data) == 33 and s.data[32] == sum(s.data[:32]) & 255 for s in wireless))

    def test_invalid_configs_never_generate_plans(self):
        for args in [dict(mode=-1), dict(mode=256), dict(brightness=6), dict(speed=-1),
                     dict(color='xyz'), dict(colorful=1), dict(mode=True)]:
            with self.subTest(args=args), self.assertRaises(ConfigError):
                light_plan(**args)
        for target in ['A+B', 'Fn', 'MUTE', 'usage:0x100', '', 'usage:xyz']:
            with self.subTest(target=target), self.assertRaises(ConfigError):
                key_record(target)
        for profile in [{}, {'version': True, 'layer': 'normal', 'mappings': {}},
                        {'version': 1, 'layer': 'normal', 'mappings': {'Fn': 'A'}},
                        {'version': 1, 'layer': 'normal', 'mappings': {'A': 'B', 'index:56': 'C'}},
                        {'version': 1, 'layer': 'normal', 'mappings': {}, 'typo': 1}]:
            with self.subTest(profile=profile), self.assertRaises(ConfigError):
                parse_profile(profile)

    def test_enter_alias_and_physical_key(self):
        self.assertEqual(resolve_key('ENTER')['key_index'], '85')
        self.assertEqual(resolve_key('KP_ENTER')['key_index'], '106')
        self.assertEqual(resolve_key('index:106')['code'], '0x58')


if __name__ == '__main__':
    unittest.main()
