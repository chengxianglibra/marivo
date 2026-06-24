# Decision-Centric Analysis Algebra Redesign

> **状态更新（评审后）**：本文（v1）是较激进的提案，已经过一轮 code-grounded
> review。review 的核心反对意见成立（详见文末 §9 评审记录），收敛后的方向已另立
> [`decision-centric-analysis-redesign-v2.md`](./decision-centric-analysis-redesign-v2.md)。
> **本文保留作为讨论留痕，不作为推进基线；请以 v2 为准。**

状态：proposal / RFC，供讨论，**不表示已被接受**。本文提出对 `marivo.analysis`
分析算子代数的一次抽象层次调整：保留"分析意图算子 + agent 契约"这一核心，
放松"代数纯粹性"带来的刚性，并把组织轴从"输出数据形状（family）"转为
"分析决策（question）"。它与现有 `docs/specs/analysis/python-analysis-design.md`
的若干明文定律存在冲突，冲突点在文末显式列出，需要 owner 决策后才能进入实现。

本文面向 Claude Code、Codex 等通用 agent 驱动的复杂互联网数据分析场景。

## 1. 背景与动机

现有 `python-analysis-design.md` 把分析能力建模成一套**封闭的、按 output family
组织的类型化代数**：

- 每个公开 core operator 有唯一 canonical output family（`observe→metric_frame`、
  `compare→delta_frame`、`decompose→attribution_frame` …）。
- family 内用封闭 shape 枚举（`MetricFrame[time_series]`、`CandidateSet[driver_axis]`）。
- shape / policy compatibility 在 compile / plan 阶段 gate。
- 自由表达（ibis / pandas）默认是 non-canonical scratch，必须经 `promote_*`
  才能重新进入 canonical 链路。

这套设计在两点上是成功的，应当保留：

1. **抽象层次选在"分析意图"上**（compare / decompose / discover / hypothesis_test
   / forecast），和 agent 从自然语言拆出的计划粒度对齐，缩短"问题→代码"的翻译距离。
2. **teach-by-error + typed ref + recommended_followups** 给 agent 一个 typed
   分支面，解决了 agent 最大的弱点：不知道下一步时会瞎猜。

但在面向**真实互联网分析的重尾分布**时，"代数纯粹性"带来的刚性是主要风险：

- **head/tail 断崖**：封闭算子集覆盖头部，长尾（cohort 留存、自定义归因窗漏斗、
  LTV、准实验、多触点归因）掉进 escape hatch；一旦掉进去，就丢掉了类型、治理、
  agent 契约——而这些正是这层抽象的价值来源。
- **概念表面积 vs agent 重试经济学错配**：compile 期 shape gate 是按"人类库使用者"
  成本模型设计的（怕 runtime 错、学一次长期受益）；agent 成本结构相反（每次学习/
  选择都花 token，重试近乎免费）。一个会教学的 runtime fail-closed 错误对 agent
  的价值约等于 compile gate，却不需要 agent 先掌握 family×shape×policy 这套格。
- **多维/层级分析在核心欠力**：单轴 `decompose`、无 nesting、无 country×platform
  联合归因——而"哪个 slice 在驱动指标"是互联网诊断的**头部**而非尾部需求。
- **"一算子一 family"作为硬律可能是错误的不变量**：真实分析迭代地从同一意图产出
  不同形状，设计只能用"family 内 shape + 例外 composite"（如 `auto_decompose`）
  打补丁；当需要越来越多 epicycle 维持一条美学定律时，往往说明定律太强。

## 2. 设计思想（五条原则）

本提案不更换抽象层次，而是**把刻度往回拨**：

1. **保留意图算子 + teach-by-error / typed-followup 契约。** 这是对的层、对的契约。
2. **把"一算子一 family + compile 期 shape gate"从硬律降为强默认。** 更多依赖会
   教学的 runtime fail-closed，更少依赖庞大的 compile 期 shape 类型系统。给 agent
   的应是"少数算子 + 极好的错误反馈"，而非"算子少但概念多"。
3. **把"可治理的自由表达"从二等 escape hatch 提升为一等共面。** 治理（lineage、
   口径漂移、join 安全）均匀附着到命名算子和自由表达两条路径；消除"掉出 canonical"
   的断崖；promotion 从显式必需降级为 point-of-use 推断 + 教学错误。
4. **把组织轴从"数据形状本体"换成"分析决策本体"。** 算子按分析问题命名：变了没
   (`observe`) → 变了多少 (`compare`) → 为什么 (`explain`) → 该看哪 (`scan`) →
   是真的吗 (`test`) → 会怎样 (`project`) → 信得过吗 (`audit`)。决策本体比形状本体
   稳定（数据形状千变万化，"为什么变"这个问题恒定）。
5. **把多维 / 层级归因放进核心，不是尾部。** 联合归因、nested drilldown、mix/rate
   拆解是一等能力。

价值重定位：**不是"每个分析都得是算子"，而是"分析上危险、手写易错的那几步
（对齐感知的 compare、带 mix/rate 的归因、带多重比较校正的检验、口径漂移检测）
做成算子来防 agent 踩坑"。代数不必对全部分析闭合。**

## 3. 目标落地形态

### 3.1 决策中心算子集

| 决策问题 | 算子 | 取代/合并现有 | 备注 |
| --- | --- | --- | --- |
| 现状是什么 | `session.observe` | `observe` | shape 运行时描述，不在签名里枚举 |
| 变了多少 | `session.compare` | `compare` | 对齐仍是 typed guardrail（危险步骤） |
| 为什么变 | `session.explain` | `decompose` + `discover.driver_axes` + `composites.auto_decompose` | **多维 joint+nested + mix/rate 进核心** |
| 该看哪里 | `session.scan` | `discover.<objective>` | 候选仍标记为"未证实" |
| 是真的吗 | `session.test` | `hypothesis_test` + `correlate` | 多重比较校正是算子职责 |
| 会怎样 | `session.project` | `forecast` | |
| 信得过吗 | `session.audit` | `assess_quality` | 对**所有** result 均一，含自由表达 |
| 自定义计算 | `session.derive` | `explore_ibis` / `from_pandas` + `promote_*` | 一等 governed result，无 promote 断崖 |

`transform`（filter/slice/rollup/topk/window/normalize/clean）保留为 family-preserving
改写，但其类型约束改为 runtime fail-closed（见 3.3）。

### 3.2 统一 result 契约

所有算子（含 `derive`）返回同一个 `AnalysisResult`，**用判别联合而非开放 `Any`**：

```text
AnalysisResult:
  kind: enum            # "metric" | "delta" | "explanation" | "candidates"
                        # | "verdict" | "projection" | "audit"
                        # 封闭枚举，保证可判别、无 Any
  value: <typed by kind>  # 每个 kind 对应一个具体 typed value（无 Any）
  schema: SchemaDescriptor  # 运行时自描述（列/嵌套/时间轴），供 agent 读取
  lineage: LineageSummary   # source artifacts/queries、cleaning steps、promotion refs
  issues: list[Issue]       # blocking / warning：coverage、drift、sample size、fanout
  followups: list[FollowupAction]  # typed 下一步，从真实 result state 生成
  cost: CostEstimate | None
  # 协议方法
  show() / render() / repr() / df()   # df() = governed pandas copy
```

与现设计的关键差异：
- `kind` 是**描述性判别标签**，不是"下游必须精确匹配的 family 类型"。下游算子接受
  宽输入，在 runtime 校验 `kind` + `schema` 是否满足，不满足则 fail-closed 教学。
- shape 不再是签名里的封闭泛型参数（不再有 `MetricFrame[time_series]` 这类
  compile 期 gate），而是 `schema` 里的运行时描述。
- `value` 仍按 `kind` 强类型，满足"public API 不返回 Any / 模糊类型"的约束。

### 3.3 runtime fail-closed 教学（替代 compile 期 shape gate）

形状不匹配的错误从 compile/plan 阶段移到 submit/run 阶段，但**必须教学**：

```text
ShapeUnsatisfiedError(
  operator="project",
  expected="time-indexed series (>= 2 time buckets)",
  received="scalar metric (schema={value: float})",
  repair="call observe(..., grain='day') to produce a time series before project()",
  candidates=[...],   # 来自真实 catalog / session 状态
)
```

理由：agent 重试近乎免费，runtime 教学错误对 agent 的有效性 ≈ compile gate，却
省去 agent 学习 family×shape×policy 笛卡尔格的成本。仍可保留**廉价的纯本地预检**
（不触数据），但它产出的也是同一形态的教学错误，而非一套独立的类型系统。

### 3.4 治理均一化（消除 canonical/scratch 断崖）

```text
# 自由表达是一等 governed result，不是 scratch
retention = session.derive(
    "ios_7d_retention",
    using=[cur],            # 声明 semantic provenance；用于 lineage 与 anchor 推断
    expr=lambda t, con: ...,  # ibis 表达式，受同一套受信任表达式校验
)
```

- `derive` 结果携带与命名算子**完全相同**的 `AnalysisResult` 契约（lineage / issues
  / followups / cost）。
- 没有显式 `promote_*` 调用。若 `derive` 结果被喂给需要 semantic anchor 而无法从
  `using=` 推断的算子，**由那个算子在 point-of-use 抛教学错误**，指明该补哪个 anchor。
  治理在使用点强制一次，而不是预先设卡。
- `cost_estimate`、`metric_definition_ref`、口径漂移检测同样附着到自由表达路径。

### 3.5 多维归因进核心

`explain` 原生支持：

- `mode="joint"`：跨多个 axis 的联合贡献分配（哪个 country×platform 组合贡献最大）。
- `mode="nested"`：层级 drilldown（某 country 内 platform 如何贡献），产出树状 value。
- `mode="joint+nested"`：两者组合。
- `effects=("mix","rate")`：mix（结构变化）vs rate（单位指标变化）分解内建。

输出 `kind="explanation"`，`value` 是 contribution tree（非扁平 frame），`followups`
指向 typed 下一步（test 顶部 driver、drilldown、scan outlier）。

## 4. 完整示例

```python
import marivo.analysis as mv

session = mv.session.get_or_create(name="dau_drop_2026w25")
dau = session.catalog.get("analytics.dau")

# WHAT
cur  = session.observe(dau, timescope=("2026-06-15", "2026-06-22"), grain="day")
base = session.observe(dau, timescope=("2026-06-08", "2026-06-15"), grain="day")

# HOW MUCH CHANGED — alignment stays a typed guardrail; ambiguity fails closed
# at runtime with a repair listing real calendars/modes.
delta = session.compare(cur, base, align=mv.calendar("company", mode="dow"))

# WHY — multi-axis joint + nested + mix/rate at the core (one operator).
why = session.explain(
    delta,
    axes=[session.catalog.get(a)
          for a in ("user.country", "user.platform", "acq.channel")],
    mode="joint+nested",
    effects=("mix", "rate"),
)
why.show()  # value is a contribution tree; followups are typed next actions

# WHERE ELSE TO LOOK
outliers = session.scan(cur, for_="cross_sectional_outliers",
                        axis=session.catalog.get("user.country"))

# IS IT REAL — multiple-comparison correction is the operator's job.
verdict = session.test(why.followups[0], correction="benjamini_hochberg")

# GOVERNED FREE EXPRESSION — co-equal, no scratch cliff, no promote_* call.
retention = session.derive(
    "ios_7d_retention",
    using=[cur],
    expr=lambda t, con: seven_day_retention(
        con.table("activity").filter(t.platform == "ios")
    ),
)

# Feed derived metric into the SAME intent operators. Missing anchors raise a
# point-of-use teaching error rather than requiring an upfront promotion step.
ret_delta = session.compare(retention,
                            session.observe_like(retention, base.timescope))

# CAN I TRUST IT — uniform across every result, including free expression.
for r in (delta, why, retention, ret_delta):
    issues = session.audit(r)
    if issues.blocking:
        issues.show()

# Agent drives the loop off typed followups, never free-text guesses.
result = session.run(why.followups[2])  # drill ios by app_version
```

## 5. 变更影响面

| 影响区域 | 文件 / 模块 | 变更内容 | 改动量 |
| --- | --- | --- | --- |
| 结果类型系统 | `marivo/analysis/frames/*`、result 协议 | 开放自描述 `AnalysisResult`（判别联合 `kind` + 运行时 `schema`）替换封闭 family×shape 枚举 | 大（核心重写） |
| 算子集重组 | session 方法、operator registry | `explain` 合并 decompose+discover.driver_axes+auto_decompose 并新增多维 joint+nested+mix/rate；`assess_quality→audit`；按决策动词重命名 | 大（含新分析能力） |
| 治理均一化 | escape-hatch 模块、`PromotionPolicy`、lineage | 取消 canonical/scratch 断崖；`derive`/`from_pandas` 携带完整契约；promotion→point-of-use 推断+教学错误 | 中-大（语义变化） |
| 横切元数据 | cost / lineage / definition-drift 管线 | 让成本、口径漂移、lineage 附着到自由表达路径 | 中 |
| 多维归因引擎 | planner / executor | 真正的 joint + nested + mix/rate（当前仅单轴）；树状 value | 大（新算法） |
| 校验器 | `marivo/semantic/validator.py` | `derive` 的 ibis 表达式体纳入同一套受信任表达式校验 | 小-中 |
| 文档 / 技能 / 示例 | `python-analysis-design.md`、`marivo/skills/marivo-analysis/**` | spec 多章重写；约 25+ 示例脚本与 cheatsheet/intents 表改写 | 大（量大但机械） |
| 快照 / 边界测试 | `__all__` snapshot、surface 测试 | 公开符号集变化→基线更新 | 中 |
| site 文档 | `site/src/content/docs/*/latest/`（中英双版） | 公共 API 示例同步 | 中 |

## 6. 与现有不变量的冲突（需 owner 决策）

本提案与 `agent-guide.md` / `python-analysis-design.md` 的明文定律冲突，是讨论的前置项：

1. **"每个公开 core operator 必须有唯一 canonical output family"** → 降为强默认。
2. **"One path per capability / `__all__` 由 snapshot pin 住"** → `explain` 合并多入口、
   `derive` 抬为一等路径，改变公共表面契约。
3. **"Public API 不得接受/返回 `Any` 或模糊类型"** → 用判别联合（封闭 `kind` 枚举 +
   各 kind 的 typed `value`）化解：形状在 `schema` 描述，但 `value` 仍具体类型。
   设计评审需确认这满足 typecheck 约束。
4. **canonical/scratch + 显式 promotion 模型** → 取消断崖是语义层面大改，需重新论证
   lineage / audit 完备性。

## 7. 渐进迁移路径（降低风险）

按风险从低到高，每步均可独立通过 `make test` / `make typecheck`：

1. **加 `explain` 多维归因**作为新 composite（不动现有 `decompose`），验证
   joint+nested+mix/rate 引擎价值。纯增量、零冲突、收益最直接，且不触 `__all__` 快照。
2. **统一 result 契约**：给现有所有 frame 补 `kind`/`schema`/`followups`/`df()`，
   暂保留 family 类型——让"决策中心 + 自描述"以非破坏方式落地。
3. **抹平自由表达断崖**：`derive` 携带完整治理契约；`promote_*` 从显式必需降级为
   point-of-use 推断 + 教学错误。
4. **最后动 spec 硬律**（一算子一 family、compile 期 shape gate），同步重写
   spec / skills / snapshot。放在有充分实测数据之后。

## 8. 待讨论的开放问题

- `kind` 枚举的最终边界：`explanation` 是否拆分 joint / nested / mix-rate 为子 kind？
- runtime 教学错误能否覆盖现 compile gate 拦下的全部错误类别？哪些必须保留本地预检？
- `derive` 的 anchor 推断规则：从 `using=` 能推断到什么程度，剩余靠 point-of-use？
- 决策动词命名（observe/compare/explain/scan/test/project/audit）是否需要保留旧名
  作为 deprecated alias 一个版本周期？
- 多维 joint 归因在 semantic layer 数据模型上的前置要求（当前 spec 把 country×platform
  列为"待 semantic layer 支持后设计"）。

## 9. 评审记录与裁决（discussion trail）

本节保留第一轮 code-grounded review 的反对意见、对应的代码事实核对结果，以及逐条
裁决。它是 v1→v2 收敛的决策依据，不删除以保留分歧轨迹。

| # | review 反对意见 | 代码事实核对 | 裁决 |
| --- | --- | --- | --- |
| 1 | `AnalysisResult` 不是简化而是弱化已有 typed frame contract | `marivo/analysis/frames/base.py` 的 `BaseFrameMeta` 已统一承载 `kind/ref/lineage/evidence_status/quality/blocking_issues/recommended_followups`；治理元数据不是缺口 | **接受**。统一 result 要么是空包一层，要么让下游宽收而削弱类型保护。改为：给现有 frame 增量补 `schema_descriptor()`，不替换 family。 |
| 2 | "agent retry 近乎免费"前提不成立；不应把 shape gate 推到 runtime | `marivo/analysis/intents/_shape.py` 已有纯本地、无 backend 的 shape 预测器（`observe_output_shape` / `compare_output_shape` / `attribution_output_shape`）；step 执行触发查询/持久化/cost | **接受**。保留 preflight/compile gate，只把错误消息升级成教学型。 |
| 3 | `derive` 一等化混淆"有 lineage"与"语义锚点可靠" | `marivo/analysis/escape_hatch.py` 的 `promote_metric_frame` 显式要求 `metric/semantic_kind/measure_column/semantic_model` 并 fail-closed；RFC 未定义 anchor 推断边界/信任模型，也未说明 `expr=lambda` 如何进入 `semantic/validator.py` | **接受**。`derive` 升级为 governed exploration，但保留显式/半显式 anchor，不取消 promotion 语义确认关。 |
| 4 | `explain` 合并 decompose+discover+auto_decompose 过大 | `marivo/analysis/intents/decompose.py` 的 `decompose` 是干净的 frame-local primitive（校验 axis 在已有列、gate fold） | **接受**。`explain` 先做 composite 实验，不替换 core taxonomy；primitive 保留。 |
| 5 | 多维归因进核心前算法 contract 不足 | RFC §3.5 只列 capability，无数学不变量/失败条件 | **接受**。v2 新增"算法不变量与 test oracle"要求（贡献守恒、基准选择、高基数、Simpson、缺失 denominator、层级不完备、多重比较），作为进 core 的前置门槛。 |
| 6 | 重命名（test/project/scan/audit）成本被低估且更模糊 | `marivo/analysis/__init__.py` 的 `__all__`、`tests/test_analysis_session_surface.py` 的 surface snapshot、`marivo-analysis` skill 锁定现算子名；现名语义更明确 | **接受**。取消动词重命名；`explain` 仅作为**新增**而非改名。补充：`__all__`/snapshot/skill 也是 agent 的先验，重命名作废这部分先验。 |

裁决结论：v1 正确识别了压力点（多维/层级归因、长尾自由计算、followup 经济学），
但药方（替换 family/shape 类型代数）证据不足、风险过大。收敛为"在现有代数上增量
加固 + `explain` composite 实验 + 先补 test oracle"，core spec 是否调整降级为**待
composite 用真实案例证明后再开的开放问题**。详见 v2。
