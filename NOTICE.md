# 第三方材料

本项目是面向 GS3104T PRO 的独立 Linux 配置工具，与 GANSS、HFD、SONiX 无隶属或官方支持关系。

- `ganss/data/official-firmware.exe` 是用户提供的官方固件工具原始样本，SHA256 为 `1191ad14fabd9c448d45622bab257947464c1c32432aec2d5fd3a1eb04b2e64f`。程序只解析其固件资源，不执行该 Windows EXE。
- `packaging/assets/ganss-gs3104t.png` 来自原厂程序图标；`ganss/data/keyboard.png` 来自原厂驱动的键盘布局示意图。`analysis/evidence/` 中的 XML、语言/键位表、反汇编片段及数据库结构来源于所分析的厂商软件。
- 厂商名称、图标、固件和原始软件材料归各自权利人所有；不将这些材料标记为本项目原创代码。

原始安装目录、说明书、完整反汇编及含无关进程内存的 USB 抓包仅保存在被 Git 忽略的本地目录。公开测试样例仅保留必要协议字段和数据摘要。
