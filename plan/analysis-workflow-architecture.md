# Factum 分析工作流架构设计

> 讨论日期：2026-03-27
> 版本：v1.0

## 1. 概述

本文档描述 Factum 如何将用户的分析意图转化为可执行的分析工作流，涵盖从用户问题到最终洞察的完整路径。

### 核心概念层级

```
用户分析意图
    ↓
Session (分析会话)
    ↓
Plan (可选的执行计划)
    ↓
Step (执行单元)
    ├─ 原子分析意图 (6个基础意图)
    ├─ 派生分析意图 (原子意图的组合)
    └─ Synthesize (证据综合)
    ↓
Observation (结构化证据)
    ↓
Findings (洞察与建议)
```

---

## 2. Session — 分析会话

### 2.1 定义

Session 是用户一次完整分析任务的容器，包含：
- 用户的分析问题
- 分析约束条件（时间范围、维度过滤等）
- 所有执行的 step
- 所有产出的 observation

### 2.2 结构

```python
Session = {
    "session_id": str,           # 唯一标识
    "question": str,             # 用户的分析问题
    "constraints": Dict,         # 约束条件（如 region="CN", time_range="2026-03"）
    "steps": [Step],             # 所有执行过的 step（按时间顺序）
    "observations": [Observation], # 所有产出的证据
    "findings": Findings | None, # synthesize 产出的最终洞察
    "created_at": datetime,
    "updated_at": datetime
}
```

### 2.3 生命周期

```
创建 → 执行 step → 积累证据 → synthesize → 完成
  ↑                    ↓
  └──── 可以随时添加新 step ────┘
```

### 2.4 Session 的价值

1. **证据积累的上下文**：所有 step 的 observation 保留在 session 中，供后续 step（尤其是 synthesize）使用
2. **约束自动注入**：session 的 constraints 自动合并到每个 step 的 WHERE 子句
3. **分析历史追溯**：完整记录分析过程，支持回溯和复现

---

## 3. Plan — 执行计划

### 3.1 定位

Plan 是**可选的**编排工具，用于提前规划多步骤分析流程。

**何时需要 Plan**：
- 复杂分析场景，需要多个 step 协同
- 需要并行执行独立的 step
- 需要提前估算成本和时间
- 需要复用分析模式

**何时不需要 Plan**：
- 简单分析，逐步探索即可
- 每步依赖前一步的结果内容（无法提前确定参数）

### 3.2 结构

```python
Plan = {
    "plan_id": str,
    "session_id": str,           # 所属 session
    "budget": Budget | None,     # 成本预算
    "retry_policy": RetryPolicy | None,
    "steps": [PlanStep],         # 步骤定义（DAG）
    "status": "draft" | "validated" | "executing" | "completed" | "failed"
}

PlanStep = {
    "id": str,                   # 步骤标识（如 "s1", "s2"）
    "type": str,                 # 原子意图、派生意图、synthesize
    "params": Dict,              # 参数（支持 $ref 动态引用）
    "depends_on": [str],         # 依赖的前序步骤 id
    "retry": RetryPolicy | None  # per-step 重试策略
}
```

### 3.3 Plan vs 逐步执行

| 维度 | 逐步执行（无 Plan） | Plan 执行 |
|------|---------------------|-----------|
| **执行模式** | Agent 每步调用 → 看结果 → 决定下一步 | Agent 生成 Plan → Factum 按 DAG 执行 |
| **并行性** | 无法并行 | 自动识别无依赖步骤并行执行 |
| **成本可控** | 无法提前估算 | 执行前估算，超预算拒绝 |
| **用户透明** | 用户看不到"接下来要做什么" | 用户可预览完整分析流程 |
| **复用性** | 每次重新决策 | 相同问题直接套用 Plan |

### 3.4 Plan 的能力边界

详见 [Plan 设计文档](./plan-design.md)。

---

## 4. Step — 执行单元

### 4.1 定义

Step 是实际执行的分析操作，是 Factum 的核心抽象。

**设计原则**：Step = 分析意图的 API，不是 SQL 语法糖。

### 4.2 Step 的三种类型

#### 4.2.1 原子分析意图（6个）

最基础的分析操作，直接编译为 SQL 执行。

| 原子意图 | 统计学分支 | 输入 | 输出 |
|----------|-----------|------|------|
| **observe** | 估计 | 1 metric + scope | MetricObservation |
| **compare** | 对比 | 1 metric + 2 scopes | Delta |
| **decompose** | 分解 | 1 metric + dimensions | Components |
| **correlate** | 关联 | 2 metrics + time_scope | Correlation |
| **detect** | 异常检测 | 1 metric + time_series | Anomalies |
| **test** | 推断 | hypothesis + 2 samples | TestResult |

**特点**：
- 1 个 API 调用 = 1 个 step 记录
- 直接执行，不展开
- 产出结构化的 observation

#### 4.2.2 派生分析意图（3个）

由原子意图组合而成，Factum 自动展开执行。

| 派生意图 | 组合 | 说明 |
|----------|------|------|
| **attribute** | compare + decompose | "GMV 为什么跌了" — 量化变化 + 维度归因 |
| **diagnose** | detect + compare + decompose | "这个指标有问题吗，原因是什么" — 异常检测 + 归因 |
| **validate** | observe + test | "这个假设成立吗" — 补充样本 + 统计检验 |

**特点**：
- 1 个 API 调用 = N 个 step 记录（N ≥ 2）
- Factum 自动展开为多个原子意图
- 所有中间参数可从输入参数确定性推导
- 对用户透明（用户只看到最终结果）

#### 4.2.3 Synthesize（特殊的 composite）

综合 session 中所有证据，产出人类可读的洞察和建议。

```python
POST /sessions/{id}/steps
{"type": "synthesize"}

# 系统执行
→ 读取 session 中所有 step 的 observations
→ 调用 LLM 综合分析
→ 产出 findings（洞察 + 建议 + 置信度）
```

**特点**：
- 不是分析意图，是证据综合器
- 无需参数，自动读取 session 上下文
- 通常在分析流程末尾调用

### 4.3 Step 的执行流程

```
用户调用 Step API
    ↓
判断 Step 类型
    ├─ 原子意图 → 编译为 SQL → 执行 → 产出 observation
    ├─ 派生意图 → 展开为多个原子意图 → 逐个执行 → 产出 observations
    └─ Synthesize → 读取 session 证据 → LLM 综合 → 产出 findings
    ↓
记录到 Session
```

---

## 5. Template — 分析模板

### 5.1 定位

Template 是**声明式的分析模式**，描述一类分析问题应该拆解为哪些步骤、步骤间的数据依赖、以及哪些环节需要外部决策。

**关键区别**：
- **派生意图**：所有参数在调用时确定，Factum 自动展开
- **Template**：部分参数需要执行中决策（看前序结果才能确定），由 Agent 编排

### 5.2 Template vs 派生意图

| 维度 | 派生意图 | Template |
|------|----------|----------|
| **参数确定性** | 所有参数调用时确定 | 部分参数需要执行中决策 |
| **展开逻辑** | 固定 DAG，Factum 自动展开 | 含 decision_point，Agent 逐步编排 |
| **执行模型** | 一次性提交，系统内部展开 | Agent 循环：调用 → 看结果 → 决策 → 调下一步 |
| **API 端点** | `POST /sessions/{id}/steps` | `GET /templates/{name}`（仅查询定义） |

### 5.3 Template 示例

```python
# explain template：找出指标变化的维度归因和上游关联原因
EXPLAIN_TEMPLATE = {
    "name": "explain",
    "steps": [
        {"intent": "compare", "params": {...}},
        {"intent": "decompose", "params": {...}, "depends_on": ["compare"]},
        {
            "intent": "correlate",
            "params": {"metric_b": "$DECISION"},  # 需要看 decompose 结果才能决定
            "decision_point": True,
            "decision_prompt": "根据分解结果，选择要关联分析的候选指标"
        }
    ]
}

# Agent 使用流程
1. GET /templates/explain → 拿到定义
2. 执行 compare
3. 执行 decompose
4. 看 decompose 结果 → 决定 candidate_metrics
5. 执行 correlate（对每个候选指标）
```

### 5.4 Template 的价值

1. **知识封装**：保留"这类问题怎么拆解"的分析模式
2. **降低门槛**：Agent 只需填空，不需要从零编排
3. **灵活性**：允许外部参与决策，适应探索性分析

---

## 6. 完整流程示例

### 场景 1：无 Plan，逐步探索

```python
# 1. 创建 session
POST /sessions
{"question": "GMV 为什么下跌了？", "constraints": {"region": "CN"}}
→ session_id = "sess_abc"

# 2. Agent 调用派生意图
POST /sessions/sess_abc/steps
{"type": "attribute", "params": {
    "metric": "GMV",
    "scope_a": {"period": "last_7d"},
    "scope_b": {"period": "prev_7d"},
    "dimensions": ["region", "channel"]
}}
→ 系统自动展开：
  step_1 (compare) → observation_1 (Delta: -15%)
  step_2 (decompose) → observation_2 (Components: mobile 贡献 80% 下跌)

# 3. Agent 看结果后，决定关联分析
POST /sessions/sess_abc/steps
{"type": "correlate", "params": {
    "metric": "GMV",
    "metric_b": "ad_spend",
    "time_scope": {"period": "last_30d"}
}}
→ step_3 (correlate) → observation_3 (Correlation: 0.9, lag=3d)

# 4. 综合结论
POST /sessions/sess_abc/steps
{"type": "synthesize"}
→ step_4 (synthesize) → findings:
  "GMV 下跌 15%，主要由 mobile 渠道贡献（80%）。
   广告投入下降与 GMV 高度相关（r=0.9），且存在 3 天延迟。
   建议：恢复 mobile 广告投放。"
```

**Session 中的记录**：
```
sess_abc.steps = [
    step_1 (compare, 来自 attribute 展开),
    step_2 (decompose, 来自 attribute 展开),
    step_3 (correlate, Agent 直接调用),
    step_4 (synthesize, Agent 直接调用)
]
```

### 场景 2：有 Plan，提前规划

```python
# 1. 创建 session
POST /sessions
{"question": "GMV 为什么下跌了？"}
→ session_id = "sess_abc"

# 2. Agent 生成 plan
POST /sessions/sess_abc/plans
{
    "budget": {"max_duration_sec": 30},
    "steps": [
        {"id": "s1", "type": "diagnose", "params": {...}},
        {"id": "s2", "type": "correlate", "params": {...}, "depends_on": ["s1"]},
        {"id": "s3", "type": "synthesize", "depends_on": ["s1", "s2"]}
    ]
}
→ plan_id = "plan_xyz"

# 3. 执行 plan
POST /plans/plan_xyz/execute
→ 系统按 DAG 执行：
  step_1 (detect, 来自 diagnose 展开)
  step_2 (compare, 来自 diagnose 展开)
  step_3 (decompose, 来自 diagnose 展开)
  step_4 (correlate, 并行执行，依赖 s1 完成)
  step_5 (synthesize, 等待 s1/s2 完成)
```

---

## 7. 架构分层

```
┌─────────────────────────────────────────────────────────────┐
│  Templates (声明式分析模式，Agent 读取定义后自行编排执行)          │
│  describe, explain, funnel_analysis, cohort_analysis, ...    │
│                                                             │
│  特征：含 decision_point，步骤间有数据依赖+外部决策交织          │
│  API：GET /templates/{name}（查询定义），无执行端点              │
│  执行者：Agent（外部编排器）                                    │
└─────────────────────────────────────────────────────────────┘
                         ↓ 组合（Agent 逐步调用）
┌─────────────────────────────────────────────────────────────┐
│  Derived Intents (派生意图，Factum 全自动展开)                  │
│  attribute, diagnose, validate                                │
│                                                             │
│  特征：所有参数调用时确定，无 decision_point                     │
│  API：POST /sessions/{id}/steps（与原子意图相同）               │
└─────────────────────────────────────────────────────────────┘
                         ↓ 展开为
┌─────────────────────────────────────────────────────────────┐
│  Atomic Intents (6 个原子意图)                               │
│  observe, compare, decompose, correlate, detect, test       │
│                                                             │
│  + synthesize (composite)                                   │
│                                                             │
│  编译为 SQL，使用内部共享模块：                                 │
│  - build_aggregate_sql()  聚合查询构建                        │
│  - build_time_filter()    时间范围过滤                        │
│  - translate()            方言适配 (dialect.py)              │
│  - QueryRouter            表名解析 → 引擎路由                  │
└─────────────────────────────────────────────────────────────┘
```

---

## 8. 核心设计原则

1. **Session 是证据积累的容器**：所有 step 的 observation 保留在 session 中，供后续 step 使用

2. **Plan 是可选的**：简单分析可以不用 plan，直接逐步调用 step；复杂分析可以用 plan 提前规划

3. **派生意图是语法糖**：用户调用 `attribute`，系统自动展开为 `compare + decompose`，但 session 中会保留 2 个 step 记录

4. **Synthesize 是终结者**：读取 session 中所有证据，产出最终洞察，通常是分析流程的最后一步

5. **Template 不在执行层级**：Template 是声明式定义，由 Agent 读取后逐步调用 step，不是 Factum 内部的执行单元

6. **Factum 是分析引擎，不是工作流引擎**：步骤间的数据依赖 + 外部决策交织由 Agent 处理，不由 Factum 内置 checkpoint/yield 机制

---

## 9. API 概览

### Session API

```
POST   /sessions                    # 创建 session
GET    /sessions/{id}               # 查询 session
POST   /sessions/{id}/steps         # 执行 step（原子/派生/synthesize）
GET    /sessions/{id}/observations  # 查询所有证据
```

### Plan API

```
POST   /sessions/{id}/plans         # 创建 plan
GET    /plans/{id}                  # 查询 plan
POST   /plans/{id}/execute          # 执行 plan
POST   /plans/{id}/estimate_cost    # 估算成本
```

### Template API

```
GET    /templates                   # 列出所有 template
GET    /templates/{name}            # 查询 template 定义
POST   /templates                   # 注册自定义 template
```

---

## 10. 参考文档

- [Step 设计原则与分析意图抽象](./step-design-principles.md)
- [Plan 设计文档](./plan-design.md)
- [Time Scope 统一设计](./time-scope-unification-rfc.md)
