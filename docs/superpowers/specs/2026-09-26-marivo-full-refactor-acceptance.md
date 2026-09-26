# 全量分析代数与 Analysis DSL 重构阶段验收主记录

Date: 2026-09-26

Status: R0.1 历史基线索引已完成；R0 整体未验收。

本文件按[主计划](2026-09-26-marivo-full-algebra-dsl-refactor-implementation-plan.md)和[R0 实施文档](2026-09-26-marivo-full-algebra-dsl-r0-implementation-plan.md)续记实际证据。历史验收不自动转成新 DSL 的技术、后端、安装包或真实 Agent 资格。

| R0 出口 | 状态 | 当前证据或缺口 |
| --- | --- | --- |
| R0.1 工作基线、输入 hash、历史证据边界 | **通过（索引与归档）** | [R0.1 索引](2026-09-26-marivo-full-refactor-r0-evidence-index.md)与 [manifest](evidence/r01/manifest.json)；S0/组合正文及 P4 小型机器结果已复制。S4 原轨迹与 wheel 为本机专有历史附件，未授予新资格 |
| R0.2 C01–C18 与消费者反查 | **未验证** | 能力台账尚未建立 |
| R0.3 必需 owning spec 决定 | **未验证** | C18、权重/参照、普通 ratio、业务顺序等尚未在本阶段接受 |
| R0.4 规则、方法、模块责任 | **未验证** | 规则与方法台账尚未建立 |
| R0.5 SQL/adapter/六后端目标资格 | **未验证** | SQL 台账与新矩阵尚未建立；无 SQL 例外获准 |
| R0.6 破坏性变更与 R1/R2 交接 | **未验证** | 目标入口、消费者及验收索引尚未收束 |

R0 整体不得标为通过。R0.1 没有改产品代码，也没有重跑旧 Runtime、安装包或 Agent；可移植原始 Agent 轨迹与 wheel 缺口在索引 §6 记录为历史待补证。后续阶段应分别记录实际代码 SHA、diff hash、owning spec 版本、通过/失败/未验证/阻塞单元及精确命令。
