# 随包官方固件更新后端（0.3.2）

后端使用项目中的官方 EXE 作为固件资源容器，不运行 Windows 程序，不依赖 Wine，不接受上传固件、URL 或文件路径。当前固件来源：

`DSM318_108K_RGB三模_HFD64KC200_V1.20_20250722_0x2765.exe`

**0.3.2 修复了资源选取和升级协议两个问题。** 此键盘实际使用 PE 资源 `RCDATA/4000/0` 中的完整 64 KiB 镜像。旧实现误用了同一 EXE 的 ZIP 归档中的 `SN32F290.hex` 通用模板，并采用 `20 05` 的擦除流程；这不是本键盘在 Windows 成功刷写时使用的固件或流程。进入 ISP 后也不要求改变 USB 编号。

用户提供的 Windows 成功抓包已核实：全部 1024 个固件块与此 PE 资源逐字节相等，设备最终返回校验和 `0x2765`，随后恢复原 USB 身份。完整 Linux 后台任务也已使用该抓包进行离线回放验证。**Linux 工具尚未在真实键盘上完成整次刷写**；`windows_capture_verified=true` 和 `hardware_update_verified=false` 明确区分两者。

## 固件及验证数据

| 项目 | 值 |
| --- | --- |
| firmware_id | `gs3104t-pro-v1.20-20250722-2765` |
| 版本 / 构建日期标签 | V1.20 / 2025-07-22 |
| EXE SHA256 | `1191ad14fabd9c448d45622bab257947464c1c32432aec2d5fd3a1eb04b2e64f` |
| 固件 PE 资源 | 类型 10（RCDATA），ID 4000，语言 0 |
| PE 资源 RVA / 文件偏移 | `0x1C564C` / `0x1B964C` |
| 完整镜像大小 | 65536 字节，直接读取，不裁剪或补齐另一份 HEX |
| 镜像 SHA256 | `42924f581b208c23ea4818c6a4d6db59669dfb4059074b99b635a2ee13c73fed` |
| 资源中的目标标签 | 资源 4010：`SN32F24xB` |
| 保护配置 | 资源 4012：1；实机响应 `5A 5A` |
| checksum16 | `0x2765`（10085），小端 16 位字累加的低 16 位 |
| 镜像末尾 | `55 55 AA AA`，地址 `0xFFFC` |
| Windows 抓包 SHA256 | `2255815a117f5d0bc6eb13a6db1a94b4796e5f3e0dca73be0bf10fb475b05644` |

每次加载校验 EXE 摘要、PE 资源目录和范围、固件大小及摘要、向量表、末尾标记和 checksum16。资源摘要用于锁定已分析且与抓包相符的样本，不等同于厂商数字签名。版本字段来自文件名标签，不用于判断当前版本、升级或降级。

## 前端接口

启动 `python -m ganss.api`，通过同一进程的 JSON Lines 调用，`api_version=1`：

| 方法 | params | 行为 |
| --- | --- | --- |
| `firmware.list` | `{}` | 列出唯一随包固件的来源、资源、摘要、大小、校验和及验证状态 |
| `firmware.prepare` | `device`，可选 `firmware_id` | 校验资源、选择有线硬件；不打开设备或进入 ISP |
| `firmware.probe_isp` | 同上 | 进入 ISP、只查询、退出并等待键盘恢复；不调用擦写函数 |
| `firmware.update` | 同上 | 启动更新线程，立即返回任务对象及 `job_id` |
| `firmware.status` | `job_id` | 查询阶段、进度、诊断及结果 |

`device` 必须使用 `devices.scan` 返回的完整名称或 ID。此键盘的有线名称为 `HFD GS3104T PRO`；2.4G 名称为 `GS3104T-PRO 2.4G`，不能对接收器更新。程序根据 HID 描述符自动选择配置通道，不写死 `/dev/hidrawN` 或 USB 接口编号。

```json
{"api_version":1,"id":"fw-list","method":"firmware.list"}
{"api_version":1,"id":"fw-prepare","method":"firmware.prepare","params":{"device":"HFD GS3104T PRO"}}
{"api_version":1,"id":"fw-start","method":"firmware.update","params":{"device":"HFD GS3104T PRO","firmware_id":"gs3104t-pro-v1.20-20250722-2765"}}
{"api_version":1,"id":"fw-status","method":"firmware.status","params":{"job_id":"启动响应中的job_id"}}
```

前端先显示 prepare 返回的硬件名、版本、替换范围和验证状态，由用户点击更新后调用 update。prepare 没有进入 ISP，不能当作芯片已查询。约每 500 毫秒轮询 status 即可。

更新任务的 `total_bytes=65536`，`written_bytes` 仅表示已发送的数据量，不能代替校验。`status` 为 running / completed / failed；阶段依次为：

```text
queued → enter_isp → wait_bootloader → check_chip → restore_code_options
→ enable_program → write_flash → verify_checksum → reset → wait_keyboard → completed
```

只有设备返回正确命令、ACK 和 checksum16，且发送退出并确认原 USB 端口的受支持键盘重新枚举，更新任务才 completed。诊断任务带 `diagnostic_only=true`，完成时 `written_bytes=0`、`checksum_verified=false`，不能作为更新成功。

`firmware.status` 顶层 `ok=true` 仅表示状态查询成功；任务可能已经 failed，错误见 `result.error.code/message`。常见错误包括 wired_connection_required、device_busy、bootloader_timeout、permission_denied、unsupported_isp_profile、unsupported_security、program_enable_failed、verify_failed、keyboard_return_timeout。

`device_changed` 表示尝试切换过运行模式，不表示固件已损坏。`may_have_partial_changes` 在发送 03 前保守设为 true，因为 Code Option 也是持久修改；不能等写入第一块数据后才标记。`reset_sent` 与 `normal_device_returned` 分别表示已发送退出和已确认重新连接。实际查询原始响应记录在 diagnostics，detected_chip 中的通用表标签不单独作为实际硅片型号或容量证明。

更新期间拒绝其他灯光、映射或固件操作，仍允许扫描和查询状态。任务保存在当前进程，最多保留 20 个结果。前端应保持同一后端进程存活；没有取消、自动重试、回滚或跨进程恢复接口。API stdin 关闭后更新线程仍会完成，终止进程则结果未知。

## 实际升级流程

1. 校验随包 EXE 与 PE 固件资源，核对选中硬件、Feature 描述符和 USB 物理端口。
2. 发送 `AA 42 89 5A FF 71 62 CC` 进入 ISP。在原端口通过 `01 AA 55 00` 查询响应确认状态，允许设备保留 `05AC:024F`。私有候选还接受原版通用表中的 `0C45:7040` / `0C45:7140`，但不将其加入普通键盘扫描。
3. 要求查询 ID 为 `20 03 00 00`，保护状态为抓包验证过的 `5A 5A`。不支持其他状态时在任何持久修改之前停止；没有 force 参数。
4. 发送 `03 AA 55 00` 加查询所得的四字节 Code Option，保持原值。不发送独立的 `02` 或 `04` 擦除命令。
5. 发送 `05 AA 55 00 00 00 00 00 00 04`，检查响应头及 `FA FA FA FA` ACK。
6. 顺序发送完整镜像的 1024 个 64 字节数据块，块间间隔 10 毫秒；无额外块头。
7. 读取 `05 AA 55 00` / ACK 响应的字节 8..9，要求等于 `0x2765`。通过后发送 `07 AA 55 00`，等待键盘重新枚举。

官方命令包在有效字段后含有无关进程内存；Linux 实现将未使用字段补零，不复制这些字节。所有实际固件块和有效命令参数与官方抓包一致。

更新会替换固件及其包含的配置，不能保证保留映射、灯光、配对等。校验方式与原厂一致，但没有逐字节读回。开始 03 后遇到错误立即停止，不自动重发或复位；已经握手但在持久修改前拒绝的错误会尝试 07 退出，并报告恢复结果。

## 逆向与复现

| 原版 VA | 依据 |
| --- | --- |
| `0x4261D4..0x426276` | FindResource 类型 10 / ID 4000，LoadResource、LockResource、SizeofResource，复制真正的固件资源 |
| `0x5B2E82` / `0x5B2A53` | `20 03` 的通用记录与 1024 块布局 |
| `0x406BB0` / `0x40A760` | 保留任务 VID/PID，进入 ISP 并查询 |
| `0x407008..0x4070FC` | `20 03` 前置选项设置 03 |
| `0x4072C6..0x4072D5` | 该分支跳过独立擦除 |
| `0x40AF00` / `0x4076E0` | 05 写入请求、ACK 和原始数据块循环 |
| `0x4077A8..0x4077F3` / `0x409880` | 读取偏移 8 的校验和，按实际镜像范围计算 |
| `0x40B270` | 07 退出 ISP |

在源码根目录执行 `python analysis/tools/verify_official_capture.py`，会核对抓包中的所有数据并生成 `analysis/evidence/windows-update-verification.json`。`python -m unittest discover -s tests -q` 包含完整后台任务的抓包回放及异常停止测试，不访问真实硬件。源代码包构建和安装说明见 README。
