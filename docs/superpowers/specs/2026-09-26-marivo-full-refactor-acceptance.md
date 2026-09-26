# 全量分析代数与 Analysis DSL 重构阶段验收主记录

Date: 2026-09-26

Status: R0.1 历史基线索引已完成，R0.2 现状反查已记录；R0 整体未验收。

本文件按[主计划](2026-09-26-marivo-full-algebra-dsl-refactor-implementation-plan.md)和[R0 实施文档](2026-09-26-marivo-full-algebra-dsl-r0-implementation-plan.md)续记实际证据。历史验收不自动转成新 DSL 的技术、后端、安装包或真实 Agent 资格。

| R0 出口 | 状态 | 当前证据或缺口 |
| --- | --- | --- |
| R0.1 工作基线、输入 hash、历史证据边界 | **通过（索引与归档）** | [R0.1 索引](2026-09-26-marivo-full-refactor-r0-evidence-index.md)与 [manifest](evidence/r01/manifest.json)；S0/组合正文及 P4 小型机器结果已复制。S4 原轨迹与 wheel 为本机专有历史附件，未授予新资格 |
| R0.2 C01–C18 与消费者反查 | **进行中（现状反查通过；目标格未验证）** | [R0.2 能力台账](2026-09-26-marivo-full-refactor-r0-capability-ledger.md)列出 C01–C18、方法子单元、公开导出/Help、两套执行链、主要消费者与目标去向。具体 typed 输入/K、规则版本、逐方法后端/类型/路线及新目标验收命令依 R0.3–R0.5 尚未冻结，故 R0.2 不标整体通过 |
| R0.3 必需 owning spec 决定 | **未验证** | C18、权重/参照、普通 ratio、业务顺序等尚未在本阶段接受 |
| R0.4 规则、方法、模块责任 | **未验证** | 规则与方法台账尚未建立 |
| R0.5 SQL/adapter/六后端目标资格 | **未验证** | SQL 台账与新矩阵尚未建立；无 SQL 例外获准 |
| R0.6 破坏性变更与 R1/R2 交接 | **未验证** | 目标入口、消费者及验收索引尚未收束 |

R0 整体不得标为通过。R0.1 没有改产品代码，也没有重跑旧 Runtime、安装包或 Agent；可移植原始 Agent 轨迹与 wheel 缺口在索引 §6 记录为历史待补证。R0.2 在 panda 的代码 HEAD 为 `d5e06022c7fcd355b2a31ab6935aea593b60f0d1`，只执行静态扫描并编写文档，未运行产品测试或远端后端。后续阶段应分别记录实际代码 SHA、diff hash、owning spec 版本、通过/失败/未验证/阻塞单元及精确命令。
