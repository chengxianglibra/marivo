# Semantic Layer Lifecycle: Agent-First 增强设计

## 1. 背景

Factum semantic layer 采用 immutable published objects 设计：对象发布后不可修改，只能 deprecate 后重新发布新版本。这个设计从数据治理角度是正确的，但从 Agent 操作角度存在明显痛点。

本文档聚焦于 Agent-first 的生命周期增强设计，补充 `semantic-layer-lifecycle-and-readiness.zh.md` 中未覆盖的 Agent 操作体验问题。

## 2. 当前设计分析

### 2.1 Immutable Published Objects 设计

核心约束（`common.py:388-407`）：

```python
if action in {"activate", "publish"}:
    allowed_statuses = ("draft",)      # 只有 draft 才能发布
elif action == "deprecate":
    allowed_statuses = ("published",)  # 只有 published 才能 deprecate
```

`update_typed_entity` 调用 `action="activate"` 检查，意味着 **只有 draft 状态才能修改**。

### 2.2 设计合理性

Immutable published objects 是经典的数据治理模式，有以下优点：

| 优点 | 说明 |
|------|------|
| **引用稳定性** | 发布后的对象被其他对象引用，修改会导致下游语义悄悄变化 |
| **审计可追溯** | 每个 published version 是固定锚点，可追溯历史定义 |
| **版本简化** | 不需要复杂的 version semantic + compatibility check + migration path |
| **消费者确定性** | 下游系统可安全依赖 published 对象，不会突然行为变化 |

### 2.3 设计代价

| 代价 | 说明 |
|------|------|
| **Intent-to-action 拆解** | 人类说"修改 metric X"是 1 个 intent，Agent 需拆成 4 个 action |
| **引用断裂** | deprecated 后下游引用者需手动迁移 |
| **对象爆炸** | 频繁修改 → 大量废弃对象 → catalog 噪音 |
| **无替代链** | 系统不记录"新对象是旧对象的替代" |

## 3. 依赖关系处理现状

### 3.1 发布时依赖验证（乐观检查）

发布对象时验证所有依赖必须是 `published` 状态：

```python
def _validate_published_entity_contract_refs(interface_contract):
    if interface_contract.get("primary_time_ref"):
        self._validate_published_time_ref(...)  # 必须存在且 published
```

**语义**：发布时依赖必须已发布（先发布 entity，才能发布依赖它的 metric）。

### 3.2 Readiness 评估时的依赖检查（持续检查）

```python
if lifecycle_status == "active":
    for dependency_ref in _metric_dependency_refs(header, payload):
        dependency = context.load_dependency_snapshot(dependency_ref)
        if dependency is None or derive_lifecycle_status(dependency.status) != "active":
            blockers.append(_blocker(
                code="METRIC_DEPENDENCY_INACTIVE",
                message="Metric dependency must exist and be active before the metric is ready.",
            ))
```

**语义**：依赖被 deprecate → Metric 变成 `readiness_status="not_ready"`，有 blocker。

### 3.3 无 Cascade Deprecation

Entity B deprecated → Metric A **不会自动 deprecated**，只是变成 `not_ready`。

`deprecate_entity` 不查找下游依赖者、不 cascade、不通知。

## 4. Agent 操作痛点

### 4.1 痛点 1：状态语义混乱

Entity B deprecated → Metric A `readiness_status=not_ready`，但 `lifecycle_status` 仍是 `active`。

Agent 需理解两层状态的组合语义：
- `lifecycle_status=active` ≠ "可用"
- `readiness_status=not_ready` = "依赖断裂"

### 4.2 痛点 2：无主动通知

B deprecated 时，A 的 owner 不收到通知。A 只在下次 readiness 评估时被发现 `not_ready`。

### 4.3 痛点 3：无下游追踪

没有反向依赖索引。Agent 无法查询"谁依赖了这个 entity"。迁移时需手动扫描所有 metrics/processes/bindings。

### 4.4 痛点 4：修复路径不明确

A 因 B deprecated 变成 `not_ready`，Agent 不知道：
- B 是否有替代版本？
- 是否需要等待 B 重新发布？
- 是否需要自己创建替代 entity？

### 4.5 痛点 5：Intent 拆解成本高

"修改 metric X 的聚合函数" → Agent 需执行：

```
1. GET /semantic/metrics/{metric_id} → 获取当前定义
2. POST /semantic/metrics/{metric_id}/deprecate → deprecated 旧版本
3. POST /semantic/metrics → 创建新 draft（复制旧定义 + 修改）
4. POST /semantic/metrics/{new_id}/activate → 发布新版本
5. POST /semantic/bindings → 迁移 binding（指向新 metric）
6. POST /compiler/compatibility-profiles → 迁移 profile（如有）
```

6 步操作，且无原子性保证，中间状态可能导致短暂不可用。

## 5. 增强设计方案

### 5.1 方案 A：高层 Intent Facade

提供单步"修改语义对象"操作，内部实现 deprecate→create→publish→migrate。

**API 设计**：

```python
# Agent 只需要调用这一个
semantic_service.modify_published_entity(
    entity_ref="entity.customer",
    modifications={
        "display_name": "Customer (v2)",
        "grain": ["customer_id", "region"],
    },
    migrate_downstream=True,  # 可选：自动迁移下游引用
)
# 内部自动：
# 1. deprecate old
# 2. create draft with modifications
# 3. populate draft
# 4. activate draft
# 5. 更新 successor_ref
# 6. （可选）迁移下游 binding/profile 引用
# 返回新 ref
```

**优点**：
- Agent action space 简化
- Intent 表达不需要拆解
- 操作可原子化（事务保证）

**实现要点**：
- 新增 `POST /semantic/entities/{id}/modify` 路由
- Service 层封装 `modify_published_*` 方法
- 返回结构：`{"deprecated_ref", "new_ref", "affected_dependents"}`

### 5.2 方案 B：替代链追踪

系统记录 deprecated→新 published 的替代关系。

**DDL 增强**：

```sql
-- 所有 semantic 表增加 successor_ref 字段
ALTER TABLE semantic_entity_contracts ADD COLUMN successor_ref TEXT;
ALTER TABLE semantic_metric_contracts ADD COLUMN successor_ref TEXT;
ALTER TABLE semantic_process_objects ADD COLUMN successor_ref TEXT;
-- ...

-- deprecated 对象携带 successor
deprecated_entity = {
    "status": "deprecated",
    "successor_ref": "entity.customer_v2",
    "deprecated_at": "2026-04-13T...",
}
```

**API 增强**：

```python
# 查询替代链
def get_successor_chain(ref: str) -> dict:
    """返回 {current, successor, chain_depth}"""
    ...

# resolve 时自动跟随替代链（可选）
def resolve_with_successor(ref: str, follow_chain: bool = True) -> str:
    """metric.customer → metric.customer_v2（如果 deprecated）"""
    ...
```

**Agent 可用操作**：
- 查询"X 的最新 active 版本是什么"（自动 resolve）
- 批量迁移下游引用
- 不需要自己维护"替代知识"

### 5.3 方案 C：下游依赖索引

建立反向依赖图，支持查询下游。

**DDL**：

```sql
CREATE TABLE semantic_dependencies (
    subject_ref TEXT NOT NULL,        -- 依赖者（如 metric.xxx）
    dependency_ref TEXT NOT NULL,     -- 被依赖者（如 entity.xxx）
    dependency_kind TEXT NOT NULL,    -- entity_binding / time_anchor / dimension / process
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (subject_ref, dependency_ref)
);

CREATE INDEX idx_semantic_dependencies_dependency
ON semantic_dependencies(dependency_ref);  -- 反向查询关键
```

**依赖 kind 枚举**：
- `entity_binding`: metric/process binding entity
- `time_anchor`: metric/process 引用 time semantic
- `dimension_descriptor`: entity 的 stable descriptor
- `process_requirement`: metric 的 process requirement
- `binding_subject`: binding 绑定的 metric/process/entity

**API**：

```python
def list_dependents(ref: str) -> list[dict]:
    """查询谁依赖了这个对象"""
    return metadata.query_rows(
        "SELECT * FROM semantic_dependencies WHERE dependency_ref = ?",
        [ref],
    )

def list_dependencies(ref: str) -> list[dict]:
    """查询这个对象依赖谁"""
    return metadata.query_rows(
        "SELECT * FROM semantic_dependencies WHERE subject_ref = ?",
        [ref],
    )
```

**Deprecate 返回下游列表**：

```python
def deprecate_entity(entity_contract_id):
    # deprecated entity
    self._deprecate_record(...)
    # 返回下游影响
    dependents = self.list_dependents(entity_ref)
    return {
        "deprecated_entity": self.get_typed_entity(entity_contract_id),
        "affected_dependents": dependents,
    }
```

### 5.4 方案 D：Deprecate 增强响应

当前 `deprecate_*` 返回只包含被 deprecated 对象本身。

**增强返回结构**：

```json
{
    "deprecated_object": {
        "status": "deprecated",
        "successor_ref": null,  // 或已有替代
        ...
    },
    "affected_dependents": [
        {
            "subject_ref": "metric.watch_time",
            "dependency_kind": "entity_binding",
            "current_readiness": "not_ready",
            "blocker_code": "METRIC_DEPENDENCY_INACTIVE"
        },
        {
            "subject_ref": "binding.customer_metric",
            "dependency_kind": "binding_subject",
            "current_readiness": "not_ready",
            "blocker_code": "BINDING_SUBJECT_DEPRECATED"
        }
    ],
    "migration_suggestions": [
        {
            "action": "update_binding_subject",
            "binding_ref": "binding.customer_metric",
            "suggested_new_subject": "entity.customer_v2"  // 如果有 successor
        }
    ]
}
```

**Agent 决策支持**：
- 立即看到下游影响范围
- 规划迁移策略（批量更新引用？等待替代版本？）
- 有替代链时，可自动迁移

## 6. 推荐实施方案

### 6.1 Phase 1：下游依赖索引（基础）

**优先级**：最高。是所有后续增强的基础。

**工作量**：
- DDL：1 表 + 1 索引
- 服务层：`_record_dependencies` / `list_dependents` / `list_dependencies`
- 触发点：`activate_*` 时写入依赖关系

**收益**：
- Agent 可查询下游
- deprecate 返回下游列表
- 目录详情页显示 dependents

### 6.2 Phase 2：替代链追踪

**优先级**：高。让迁移有明确路径。

**工作量**：
- DDL：所有 semantic 表增加 `successor_ref`
- API：`get_successor_chain` / `resolve_with_successor`
- 触发点：`modify_published_*` 时写入 successor

**收益**：
- Agent 可自动 resolve 到最新版本
- 迁移策略明确

### 6.3 Phase 3：高层 Intent Facade

**优先级**：中。简化 Agent action space。

**工作量**：
- 新路由：`POST /semantic/{type}/{id}/modify`
- Service：`modify_published_*` 封装方法
- 事务保证：多步操作原子化

**收益**：
- Agent intent → 1 action
- 减少中间状态风险

### 6.4 Phase 4：Deprecate 增强响应

**优先级**：低。在 Phase 1-2 基础上自然实现。

**工作量**：
- 修改 `deprecate_*` 返回结构
- 增加 `migration_suggestions` 计算

**收益**：
- Agent 决策信息完整

## 7. API 变更清单

### 7.1 新增 API

| 路由 | 方法 | 说明 |
|------|------|------|
| `/semantic/dependencies/{ref}` | GET | 查询依赖关系 |
| `/semantic/dependents/{ref}` | GET | 查询下游依赖者 |
| `/semantic/{type}/{id}/modify` | POST | 高层修改 facade |
| `/semantic/successor-chain/{ref}` | GET | 查询替代链 |

### 7.2 增强 API 返回

| 路由 | 增强字段 |
|------|----------|
| `/semantic/{type}/{id}/deprecate` | `affected_dependents`, `migration_suggestions` |
| `/semantic/{type}` (list) | 每对象增加 `dependency_count`, `dependent_count` |
| `/semantic/{type}/{id}` (detail) | `dependencies`, `dependents`, `successor_ref` |

## 8. 与现有设计的关系

### 8.1 与 Lifecycle/Readiness 分离设计的关系

本增强设计 **不改变** `semantic-layer-lifecycle-and-readiness.zh.md` 提出的 lifecycle/readiness 分离模型。

- Lifecycle（draft/active/deprecated）保持不变
- Readiness（not_ready/ready/stale）保持不变
- 只增加 Agent 操作所需的 **上下游信息**

### 8.2 与 Immutable Published Objects 的关系

本增强设计 **保持** immutable published objects 的核心约束。

- 发布后不可修改
- 只能 deprecate → 重新发布

增强的是 **操作体验**，而非 **语义模型**：
- 替代链让 Agent 知道"新版本是谁"
- 下游索引让 Agent 知道"影响谁"
- Intent facade 让 Agent 不需手动拆解

### 8.3 与 Readiness Blocker 的关系

下游索引与 readiness blocker **协同**：

- `METRIC_DEPENDENCY_INACTIVE` blocker 已指明 `dependency_ref`
- 下游索引反向查询，让 Agent 看到"我 deprecated 影响了谁"

两者互补：
- Blocker：上游 → 下游视角（"我依赖的这个对象怎么了"）
- Dependents：下游 → 上游视角（"我 deprecated 影响了谁"）

## 9. Agent 操作流程对比

### 9.1 当前流程：修改 Metric

```
Agent intent: "修改 metric.customer_count 的聚合函数为 sum"

Step 1: GET /semantic/metrics/metric.customer_count
Step 2: POST /semantic/metrics/metric.customer_count/deprecate
        → 返回 deprecated metric（无下游信息）
Step 3: Agent 手动搜索受影响对象：
        - GET /semantic/bindings?status=published（扫描所有 binding）
        - GET /compiler/compatibility-profiles（扫描所有 profile）
        - 人工判断哪些引用了 customer_count
Step 4: POST /semantic/metrics（创建新 draft）
Step 5: POST /semantic/metrics/{new_id}/activate
Step 6: Agent 手动迁移 binding：
        - POST /semantic/bindings/{binding_id}/deprecate
        - POST /semantic/bindings（创建指向新 metric 的 binding）
Step 7: Agent 手动迁移 profile（如有）

总计：7+ 步，中间状态可能短暂不可用，无原子性保证
```

### 9.2 增强后流程：修改 Metric

```
Agent intent: "修改 metric.customer_count 的聚合函数为 sum"

Step 1: POST /semantic/metrics/metric.customer_count/modify
        payload: {"modifications": {"aggregation": "sum"}, "migrate_downstream": true}
        → 内部原子执行：
           - deprecate old
           - create draft
           - activate new
           - 记录 successor_ref
           - 迁移 binding/profile（可选）
        → 返回：
           {
             "deprecated_ref": "metric.customer_count",
             "new_ref": "metric.customer_count_v2",
             "affected_dependents": [
               {"subject_ref": "binding.customer_count_default", "migration_status": "migrated"},
               {"subject_ref": "profile.customer_count_inferential", "migration_status": "pending"}
             ]
           }

Step 2: Agent 检查 `migration_status: "pending"` 的对象，决定是否需要额外操作

总计：1-2 步，原子性保证，状态完整
```

## 10. 设计结论

**Immutable 设计本身是对的**——给 Agent 提供确定性是核心价值。

问题在于缺少配套的：
- **Intent 封装**：高层操作 facade
- **替代链追踪**：deprecated→新 published 的显式关系
- **下游依赖索引**：反向查询影响范围

这三个增强到位后，Agent 只需要表达一个 intent："修改 metric X"，系统内部处理多步流程 + 下游迁移 + 返回新 ref。

比 mutable+versioning 设计更简洁、更不容易出错——Agent 不需要理解版本语义 + 兼容性规则 + 依赖迁移策略。

## 11. 后续任务清单

见 `semantic-layer-agent-lifecycle-enhancement-task-list.zh.md`（待创建）。

主要任务：
1. [Phase 1] 下游依赖索引 DDL + 服务层
2. [Phase 2] 替代链追踪 DDL + API
3. [Phase 3] 高层 modify facade API
4. [Phase 4] deprecate 增强返回结构
5. [Phase 5] 目录详情页展示 dependents
6. [Phase 6] Readiness blocker 增加 downstream 视角
