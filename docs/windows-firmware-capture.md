# 官方 Windows 刷写事务记录

目的：记录用户已成功使用的同一官方升级 EXE 的完整 USB 通信，用实际擦写事务核对 Linux 后端。当前 `20 03` 设备的通用表写入范围与归档中 HEX 数据范围存在未解释差异；不据此否定官方兼容性，也不截断或强制刷写。

1. 安装 [Wireshark](https://www.wireshark.org/download.html)，安装时选择 USBPcap；按安装器提示重启。
2. 键盘切到 USB 有线模式并接好。以管理员身份启动 Wireshark。
3. 在首页找到 USBPcap 接口，点击旁边的齿轮。在 Attached USB Devices 中定位 HFD / GS3104T PRO 所在的 Root Hub，启用 Capture from newly connected devices。抓包过滤条件留空，避免漏掉更新时重新连接的设备。
4. 开始捕获，再将键盘 USB 线拔下并插回同一端口，以记录完整的 USB 描述符。
5. 运行此前成功使用的官方固件 EXE，执行一次更新。保留从打开程序、点击更新到提示成功和键盘恢复输入的整个过程。
6. 成功后简单验证一次输入，再等约 5 秒后停止捕获。记录期间仅进行刷写与简单输入验证。
7. 将全部捕获保存成 GS3104T-official-update.pcapng。不要仅导出截图、文本、筛选后的报文或截短数据。文件较大可压缩后提供。

请一并记录 EXE 的完整文件名和升级器显示的最终结果。无需另行上传固件；Linux 后端继续使用随包的官方资源。

若 Wireshark 没有 USBPcap 接口，可先从 [USBPcap 官方发布页](https://github.com/desowin/usbpcap/releases) 安装并重启。接口旁的齿轮用于查看所连设备和配置捕获。必要时根据 [USBPcap 官方教程](https://desowin.org/usbpcap/tour.html)检查 extcap 安装位置。

分析重点：进入 ISP 的完整请求、所有查询及响应、Code Option 修改、独立擦除是否存在、05 请求的计数、实际固件数据块、末尾校验与返回用户模式，以及更新前后的 USB 身份与描述符。
