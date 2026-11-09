# 前端调用接口 v1

推荐桌面前端启动 `python -m ganss.api`，通过标准输入、输出交换 UTF-8 JSON Lines；每行一个完整 JSON 请求，对应一行响应。进程按顺序处理请求，输出后立即 flush，关闭 stdin 后退出。stdout 专用于 JSON；stderr 用于进程诊断。也可在 Python 后端直接调用 `from ganss.api import dispatch`，传入同样的字典并接收响应字典。

前端无需知道 hidraw 路径或接口号，先扫描，再传扫描结果中的完整硬件名或 ID。API 与 CLI 使用相同的型号过滤、通道选择和设备锁。进程使用启动用户的设备权限，推荐按 README 配置 udev。

## 请求与方法

固件更新使用独立的异步任务接口 `firmware.list`、`firmware.prepare`、`firmware.update`、`firmware.status`，从随包官方 EXE 的 PE 资源 RCDATA/4000 提取完整 64 KiB 固件，无上传参数。另有 `firmware.probe_isp`，只进入、查询并退出 ISP，不擦写固件；同样通过 status 轮询，诊断完成不能视为更新成功。参数、任务进度、权限与未实机验证范围见 [固件更新后端](firmware-update.md)。

请求顶层字段：`api_version` 必须为整数 1，`id` 为可选的字符串、整数或 null，`method` 为下表方法，`params` 为对象。未知字段会被拒绝。响应原样返回 `id`。所有写入方法的 `params.device` 必填，`params.dry_run` 可选、默认 false；为 true 时返回报文计划，不打开设备。

| method | params（除 device、dry_run 外） | 含义 |
| --- | --- | --- |
| `devices.scan` | 无，且不接受 device、dry_run | 返回 `result.devices`，只列对应型号硬件 |
| `keys.list` | 无，且不接受 device、dry_run | 返回 `result.keys`，每项含 index、name、usage |
| `lighting.set` | mode=11、color="FFFFFF"、brightness=5、speed=3、colorful=false | 写入完整灯光参数；未传字段使用默认值 |
| `keymap.set` | source、target 必填；layer="normal" | 2.4G 单键更新；其余键保留 |
| `keymap.apply` | profile 必填 | 替换整层，未列出的键恢复默认 |

扫描项含 `id`、`name`、`serial`、`connections`、`available`、`access_status`。`connections` 为 usb/wireless 列表；`access_status` 为 ready/permission_denied/node_missing/unsupported。available 表示配置节点可访问，不表示无线键盘已开机。ID 随 USB 拓扑生成，换插口应重新扫描。

灯光 mode 为 0..255 整数，0 表示关闭；brightness、speed 为 0..5 整数；color 为 RRGGBB 或 #RRGGBB。模式编号不保证都有固件支持。映射 source、target 的键名、组合键格式及限制见 README。`keymap.set` 的 target="default" 在普通层恢复标准键码；Fn 层需要显式目标。Fn+Tab 需要处于启用驱动自定义按键的状态，目前无法读取该开关。

0.3.4 修正了亮度与速度的协议字段错位。前端仍使用原有 `brightness` 与 `speed` 参数，无需改动调用格式；升级后需要重新应用灯光设置。

## 调用示例

以下每个 JSON 对象均作为一行写入子进程 stdin：

```json
{"api_version":1,"id":"scan-1","method":"devices.scan"}
{"api_version":1,"id":"light-preview","method":"lighting.set","params":{"device":"GS3104T-PRO 2.4G","color":"FF8000","brightness":4,"dry_run":true}}
{"api_version":1,"id":"light-1","method":"lighting.set","params":{"device":"GS3104T-PRO 2.4G","mode":11,"color":"FF8000","brightness":4,"speed":3}}
{"api_version":1,"id":"map-1","method":"keymap.set","params":{"device":"GS3104T-PRO 2.4G","source":"A","target":"B"}}
{"api_version":1,"id":"restore-1","method":"keymap.set","params":{"device":"GS3104T-PRO 2.4G","source":"A","target":"default"}}
{"api_version":1,"id":"layer-1","method":"keymap.apply","params":{"device":"GS3104T-PRO 2.4G","profile":{"version":1,"layer":"normal","mappings":{"A":"B","CapsLock":"CTRL_L"}}}}
```

Python 后端：

```python
from ganss.api import dispatch

response = dispatch({"api_version": 1, "id": "scan-1", "method": "devices.scan"})
if response["ok"]:
    devices = response["result"]["devices"]
```

Electron / Node.js 启动示例（python 路径应指向安装本包的解释器）：

```javascript
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';

const backend = spawn('/path/to/venv/bin/python', ['-m', 'ganss.api'], {
  stdio: ['pipe', 'pipe', 'pipe'],
});
createInterface({ input: backend.stdout }).on('line', line => {
  const response = JSON.parse(line);
  // 根据 response.id 匹配请求，按 response.ok 更新界面。
  console.log(response);
});
backend.stderr.on('data', data => console.error(data.toString()));
backend.on('error', error => console.error(error));
backend.on('exit', code => console.log('backend exited', code));
backend.stdin.write(JSON.stringify({
  api_version: 1, id: 'scan-1', method: 'devices.scan',
}) + '\n');
```

## 响应与错误

成功响应统一为 `{"api_version":1,"id":"...","ok":true,"result":{...}}`。

写操作 result 包含 `device`、`connection`、`dry_run`、`replaces_entire_layer` 和 `warnings`。预览返回 `status="planned"`、`steps`；实际写入返回 `status="transferred"`、`completed_steps`、`trace`、`effect_verified=false`、`persistence_verified=false`。trace 的每项含 direction、channel、hex。

`transferred` 只表示报文已发送且所需响应检查通过。界面应显示“已发送，请确认键盘效果”，不应显示“已验证生效”或“已永久保存”。写入和扫描均为同步调用，Python GUI 应放入工作线程。前端应串行配置同一设备，操作完成前禁用重复提交；不要因超时自动重发整层配置。

失败响应示例：

```json
{"api_version":1,"id":"map-1","ok":false,"error":{"code":"transfer_failed","message":"步骤 2 失败……","may_have_partial_changes":true}}
```

| error.code | 含义 |
| --- | --- |
| invalid_json | 无效 JSON、重复字段或非标准常量；id 返回 null |
| invalid_request | 版本、方法、参数错误，或无法选择唯一硬件/通道 |
| permission_denied | 设备访问权限不足 |
| device_io_error | 打开设备等系统 I/O 错误 |
| transfer_failed | 传输或响应检查失败，可能已有部分写入 |

协议错误不会结束 JSONL 进程；进程意外退出时前端需将尚未收到响应的请求标记为结果未知。当前没有读取设备映射、读取灯光状态、自动回滚或自动读取 Fn+Tab 状态的接口。需要记录操作对象时，请保留请求中的 device 和响应中的实际硬件信息。
