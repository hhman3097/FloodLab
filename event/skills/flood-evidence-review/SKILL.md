---
name: flood-evidence-review
description: Translate Karla evidence into distinct cropland and road verification lists.
---
用户要求核查农田、主要道路时，用 karla_exposure 引用已完成的 comparison_id。全范围 study；指定北部用 north。若用户本轮同时要求全范围和北部，分别生成两套，避免混合统计。保留证据日期、统计范围和本地成果；双期一致优先，单期及未判读单列。农田是 WorldCover 土地分类的相交面积，不是损失面积。道路空间重叠不能证明封路，带桥隧标记的路段需核查高程或通行记录。可点击的地图与清单由产品界面挂载，勿编造路径或链接。输出 action=reply，scope=external。

道路使用 2026 年 OpenStreetMap 快照作现状核查参考，不是已验证的 2023 年历史路网；只能说重叠于 2023 年遥感证据，不可称该路在灾时必定通车或封闭。
