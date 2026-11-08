#!/usr/bin/env python3
"""Execute the original x86 ISP routine offline with a simulated HID device.

Requires unicorn in an analysis-only environment. No USB/filesystem transport
exists in the emulator. Windows UI/string/time helpers are stubbed; the native
branching, command builders, block loop and checksum function run unchanged.
This does not establish which template Windows selects on the real keyboard.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import struct
import zipfile

from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_ECX, UC_X86_REG_EDI, UC_X86_REG_ESP, UC_X86_REG_EIP

ROOT = Path(__file__).resolve().parents[2]
SHA256 = '1191ad14fabd9c448d45622bab257947464c1c32432aec2d5fd3a1eb04b2e64f'


def run(index, *, entry='engine', query_id=None):
    exe = (ROOT / 'ganss/data/official-firmware.exe').read_bytes()
    assert hashlib.sha256(exe).hexdigest() == SHA256
    u = Uc(UC_ARCH_X86, UC_MODE_32)
    u.mem_map(0, 0x1000)  # FS:[0], simulated Windows exception-chain storage
    u.mem_map(0x400000, 0x200000)
    pe = struct.unpack_from('<I', exe, 0x3c)[0]
    sections, optsize = struct.unpack_from('<H', exe, pe + 6)[0], struct.unpack_from('<H', exe, pe + 20)[0]
    for n in range(sections):
        at = pe + 24 + optsize + n * 40
        _, rva, size, offset = struct.unpack_from('<IIII', exe, at + 8)
        u.mem_write(0x400000 + rva, exe[offset:offset + size])
    u.mem_map(0x1000000, 0x100000)
    u.mem_map(0x2000000, 0x100000)
    u.mem_map(0x3000000, 0x1000)
    main, task, fwaddr, string = 0x2000000, 0x2001000, 0x2010000, 0x2060010
    def put(a, x):
        u.mem_write(a, struct.pack('<I', x))
    def word(a):
        return struct.unpack('<I', u.mem_read(a, 4))[0]
    def reg(r):
        return u.reg_read(r)
    def ret(value=None, pop=0):
        sp = reg(UC_X86_REG_ESP)
        destination = word(sp)
        u.reg_write(UC_X86_REG_ESP, sp + 4 + pop)
        if value is not None:
            u.reg_write(UC_X86_REG_EAX, value)
        u.reg_write(UC_X86_REG_EIP, destination)
    with zipfile.ZipFile(io.BytesIO(exe)) as archive:
        raw = archive.read('Settings\\FWFiles\\SN32F290.hex')
    image = bytearray(bytes([0 if index in (13, 14) else 255]) * 0x40000)
    base = 0
    for line in raw.decode().splitlines():
        rec = bytes.fromhex(line[1:])
        assert sum(rec) & 255 == 0
        if rec[3] == 4:
            base = int.from_bytes(rec[4:-1], 'big') << 16
        elif rec[3] == 0:
            start = base + int.from_bytes(rec[1:3], 'big')
            image[start:start + rec[0]] = rec[4:-1]
    u.mem_write(fwaddr, bytes(image))
    put(main + 0x18, 2)
    put(main + 0xb8, 1)
    u.mem_write(main + 8, bytes.fromhex('00 00 5a 5a'))
    put(task + 4, index)
    put(task + 0x14, fwaddr)
    put(task + 0x20, 1)
    u.mem_write(task + 0x18, bytes.fromhex('ac 05 4f 02'))
    put(string - 4, 1000000)
    # Simulated MFC string allocator/vtable, never the Windows allocator.
    put(0x2061000, 0x2061100)
    put(0x206110c, 0x3000030)
    put(0x55b870, 0x3000010)  # SendMessageW
    put(0x55b7bc, 0x3000020)  # MessageBoxW (error paths)
    events, blocks, written, last, remaining = [], [], bytearray(), None, 0

    def hook(_, address, size, data):
        nonlocal last, remaining
        sp = reg(UC_X86_REG_ESP)
        if address == 0x427620:
            pointer, length = word(sp + 12), word(sp + 16)
            payload = bytes(u.mem_read(pointer, length))
            if remaining:
                assert length == 64
                blocks.append(payload)
                written.extend(payload)
                remaining -= 1
            else:
                events.append({'direction': 'out', 'hex': payload.hex(' ')})
                last = payload
                if payload[0] == 5:
                    remaining = int.from_bytes(payload[8:10], 'little')
            ret(1)
        elif address == 0x427770:
            pointer, length = word(sp + 12), word(sp + 16)
            assert last is not None
            response = bytearray(last[:4] + b'\xfa' * 4 + bytes(56))
            if last[0] == 1:
                response[8:12] = query_id or (b'\x20\x03\0\0' if index == 14 else b'\x20\x05\0\0')
                response[12:18] = bytes.fromhex('00 00 5a 5a 1e 00')
            if last[0] == 5 and not remaining:
                checksum = sum(w[0] for w in struct.iter_unpack('<H', written)) & 65535
                response[8:10] = checksum.to_bytes(2, 'little')
            u.mem_write(pointer, bytes(response[:length]))
            events.append({'direction': 'in', 'hex': response[:length].hex(' ')})
            ret(1)
        elif address == 0x40a4f0:
            put(reg(UC_X86_REG_ECX), string)
            ret(reg(UC_X86_REG_ECX), 4)
        elif address == 0x43a694:
            ret(0x2061000)
        elif address == 0x3000030:
            ret(string - 16)
        elif address == 0x409d60:
            ret(word(sp + 4))
        elif address == 0x409e70:
            put(word(sp + 4), string)
            ret()
        elif address == 0x409ef0:
            ret()
        elif address == 0x40c6a0:
            events.append({'stubbed_enter_isp': 'aa 42 89 5a ff 71 62 cc'})
            ret(1, 16)
        elif address == 0x4095d0:
            events.append({'native_result': reg(UC_X86_REG_ECX)})
            ret(int(reg(UC_X86_REG_ECX) == 5), 8)
        elif address in (0x4097d0, 0x409590):
            ret(pop=4)
        elif address in (0x40a5d0, 0x52733f):
            ret()
        elif address in (0x3000010, 0x3000020):
            ret(1, 16)
    u.hook_add(UC_HOOK_CODE, hook)
    put(0x10ffff0, 0x3000000)
    u.reg_write(UC_X86_REG_ESP, 0x10ffff0)
    put(0x10ffff4, task)
    u.reg_write(UC_X86_REG_ECX, task if entry == 'engine' else main)
    u.reg_write(UC_X86_REG_EDI, main)
    entry_va = 0x407210 if entry == 'engine' else 0x406bb0
    u.emu_start(entry_va, 0x3000000, count=10000000)
    assert reg(UC_X86_REG_EIP) == 0x3000000, hex(reg(UC_X86_REG_EIP))
    return {'source_sha256': SHA256, 'entry_va': hex(entry_va), 'template_index': index,
            'query_id_override': query_id.hex(' ') if query_id else None,
            'simulated': True, 'hardware_access': False, 'result': reg(UC_X86_REG_EAX) & 255,
            'events': events, 'data_blocks': len(blocks), 'written_bytes': len(written),
            'written_sha256': hashlib.sha256(written).hexdigest(),
            'checksum16': sum(w[0] for w in struct.iter_unpack('<H', written)) & 65535}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', choices=(14, 18), type=int, required=True)
    parser.add_argument('--entry', choices=('engine', 'flow'), default='engine')
    parser.add_argument('--query-id', help='Four simulated query bytes, e.g. 20030000')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    query_id = bytes.fromhex(args.query_id) if args.query_id else None
    if query_id is not None and len(query_id) != 4:
        parser.error('--query-id must contain four bytes')
    args.output.write_text(json.dumps(run(args.index, entry=args.entry, query_id=query_id), ensure_ascii=False, indent=2) + '\n')
