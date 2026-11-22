# FloodLab 0.6.0 研究环境

## 新机器复现

需要 Python 3.12、uv、Node/npm、QGIS 3.44 及 XeLaTeX/latexmk。
本机实跑均未使用外部 GPU。以下命令在项目根目录运行：

```sh
uv sync --locked --python 3.12 --extra qa
micromamba create -y -p "$PWD/.tools/qgis" -c conda-forge 'qgis=3.44'
npm --prefix workbench ci --ignore-scripts
```

压缩包包含事件输入、矢量结果和历史地图，但不含软件环境、Sen1Floods11 原始输入、
公开权重或本地旧模型缓存。解压后先迁移历史运行记录中的绝对路径：

```sh
.venv/bin/python rebase_paths.py
.venv/bin/python workbench/server.py
```

迁移保留原始 JSON 到 `relocation-originals/` 并写出迁移散列日志；
原始 `tools.jsonl` 不修改。QGIS 工程使用相对引用，需保留整个项目结构。
仅下载一个 QGZ 不会包含它引用的全部数据。

### 遥感基准与事件分析

```sh
uv run --no-sync python floodlab.py fetch --split test
uv run --no-sync python floodlab.py run --split test --device cpu
uv run --no-sync python figures.py
uv run --no-sync python event/analyze.py
env QT_QPA_PLATFORM=offscreen QGIS_PREFIX_PATH="$PWD/.tools/qgis" \
  PYTHONPATH="$PWD/.tools/qgis/share/qgis/python" \
  .tools/qgis/bin/python event/cartography.py --mode both
uv run --no-sync pytest -q
```

`fetch` 按公开地址下载官方清单、影像/标注和固定检查点；
`outputs/inputs-test.json` 给出 182 项输入的散列。
事件输入已有归档，需重取时运行 `event/acquire.py`、`event/acquire_assets.py`。
`pytest` 的全清单核对需要先下载 Sen1Floods11 输入。

浏览器、真实模型和桌面候选版验收见 [回归验收](ACCEPTANCE.md)。

### 新版 Agent 与独立核验

```sh
.venv/bin/python event/gis_agent.py --workspace event/runs/my-run \
  --question '按 1 km 网格统计所有已提供道路的暴露长度和暴露率并制图'
.venv/bin/python event/benchmark_gis.py --output event/runs/my-comparison
.venv/bin/python event/evaluate_comparison.py
.venv/bin/python event/verify_gis.py \
  event/runs/gis-agent-pilot-v2/major_grid_exposure.gpkg \
  --grid-size 2000 --ratio-field exposure_pct --ratio-scale 100 \
  --classes primary primary_link secondary secondary_link
```

API key 以隐藏输入读取，也支持 `AI4S_API_KEY` 环境变量。新任务目录必须未存在；
中断恢复使用 `--resume`。`evaluate_comparison.py` 默认核对交付中归档的对照，
不覆盖其模型记录；新的模型运行可能选择不同字段，需按实际字段调用 `verify_gis.py`。
完整参数、限制和产品检查见 `workbench/README.md`。
