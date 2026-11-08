#!/usr/bin/env python3
"""Extract old native driver's light-mode seed records, without running it."""
import csv
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / 'local/samples'
EXE = SAMPLES / 'GS3104T-PRO/DeviceDriver.exe'
OUT = ROOT / 'analysis/evidence'
data = EXE.read_bytes()
pe = struct.unpack_from('<I', data, 0x3c)[0]
count = struct.unpack_from('<H', data, pe + 6)[0]
opt_size = struct.unpack_from('<H', data, pe + 20)[0]
base = struct.unpack_from('<I', data, pe + 24 + 28)[0]
sections = []
for i in range(count):
    pos = pe + 24 + opt_size + 40 * i
    size, rva, raw_size, raw = struct.unpack_from('<IIII', data, pos + 8)
    sections.append((base + rva, raw_size, raw))


def utf16_at(va):
    for start, length, raw in sections:
        if start <= va < start + length:
            offset = raw + va - start
            end = offset
            while data[end:end + 2] != b'\0\0':
                end += 2
            return data[offset:end].decode('utf-16le')
    raise ValueError(hex(va))


def disassemble(start, end):
    return subprocess.check_output([
        'objdump', '-d', '-Mintel', f'--start-address={start}',
        f'--stop-address={end}', str(EXE)], text=True)


languages = {}
for locale in ('2052', '1033'):
    path = SAMPLES / f'GS3104T-PRO/language/{locale}.lan'
    languages[locale] = dict(re.findall(r'^(\d+)=(.*)$', path.read_text(encoding='utf-16'), re.M))

seed = disassemble('0x42b320', '0x42b75b')
records = []
lang_id = mode = flags = pointer = None
for line in seed.splitlines():
    match = re.search(r'push\s+(0x[0-9a-f]+)$', line)
    if match:
        va = int(match[1], 16)
        if 0x56e41c <= va <= 0x56e4b4:
            pointer, lang_id = hex(va), int(utf16_at(va))
    match = re.search(r'mov\s+DWORD PTR \[ebp-0x34\],(0x[0-9a-f]+)$', line)
    if match:
        mode = int(match[1], 16)
    match = re.search(r'mov\s+DWORD PTR \[ebp-0x18\],(0x[0-9a-f]+)$', line)
    if match:
        flags = int(match[1], 16)
    if re.search(r'call\s+0x408020$', line):
        assert None not in (lang_id, mode, flags, pointer)
        records.append({'mode': mode, 'name_zh': languages['2052'][str(lang_id)].strip(),
                        'name_en': languages['1033'][str(lang_id)].strip(),
                        'language_id': lang_id, 'language_id_string_va': pointer,
                        'insert_call_va': '0x' + line.strip().split(':')[0],
                        'config_func_raw': flags, 'physical_effect_verified': mode == 11})
        lang_id = mode = flags = pointer = None
assert len(records) == 20
assert [r['mode'] for r in records] == list(range(1, 20)) + [0]
assert [r['language_id'] for r in records] == list(range(521, 541))
result = {'source': str(EXE.relative_to(ROOT)),
          'source_sha256': hashlib.sha256(data).hexdigest(),
          'scope': 'Old native driver built-in lighting modes; not a firmware-wide capability query.',
          'modes': records}
OUT.mkdir(parents=True, exist_ok=True)
(OUT / 'light-modes.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
with (OUT / 'light-modes.csv').open('w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=list(records[0]))
    writer.writeheader()
    writer.writerows(records)
evidence = [seed]
for start, end in [('0x408020', '0x4081c7'), ('0x408520', '0x4086e0'),
                   ('0x4086e0', '0x408940'), ('0x42a620', '0x42a840'),
                   ('0x42aab0', '0x42ab90'), ('0x42b0a0', '0x42b318')]:
    evidence.append(disassemble(start, end))
(OUT / 'light-modes-disasm.txt').write_text('\n'.join(evidence))
for record in records:
    print(f"{record['mode']:2}  {record['name_zh']}  lang={record['language_id']}")
