"""Bundled official RCDATA firmware and capture-verified legacy 20 03 ISP flow.

The Windows EXE is a resource container only; it is never executed. See
docs/firmware-update.md for source hashes, protocol evidence and limitations.
"""
from contextlib import contextmanager
from importlib.resources import files
import copy
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import struct
import threading
import time
import uuid

from .linux import (Hidraw, ioc, parse_descriptor, public_hardware,
                    select_hardware, select_transport, supported_hardware)
from .protocol import ConfigError

FIRMWARE_ID = 'gs3104t-pro-v1.20-20250722-2765'
EXE_NAME = 'DSM318_108K_RGB三模_HFD64KC200_V1.20_20250722_0x2765.exe'
EXE_SHA256 = '1191ad14fabd9c448d45622bab257947464c1c32432aec2d5fd3a1eb04b2e64f'
IMAGE_SHA256 = '42924f581b208c23ea4818c6a4d6db59669dfb4059074b99b635a2ee13c73fed'
FLASH_SIZE = 0x10000
EXPECTED_ISP_ID = bytes.fromhex('20 03 00 00')
# Generic template identities; the real keyboard can retain its normal ID.
BOOT_IDS = frozenset(((0x0C45, 0x7140), (0x0C45, 0x7040)))
ENTER_ISP = bytes.fromhex('AA42895AFF7162CC')
OPERATION_LOCK = threading.Lock()


class FirmwareError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@contextmanager
def device_operation():
    if not OPERATION_LOCK.acquire(blocking=False):
        raise FirmwareError('device_busy', '后端正在执行设备配置或固件更新，请等待完成')
    try:
        yield
    finally:
        OPERATION_LOCK.release()


def pe_resource(blob, resource_id, *, resource_type=10, language=0):
    """Bounded PE32 resource lookup by type / numeric ID / language, not ZIP."""
    try:
        def read(offset, size):
            if offset < 0 or size < 0 or offset + size > len(blob):
                raise ValueError('PE 资源越界或截断')
            return blob[offset:offset + size]
        def unpack(fmt, offset):
            return struct.unpack(fmt, read(offset, struct.calcsize(fmt)))
        if read(0, 2) != b'MZ':
            raise ValueError('缺少 DOS 标记')
        pe, = unpack('<I', 0x3c)
        if read(pe, 4) != b'PE\0\0':
            raise ValueError('缺少 PE 标记')
        count, = unpack('<H', pe + 6)
        optsize, = unpack('<H', pe + 20)
        opt = pe + 24
        if unpack('<H', opt)[0] != 0x10b or optsize < 120 or not 1 <= count <= 96:
            raise ValueError('不支持的 PE 布局')
        sections = []
        for n in range(count):
            _, rva, size, offset = unpack('<IIII', opt + optsize + n * 40 + 8)
            sections.append((rva, size, offset))
        def mapped(rva, size):
            for base, length, offset in sections:
                if base <= rva and size <= length and rva - base <= length - size:
                    position = offset + rva - base
                    read(position, size)
                    return position
            raise ValueError('资源 RVA 不在文件节范围内')
        resource_rva, resource_size = unpack('<II', opt + 112)
        root = mapped(resource_rva, resource_size)
        def resource_read(relative, size):
            if relative < 0 or size < 0 or relative + size > resource_size:
                raise ValueError('资源目录范围异常')
            return read(root + relative, size)
        directory = 0
        for depth, wanted in enumerate((resource_type, resource_id, language)):
            header = resource_read(directory, 16)
            named, ids = struct.unpack_from('<HH', header, 12)
            if named + ids > 4096:
                raise ValueError('资源目录项过多')
            entries = resource_read(directory + 16, 8 * (named + ids))
            values = [target for key, target in struct.iter_unpack('<II', entries) if key == wanted]
            if len(values) != 1:
                raise ValueError(f'资源项 {wanted} 缺失或重复')
            target = values[0]
            if bool(target & 0x80000000) != (depth < 2):
                raise ValueError('资源目录层级异常')
            directory = target & 0x7fffffff
        rva, size, _, _ = struct.unpack('<IIII', resource_read(directory, 16))
        return read(mapped(rva, size), size)
    except (ValueError, struct.error) as error:
        raise FirmwareError('firmware_invalid', str(error)) from error


def load_official():
    blob = files('ganss').joinpath('data/official-firmware.exe').read_bytes()
    if hashlib.sha256(blob).hexdigest() != EXE_SHA256:
        raise FirmwareError('firmware_invalid', '随包官方 EXE 的 SHA256 不匹配')
    image = pe_resource(blob, 4000)
    if len(image) != FLASH_SIZE or hashlib.sha256(image).hexdigest() != IMAGE_SHA256:
        raise FirmwareError('firmware_invalid', '官方 RCDATA/4000 固件长度或 SHA256 不匹配')
    if checksum16(image) != 0x2765 or image[-4:] != bytes.fromhex('5555aaaa'):
        raise FirmwareError('firmware_invalid', '官方固件校验和或末尾标记不匹配')
    sp, reset = struct.unpack_from('<II', image)
    if not 0x20000000 <= sp < 0x20010000 or not reset & 1 or reset >= FLASH_SIZE:
        raise FirmwareError('firmware_invalid', '官方固件向量表异常')
    target = pe_resource(blob, 4010).decode('utf-16le')
    if target != 'SN32F24xB' or pe_resource(blob, 4012) != b'\x01':
        raise FirmwareError('firmware_invalid', '官方固件目标或保护配置异常')
    return image, {'id': FIRMWARE_ID, 'name': EXE_NAME, 'version_label': 'V1.20',
                   'build_date_label': '2025-07-22', 'chip': target,
                   'chip_label_source': 'pe_resource_4010',
                   'source_sha256': EXE_SHA256, 'image_sha256': IMAGE_SHA256,
                   'resource': {'type': 10, 'id': 4000, 'language': 0},
                   'expected_isp_id_hex': EXPECTED_ISP_ID.hex(' '),
                   'populated_bytes': len(image), 'flash_bytes': len(image),
                   'checksum16': checksum16(image), 'bundled': True,
                   'windows_capture_verified': True,
                   'hardware_update_verified': False}


def checksum16(image):
    return sum(word[0] for word in struct.iter_unpack('<H', image)) & 0xFFFF


def command(code, params=b''):
    if len(params) > 60:
        raise ValueError('ISP 参数过长')
    return (bytes((code, 0xAA, 0x55, 0)) + params).ljust(64, b'\0')


def chip_info(response):
    if (len(response) != 64 or response[:4] != command(1)[:4]
            or response[4:8] != b'\xfa' * 4):
        raise FirmwareError('invalid_isp_response', '未收到有效 ISP 查询响应')
    identity = response[8:12]
    known = {b'\x20\x03\0\0': ('SN32F248B', 0x10000),
             b'\x20\x05\0\0': ('SN32F290', 0x40000)}
    name, size = known.get(identity, ('unknown', None))
    # These are generic updater table labels, not a measurement of the device's
    # silicon or flash capacity. The actual image is RCDATA/4000; the archive's
    # SN32F290.hex is a separate template, not this keyboard's flash image.
    return {'chip': 'unknown', 'chip_id_hex': identity.hex(' '), 'flash_bytes': None,
            'generic_table_profile': name, 'generic_table_flash_bytes': size,
            'hardware_identity_verified': False, 'raw_response_hex': response.hex(' '),
            'code_options_hex': response[12:16].hex(' '), 'security_hex': response[14:16].hex(' ')}


def prepare(device, firmware_id=FIRMWARE_ID):
    if firmware_id != FIRMWARE_ID:
        raise ConfigError('未知随包固件 ID；不接受用户上传的文件或路径')
    image, metadata = load_official()
    hardware = select_hardware(device)
    path, connection = select_transport(hardware)
    if connection != 'usb':
        raise FirmwareError('wired_connection_required',
                            f"硬件「{hardware['name']}」必须通过 USB 有线连接升级，不能刷写 2.4G 接收器")
    physical = next(e['physical'] for e in hardware['endpoints'] if e['path'] == path)
    return hardware, path, physical, image, metadata


def preview(device, firmware_id=FIRMWARE_ID):
    hardware, _, _, _, metadata = prepare(device, firmware_id)
    return {'device': public_hardware(hardware), 'firmware': metadata, 'status': 'prepared',
            'device_changed': False, 'chip_identity_verified': False,
            'stages': ['enter_isp', 'wait_bootloader', 'check_chip',
                       'restore_code_options', 'enable_program',
                       'write_flash', 'verify_checksum', 'reset', 'wait_keyboard'],
            'warnings': ['更新会替换整个固件及配置区域，请保持有线连接和供电。',
                         '固件和流程已与官方 Windows 成功刷写记录核对；Linux 完整升级尚未实机验证。']}


def _physical(device):
    resolved = device.resolve()
    return next((str(p) for p in (resolved, *resolved.parents)
                 if (p / 'idVendor').exists() and (p / 'idProduct').exists()), None)


def boot_endpoints(physical, sysfs=Path('/sys/class/hidraw')):
    """Private inventory: only the selected keyboard's USB port, never all ISP devices."""
    found = []
    for entry in sysfs.glob('hidraw*'):
        device = entry / 'device'
        try:
            if _physical(device) != physical:
                continue
            props = dict(line.split('=', 1) for line in (device / 'uevent').read_text().splitlines() if '=' in line)
            bus, vid, pid = (int(v, 16) for v in props['HID_ID'].split(':'))
            caps = parse_descriptor((device / 'report_descriptor').read_bytes())
            identity_ok = ((vid, pid) in BOOT_IDS or
                           supported_hardware(bus, vid, pid, props.get('HID_NAME', '')))
            if bus == 3 and identity_ok and not caps.numbered and caps.size('feature') == 64:
                found.append('/dev/' + entry.name)
        except (OSError, ValueError, KeyError):
            continue
    return found


class BootHid:
    def __init__(self, path, physical):
        self.path, self.physical, self.fd = path, physical, None

    def __enter__(self):
        try:
            self.fd = os.open(self.path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
            if not stat.S_ISCHR(os.fstat(self.fd).st_mode):
                raise FirmwareError('boot_identity_mismatch', 'ISP 节点不是字符设备')
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            info = bytearray(8)
            fcntl.ioctl(self.fd, ioc(2, 3, 8), info, True)
            bus, vid, pid = struct.unpack('IHH', info)
            name = bytearray(256)
            fcntl.ioctl(self.fd, ioc(2, 4, 256), name, True)
            hid_name = name.split(b'\0', 1)[0].decode('utf-8', errors='replace')
            identity_ok = ((vid, pid) in BOOT_IDS or supported_hardware(bus, vid, pid, hid_name))
            if bus != 3 or not identity_ok or _physical(
                    Path('/sys/class/hidraw') / Path(self.path).name / 'device') != self.physical:
                raise FirmwareError('boot_identity_mismatch', 'ISP 设备身份或 USB 物理端口发生变化')
            size = bytearray(4)
            fcntl.ioctl(self.fd, ioc(2, 1, 4), size, True)
            length = struct.unpack('I', size)[0]
            if not 1 <= length <= 4096:
                raise FirmwareError('boot_identity_mismatch', 'ISP HID 描述符长度异常')
            desc = bytearray(struct.pack('I', length) + bytes(4096))
            fcntl.ioctl(self.fd, ioc(2, 2, 4100), desc, True)
            caps = parse_descriptor(desc[4:4 + length])
            if caps.numbered or caps.size('feature') != 64:
                raise FirmwareError('boot_identity_mismatch', 'ISP 必须提供无 Report ID 的 64 字节 Feature')
            return self
        except BaseException:
            self.__exit__()
            raise

    def __exit__(self, *_):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def send(self, payload):
        if len(payload) != 64:
            raise ValueError('ISP 业务包必须为 64 字节')
        report = bytearray(b'\0' + payload)
        count = fcntl.ioctl(self.fd, ioc(3, 6, 65), report, True)
        if count != 65:
            raise FirmwareError('transfer_failed', 'ISP Feature 写入不完整')

    def receive(self):
        report = bytearray(65)
        count = fcntl.ioctl(self.fd, ioc(3, 7, 65), report, True)
        if count == 64:
            return bytes(report[:64])
        if count == 65 and report[0] == 0:
            return bytes(report[1:])
        raise FirmwareError('transfer_failed', 'ISP Feature 响应长度或 Report ID 异常')


def program(transport, image, progress, sleep=None, *, initial_info=None):
    """Replay the official legacy flow: 01, 03, 05, 1024 blocks, checksum, 07.

    No standalone erase (02/04), retries, or reset after a persistent write fails.
    The only accepted image is the bundled, Windows-capture-verified RCDATA.
    """
    if sleep is None:
        sleep = time.sleep
    if len(image) != FLASH_SIZE or hashlib.sha256(image).hexdigest() != IMAGE_SHA256:
        raise FirmwareError('firmware_invalid', '固件必须是随包官方 RCDATA/4000 的完整 64 KiB 镜像')
    progress('check_chip', 0, False)
    if initial_info is None:
        transport.send(command(1))
        sleep(0.03)
        initial_info = transport.receive()
    detected = chip_info(initial_info)
    if initial_info[8:12] != EXPECTED_ISP_ID:
        raise FirmwareError('unsupported_isp_profile',
                            f"随包官方固件要求 ISP 20 03 00 00，当前返回 {detected['chip_id_hex']}；未执行擦除或固件写入")
    options = initial_info[12:16]
    # Resource 4012 and the successful official capture both select CS1 (5A5A).
    # Preserve all four bytes, never silently elevate/downgrade protection.
    if options[2:] != b'\x5a\x5a':
        raise FirmwareError('unsupported_security', '当前仅验证 5A 5A 保护状态；未修改 Code Option 或固件')
    progress('restore_code_options', 0, True)
    transport.send(command(3, options))
    sleep(0.02)
    progress('enable_program', 0, True)
    transport.send(command(5, struct.pack('<HHH', 0, 0, FLASH_SIZE // 64)))
    sleep(0.05)
    ack = transport.receive()
    if len(ack) != 64 or ack[:4] != command(5)[:4] or ack[4:8] != b'\xfa' * 4:
        raise FirmwareError('program_enable_failed', 'ISP 拒绝开始写入；已停止，未发送固件数据')
    sleep(0.02)
    for offset in range(0, FLASH_SIZE, 64):
        transport.send(image[offset:offset + 64])
        sleep(0.01)
        if offset % 4096 == 0 or offset + 64 == FLASH_SIZE:
            progress('write_flash', offset + 64, True)
    sleep(0.02)
    progress('verify_checksum', FLASH_SIZE, True)
    response = transport.receive()
    if (len(response) != 64 or response[:4] != command(5)[:4]
            or response[4:8] != b'\xfa' * 4
            or int.from_bytes(response[8:10], 'little') != checksum16(image)):
        raise FirmwareError('verify_failed', '固件校验响应或校验和不匹配；已停止，未复位')
    progress('reset', FLASH_SIZE, True)
    transport.send(command(7))


def wait_bootloader(physical, timeout=15, sleep=time.sleep, observe=None):
    deadline = time.monotonic() + timeout
    permission_pending = False
    last_error = ''
    while time.monotonic() < deadline:
        endpoints = boot_endpoints(physical)
        if len(endpoints) > 1:
            raise FirmwareError('ambiguous_bootloader', '所选端口存在多个 ISP 配置通道')
        permission_pending = bool(endpoints) and not os.access(endpoints[0], os.R_OK | os.W_OK)
        if endpoints and not permission_pending:
            try:
                # Original 0x406BB0 -> 0x40A760 queries the task's VID/PID;
                # ROM ISP can retain the application's USB identity and node.
                with BootHid(endpoints[0], physical) as boot:
                    boot.send(command(1))
                    sleep(0.02)
                    response = boot.receive()
                if observe:
                    observe({'node': endpoints[0], 'query_response': response.hex(' ')})
                detected = chip_info(response)
                if observe:
                    observe({'node': endpoints[0], 'chip_info': detected})
                return endpoints[0]
            except (OSError, FirmwareError) as e:
                last_error = str(e)
                if observe:
                    observe({'node': endpoints[0], 'query_error': last_error})
        sleep(0.2)
    if permission_pending:
        raise PermissionError('已发现所选键盘的 ISP 节点，但没有读写权限；请安装新版 udev 规则')
    raise FirmwareError('bootloader_timeout', '原 USB 端口未通过 ISP 协议握手，未执行擦除或固件写入' +
                        (f'；最后结果：{last_error}' if last_error else '；未发现可查询的配置通道'))


_jobs, _jobs_lock = {}, threading.Lock()


def _hid_generation(path):
    return str((Path('/sys/class/hidraw') / Path(path).name / 'device').resolve())


def _wait_keyboard(hardware_id, physical, previous_generation, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            current = select_hardware(hardware_id)
            if any(e['physical'] == physical and e['usb_supported'] and
                   _hid_generation(e['path']) != previous_generation for e in current['endpoints']):
                return True
        except (ConfigError, OSError):
            pass
        time.sleep(0.2)
    return False


def job_status(job_id):
    with _jobs_lock:
        if not isinstance(job_id, str) or job_id not in _jobs:
            raise ConfigError('未知固件任务 ID；任务状态仅保存在当前后端进程')
        return copy.deepcopy(_jobs[job_id])


def start_update(device, firmware_id=FIRMWARE_ID, *, diagnostic_only=False):
    hardware, path, physical, image, metadata = prepare(device, firmware_id)
    if not OPERATION_LOCK.acquire(blocking=False):
        raise FirmwareError('device_busy', '后端正在执行设备操作')
    job_id = uuid.uuid4().hex
    with _jobs_lock:
        # Retain recent outcomes without accumulating arbitrary request history.
        if len(_jobs) >= 20:
            del _jobs[next(iter(_jobs))]
        _jobs[job_id] = {'job_id': job_id, 'device': public_hardware(hardware),
                         'firmware': metadata, 'status': 'running', 'stage': 'queued',
                         'diagnostic_only': diagnostic_only, 'isp_handshake_verified': False,
                         'diagnostics': [], 'detected_chip': None,
                         'written_bytes': 0, 'total_bytes': FLASH_SIZE,
                         'may_have_partial_changes': False, 'device_changed': False,
                         'checksum_verified': False, 'reset_sent': False,
                         'normal_device_returned': False}
        accepted = copy.deepcopy(_jobs[job_id])

    def progress(stage, written, partial):
        with _jobs_lock:
            _jobs[job_id].update(stage=stage, written_bytes=written,
                                 may_have_partial_changes=partial)

    def worker():
        try:
            progress('enter_isp', 0, False)
            # Revalidate and lock the selected normal-mode endpoint before the
            # official 8-byte transition command. Do not use receiver protocol.
            with Hidraw(path, 'usb') as normal:
                if _physical(Path('/sys/class/hidraw') / Path(path).name / 'device') != physical:
                    raise FirmwareError('device_identity_changed', '配置节点的 USB 物理端口已变化，请重新扫描')
                with _jobs_lock:
                    _jobs[job_id]['device_changed'] = True
                normal.send_feature(b'\0' + ENTER_ISP.ljust(64, b'\0'))
            progress('wait_bootloader', 0, False)
            handshake_response = None
            def observe(entry):
                nonlocal handshake_response
                with _jobs_lock:
                    log = _jobs[job_id]['diagnostics']
                    if len(log) < 80:
                        log.append(entry)
                    if 'chip_info' in entry:
                        _jobs[job_id]['detected_chip'] = entry['chip_info']
                        handshake_response = bytes.fromhex(entry['chip_info']['raw_response_hex'])
            boot_path = wait_bootloader(physical, observe=observe)
            with _jobs_lock:
                _jobs[job_id]['isp_handshake_verified'] = True
            with BootHid(boot_path, physical) as boot:
                generation = _hid_generation(boot_path)
                try:
                    if diagnostic_only:
                        progress('exit_isp', 0, False)
                        boot.send(command(7))
                    else:
                        program(boot, image, progress, initial_info=handshake_response)
                except Exception:
                    with _jobs_lock:
                        partial = _jobs[job_id]['may_have_partial_changes']
                    if not partial:
                        # A confirmed ISP session rejected before erasing may
                        # safely return to the unchanged application firmware.
                        try:
                            boot.send(command(7))
                            returned = _wait_keyboard(hardware['id'], physical, generation)
                            with _jobs_lock:
                                _jobs[job_id].update(reset_sent=True, normal_device_returned=returned)
                        except (OSError, FirmwareError) as recovery:
                            observe({'exit_isp_error': str(recovery)})
                    raise
            with _jobs_lock:
                _jobs[job_id].update(checksum_verified=not diagnostic_only, reset_sent=True)
            progress('wait_keyboard', 0 if diagnostic_only else FLASH_SIZE, not diagnostic_only)
            returned = _wait_keyboard(hardware['id'], physical, generation)
            if not returned:
                message = ('已发送退出 ISP，但未确认键盘重新枚举' if diagnostic_only else
                           '校验和已通过并发送复位，但未确认键盘重新枚举')
                raise FirmwareError('keyboard_return_timeout', message)
            with _jobs_lock:
                _jobs[job_id].update(status='completed', stage='completed',
                                     normal_device_returned=True, may_have_partial_changes=False)
        except Exception as e:
            with _jobs_lock:
                _jobs[job_id].update(status='failed', error={
                    'code': e.code if isinstance(e, FirmwareError) else
                            'permission_denied' if isinstance(e, PermissionError) else 'device_io_error',
                    'message': f"硬件「{hardware['name']}」：{e}"})
        finally:
            OPERATION_LOCK.release()

    thread = threading.Thread(target=worker, name='ganss-firmware-update', daemon=False)
    try:
        thread.start()
    except BaseException:
        OPERATION_LOCK.release()
        with _jobs_lock:
            del _jobs[job_id]
        raise
    return accepted
