#!/usr/bin/env python3
"""Extract HID Feature transactions from PCAP/PCAPNG USBPcap captures offline.

USBPcap header: https://desowin.org/usbpcap/captureformat.html
Does not open hardware, execute captures, or send any packets.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

MAX_PACKET = 16 * 1024 * 1024


def packets(blob):
    """Yield (frame_number, linktype, packet); reject truncated records."""
    if blob[:4] in (b'\xd4\xc3\xb2\xa1', b'\x4d\x3c\xb2\xa1', b'\xa1\xb2\xc3\xd4', b'\xa1\xb2\x3c\x4d'):
        endian = '<' if blob[0] in (0xd4, 0x4d) else '>'
        if len(blob) < 24:
            raise ValueError('Truncated PCAP header')
        linktype = struct.unpack_from(endian + 'I', blob, 20)[0] & 0xffff
        offset, frame = 24, 0
        while offset < len(blob):
            if offset + 16 > len(blob):
                raise ValueError('Truncated PCAP packet header')
            _, _, size, original = struct.unpack_from(endian + 'IIII', blob, offset)
            offset += 16
            if size > MAX_PACKET or size > original or offset + size > len(blob):
                raise ValueError('Invalid/truncated PCAP packet')
            frame += 1
            yield frame, linktype, blob[offset:offset + size]
            offset += size
        return
    if blob[:4] != b'\x0a\x0d\x0d\x0a':
        raise ValueError('Expected PCAP or PCAPNG')
    offset, frame, endian, interfaces = 0, 0, None, []
    while offset < len(blob):
        if offset + 12 > len(blob):
            raise ValueError('Truncated PCAPNG block')
        if blob[offset:offset + 4] == b'\x0a\x0d\x0d\x0a':
            magic = blob[offset + 8:offset + 12]
            if magic not in (b'\x4d\x3c\x2b\x1a', b'\x1a\x2b\x3c\x4d'):
                raise ValueError('Invalid PCAPNG byte order')
            endian = '<' if magic[0] == 0x4d else '>'
            interfaces = []
        if endian is None:
            raise ValueError('Missing PCAPNG section')
        kind, size = struct.unpack_from(endian + 'II', blob, offset)
        if (size < 12 or size % 4 or size > MAX_PACKET + 1024
                or offset + size > len(blob)
                or struct.unpack_from(endian + 'I', blob, offset + size - 4)[0] != size):
            raise ValueError('Invalid/truncated PCAPNG block')
        body = blob[offset + 8:offset + size - 4]
        if kind == 1:
            if len(body) < 8:
                raise ValueError('Truncated PCAPNG interface')
            interfaces.append(struct.unpack_from(endian + 'H', body)[0])
        elif kind == 6:
            if len(body) < 20:
                raise ValueError('Truncated PCAPNG packet header')
            interface, _, _, caplen, original = struct.unpack_from(endian + 'IIIII', body)
            if interface >= len(interfaces) or caplen > original or 20 + caplen > len(body):
                raise ValueError('Invalid/truncated PCAPNG packet')
            frame += 1
            yield frame, interfaces[interface], body[20:20 + caplen]
        offset += size


def transactions(blob):
    pending, result = {}, []
    for frame, linktype, packet in packets(blob):
        if linktype != 249:  # LINKTYPE_USBPCAP
            continue
        if len(packet) < 27:
            raise ValueError(f'Truncated USBPcap header at frame {frame}')
        length, irp, status, function, info, bus, device, endpoint, transfer, size = struct.unpack_from('<HQIHBHHBBI', packet)
        if length < 27 or length + size > len(packet):
            raise ValueError(f'Truncated USBPcap payload at frame {frame}')
        if transfer != 2:
            continue
        if length < 28:
            raise ValueError(f'Missing control stage at frame {frame}')
        stage, payload, key = packet[27], packet[length:length + size], (bus, device, irp)
        if stage == 0:
            if len(payload) < 8:
                raise ValueError(f'Truncated USB SETUP at frame {frame}')
            bm, request, value, interface, expected = struct.unpack('<BBHHH', payload[:8])
            pending[key] = {'frame': frame, 'bus': bus, 'device': device,
                            'bm_request_type': bm, 'request': request, 'value': value,
                            'interface': interface, 'expected_length': expected,
                            'out': payload[8:].hex(), 'in': ''}
        elif stage in (1, 2, 3) and key in pending:
            entry = pending[key]
            if payload:
                direction = 'in' if entry['bm_request_type'] & 0x80 else 'out'
                entry[direction] += payload.hex()
            if stage in (2, 3):
                entry['completion_frame'], entry['status'] = frame, status
                if len(bytes.fromhex(entry['out'])) > entry['expected_length'] or len(bytes.fromhex(entry['in'])) > entry['expected_length']:
                    raise ValueError(f'Control payload exceeds SETUP length at frame {frame}')
                result.append(pending.pop(key))
    # Incomplete transactions are evidence, not successful writes.
    for entry in pending.values():
        entry['status'] = 'incomplete'
        result.append(entry)
    return sorted(result, key=lambda item: item['frame'])


def analyze(blob):
    records = transactions(blob)
    features, descriptors = [], []
    for entry in records:
        if entry['request'] == 6 and entry['bm_request_type'] == 0x80 and entry['value'] >> 8 == 1:
            data = bytes.fromhex(entry['in'])
            if len(data) >= 18 and data[:2] == b'\x12\x01':
                descriptors.append({'frame': entry.get('completion_frame', entry['frame']), 'status': entry['status'], 'bus': entry['bus'],
                                    'device': entry['device'], 'vid': f'{int.from_bytes(data[8:10], "little"):04x}',
                                    'pid': f'{int.from_bytes(data[10:12], "little"):04x}',
                                    'hex': data.hex(' ')})
        if ((entry['bm_request_type'], entry['request']) in ((0x21, 9), (0xa1, 1))
                and entry['value'] >> 8 == 3):
            features.append(entry)
    return {'capture_sha256': hashlib.sha256(blob).hexdigest(), 'hardware_access': False,
            'device_descriptors': descriptors, 'feature_transactions': features,
            'note': 'Raw USB payload excludes a synthetic API Report ID prefix. Check status before counting writes.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(analyze(args.capture.read_bytes()), ensure_ascii=False, indent=2) + '\n')
