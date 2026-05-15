# Marivo MCP 工具参考文档

本文档介绍 Marivo MCP 所有 32 个工具的完整输入参数与输出结构，按三个分析面（Datasource / Semantic Layer / Analysis）组织。每个字段标注明确类型，每项工具附带输入输出示例 JSON。

---

## 通用响应信封

所有工具返回统一信封格式：

```typescript
interface MarivoResponse<T> {
  data: T | null;          // 业务数据，失败时为 null
  error: string | null;    // 错误信息，成功时为 null
}
```

下文各工具的"输出"仅描述 `data` 字段内部的类型结构，省略信封外壳。示例 JSON 展示完整信封结构。

---

## 一、Datasource Surface — 数据源管理

### 1.1 create_datasource

创建数据源。支持 `duckdb` 和 `trino` 两种类型。

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| datasource_type | `"duckdb"` \| `"trino"` | 是 | 数据源类型枚举 |
| display_name | string | 是 | 数据源显示名称 |
| connection | object \| null | 否 | 连接参数对象，结构随 datasource_type 不同而异；null 时使用空对象 {} |

connection 字段 — trino 类型：

| 键 | 类型 | 默认 | 说明 |
|----|------|------|------|
| datasource_type | `"trino"` | — | 必填，与顶层 datasource_type 一致 |
| host | string | — | 服务器地址 |
| port | integer | 8080 | 端口号 |
| user | string \| null | null | 连接用户名 |
| catalog | string \| null | null | 默认 catalog |
| http_scheme | `"http"` \| `"https"` | `"http"` | HTTP 协议 |
| source | string \| null | null | 来源标识 |
| client_tags | string \| null | null | 客户端标签 |
| session_properties | object | {} | Trino session 属性键值对 |

connection 字段 — duckdb 类型：

| 键 | 类型 | 默认 | 说明 |
|----|------|------|------|
| datasource_type | `"duckdb"` | — | 必填，与顶层 datasource_type 一致 |
| path | string \| null | null | DuckDB 文件路径 |
| database | string \| null | null | 数据库名 |
| db_path | string \| null | null | 完整数据库路径 |

**输出 — Datasource**：

```typescript
interface Datasource {
  datasource_id: string;                        // 如 "ds_15c11b454309"
  datasource_type: "duckdb" | "trino";          // 类型枚举
  display_name: string;                         // 如 "Trino - Example Analytics"
  connection: TrinoConnection | DuckDbConnection; // 同输入 connection（含 datasource_type）
  owner_user: string | null;                    // 创建者，可能为 null
  status: "active" | "inactive" | "deprecated"; // 状态枚举，默认 "active"
  readiness_status: "not_ready" | "ready";      // 必须为 "ready" 才可使用
  failure_code: string | null;                  // 失败时的错误码
  created_at: string;                           // ISO-8601 datetime
  updated_at: string;                           // ISO-8601 datetime
}
```

**输入示例**：

```json
{
  "datasource_type": "trino",
  "display_name": "Trino - Example Analytics",
  "connection": {
    "datasource_type": "trino",
    "host": "trino.example.internal",
    "port": 8080,
    "user": "analytics",
    "catalog": "iceberg",
    "http_scheme": "https"
  }
}
```

**输出示例**：

```json
{
  "data": {
    "datasource_id": "ds_15c11b454309",
    "datasource_type": "trino",
    "display_name": "Trino - Example Analytics",
    "connection": {
      "datasource_type": "trino",
      "host": "trino.example.internal",
      "port": 8080,
      "user": "analytics",
      "catalog": "iceberg",
      "http_scheme": "https",
      "source": null,
      "client_tags": null,
      "session_properties": {}
    },
    "owner_user": "lichengxiang",
    "status": "active",
    "readiness_status": "ready",
    "failure_code": null,
    "created_at": "2025-03-01T10:00:00Z",
    "updated_at": "2025-03-01T10:00:00Z"
  },
  "error": null
}
```

---

### 1.2 list_datasources

列出所有已注册数据源。

**输入参数**：无

**输出 — Datasource[]**：数据源对象数组，结构同 1.1 Datasource。

**输出示例**：

```json
{
  "data": [
    {
      "datasource_id": "ds_15c11b454309",
      "datasource_type": "trino",
      "display_name": "Trino - Example Analytics",
      "connection": { "datasource_type": "trino", "host": "trino.example.internal", "port": 8080, "user": "analytics", "catalog": "iceberg", "http_scheme": "https", "source": null, "client_tags": null, "session_properties": {} },
      "owner_user": "lichengxiang",
      "status": "active",
      "readiness_status": "ready",
      "failure_code": null,
      "created_at": "2025-03-01T10:00:00Z",
      "updated_at": "2025-03-01T10:00:00Z"
    }
  ],
  "error": null
}
```

---

### 1.3 get_datasource

读取单个数据源详情。

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| datasource_id | string | 是 | 数据源ID，如 `"ds_15c11b454309"` |

**输出 — Datasource**：单个数据源对象，结构同 1.1 Datasource。

**输入示例**：

```json
{ "datasource_id": "ds_15c11b454309" }
```

**输出示例**：同 1.1 输出示例。

---

### 1.4 update_datasource

更新数据源属性。

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| datasource_id | string | 是 | 数据源ID |
| display_name | string \| null | 否 | 新显示名称，null 表示不更新 |
| connection | object \| null | 否 | 新连接参数（需包含完整对象含 datasource_type），null 表示不更新 |

**输出 — Datasource**：更新后的数据源对象，结构同 1.1 Datasource。

**输入示例**：

```json
{
  "datasource_id": "ds_15c11b454309",
  "display_name": "Trino - Example Analytics (Updated)"
}
```

**输出示例**：同 1.1 输出示例（display_name 为新值）。

---

### 1.5 delete_datasource

删除数据源。

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| datasource_id | string | 是 | 数据源ID |

**输出 — DeleteResult**：

```typescript
interface DeleteResult {
  datasource_id: string;    // 被删除的数据源ID
  deleted: boolean;         // 是否成功删除，默认 true
}
```

**输入示例**：

```json
{ "datasource_id": "ds_15c11b454309" }
```

**输出示例**：

```json
{
  "data": {
    "datasource_id": "ds_15c11b454309",
    "deleted": true
  },
  "error": null
}
```

---

### 1.6 browse_schemas

浏览数据源中的 schema 列表。

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| datasource_id | string | 是 | 数据源ID |
| catalog | string \| null | 否 | 限定 catalog（多 catalog 数据源适用），null 表示不限 |

**输出 — BrowseSchemaItem[]**：

```typescript
interface BrowseSchemaItem {
  schema_name: string;      // schema 名称，如 "iceberg_inf"
  table_count: integer;     // 该 schema 下的表数量
}
```

**输入示例**：

```json
{ "datasource_id": "ds_15c11b454309", "catalog": null }
```

**输出示例**：

```json
{
  "data": [
    { "schema_name": "iceberg_inf", "table_count": 12 },
    { "schema_name": "default", "table_count": 3 }
  ],
  "error": null
}
```

---

### 1.7 browse_tables

浏览指定 schema 下的表列表。

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| datasource_id | string | 是 | 数据源ID |
| schema_name | string \| null | 否 | Schema 名称；**实际必填**，null 时返回验证错误 |
| catalog | string \| null | 否 | 限定 catalog |

**输出 — BrowseTableItem[]**：

```typescript
interface BrowseTableItem {
  table_name: string;       // 表名，如 "dwd_olap_trino_query_info_i_hr"
  schema_name: string;      // 所属 schema，如 "iceberg_inf"
  row_count: integer | null; // 行数估算，可能为 null
  column_count: integer | null; // 列数量，可能为 null
}
```

**输入示例**：

```json
{ "datasource_id": "ds_15c11b454309", "schema_name": "iceberg_inf", "catalog": null }
```

**输出示例**：

```json
{
  "data": [
    { "table_name": "dwd_olap_trino_query_info_i_hr", "schema_name": "iceberg_inf", "row_count": 1500000, "column_count": 28 },
    { "table_name": "dwd_olap_cluster_resource_i_hr", "schema_name": "iceberg_inf", "row_count": null, "column_count": 15 }
  ],
  "error": null
}
```

---

### 1.8 browse_columns

浏览指定表的列信息。

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| datasource_id | string | 是 | 数据源ID |
| schema_name | string | 是 | Schema 名称 |
| table_name | string | 是 | 表名称 |

**输出 — ColumnInfo[]**：

```typescript
interface ColumnInfo {
  name: string;               // 列名，如 "cluster"
  schema_name: string;        // 所属 schema
  table_name: string;         // 所属表
  data_type: string | null;   // 数据类型，如 "varchar"、"double"、"bigint"；可能为 null
  properties: object;         // 列属性键值对，值可为 string | integer | float | boolean | null
}
```

**输入示例**：

```json
{
  "datasource_id": "ds_15c11b454309",
  "schema_name": "iceberg_inf",
  "table_name": "dwd_olap_trino_query_info_i_hr"
}
```

**输出示例**：

```json
{
  "data": [
    { "name": "cluster", "schema_name": "iceberg_inf", "table_name": "dwd_olap_trino_query_info_i_hr", "data_type": "varchar", "properties": { "nullable": true, "comment": "集群标识" } },
    { "name": "query_count", "schema_name": "iceberg_inf", "table_name": "dwd_olap_trino_query_info_i_hr", "data_type": "bigint", "properties": { "nullable": false } }
  ],
  "error": null
}
```

---

### 1.9 preview_table

预览表的样本行（仅用于元数据理解，不是分析证据）。

**输入参数**：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| datasource_id | string | 是 | — | 数据源ID |
| schema | string | 是 | — | Schema 名称 |
| table | string | 是 | — | 表名称 |
| limit | integer | 否 | 100 | 最大行数，上限 1000 |
| columns | string \| null | 否 | null | 逗号分隔的列名列表，null 返回所有列 |
| filters | object \| null | 否 | null | 按列名键控的等值过滤条件，如 `{"state":"FAILED"}` |

**输出 — PreviewResult**：

```typescript
interface PreviewResult {
  datasource_id: string;            // 数据源ID
  schema_name: string;              // schema 名称
  table_name: string;               // 表名称
  columns: ColumnBrief[];           // 返回列的类型摘要
  rows: object[];                   // 行数据数组，每行为 { 列名: 值 } 对象
  row_count: integer;               // 实际返回行数
  truncated: boolean;               // 是否因 limit 截断
  limit_requested: integer;         // 用户请求的 limit
  limit_applied: integer;           // 实际应用的 limit
  filters_applied: object | null;   // 实际应用的过滤条件
}

interface ColumnBrief {
  name: string;                     // 列名
  type: string;                     // 数据类型
}
```

**输入示例**：

```json
{
  "datasource_id": "ds_15c11b454309",
  "schema": "iceberg_inf",
  "table": "dwd_olap_trino_query_info_i_hr",
  "limit": 5,
  "columns": "cluster,query_count,state",
  "filters": {
    "state": "FAILED"
  }
}
```

**输出示例**：

```json
{
  "data": {
    "datasource_id": "ds_15c11b454309",
    "schema_name": "iceberg_inf",
    "table_name": "dwd_olap_trino_query_info_i_hr",
    "columns": [
      { "name": "cluster", "type": "varchar" },
      { "name": "query_count", "type": "bigint" },
      { "name": "state", "type": "varchar" }
    ],
    "rows": [
      { "cluster": "jscs-ai-offline", "query_count": 1523, "state": "FAILED" },
      { "cluster": "jscs-ai-online", "query_count": 891, "state": "FAILED" }
    ],
    "row_count": 2,
    "truncated": false,
    "limit_requested": 5,
    "limit_applied": 5,
    "filters_applied": { "state": "FAILED" }
  },
  "error": null
}
```

---

## 二、Semantic Layer Surface — 语义模型管理

### 2.1 list_semantic_models

列出所有语义模型。

**输入参数**：无

**输出 — SemanticModelSummary[]**：

```typescript
interface SemanticModelSummary {
  name: string;                 // 模型名称，如 "trino_query_analysis"
  description: string;          // 模型描述
  status: string;               // 模型状态
  created_at: string;           // ISO-8601 datetime
  updated_at: string;           // ISO-8601 datetime
}
```

**输出示例**：

```json
{
  "data": [
    {
      "name": "trino_query_analysis",
      "description": "Trino 查询量与性能分析语义模型",
      "status": "active",
      "created_at": "2025-03-01T10:00:00Z",
      "updated_at": "2025-03-10T08:30:00Z"
    }
  ],
  "error": null
}
```

---

### 2.2 get_semantic_model

获取语义模型详情（完整 OSI-Marivo 文档）。

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| model | string | 是 | 模型名称，如 `"trino_query_analysis"` |

**输出 — OsiDocument**：

```typescript
interface OsiDocument {
  version: string;                          // "0.1.1"
  dialects: string[];                       // ["ANSI_SQL"]
  vendors: string[];                        // ["MARIVO"]
  semantic_model: SemanticModel[];          // 语义模型数组
}

interface SemanticModel {
  name: string;                             // 模型标识
  description: string;                      // 模型描述
  ai_context: AiContext | undefined;        // AI 辅助信息
  datasets: Dataset[];                      // 数据集数组
  relationships: Relationship[];            // 关系数组
  metrics: Metric[];                        // 指标数组
}

interface AiContext {
  instructions: string;                     // AI 使用指引
  synonyms: string[];                       // 同义词列表
  examples: string[];                       // 示例问题列表
}

interface Dataset {
  name: string;                             // 数据集标识
  source: string;                           // 物理表/视图的 relation FQN（schema.table 或 catalog.schema.table），不支持 SQL query
  primary_key: string[];                    // 主键列名数组
  unique_keys: string[][];                  // 唯一键数组
  description: string;                      // 数据集描述
  ai_context: AiContext | undefined;
  fields: Field[];                          // 字段数组
  custom_extensions: DatasetExtension[];    // MARIVO 扩展
}

interface Field {
  name: string;                             // 字段标识（语义名）
  expression: Expression;                   // 表达式定义
  dimension: Dimension | undefined;         // 维度标记
  label: string | undefined;                // 分类标签
  description: string | undefined;          // 字段描述
  ai_context: AiContext | undefined;
  custom_extensions: object[] | undefined;
}

interface Expression {
  dialects: DialectExpression[];
}

interface DialectExpression {
  dialect: string;                          // "ANSI_SQL" | 其他方言
  expression: string;                       // 方言表达式字符串
}

interface Dimension {
  is_time: boolean | undefined;             // true 表示时间维度
}

interface DatasetExtension {
  vendor_name: string;                      // "MARIVO"
  data: {
    datasource_id: string;                  // 数据源ID引用
  };
}

interface Metric {
  name: string;                             // 指标标识
  expression: Expression;                   // 聚合表达式
  description: string | undefined;
  ai_context: AiContext | undefined;
  custom_extensions: MetricExtension[];     // 仅可加指标有此扩展
}

interface MetricExtension {
  vendor_name: string;                      // "MARIVO"
  data: {
    additive_dimensions: string[];          // 可加维度列表，最少 1 项
  };
}

interface Relationship {
  name: string;
  from: string;                             // 多端数据集名称
  to: string;                               // 一端数据集名称
  from_columns: string[];
  to_columns: string[];
  ai_context: AiContext | undefined;
}
```

**输入示例**：

```json
{ "model": "trino_query_analysis" }
```

**输出示例**（精简）：

```json
{
  "data": {
    "version": "0.1.1",
    "dialects": ["ANSI_SQL"],
    "vendors": ["MARIVO"],
    "semantic_model": [
      {
        "name": "trino_query_analysis",
        "description": "Trino 查询量与性能分析",
        "ai_context": { "instructions": "分析Trino查询量趋势与异常", "synonyms": ["trino查询", "查询量"], "examples": ["最近一周Trino查询量趋势"] },
        "datasets": [
          {
            "name": "trino_query_log",
            "source": "iceberg_inf.dwd_olap_trino_query_info_i_hr",
            "primary_key": ["query_id"],
            "unique_keys": [],
            "description": "Trino查询日志",
            "fields": [
              { "name": "cluster", "expression": { "dialects": [{ "dialect": "ANSI_SQL", "expression": "cluster" }] }, "dimension": {}, "description": "集群标识" },
              { "name": "query_count", "expression": { "dialects": [{ "dialect": "ANSI_SQL", "expression": "query_count" }] }, "dimension": undefined, "description": "查询数量" },
              { "name": "create_time", "expression": { "dialects": [{ "dialect": "ANSI_SQL", "expression": "create_time" }] }, "dimension": { "is_time": true }, "description": "查询时间" }
            ],
            "custom_extensions": [{ "vendor_name": "MARIVO", "data": { "datasource_id": "ds_15c11b454309" } }]
          }
        ],
        "metrics": [
          { "name": "total_query_count", "expression": { "dialects": [{ "dialect": "ANSI_SQL", "expression": "SUM(query_count)" }] }, "description": "总查询量", "custom_extensions": [{ "vendor_name": "MARIVO", "data": { "additive_dimensions": ["cluster", "department"] } }] }
        ],
        "relationships": []
      }
    ]
  },
  "error": null
}
```

---

### 2.3 delete_semantic_model

删除语义模型。

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| model | string | 是 | 模型名称 |

**输出**：删除确认结果。

**输入示例**：

```json
{ "model": "trino_query_analysis" }
```

**输出示例**：

```json
{
  "data": { "deleted": true, "model": "trino_query_analysis" },
  "error": null
}
```

---

### 2.4 validate_osi_semantic_models

验证 OSI-Marivo 语义文档的结构合规性。验证通过不代表业务审批。

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| input.input_path | string \| null | 否 | 本地 JSON 文件路径，优先使用 |
| input.document | object \| null | 否 | inline OSI-Marivo 文档对象 |

`input_path` 和 `document` **必须恰好提供一个**，推荐 `input_path`。

**输出 — ValidationResult**：

```typescript
interface ValidationResult {
  valid: boolean;                          // 是否通过验证
  schema_version: string;                  // "0.1.1"
  errors: ValidationError[];               // 错误列表，验证通过时为空数组
  warnings: ValidationWarning[];           // 警告列表
  summary: ValidationSummary;              // 文档统计摘要
}

interface ValidationError {
  code: string;                            // 错误码
  message: string;                         // 错误详细描述
  json_pointer: string;                    // JSON Path 指向出错位置
  severity: string;                        // "error" | "warning"
  hint: string;                            // 修复建议
  context: object;                         // 上下文信息
}

interface ValidationWarning {
  code: string;
  message: string;
}

interface ValidationSummary {
  models: integer;
  datasets: integer;
  fields: integer;
  metrics: integer;
  relationships: integer;
}
```

验证错误码包括：`SCHEMA_VALIDATION_FAILED`、`EMPTY_SEMANTIC_MODEL`、`DUPLICATE_NAME`、`UNKNOWN_DATASET`、`UNKNOWN_FIELD`、`INVALID_AGGREGATION_SEMANTICS`、`INVALID_DATASET_SOURCE`（source 不是合法 relation FQN）等。

**输入示例**（使用 input_path）：

```json
{
  "input": {
    "input_path": "/path/to/trino_query_analysis.json"
  }
}
```

**输出示例**（验证通过）：

```json
{
  "data": {
    "valid": true,
    "schema_version": "0.1.1",
    "errors": [],
    "warnings": [],
    "summary": { "models": 1, "datasets": 1, "fields": 15, "metrics": 3, "relationships": 0 }
  },
  "error": null
}
```

---

### 2.5 import_osi_semantic_models

导入 OSI-Marivo 语义文档到 Marivo 平台。**必须在用户明确批准后方可调用。**

**输入参数**：同 2.4 validate。

**输出 — ImportResult**：

```typescript
interface ImportResult {
  imported_models: string[];               // 成功导入的模型名称数组
  status: string;                          // "success" | "partial" | "failed"
  errors: ImportError[];                   // 导入错误列表，成功时为空数组
}

interface ImportError {
  model_name: string;
  message: string;
}
```

**输入示例**：

```json
{
  "input": {
    "input_path": "/path/to/trino_query_analysis.json"
  }
}
```

**输出示例**（导入成功）：

```json
{
  "data": {
    "imported_models": ["trino_query_analysis"],
    "status": "success",
    "errors": []
  },
  "error": null
}
```

---

### 2.6 export_osi_semantic_models

导出 OSI-Marivo 语义文档，可选写入本地文件。

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| semantic_model_name | string \| null | 否 | 模型名称，null 导出默认 scope |
| output_path | string \| null | 否 | 本地 JSON 文件路径，指定则同时写入文件 |

注意：这两个参数是顶层参数，**不嵌套在 `input` 对象中**。

**输出**：未指定 `output_path` 时返回 OsiDocument（结构同 2.2）；指定 `output_path` 时返回含 `output_path` 和 `document` 的对象。

**输入示例**（导出并写入文件）：

```json
{
  "semantic_model_name": "trino_query_analysis",
  "output_path": "/path/to/exported_model.json"
}
```

**输出示例**（指定 output_path）：

```json
{
  "data": {
    "output_path": "/path/to/exported_model.json",
    "document": { "version": "0.1.1", "dialects": ["ANSI_SQL"], "vendors": ["MARIVO"], "semantic_model": [...] }
  },
  "error": null
}
```

**输出示例**（未指定 output_path）：

```json
{
  "data": { "version": "0.1.1", "dialects": ["ANSI_SQL"], "vendors": ["MARIVO"], "semantic_model": [...] },
  "error": null
}
```

---

## 三、Analysis Surface — 分析会话与 Intent

### 3.1 create_session

创建分析会话。

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| goal | string | 是 | 分析目标描述 |
| budget | object \| null | 否 | 预算限制配置 |
| policy | object \| null | 否 | 分析策略配置 |

**输出 — AnalysisSession**：

```typescript
interface AnalysisSession {
  session_id: string;                       // 会话ID
  goal: SessionGoal;                        // 分析目标（嵌套对象）
  scope: SessionScope;                      // 会话限定范围
  owner_user: string | null;                // 创建者
  lifecycle: SessionLifecycle;              // 会话生命周期状态
  state_summary: SessionStateSummary;       // 会话状态视图引用
  created_at: string;                       // ISO-8601 datetime
  updated_at: string;                       // ISO-8601 datetime
  schema_version: string;                   // 数据版本
}

interface SessionGoal {
  question: string;                         // 分析目标问题
}

interface SessionScope {
  constraints: object | null;               // 限定约束键值对
}

interface SessionLifecycle {
  status: string;                           // "active" | "terminated"
  terminal_reason: string | null;           // 终止原因
  ended_at: string | null;                  // 终止时间
  rollover_from_session_id: string | null;  // 从哪个会话滚动过来
}

interface SessionStateSummary {
  state_view_ref: {
    session_id: string;
    view_type: string;
  };
}
```

**输入示例**：

```json
{
  "goal": "分析Trino查询量趋势与异常"
}
```

**输出示例**：

```json
{
  "data": {
    "session_id": "ses_abc123",
    "goal": { "question": "分析Trino查询量趋势与异常" },
    "scope": { "constraints": null },
    "owner_user": "lichengxiang",
    "lifecycle": { "status": "active", "terminal_reason": null, "ended_at": null, "rollover_from_session_id": null },
    "state_summary": { "state_view_ref": { "session_id": "ses_abc123", "view_type": "default" } },
    "created_at": "2025-03-15T08:00:00Z",
    "updated_at": "2025-03-15T08:00:00Z",
    "schema_version": "0.1"
  },
  "error": null
}
```

---

### 3.2 list_sessions

列出分析会话。

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| status | string \| null | 否 | 按状态过滤，如 `"active"` |
| session_id | string \| null | 否 | 按 session ID 过滤 |
| limit | integer \| null | 否 | 返回数量上限 |
| page_token | string \| null | 否 | 分页 token |

**输出 — SessionListResponse**：

```typescript
interface SessionListResponse {
  items: AnalysisSession[];               // 会话数组，结构同 3.1 AnalysisSession
  next_page_token: string | null;         // 下一页 token
}
```

注意：字段名为 `items`，非 `sessions`；无 `total_count` 字段。

**输入示例**：

```json
{ "status": "active", "limit": 10 }
```

**输出示例**：

```json
{
  "data": {
    "items": [
      {
        "session_id": "ses_abc123",
        "goal": { "question": "分析Trino查询量趋势与异常" },
        "scope": { "constraints": null },
        "owner_user": "lichengxiang",
        "lifecycle": { "status": "active", "terminal_reason": null, "ended_at": null, "rollover_from_session_id": null },
        "state_summary": { "state_view_ref": { "session_id": "ses_abc123", "view_type": "default" } },
        "created_at": "2025-03-15T08:00:00Z",
        "updated_at": "2025-03-15T08:00:00Z",
        "schema_version": "0.1"
      }
    ],
    "next_page_token": null
  },
  "error": null
}
```

---

### 3.3 get_session

读取单个会话根信息（不含 state 和 proposition context 内联）。

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| session_id | string | 是 | 会话ID |

**输出 — AnalysisSession**：结构同 3.1 create_session 输出。

**输入示例**：

```json
{ "session_id": "ses_abc123" }
```

**输出示例**：同 3.1 输出示例。

---

### 3.4 terminate_session

终止分析会话。分析结束后必须显式调用。

**输入参数**：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| session_id | string | 是 | — | 会话ID |
| terminal_reason | string | 否 | `"user_closed"` | 终止原因 |

**输出 — AnalysisSession**：返回完整会话对象（与 create_session 同结构），lifecycle.status 变为 `"terminated"`。

注意：返回的是完整 AnalysisSession 对象，非独立的 TerminateResult。

**输入示例**：

```json
{
  "session_id": "ses_abc123",
  "terminal_reason": "analysis_complete"
}
```

**输出示例**：

```json
{
  "data": {
    "session_id": "ses_abc123",
    "goal": { "question": "分析Trino查询量趋势与异常" },
    "scope": { "constraints": null },
    "owner_user": "lichengxiang",
    "lifecycle": { "status": "terminated", "terminal_reason": "analysis_complete", "ended_at": "2025-03-15T09:30:00Z", "rollover_from_session_id": null },
    "state_summary": { "state_view_ref": { "session_id": "ses_abc123", "view_type": "default" } },
    "created_at": "2025-03-15T08:00:00Z",
    "updated_at": "2025-03-15T09:30:00Z",
    "schema_version": "0.1"
  },
  "error": null
}
```

---

### 3.5 observe

观测指标在指定时间窗口和维度下的值。最基础的 intent，产生 artifact_id 可供后续 compare/decompose/correlate/forecast 引用。

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| session_id | string | 是 | 会话ID |
| metric | string | 是 | 语义指标名称，如 `"query_count"`（不带 `metric.` 前缀） |
| time_scope | McpTimeScope | 是 | 时间范围定义（需为结构化对象，不接受简写字符串） |

可选参数：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| granularity | `"hour"` \| `"day"` \| `"week"` \| `"month"` \| `"quarter"` \| `"year"` \| null | 否 | 时间粒度 |
| dimensions | string[] \| null | 否 | 按维度分组，如 `["cluster", "department"]` |
| filter_expression | object \| null | 否 | 过滤表达式对象（需为结构化对象，不接受 JSON 字符串） |

**输出 — ObserveArtifact**：

返回的 artifact 包含 AOI 合约层的结构化结果。根据参数组合不同，结果形态分为：

- **scalar**：单一数值 `value`
- **time_series**：`points[]` 每项含 `bucket_start`、`value`
- **segmented**：`rows[]` 每项含 `keys`（维度键值对）、`value`

```typescript
interface ObserveArtifact {
  artifact_id: string;                     // 步骤 artifact ID，如 "art_obs_1"，供后续 intent 引用
  result: ScalarObservationResult | TimeSeriesObservationResult | SegmentedObservationResult;
  failure: AnalysisFailure | null;
}

interface ScalarObservationResult {
  value: number | null;
}

interface TimeSeriesObservationResult {
  points: TimeSeriesPoint[];
}

interface TimeSeriesPoint {
  bucket_start: string;                    // ISO-8601 datetime
  value: number | null;
}

interface SegmentedObservationResult {
  rows: SegmentedObservationRow[];
}

interface SegmentedObservationRow {
  item_id: string;
  keys: object;                            // 维度值键值对
  value: number | null;
}

interface AnalysisFailure {
  code: string;
  message: string;
}
```

**输入示例**（按天观测总查询量时间序列）：

```json
{
  "session_id": "ses_abc123",
  "metric": "total_query_count",
  "time_scope": {
    "field": "create_time",
    "start": "2025-03-01",
    "end": "2025-03-08"
  },
  "granularity": "day",
  "dimensions": ["cluster"]
}
```

**输出示例**（时间序列 + 分段）：

```json
{
  "data": {
    "artifact_id": "art_obs_1",
    "result": {
      "rows": [
        { "item_id": "item_0", "keys": { "cluster": "jscs-ai-offline", "bucket_start": "2025-03-01T00:00:00Z" }, "value": 15230 },
        { "item_id": "item_1", "keys": { "cluster": "jscs-ai-online", "bucket_start": "2025-03-01T00:00:00Z" }, "value": 8910 }
      ]
    },
    "failure": null
  },
  "error": null
}
```

---

### 3.6 detect

检测指标时间序列中的异常窗口。

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| session_id | string | 是 | 会话ID |
| metric | string | 是 | 语义指标名称 |
| time_scope | McpTimeScope | 是 | 时间范围定义 |
| granularity | `"hour"` \| `"day"` \| `"week"` \| `"month"` \| `"quarter"` \| `"year"` | 是 | 时间粒度 |

可选参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| filter_expression | object \| null | 否 | null | 过滤表达式对象（需为结构化对象） |
| split_by | string[] \| null | 否 | null | 按维度列表拆分检测，如 `["cluster"]` |
| profile | string \| null | 否 | null | 检测轮廓，如 `"spike_dip"` / `"level_shift"` / `"seasonal_residual"` |
| sensitivity | float \| null | 否 | null | 检测灵敏度数值 |
| limit | integer \| null | 否 | null | 返回异常点数量上限 |

**输出 — DetectArtifact**：

```typescript
interface DetectArtifact {
  artifact_id: string;
  result: AnomalyCandidatesResult;
  failure: AnalysisFailure | null;
}

interface AnomalyCandidatesResult {
  items: AnomalyCandidate[];
}

interface AnomalyCandidate {
  item_id: string;
  bucket_start: string;                    // 异常发生的时间桶，ISO-8601
  value: number | null;                    // 实际观测值
  score: number;                           // 异常偏离评分
  series_keys: object | null;              // split_by 维度的键值对
}
```

**输入示例**：

```json
{
  "session_id": "ses_abc123",
  "metric": "total_query_count",
  "time_scope": {
    "field": "create_time",
    "start": "2025-03-01",
    "end": "2025-03-08"
  },
  "granularity": "day",
  "profile": "spike_dip",
  "sensitivity": 0.8
}
```

**输出示例**：

```json
{
  "data": {
    "artifact_id": "art_detect_1",
    "result": {
      "items": [
        { "item_id": "item_0", "bucket_start": "2025-03-05T00:00:00Z", "value": 45000, "score": 3.2, "series_keys": null }
      ]
    },
    "failure": null
  },
  "error": null
}
```

---

### 3.7 compare

对比两个已完成的 observe artifact 结果。

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| session_id | string | 是 | 会话ID |
| left_artifact_id | string | 是 | 左侧（通常为当前/异常）observe artifact ID |
| right_artifact_id | string | 是 | 右侧（通常为基线）observe artifact ID |

可选参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| compare_type | `"normal"` \| `"yoy"` \| `"mom"` \| `"wow"` \| `"holiday_aligned_yoy"` \| `"weekday_aligned_yoy"` \| `"weekday_aligned_mom"` | 否 | `"normal"` | 对比类型枚举 |

注意：参数为 **字符串 artifact ID**（如 `"art_obs_1"`），非引用对象。compare_type 不同于 mode，支持多种对齐策略。

**输出 — CompareArtifact**：

```typescript
interface CompareArtifact {
  artifact_id: string;
  result: ScalarDeltaResult | TimeSeriesDeltaResult | SegmentedDeltaResult;
  failure: AnalysisFailure | null;
}

interface ScalarDeltaResult {
  left_value: number | null;
  right_value: number | null;
  delta: number | null;
  matched_time_scope: object | null;
}

interface TimeSeriesDeltaResult {
  points: DeltaPoint[];
  matched_time_scope: object | null;
}

interface DeltaPoint {
  bucket_start: string;
  left_value: number | null;
  right_value: number | null;
  delta: number | null;
}

interface SegmentedDeltaResult {
  rows: SegmentedDeltaRow[];
  matched_time_scope: object | null;
}

interface SegmentedDeltaRow {
  item_id: string;
  keys: object;
  left_value: number | null;
  right_value: number | null;
  delta: number | null;
}
```

**输入示例**：

```json
{
  "session_id": "ses_abc123",
  "left_artifact_id": "art_obs_current",
  "right_artifact_id": "art_obs_baseline",
  "compare_type": "normal"
}
```

**输出示例**（分段对比）：

```json
{
  "data": {
    "artifact_id": "art_compare_1",
    "result": {
      "rows": [
        { "item_id": "item_0", "keys": { "cluster": "jscs-ai-offline" }, "left_value": 45000, "right_value": 15230, "delta": 29770 },
        { "item_id": "item_1", "keys": { "cluster": "jscs-ai-online" }, "left_value": 15000, "right_value": 8910, "delta": 6090 }
      ],
      "matched_time_scope": null
    },
    "failure": null
  },
  "error": null
}
```

---

### 3.8 decompose

拆解 compare 产生的差异，按指定维度归因各维度值对总差异的贡献占比。**仅对可加指标（声明了 additive_dimensions 的 SUM 类 metric）有效。**

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| session_id | string | 是 | 会话ID |
| compare_artifact_id | string | 是 | compare artifact ID（字符串，如 `"art_compare_1"`） |
| dimension | string | 是 | 拆解维度名称，如 `"cluster"`、`"department"` |

可选参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| limit | integer \| null | 否 | null | 返回 top N 维度值数量 |

注意：参数为字符串 artifact ID，非 CompareArtifactRef 对象。无 `method` 参数（仅支持 delta_share）。

**输出 — DecomposeArtifact**：

```typescript
interface DecomposeArtifact {
  artifact_id: string;
  result: DeltaDecompositionResult;
  failure: AnalysisFailure | null;
}

interface DeltaDecompositionResult {
  items: DecompositionItem[];
}

interface DecompositionItem {
  item_id: string;
  key: string;                             // 维度值，如 "jscs-ai-offline"
  contribution: number;                    // 该维度值的差值 (left - right)
  share: number;                           // 对总差异的贡献占比（0~1）
}
```

**输入示例**：

```json
{
  "session_id": "ses_abc123",
  "compare_artifact_id": "art_compare_1",
  "dimension": "cluster",
  "limit": 5
}
```

**输出示例**：

```json
{
  "data": {
    "artifact_id": "art_decompose_1",
    "result": {
      "items": [
        { "item_id": "item_0", "key": "jscs-ai-offline", "contribution": 29770, "share": 0.83 },
        { "item_id": "item_1", "key": "jscs-ai-online", "contribution": 6090, "share": 0.17 }
      ]
    },
    "failure": null
  },
  "error": null
}
```

---

### 3.9 attribute

维度归因分析。直接对比两个时间切片，按指定维度拆解差异贡献，无需先做 observe + compare。

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| session_id | string | 是 | 会话ID |
| metric | string | 是 | 语义指标名称 |
| left | McpSliceRef | 是 | 基准切片（通常为基线时段） |
| right | McpSliceRef | 是 | 对比切片（通常为异常/当前时段） |
| dimensions | string[] | 是 | 拆解维度列表，如 `["cluster", "department"]` |

可选参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| decomposition_method | string | 否 | `"delta_share"` | 拆解方法 |
| decomposition_limit | integer | 否 | 5 | 返回 top N 维度值数量 |

**输出 — AttributeArtifact**：

```typescript
interface AttributeArtifact {
  artifact_id: string;
  result: DeltaDecompositionResult;        // 结构同 decompose 输出
  failure: AnalysisFailure | null;
}
```

**输入示例**：

```json
{
  "session_id": "ses_abc123",
  "metric": "total_query_count",
  "left": {
    "time_scope": { "field": "create_time", "start": "2025-02-25", "end": "2025-03-04" }
  },
  "right": {
    "time_scope": { "field": "create_time", "start": "2025-03-04", "end": "2025-03-11" }
  },
  "dimensions": ["cluster"],
  "decomposition_limit": 5
}
```

**输出示例**：

```json
{
  "data": {
    "artifact_id": "art_attribute_1",
    "result": {
      "items": [
        { "item_id": "item_0", "key": "jscs-ai-offline", "contribution": 29770, "share": 0.83 },
        { "item_id": "item_1", "key": "jscs-ai-online", "contribution": 6090, "share": 0.17 }
      ]
    },
    "failure": null
  },
  "error": null
}
```

---

### 3.10 diagnose

综合诊断异常。支持两种模式：auto_detect（自动发现异常并归因）和 explicit_compare（已知双窗口直接归因）。

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| session_id | string | 是 | 会话ID |
| metric | string | 是 | 语义指标名称 |
| candidate_dimensions | string[] | 是 | 候选归因维度列表 |

可选参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| mode | `"auto_detect"` \| `"explicit_compare"` | 否 | `"auto_detect"` | 诊断模式 |
| time_scope | McpTimeScope \| null | 否 | null | auto_detect 模式的时间范围 |
| current | McpSliceRef \| null | 否 | null | explicit_compare 模式的当前切片 |
| baseline | McpSliceRef \| null | 否 | null | explicit_compare 模式的基线切片 |
| baseline_policy | `"previous_adjacent_equal_length"` | 否 | `"previous_adjacent_equal_length"` | 基线策略 |
| granularity | `"hour"` \| `"day"` \| `"week"` \| `"month"` \| null | 否 | null | 时间粒度 |
| scope | ObserveScope \| null | 否 | null | 人口限定 |
| detect_split_by | string \| null | 否 | null | detect 阶段的 split_by 维度 |
| profile | `"auto"` \| `"spike_dip"` \| `"level_shift"` \| `"seasonal_residual"` | 否 | `"auto"` | detect 阶段的轮廓 |
| sensitivity | `"conservative"` \| `"balanced"` \| `"aggressive"` | 否 | `"balanced"` | detect 阶段的灵敏度 |
| patterns | `["point_anomaly"]` \| `["period_shift"]` \| 两者组合 \| null | 否 | null | detect 阶段的模式 |
| decomposition_limit | integer \| null | 否 | 5 | 归因拆解返回的维度值上限 |
| candidate_limit | integer \| null | 否 | null | 候选维度数量上限 |
| followup_limit | integer \| null | 否 | 3 | follow-up 维度数量上限 |

**输出 — DiagnoseArtifact**：

```typescript
interface DiagnoseArtifact {
  artifact_id: string;
  result: object;                          // 综合诊断结果，包含异常检测与归因
  failure: AnalysisFailure | null;
}
```

**输入示例**（auto_detect 模式）：

```json
{
  "session_id": "ses_abc123",
  "metric": "total_query_count",
  "candidate_dimensions": ["cluster", "department"],
  "mode": "auto_detect",
  "time_scope": {
    "field": "create_time",
    "start": "2025-03-01",
    "end": "2025-03-08"
  },
  "granularity": "day",
  "profile": "spike_dip",
  "sensitivity": "balanced"
}
```

**输出示例**（精简）：

```json
{
  "data": {
    "artifact_id": "art_diagnose_1",
    "result": {
      "anomaly_detection": { "items": [{ "item_id": "item_0", "bucket_start": "2025-03-05T00:00:00Z", "value": 45000, "score": 3.2, "series_keys": null }] },
      "attributions": [
        { "dimension": "cluster", "explained_delta_share": 0.83, "rank": 1, "top_contributors": [{ "dimension_value": "jscs-ai-offline", "delta": 29770, "delta_share": 0.83, "direction": "increase" }] }
      ]
    },
    "failure": null
  },
  "error": null
}
```

---

### 3.11 correlate

相关性分析。判断两个 observe artifact 之间的统计相关性。

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| session_id | string | 是 | 会话ID |
| left_artifact_id | string | 是 | 左侧 observe artifact ID |
| right_artifact_id | string | 是 | 右侧 observe artifact ID |

可选参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| method | `"pearson"` \| `"spearman"` \| null | 否 | null | 相关性方法（不支持 "kendall"） |

注意：参数为字符串 artifact ID，非引用对象。无 `min_pairs` 参数。

**输出 — CorrelateArtifact**：

```typescript
interface CorrelateArtifact {
  artifact_id: string;
  result: AssociationResult;
  failure: AnalysisFailure | null;
}

interface AssociationResult {
  coefficient: number;                     // 相关系数，-1 到 1
  p_value: number;                         // 统计显著性 p-value
  n_pairs: integer;                        // 有效配对数量
  matched_time_scope: object | null;
}
```

**输入示例**：

```json
{
  "session_id": "ses_abc123",
  "left_artifact_id": "art_obs_query_count",
  "right_artifact_id": "art_obs_latency",
  "method": "spearman"
}
```

**输出示例**：

```json
{
  "data": {
    "artifact_id": "art_correlate_1",
    "result": {
      "coefficient": 0.72,
      "p_value": 0.003,
      "n_pairs": 7,
      "matched_time_scope": null
    },
    "failure": null
  },
  "error": null
}
```

---

### 3.12 test_intent

定向假设验证。对两个时间切片进行特定假设的统计检验。

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| session_id | string | 是 | 会话ID |
| metric | string | 是 | 语义指标名称 |
| left | McpSliceRef | 是 | 左侧时间切片 |
| right | McpSliceRef | 是 | 右侧时间切片 |
| kind | `"numeric"` \| `"rate"` | 是 | 样本类型枚举 |
| hypothesis | object | 是 | 假设描述对象（需为结构化对象） |

注意：需提供 `metric` 和 `kind` 参数（原文档未列出）。`hypothesis` 需为结构化对象，不接受 JSON 字符串。

**输出 — TestIntentArtifact**：

```typescript
interface TestIntentArtifact {
  artifact_id: string;
  result: HypothesisTestResult;            // 统计检验结果
  failure: AnalysisFailure | null;
}
```

**输入示例**：

```json
{
  "session_id": "ses_abc123",
  "metric": "total_query_count",
  "left": {
    "time_scope": { "field": "create_time", "start": "2025-02-25", "end": "2025-03-04" }
  },
  "right": {
    "time_scope": { "field": "create_time", "start": "2025-03-04", "end": "2025-03-11" }
  },
  "kind": "numeric",
  "hypothesis": { "family": "t_test", "alternative": "greater", "alpha": 0.05 }
}
```

**输出示例**：

```json
{
  "data": {
    "artifact_id": "art_test_1",
    "result": {
      "statistic": 3.45,
      "p_value": 0.002,
      "decision": { "reject_null": true },
      "assumption_notes": null
    },
    "failure": null
  },
  "error": null
}
```

---

### 3.13 forecast

基于历史数据预测指标未来趋势。

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| session_id | string | 是 | 会话ID |
| source_artifact_id | string | 是 | 数据源 artifact ID（observe 步骤产生的 artifact ID） |
| horizon | integer | 是 | 预测步数（向前预测多少个 granularity 单位） |

可选参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| profile | string \| null | 否 | null | 预测轮廓 |

注意：参数为字符串 artifact ID（如 `"art_obs_1"`），非引用对象。无 `interval_level` 参数。

**输出 — ForecastArtifact**：

```typescript
interface ForecastArtifact {
  artifact_id: string;
  result: ForecastSeriesResult;
  failure: AnalysisFailure | null;
}

interface ForecastSeriesResult {
  points: ForecastPoint[];
}

interface ForecastPoint {
  bucket_start: string;                    // 预测时间桶，ISO-8601
  value: number | null;                    // 预测值
  ci_low: number | null;                   // 置信区间下界
  ci_high: number | null;                  // 置信区间上界
}
```

**输入示例**：

```json
{
  "session_id": "ses_abc123",
  "source_artifact_id": "art_obs_1",
  "horizon": 7,
  "profile": "auto"
}
```

**输出示例**：

```json
{
  "data": {
    "artifact_id": "art_forecast_1",
    "result": {
      "points": [
        { "bucket_start": "2025-03-08T00:00:00Z", "value": 16000, "ci_low": 12000, "ci_high": 20000 },
        { "bucket_start": "2025-03-09T00:00:00Z", "value": 15800, "ci_low": 11000, "ci_high": 20600 }
      ]
    },
    "failure": null
  },
  "error": null
}
```

---

### 3.14 get_session_state

读取会话级决策视图（session state）。用于判断当前分析进展和下一步方向。state 不是步骤列表或 artifact 清单；state 为空不等于分析失败。

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| session_id | string | 是 | 会话ID |

可选过滤参数：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| metric | string \| null | 否 | 按指标名称过滤 |
| entity | string \| null | 否 | 按实体过滤 |
| proposition_type | string[] \| null | 否 | 按命题类型过滤 |
| origin_kind | string[] \| null | 否 | 按来源 intent 类型过滤 |
| assessment_presence | string \| null | 否 | 按评估存在性过滤 |
| assessment_status | string[] \| null | 否 | 按评估状态过滤 |
| has_blocking_gaps | boolean \| null | 否 | 是否有阻塞缺口 |
| limit | integer \| null | 否 | 返回数量上限 |
| page_token | string \| null | 否 | 分页 token |

**输出 — SessionStateView**：

```typescript
interface SessionStateView {
  session_id: string;                       // 会话ID
  active_propositions: object[];            // 活跃命题列表
  backing_findings: object[];               // 支撑发现列表
  blocking_gaps: object[];                  // 阻塞缺口列表
  artifact_refs: object[];                  // artifact 引用列表
  focus_subjects: object[];                 // 关注主题列表
  truncation: SessionStateTruncation;       // 截断信息
  schema_version: string;                   // 数据版本
  next_page_token: string | null;           // 下一页 token
}

interface SessionStateTruncation {
  is_truncated: boolean;                    // 是否截断
  returned_count: integer;                  // 实际返回条数
  total_count: integer;                     // 总条数
  sort_key: string;                         // 排序键
  applies_to: string;                       // 截断适用范围
}
```

**输入示例**：

```json
{ "session_id": "ses_abc123" }
```

**输出示例**（精简）：

```json
{
  "data": {
    "session_id": "ses_abc123",
    "active_propositions": [
      { "proposition_id": "prop_abc123", "proposition_type": "anomaly", "origin_kind": "detect", "metric": "total_query_count", "summary": "2025-03-05 查询量异常突增" }
    ],
    "backing_findings": [],
    "blocking_gaps": [],
    "artifact_refs": [{ "artifact_id": "art_detect_1", "step_type": "detect" }],
    "focus_subjects": [{ "metric": "total_query_count", "entity": null }],
    "truncation": { "is_truncated": false, "returned_count": 1, "total_count": 1, "sort_key": "created_at", "applies_to": "propositions" },
    "schema_version": "0.1",
    "next_page_token": null
  },
  "error": null
}
```

---

### 3.15 query_session_state

读取会话 state，支持结构化 slice 过滤。当需要按时间切片等复杂条件过滤时使用。

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| session_id | string | 是 | 会话ID |

可选过滤参数：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| metric | string \| null | 否 | 按指标名称过滤 |
| entity | string \| null | 否 | 按实体过滤 |
| proposition_types | string[] \| null | 否 | 按命题类型过滤 |
| origin_kinds | string[] \| null | 否 | 按来源 intent 类型过滤 |
| assessment_presence | string \| null | 否 | 按评估存在性过滤 |
| assessment_statuses | string[] \| null | 否 | 按评估状态过滤 |
| has_blocking_gaps | boolean \| null | 否 | 是否有阻塞缺口 |
| slice | object \| null | 否 | 切片过滤条件（McpSliceRef 结构） |
| limit | integer \| null | 否 | 返回数量上限 |
| page_token | string \| null | 否 | 分页 token |

注意：`proposition_types` 和 `origin_kinds`（带 s 后缀）与 get_session_state 的 `proposition_type` 和 `origin_kind` 不同。

**输出 — SessionStateView**：结构同 3.15 get_session_state，额外支持 slice 过滤。

**输入示例**：

```json
{
  "session_id": "ses_abc123",
  "proposition_types": ["anomaly"],
  "slice": {
    "time_scope": { "field": "create_time", "start": "2025-03-04", "end": "2025-03-06" }
  }
}
```

**输出示例**：同 3.15 输出示例。

---

### 3.16 get_proposition_context

读取单个 proposition 的局部证据闭包。仅在需要解释某个具体 proposition 时调用，不要批量读取。

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| session_id | string | 是 | 会话ID |
| proposition_id | string | 是 | Proposition ID |

**输出 — PropositionContextView**：

```typescript
interface PropositionContextView {
  proposition: object;                      // 命题详情对象
  seed_entries: object[];                   // 产生此命题的种子条目
  relevant_findings: object[];              // 相关发现列表
  latest_assessment: object | null;         // 最新评估
  blocking_gaps: object[];                  // 阻塞缺口
  non_blocking_gaps: object[];              // 非阻塞缺口
  applied_inference_records: object[];      // 推理记录
  assessment_dependencies: object[];        // 评估依赖
  artifact_refs: object[];                  // artifact 引用列表
  schema_version: string;                   // 数据版本
}
```

**输入示例**：

```json
{
  "session_id": "ses_abc123",
  "proposition_id": "prop_abc123"
}
```

**输出示例**（精简）：

```json
{
  "data": {
    "proposition": { "proposition_id": "prop_abc123", "proposition_type": "anomaly", "description": "2025-03-05 查询量异常突增" },
    "seed_entries": [],
    "relevant_findings": [{ "finding_id": "f_1", "finding_type": "anomaly_candidate", "artifact_id": "art_detect_1" }],
    "latest_assessment": { "assessment_id": "ass_1", "status": "confirmed" },
    "blocking_gaps": [],
    "non_blocking_gaps": [],
    "applied_inference_records": [],
    "assessment_dependencies": [],
    "artifact_refs": [{ "artifact_id": "art_detect_1", "step_type": "detect" }],
    "schema_version": "0.1"
  },
  "error": null
}
```

---

## 四、共享数据结构参考

### 4.1 McpTimeScope

时间范围定义，半开区间 `[start, end)`。

```typescript
interface McpTimeScope {
  field: string;               // OSI dataset 时间字段名（必填），如 "create_time"
  start: string;               // ISO-8601 日期或 datetime（包含，必填），如 "2025-02-01"
  end: string;                 // ISO-8601 日期或 datetime（不包含，必填），如 "2025-02-08"
}
```

验证约束：`start` 必须严格早于 `end`；不接受简写字符串格式。

**示例**：

```json
{ "field": "create_time", "start": "2025-03-01", "end": "2025-03-08" }
```

### 4.2 McpSliceRef

AOI-aligned 切片引用：时间范围 + 可选人口限定。

```typescript
interface McpSliceRef {
  time_scope: McpTimeScope;    // 时间范围（必填）
  scope: ObserveScope | null;  // 人口限定（可选）
}
```

**示例**：

```json
{
  "time_scope": { "field": "create_time", "start": "2025-03-04", "end": "2025-03-11" },
  "scope": { "constraints": { "cluster": "jscs-ai-offline" } }
}
```

### 4.3 ObserveScope

非时间维度的人口限定。

```typescript
interface ObserveScope {
  predicate_ref: string | null; // 上游 observe 步骤引用，如 "step_obs_current"
  constraints: object | null;   // 维度等值约束键值对，如 {"cluster": "jscs-ai-offline"}
}
```

**示例**：

```json
{ "constraints": { "cluster": "jscs-ai-offline", "state": "SUCCEED" } }
```

### 4.4 AnalysisFailure

分析失败时的错误结构。

```typescript
interface AnalysisFailure {
  code: string;                // 错误码
  message: string;             // 错误描述
}
```

**示例**：

```json
{ "code": "INSUFFICIENT_DATA", "message": "Not enough data points for time series analysis (min 7 required, got 2)" }
```

---

## 五、Intent 使用边界速查

| 问题本质 | 应使用的 Intent | 错误做法 |
|---------|----------------|---------|
| 观测当前值 | `observe` | — |
| 发现异常窗口 | `detect` 或 `diagnose(mode="auto_detect")` | 用 observe 后肉眼判断 |
| 已知双窗口做归因 | `diagnose(mode="explicit_compare")` | 两次 observe 后口头比较 |
| 对比两个已完成的 observe | `compare` | 口头比较数值差异 |
| 拆解差异的维度贡献 | `decompose`（需先 compare） | grouped observe |
| 维度归因（直接指定切片） | `attribute` | — |
| 判断相关性 | `correlate` | 肉眼比较趋势线 |
| 显著性/假设验证 | `test_intent` | 只看数值差异 |
| 预测 | `forecast` | — |
| 综合诊断（异常+归因） | `diagnose` | detect + 口头解释 |

**关键限制**：
- `AVG` / `APPROX_PERCENTILE` / 比率类指标为非可加指标，不可使用 `decompose` / `attribute` / `diagnose` 的归因拆解
- 仅声明了 `additive_dimensions` 的 `SUM` 类指标可被分解
- metric/dimension 引用使用语义对象名称，不带 `metric.` / `dimension.` 前缀
- 非可加指标不添加 MARIVO `custom_extension`（`additive_dimensions` 不能为空数组）
- `compare`、`decompose`、`correlate`、`forecast` 使用 artifact ID 字符串引用，非结构化引用对象
- `observe`/`detect` 的 `filter_expression` 和 `test_intent` 的 `hypothesis` 必须为结构化对象，不接受 JSON 字符串
- `correlate` 仅支持 `"pearson"` 和 `"spearman"` 方法，不支持 `"kendall"`
- `decompose` 仅支持 `"delta_share"` 方法，无 method 参数

---

## 六、OSI-Marivo 文档规范要点

语义模型使用 OSI 0.1.1 规范 + MARIVO vendor extension。

**核心结构层级**：

```
OsiDocument
  ├── version: "0.1.1"
  ├── dialects: ["ANSI_SQL"]
  ├── vendors: ["MARIVO"]
  └── semantic_model[]
      ├── name, description, ai_context
      ├── datasets[]
      │   ├── name, source, primary_key, description
      │   ├── fields[]
      │   │   ├── name, expression (DialectExpression)
      │   │   ├── dimension?: { is_time?: boolean }
      │   │   └── description
      │   └── custom_extensions[]: MARIVO datasource_id
      ├── metrics[]
      │   ├── name, expression (DialectExpression)
      │   ├── description
      │   └── custom_extensions[]: MARIVO additive_dimensions (仅可加指标)
      └── relationships[]
          ├── name, from, to, from_columns, to_columns
```

**关键约束**：
- 非可加指标：不添加 `custom_extensions`（`additive_dimensions` 不能为空数组，验证器会拒绝）
- SQL 关键字列：在 `expression` 中使用双引号，如 `"schema"`、`"user"`
- 时间字段：标记 `dimension: { is_time: true }`，对 varchar 时间列使用 `CAST(col AS TIMESTAMP)`
- 验证使用 `input_path`（本地文件方式），不用 inline `document`
- 导入必须经用户明确批准后才调用 `import_osi_semantic_models`
