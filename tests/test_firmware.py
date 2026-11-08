from collections import deque
import hashlib
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from ganss import firmware as fw
from ganss.api import dispatch


class FakeBoot:
    def __init__(self, image, chip=b'\x20\x03\0\0', security=b'\x5a\x5a', ack_ok=True, verify_ok=True):
        info = fw.command(1)[:4] + b'\xfa' * 4 + chip + b'\x12\x34' + security + bytes(48)
        ack = fw.command(5)[:4] + (b'\xfa' * 4 if ack_ok else bytes(4)) + bytes(56)
        checksum = fw.checksum16(image) ^ (0 if verify_ok else 1)
        final = fw.command(5)[:4] + b'\xfa' * 4 + checksum.to_bytes(2, 'little') + bytes(54)
        self.responses = deque([info, ack, final])
        self.sent = []

    def send(self, packet):
        self.sent.append(packet)

    def receive(self):
        return self.responses.popleft()


class FirmwareTests(unittest.TestCase):
    def test_official_artifact_matches_windows_programmed_image(self):
        image, metadata = fw.load_official()
        self.assertEqual(len(image), 0x10000)
        self.assertEqual(image[:8], bytes.fromhex('0817002031680000'))
        self.assertEqual(image[-4:], bytes.fromhex('5555aaaa'))
        self.assertEqual(hashlib.sha256(image).hexdigest(), fw.IMAGE_SHA256)
        self.assertEqual(metadata['checksum16'], 0x2765)
        self.assertEqual(metadata['resource'], {'type': 10, 'id': 4000, 'language': 0})
        self.assertEqual(metadata['chip'], 'SN32F24xB')
        self.assertTrue(metadata['windows_capture_verified'])
        self.assertFalse(metadata['hardware_update_verified'])

    def test_complete_job_replays_successful_official_windows_capture(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/official-update.json').read_text())
        self.assertEqual(fixture['capture_sha256'],
                         '2255815a117f5d0bc6eb13a6db1a94b4796e5f3e0dca73be0bf10fb475b05644')
        self.assertEqual(fixture['feature_transactions'], 1032)
        replies = [bytes.fromhex(value).ljust(64, b'\0') for value in fixture['responses_hex']]
        image, metadata = fw.load_official()
        self.assertEqual(fixture['programmed_image_bytes'], 65536)
        self.assertEqual(hashlib.sha256(image).hexdigest(), fixture['programmed_image_sha256'])
        self.assertEqual(int.from_bytes(replies[-1][8:10], 'little'), 0x2765)

        class Replay:
            def __init__(self):
                self.sent = []
                self.responses = deque(replies)
            def __enter__(self):
                return self
            def __exit__(self, *_):
                pass
            def send_feature(self, payload):
                self.sent.append(payload[1:])
            def send(self, payload):
                self.sent.append(payload)
            def receive(self):
                return self.responses.popleft()
        normal, boot = Replay(), Replay()
        hardware = {'name': 'HFD GS3104T PRO', 'id': 'test', 'endpoints': []}
        with patch('ganss.firmware.prepare', return_value=(hardware, '/dev/test', 'port', image, metadata)), \
             patch('ganss.firmware._physical', return_value='port'), \
             patch('ganss.firmware.Hidraw', return_value=normal), \
             patch('ganss.firmware.BootHid', return_value=boot), \
             patch('ganss.firmware.boot_endpoints', return_value=['/dev/test']), \
             patch('ganss.firmware.os.access', return_value=True), \
             patch('ganss.firmware.time.sleep'), \
             patch('ganss.firmware._wait_keyboard', return_value=True):
            job = fw.start_update(hardware['name'])
            for thread in threading.enumerate():
                if thread.name == 'ganss-firmware-update':
                    thread.join(timeout=5)
        result = fw.job_status(job['job_id'])
        self.assertEqual(result['status'], 'completed', result.get('error'))
        self.assertEqual(result['written_bytes'], 65536)
        self.assertEqual(result['total_bytes'], 65536)
        self.assertTrue(result['checksum_verified'])
        self.assertTrue(result['normal_device_returned'])
        self.assertTrue(result['isp_handshake_verified'])
        self.assertFalse(result['may_have_partial_changes'])
        self.assertEqual(len(boot.responses), 0)
        actual = normal.sent + boot.sent
        self.assertEqual(len(actual), fixture['out_transfers'])
        for index, name in ((0, 'enter_isp'), (1, 'query'), (2, 'code_options'),
                            (3, 'enable_program'), (-1, 'return_user')):
            expected = bytes.fromhex(fixture['commands_hex'][name]).ljust(64, b'\0')
            self.assertEqual(actual[index], expected, name)
        self.assertEqual(len(actual[4:-1]), 1024)
        self.assertTrue(all(len(packet) == 64 for packet in actual[4:-1]))
        # This golden digest comes from all raw captured blocks, independently
        # of resource extraction; it verifies their complete order and contents.
        self.assertEqual(hashlib.sha256(b''.join(actual[4:-1])).hexdigest(),
                         fixture['programmed_image_sha256'])

    def test_tampered_exe_refused(self):
        with patch('ganss.firmware.files') as resources:
            resources.return_value.joinpath.return_value.read_bytes.return_value = b'bad exe'
            with self.assertRaisesRegex(fw.FirmwareError, 'SHA256'):
                fw.load_official()

    def test_resource_truncation_and_missing_ids_rejected(self):
        blob = (Path(__file__).resolve().parents[1] / 'ganss/data/official-firmware.exe').read_bytes()
        for truncated in (b'', b'MZ', blob[:100], blob[:0x1b964c + 65535]):
            with self.assertRaises(fw.FirmwareError):
                fw.pe_resource(truncated, 4000)
        for resource_id in (0, 4001, 65535):
            with self.assertRaises(fw.FirmwareError):
                fw.pe_resource(blob, resource_id)

    def test_wrong_image_rejected_before_query(self):
        image, _ = fw.load_official()
        for invalid in (image[:-1], bytes(len(image)), image + bytes(1)):
            device = FakeBoot(image)
            with self.assertRaises(fw.FirmwareError):
                fw.program(device, invalid, lambda *_: None, sleep=lambda _: None)
            self.assertEqual(device.sent, [])

    def test_unknown_id_refused_without_device_access(self):
        with patch('ganss.firmware.select_hardware') as scan:
            with self.assertRaises(ValueError):
                fw.prepare('keyboard', '/tmp/upload.hex')
        scan.assert_not_called()

    def test_wireless_refused_before_open_or_transition(self):
        with patch('ganss.firmware.load_official', return_value=(b'', {})), \
             patch('ganss.firmware.select_hardware', return_value={'name': 'GS3104T-PRO 2.4G'}), \
             patch('ganss.firmware.select_transport', return_value=('/dev/example', 'wireless')), \
             patch('ganss.firmware.Hidraw') as hid:
            with self.assertRaises(fw.FirmwareError) as error:
                fw.prepare('GS3104T-PRO 2.4G')
        self.assertEqual(error.exception.code, 'wired_connection_required')
        hid.assert_not_called()

    def test_complete_native_sequence(self):
        image, _ = fw.load_official()
        device = FakeBoot(image)
        progress = []
        fw.program(device, image, lambda *args: progress.append(args), sleep=lambda _: None)
        self.assertEqual(device.sent[0], fw.command(1))
        self.assertEqual(device.sent[1], fw.command(3, b'\x12\x34\x5a\x5a'))
        self.assertEqual(device.sent[2][:10], bytes.fromhex('05aa5500000000000004'))
        self.assertEqual(b''.join(device.sent[3:-1]), image)
        self.assertEqual(device.sent[-1], fw.command(7))
        self.assertEqual(len(device.sent), 1028)
        self.assertEqual(progress[-1], ('reset', fw.FLASH_SIZE, True))

    def test_wrong_chip_and_protection_stop_before_erase(self):
        image, _ = fw.load_official()
        for kwargs in ({'chip': b'\x20\x05\0\0'}, {'chip': b'\x20\x03\x01\0'},
                       {'security': b'\xc3\xc3'}, {'security': b'\0\0'},
                       {'security': b'\xff\xff'}, {'security': b'\xa5\xa5'}):
            device = FakeBoot(image, **kwargs)
            with self.assertRaises(fw.FirmwareError):
                fw.program(device, image, lambda *_: None, sleep=lambda _: None)
            self.assertEqual(device.sent, [fw.command(1)])

    def test_existing_protection_preserved(self):
        image, _ = fw.load_official()
        device = FakeBoot(image, security=b'\x5a\x5a')
        fw.program(device, image, lambda *_: None, sleep=lambda _: None)
        self.assertEqual(device.sent[1][4:8], b'\x12\x34\x5a\x5a')

    def test_failures_never_reset_or_continue(self):
        image, _ = fw.load_official()
        for kwargs, expected in [({'ack_ok': False}, 3), ({'verify_ok': False}, 1027)]:
            device = FakeBoot(image, **kwargs)
            with self.assertRaises(fw.FirmwareError):
                fw.program(device, image, lambda *_: None, sleep=lambda _: None)
            self.assertEqual(len(device.sent), expected)

    def test_checksum_value_alone_does_not_confirm_programming(self):
        image, _ = fw.load_official()
        for response in (bytes(8) + b'\x65\x27' + bytes(54), b'',
                         fw.command(4)[:4] + b'\xfa' * 4 + b'\x65\x27' + bytes(54)):
            device = FakeBoot(image)
            device.responses[-1] = response
            with self.assertRaises(fw.FirmwareError) as error:
                fw.program(device, image, lambda *_: None, sleep=lambda _: None)
            self.assertEqual(error.exception.code, 'verify_failed')
            self.assertEqual(len(device.sent), 1027)

    def test_final_checksum_offset_eight(self):
        image, _ = fw.load_official()
        device = FakeBoot(image)
        response = bytearray(device.responses[-1])
        response[8:10] = b'\x01\0'
        response[28:34] = bytes(6)
        device.responses[-1] = bytes(response)
        with self.assertRaises(fw.FirmwareError) as error:
            fw.program(device, image, lambda *_: None, sleep=lambda _: None)
        self.assertEqual(error.exception.code, 'verify_failed')
        self.assertEqual(len(device.sent), 1027)

    def test_operation_lock(self):
        with fw.device_operation():
            with self.assertRaises(fw.FirmwareError) as error:
                with fw.device_operation():
                    self.fail('must not enter')
        self.assertEqual(error.exception.code, 'device_busy')

    def test_api_catalog_and_upload_rejection(self):
        response = dispatch({'api_version': 1, 'method': 'firmware.list'})
        self.assertTrue(response['ok'])
        self.assertTrue(response['result']['firmwares'][0]['bundled'])
        response = dispatch({'api_version': 1, 'method': 'firmware.update',
                             'params': {'device': 'keyboard', 'file': '/tmp/upload.hex'}})
        self.assertFalse(response['ok'])

    def test_async_job_reports_boot_error_with_device_name(self):
        hardware = {'name': 'GS3104T PRO', 'id': 'test', 'endpoints': []}
        class Transition:
            def __enter__(self):
                return self
            def __exit__(self, *_):
                pass
            def send_feature(self, data):
                self.data = data
        def missing(_, **kwargs):
            raise fw.FirmwareError('bootloader_timeout', 'test timeout')
        with patch('ganss.firmware.prepare', return_value=(hardware, '/dev/test', 'usb-port', b'', {})), \
             patch('ganss.firmware._physical', return_value='usb-port'), \
             patch('ganss.firmware.wait_bootloader', side_effect=missing):
            # Transition accepts the same constructor as Hidraw.
            with patch('ganss.firmware.Hidraw', return_value=Transition()):
                job = fw.start_update('GS3104T PRO')
                for thread in threading.enumerate():
                    if thread.name == 'ganss-firmware-update':
                        thread.join(timeout=2)
        result = fw.job_status(job['job_id'])
        self.assertEqual(result['status'], 'failed')
        self.assertIn('GS3104T PRO', result['error']['message'])
        self.assertFalse(result['may_have_partial_changes'])
        self.assertTrue(result['device_changed'])

    def test_wait_uses_protocol_handshake_on_retained_usb_identity(self):
        class Query:
            def __init__(self):
                self.sent = []
            def __enter__(self):
                return self
            def __exit__(self, *_):
                pass
            def send(self, payload):
                self.sent.append(payload)
            def receive(self):
                return fw.command(1)[:4] + b'\xfa' * 4 + b'\x20\x03\0\0' + bytes(52)
        query = Query()
        with patch('ganss.firmware.boot_endpoints', return_value=['/dev/test']), \
             patch('ganss.firmware.os.access', return_value=True), \
             patch('ganss.firmware.BootHid', return_value=query):
            self.assertEqual(fw.wait_bootloader('port', sleep=lambda _: None), '/dev/test')
        self.assertEqual(query.sent, [fw.command(1)])

    def test_wait_does_not_accept_normal_device_echo(self):
        class Query:
            def __enter__(self):
                return self
            def __exit__(self, *_):
                pass
            def send(self, payload):
                self.payload = payload
            def receive(self):
                return self.payload
        query = Query()
        with patch('ganss.firmware.boot_endpoints', return_value=['/dev/test']), \
             patch('ganss.firmware.os.access', return_value=True), \
             patch('ganss.firmware.BootHid', return_value=query), \
             patch('ganss.firmware.time.monotonic', side_effect=[0, 0, 2]):
            with self.assertRaises(fw.FirmwareError) as error:
                fw.wait_bootloader('port', timeout=1, sleep=lambda _: None)
        self.assertEqual(error.exception.code, 'bootloader_timeout')
        self.assertEqual(query.payload, fw.command(1))

    def test_probe_exits_isp_without_programming(self):
        hardware = {'name': 'HFD GS3104T PRO', 'id': 'test',
                    'endpoints': [{'physical': 'usb-port', 'usb_supported': True}]}
        class Session:
            def __init__(self):
                self.sent = []
            def __enter__(self):
                return self
            def __exit__(self, *_):
                pass
            def send_feature(self, data):
                self.sent.append(data)
            def send(self, data):
                self.sent.append(data)
        normal, boot = Session(), Session()
        with patch('ganss.firmware.prepare', return_value=(hardware, '/dev/test', 'usb-port', b'', {})), \
             patch('ganss.firmware._physical', return_value='usb-port'), \
             patch('ganss.firmware.Hidraw', return_value=normal), \
             patch('ganss.firmware.BootHid', return_value=boot), \
             patch('ganss.firmware.wait_bootloader', return_value='/dev/test'), \
             patch('ganss.firmware._wait_keyboard', return_value=True), \
             patch('ganss.firmware.select_hardware', return_value=hardware), \
             patch('ganss.firmware.program') as program:
            job = fw.start_update(hardware['name'], diagnostic_only=True)
            for thread in threading.enumerate():
                if thread.name == 'ganss-firmware-update':
                    thread.join(timeout=2)
        program.assert_not_called()
        result = fw.job_status(job['job_id'])
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['written_bytes'], 0)
        self.assertFalse(result['checksum_verified'])
        self.assertTrue(result['isp_handshake_verified'])
        self.assertEqual(boot.sent[-1], fw.command(7))

    def test_actual_legacy_chip_response(self):
        response = bytes.fromhex('01 aa 55 00 fa fa fa fa 20 03 00 00 00 00 5a 5a 1e 00') + bytes(46)
        info = fw.chip_info(response)
        self.assertEqual(info['chip'], 'unknown')
        self.assertIsNone(info['flash_bytes'])
        self.assertFalse(info['hardware_identity_verified'])
        self.assertEqual(info['generic_table_profile'], 'SN32F248B')
        self.assertEqual(info['raw_response_hex'], response.hex(' '))
        self.assertEqual(info['security_hex'], '5a 5a')

    def test_unimplemented_profile_returns_to_keyboard_without_erasing(self):
        image, _ = fw.load_official()
        hardware = {'name': 'HFD GS3104T PRO', 'id': 'test', 'endpoints': []}
        class Boot(FakeBoot):
            def __enter__(self):
                return self
            def __exit__(self, *_):
                pass
        boot = Boot(image, chip=b'\x20\x05\0\0', security=b'\x5a\x5a')
        class Normal:
            def __enter__(self):
                return self
            def __exit__(self, *_):
                pass
            def send_feature(self, _):
                pass
        with patch('ganss.firmware.prepare', return_value=(hardware, '/dev/test', 'port', image, {})), \
             patch('ganss.firmware._physical', return_value='port'), \
             patch('ganss.firmware.Hidraw', return_value=Normal()), \
             patch('ganss.firmware.BootHid', return_value=boot), \
             patch('ganss.firmware.wait_bootloader', return_value='/dev/test'), \
             patch('ganss.firmware._wait_keyboard', return_value=True):
            job = fw.start_update(hardware['name'])
            for thread in threading.enumerate():
                if thread.name == 'ganss-firmware-update':
                    thread.join(timeout=2)
        result = fw.job_status(job['job_id'])
        self.assertEqual(result['error']['code'], 'unsupported_isp_profile')
        self.assertIn('20 05 00 00', result['error']['message'])
        self.assertFalse(result['may_have_partial_changes'])
        self.assertTrue(result['normal_device_returned'])
        self.assertEqual(boot.sent, [fw.command(1), fw.command(7)])
