## FloodLab 0.6.1

- 切换地区后不会把旧事件的地图当作本轮成果；需要回看时可以打开原图。
- 失败后可继续追问；迟到的停止请求不会卡住队列；工具预算耗尽会明确提示未完成。
- 统计图层丢失时仍可读取历史回答和下载尚在的成果。可在自选工作区打开登记的 QGIS 工程。
- EMS 目录与新闻引用分开处理；跨事件地图标注排名网格，PDF 文字可搜索。

下载 ZIP 后完整解压，再打开应用。内置 Pi 和 QGIS；历史案例无需 API Key，执行新任务需要配置 Responses 接口。

- macOS：Apple 芯片 `arm64`，已从本版 ZIP 验证首次启动及保留对话的升级。
- Windows：`x64`，已验证 ZIP 和包内依赖与代码散列，尚未在 Windows 实机运行。

安装包可用 `SHA256SUMS.txt` 核对。课程报告作为研究结果的冻结文档，版本仍为 0.6.0；软件回归见 [验收记录](https://github.com/hhman3097/FloodLab/blob/main/docs/ACCEPTANCE.md)。
