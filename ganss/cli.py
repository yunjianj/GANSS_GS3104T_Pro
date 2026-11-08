"""Command line interface. All configuration commands also work offline via --dry-run."""
import argparse
from contextlib import ExitStack
import json
import sys

from . import __version__
from .linux import Hidraw, public_hardware, scan_hardware, select_hardware, select_transport
from .protocol import BY_INDEX, ConfigError, keymap_plan, keymap_single_plan, light_plan
from .runner import ProtocolError, execute


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError(f'JSON 字段重复: {key}')
        result[key] = value
    return result


def load_profile(path):
    if path == '-':
        return json.load(sys.stdin, object_pairs_hook=unique_object)
    with open(path, encoding='utf-8') as f:
        return json.load(f, object_pairs_hook=unique_object)


def positive_byte(value):
    try:
        number = int(value, 0)
    except ValueError as e:
        raise argparse.ArgumentTypeError('请输入十进制或 0x 前缀的整数') from e
    if not 0 <= number <= 255:
        raise argparse.ArgumentTypeError('必须在 0..255 范围内')
    return number


def build_parser():
    parser = argparse.ArgumentParser(prog='ganss', description='GANSS GS3104T PRO Linux 键盘配置工具')
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest='command')
    devices = commands.add_parser('scan', aliases=['devices'], help='扫描对应型号的键盘，按物理硬件显示')
    devices.add_argument('--json', action='store_true')
    keys = commands.add_parser('keys', help='显示物理键名称和索引')
    keys.add_argument('--json', action='store_true')
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--device', required=True, metavar='硬件名', help='scan 中的完整硬件名或 ID；程序自动识别配置通道')
    common.add_argument('--dry-run', action='store_true', help='扫描硬件并输出 JSON 报文计划，不打开设备或发送命令')
    common.add_argument('--trace', metavar='FILE', help='将实际发送和接收的报文记录为 JSONL')
    light = commands.add_parser('light', parents=[common], help='设置灯光模式、RGB、亮度与速度')
    light.add_argument('--mode', type=positive_byte, default=11, help='原厂灯效编号；0 关闭，默认 11')
    light.add_argument('--brightness', type=int, default=5, metavar='0..5')
    light.add_argument('--speed', type=int, default=3, metavar='0..5')
    light.add_argument('--color', default='FFFFFF', metavar='RRGGBB')
    light.add_argument('--colorful', action='store_true', help='启用原厂多彩标志')
    keymap = commands.add_parser('keymap', help='创建或应用整层按键配置')
    actions = keymap.add_subparsers(dest='action', required=True)
    init = actions.add_parser('init', help='输出默认映射 JSON；重定向保存后编辑')
    init.add_argument('--layer', choices=('normal', 'fn'), default='normal')
    apply = actions.add_parser('apply', parents=[common],
                              help='应用完整层配置；未列出的按键恢复默认映射')
    apply.add_argument('file', help='JSON 配置文件，或 - 从标准输入读取')
    single = actions.add_parser('set', parents=[common], help='2.4G 单键更新，保留其他键位')
    single.add_argument('source', help='物理键名称或 index:编号')
    single.add_argument('target', help='目标键或组合键；default 恢复普通层该键原始键码')
    single.add_argument('--layer', choices=('normal', 'fn'), default='normal')
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command is None:
            parser.print_help()
            return 0
        if args.command in ('scan', 'devices'):
            hardware = scan_hardware()
            if args.json:
                print(json.dumps([public_hardware(h) for h in hardware], ensure_ascii=False, indent=2))
            elif not hardware:
                print('未发现 GANSS GS3104T PRO 键盘或接收器，请检查连接。')
            else:
                for h in hardware:
                    status = {'ready': '已发现配置通道', 'permission_denied': '设备权限不足',
                              'node_missing': '当前环境没有设备节点', 'unsupported': '配置通道不可用'}.get(
                                  h.get('access_status'), '已发现配置通道' if h['available'] else '配置通道不可用')
                    print(f"{h['name']}  ID={h['id']}  {status}")
            return 0
        if args.command == 'keys':
            keys = [{'index': i, 'name': k['name'], 'usage': k['code']} for i, k in sorted(BY_INDEX.items())]
            if args.json:
                print(json.dumps(keys, ensure_ascii=False, indent=2))
            else:
                for k in keys:
                    print(f"index:{k['index']:<3} {k['name']:<14} {k['usage']}")
            return 0
        if args.command == 'keymap' and args.action == 'init':
            print(json.dumps({'version': 1, 'layer': args.layer, 'mappings': {}}, ensure_ascii=False, indent=2))
            return 0
        profile = load_profile(args.file) if args.command == 'keymap' and args.action == 'apply' else None
        hardware = select_hardware(args.device)
        args.device = hardware['name']
        path, connection = select_transport(hardware)
        if args.command == 'light':
            plan = light_plan(args.mode, args.brightness, args.speed, args.color, args.colorful, connection)
        elif args.action == 'set':
            plan = keymap_single_plan(args.source, args.target, args.layer, connection)
        else:
            plan = keymap_plan(profile, connection)
        if args.dry_run:
            print(json.dumps({'device': public_hardware(hardware), 'connection': connection, 'operation': args.command,
                              'replaces_entire_layer': args.command == 'keymap' and args.action == 'apply',
                              'steps': [s.as_dict() for s in plan]}, ensure_ascii=False, indent=2))
            return 0
        if args.command == 'light':
            print(f"硬件「{hardware['name']}」：正在设置灯光。", file=sys.stderr)
        if args.command == 'keymap':
            print(f"硬件「{hardware['name']}」：若映射未生效，请检查 Fn+Tab 是否屏蔽了驱动自定义按键指令。", file=sys.stderr)
            if args.action == 'apply':
                print(f"硬件「{hardware['name']}」：正在替换所选层的完整映射；未列出的按键恢复默认。", file=sys.stderr)
            else:
                print(f"硬件「{hardware['name']}」：更新 {args.source} → {args.target}。", file=sys.stderr)
        with ExitStack() as stack:
            trace = None
            if args.trace:
                stream = stack.enter_context(open(args.trace, 'a', encoding='utf-8'))

                def trace(entry):
                    entry = {'device': hardware['name'], 'device_id': hardware['id'], **entry}
                    stream.write(json.dumps(entry, ensure_ascii=False) + '\n')
                    stream.flush()
            transport = stack.enter_context(Hidraw(path, connection, trace))
            count = execute(plan, transport)
        print(f"硬件「{hardware['name']}」：已发送 {count} 个配置报文，所需响应检查通过。设备效果及断电保存请在键盘上确认。")
        return 0
    except PermissionError as e:
        print(f'硬件「{args.device}」权限不足: {e}。请按 README 安装对应 udev 规则。', file=sys.stderr)
        return 1
    except (ConfigError, ProtocolError, OSError, json.JSONDecodeError) as e:
        target = f'硬件「{args.device}」' if hasattr(args, 'device') else ''
        print(f'{target}错误: {e}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(f'硬件「{args.device}」操作已中断；若已开始发送，设备可能处于部分更新状态。', file=sys.stderr)
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
