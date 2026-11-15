# ICD 比特流压缩实验平台

实验一对比 zlib（级别 1、6）、Zstandard（级别 1）和预置共享字典的 Zstandard。目标是检验公开报文载荷的最终发送长度能否节省至少 20%，并对每个样本逐 bit 验证无损恢复。公开数据提供实验基线，当前仓库没有甲方 ICD 实例。

## Windows 快速启动

安装 Python 3.11 或更新版本，以及 Git for Windows 和 Git LFS。在 PowerShell 中执行：

```powershell
git lfs install
git clone https://github.com/obitokks/ICD.git
cd ICD
git lfs pull
python experiment_one/prepare_data.py
.\experiment_one\start.cmd
```

数据准备脚本校验原包 SHA-256 并解包。预留约 2 GB 磁盘空间供原包、解包输入和运行环境使用，实验结果库会随样本数增长。启动脚本会创建 Python 虚拟环境并安装依赖。打开 [本地实验台](http://127.0.0.1:8765)，在配置页选择数据源、算法、模式、样本数量和种子，再点击「启动实验」。也可点「一键跑全部实验」，两种模式依次覆盖 3 个数据源和 4 种算法，共 24 个组合。

## 测试数据

| 来源 | 仓库中的原包 | 用于实验的数据 |
|---|---|---|
| ROAD | [road.zip](ICD_Bitstream_Data/01_raw/ROAD/road.zip)，556,718,276 字节，经 Git LFS 获取 | 原始 candump 的 CAN 数据区 |
| DroneKit | [dronekit.zip](ICD_Bitstream_Data/01_raw/DroneKit/dronekit.zip)，5,091,257 字节 | MAVLink 的 flight.tlog 和 DataFlash 的 log171.bin |

两个原包的校验值和来源见[来源登记](ICD_Bitstream_Data/00_docs/source_register.csv)。GitHub 的「Download ZIP」可能只包含 ROAD 的 LFS 指针，按上面的 Git 命令获取完整数据。HCRL Car-Hacking 的官方下载未成功，本实验数据集包含上表所列公开原包。

## 样本、算法与计量

- 同一原始文件内按记录顺序累积完整载荷，达到至少 376 字节（严格超过 3000 bit）后形成一块。每块调用一次压缩器。训练区与测试区独立分块，末尾不足阈值的片段记录后排除。每来源样本数指合并后的块数。
- 模式 A 原样保留首部和适用的原校验，整块计费。ROAD 的首部为从 candump CAN ID 提取的 4 字节逻辑首部，MAVLink 和 DataFlash 使用日志中的首部。模式 B 仅处理连续拼接的载荷。
- 模式 A 的合并表示为「首部段、载荷段、原校验段」。实验假设外层已知合并块和各段长度。每条源记录的偏移供抽查追溯，发送块无需附带记录边界表。
- 字典由各来源独立训练区生成，收发双方预置。既有 zstd 字典 ID 随压缩帧计费，字典本体无需随块发送。
- 每块计入 1 字节模式标记和实际采用的压缩封装。压缩输出未缩短载荷时回退原样发送，模式标记仍计费。总压缩率是各样本「最终发送长度 ÷ 原始长度」的算术平均，单块比例 ≤80% 且验证通过计为达标。
- 平均原始长度按每个数据源的已测试样本统计，同一样本由多种算法测试时只计一次。模式 A 的原始长度包含旁路首部和校验，模式 B 为载荷长度。
- 编码和解码分别记录用时，并保存环境、压缩输出、发送块、恢复数据和无损校验信息。

## 查看、导出与复现

「实时测试」显示后台进度；「样本抽查」按样本序号查看原始、编码及恢复数据和 SHA-256；「实验结果」支持 CSV、JSON、逐样本 JSONL 和多实验 PDF 导出。「实验记录」支持查看参数、暂停恢复，以及经确认删除单条记录。当前结果库由实验启动后在本机生成。

更多说明见 [平台 README](experiment_one/README.md)。开发验收可运行 `python experiment_one/smoke_test.py`，验收使用独立临时数据。

## 数据来源与署名

- [ROAD 数据集](https://zenodo.org/records/10462796)，Verma 等，ORNL，DOI 10.1371/journal.pone.0296879。[作者的许可说明](https://0xsam.com/road/) 为 Creative Commons Attribution。
- [DroneKit LA Test Data](https://github.com/dronekit/dronekit-la-testdata)，CC BY-SA 3.0。作者署名与许可见原包 README。
