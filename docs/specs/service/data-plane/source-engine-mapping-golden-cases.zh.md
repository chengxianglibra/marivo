# Datasource Routing Golden Cases

> **注意：** Source / Engine / Mapping 三层模型已合并为统一的 Datasource 模型。本文档中的 "source authority"、"execution engine"、"mapping" 概念已被 `datasource` 取代。保留本文档用于回归参考，但术语应以 `docs/api/sources.md` 为准。

本文记录 datasource routing 的最小回归样例。样例只描述当前 contract 边界，不新增 API 或 schema 字段。

在 dataset-native 模型中，物理接地通过 `Dataset.source`（datasource-local relation FQN）和 `Dataset.custom_extensions[].data.datasource_id`（MARIVO extension）表达，不需要独立的 typed binding 或 source_object 快照。

## 1. duckdb -> duckdb pass-through

- Datasource：`type=duckdb`，connection 使用本地 DuckDB。
- Dataset grounding：`source=analytics.watch_events`，`datasource_id=ds_duckdb_main`。
- 期望结果：routing/compile 产出 `analytics.watch_events`（DuckDB 会省略 catalog 前缀）。
- 回归风险：重新把 DuckDB execution catalog 猜测塞进 Dataset.source。

## 2. trino datasource routing

- Datasource：`type=trino`，connection catalog 为 `iceberg_prod`。
- Dataset grounding：`source=iceberg_prod.analytics.watch_events`，`datasource_id=ds_trino_iceberg`。
- 期望结果：routing/compile 使用 datasource connection 的 catalog/schema 解析 `source`。
- 回归风险：sync 阶段写入不同 catalog，导致 dataset source 与实际物理位置不一致。

## 3. datasource unreachable

- Datasource：存在配置但不可达。
- Dataset grounding：引用了不可达的 `datasource_id`。
- 期望结果：readiness 报 `datasource_unreachable` blocker；routing fail closed。
- 回归风险：缺 datasource 时回退到默认 namespace 或历史配置。

## 4. datasource schema incomplete

- Datasource：已注册但 browse 结果不完整（schema/tables/columns 部分缺失）。
- Dataset grounding：引用了 datasource 中不存在的 relation 或 field。
- 期望结果：readiness 报 `dataset_grounding_incomplete` blocker；compile 不消费该 grounding。
- 回归风险：部分 catalog 覆盖被误认为 ready，导致运行时 SQL 才失败。

## 5. dataset grounding unresolved

- Dataset：`source` 或 `datasource_id` 缺失或无效。
- Datasource：是否 ready 不影响 grounding 判断。
- 期望结果：readiness 报 `dataset_grounding_unresolved` blocker；不生成 execution locator。
- 回归风险：用 execution-side table name 反推 source，掩盖 datasource identity 缺陷。
