"""Pure packet encoding, traced to the bundled Windows driver disassembly."""
from dataclasses import dataclass
from importlib.resources import files
import json
import re


class ConfigError(ValueError):
    pass


def integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ConfigError(f"{name} 必须为 {low}..{high} 的整数")
    return value


KEYS = json.loads(files('ganss').joinpath('data/keymap.json').read_text(encoding='utf-8'))
BY_INDEX = {int(k['key_index']): k for k in KEYS}
ALIASES = {'ESCAPE': 1, 'ENTER': 85, 'KP_ENTER': 106, 'SPACE': 94,
           'CTRL': 91, 'LCTRL': 91, 'RCTRL': 98, 'SHIFT': 73,
           'LSHIFT': 73, 'RSHIFT': 84, 'ALT': 93, 'LALT': 93, 'RALT': 95,
           'WIN': 92, 'SUPER': 92, 'LEFT': 99, 'DOWN': 100, 'UP': 101, 'RIGHT': 102}


def resolve_key(name):
    if not isinstance(name, str):
        raise ConfigError('按键名称必须为字符串')
    if name.lower().startswith('index:'):
        try:
            key = BY_INDEX.get(int(name[6:]))
        except ValueError:
            key = None
        if key:
            return key
    if name.upper() in ALIASES:
        return BY_INDEX[ALIASES[name.upper()]]
    candidates = [k for k in KEYS if k['name'].casefold() == name.casefold()]
    if len(candidates) == 1:
        return candidates[0]
    raise ConfigError(f'未知或重名的按键 {name!r}，请用 keys 查看名称，或使用 index:编号')


def key_record(target):
    """0x4187C5: type=2, modifier mask, usage, reserved; 0x451B40 mask."""
    if target == 'default':
        return bytes(4)
    if not isinstance(target, str):
        raise ConfigError('映射目标必须为字符串，例如 B、CTRL+C 或 default')
    mask, usages = 0, []
    for part in target.split('+'):
        if part.lower().startswith('usage:'):
            try:
                usage = int(part[6:], 0)
            except ValueError as e:
                raise ConfigError(f'无效 usage: {part}') from e
        else:
            usage = int(resolve_key(part)['code'], 16)
        if 0xE0 <= usage <= 0xE7:
            mask |= 1 << (usage - 0xE0)
        elif 4 <= usage <= 0x77:
            usages.append(usage)
        else:
            raise ConfigError(f'{part} 不是本版本支持的标准键盘 usage，暂不支持 Fn/媒体/专用动作作为目标')
    if len(usages) > 1:
        raise ConfigError('一个映射最多包含一个普通键，可组合多个修饰键')
    return bytes((2, mask, usages[0] if usages else 0, 0))


@dataclass(frozen=True)
class Step:
    label: str
    channel: str
    data: bytes
    ack: bool = False
    delay_ms: int = 35

    def as_dict(self):
        return {'label': self.label, 'channel': self.channel, 'hex': self.data.hex(' '),
                'length': len(self.data), 'ack': self.ack, 'delay_ms': self.delay_ms}


def feature(payload):
    if len(payload) != 64:
        raise ConfigError('Feature 业务块必须为 64 字节')
    return b'\0' + bytes(payload)


def command(code, argument=0):
    p = bytearray(64)
    p[0:2] = bytes((4, code))
    p[8] = argument
    return feature(p)


def short_packet(payload):
    if len(payload) != 33 or payload[0] != 0 or payload[32] != 0:
        raise ConfigError('短报文必须为 33 字节，首字节和初始校验字节为零')
    p = bytearray(payload)
    p[32] = sum(p) & 255  # The original 65-byte source has a zero-filled tail.
    return bytes(p)


def color_rgb(value):
    if not isinstance(value, str) or not re.fullmatch(r'#?[0-9a-fA-F]{6}', value):
        raise ConfigError('颜色必须为 RRGGBB，例如 FF8000')
    return bytes.fromhex(value.lstrip('#'))


def light_plan(mode=11, brightness=5, speed=3, color='FFFFFF', colorful=False, connection='usb'):
    integer(mode, 0, 255, 'mode')
    integer(brightness, 0, 5, 'brightness')
    integer(speed, 0, 5, 'speed')
    if type(colorful) is not bool:
        raise ConfigError('colorful 必须为布尔值')
    rgb = color_rgb(color)
    if connection == 'usb':
        # 0x408520 loads the struct at ebp-0x88. 0x42B144..162 writes
        # colorful, brightness, speed, direction (not a second mode byte).
        p = bytearray(64)
        p[0] = mode
        if mode:
            p[1:4] = rgb
            p[8:12] = bytes((int(colorful), brightness, speed, 0))
        p[14:16] = b'\xaa\x55'
        return [Step('准备灯光事务', 'feature', command(0x18), True),
                Step('选择灯光参数块', 'feature', command(0x13, 1), True),
                Step('写入灯光参数', 'feature', feature(p)),
                Step('结束灯光事务', 'feature', command(2), True),
                Step('刷新灯光', 'feature', command(0xF0))]
    if connection == 'wireless':
        # 0x42B288..0x42B2C7.
        p = bytearray(33)
        p[1:4] = bytes((5, 16, 0))
        p[4] = mode
        if mode:
            p[5:8] = rgb
            p[12:16] = bytes((int(colorful), brightness, speed, 0))
        p[18:20] = b'\xaa\x55'
        return [Step('写入无线灯光参数', 'short', short_packet(p), True, 5)]
    raise ConfigError('connection 必须为 usb 或 wireless')


def parse_profile(value):
    if not isinstance(value, dict) or set(value) - {'version', 'layer', 'mappings'}:
        raise ConfigError('配置只能包含 version、layer、mappings')
    if type(value.get('version')) is not int or value['version'] != 1:
        raise ConfigError('配置 version 必须为 1')
    if value.get('layer') not in ('normal', 'fn'):
        raise ConfigError('layer 必须为 normal 或 fn')
    mappings = value.get('mappings')
    if not isinstance(mappings, dict):
        raise ConfigError('mappings 必须为对象')
    seen, records = set(), {}
    for source, target in mappings.items():
        key = resolve_key(source)
        index = int(key['key_index'])
        if index in seen:
            raise ConfigError(f'同一物理键重复出现: {source}')
        seen.add(index)
        if key['name'] == 'Fn' and target != 'default':
            raise ConfigError('本版本保留 Fn 键的默认功能')
        records[index] = key_record(target)
    return value['layer'], records


def keymap_plan(profile, connection='usb'):
    layer, records = parse_profile(profile)
    table = bytearray(576)
    for index, record in records.items():
        table[index * 4:index * 4 + 4] = record
    table[-2:] = b'\xaa\x55'  # 0x418CC4, no index subtraction in 0x4187C5.
    if connection == 'usb':
        return ([Step('准备键位事务', 'feature', command(0x18), True),
                 Step(f'选择 {layer} 层', 'feature', command(0x11 if layer == 'normal' else 0x27, 9), True)]
                + [Step(f'键位块 {i // 64 + 1}/9', 'feature', feature(table[i:i + 64]))
                   for i in range(0, 576, 64)]
                + [Step('结束键位事务', 'feature', command(2), True),
                   Step('刷新键位', 'feature', command(0xF0), True)])
    if connection == 'wireless':
        # 0x4190A6: 21 blocks; the last has 16 valid bytes, 28 copied.
        data = bytes(table) + bytes(12)
        result = []
        for block in range(21):
            p = bytearray(33)
            p[1:4] = bytes((0x10 if layer == 'normal' else 0x12,
                            16 if block == 20 else 28, block))
            p[4:32] = data[block * 28:block * 28 + 28]
            result.append(Step(f'{layer} 键位块 {block + 1}/21', 'short', short_packet(p), True, 5))
        return result
    raise ConfigError('connection 必须为 usb 或 wireless')


def keymap_single_plan(source, target, layer='normal', connection='wireless'):
    """0x41920F..0x4192BB: modify one record without replacing the layer."""
    if connection != 'wireless':
        raise ConfigError('单键更新目前仅支持 2.4G；有线请使用完整层配置')
    if layer not in ('normal', 'fn'):
        raise ConfigError('layer 必须为 normal 或 fn')
    key = resolve_key(source)
    if key['name'] == 'Fn':
        raise ConfigError('本版本保留 Fn 键的默认功能')
    if target == 'default':
        if layer == 'fn':
            raise ConfigError('Fn 层默认动作未完整恢复，请显式指定映射目标')
        target = 'usage:' + key['code']
    record = key_record(target)
    p = bytearray(33)
    p[1:4] = bytes((3 if layer == 'normal' else 0x0D, 4, int(key['key_index'])))
    p[4:8] = record
    return [Step(f'更新 {layer} 层单键 {key["name"]}', 'short', short_packet(p), True, 5)]
