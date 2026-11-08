# 本地固件提取产物

此目录除本文件外均为可再生成产物，不加入 Git。

- `official-rcdata-4000.bin`：真正用于当前键盘的完整 64 KiB 镜像，由 `verify_official_capture.py` 提取。
- `Settings/FWFiles/SN32F290.hex` 和对应分段文件：同一 EXE 内的另一归档模板，保留作历史分析，不用于当前键盘升级。

运行时从仓库内固定摘要的 `ganss/data/official-firmware.exe` 读取 PE 资源，不依赖本目录。
