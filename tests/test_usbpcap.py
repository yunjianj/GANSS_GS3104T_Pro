"""Offline capture fixtures for both documented USBPcap control formats."""
import struct
import unittest

from analysis.tools.read_usbpcap import analyze, packets


def usb(stage, payload, *, incoming=False, irp=42, status=0):
    return struct.pack('<HQIHBHHBBIB', 28, irp, status, 0, int(incoming),
                       1, 7, 0x80 if incoming else 0, 2, len(payload), stage) + payload


def pcap(records):
    header = struct.pack('<IHHIIII', 0xa1b2c3d4, 2, 4, 0, 0, 65535, 249)
    return header + b''.join(struct.pack('<IIII', 0, 0, len(p), len(p)) + p for p in records)


def pcapng(records, endian='<'):
    def block(kind, body):
        body += bytes(-len(body) % 4)
        length = len(body) + 12
        return struct.pack(endian + 'II', kind, length) + body + struct.pack(endian + 'I', length)
    header = block(0x0a0d0d0a, struct.pack(endian + 'IHHq', 0x1a2b3c4d, 1, 0, -1))
    header += block(1, struct.pack(endian + 'HHI', 249, 0, 65535))
    return header + b''.join(block(6, struct.pack(endian + 'IIIII', 0, 0, 0, len(p), len(p)) + p) for p in records)


class UsbPcapTests(unittest.TestCase):
    def test_modern_combined_setup_and_out_has_no_synthetic_report_id(self):
        payload = bytes.fromhex('05 aa 55 00 00 00 00 00 00 04') + bytes(54)
        setup = struct.pack('<BBHHH', 0x21, 9, 0x0300, 2, 64)
        records = [usb(0, setup + payload), usb(3, b'', incoming=True)]
        for container in (pcap(records), pcapng(records), pcapng(records, '>')):
            rows = analyze(container)['feature_transactions']
            self.assertEqual(len(rows), 1)
            self.assertEqual(bytes.fromhex(rows[0]['out']), payload)
            self.assertEqual(rows[0]['interface'], 2)
            self.assertEqual(rows[0]['status'], 0)

    def test_legacy_three_stage_query(self):
        setup = struct.pack('<BBHHH', 0xa1, 1, 0x0300, 2, 64)
        reply = bytes.fromhex('01 aa 55 00 fa fa fa fa 20 03 00 00 00 00 5a 5a 1e 00') + bytes(46)
        capture = pcap([usb(0, setup), usb(1, reply, incoming=True), usb(2, b'', incoming=True)])
        row, = analyze(capture)['feature_transactions']
        self.assertEqual(bytes.fromhex(row['in']), reply)
        self.assertEqual(row['completion_frame'], 3)

    def test_truncated_capture_rejected(self):
        records = [usb(0, struct.pack('<BBHHH', 0x21, 9, 0x0300, 2, 64) + bytes(64))]
        for container in (pcap(records), pcapng(records)):
            with self.assertRaises(ValueError):
                list(packets(container[:-1]))

    def test_incomplete_out_never_counted_as_success(self):
        capture = pcap([usb(0, struct.pack('<BBHHH', 0x21, 9, 0x0300, 2, 64) + bytes(64))])
        row, = analyze(capture)['feature_transactions']
        self.assertEqual(row['status'], 'incomplete')

    def test_failed_usb_status_preserved(self):
        capture = pcap([usb(0, struct.pack('<BBHHH', 0x21, 9, 0x0300, 2, 64) + bytes(64)),
                        usb(3, b'', incoming=True, status=0xc0000001)])
        row, = analyze(capture)['feature_transactions']
        self.assertEqual(row['status'], 0xc0000001)
