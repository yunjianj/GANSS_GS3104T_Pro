#!/usr/bin/env python3
"""Reproduce local, offline evidence extraction. Never opens a HID device."""
import csv
import hashlib
import json
import re
import struct
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'analysis'
SAMPLES = ROOT / 'local/samples'


def dump(name, value):
    p = OUT / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def parse_hex(path):
    memory, base, types, eof = {}, 0, Counter(), False
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        assert line.startswith(':'), number
        rec = bytes.fromhex(line[1:])
        assert len(rec) == rec[0] + 5 and sum(rec) % 256 == 0, number
        size, address, typ = rec[0], int.from_bytes(rec[1:3], 'big'), rec[3]
        data = rec[4:4 + size]
        types[typ] += 1
        if typ == 0:
            for i, byte in enumerate(data):
                a = base + address + i
                assert a not in memory or memory[a] == byte, (number, a)
                memory[a] = byte
        elif typ == 1:
            eof = True
        elif typ == 2:
            base = int.from_bytes(data, 'big') << 4
        elif typ == 4:
            base = int.from_bytes(data, 'big') << 16
        else:
            assert typ in (3, 5), (number, typ)
    assert eof and memory
    addresses = sorted(memory)
    ranges, start, end = [], addresses[0], addresses[0]
    for a in addresses[1:]:
        if a != end + 1:
            ranges.append([start, end + 1])
            start = a
        end = a
    ranges.append([start, end + 1])
    # Separate ranges avoid allocating a huge file for distant option bytes.
    regions = []
    for start, end in ranges:
        data = bytes(memory[a] for a in range(start, end))
        name = f'firmware/region_{start:08x}_{end:08x}.bin'
        (OUT / name).write_bytes(data)
        regions.append({'start': hex(start), 'end_exclusive': hex(end), 'size': len(data),
                        'file': name, 'sha256': hashlib.sha256(data).hexdigest()})
    first = bytes(memory.get(i, 0xff) for i in range(64))
    descriptors = []
    for a in addresses:
        if memory[a] == 18 and memory.get(a + 1) == 1:
            d = bytes(memory.get(a + i, 0xff) for i in range(18))
            if d[2:4] in (b'\x00\x02', b'\x10\x01') and d[7] in (8, 16, 32, 64):
                descriptors.append({'address': hex(a), 'hex': d.hex(' '),
                                    'vid': f'{int.from_bytes(d[8:10], "little"):04x}',
                                    'pid': f'{int.from_bytes(d[10:12], "little"):04x}'})
    return {'record_types': dict(types), 'checksums_valid': True, 'eof_present': eof,
            'unique_data_bytes': len(memory), 'regions': regions,
            'initial_sp': hex(struct.unpack_from('<I', first)[0]),
            'reset_vector': hex(struct.unpack_from('<I', first, 4)[0]),
            'usb_device_descriptor_candidates': descriptors}


def main():
    samples = []
    for directory in ('GS3104T-PRO', 'GS3104T-PRO驱动+固件 20241015'):
        for p in sorted((SAMPLES / directory).rglob('*')):
            if p.is_file():
                samples.append({'file': str(p.relative_to(ROOT)), 'size': p.stat().st_size,
                                'sha256': hashlib.sha256(p.read_bytes()).hexdigest()})
    dump('evidence/sample-manifest.json', samples)
    updater = next((SAMPLES / 'GS3104T-PRO驱动+固件 20241015').glob('DSM*.exe'))
    with zipfile.ZipFile(updater) as z:
        for name in z.namelist():
            if name.endswith(('.hex', '.ini', '.txt')):
                relative = Path(name.replace('\\', '/'))
                assert not relative.is_absolute() and '..' not in relative.parts
                p = OUT / 'firmware' / relative
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(z.read(name))
                if p.suffix in ('.ini', '.txt'):
                    data = p.read_bytes()
                    encoding = 'utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'cp950'
                    p.with_suffix(p.suffix + '.utf8.txt').write_text(data.decode(encoding), encoding='utf-8')
    dump('evidence/firmware-summary.json', parse_hex(OUT / 'firmware/Settings/FWFiles/SN32F290.hex'))
    firmware = (OUT / 'firmware/region_00000000_000167f4.bin').read_bytes()
    start = 0x119c7  # Sample-specific configuration descriptor, not a scan heuristic.
    total = int.from_bytes(firmware[start + 2:start + 4], 'little')
    pos, descriptors = start, []
    while pos < start + total:
        size, typ = firmware[pos:pos + 2]
        assert size >= 2 and pos + size <= start + total
        data = firmware[pos:pos + size]
        item = {'address': hex(pos), 'type': typ, 'hex': data.hex(' ')}
        if typ == 4:
            item.update(interface=data[2], endpoints=data[4], cls=data[5],
                        subclass=data[6], protocol=data[7])
        elif typ == 5:
            item.update(endpoint=hex(data[2]), attributes=data[3],
                        max_packet=int.from_bytes(data[4:6], 'little'), interval=data[6])
        elif typ == 0x21:
            item.update(report_descriptor_length=int.from_bytes(data[7:9], 'little'))
        descriptors.append(item)
        pos += size
    assert pos == start + total
    dump('evidence/firmware-usb-config.json', descriptors)
    driver = (SAMPLES / 'GS3104T-PRO/DeviceDriver.exe').read_bytes()
    schemas = [s.decode('ascii').replace('\t', ' ').rstrip(';') + ';'
               for s in driver.split(b'\0') if s.startswith(b'CREATE TABLE IF NOT EXISTS t_')]
    (OUT / 'evidence/database-schema.sql').write_text('\n\n'.join(schemas) + '\n')
    tree = ET.parse(SAMPLES / 'GS3104T-PRO/layouts/rgb-keyboard.xml')
    keys = [dict(k.attrib) for k in tree.findall('.//key')]
    dump('evidence/keymap.json', keys)
    with (OUT / 'evidence/keymap.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(keys[0]))
        writer.writeheader()
        writer.writerows(keys)
    dump('evidence/layout-summary.json', {
        'key_count': len(keys),
        'unique_key_indices': len({k['key_index'] for k in keys}),
        'max_key_index': max(int(k['key_index']) for k in keys),
        'key_light_indices_identical': all(k['key_index'] == k['light_index'] for k in keys),
        'options': {e.tag: e.attrib for e in tree.getroot() if e.tag != 'Keyboard'}})
    disasm = OUT / 'evidence/driver-disasm.txt'
    if disasm.exists():
        lines = disasm.read_text().splitlines()
        # Exact byte patterns are evidence, not an automatic semantic decoder.
        commands = []
        for line in lines:
            m = re.search(r'^\s*([0-9a-f]+):.*mov\s+WORD PTR \[[^]]+\],0x([0-9a-f]+)$', line)
            if m and int(m[2], 16) & 255 == 4:
                commands.append({'va': '0x' + m[1], 'business_prefix': bytes.fromhex(m[2].zfill(4))[::-1].hex(' ')})
        dump('evidence/command-immediates.json', commands)
        ranges = [(0x44ed70, 0x44f7f5), (0x450990, 0x451410), (0x418230, 0x4185d3),
                  (0x4185e0, 0x419180), (0x42ae50, 0x42b318),
                  (0x414230, 0x414480), (0x44b7b0, 0x44b8b2)]
        selected = []
        for line in lines:
            m = re.match(r'\s*([0-9a-f]+):', line)
            if m and any(lo <= int(m[1], 16) < hi for lo, hi in ranges):
                selected.append(line)
        (OUT / 'evidence/protocol-disasm.txt').write_text('\n'.join(selected) + '\n')
    comparisons = []
    for p in (OUT / 'installer/app').rglob('*'):
        if p.is_file():
            q = SAMPLES / 'GS3104T-PRO' / p.relative_to(OUT / 'installer/app')
            comparisons.append({'file': str(p.relative_to(OUT / 'installer/app')),
                                'identical': q.exists() and p.read_bytes() == q.read_bytes()})
    dump('evidence/installer-comparison.json', comparisons)
    print('Offline extraction complete; see analysis/evidence/*.json')


if __name__ == '__main__':
    main()
