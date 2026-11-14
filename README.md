# ICD 比特流压缩实验

安装 Python 3.11+ 和 Git LFS，克隆仓库后执行：

    git lfs pull
    python experiment_one/prepare_data.py

数据准备脚本校验原始压缩包的 SHA-256，并提取 ROAD candump 和 DroneKit 测试日志。双击 experiment_one/start.cmd，打开 http://127.0.0.1:8765。详细口径见 [实验平台说明](experiment_one/README.md)。

## 数据来源与署名

- [ROAD 数据集](https://zenodo.org/records/10462796)，Verma 等，ORNL，DOI 10.1371/journal.pone.0296879。[发布者许可说明](https://0xsam.com/road/) 为 Creative Commons Attribution。ROAD ZIP 通过 Git LFS 分发。
- [DroneKit LA Test Data](https://github.com/dronekit/dronekit-la-testdata)，许可 CC BY-SA 3.0，作者署名和许可见原包 README。仓库保留原发布压缩包。
