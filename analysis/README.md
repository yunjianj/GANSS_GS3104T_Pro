# 逆向材料

- [逆向报告](逆向报告.md)：配置协议、模式编号、固件资源与 Windows 抓包结论。
- [实机记录](hardware-tests/results.md)：实际效果与验证边界。
- `evidence/`：适合公开的摘要、XML、数据表和必要反汇编片段。
- `tools/`：只读提取、离线模拟与抓包核对工具。
- `firmware/`：本地提取产物，除说明文件外不加入 Git。

原始厂商目录放在 `local/samples/`，原始抓包放在 `local/captures/`，历史实机日志和探索脚本放在 `local/hardware-tests/`。完整反汇编、字符串及 PE 转储保留在本地 `analysis/evidence/` 并被 `.gitignore` 排除；报告中这些文件名是本地证据索引，公开仓库提供精简片段和生成方法。

普通开发与单元测试只需要仓库内容：

```sh
python -m unittest discover -s tests -q
```

完整原始抓包不公开。`tests/fixtures/official-update.json` 是由其生成的脱敏协议字段与完整写入数据 SHA256；无需原始抓包即可验证后台任务的命令顺序、字节数、内容摘要和错误处理。

拥有原始样本时可进一步复现：

```sh
python analysis/tools/extract_evidence.py
python analysis/tools/extract_light_modes.py
python analysis/tools/extract_isp_profiles.py
python analysis/tools/verify_official_capture.py --capture local/captures/GS3104T-official-update.pcapng
```

前两项使用 `local/samples/` 下的 Windows 驱动目录和安装包，依赖 GNU objdump、strings、innoextract 等离线工具。函数模拟脚本另需 Unicorn；这不是 Linux 配置工具的运行依赖。模板模拟记录是历史分析，实际固件以报告开头的 RCDATA/4000 和成功抓包结论为准。
