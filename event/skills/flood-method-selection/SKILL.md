---
name: flood-method-selection
description: Select a real method for the Karla Lake EMSN184 recovery case.
---
Karla 湖、卡拉湖或 EMSN184 的实做请求使用当前对话里的 Karla 工具，不调用瓦伦西亚网格或另建运行器。先 karla_inventory 读取真实可用时相和已有 ID。首报截至 2023-12-18：用 karla_acquire 下载 sar，读取两期相对轨道与有效像元；若可用，调用 karla_segment，显式选择 sar_change 并记录为何同轨 VV/VH 可比。不能提前偷看次日光学。更新至 12 月 19 日：下载 optical，检查工具返回的覆盖率；若预/后期可判读，显式选择 optical_mndwi，记下理由。不要复跑历史图替代新观测。只有取得真实结果才报面积。输出 action=reply 和 scope=external。
