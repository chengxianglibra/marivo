# Analysis 设计文档

本目录存放 Factum 的内部分析设计文档，覆盖 typed analysis intents、Evidence Engine canonical schema，以及 agent-first 读取面。这些文档属于内部设计说明，不属于对外 HTTP API 参考。

## 目录组织

文档现已按主题拆分为以下子目录：

- `foundations/`：跨主题基础原则、术语和 agent-first 设计总纲
- `evidence-engine/`：Evidence Engine 主线主题文档
- `evidence-engine/schemas/`：Evidence Engine canonical objects 与读取面的字段级 schema
- `evidence-engine/rules/`：推断规则族、规则注册表与 judgment policy 补充
- `intents/`：typed intent 设计文档
- `intents/atomic/`：原子分析意图 schema
- `intents/derived/`：派生分析意图 schema

## 基础原则

- [中英文术语对照表](foundations/terminology.md) — 统一术语、推荐译法与写作约定
- [Agent 交互分析接口设计准则](foundations/agent-interaction-contract-principles.md) — action surface、state surface、projection surface 的总纲
- [Agent-First Intent Architecture](foundations/agent-first-intent-architecture.md) — Agent-only 场景下 intent system 的总体分工
- [规范 Schema 设计原则](foundations/canonical-schema-principles.md) — canonical schema 的跨主题设计基线

## Evidence Engine 主线

Evidence Engine 的目标态规范链路为：

`artifact -> finding -> proposition -> assessment -> action proposal`

建议按以下顺序阅读：

1. [Evidence Engine 主题总览](evidence-engine/overview.md)
2. [Evidence Engine Runtime Pipeline](evidence-engine/runtime-pipeline.md)
3. [Artifact -> Finding 生成规则](evidence-engine/artifact-finding-generation-rules.md)
4. [Finding -> Proposition Seeding](evidence-engine/finding-proposition-seeding.md)
5. [Inference And Gap Engine](evidence-engine/inference-and-gap-engine.md)
6. [Graph And Reference Semantics](evidence-engine/graph-and-reference-semantics.md)
7. [Evidence Engine Read Surfaces](evidence-engine/read-surfaces.md)

这些主线文档负责解释：

- 整体对象分层与主题边界
- runtime lifecycle、commit boundary、replay、seeding
- seeding template、registration、identity normalization
- assessment recompute、gap management、judgment policy、rule registry
- edge taxonomy、typed refs、closure integrity
- session/state/context 读取面的主题关系

## Evidence Engine 对象 Schema

以下文档仍保留独立 schema 权威地位：

- [Session Schema](evidence-engine/schemas/session.md)
- [Finding Schema](evidence-engine/schemas/finding.md)
- [Proposition Schema](evidence-engine/schemas/proposition.md)
- [Assessment Schema](evidence-engine/schemas/assessment.md)
- [Action Proposal Schema](evidence-engine/schemas/action-proposal.md)
- [State Surface Schema](evidence-engine/schemas/state-surface-schema.md)
- [Context Surface Schema](evidence-engine/schemas/context-surface-schema.md)

如果你需要字段级定义、typed schema、null/empty semantics 或 object-level illegal states，应回到这些对象 schema 文档。

## Evidence Engine Rule Family Extensions

以下文档属于 inference/gap engine 的 family-level extension：

- [Precondition Gate Contract](evidence-engine/rules/precondition-gate-contract.md)
- [Quality Gate Contract](evidence-engine/rules/quality-gate-contract.md)
- [Comparability Gate Contract](evidence-engine/rules/comparability-gate-contract.md)
- [Rule Family Design Checklist](evidence-engine/rules/rule-family-design-checklist.md)

以下文档保留为独立补充，但仅保留主线之外的细粒度内容：

- [Assessment Judgment Policy](evidence-engine/rules/assessment-judgment-policy.md)
- [Rule Registry Contract](evidence-engine/rules/rule-registry-contract.md)

## Analysis Action Surface / Intent System

这些文档描述 typed intent 与执行面，不属于 Evidence Engine 主题本体：

- [原子分析意图设计](intents/primitive-intent-design.md)
- [派生分析意图设计](intents/derived-intent-design.md)
- [`observe` Schema](intents/atomic/observe.md)
- [`compare` Schema](intents/atomic/compare.md)
- [`decompose` Schema](intents/atomic/decompose.md)
- [`correlate` Schema](intents/atomic/correlate.md)
- [`detect` Schema](intents/atomic/detect.md)
- [`test` Schema](intents/atomic/test.md)
- [`forecast` Schema](intents/atomic/forecast.md)
- [`attribute` Schema](intents/derived/attribute.md)
- [`diagnose` Schema](intents/derived/diagnose.md)
- [`validate` Schema](intents/derived/validate.md)

## 对外 API 与读取绑定

- [Session State Surface API](../api/session-state.md) — 对外 HTTP session-level state contract
- [Context Surface API](../api/context-surface.md) — 对外 HTTP proposition-level context contract

## 已迁移映射

以下旧文档的主线职责已迁移到 `docs/analysis/evidence-engine/`，兼容页已清理：

- `evidence-engine-design.md` -> `evidence-engine/overview.md`
- `evidence-engine-runtime-lifecycle.md` -> `evidence-engine/runtime-pipeline.md`
- `artifact-finding-extraction-contract.md` -> `evidence-engine/runtime-pipeline.md`
- `proposition-seeding-contract.md` -> `evidence-engine/finding-proposition-seeding.md`
- `inference-rule-engine-contract.md` -> `evidence-engine/inference-and-gap-engine.md`
- `gap-management-contract.md` -> `evidence-engine/inference-and-gap-engine.md`
- `evidence-graph-edge-semantics.md` -> `evidence-engine/graph-and-reference-semantics.md`
- `reference-integrity-contract.md` -> `evidence-engine/graph-and-reference-semantics.md`
- `state-schema.md` -> `evidence-engine/read-surfaces.md`

## 规范命名基线

为避免上下游文档各自发明平行命名，`docs/analysis/` 统一采用以下基线：

- request type 直接使用各文档中声明的规范名称：`ObserveRequest`、`CompareRequest`、`DecomposeRequest`、`CorrelateRequest`、`DetectRequest`、`TestRequest`、`ForecastRequest`、`AttributeRequest`、`DiagnoseRequest`、`ValidateRequest`
- 原子工件（artifact）的 subtype 由 artifact 本体上的 discriminator 决定，例如 `observation_type`、`comparison_type`、`decomposition_type`、`artifact_type = “anomaly_candidates”`、`result_type = “hypothesis_test”`、`observation_type = “forecast_series”`
- typed ref 必须引用真实存在的规范对象（canonical object）；下游 guard 应写成”`step_type + artifact_id + subtype discriminator`”，不得要求上游产出未定义的 `artifact_type` 字面值
- `scope` 一律复用规范结构化 scope：`constraints + predicate AST`；不得引入字符串 predicate 变体
- truncation、top-k、紧凑视图与派生 bundle 的展示限制只属于投影（projection）/ projection metadata，不属于规范 source identity

## 原子分析意图

- [原子分析意图设计](intents/primitive-intent-design.md) — 原子分析意图家族的跨步骤设计规则
- [`observe` 步骤 Schema](intents/atomic/observe.md) — 规划中的 `observe` 原子意图类型契约草案
- [`compare` 步骤 Schema](intents/atomic/compare.md) — 规划中的 `compare` 原子意图类型契约草案
- [`decompose` 步骤 Schema](intents/atomic/decompose.md) — 规划中的 `decompose` 原子意图类型契约草案
- [`correlate` 步骤 Schema](intents/atomic/correlate.md) — 规划中的 `correlate` 原子意图类型契约草案
- [`detect` 步骤 Schema](intents/atomic/detect.md) — 规划中的 `detect` 原子意图类型契约草案
- [`test` 步骤 Schema](intents/atomic/test.md) — 规划中的 `test` 原子意图类型契约草案
- [`forecast` 步骤 Schema](intents/atomic/forecast.md) — 规划中的 `forecast` 原子意图类型契约草案

## 派生分析意图

- [派生分析意图设计](intents/derived-intent-design.md) — 可执行高层意图如何展开为确定性的原子步骤 DAG
- [`attribute` 派生意图 Schema](intents/derived/attribute.md) — 规划中的变化归因派生意图类型契约草案
- [`diagnose` 派生意图 Schema](intents/derived/diagnose.md) — 规划中的异常诊断派生意图类型契约草案
- [`validate` 派生意图 Schema](intents/derived/validate.md) — 规划中的假设验证派生意图类型契约草案

## 补充说明

- 原子步骤命名与 v1 范围以 [`primitive-intent-design.md`](intents/primitive-intent-design.md) 为准；当前目录中不再维护单独的 `naming-rationale.md`
- `docs/analysis/` 负责设计原则、canonical schema 与 typed intent 语义；若需要对外 HTTP wire contract，应写入 `docs/api/`，而不是在本目录把设计准则写成接口参考
