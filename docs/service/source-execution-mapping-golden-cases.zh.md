# Source / Execution Mapping Golden Cases

本文记录 source authority、execution engine 与 mapping 的最小回归样例。样例只描述现有 contract 边界，不新增 API 或 schema 字段。

## 1. duckdb -> duckdb pass-through

- Source authority：`source_type=duckdb`，`authority.synthetic_catalog=main`。
- Engine：`engine_type=duckdb`，本地 DuckDB connection；engine default namespace 不参与 catalog projection。
- Mapping：`authority_catalog=main` -> `execution_catalog=main`，`default_schema=null`。
- Source object authority locator：`{"catalog":"main","schema":"analytics","table":"watch_events"}`。
- 期望结果：routing/compile 产出 `analytics.watch_events`（DuckDB 会省略 `main.`）；typed binding grounding 仍引用 source object/authority locator。
- 回归风险：重新把 DuckDB execution catalog 猜测塞进 source 或 binding grounding。

## 2. trino authority -> trino execution remap

- Source authority：`source_type=trino`，authority connection catalog 为 `iceberg_authority`。
- Engine：`engine_type=trino`，execution connection catalog/default namespace 可为 `iceberg_prod`。
- Mapping：`authority_catalog=iceberg_authority` -> `execution_catalog=iceberg_prod`，`default_schema=null`。
- Source object authority locator：`{"catalog":"iceberg_authority","schema":"analytics","table":"watch_events"}`。
- 期望结果：routing/compile 产出 `iceberg_prod.analytics.watch_events`；source object 回读仍只暴露 `iceberg_authority`。
- 回归风险：sync 阶段写入 execution catalog，导致 typed binding 随 engine/catalog 迁移而失效。

## 3. mapping missing

- Source authority：任意 ready source，且 source object 已有 authority locator。
- Engine：可以存在 ready engine，但没有 active + ready mapping 覆盖该 source。
- Mapping：不存在 active mapping。
- Source object authority locator：例如 `{"catalog":"main","schema":"analytics","table":"watch_events"}`。
- 期望结果：routing fail closed，返回 `mapping_missing` / `no_active_mappings` 证据；compile/readiness 在执行 SQL 前暴露 blocker。
- 回归风险：缺 mapping 时回退到 engine default namespace 或历史 binding namespace。

## 4. mapping incomplete

- Source authority：已同步多个 authority catalog，或 source catalog 集合已知。
- Engine：ready engine。
- Mapping：存在 active mapping，但 `catalog_mappings` 为空或未覆盖 source 已知 authority catalog。
- Source object authority locator：落在未覆盖 authority catalog 上。
- 期望结果：mapping readiness 为 `not_ready`，failure code 为 `mapping_incomplete`；routing 不消费该 mapping。
- 回归风险：部分 catalog 覆盖被误认为 ready，导致运行时 SQL 才失败。

## 5. binding grounding unresolved

- Source authority：source object 缺失、source object ref 错误，或 carrier locator 无法唯一匹配 authority locator。
- Engine：是否 ready 不影响 grounding 判断。
- Mapping：即使存在 ready mapping，也不能修复 source-side grounding 缺失。
- Source object authority locator：无法从 typed binding 的 `source_object_ref` 或 carrier locator 确定。
- 期望结果：typed binding/readiness 报 grounding unresolved；不生成 execution locator，不进入 mapping projection。
- 回归风险：用 execution-side table name 反推 source object，掩盖 source authority identity 缺陷。
