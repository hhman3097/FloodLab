# 2026-11-25 产品复跑

使用 \`desktop/check_report_cases.py\`，由实际 Pi / Responses 模型在隔离工作区执行。图和表进入课程报告第 3 章；复跑不覆盖历史数据或冻结的对照实验。

| 报告主张 | 实测依据 |
|---|---|
| 莆田—仙游目录的覆盖与缺口 | \`delivery-qa/report-functional-2026-11-25-fixed-aoi/putian.json\`；两轮均完成，首轮真实查询、二轮仅引用原覆盖图。工作区的 \`coverage.geojson\` 和 \`results.json\` 保留足迹及原查询，正文附浏览器实图。 |
| Kastelli 能查、下载、量算并跨轮引用 | \`delivery-qa/report-functional-2026-11-25/crete.json\`；首两轮查目录和计算，后三轮没有工具调用，分别解释分母、切换地点和恢复原图。 |
| 道路几何有效，但部分交互未完成 | \`delivery-qa/report-functional-2026-11-25-road-retry/\` 的地图和 \`numeric-check.json\`。随后 \`-road-final\` 记录耗尽工具后的文字汇总，\`-road-26\` 和 \`-road-transport\` 记录实际网络失败。 |

复跑指定任务：\`.venv/bin/python desktop/check_report_cases.py /tmp/floodlab-report-run --case putian\`。可另选 \`crete\`、\`valencia\`；省略 \`--case\` 则执行全部。路径须未存在。在线目录与模型回复可能变化；工具返回、输出文件以及独立复算是分别核对的证据。
