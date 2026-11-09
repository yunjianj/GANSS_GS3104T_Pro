# GANSS GS3104T PRO Linux 配置工具

基于项目内原厂驱动的静态逆向实现的 Python 命令行工具，提供灯光控制和普通层、Fn 层按键映射。直接使用 Linux hidraw，不依赖 hidapi、libusb 或第三方 Python 运行库。需要 Python 3.10 及以上版本，适用于采用 asm-generic ioctl ABI 的 Linux，例如 x86_64 和 aarch64。

当前版本为 **0.3.4**。旧版 GS3104T-PRO 2.4G 已实机验证灯光、单键 A→B、普通层整层 A→B，以及恢复默认映射，均由用户确认实际效果。映射需要通过 Fn+Tab 启用驱动自定义按键。详情见 [实机测试记录](analysis/hardware-tests/results.md)。有线扫描与 ISP 查询已实机验证；0.3.2 修复固件资源选取和 `20 03` 流程，并与官方 Windows 成功抓包逐块核对。Linux 完整刷写尚未实机验证。协议依据见 [实现说明](docs/protocol.md) 与 [逆向报告](analysis/逆向报告.md)。

## 快速开始

在项目根目录直接运行，无需安装：

```sh
python -m ganss scan
python -m ganss keys
python -m ganss light --device "GS3104T-PRO 2.4G" --color FF8000 --brightness 4 --speed 3 --dry-run
```

若希望使用 `ganss` 命令，可在自己的虚拟环境中安装：

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install .
ganss --help
```

安装构建依赖是 setuptools，运行时只用标准库。所有灯光和映射操作都必须通过 `--device` 指定扫描结果中的硬件名或 ID。`--dry-run` 读取扫描信息并输出该硬件对应的 JSON 报文计划，不打开设备节点或发送命令。

## 扫描硬件与访问权限

```sh
python -m ganss scan
python -m ganss scan --json
```

扫描只显示对应型号的键盘或接收器，不显示其他厂商或其他型号的键盘。同一物理硬件的多个 HID 接口合并为一条记录，例如：

```text
GS3104T-PRO 2.4G  ID=7c33ef7d34b5  已发现配置通道
```

操作时通过硬件名选择目标：

```sh
python -m ganss light --device "GS3104T-PRO 2.4G" --color FF8000
```

程序每次操作前重新扫描，根据目标硬件的 HID 描述符自动识别有线 Feature 通道或 2.4G 短报文通道，不依赖接口编号或 `/dev/hidrawN` 编号。用户不需要指定连接协议。`devices` 是 `scan` 的兼容别名；此前的 `--all`、设备路径选择和 `--connection` 选项已移除。

多台同名硬件会显示带区分标识的完整名称，例如 `GS3104T-PRO 2.4G [7c33ef7d34b5]`，操作时使用该完整名称或扫描给出的 ID。ID 根据 USB 拓扑路径生成，不受 hidraw 编号变化影响；更换 USB 插口后应重新扫描。程序不会在多个同名设备中自行选择。

扫描是只读操作，显示硬件名称、ID、可识别的通道及访问状态；会区分节点缺失和权限不足。它不发送探测命令，也不能判断无线接收器对应的键盘是否已开机。运行中的提示、成功或错误信息和报文日志均包含所选硬件名。

当前仅支持 USB 总线上的 `05AC:024F` 且产品名匹配 GS3104T PRO 的硬件，常规配置扫描不包含蓝牙及固件升级器的 `0C45:*` 身份；升级后端仅在所选键盘的原 USB 端口追踪 ISP 身份。打开设备后会再次检查型号、VID/PID 和描述符。

如普通用户没有权限，可安装本项目提供的 udev 规则：

```sh
sudo install -m 644 udev/70-ganss-gs3104t.rules /etc/udev/rules.d/70-ganss-gs3104t.rules
sudo udevadm control --reload-rules
```

随后拔插接收器或键盘。规则通过 `uaccess` 授权当前活动的本地登录用户，不把设备设为全局可写。无 logind/无本地登录会话的服务器需按自己的用户组策略配置访问权限。容器或沙箱中仅有 sysfs 记录、没有 `/dev/hidrawN` 时，需要在能访问设备节点的宿主环境运行。

## 灯光控制

扫描后使用对应硬件名，有线和 2.4G 的操作方式相同：

```sh
python -m ganss light --device "GS3104T-PRO 2.4G" --mode 11 --color FF8000 --brightness 4 --speed 3
python -m ganss light --device "GS3104T-PRO 2.4G" --mode 0
```

有线硬件名以扫描结果为准，例如 `HFD GS3104T PRO`。程序保留设备原始 HID 名称，仅在型号匹配时识别已确认的厂商前缀。

参数说明：

| 参数 | 含义 |
| --- | --- |
| `--mode` | 原厂数字模式编号，0 关闭，默认 11；接受 0..255，但并非每个编号都对应有效灯效 |
| `--color` | RRGGBB，例如 `FF8000`；颜色字符串带 `#` 时要加引号 |
| `--brightness` | 0..5，默认 5 |
| `--speed` | 0..5，默认 3 |
| `--colorful` | 设置原厂多彩标志；某些灯效可能忽略单色值 |

本版本不提供部分字段的“保留当前值”语义：每次 `light` 都写入完整灯光参数，未传参数使用上述默认值。暂不提供逐键 RGB、音乐同步、灯效名称枚举或设备状态读取。

## 按键映射

**先检查键盘上的映射开关。** 2024-10-16 版说明书将 `Fn+Tab` 定义为“屏蔽或打开驱动自定义按键指令”。如果配置收到响应但实际按键仍保持默认，可按一次 `Fn+Tab` 后重试。这个组合键切换状态，程序目前不能读取其当前状态，也不会自动切换。

2.4G 可以只更新一个按键，保留同层其他自定义映射：

```sh
python -m ganss keymap set A B --device "GS3104T-PRO 2.4G"
python -m ganss keymap set A default --device "GS3104T-PRO 2.4G"
python -m ganss keymap set H LEFT --layer fn --device "GS3104T-PRO 2.4G"
```

`default` 在普通层恢复该物理键的标准键码，不等于恢复更新前的自定义动作。Fn 层需要显式指定目标。单键更新尚未实现有线通道。

整层配置仍使用 `keymap apply`。先生成一个配置并编辑：

```sh
python -m ganss keymap init > my-keys.json
```

配置格式：

```json
{
  "version": 1,
  "layer": "normal",
  "mappings": {
    "A": "B",
    "CapsLock": "CTRL_L",
    "index:112": "CTRL+SHIFT+S"
  }
}
```

这表示 A 输出 B、CapsLock 输出左 Ctrl、Print Screen 输出 Ctrl+Shift+S。

**配置替换所选层的完整映射，未列出的键恢复原厂默认行为。** 它不会先读取并合并设备中已有的映射。空 `mappings` 用于恢复该层默认映射；`default` 目标也会写入默认槽。保留你自己的完整 JSON 文件，后续编辑该文件再应用。

先预览，再发送：

```sh
python -m ganss keymap apply my-keys.json --device "GS3104T-PRO 2.4G" --dry-run
python -m ganss keymap apply my-keys.json --device "GS3104T-PRO 2.4G"
```

`layer` 可为 `normal` 或 `fn`。Fn 层例子见 [examples/fn-keymap.json](examples/fn-keymap.json)，普通层例子见 [examples/keymap.json](examples/keymap.json)。每次只替换文件指定的层。

键名来源于原厂布局，通过 `python -m ganss keys` 查看。名称不区分大小写，也可用 `index:56` 指定 A。`ENTER` 指主回车，`KP_ENTER` 指小键盘回车；数字键容易与小键盘混淆，建议用列表中的索引精确选择。方向键可写 `LEFT`、`RIGHT`、`UP`、`DOWN`。

目标支持：

- 一个普通 HID 键，例如 `B`、`F1`、`SPACE`、`ENTER`。
- 单独修饰键，例如 `CTRL_L`、`CTRL_R`、`SHIFT_L`、`ALT_R`。
- 多个修饰键与一个普通键组合，例如 `CTRL+SHIFT+C`。
- 用 `usage:0x04` 精确指定标准键盘 usage。
- `default`，恢复该键的默认映射。

本版本保留 Fn 键本身的默认功能。暂不提供媒体键、宏、禁用键或专用系统动作作为映射目标；这些项目仍会以默认行为保留。源按键可使用原厂表中已有的专用键索引。组合键不是宏，不会按时间依次播放多个普通键。

## 报文追踪与错误处理

```sh
python -m ganss light --device "GS3104T-PRO 2.4G" --color 00FF00 --trace session.jsonl
```

追踪文件逐行记录硬件名、硬件 ID，以及实际发送与收到的十六进制报文，包含 Output 补零后的数据。程序在事务的第一个错误处停止，并报告完成了多少步；异常中断可能使设备停留在部分更新状态。本版本不自动重发整段配置，也不保证回滚。

Feature 事务检查原厂使用的状态字节；短报文检查命令、长度和块号回显。短报文等待最多约 400 毫秒；Feature ioctl 的等待时间由内核 USB 栈控制。通过全部响应检查后，输出只声明传输及检查完成，键盘实际效果与断电保存仍需确认。

## 官方固件更新后端

后端 API 新增 `firmware.list`、`firmware.prepare`、`firmware.update`、`firmware.status`。安装包自带项目文件夹中的官方升级 EXE，自动提取并校验其固件，不要求用户上传文件。更新作为后台任务运行，前端可轮询阶段、进度和错误；只能通过 USB 有线连接升级。

**原生 ISP 刷写尚未实机验证。** 当前仅完成官方资源和模拟事务测试；实际更新会替换整个 Flash。支持的芯片/保护状态、udev 权限、版本标签及失败恢复限制见 [固件更新后端说明](docs/firmware-update.md)。本次开发未执行真实刷写。

## 开发与测试

前端可通过 Python 函数或 `python -m ganss.api` 的 JSON Lines 子进程接口调用扫描、灯光、单键与整层映射，详见 [前端接口文档](docs/frontend-api.md)。

```sh
python -m unittest discover -s tests -v
```

测试覆盖扫描时按型号过滤、多接口合并、同名硬件区分、自动选择协议、按硬件名操作，以及原厂编码对应的黄金报文、RGB 字节顺序、修饰键位掩码、576 字节表索引和尾标记、21 块无线重建、校验和、HID 描述符解析、Linux Feature 返回布局、异常停止和命令行离线运行。测试不要求键盘，也不访问真实设备。

源代码位于 `ganss/`：`protocol.py` 负责纯编码，`linux.py` 负责枚举及 hidraw，`runner.py` 执行事务，`cli.py` 提供命令行。键位数据已随包附带，安装后不依赖原厂 EXE 或分析目录。

## Linux 桌面前端

**色彩预览并不可靠，仅供示意；颜色、亮度和动画速度均以键盘实际效果为准。**

提供基于 GTK 3 的原生桌面界面，支持扫描设备、设置灯光、查看近似的灯效示意预览、管理多个键位规则集，以及发送前预览。需要系统安装 GTK 3 和 Python GObject（例如 Debian/Ubuntu 的 `python3-gi`、`gir1.2-gtk-3.0`，或 Arch Linux 的 `python-gobject`、`gtk3`）。使用能导入 `gi` 的 Python 环境，在项目目录运行：

```bash
python -m ganss.gui
```

安装本项目后也可运行 `ganss-gui`。设备权限仍需按本 README 的 udev 说明配置。界面显示的是待发送的参数，不代表从设备读取的当前配置。键位映射页面可创建普通层或 Fn 层规则集，点击原厂布局示意图选择按键，逐键添加、修改和删除规则；规则自动保存在 `~/.config/ganss-gs3104t/profiles.json`（或 `$XDG_CONFIG_HOME/ganss-gs3104t/profiles.json`）。内置的只读默认规则集为空的普通层配置。应用规则集会替换所选整层，未列出的按键恢复默认。

### Arch Linux 安装包

在装有构建依赖的 Arch Linux 上运行 `packaging/arch/build.sh`，安装包将生成在 `dist/`。使用 `sudo pacman -U dist/ganss-gs3104t-0.3.4-2-any.pkg.tar.zst` 安装；安装后可从应用菜单启动。包内包含基于官方程序图标转换的桌面图标和 udev 规则。安装规则后重新插拔设备，使权限规则生效。

## 0.3.2 固件更新修复

固件更新通过随包官方 EXE 的 PE 资源 RCDATA/4000 提取完整 65536 字节镜像，SHA256 为 `42924f581b208c23ea4818c6a4d6db59669dfb4059074b99b635a2ee13c73fed`，校验和 `0x2765`。该镜像与官方 Windows 成功刷写的 1024 个数据块逐字节一致。此前误用的 ZIP 中 SN32F290.hex 属于另一模板，已停止使用。

有线硬件按扫描原名选择；进入 ISP 时允许保留原 USB 身份，使用协议响应确认状态。更新采用 `20 03` 对应的 `01 → 03 → 05 → 数据 → 校验 → 07` 流程，不发送旧模板的独立擦除命令。校验通过并确认键盘重新连接后才报告完成。固件来源、前端接口和验证边界见 [固件更新说明](docs/firmware-update.md)。

## 仓库目录

| 目录 | 内容 |
| --- | --- |
| `ganss/` | CLI、前端 API、GTK 界面、HID 协议与随包固件容器 |
| `tests/` | 离线测试及脱敏的官方升级回放样例 |
| `docs/` | 协议、前端接口、固件更新与抓包说明 |
| `analysis/` | 逆向报告、公开证据摘要与离线分析脚本 |
| `examples/` | 按键映射配置示例 |
| `packaging/`、`udev/` | Arch 构建脚本、桌面集成和设备权限规则 |
| `local/` | 仅本地保留的原始样本、抓包、实机日志和构建缓存；Git 忽略 |
| `dist/` | 构建产物；Git 忽略，通过 GitHub Releases 分发 |

原始抓包无需公开或参与常规测试。脱敏样例保留有效协议字段和完整写入数据 SHA256，不包含主机路径、输入报告或官方工具未初始化的内存。原始材料复现方法见 [分析目录说明](analysis/README.md)，第三方材料归属见 [NOTICE](NOTICE.md)。

发行安装包与校验摘要见 [GitHub Releases](https://github.com/yunjianj/GANSS_GS3104T_Pro/releases)。
