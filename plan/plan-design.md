# Factum Plan 设计文档

> 讨论日期：2026-03-27
> 版本：v1.0

## 1. 概述

Plan 是 Factum 的可选编排工具，用于提前规划多步骤分析流程。本文档描述 Plan 的设计目标、能力边界、执行模型和实现方案。

---

## 2. 设计目标

### 2.1 Plan 存在的价值

Plan 解决逐步探索模式的以下问题：

| 问题 | 无 Plan（逐步探索） | 有 Plan（提前规划） |
|------|---------------------|---------------------|
| **执行效率** | 每步串行，无法并行 | 自动识别无依赖步骤并行执行 |
| **成本可控** | 无法提前估算 | 执行前估算，超预算拒绝 |
| **用户透明** | 看不到"接下来要做什么" | 可预览完整分析流程 |
| **流程复用** | 每次重新决策 | 相同问题直接套用 Plan |
| **失败处理** | 单步失败需手动重试 | 整体回滚/重试 |

### 2.2 Plan 对 Agent 的价值

```python
# Agent 无 Plan 的工作模式
loop:
    1. 看当前证据
    2. 决定下一步做什么
    3. 调用 1 个 step
    4. 等结果
    5. 回到步骤 1

# Agent 有 Plan 的工作模式
1. 理解用户意图
2. 生成完整的分析计划（DAG）
3. 提交给 Factum 执行
4. Factum 按 DAG 并行/串行执行
5. 拿到所有结果
```

**核心优势**：
- Agent 可以"一次性思考"，生成完整流程
- Factum 负责高效执行（并行、重试、成本控制）
- 用户可以预览和审批分析计划

---

## 3. 能力边界

### 3.1 Plan 应该支持的能力（优先级排序）

#### P0：DAG 依赖 + 并行执行

```python
Plan = {
    "steps": [
        {"id": "s1", "type": "observe", "params": {"metric": "GMV"}},
        {"id": "s2", "type": "observe", "params": {"metric": "DAU"}},
        {"id": "s3", "type": "synthesize", "depends_on": ["s1", "s2"]}
    ]
}

# 执行时：s1 和 s2 并行执行，s3 等待 s1/s2 完成
```

**实现要点**：
- 拓扑排序识别执行顺序
- 无依赖的步骤并行调度
- 等待所有前置步骤完成后再执行后续步骤

#### P0：成本估算 + 预算控制

```python
Plan = {
    "budget": {
        "max_duration_sec": 30,      # 最大执行时间
        "max_scan_rows": 10000000    # 最大扫描行数
    },
    "steps": [...]
}

# 执行前
POST /plans/{id}/estimate_cost
→ {"estimated_duration_sec": 25, "estimated_scan_rows": 8000000}

# 执行时
POST /plans/{id}/execute
→ 如果预估超预算，拒绝执行并返回警告
```

**实现要点**：
- 基于表行数估算扫描成本
- 基于历史执行时间估算时长
- 执行前检查预算，超预算拒绝

#### P1：动态参数（$ref 引用前序结果）

```python
Plan = {
    "steps": [
        {"id": "s1", "type": "decompose", "params": {...}},
        {
            "id": "s2",
            "type": "correlate",
            "params": {
                "metric": "GMV",
                "metric_b": "$s1.result.top_drivers[0].suggested_upstream"  # 动态参数
            },
            "depends_on": ["s1"]
        }
    ]
}
```

**实现要点**：
- 参数模板语法：`$step_id.path.to.value`
- 从前序步骤结果中提取值（JSONPath）
- 参数验证：提取失败时的错误处理

#### P1：失败重试（per-step）

```python
Plan = {
    "retry_policy": {
        "max_retries": 3,
        "backoff": "exponential"  # 1s, 2s, 4s
    },
    "steps": [
        {
            "id": "s1",
            "type": "observe",
            "params": {...},
            "retry": {"max_retries": 2}  # per-step 覆盖全局策略
        }
    ]
}
```

**实现要点**：
- 全局重试策略 + per-step 覆盖
- 指数退避（exponential backoff）
- 区分可重试错误（网络超时）和不可重试错误（参数错误）

#### P2：Plan 模板化

```python
# 注册 template
POST /plans/templates
{
    "name": "gmv_drop_investigation",
    "params": ["metric", "time_range"],  # 参数化
    "steps": [
        {"type": "diagnose", "params": {"metric": "$metric", ...}},
        {"type": "correlate", "params": {...}},
        {"type": "synthesize"}
    ]
}

# 从 template 实例化
POST /plans/from_template
{
    "template": "gmv_drop_investigation",
    "params": {"metric": "GMV", "time_range": "last_7d"}
}
→ plan_id = "plan_xyz"
```

**实现要点**：
- Plan template 存储（metadata store）
- 参数占位符替换（`$param`）
- Template 版本管理

### 3.2 Plan 不应该支持的能力

#### ✗ 条件分支（if-else）

```python
# 不支持
Plan = {
    "steps": [
        {"id": "s1", "type": "detect", "params": {...}},
        {
            "id": "s2",
            "type": "attribute",
            "condition": "s1.result.anomaly_count > 0",  # ✗ 不支持
            "depends_on": ["s1"]
        }
    ]
}
```

**原因**：
- 条件分支属于"执行中决策"，应该由 Agent 处理
- 引入条件会让 Plan 变成编程语言，复杂度爆炸
- Agent 可以在生成 plan 时做决策（如果需要条件，生成两个不同的 plan）

**替代方案**：Agent 先执行 detect，看结果后决定是否生成包含 attribute 的 plan

#### ✗ 循环（for-each）

```python
# 不支持
Plan = {
    "steps": [
        {"id": "s1", "type": "decompose", "params": {...}},
        {
            "id": "s2",
            "type": "correlate",
            "for_each": "$s1.result.top_drivers[:3]",  # ✗ 不支持
            "params": {"metric_b": "$item.related_metric"}
        }
    ]
}
```

**原因**：
- 循环次数依赖前序结果，无法提前确定 DAG 结构
- 循环结果的聚合语义不明确（3 次执行的结果如何组织）

**替代方案**：Agent 在生成 plan 时展开循环

```python
# Agent 看到 decompose 结果后，生成包含 3 个 correlate 的 plan
Plan = {
    "steps": [
        {"id": "s1", "type": "decompose", "params": {...}},
        {"id": "s2", "type": "correlate", "params": {"metric_b": "ad_spend"}, "depends_on": ["s1"]},
        {"id": "s3", "type": "correlate", "params": {"metric_b": "user_count"}, "depends_on": ["s1"]},
        {"id": "s4", "type": "correlate", "params": {"metric_b": "conversion_rate"}, "depends_on": ["s1"]}
    ]
}
```

---

## 4. 数据结构

### 4.1 Plan 结构

```python
Plan = {
    "plan_id": str,                  # 唯一标识
    "session_id": str,               # 所属 session
    "name": str | None,              # 可选的 plan 名称
    "description": str | None,       # 可选的描述

    # 预算控制
    "budget": {
        "max_duration_sec": int | None,
        "max_scan_rows": int | None,
        "max_cost_usd": float | None
    } | None,

    # 重试策略
    "retry_policy": {
        "max_retries": int,          # 默认 0（不重试）
        "backoff": "constant" | "linear" | "exponential",
        "initial_delay_ms": int      # 默认 1000
    } | None,

    # 失败处理
    "continue_on_failure": bool,     # 默认 false（某步失败则整体失败）

    # 步骤定义
    "steps": [PlanStep],

    # 执行状态
    "status": "draft" | "validated" | "approved" | "executing" | "completed" | "failed",
    "execution": {
        "started_at": datetime | None,
        "completed_at": datetime | None,
        "step_status": {
            "s1": "completed",
            "s2": "executing",
            "s3": "pending"
        },
        "actual_cost": {
            "duration_sec": float,
            "scan_rows": int
        }
    } | None,

    "created_at": datetime,
    "updated_at": datetime
}
```

### 4.2 PlanStep 结构

```python
PlanStep = {
    "id": str,                       # 步骤标识（如 "s1", "s2"）
    "type": str,                     # 原子意图、派生意图、synthesize
    "params": Dict[str, Any],        # 参数（支持 $ref 动态引用）
    "depends_on": [str],             # 依赖的前序步骤 id

    # per-step 重试策略（覆盖全局）
    "retry": {
        "max_retries": int,
        "backoff": str,
        "initial_delay_ms": int
    } | None,

    # per-step 超时（覆盖全局）
    "timeout_sec": int | None
}
```

### 4.3 动态参数语法

```python
# $ref 语法：$step_id.path.to.value
"params": {
    "metric_b": "$s1.result.top_drivers[0].suggested_upstream"
}

# 支持的路径操作
$s1.result                          # 访问对象字段
$s1.result.top_drivers[0]           # 访问数组元素
$s1.result.top_drivers[0].name      # 链式访问
$s1.result.top_drivers[:3]          # 数组切片（返回列表）

# 错误处理
- 如果路径不存在，step 执行失败
- 如果类型不匹配（如期望 string 但得到 int），step 执行失败
```

---

## 5. 执行模型

### 5.1 Plan 生命周期

```
draft → validated → approved → executing → completed/failed
  ↓         ↓          ↓           ↓
创建     验证      审批       执行
```

#### 状态转换

```python
# 1. 创建 plan（draft）
POST /sessions/{id}/plans
{"steps": [...]}
→ status = "draft"

# 2. 验证 plan（validated）
POST /plans/{id}/validate
→ 检查：step 类型有效、依赖无环、参数完整
→ status = "validated"

# 3. 估算成本（可选）
POST /plans/{id}/estimate_cost
→ 返回预估成本，不改变状态

# 4. 审批 plan（approved，可选）
POST /plans/{id}/approve
→ status = "approved"

# 5. 执行 plan（executing → completed/failed）
POST /plans/{id}/execute
→ status = "executing"
→ 按 DAG 执行所有 step
→ status = "completed" | "failed"
```

### 5.2 执行算法

```python
def execute_plan(plan: Plan):
    # 1. 拓扑排序
    dag = build_dag(plan.steps)
    execution_order = topological_sort(dag)

    # 2. 初始化状态
    step_status = {s.id: "pending" for s in plan.steps}
    step_results = {}

    # 3. 按层级执行
    for level in execution_order:
        # 同一层级的步骤并行执行
        futures = []
        for step_id in level:
            step = get_step(step_id)

            # 解析动态参数
            params = resolve_params(step.params, step_results)

            # 提交执行
            future = executor.submit(execute_step, step.type, params)
            futures.append((step_id, future))

        # 等待当前层级所有步骤完成
        for step_id, future in futures:
            try:
                result = future.result(timeout=step.timeout_sec)
                step_status[step_id] = "completed"
                step_results[step_id] = result
            except Exception as e:
                step_status[step_id] = "failed"
                if not plan.continue_on_failure:
                    raise PlanExecutionError(f"Step {step_id} failed: {e}")
                # 如果 continue_on_failure=True，继续执行不依赖此步骤的后续步骤

    return step_results
```

### 5.3 并行执行示例

```python
Plan = {
    "steps": [
        {"id": "s1", "type": "observe", "params": {"metric": "GMV"}},
        {"id": "s2", "type": "observe", "params": {"metric": "DAU"}},
        {"id": "s3", "type": "observe", "params": {"metric": "revenue"}},
        {"id": "s4", "type": "compare", "params": {...}, "depends_on": ["s1"]},
        {"id": "s5", "type": "correlate", "params": {...}, "depends_on": ["s1", "s2"]},
        {"id": "s6", "type": "synthesize", "depends_on": ["s4", "s5"]}
    ]
}

# 执行顺序（按层级）
Level 0: [s1, s2, s3]  # 并行执行
Level 1: [s4, s5]      # 并行执行（等待 Level 0 完成）
Level 2: [s6]          # 等待 Level 1 完成
```

### 5.4 动态参数解析

```python
def resolve_params(params: Dict, step_results: Dict) -> Dict:
    """解析参数中的 $ref 引用"""
    resolved = {}
    for key, value in params.items():
        if isinstance(value, str) and value.startswith("$"):
            # 解析 $ref
            ref_path = value[1:]  # 去掉 $
            resolved[key] = extract_value(step_results, ref_path)
        elif isinstance(value, dict):
            resolved[key] = resolve_params(value, step_results)
        elif isinstance(value, list):
            resolved[key] = [resolve_params(v, step_results) if isinstance(v, dict) else v for v in value]
        else:
            resolved[key] = value
    return resolved

def extract_value(step_results: Dict, path: str) -> Any:
    """从 step_results 中提取值

    path 格式：step_id.field.subfield[index]
    示例：s1.result.top_drivers[0].name
    """
    parts = parse_path(path)  # ["s1", "result", "top_drivers", 0, "name"]
    value = step_results
    for part in parts:
        if isinstance(part, int):
            value = value[part]
        else:
            value = value[part]
    return value
```

---

## 6. 成本估算

### 6.1 估算维度

```python
CostEstimate = {
    "duration_sec": float,           # 预估执行时间
    "scan_rows": int,                # 预估扫描行数
    "cost_usd": float | None,        # 预估费用（如果有计费）
    "breakdown": [
        {
            "step_id": "s1",
            "duration_sec": 5.2,
            "scan_rows": 1000000
        },
        ...
    ]
}
```

### 6.2 估算算法

```python
def estimate_cost(plan: Plan) -> CostEstimate:
    """估算 plan 的执行成本"""
    breakdown = []

    for step in plan.steps:
        # 1. 估算扫描行数（基于表统计信息）
        scan_rows = estimate_scan_rows(step)

        # 2. 估算执行时间（基于历史数据）
        duration_sec = estimate_duration(step, scan_rows)

        breakdown.append({
            "step_id": step.id,
            "duration_sec": duration_sec,
            "scan_rows": scan_rows
        })

    # 3. 考虑并行执行（关键路径）
    dag = build_dag(plan.steps)
    critical_path_duration = calculate_critical_path(dag, breakdown)

    return CostEstimate(
        duration_sec=critical_path_duration,
        scan_rows=sum(b["scan_rows"] for b in breakdown),
        breakdown=breakdown
    )

def estimate_scan_rows(step: PlanStep) -> int:
    """估算步骤的扫描行数"""
    # 从 semantic layer 获取 metric 对应的表
    table = resolve_metric_table(step.params["metric"])

    # 获取表的行数统计
    table_stats = get_table_stats(table)

    # 根据 filters 估算过滤后的行数
    selectivity = estimate_selectivity(step.params.get("filters"))

    return int(table_stats.row_count * selectivity)

def estimate_duration(step: PlanStep, scan_rows: int) -> float:
    """估算步骤的执行时间"""
    # 基于历史执行时间（相同 step 类型 + 相似扫描行数）
    historical_avg = get_historical_avg_duration(step.type, scan_rows)

    # 考虑引擎性能（DuckDB vs Trino）
    engine = resolve_engine(step.params["metric"])
    engine_factor = get_engine_performance_factor(engine)

    return historical_avg * engine_factor
```

### 6.3 预算检查

```python
def check_budget(plan: Plan, estimate: CostEstimate) -> BudgetCheckResult:
    """检查预估成本是否超预算"""
    if not plan.budget:
        return BudgetCheckResult(ok=True)

    violations = []

    if plan.budget.max_duration_sec and estimate.duration_sec > plan.budget.max_duration_sec:
        violations.append(f"预估时间 {estimate.duration_sec}s 超过预算 {plan.budget.max_duration_sec}s")

    if plan.budget.max_scan_rows and estimate.scan_rows > plan.budget.max_scan_rows:
        violations.append(f"预估扫描行数 {estimate.scan_rows} 超过预算 {plan.budget.max_scan_rows}")

    return BudgetCheckResult(
        ok=len(violations) == 0,
        violations=violations
    )
```

---

## 7. 失败处理

### 7.1 重试策略

```python
def execute_step_with_retry(step: PlanStep, retry_policy: RetryPolicy) -> Any:
    """执行步骤，失败时重试"""
    max_retries = retry_policy.max_retries
    backoff = retry_policy.backoff
    delay = retry_policy.initial_delay_ms / 1000.0

    for attempt in range(max_retries + 1):
        try:
            return execute_step(step.type, step.params)
        except Exception as e:
            if attempt == max_retries:
                raise  # 最后一次尝试失败，抛出异常

            if not is_retryable_error(e):
                raise  # 不可重试的错误（如参数错误），直接抛出

            # 计算下次重试的延迟
            if backoff == "constant":
                sleep(delay)
            elif backoff == "linear":
                sleep(delay * (attempt + 1))
            elif backoff == "exponential":
                sleep(delay * (2 ** attempt))

def is_retryable_error(e: Exception) -> bool:
    """判断错误是否可重试"""
    # 可重试：网络超时、临时性数据库错误
    # 不可重试：参数错误、权限错误、数据不存在
    return isinstance(e, (TimeoutError, ConnectionError, TemporaryDatabaseError))
```

### 7.2 部分失败处理

```python
# continue_on_failure = True 时的行为
Plan = {
    "continue_on_failure": True,
    "steps": [
        {"id": "s1", "type": "observe", "params": {...}},
        {"id": "s2", "type": "observe", "params": {...}},
        {"id": "s3", "type": "correlate", "params": {...}, "depends_on": ["s1", "s2"]},
        {"id": "s4", "type": "synthesize", "depends_on": ["s1"]}
    ]
}

# 执行场景：s2 失败
# - s1 成功
# - s2 失败（重试 3 次后仍失败）
# - s3 跳过（依赖 s2，但 s2 失败）
# - s4 继续执行（只依赖 s1，s1 成功）

# 最终状态
{
    "status": "partial_success",
    "step_status": {
        "s1": "completed",
        "s2": "failed",
        "s3": "skipped",
        "s4": "completed"
    }
}
```

---

## 8. API 设计

### 8.1 创建 Plan

```
POST /sessions/{session_id}/plans

Request:
{
    "name": "GMV drop investigation",
    "budget": {
        "max_duration_sec": 30,
        "max_scan_rows": 10000000
    },
    "retry_policy": {
        "max_retries": 3,
        "backoff": "exponential"
    },
    "steps": [
        {"id": "s1", "type": "diagnose", "params": {...}},
        {"id": "s2", "type": "correlate", "params": {...}, "depends_on": ["s1"]},
        {"id": "s3", "type": "synthesize", "depends_on": ["s1", "s2"]}
    ]
}

Response:
{
    "plan_id": "plan_xyz",
    "status": "draft"
}
```

### 8.2 验证 Plan

```
POST /plans/{plan_id}/validate

Response:
{
    "valid": true,
    "errors": [],
    "warnings": [
        "Step s2 depends on s1.result.top_drivers[0], but s1 may return empty list"
    ]
}
```

### 8.3 估算成本

```
POST /plans/{plan_id}/estimate_cost

Response:
{
    "duration_sec": 25.3,
    "scan_rows": 8500000,
    "breakdown": [
        {"step_id": "s1", "duration_sec": 15.2, "scan_rows": 5000000},
        {"step_id": "s2", "duration_sec": 10.1, "scan_rows": 3500000},
        {"step_id": "s3", "duration_sec": 0, "scan_rows": 0}
    ],
    "budget_check": {
        "ok": true,
        "violations": []
    }
}
```

### 8.4 执行 Plan

```
POST /plans/{plan_id}/execute

Response:
{
    "execution_id": "exec_abc",
    "status": "executing"
}

# 轮询执行状态
GET /plans/{plan_id}/execution/{execution_id}

Response:
{
    "status": "executing",
    "step_status": {
        "s1": "completed",
        "s2": "executing",
        "s3": "pending"
    },
    "progress": 0.33
}
```

### 8.5 从 Template 创建 Plan

```
POST /plans/from_template

Request:
{
    "template": "gmv_drop_investigation",
    "session_id": "sess_abc",
    "params": {
        "metric": "GMV",
        "time_range": "last_7d"
    }
}

Response:
{
    "plan_id": "plan_xyz",
    "status": "draft"
}
```

---

## 9. 实现路线图

### Phase 1：基础能力（P0）

- [ ] Plan CRUD API
- [ ] DAG 依赖解析（拓扑排序）
- [ ] 串行执行（先不做并行）
- [ ] 基础成本估算（扫描行数）
- [ ] 预算检查（执行前拒绝超预算）

### Phase 2：并行执行（P0）

- [ ] 并行调度器（ThreadPoolExecutor）
- [ ] 关键路径计算（考虑并行的时间估算）
- [ ] 执行状态实时更新

### Phase 3：动态参数（P1）

- [ ] $ref 语法解析
- [ ] JSONPath 提取
- [ ] 参数验证和错误处理

### Phase 4：失败处理（P1）

- [ ] 重试策略（constant/linear/exponential backoff）
- [ ] 可重试错误判断
- [ ] continue_on_failure 支持

### Phase 5：模板化（P2）

- [ ] Plan template 存储
- [ ] 参数占位符替换
- [ ] Template 版本管理

---

## 10. 参考文档

- [分析工作流架构设计](./analysis-workflow-architecture.md)
- [Step 设计原则与分析意图抽象](./step-design-principles.md)
