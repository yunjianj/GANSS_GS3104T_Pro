#!/usr/bin/env python3
"""Verify this project's official Windows capture against EXE RCDATA/4000.

Offline only. Emits a compact report without the updater's unused process-memory
tail bytes. It never flashes or opens a hardware node.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from ganss.firmware import load_official
from analysis.tools.read_usbpcap import analyze


def verify(capture=None):
    capture = capture or ROOT / 'local/captures/GS3104T-official-update.pcapng'
    raw = capture.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == '2255815a117f5d0bc6eb13a6db1a94b4796e5f3e0dca73be0bf10fb475b05644'
    parsed = analyze(raw)
    rows = parsed['feature_transactions']
    assert len(rows) == 1032 and all(row['status'] == 0 for row in rows)
    assert {(r['bus'], r['device'], r['interface']) for r in rows} == {(2, 3, 2)}
    out = [(r, bytes.fromhex(r['out'])) for r in rows if r['out']]
    incoming = [(r, bytes.fromhex(r['in'])) for r in rows if r['in']]
    assert len(out) == 1029 and len(incoming) == 3
    assert out[0][1][:8] == bytes.fromhex('aa42895aff7162cc')
    assert out[1][1][:4] == bytes.fromhex('01aa5500')
    assert out[2][1][:8] == bytes.fromhex('03aa550000005a5a')
    assert out[3][1][:10] == bytes.fromhex('05aa5500000000000004')
    assert out[-1][1][:8] == bytes.fromhex('07aa550000000000')
    assert incoming[0][1][:18] == bytes.fromhex('01aa5500fafafafa2003000000005a5a1e00')
    for _, reply in incoming[1:]:
        assert len(reply) == 64 and reply[:8] == bytes.fromhex('05aa5500fafafafa')
    image, metadata = load_official()
    data = b''.join(packet for _, packet in out[4:-1])
    assert len(data) == 65536 and data == image
    checksum = sum(w[0] for w in struct.iter_unpack('<H', data)) & 65535
    assert checksum == int.from_bytes(incoming[-1][1][8:10], 'little') == 0x2765
    exe = (ROOT / 'ganss/data/official-firmware.exe').read_bytes()
    offset = exe.find(image)
    assert offset == 0x1b964c and exe.find(image, offset + 1) == -1
    output = ROOT / 'analysis/firmware/official-rcdata-4000.bin'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(image)
    return {
        'capture_sha256': parsed['capture_sha256'], 'firmware': metadata,
        'resource_file_offset': hex(offset), 'resource_rva': '0x1c564c',
        'feature_transactions': len(rows), 'all_usb_status_success': True,
        'observed_usb': {'vid': '05ac', 'pid': '024f', 'interface': 2,
                         'note': 'Capture observation only; never hardcoded as device selection.'},
        'source_image_equals_all_captured_blocks': True,
        'program_blocks_64': len(out[4:-1]), 'checksum16_from_device': checksum,
        'frames': {'enter_isp': 121, 'query': 123, 'query_reply': 125,
                   'code_options': 127, 'program_request': 129, 'program_ack': 131,
                   'first_data': 133, 'last_data': 2179, 'checksum_reply': 2181,
                   'return_user': 2183, 'post_update_descriptor': 2206},
        'separate_erase_commands_sent': False, 'linux_hardware_update_verified': False,
        'native_resource_load_va': '0x4261d4..0x426276',
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', type=Path, help='Local original PCAPNG; not needed for public unit tests')
    args = parser.parse_args()
    report = verify(args.capture)
    destination = ROOT / 'analysis/evidence/windows-update-verification.json'
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(destination)
