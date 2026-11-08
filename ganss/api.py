"""Versioned frontend API: Python dispatch() or UTF-8 JSON Lines over stdio."""
import json
import sys

from .linux import Hidraw, public_hardware, scan_hardware, select_hardware, select_transport
from .protocol import BY_INDEX, ConfigError, keymap_plan, keymap_single_plan, light_plan
from .runner import ProtocolError, execute
from .firmware import (FIRMWARE_ID, FirmwareError, device_operation, job_status,
                       load_official, preview, start_update)


def _fields(value, allowed, required=()):
    if not isinstance(value, dict):
        raise ConfigError('参数必须为 JSON 对象')
    if set(value) - set(allowed):
        raise ConfigError('未知字段: ' + ', '.join(sorted(set(value) - set(allowed))))
    if set(required) - set(value):
        raise ConfigError('缺少字段: ' + ', '.join(sorted(set(required) - set(value))))


def _handle(request):
    _fields(request, ('id', 'api_version', 'method', 'params'), ('api_version', 'method'))
    if type(request['api_version']) is not int or request['api_version'] != 1:
        raise ConfigError('api_version 必须为 1')
    method, params = request['method'], request.get('params', {})
    if not isinstance(method, str):
        raise ConfigError('method 必须为字符串')
    if method == 'firmware.list':
        _fields(params, ())
        return {'firmwares': [load_official()[1]]}
    if method == 'firmware.status':
        _fields(params, ('job_id',), ('job_id',))
        return job_status(params['job_id'])
    if method in ('firmware.prepare', 'firmware.update', 'firmware.probe_isp'):
        _fields(params, ('device', 'firmware_id'), ('device',))
        if not isinstance(params['device'], str) or not params['device']:
            raise ConfigError('device 必须为扫描得到的硬件名或 ID')
        firmware_id = params.get('firmware_id', FIRMWARE_ID)
        if method == 'firmware.prepare':
            return preview(params['device'], firmware_id)
        return start_update(params['device'], firmware_id,
                            diagnostic_only=method == 'firmware.probe_isp')
    if method in ('devices.scan', 'keys.list'):
        _fields(params, ())
        if method == 'devices.scan':
            return {'devices': [public_hardware(h) for h in scan_hardware()]}
        return {'keys': [{'index': i, 'name': k['name'], 'usage': k['code']}
                         for i, k in sorted(BY_INDEX.items())]}
    options = {
        'lighting.set': ('mode', 'brightness', 'speed', 'color', 'colorful'),
        'keymap.set': ('source', 'target', 'layer'),
        'keymap.apply': ('profile',),
    }
    if method not in options:
        raise ConfigError(f'未知方法: {method}')
    required = {'keymap.set': ('source', 'target'), 'keymap.apply': ('profile',)}.get(method, ())
    _fields(params, ('device', 'dry_run') + options[method], ('device',) + required)
    if not isinstance(params['device'], str) or not params['device']:
        raise ConfigError('device 必须为扫描得到的硬件名或 ID')
    if type(params.get('dry_run', False)) is not bool:
        raise ConfigError('dry_run 必须为布尔值')
    if 'colorful' in params and type(params['colorful']) is not bool:
        raise ConfigError('colorful 必须为布尔值')
    hardware = select_hardware(params['device'])
    path, connection = select_transport(hardware)
    args = {k: params[k] for k in options[method] if k in params}
    if method == 'lighting.set':
        plan = light_plan(connection=connection, **args)
    elif method == 'keymap.set':
        plan = keymap_single_plan(connection=connection, **args)
    else:
        plan = keymap_plan(params['profile'], connection)
    result = {'device': public_hardware(hardware), 'connection': connection,
              'dry_run': params.get('dry_run', False),
              'replaces_entire_layer': method == 'keymap.apply',
              'warnings': ['若映射未生效，请检查 Fn+Tab 是否屏蔽驱动自定义按键指令。']
                          if method.startswith('keymap.') else []}
    if result['dry_run']:
        result.update(steps=[step.as_dict() for step in plan], status='planned')
    else:
        trace = []
        with device_operation():
            with Hidraw(path, connection, trace.append) as transport:
                completed = execute(plan, transport)
        result.update(status='transferred', completed_steps=completed, trace=trace,
                      effect_verified=False, persistence_verified=False)
    return result


def dispatch(request):
    """Return a JSON-serializable envelope; expected device errors never raise.

    Synchronous and blocking. Call from a backend worker, not a UI thread.
    A successful write confirms transport only, not the physical key effect.
    """
    request_id = request.get('id') if isinstance(request, dict) else None
    if request_id is not None and type(request_id) not in (str, int):
        return _error(None, 'invalid_request', 'id 必须为字符串、整数或 null')
    try:
        return {'api_version': 1, 'id': request_id, 'ok': True, 'result': _handle(request)}
    except FirmwareError as e:
        return _error(request_id, e.code, str(e))
    except ConfigError as e:
        return _error(request_id, 'invalid_request', str(e))
    except PermissionError as e:
        return _error(request_id, 'permission_denied', str(e))
    except ProtocolError as e:
        return _error(request_id, 'transfer_failed', str(e), partial=True)
    except OSError as e:
        return _error(request_id, 'device_io_error', str(e))


def _error(request_id, code, message, partial=False):
    return {'api_version': 1, 'id': request_id, 'ok': False,
            'error': {'code': code, 'message': message, 'may_have_partial_changes': partial}}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError(f'JSON 字段重复: {key}')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ConfigError(f'非标准 JSON 常量: {value}')


def main():
    # One request per line, one response per line. stdout is protocol only.
    for line in sys.stdin:
        try:
            request = json.loads(line, object_pairs_hook=_unique,
                                 parse_constant=_invalid_constant)
            response = dispatch(request)
        except (ValueError, RecursionError) as e:
            response = _error(None, 'invalid_json', str(e))
        print(json.dumps(response, ensure_ascii=False, allow_nan=False), flush=True)


if __name__ == '__main__':
    main()
