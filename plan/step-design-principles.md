# Step 设计原则与分析意图抽象

> 讨论日期：2026-03-26

## 1. 问题背景

Factum 当前有 7 种 step 类型，核心问题是：
1. 如何避免为特定业务场景不停开发新 step 类型？
2. Step 层的正确抽象是什么？和 SQL 有什么区别？

---

## 2. 错误路径 vs 正确路径

### 错误路径：Step = SQL 语法糖

```
Step 只是对 SQL 关系代数的薄封装：
  join → SQL JOIN
  aggregate → SQL GROUP BY
  window → SQL OVER()

问题：Agent 直接生成 SQL 更灵活，Factum 变成多余的中间层
```

### 正确路径：Step = 分析意图的原语

```
Step 封装的是"分析意图"：
  - 用户说"对比 GMV" → 不需要知道时间窗口 SQL 怎么写
  - 用户说"归因分析" → 不需要知道贡献度公式
  - SQL 是实现细节，不是 API 层

价值：
  1. 语义抽象：操作 metric/entity，不操作 table/column
  2. 证据结构：输出是可解释的 observation，不是原始行
  3. 分析方法封装：归因、相关性、异常检测等方法不需要用户写
  4. 治理内置：policy、合规自动应用
```

---

## 3. Step 设计判断标准

设计一个新 step 时，应该回答以下问题：

| 标准 | 通过示例 | 失败示例 |
|------|----------|----------|
| 是否封装了"分析方法"而不仅是"计算"？ | `compare_metric` 封装了对比分析的完整逻辑 | `join` 只是 SQL JOIN 的映射 |
| 用户是否需要知道内部实现细节？ | 用户说"归因分析"，不需要知道贡献度公式 | 用户需要理解 SQL 子查询结构 |
| 输出是否是结构化 evidence？ | 有 observation_type、confidence、provenance | 只返回原始行数据 |
| 是否有治理/合规价值？ | 自动应用 policy、row_filter | 无约束执行 |
| 输出是否可程序化裁剪且裁剪后仍反映分析意图？ | decompose 按 \|share\| 排序取 top-k，裁剪后仍反映"谁贡献最大" | describe 的多维观测无统一排序维度，裁剪任一维度都可能丢失关键信息 |

---

## 4. 设计原则：分析领域的关系代数

类比关系代数（Codd, 1970）用 select/project/join/union 等正交算子组合表达任意数据操作，
Factum 的 step 层需要一组正交的**原子分析意图**，组合起来能表达任意复杂的业务分析场景。

关键区别：关系代数操作 table/column，分析意图操作 entity/metric。

### 原子意图的判断标准

1. **不可替代**：不能由其他原子意图组合表达
2. **输入输出类型独立**：每个算子的签名（输入什么、输出什么）不同
3. **统计学锚点**：对应统计学中一个独立的方法分支
4. **语义层操作**：作用于 metric/entity，而非 table/column

---

## 5. 六个原子分析意图

### 理论锚点

六个原子意图分别对应统计学的独立分支：

| 原子意图 | 统计学分支 | 核心参考 |
|----------|-----------|---------|
| observe | Estimation（估计） | Casella & Berger, *Statistical Inference* Ch.7 |
| compare | Comparison（对比） | Casella & Berger, *Statistical Inference* Ch.8-9 |
| decompose | Decomposition（分解） | Shapley (1953); Shorrocks (2013) *Decomposition Procedures*; Fisher ANOVA (1925) |
| correlate | Association（关联） | Pearson (1895); Granger (1969) *Investigating Causal Relations* |
| detect | Anomaly Detection（异常检测） | Chandola et al. (2009) *Anomaly Detection: A Survey*, ACM Computing Surveys |
| test | Inference（推断） | Neyman & Pearson (1933); Fisher (1925) *Statistical Methods* |

综合参考：Larry Wasserman, *All of Statistics* (2004) — 章节组织覆盖以上六个分支的独立性。

---

### 5.1 Observe — 观测（Estimation）

读取语义指标在指定时间范围和筛选条件下的观测值。

**核心职责**：产出类型化观测，作为下游步骤的输入。

**输入**：
- `metric`：语义指标名称
- `time_scope`：时间范围（range/snapshot_now/latest_available/as_of）
- `result_mode`：观测模式（standard/numeric_sample_summary/rate_sample_summary）
- `filters`：筛选条件（可选）
- `granularity`：时间粒度（可选，用于时序观测）
- `dimensions`：维度拆分（可选，用于分段观测）
- `limit`：分段观测的行数限制（可选）

**输出类型**：
- `scalar`：单一聚合值
- `time_series`：按时间粒度的序列
- `segmented`：按维度拆分的分段值
- `numeric_sample_summary`：数值样本统计摘要（用于下游 test）
- `rate_sample_summary`：比率样本统计摘要（用于下游 test）

**关键约束**：
- `time_scope` 必需——观测必须有明确的时间语义
- `granularity` 与 `dimensions` 互斥——不能同时按时间和维度拆分
- inferential-ready 模式（sample_summary）只支持标量输出

**当前对应**：`aggregate_query`（部分）、`profile_table`（部分）

---

### 5.2 Compare — 对比（Comparison）

计算同一指标的两个观测之间的类型化差异。

**核心职责**：消费上游观测引用，产出结构化 delta。

**输入**：
- `left_ref`：当前期/实验组观测的步骤引用
- `right_ref`：基准期/对照组观测的步骤引用
- `mode`：对比模式（auto/scalar/segmented）
- `limit`：分段对比的行数限制（可选）

**输出类型**：
- `scalar_delta`：标量差异（absolute_delta, relative_delta, direction）
- `segmented_delta`：分段差异（按维度的 delta 行 + scope 级汇总）

**关键约束**：
- 两个观测必须是同一 metric
- 两个观测必须是相同的 observation_type（scalar vs scalar, segmented vs segmented）
- segmented 对比要求两侧 dimensions 完全一致
- 不支持 time_series 对比（v1）

**可比性检查**：
- 同 metric、同 unit、同 aggregation_semantics
- 状态：comparable / needs_attention / not_comparable

**当前对应**：`compare_metric` ✓

---

### 5.3 Decompose — 分解（Decomposition）

将已定义的 metric delta 按维度分配为排序的贡献度。

**核心职责**：delta 归因，不是通用组合分析。

> **V1 范围限制**：当前版本只支持**变化分解**（delta attribution），不支持总量分解（total composition）。如需分解当前值的构成，应使用 `observe(segmented)`。未来版本可能扩展支持总量分解。

**输入**：
- `compare_ref`：前序 compare 步骤引用（定义待解释的 delta）
- `dimension`：单个语义维度（v1 只支持单维度）
- `method`：归因方法（v1 只支持 delta_share）
- `limit`：贡献行数限制（可选）

**输出类型**：
- `delta_decomposition`：按维度的贡献行 + scope 级汇总 + 截断/未解释残差

**关键约束**：
- 只消费 `scalar_delta`（不支持 segmented_delta 作为输入）
- 只支持 additive metrics（v1）
- v1 不支持总量分解（只支持变化分解）
- v1 不支持多维度交互分析

**贡献语义**：
- `absolute_contribution = segment_left_value - segment_right_value`
- `contribution_share = absolute_contribution / scope_absolute_delta`（带符号）
- `presence`：both / left_only / right_only（区分持续变化 vs 结构性新增/消失）

**当前对应**：`attribute_change`（部分，缺交互效应和残差）

---

### 5.4 Correlate — 关联（Association）

估计两个时间序列观测之间的统计关联。

**核心职责**：成对关联分析，不是因果推断或候选扫描。

**输入**：
- `left_ref`：第一个时序观测的步骤引用
- `right_ref`：第二个时序观测的步骤引用
- `method`：关联方法（pearson / spearman）
- `min_pairs`：最小对齐数据点数（可选，默认 5）

**输出类型**：
- `pairwise_time_series`：关联系数 + p_value + 对齐元数据

**关键约束**：
- 两个观测必须都是 `time_series` 类型
- 两个观测必须有相同的 `granularity`
- 对齐规则：intersection_by_time_bucket（交集，不插值）
- v1 不支持 lag 搜索、control_for、多指标扫描

**关联语义**：
- `coefficient`：[-1, 1]，绝对值越大关联越强
- `sign`：positive / negative / zero / undefined
- `significance`：significant / not_significant / undefined（基于 p_value）
- 关联不等于因果

**当前对应**：`correlate_metrics` ✓

---

### 5.5 Detect — 检测（Anomaly Detection）

扫描指标时间范围，返回排序的异常候选点。

**核心职责**：候选发现，不是根因诊断或确认事实。

**输入**：
- `metric`：语义指标名称
- `time_scope`：扫描时间范围（必须是 range）
- `granularity`：扫描粒度（hour / day / week / month，默认 day）
- `dimension`：单个维度拆分（可选，v1 只支持单维度）
- `profile`：检测模式（auto / spike_dip / level_shift / seasonal_residual）
- `sensitivity`：灵敏度（conservative / balanced / aggressive）
- `limit`：返回候选数（可选，默认 10）
- `max_series`：最大扫描序列数（可选，用于维度拆分）

**输出类型**：
- `anomaly_candidates`：排序的候选行 + 扫描摘要 + 截断元数据

**关键约束**：
- 只支持 range time_scope（不支持 snapshot）
- v1 只支持单维度拆分
- profile 是语义契约，不是算法选择器
- 输出是候选标记，不是确认事实

**候选语义**：
- `observed_value` vs `expected_value`
- `deviation_abs` / `deviation_pct`
- `candidate_score`：排序分数（不是概率）
- `flag_level`：low / medium / high（优先级，不是跨方法的严重度定理）
- `direction`：up / down / flat / undefined

**当前对应**：无（新增）

---

### 5.6 Test — 检验（Statistical Inference）

评估结构化统计假设。

**核心职责**：假设检验，不是业务结论或综合判断。

**输入**：
- `left_ref`：第一组样本观测的步骤引用
- `right_ref`：第二组样本观测的步骤引用
- `hypothesis`：结构化假设定义（family / alternative / alpha / label）
- `method`：检验方法（auto / welch_t / mann_whitney_u / two_proportion_z）

**输出类型**：
- `hypothesis_test`：检验统计量 + p_value + 决策 + 置信区间 + 假设检验元数据

**关键约束**：
- 只消费 inferential-ready 观测（numeric_sample_summary / rate_sample_summary）
- 两个观测必须是相同的 observation_type
- 两个观测必须引用同一 metric（或显式标记为可比的 metric family）
- v1 只支持 family = "difference"
- v1 不支持配对检验、重复测量、多臂检验

**假设语义**：
- `family`：difference（v1 唯一支持）
- `alternative`：two_sided / greater / less
- `alpha`：显著性水平（默认 0.05）
- `estimate`：estimand + value + confidence_interval
- `decision.reject_null`：基于 p_value <= alpha
- reject_null = true 不等于因果或重要性

**当前对应**：无（新增）

---

### 正交性验证

```
           observe  compare  decompose  correlate  detect  test
输入        1 metric 2 scope  metric+dims 2 metrics series  samples
输出        value    delta    components  coefficient 异常点 p-value
操作对象    metric   metric   dim→metric  metric×2   metric metric
时间性      截面     对比     截面        序列       序列   截面
统计分支    估计     对比     分解        关联       检测   推断
```

每个算子的输入签名、输出类型、统计学锚点均不同，交叉最小化。

---

## 6. 派生分析意图

由原子意图组合而成，提供更高层的便捷语义。用户可直接调用，内部展开为原子意图的 DAG。

**派生意图的判断标准**：不需要外界（人/Agent）参与其内部原子意图组合串联的任何逻辑，对用户是一个完整的原子分析动作。所有中间参数必须能从输入参数和系统状态（semantic layer）确定性推导。不满足此标准的组合应定义为 template（见第 7 节），由外部编排。

**输出可裁剪性**（适用于所有面向用户的意图——原子 + 派生）：输出数据可按照一定程序化方式裁剪，且裁剪后的数据依然较大程度反映此分析意图。这是意图暴露给用户的前提——消费方（人/Agent）的处理能力有限，输出必须在受控大小内仍保持分析价值。不满足此约束的组合应降级为 template。

### 6.1 Attribute（归因）= Compare + Decompose

```
attribute(metric, scope_a, scope_b, dimensions, [method], [limit])
  ≡ step1 = compare(metric, scope_a, scope_b)
    → decompose(metric, dimensions, delta_ref=step1, method, limit)
```

"GMV 为什么跌了"——先量化变化，再拆解到维度。

**与 decompose 的区别**：attribute 是便捷封装，自动创建 compare 步骤；decompose 是原子步骤，需要手动提供 compare_ref。两者的归因逻辑完全相同。

全自动：所有参数在调用时确定，compare → decompose 的串联（delta_ref）系统自动完成。✓

#### 输入参数语义

- **metric**（必需）—— 要归因的语义指标
- **scope_a**（必需）—— 基准组，类型同 compare 的 `Scope = Dict[str, value]`
- **scope_b**（必需）—— 对照组，结构同 scope_a
- **dimensions**（必需）—— 按哪些维度分解变化量，如 `[region, channel]`
- **method**（可选）—— 分解算法，透传给内部 decompose：`contribution`、`shapley`、`anova`。默认 `contribution`
- **limit**（可选）—— top_drivers 的最大条数，按 `|share|` 降序截断。默认 10

#### 输出字段语义
```python
Attribution(
    delta: Delta,
    top_drivers: [(dim_value, contribution, share, direction)],
    interaction_effects: [(dim_combination, effect)] | None,
    residual_share: float
)
```

- **delta** —— 整体变化量化（来自 compare），包含 absolute、percentage、direction
- **top_drivers** —— 按 `|share|` 降序排列的主因列表，直接回答"谁贡献最大"。每项包含维度取值、绝对贡献量、占比、变化方向
- **interaction_effects** —— 仅保留显著的交互项（`|effect|` 超过阈值）。单维度分解时为空。存在显著交互效应意味着需要交叉定位根因
- **residual_share** —— 未解释占比（0~1）。值高说明选定维度不足以解释变化，暗示需要补充更多维度

### 6.2 Diagnose（诊断）= Detect + Compare + Decompose

```
diagnose(metric, lookback, dimensions, [granularity], [sensitivity], [method], [limit], [drivers_limit])
  ≡ anomalies = detect(metric, lookback, granularity, sensitivity)
    → 按 severity + 偏离量降序取 top-limit 个异常
    → for each anomaly:
        step_n = compare(metric, anomaly_window, baseline_window)
        → decompose(metric, dimensions, delta_ref=step_n, method, drivers_limit)
```

"这个指标最近有没有问题，有的话是什么原因"——完整异常诊断流。

全自动：`baseline_window` 由系统规则推导（异常窗口的前一个等长周期），`dimensions` 由用户在调用时传入，输出按异常严重程度确定性排序截断。✓

#### 输入参数语义

- **metric**（必需）—— 要诊断的语义指标
- **lookback**（必需）—— 回溯时间窗口，类型为 `TimeScope`。detect 在此范围内扫描异常
- **dimensions**（必需）—— 按哪些维度分解异常变化量，透传给内部 decompose
- **granularity**（可选）—— 异常检测的采样粒度，透传给内部 detect。默认 `day`
- **sensitivity**（可选）—— 异常检测灵敏度，透传给内部 detect。默认 0.5
- **method**（可选）—— 分解算法，透传给内部 decompose。默认 `contribution`
- **limit**（可选）—— 最多返回的异常数量，按 severity + 偏离量降序截断。默认 5
- **drivers_limit**（可选）—— 每个异常的归因条数（top_drivers），按 `|share|` 降序截断。默认 5

#### 输出字段语义

```python
Diagnosis(
    anomaly_count: int,
    top_anomalies: [(period, severity, delta: Delta, top_drivers: [(dim_value, contribution, share, direction)])]
)
```

- **anomaly_count** —— 检测到的异常总数（截断前）
- **top_anomalies** —— 按严重程度排序的 top-limit 个异常，每个包含：异常时间区间、严重程度、整体变化量化、按贡献排序的维度归因

### 6.3 Validate（验证）= Observe + Test

```
validate(hypothesis: Hypothesis, sample_a, sample_b, [method])
  ≡ observe(...)                                    # 从 sample_a/sample_b 引用的步骤补充样本
    → test(hypothesis, sample_a, sample_b, method)   # 统计检验
```

对已有证据做假设检验。注意：多证据综合判断属于 synthesize 的职责，不在 validate 内。

全自动：hypothesis 已结构化，sample 引用前序步骤，参数完全确定。✓

#### 输入参数语义

- **hypothesis**（必需）—— 结构化假设定义，类型为 `Hypothesis`（null/alternative/margin/label）
- **sample_a**（必需）—— 第一组样本，引用前序步骤（step_id）
- **sample_b**（必需）—— 第二组样本，引用前序步骤（step_id）
- **method**（可选）—— 检验方法，透传给内部 test：`t_test`、`chi_square`、`mann_whitney`。默认自动选择

#### 输出字段语义

```python
Validation(observations: [MetricObservation] | None, result: TestResult)
```

- **observations** —— observe 补充的样本数据（如 sample 引用的步骤数据不足时触发补充观测）。无需补充时为空
- **result** —— test 的输出，包含 statistic、p_value、reject_null、confidence_interval

---

## 7. Template（分析模板）

### 7.0 定位：声明式分析模式，不是可执行工作流

Template 是**结构化的"分析菜谱"**——描述一类分析问题应该拆解为哪些步骤、步骤间的数据依赖、以及哪些环节需要外部决策。

关键设计决策：**Factum 是分析引擎，不是工作流引擎。** Template 的执行者是 Agent（外部编排器），不是 Factum 的 plan executor。

原因：
1. Agent 天然就是一个有状态的决策循环（调用 step → 看结果 → 思考 → 调下一步），checkpoint 是 Agent 的自然行为
2. 把中间决策逻辑放进 Factum 意味着需要支持暂停-等待-恢复的状态机（类似 Temporal/Airflow），这不是分析引擎该做的事
3. checkpoint 的触发条件、context 结构、返回参数 schema、超时/取消/重试语义都是工作流引擎的职责，不应侵入分析意图层

因此：
- Template **不**由 Factum 自动展开执行（区别于派生意图）
- Template 通过 API 提供定义查询（`GET /templates/{name}`），不提供执行端点
- Agent 读取 template 定义后，自己控制执行节奏和中间决策

### 7.1 Template 结构定义

每个 template 包含以下结构：

```python
Template = {
    "name": str,                    # 模板名称
    "description": str,             # 人类可读描述
    "steps": [TemplateStep],        # 步骤序列（含依赖和决策点）
    "required_inputs": [str],       # 调用前必须确定的参数
    "deferred_inputs": [str]        # 执行中才确定的参数（需外部决策）
}

TemplateStep = {
    "intent": str,                  # 原子/派生意图名称
    "params": Dict[str, str],       # 参数映射（$var 引用输入，$REF 引用前序步骤输出）
    "output_ref": str,              # 输出引用名，供后续步骤引用
    "decision_point": bool,         # 是否为决策点（默认 false）
    "decision_prompt": str,         # 决策点的提示（描述需要外部决定什么）
    "decision_param": str,          # 决策点需要外部填入的参数名
    "suggestions_from": str,        # 系统可从哪个前序输出推导建议候选
    "repeat": str | None            # 重复模式：null（单次）、"per_item"（对决策结果逐项执行）
}
```

### 7.2 Describe（描述）= Observe × N [+ Detect]

```python
DESCRIBE_TEMPLATE = {
    "name": "describe",
    "description": "给出指标的全貌——多维切面观测 + 时序趋势 + 可选异常标记",
    "steps": [
        {
            "intent": "observe",
            "params": {"metric": "$metric", "time_scope": "$time_scope",
                       "dimensions": "$DECISION", "filters": "$filters"},
            "output_ref": "dim_observations",
            "decision_point": True,
            "decision_prompt": "选择要观测的维度切面（可能有几十个维度，全算会爆炸）",
            "decision_param": "dimensions",
            "suggestions_from": None  # 首步无前序输出，Agent 根据 semantic layer 选择
        },
        {
            "intent": "observe",
            "params": {"metric": "$metric", "time_scope": "$time_scope",
                       "granularity": "$granularity", "filters": "$filters"},
            "output_ref": "time_series"
        },
        {
            "intent": "detect",
            "params": {"metric": "$metric", "time_scope": "$time_scope",
                       "granularity": "$granularity"},
            "output_ref": "anomalies",
            "decision_point": True,
            "decision_prompt": "是否需要异常检测？根据时序观测结果判断",
            "decision_param": "_skip",
            "suggestions_from": "time_series"
        }
    ],
    "required_inputs": ["metric", "time_scope"],
    "deferred_inputs": ["dimensions", "granularity", "filters"]
}
```

"给我看看这个指标的全貌"——多次 observe 加统计摘要。

降级原因：describe 的价值是"全貌"，但"全貌"与"受控输出"矛盾。多维观测结果之间没有统一的排序维度（无法确定性判断哪个维度的 breakdown 更重要），无法生成确定性 summary。需要人或 Agent 决定观测哪些维度、哪些结果值得深入。

### 7.3 Explain（解释）= Compare + Decompose + Correlate

```python
EXPLAIN_TEMPLATE = {
    "name": "explain",
    "description": "找出指标变化的维度归因和上游关联原因",
    "steps": [
        {
            "intent": "compare",
            "params": {"metric": "$metric", "scope_a": "$scope_a", "scope_b": "$scope_b"},
            "output_ref": "delta"
        },
        {
            "intent": "decompose",
            "params": {"metric": "$metric", "dimensions": "$dimensions",
                       "delta_ref": "$delta"},
            "output_ref": "drivers"
        },
        {
            "intent": "correlate",
            "params": {"metric": "$metric", "metric_b": "$DECISION",
                       "time_scope": "$time_scope"},
            "output_ref": "correlations",
            "decision_point": True,
            "decision_prompt": "根据分解结果，选择要关联分析的候选指标",
            "decision_param": "candidate_metrics",
            "suggestions_from": "drivers",
            "repeat": "per_item"
        }
    ],
    "required_inputs": ["metric", "scope_a", "scope_b", "dimensions"],
    "deferred_inputs": ["candidate_metrics", "time_scope"]
}
```

比 attribute 更进一步——不仅找维度贡献，还找关联指标作为上游原因。

降级原因：correlate 的 `candidate_metrics`（跟谁算关联）无法确定性推导——metric 可能有几十上百个，全部计算既昂贵又产生噪声。且这个决策依赖 decompose 的结果（看到哪些维度贡献大，才能推断可能关联的上游指标），属于**步骤间数据依赖 + 外部决策的交织**。

### 7.4 Agent 如何使用 Template

Agent 的执行流程：

```
1. 识别用户意图 → 匹配 template（如 "GMV 全貌" → describe）
2. GET /templates/describe → 拿到模板定义
3. 填充 required_inputs（metric, time_scope）
4. 遍历 steps：
   a. 非 decision_point → 直接调用对应原子意图
   b. decision_point → 读取前序步骤结果 + suggestions_from 建议
                      → Agent 自主决策（或询问用户）填入 deferred_input
                      → 调用对应原子意图
   c. repeat="per_item" → 对决策结果列表逐项执行
5. 所有步骤完成 → 可选调用 synthesize 综合结论
```

Template 提供的是**分析模式知识**（这类问题怎么拆解），Agent 提供的是**执行编排 + 中间决策**（什么时候执行、中间结果怎么判断）。

### 7.5 派生意图 vs Template 的判断标准

| 维度 | 派生意图（Derived Intent） | Template |
|------|---------------------------|----------|
| **参数确定性** | 所有参数在调用时确定 | 部分参数需要执行中决策（deferred_inputs） |
| **展开逻辑** | 固定 DAG，Factum 自动展开 | 含 decision_point，Agent 逐步编排 |
| **执行模型** | 一次性提交，系统内部展开执行 | Agent 循环：调用 → 看结果 → 决策 → 调下一步 |
| **输出可控性** | 输出大小可确定性裁剪 | 输出大小无法确定性控制（如 describe 的多维观测） |
| **API 端点** | `POST /sessions/{id}/steps` | `GET /templates/{name}`（仅查询定义） |
| **示例** | `attribute(metric, scope_a, scope_b, dims)` | `explain(metric, scope_a, scope_b, dims, candidate_metrics=?)` |

**关键区别**：派生意图的所有中间步骤参数可从输入参数确定性推导（如 `attribute` 的 `delta_ref` 自动引用内部 `compare` 步骤），而 template 的某些步骤参数依赖前序步骤的**结果内容**（如 `explain` 需要看 `decompose` 结果才能选择 `candidate_metrics`）。

前者是纯函数组合，后者是有状态的决策流。

---

## 8. 意图组合 = 分析工作流

典型分析流程示例：

```
用户: "GMV 怎么跌了？"

Step 1: compare(GMV, last_7d vs prev_7d)
        → 发现下跌 15%

Step 2: decompose(GMV, dims=[region, channel, product], delta_ref=step1)
        → CN 地区 + mobile 渠道贡献 80% 下跌，交互效应 5%

Step 3: detect(GMV, lookback=30d)
        → 1 月 15 日开始异常

Step 4: correlate(GMV, ad_spend)
        → 广告投入下降，相关性 0.9，延迟 3 天

Step 5: test(hypothesis="ad_spend_decline → GMV_decline", samples=[...])
        → p < 0.01，拒绝零假设

Step 6: synthesize_findings()
        → 综合报告和建议
```

等价的派生写法：

```
Step 1: diagnose(GMV, lookback=30d)        # = detect + compare + decompose
Step 2: correlate(GMV, ad_spend)
Step 3: validate("ad_cut_caused_decline")  # = observe + test
Step 4: synthesize_findings()
```

---

## 9. Step 架构

```python
ANALYSIS_INTENT_TAXONOMY = {
    # 6 个原子分析意图
    "observe":   {"category": "atomic", "stats_branch": "estimation"},
    "compare":   {"category": "atomic", "stats_branch": "comparison"},
    "decompose": {"category": "atomic", "stats_branch": "decomposition"},
    "correlate": {"category": "atomic", "stats_branch": "association"},
    "detect":    {"category": "atomic", "stats_branch": "anomaly_detection"},
    "test":      {"category": "atomic", "stats_branch": "inference"},

    # 派生分析意图（原子意图的组合，全自动展开，无需外部干预）
    "attribute": {"category": "derived", "expands_to": ["compare", "decompose"]},
    "diagnose":  {"category": "derived", "expands_to": ["detect", "compare", "decompose"]},
    "validate":  {"category": "derived", "expands_to": ["observe", "test"]},

    # Template（声明式分析模式，Agent 编排执行，不由 Factum 自动展开）
    # "describe": {"category": "template", "composes": ["observe", "detect"],
    #              "deferred_inputs": ["dimensions"], "has_decision_points": True},
    # "explain":  {"category": "template", "composes": ["compare", "decompose", "correlate"],
    #              "deferred_inputs": ["candidate_metrics"], "has_decision_points": True},

    # 综合意图
    "synthesize": {"category": "composite", "description": "综合生成洞察"},
}
```

---

## 10. 架构分层

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

**设计决策**：不引入独立的 Primitives 层。

原因：
1. 当前 6 个原子意图 + 2 种引擎的规模下，共享操作用 helper 函数即可，不需要独立的抽象层
2. SQL 方言差异已由 `dialect.py` 统一处理，不需要 Primitives 再做一遍
3. 引入新层级的代价（IR、注册、测试）在当前规模下不合理
4. YAGNI：如果未来引擎增加到 5+ 种或原子意图增加到 15+，再考虑提取 Primitives 层

---

## 11. 当前 Step 重构映射

| 当前 Step | 状态 | 映射 |
|-----------|------|------|
| `compare_metric` | ✓ 保留 | → 原子 `compare` |
| `aggregate_query` | ⚠️ 重构 | → 原子 `observe` 的内部实现 |
| `profile_table` | ⚠️ 重构 | → Template `describe`（observe × N，需外部指定维度） |
| `sample_rows` | ⚠️ 降级 | → 内部 helper（不作为独立 step 暴露） |
| `attribute_change` | ⚠️ 重构 | → 派生 `attribute`（compare + decompose） |
| `correlate_metrics` | ✓ 保留 | → 原子 `correlate` |
| `synthesize_findings` | ✓ 保留 | → composite `synthesize` |
| — | ❌ 新增 | 原子 `observe` |
| — | ❌ 新增 | 原子 `decompose` |
| — | ❌ 新增 | 原子 `detect` |
| — | ❌ 新增 | 原子 `test` |
| — | ❌ 新增 | 派生 `diagnose`, `validate` |
| — | ❌ 新增 | Template `describe`（需外部指定观测维度）、`explain`（需外部指定 candidate_metrics） |

---

## 12. 核心原则总结

1. **Step = 分析意图的 API**，不是 SQL 语法糖
2. **6 个原子意图**（observe, compare, decompose, correlate, detect, test）正交且完备，各对应统计学独立分支
3. **派生意图**（attribute, diagnose, validate）由原子意图组合而成，Factum 全自动展开，无需外部干预
4. **输出可裁剪性**：所有面向用户的意图（原子 + 派生），输出数据可按程序化方式裁剪，且裁剪后仍较大程度反映分析意图
5. **分析方法是参数**（method: shapley|pearson|zscore），不是新 step
6. **Template 是声明式分析模式**，不是可执行工作流。Factum 提供定义查询（`GET /templates/{name}`），Agent 负责编排执行和中间决策
7. **Factum 是分析引擎，不是工作流引擎**。步骤间的数据依赖 + 外部决策交织由 Agent 处理，不由 Factum 内置 checkpoint/yield 机制
8. **业务场景用 template 组合意图**，无需开发新 step
9. **不引入独立的 Primitives 层**。原子意图直接编译为 SQL，共享操作用 helper 函数实现（当前规模下不需要独立抽象层）

---

## 13. 后续行动

1. [ ] 实现原子 `observe` step（从 aggregate_query 提炼）
2. [ ] 实现原子 `decompose` step（从 attribute_change 拆出）
3. [ ] 实现原子 `detect` step
4. [ ] 实现原子 `test` step
5. [ ] 重构 `attribute_change` 为派生 `attribute`（调用 compare + decompose）
6. [ ] 重构 `profile_table` 为 Template `describe`
7. [ ] 实现派生 `diagnose`, `validate`
8. [ ] 将 `sample_rows` 降级为内部 helper 函数（不作为独立 step 暴露）
9. [ ] 提取共享 SQL 构建模块：`build_aggregate_sql()`, `build_time_filter()` 等
10. [ ] 实现 template 定义存储与查询 API（`GET /templates/{name}`）
11. [ ] 实现内置 template：`describe`（含 decision_point）、`explain`（含 decision_point）
12. [ ] 设计 template 自定义机制，支持用户注册业务场景 template
