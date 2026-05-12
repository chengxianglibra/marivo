---
status: draft
created: 2026-05-12
updated: 2026-05-12
---

# Semantic Layer MCP Surface Design

**日期：** 2026-05-12
**状态：** Draft
**范围：** 本地 stdio MCP、server HTTP MCP、HTTP API 共享的 semantic layer 管理 surface；OSI document import/export；private working copy；field 级 CRUD；datasource 自动绑定

---

## 1. 目标

Marivo 需要通过 MCP 暴露给 agent 一组稳定、可操作、与 HTTP API 同构的 semantic layer surface。这个 surface 支持两类核心工作流：

1. **业务域专家建模**：基于 datasource 浏览和 preview 结果，创建、修订、验证、导出符合 `osi-marivo-spec` 的语义层 JSON document。
2. **数据分析人员分析补缺**：agent 从外部 git / 知识库选择最新 OSI document，导入为当前用户 private working copy，在分析过程中补齐缺失的 field / metric / relationship，并在需要沉淀时导出 OSI document 进入外部 review。

本设计的关键目标不是增加一组独立 MCP wrapper，而是统一 semantic application service：

```text
MCP stdio / HTTP MCP / HTTP API
  -> context provider
  -> transport DTO / HTTP model adapter
  -> semantic application service
  -> OSI validation / import merge / export / CRUD / readiness / datasource binding
  -> metadata storage
```

stdio MCP 和 HTTP MCP 的工具 surface 必须一致。HTTP API 可以保留 RESTful route 形态，但语义必须与 MCP 相同。runtime、storage、application 逻辑不能按 transport 分叉。

### 1.1 实现方式：聚焦的 application-service extraction

本设计采用聚焦的 semantic application service extraction，而不是在现有 `SemanticModelV2Service.import_osi_document` 中继续堆逻辑，也不是重建完整 semantic management domain。

目标边界：

| 组件 | 职责 | 不负责 |
|---|---|---|
| `SemanticModelV2Service` 或等价 application facade | 对 HTTP/MCP 暴露稳定方法；解析 current user；编排 import/export/CRUD/readiness | 内联复杂 merge、binding、report 细节 |
| `DatasourceBinder` | 基于当前 context 可访问 datasource，为 import 中的 dataset 选择稳定的可用 datasource | 保存 datasource、读取凭证、绕过权限过滤 |
| `SemanticMergePlanner` | 对 OSI document 做 preflight，产出分层 merge plan；验证空 document、重复名称、引用关系 | 执行 SQL 写入 |
| `SemanticMergeExecutor` | 在一个事务中应用 merge plan，写入 model/dataset/field/metric/relationship，并生成 merge report | 重新解析 transport DTO |
| `OsiDocumentExporter` | 从当前用户 private working copy 组装纯 OSI document | 导出 public/official/shared model 或写文件 |

这些组件可以是独立类、内部 helper 或模块函数，但职责边界必须可测试。实现不应把 private import、datasource binding、child merge、report 生成继续集中在一个大型方法中。

---

## 2. 非目标

- 不在 OSI JSON 中写入 owner、权限、凭证、本地状态、revision、外部版本号或内部审计字段。
- 不由 Marivo 管理外部版本。版本由 git、文件名、知识库元信息或上游发布流程管理。
- 不为旧 public import 语义保留兼容 surface。
- 不允许 agent 或 payload 指定 owner、visibility、mode、datasource_mapping。
- 不在 MCP 层直接暴露生成的 OSI Pydantic 模型作为原始透传参数。MCP 仍保留 agent-friendly DTO，再转换为 canonical OSI/application contract。
- 不在本设计中定义 public/official 发布流程。发布应走独立 admin/publish surface。

---

## 3. 核心对象模型

Semantic layer 的对象层级如下：

```text
semantic_model
  datasets
    fields
  metrics
  relationships
```

语义定义：

| 对象 | 语义 | 生命周期 |
|---|---|---|
| `semantic_model` | 当前用户的语义层工作副本容器 | 顶层 CRUD；import merge 的根节点 |
| `dataset` | 逻辑数据集及物理 grounding | model 下 CRUD；create 时可携带初始 fields |
| `field` | dataset 的子对象 | 独立 CRUD；import 中作为叶子整体替换 |
| `metric` | model 下的指标对象 | 独立 CRUD；通过名称引用 dataset / field |
| `relationship` | model 下的关系对象 | 独立 CRUD；通过名称引用 dataset / field |

`import_osi_document` 和 `export_osi_document` 处理的是符合 `osi-marivo-spec` 的 OSI document。document 只表达可交换的语义层内容，不表达 Marivo 本地执行状态。

---

## 4. Surface

### 4.1 MCP Tools

目标 MCP semantic tools：

```text
import_osi_document(document)
export_osi_document(semantic_model_name? = null)

create_semantic_model / get_semantic_model / list_semantic_models / update_semantic_model / delete_semantic_model
create_dataset / get_dataset / list_datasets / update_dataset / delete_dataset
create_field / get_field / list_fields / update_field / delete_field
create_metric / get_metric / list_metrics / update_metric / delete_metric
create_relationship / get_relationship / list_relationships / update_relationship / delete_relationship
get_semantic_model_readiness
```

工具 schema 不包含以下字段：

- `owner`
- `owner_user`
- `visibility`
- `mode`
- `datasource_mapping`
- `semantic_model_name` on import

`requesting_user` 不应出现在 MCP tool schema 中。读写 identity 均来自 context provider。

### 4.2 HTTP API

现有 HTTP `/semantic-models/import` 的旧含义是导入为 public/latest layer。该语义删除，并替换为新的 `import_osi_document` application 语义：

- operation name / 文档名：`import_osi_document`
- 行为：导入为当前用户 private working copy
- 不再创建或更新 public/official model
- 不再由 payload 指定 owner、mode、datasource_mapping

HTTP route 保留现有 `/semantic-models/import` 路径，避免不必要的 route churn；但 operation name、文档和 service 方法都应表达为 `import_osi_document`。该 route 的服务语义必须只有一套：`import_osi_document(document)`。

HTTP API 同时需要暴露 `export_osi_document` 等价能力。该能力返回 OSI document，不写文件路径。

### 4.3 Import Response

`import_osi_document` 在 HTTP 和 MCP 中都返回 `ImportOsiDocumentReport`，不返回 OSI document。调用方需要读取导入后的 document 时，使用 `get_semantic_model` 或 `export_osi_document`。

这意味着 HTTP `/semantic-models/import` 的 response model 也要从当前 `OSIDocument` 改为 merge report。HTTP 与 MCP 不允许一个返回 document、另一个返回 report。

---

## 5. Ownership 与可见性

统一规则：

1. import 和所有 create 写入当前用户 private working copy。
2. agent 或 payload 不允许指定 owner。
3. CRUD 写操作只能修改当前用户 private working copy。
4. public / official / shared model 可读，但普通 CRUD / import 不直接修改它。
5. 分析过程中 agent 补缺新增的 field / metric / relationship 写入当前用户 private working copy。

读写解析规则：

| 操作 | 解析范围 |
|---|---|
| `get/list_semantic_models` | 当前用户可见模型；private shadow public |
| `get/list_dataset|field|metric|relationship` | 当前用户可见模型；private shadow public |
| `get_semantic_model_readiness` | 当前用户可见模型；private shadow public |
| `create/update/delete_*` | 当前用户 private working copy only |
| `import_osi_document` | 当前用户 private working copy only |
| `export_osi_document` | 当前用户 private working copy only |

当指定 `export_osi_document(semantic_model_name)` 且当前用户没有同名 private working copy 时，返回 `NOT_FOUND_SEMANTIC_MODEL`。即使存在同名 public/official 可读模型，也不回退导出。

---

## 6. Import 语义

`import_osi_document(document)` 固定执行：

```text
merge into current user's private working copy
```

流程：

1. 校验 `document` 符合 `osi-marivo-spec/schema/osi-marivo.schema.json`。
2. 从 document 中读取一个或多个 semantic model。
3. 基于当前用户可访问 datasource 自动匹配每个 dataset grounding。
4. 验证每个 dataset 绑定到的 datasource 对当前用户可访问。
5. 在写事务开始前完成所有 blocking validation。
6. 全部验证通过后，在一个事务中执行分层增量 merge。
7. 返回 merge report。

任何 model / dataset 无法通过校验或绑定时，整个 import 失败，不写入部分结果。

如果 `document.semantic_model` 为空，返回 `VALIDATION`，不允许静默返回空成功 report。

同一个 import document 内的 semantic identity 必须唯一：

- semantic model name 在 document 内唯一。
- dataset name 在同一 semantic model 内唯一。
- field name 在同一 dataset 内唯一。
- metric name 在同一 semantic model 内唯一。
- relationship name 在同一 semantic model 内唯一。

重复名称返回 `VALIDATION`，不允许 last-write-wins，也不依赖 storage constraint 产生 late failure。

### 6.1 分层 Merge 规则

| 节点 | 类型 | 规则 |
|---|---|---|
| `semantic_model` | 非叶子 | 节点不存在则新增；存在则 patch 自身属性，并继续下钻 |
| `dataset` | 非叶子 | 节点不存在则新增；存在则 patch 自身属性，并继续下钻 fields |
| `field` | 叶子 | 节点不存在则新增；存在则整体替换该 field 内容 |
| `metric` | 叶子 | 节点不存在则新增；存在则整体替换该 metric 内容 |
| `relationship` | 叶子 | 节点不存在则新增；存在则整体替换该 relationship 内容 |

import document 中不存在的本地对象一律保留。缺失对象不代表删除。

### 6.2 Dataset 与 Field

`dataset` 是非叶子节点。dataset create 可以携带初始 `fields`，便于一次性创建 dataset skeleton。后续 field 变更必须走：

- `create_field`
- `update_field`
- `delete_field`
- `import_osi_document` 的 field 叶子 merge

`update_dataset` 不负责替换 field 集合，也不把缺失 `fields` 解释为删除。

### 6.3 Merge Report

成功响应返回 merge report。建议结构：

```json
{
  "models": [
    {
      "name": "sales",
      "created": false,
      "updated": true,
      "datasets": {"created": 1, "updated": 2, "unchanged": 0},
      "fields": {"created": 3, "updated": 4, "unchanged": 0},
      "metrics": {"created": 2, "updated": 1, "unchanged": 0},
      "relationships": {"created": 0, "updated": 1, "unchanged": 0},
      "datasource_bindings": [
        {
          "dataset": "orders",
          "datasource_id": "duckdb-local",
          "selection": "first_accessible_candidate"
        }
      ]
    }
  ],
  "errors": []
}
```

失败时不返回 partial merge report。错误 detail 可以列出 blocking dataset、binding candidates 和失败原因，但不能泄漏不可见 datasource 的敏感信息。

---

## 7. Export 语义

`export_osi_document(semantic_model_name? = null)` 固定导出当前用户 private working copy。

规则：

1. 指定 `semantic_model_name`：只导出当前用户 private working copy 中的该模型。
2. 未指定 `semantic_model_name`：导出当前用户所有 private working copy。
3. 不导出 public / official / shared 可读模型。
4. 不返回或写入文件路径。
5. 返回符合 `osi-marivo-spec` 的 JSON document。
6. document 中不包含 owner、visibility、权限状态、本地 datasource 凭证、内部审计字段。

保存到 git、知识库、文件系统或外部 review 流程由 agent / skill 负责，不属于 semantic application service。

---

## 8. Datasource 自动绑定

`import_osi_document` 不接受 `datasource_mapping`。dataset grounding 由 application service 根据 OSI/MARIVO 扩展中的逻辑信息和当前 context 可访问 datasource 自动匹配。

绑定规则：

1. local stdio 使用本地 metadata 的 datasource inventory。
2. server HTTP / HTTP MCP 使用服务端权限过滤后的 datasource inventory。
3. 没有候选 datasource：import 失败。
4. 有候选但没有可访问 datasource：import 失败。
5. 多个可访问候选：按稳定顺序选择第一个可用 datasource。
6. 稳定顺序必须由 service 明确定义，建议按 datasource name、再按 datasource id 排序，避免同一 document 在不同运行中随机绑定。
7. merge report 必须记录实际绑定的 datasource，便于审计 agent 自动选择结果。

绑定发生在写事务前。只要任一 dataset binding 失败，整个 import 不写入。

绑定算法必须 bounded：

- 先使用 datasource registry / metadata 中已有的 cheap metadata 过滤候选。
- 按稳定顺序遍历候选，找到第一个当前用户可访问且能匹配 dataset grounding 的 datasource 后立即停止。
- 单次 import 内缓存 datasource 可访问性和 catalog 检查结果。
- 不允许为了一个 import 对所有 datasource 的所有 schema/table 做无界扫描。
- 绑定失败 detail 可以描述当前 dataset 的失败原因，但不能泄漏不可见 datasource 的敏感信息。

---

## 9. CRUD 语义

### 9.1 Patch Update

所有 `update_*` 都是 patch 语义，只修改显式传入字段。

字段缺失与字段为 `null` 必须区分：

- 字段缺失：不修改。
- 字段显式为 `null`：只有当对应 DTO 明确允许清空该字段时才表示清空。

不能靠 Pydantic `exclude_none=True` 隐式吞掉“清空字段”的语义。需要清空能力的字段应在 DTO 层显式建模。

HTTP/MCP patch DTO 转换必须使用 Pydantic `model_fields_set` 或等价 sentinel 机制来区分“字段缺失”和“字段显式为 null”。允许清空的字段必须在 DTO 和 service 层显式支持；不允许用 `exclude_none=True` 作为通用 patch 转换策略。

### 9.2 Delete

删除必须显式调用 `delete_*`：

- `delete_semantic_model`
- `delete_dataset`
- `delete_field`
- `delete_metric`
- `delete_relationship`

import/update 中缺失对象不代表删除。

### 9.3 Field 级 CRUD

新增 field 级 CRUD 是本设计的核心缺口之一：

```text
create_field(model, dataset, payload)
get_field(model, dataset, name)
list_fields(model, dataset)
update_field(model, dataset, name, payload)
delete_field(model, dataset, name)
```

写操作只作用于当前用户 private working copy。读操作按可见模型解析，private 优先，public fallback。

---

## 10. 错误模型

建议错误码：

| 错误码 | 场景 |
|---|---|
| `NOT_FOUND_SEMANTIC_MODEL` | 指定 private-only export 或写操作目标不存在 |
| `NOT_FOUND_DATASET` | dataset 目标不存在 |
| `NOT_FOUND_FIELD` | field 目标不存在 |
| `NOT_FOUND_METRIC` | metric 目标不存在 |
| `NOT_FOUND_RELATIONSHIP` | relationship 目标不存在 |
| `FORBIDDEN` | 尝试通过普通 CRUD/import 修改 public/official/shared model |
| `VALIDATION` | OSI schema 或对象引用校验失败 |
| `DATASOURCE_BINDING_FAILED` | dataset grounding 无法绑定到任何可用 datasource |
| `DATASET_ACCESS_DENIED` | dataset grounding 指向不可访问 datasource |
| `CONFLICT` | 当前用户 private working copy 内命名冲突 |

MCP 和 HTTP 都通过同一 application error 映射表输出结构化错误。transport 可以不同，错误语义不能不同。

### 10.1 Logging

`import_osi_document` 必须产生结构化日志，至少覆盖：

- import start / success / failure。
- current user、model names、dataset names。
- datasource binding decision，包括 selected datasource id。
- validation failure error code。
- transaction rollback status。

日志不得包含 datasource credentials，也不得泄漏当前用户不可见 datasource 的敏感信息。

---

## 11. Local Stdio 与 Server HTTP MCP

两者 surface 一致，runtime/storage/application 逻辑一致。

```text
MCP transport
  -> context provider
  -> semantic application service
  -> repository/storage
```

差异只在 context provider：

| 模式 | current_user | datasource inventory |
|---|---|---|
| local stdio | 本地默认用户或 workspace identity | 本地 metadata |
| server HTTP MCP | 认证身份 | 服务端权限过滤后的 datasource |

统一部分：

- OSI 校验
- import/export
- private working copy
- 分层 merge
- field/object CRUD
- datasource 自动绑定
- dataset 可访问验证
- readiness
- 错误与 merge report

---

## 12. 工作流

### 12.1 业务域专家建模

1. 使用 `list_datasources` / `get_datasource` / `browse_*` / `preview_table` 确认数据。
2. 使用 `create_semantic_model` 或 `import_osi_document` 建立 private working copy。
3. 使用 `create_dataset` / `create_field` / `update_field` 构建 dataset 与 fields。
4. 使用 `create_metric` / `create_relationship` 构建指标和关系。
5. 使用 `get_semantic_model_readiness` 验证。
6. 使用 `export_osi_document(semantic_model_name)` 导出给外部 review / 上线流程。

### 12.2 数据分析人员分析

1. agent 在外部 git / 知识库判断业务域和最新 OSI document。
2. 使用 `import_osi_document(document)` 导入为当前用户 private working copy。
3. 使用 `get_semantic_model_readiness` 检查可用性。
4. 分析中缺语义时，用 field / metric / relationship CRUD 补缺。
5. 使用 analysis / session tools 继续分析。
6. 需要沉淀时使用 `export_osi_document(semantic_model_name)`，由 agent 提交外部 review。

---

## 13. 实现影响

当前仓库已有以下基础：

- `marivo/transports/mcp/tools/semantic.py` 已有 model / dataset / metric / relationship CRUD。
- `marivo/runtime/semantic/semantic_service.py` 已有 OSI generated model 写入路径和可见性解析。
- `marivo/runtime/semantic/osi_storage.py` 已有 OSI 与 `semantic_models` / `semantic_datasets` / `semantic_fields` / `semantic_metrics` / `semantic_relationships` 的映射。
- HTTP `/semantic-models/import` 当前仍表达旧 public import 语义，需要替换。

本设计是破坏性目标态切换，不考虑旧 public import 数据迁移和兼容性。旧 public import 语义和相关测试按目标态删除或重写；不需要保留旧 public imported model 的可编辑性或自动迁移策略。

需要补齐：

1. semantic application service 中的 `import_osi_document` 私有工作副本 merge。
2. semantic application service 中的 `export_osi_document` private-only 导出。
3. field 级 CRUD。
4. datasource 自动绑定与可访问验证。
5. HTTP import 旧 public 语义删除或替换。
6. MCP import/export tool 暴露。
7. stdio MCP 与 HTTP MCP tool schema parity。

---

## 14. 测试策略

### 14.1 Service Tests

覆盖：

- import 新建 private working copy。
- import merge 到已有 private working copy。
- 非叶子节点 patch 自身属性并继续下钻。
- field / metric / relationship 叶子整体替换。
- import document 缺失的本地对象保留。
- dataset create 携带初始 fields。
- dataset update 不替换 fields 集合。
- export 指定 model 只导出当前用户 private working copy。
- export 未指定 model 只导出当前用户所有 private working copy。
- 指定 export 无 private 时返回 `NOT_FOUND_SEMANTIC_MODEL`。
- datasource 无候选失败。
- datasource 多候选时按稳定顺序选择第一个可访问 datasource。
- 任一 binding 失败时事务不写入。
- mid-merge 写入失败时事务回滚，并断言原 private working copy 的 model/dataset/field/metric/relationship 均保持不变。
- 同一 import document 内重复 model/dataset/field/metric/relationship 名称返回 `VALIDATION`。
- empty `semantic_model` 返回 `VALIDATION`。

### 14.2 HTTP Tests

覆盖：

- `/semantic-models/import` 执行新的 `import_osi_document` private merge 语义。
- 旧 public/latest import 行为不再可达。
- HTTP export 返回纯 OSI document，不含 owner / visibility / credential。
- HTTP CRUD 写操作不能修改 public/official/shared model。

### 14.3 MCP Tests

覆盖：

- inventory 包含 `import_osi_document`、`export_osi_document`、field CRUD。
- tool schema 不包含 owner / visibility / mode / datasource_mapping。
- stdio MCP 与 HTTP MCP tool schema parity。
- MCP import/export 与 HTTP API 调用同一 application service 语义。

### 14.4 Readiness Tests

覆盖：

- 无 private working copy 时，readiness 可读 public fallback。
- 用户创建 private working copy 后，readiness 针对 private shadow。
- import 补缺后 readiness 反映 private working copy 状态。

---

## 15. 验收标准

1. MCP stdio 和 HTTP MCP 暴露相同 semantic layer tool set。
2. HTTP `/semantic-models/import` 不再创建或更新 public/official model，而是执行 `import_osi_document` private working copy merge。
3. `export_osi_document()` 未指定 model 时只导出当前用户 private working copy。
4. `export_osi_document(name)` 找不到当前用户 private model 时返回 `NOT_FOUND_SEMANTIC_MODEL`。
5. field 级 CRUD 在 HTTP/MCP/application service 中可用。
6. import merge 对非叶子节点 patch，对叶子节点整体替换，不删除缺失对象。
7. datasource 自动绑定在多个候选时按稳定顺序选择第一个可访问 datasource，并在 merge report 中记录选择。
8. 普通 CRUD/import 无法修改 public/official/shared model。
9. `make test`、`make typecheck`、`make lint` 通过。
