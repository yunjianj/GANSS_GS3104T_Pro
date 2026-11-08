#!/usr/bin/env python3
"""Read generic ISP profiles from the pinned original EXE; never opens USB."""
import hashlib
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[2]
SHA256 = '1191ad14fabd9c448d45622bab257947464c1c32432aec2d5fd3a1eb04b2e64f'


def extract():
    blob = (ROOT / 'ganss/data/official-firmware.exe').read_bytes()
    assert hashlib.sha256(blob).hexdigest() == SHA256
    pe = struct.unpack_from('<I', blob, 0x3c)[0]
    count, optsize = struct.unpack_from('<H', blob, pe + 6)[0], struct.unpack_from('<H', blob, pe + 20)[0]
    sections = []
    for n in range(count):
        at = pe + 24 + optsize + n * 40
        vsize, rva, size, offset = struct.unpack_from('<IIII', blob, at + 8)
        sections.append((0x400000 + rva, max(vsize, size), offset))
    def read(va, size):
        for start, length, offset in sections:
            if start <= va and va + size <= start + length:
                return blob[offset + va - start:offset + va - start + size]
        raise ValueError(hex(va))
    def dword(raw, offset):
        return struct.unpack_from('<I', raw, offset)[0]
    def word(raw, offset):
        return struct.unpack_from('<H', raw, offset)[0]
    profiles = []
    for index in (14, 18):
        va = 0x5b2bb8 + index * 51
        row = read(va, 51)
        layout_va = dword(row, 39)
        layout = read(layout_va, 59)
        profiles.append({
            'index': index, 'label': read(dword(row, 0), 32).split(b'\0')[0].decode('ascii'),
            'chip_record_va': hex(va), 'chip_record_hex': row.hex(' '),
            'query_id_hex': row[16:20].hex(' '),
            'template_boot_vid': f'{word(row, 12):04x}',
            'template_boot_pid': f'{word(row, 14):04x}',
            'dispatch_type': dword(row, 27),
            'memory_layout_va': hex(layout_va), 'memory_layout_hex': layout.hex(' '),
            'template_flash_bytes': dword(layout, 13),
            'erase_count': word(layout, 39), 'write_blocks_64': word(layout, 43),
            'hex_unspecified_fill': 0 if index == 14 else 255,
            'early_code_options_03': index == 14,
            'separate_02_erase_options_04_erase_flash': index != 14,
            'generic_profile_only': True,
            'windows_runtime_selection_verified': False,
        })
    return {'source_sha256': SHA256,
            'notes': 'Static generic tables do not prove actual silicon capacity or Windows runtime selection.',
            'profiles': profiles,
            'code_evidence': {
                'hex_fill': '0x4216E7..0x42170A',
                'query_match': '0x406E59..0x406F64',
                '2003_early_code_options': '0x407008..0x4070FC',
                'skip_erase_for_index_14': '0x4072C6..0x4072D5 -> 0x407566',
                'block_count': '0x407245 / 0x407592..0x40759C',
                'data_loop': '0x4076BD..0x407749',
                'checksum_range': '0x4077C6..0x4077DF',
            }}


if __name__ == '__main__':
    output = ROOT / 'analysis/evidence/updater-isp-profiles.json'
    output.write_text(json.dumps(extract(), ensure_ascii=False, indent=2) + '\n')
    print(output)
