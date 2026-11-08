import unittest
from unittest.mock import patch

from ganss.linux import Capabilities, Hidraw, ioc, parse_descriptor
from ganss.protocol import ConfigError, light_plan
from ganss.runner import ProtocolError, execute

FEATURE = bytes.fromhex('06 13 ff 09 01 a1 01 15 00 26 ff 00 75 08 95 40 09 02 81 02 09 03 91 02 0a 00 ff b1 02 c0')
# Read-only sysfs observation of the receiver's MI_03 interface.
SHORT = bytes.fromhex('06 60 ff 09 61 a1 01 09 62 15 00 26 ff 00 95 20 75 08 81 02 09 63 15 00 26 ff 00 95 20 75 08 91 02 c0')


class DescriptorTests(unittest.TestCase):
    def test_feature64_from_embedded_firmware(self):
        caps = parse_descriptor(FEATURE)
        self.assertEqual(caps.size('feature'), 64)
        self.assertTrue(caps.supports('usb'))
        self.assertFalse(caps.supports('wireless'))

    def test_receiver_32_byte_interface(self):
        caps = parse_descriptor(SHORT)
        self.assertTrue(caps.supports('wireless'))
        self.assertFalse(caps.supports('usb'))
        other = SHORT.replace(b'\x95\x20', b'\x95\x40')
        self.assertFalse(parse_descriptor(other).supports('wireless'))

    def test_numbered_rejected_and_globals_restored(self):
        numbered = parse_descriptor(b'\x85\x01' + FEATURE)
        self.assertEqual(numbered.size('feature', 1), 64)
        self.assertFalse(numbered.supports('usb'))
        data = bytes.fromhex('06 00 ff 75 08 95 01 a4 75 01 95 08 b1 02 b4 b1 02')
        self.assertEqual(parse_descriptor(data).size('feature'), 2)

    def test_truncation_and_pop_underflow(self):
        for data in [b'\x76\x01', b'\xfe', b'\xfe\x03\x01\x00', b'\xb4', b'\xa4']:
            with self.subTest(data=data), self.assertRaises(ConfigError):
                parse_descriptor(data)

    def test_ioctl_numbers_match_linux_uapi(self):
        self.assertEqual(ioc(3, 6, 65), 0xC0414806)
        self.assertEqual(ioc(3, 7, 65), 0xC0414807)
        self.assertEqual(ioc(2, 2, 4100), 0x90044802)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.transport = Hidraw('/dev/fake')
        self.transport.fd = 42

    def test_feature_get_linux_64_payload(self):
        payload = bytes.fromhex('04 18 00 01') + bytes(60)

        def ioctl(fd, req, buf, mutate):
            buf[:64] = payload
            return 64
        with patch('ganss.linux.fcntl.ioctl', side_effect=ioctl):
            self.assertEqual(self.transport.get_feature(), payload)

    def test_feature_get_65_with_zero_id(self):
        payload = bytes.fromhex('04 18 00 01') + bytes(60)

        def ioctl(fd, req, buf, mutate):
            buf[:] = b'\0' + payload
            return 65
        with patch('ganss.linux.fcntl.ioctl', side_effect=ioctl):
            self.assertEqual(self.transport.get_feature(), payload)

    def test_feature_get_short_and_wrong_id_rejected(self):
        for count in (0, 4, 63, 65):
            def ioctl(fd, req, buf, mutate):
                buf[0] = 9
                return count
            with self.subTest(count=count), patch('ganss.linux.fcntl.ioctl', side_effect=ioctl):
                with self.assertRaises(OSError):
                    self.transport.get_feature()

    def test_short_write_rejected(self):
        with patch('ganss.linux.fcntl.ioctl', return_value=64):
            with self.assertRaises(OSError):
                self.transport.send_feature(bytes(65))

    def test_short_echo_and_output_size(self):
        self.transport.caps = parse_descriptor(SHORT)
        packet = light_plan(connection='wireless')[0].data
        with patch('ganss.linux.select.select', side_effect=[([], [], []), ([42], [], [])]), \
             patch('ganss.linux.os.write', return_value=33) as write, \
             patch('ganss.linux.os.read', return_value=packet[1:]):
            self.transport.short_exchange(packet)
            write.assert_called_once_with(42, packet)

    def test_short_timeout(self):
        self.transport.caps = parse_descriptor(SHORT)
        with patch('ganss.linux.select.select', return_value=([], [], [])), \
             patch('ganss.linux.os.write', return_value=33):
            with self.assertRaises(TimeoutError):
                self.transport.short_exchange(light_plan(connection='wireless')[0].data)

    def test_close_on_open_validation_error(self):
        with patch('ganss.linux.os.open', return_value=42), \
             patch('ganss.linux.os.fstat') as fstat, patch('ganss.linux.os.close') as close:
            fstat.return_value.st_mode = 0
            with self.assertRaises(ConfigError):
                self.transport.__enter__()
            close.assert_called_once_with(42)


class FakeTransport:
    def __init__(self, status=1):
        self.sent, self.acks, self.status = [], 0, status

    def send_feature(self, data):
        self.sent.append(data)

    def get_feature(self):
        self.acks += 1
        return bytes((4, 0, 0, self.status)) + bytes(60)


class RunnerTests(unittest.TestCase):
    def test_complete_light_transaction(self):
        fake = FakeTransport()
        self.assertEqual(execute(light_plan(), fake, sleep=lambda _: None), 5)
        self.assertEqual(len(fake.sent), 5)
        self.assertEqual(fake.acks, 3)

    def test_bad_ack_stops_before_data_or_commit(self):
        fake = FakeTransport(status=255)
        with self.assertRaisesRegex(ProtocolError, '步骤 1'):
            execute(light_plan(), fake, sleep=lambda _: None)
        self.assertEqual(len(fake.sent), 1)

    def test_device_disconnect_stops_transaction(self):
        fake = FakeTransport()
        fake.send_feature = lambda _: (_ for _ in ()).throw(OSError('disconnected'))
        with self.assertRaisesRegex(ProtocolError, '已完成 0 步'):
            execute(light_plan(), fake, sleep=lambda _: None)
