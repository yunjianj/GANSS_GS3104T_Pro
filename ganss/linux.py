"""Linux hidraw transport; no hidapi, libusb or third party dependency."""
from dataclasses import dataclass, field
import fcntl
import hashlib
import os
from pathlib import Path
import select
import re
import stat
import struct
import time

from .protocol import ConfigError

SUPPORTED_ID = (0x05AC, 0x024F)


def ioc(direction, number, size):
    # Linux asm-generic ioctl ABI, used by x86/x86_64 and ARM/aarch64.
    return (direction << 30) | (size << 16) | (ord('H') << 8) | number


@dataclass
class Capabilities:
    reports: dict = field(default_factory=lambda: {'input': {}, 'output': {}, 'feature': {}})
    usage_pages: set = field(default_factory=set)
    numbered: bool = False

    def size(self, kind, report_id=0):
        return (self.reports[kind].get(report_id, 0) + 7) // 8

    def supports(self, connection):
        if self.numbered or not any(p >= 0xFF00 for p in self.usage_pages):
            return False
        if connection == 'usb':
            return self.size('feature') == 64
        # The receiver exposes both 32-byte and 64-byte vendor interfaces.
        # The driver's 33-byte short API reports belong to the 32-byte one.
        return self.size('output') == 32 and self.size('input') == 32

    def as_dict(self):
        return {'usage_pages': [f'{p:04X}' for p in sorted(self.usage_pages)],
                'numbered': self.numbered,
                'reports_bytes': {kind: {str(k): (v + 7) // 8 for k, v in values.items()}
                                  for kind, values in self.reports.items()}}


def parse_descriptor(data):
    """Parse HID short items and global Push/Pop, accumulating report bit sizes."""
    result = Capabilities()
    state = {'page': 0, 'size': 0, 'count': 0, 'id': 0}
    stack, pos = [], 0
    while pos < len(data):
        prefix = data[pos]
        pos += 1
        if prefix == 0xFE:
            if pos + 2 > len(data):
                raise ConfigError('HID 长条目截断')
            length = data[pos]
            pos += 2 + length
            if pos > len(data):
                raise ConfigError('HID 长条目数据截断')
            continue
        size = (0, 1, 2, 4)[prefix & 3]
        typ, tag = (prefix >> 2) & 3, prefix >> 4
        if pos + size > len(data):
            raise ConfigError('HID 描述符条目截断')
        value = int.from_bytes(data[pos:pos + size], 'little')
        pos += size
        if typ == 1:
            if tag == 0:
                state['page'] = value
                result.usage_pages.add(value)
            elif tag in (7, 8, 9):
                state[{7: 'size', 8: 'id', 9: 'count'}[tag]] = value
                if tag == 8:
                    if not 1 <= value <= 255:
                        raise ConfigError('HID Report ID 无效')
                    result.numbered = True
            elif tag == 10:
                stack.append(state.copy())
            elif tag == 11:
                if not stack:
                    raise ConfigError('HID 全局状态栈下溢')
                state = stack.pop()
        elif typ == 0 and tag in (8, 9, 11):
            kind = {8: 'input', 9: 'output', 11: 'feature'}[tag]
            bits = state['size'] * state['count']
            if bits > 65536:
                raise ConfigError('HID 报告过大')
            reports = result.reports[kind]
            reports[state['id']] = reports.get(state['id'], 0) + bits
    if stack:
        raise ConfigError('HID 全局状态栈未闭合')
    return result


def _properties(path):
    return dict(line.split('=', 1) for line in path.read_text().splitlines() if '=' in line)


def supported_hardware(bus, vid, pid, name):
    """Match observed identities; preserve the raw HID name for display."""
    model = re.sub(r"[\s_-]", "", name).upper()
    return (bus == 3 and (vid, pid) == SUPPORTED_ID
            and model in ('GS3104TPRO', 'HFDGS3104TPRO', 'GS3104TPRO2.4G'))


def enumerate_devices(sysfs=Path('/sys/class/hidraw')):
    """Internal endpoint inventory. Filter identity before reporting any errors."""
    result = []
    for entry in sorted(sysfs.glob('hidraw*')):
        device = entry / 'device'
        try:
            props = _properties(device / 'uevent')
            bus, vid, pid = (int(p, 16) for p in props['HID_ID'].split(':'))
            name = props.get('HID_NAME', '')
        except (OSError, ValueError, KeyError):
            continue
        if not supported_hardware(bus, vid, pid, name):
            continue
        resolved = device.resolve()
        physical = next((p for p in (resolved, *resolved.parents)
                         if (p / 'idVendor').exists() and (p / 'idProduct').exists()), resolved)
        serial_file = physical / 'serial'
        try:
            serial = serial_file.read_text().strip() if serial_file.exists() else ''
        except OSError:
            serial = ''
        # USB device parent groups all HID interfaces of one physical unit.
        identity = str(physical)
        item = {'path': f'/dev/{entry.name}', 'bus': bus,
                'node_available': Path(f'/dev/{entry.name}').exists(),
                'readwrite_access': os.access(f'/dev/{entry.name}', os.R_OK | os.W_OK),
                'vid': f'{vid:04X}', 'pid': f'{pid:04X}', 'name': name,
                'physical': identity, 'serial': serial,
                'usb_supported': False, 'wireless_supported': False}
        try:
            caps = parse_descriptor((device / 'report_descriptor').read_bytes())
            item.update(usb_supported=caps.supports('usb'),
                        wireless_supported=caps.supports('wireless'), **caps.as_dict())
        except (OSError, ValueError) as e:
            item['error'] = str(e)
        result.append(item)
    return result


def scan_hardware(sysfs=Path('/sys/class/hidraw')):
    """Return physical keyboards/receivers, never unrelated HID hardware."""
    groups = {}
    for endpoint in enumerate_devices(sysfs):
        key = endpoint['physical']
        if key not in groups:
            stable = hashlib.sha256(key.encode()).hexdigest()[:12]
            groups[key] = {'id': stable, 'name': endpoint['name'],
                           'serial': endpoint['serial'], 'endpoints': []}
        groups[key]['endpoints'].append(endpoint)
    hardware = list(groups.values())
    names = [h['name'] for h in hardware]
    for h in hardware:
        if names.count(h['name']) > 1:
            h['name'] += f" [{h['id']}]"
        h['connections'] = [kind for kind in ('usb', 'wireless')
                            if any(e[f'{kind}_supported'] for e in h['endpoints'])]
        h['available'] = any(e['node_available'] and e['readwrite_access'] and
                             (e['usb_supported'] or e['wireless_supported'])
                             for e in h['endpoints'])
        h['access_status'] = ('ready' if h['available'] else
                              'permission_denied' if any(e['node_available'] and
                              (e['usb_supported'] or e['wireless_supported'])
                              for e in h['endpoints']) else
                              'node_missing' if h['connections'] else 'unsupported')
    return sorted(hardware, key=lambda h: h['name'])


def public_hardware(hardware):
    return {k: v for k, v in hardware.items() if k != 'endpoints'}


def select_hardware(name):
    matches = [h for h in scan_hardware() if name in (h['name'], h['id'])]
    if not matches:
        raise ConfigError(f'未找到硬件「{name}」，请运行 ganss scan 后使用扫描结果中的硬件名')
    if len(matches) != 1:
        raise ConfigError(f'硬件名「{name}」不唯一，请使用扫描结果中的完整名称或 ID')
    return matches[0]


def select_transport(hardware):
    # Prefer the native Feature protocol if available. Interface numbers and
    # hidraw paths never participate in capability selection.
    for connection in ('usb', 'wireless'):
        endpoints = [e for e in hardware['endpoints'] if e[f'{connection}_supported']]
        accessible = [e for e in endpoints if e['node_available'] and e.get('readwrite_access', True)]
        candidates = accessible or endpoints
        if len(candidates) == 1:
            return candidates[0]['path'], connection
        if candidates:
            raise ConfigError(f"硬件「{hardware['name']}」存在多个相同能力的配置通道，无法唯一识别")
    raise ConfigError(f"硬件「{hardware['name']}」没有可识别的配置通道")


class Hidraw:
    def __init__(self, path, connection='usb', trace=None):
        self.path, self.connection, self.trace = path, connection, trace
        self.fd = None

    def __enter__(self):
        try:
            self.fd = os.open(self.path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
            if not stat.S_ISCHR(os.fstat(self.fd).st_mode):
                raise ConfigError('设备路径必须为 hidraw 字符设备')
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            info = bytearray(8)
            fcntl.ioctl(self.fd, ioc(2, 3, 8), info, True)
            bus, vid, pid = struct.unpack('IHH', info)
            name_buf = bytearray(256)
            fcntl.ioctl(self.fd, ioc(2, 4, 256), name_buf, True)
            name = name_buf.split(b'\0', 1)[0].decode('utf-8', errors='replace')
            if not supported_hardware(bus, vid, pid, name):
                raise ConfigError(f'设备身份不匹配: bus={bus} {vid:04X}:{pid:04X}；仅支持 USB HID 05AC:024F')
            size_buf = bytearray(4)
            fcntl.ioctl(self.fd, ioc(2, 1, 4), size_buf, True)
            size = struct.unpack('I', size_buf)[0]
            if not 1 <= size <= 4096:
                raise ConfigError('HID 描述符长度无效')
            desc = bytearray(struct.pack('I', size) + bytes(4096))
            fcntl.ioctl(self.fd, ioc(2, 2, 4100), desc, True)
            self.caps = parse_descriptor(desc[4:4 + size])
            if not self.caps.supports(self.connection):
                raise ConfigError(f'所选接口不支持 {self.connection} 协议，运行 scan 重新扫描硬件')
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def log(self, direction, channel, data):
        if self.trace:
            self.trace({'direction': direction, 'channel': channel, 'hex': bytes(data).hex(' ')})

    def send_feature(self, data):
        self.log('send', 'feature', data)
        buf = bytearray(data)
        count = fcntl.ioctl(self.fd, ioc(3, 6, len(buf)), buf, True)
        if count != len(data):
            raise OSError(f'Feature 写入不完整: {count}/{len(data)}')

    def get_feature(self):
        buf = bytearray(65)
        count = fcntl.ioctl(self.fd, ioc(3, 7, 65), buf, True)
        # Unnumbered USB GET_REPORT commonly returns 64 bytes without ID.
        # Drivers returning a 65-byte API representation must include ID zero.
        if count == 64:
            payload = bytes(buf[:64])
        elif count == 65 and buf[0] == 0:
            payload = bytes(buf[1:65])
        else:
            raise OSError(f'Feature 响应长度或 Report ID 异常: {count}')
        self.log('receive', 'feature', payload)
        return payload

    def short_exchange(self, data):
        # Discard previously queued unrelated keyboard notifications before writing.
        drained = 0
        while select.select([self.fd], [], [], 0)[0]:
            os.read(self.fd, 4096)
            drained += 1
            if drained >= 256:
                raise OSError('输入队列持续繁忙，未发送配置')
        report = data.ljust(self.caps.size('output') + 1, b'\0')
        self.log('send', 'short', report)
        count = os.write(self.fd, report)
        if count != len(report):
            raise OSError(f'Output 写入不完整: {count}/{len(report)}')
        deadline = time.monotonic() + 0.4
        while time.monotonic() < deadline:
            if not select.select([self.fd], [], [], max(0, deadline - time.monotonic()))[0]:
                break
            response = os.read(self.fd, 4096)
            self.log('receive', 'short', response)
            if len(response) == self.caps.size('input') + 1 and response[0] == 0:
                response = response[1:]
            if len(response) >= 3 and response[:3] == data[1:4]:
                return
        raise TimeoutError('未收到匹配的无线命令回显；事务未确认，后续报文已停止')
