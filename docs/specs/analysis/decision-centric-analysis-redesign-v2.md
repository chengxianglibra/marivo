# Analysis Algebra Incremental Hardening (v2, convergent)

状态：proposal / RFC，供讨论，**不表示已被接受**。本文是
[`decision-centric-analysis-redesign.md`](./decision-centric-analysis-redesign.md)（v1）
经一轮 code-grounded review 后的收敛修订。与 v1 不同，v2 **不替换**现有的
fixed-family + typed-frame 公共契约，也**不重命名** core 算子；它在现有代数上做
增量加固，并把"是否调整 core taxonomy"降级为需要先用真实案例证明的开放问题。

v1 的评审记录（反对意见 + 代码核对 + 裁决）保留在 v1 §9，作为本次收敛的决策依据。

## 1. 共识与分歧

v1 与 review 的共识：以下是真实压力点，需要解决。

- **多维 / 层级归因**：当前 `decompose` 是单轴 frame-local primitive，缺 joint /
  nested / mix-rate。
- **长尾自由计算**：cohort 留存、自定义归因窗、LTV 等掉进 escape hatch 后丢失契约。
- **followup 经济学**：agent 需要 typed 分支面，而非自由文本猜测下一步。

分歧只在药方。v1 主张替换类型代数；review 主张在现有代数上增量加固。**经代码核对，
证据支持 review。** 关键事实：

- `marivo/analysis/frames/base.py` 的 `BaseFrameMeta` 已统一承载
  `kind / ref / lineage / evidence_status / quality / blocking_issues /
  recommended_followups` —— 治理元数据不是缺口。
- `marivo/analysis/intents/_shape.py` 已有纯本地、无 backend 的 shape 预测器 ——
  preflight gate 已存在，不需要把校验推迟到 runtime。
- `marivo/analysis/escape_hatch.py` 的 `promote_metric_frame` 已做显式 anchor +
  missing-field 校验 —— promotion 是有意的语义确认关。
- `marivo/analysis/intents/decompose.py` 的 `decompose` 是干净 primitive —— 有独立
  价值，不应被 mega-operator 吞掉。

## 2. 设计原则（收敛版）

1. **保留 fixed family + typed frame 作为公共契约。** 不替换为运行时解释的统一
   result，不动 `__all__` / surface snapshot / skill 锁定的算子名。
2. **增量补 result 自描述。** 给每个 frame 增加 `schema_descriptor()`，让 agent 能
   运行时读取列 / 嵌套 / 时间轴结构，但**不改变**其 family 类型。
3. **保留 preflight gate，只升级错误消息。** 复用 `_shape.py` 的纯本地预测器，把
   shape mismatch 的报错统一成教学型（expected / received / repair / candidates），
   不把校验推迟到 submit/run。
4. **新增 `explain` 作为 composite 实验，不替换 core。** 承接 axis discovery +
   joint/nested + mix-rate + cost/stopping，先证明价值，再讨论是否进 core。
5. **`derive` 升级为 governed exploration，保留显式/半显式 anchor。** 不取消
   promotion 这道语义确认关。
6. **多维归因进核心前必须先有可测试的算法不变量与 test oracle。**

价值定位（不变）：**把分析上危险、手写易错的步骤做成算子来防 agent 踩坑；代数不必
对全部分析闭合。**

## 3. 目标落地形态

### 3.1 result 自描述（增量，不替换 family）

给 `BaseFrame` 增加一个只读投影方法，**不引入新 result 类型、不改 family**：

```text
frame.schema_descriptor() -> SchemaDescriptor
  SchemaDescriptor:
    kind: str                 # 复用 BaseFrameMeta.kind，不新增枚举
    semantic_shape: str        # scalar | time_series | segmented | panel（来自现有 meta）
    columns: list[ColumnSpec]  # name, dtype, role(time|dimension|measure|delta|...)
    nesting: NestingSpec | None  # 仅 explain 树状结果使用
    time_axis: TimeAxisSpec | None
```

- `SchemaDescriptor` 是 projection（读视图），**不进入 canonical artifact 链路**，
  不 seed evidence，与现有 `summary()` / `preview()` 同级。
- `kind` / `semantic_shape` 直接读现有 `BaseFrameMeta`，不另立平行类型系统。
- 满足"public API 不返回 Any / 模糊类型"：`SchemaDescriptor` 全字段具体类型。

下游算子签名**保持现状**（仍接受 `DeltaFrame` 等具体类型）；`schema_descriptor()`
只是给 agent 一个统一的运行时自检入口，不改变类型保护。

### 3.2 统一教学型错误（保留 preflight）

不移动 gate 位置，只统一错误形态。现有 `_shape.py` 预测器 + 各 intent 的 guard
继续在提交前拦截；报错升级为：

```text
ShapeUnsatisfiedError(
  operator="forecast",
  expected="time_series | panel (>= 2 time buckets)",
  received="scalar (schema_descriptor.semantic_shape='scalar')",
  repair="observe(..., grain='day') first to produce a time series",
  candidates=[...],   # 来自真实 catalog / session 状态
)
```

要求：所有 intent 的 shape/kind guard 复用同一个错误基类与字段
（expected / received / repair / candidates），消息从真实状态生成，不硬编码。
这是**消息层统一**，不是把校验推迟到 runtime。

### 3.3 `explain` 作为 composite 实验

```python
why = session.explain(
    delta,
    axes=[session.catalog.get(a) for a in (...)],
    mode="joint+nested",        # single | joint | nested | joint+nested
    effects=("mix", "rate"),
    budget=mv.cost_budget(max_axes=3, max_cardinality=200),  # stopping 条件
)
```

- 输出归入**现有 family**：`mode` 不涉及 nesting 时复用 `AttributionFrame`；nested
  树状结果先作为 `explain` 专属 result-family 实验（命名 / 协议沿用现有 frame family
  规范，纳入 family registry 审批），而不是新建一套 result 抽象。
- `explain` 内部**复用** core `decompose` primitive（不吞掉它）：axis discovery +
  排序 + 逐轴 `decompose` + 组合，全部走现有 step DAG 与 lineage。
- `decompose` / `discover.driver_axes` 等 core 算子**保持不变**，`explain` 是其上的
  composite，contract_level 标 `exploratory`，直到 §3.5 的不变量被满足才考虑 canonical。

### 3.4 `derive`：governed exploration，保留 anchor

```python
retention = session.derive(
    "ios_7d_retention",
    using=[cur],
    expr=lambda t, con: ...,    # 受 semantic/validator.py 受信任表达式体系约束
    anchors=mv.anchors(metric=..., semantic_kind="segmented",
                       measure_column="value", semantic_model="sales"),
)
```

- `derive` 结果携带完整 lineage / cost / issues（治理附着到自由表达路径）——这是相对
  现状的增量收益。
- 但 **anchor 仍显式/半显式**：`anchors=` 可缺省到 `using=` 能可靠推断的部分，**不能
  推断的部分必须显式提供**，否则在 point-of-use 抛教学错误。明确定义：
  - 可推断：time_axis、semantic_kind（从 `using=` 源 frame 继承）。
  - 必须显式：metric 身份、measure_column、semantic_model（涉及语义确认，不猜）。
- `expr=` 进入与现有 promotion 相同的 catalog / schema / axis 校验路径，不绕过
  `semantic/validator.py`。
- 结论：`derive` 是"更好用的 governed exploration"，**不取消 promotion 语义关**。

### 3.5 多维归因的算法不变量（进 core 的前置门槛）

`explain` 在升级为 canonical core 之前，必须先落成可测试的不变量与失败条件：

| 维度 | 不变量 / 失败条件 | test oracle 形态 |
| --- | --- | --- |
| 贡献守恒 | Σ(贡献) == 总 delta（容差内）；mix+rate 分解和守恒 | 合成数据精确等式断言 |
| 基准选择 | 显式声明 base period / base mix；不同基准产出可复现且标注 | 固定基准回归测试 |
| 高基数爆炸 | 超 `budget.max_cardinality` 时 fail-closed 或显式 topk + residual 桶 | 大基数输入触发 BlockingIssue |
| Simpson's paradox | nested 与 joint 结论冲突时显式标注，不静默选一边 | 构造反转案例断言 warning |
| 缺失 denominator | ratio_mix / weighted_mix 缺分母时 fail-closed | 缺列输入断言教学错误 |
| 层级不完备 | nested 轴存在缺失父级时标注覆盖率，不静默补零 | 缺层输入断言 coverage 字段 |
| 多重比较 | `explain`→`test` 链路传播比较次数，供 `hypothesis_test` 校正 | followup 携带 n_comparisons |

未满足以上全部，`explain` 只能停留在 `exploratory` composite，不进 canonical evidence
链路。

## 4. 完整示例（收敛版）

```python
import marivo.analysis as mv

session = mv.session.get_or_create(name="dau_drop_2026w25")
dau = session.catalog.get("analytics.dau")

# core 算子名保持不变
cur  = session.observe(dau, timescope={"start": "2026-06-15", "end": "2026-06-22"}, grain="day")
base = session.observe(dau, timescope={"start": "2026-06-08", "end": "2026-06-15"}, grain="day")

# 运行时自检：增量新增，不改类型
print(cur.schema_descriptor().semantic_shape)   # "time_series"

# preflight 仍在提交前拦截；报错为统一教学型
delta = session.compare(cur, base, alignment=mv.dow_aligned(calendar=mv.CalendarRef("company")))

# explain 作为 composite 实验，内部复用 decompose primitive
why = session.explain(
    delta,
    axes=[session.catalog.get(a) for a in
          ("sales.orders.country", "sales.orders.platform", "sales.orders.channel")],
    mode="joint+nested",
    effects=("mix", "rate"),
    budget=mv.cost_budget(max_axes=3, max_cardinality=200),
)
why.show()   # value 归入现有/实验 family；followups 为 typed 下一步

# core decompose primitive 保持可单独使用，未被吞掉
country_only = session.decompose(delta, axis=session.catalog.get("sales.orders.country"))

# derive：governed exploration，anchor 显式
retention = session.derive(
    "ios_7d_retention",
    using=[cur],
    expr=lambda t, con: seven_day_retention(con.table("activity").filter(t.platform == "ios")),
    anchors=mv.anchors(metric=session.catalog.get("analytics.retention_7d"),
                       semantic_kind="segmented", measure_column="value",
                       semantic_model="analytics"),
)

# 既有 assess_quality 名称保持，不重命名为 audit
for frame in (delta, why, retention, country_only):
    report = session.assess_quality(frame)
    if report.blocking_issues:
        report.show()

# 多重比较：explain→test 传播比较次数
verdict = session.hypothesis_test(
    cur, base, hypothesis="mean_changed",
    sampling=mv.SamplingPolicy(unit="day", method="paired_numeric_summary",
                               pairing="window_bucket", null_handling="drop_pair"),
)
```

## 5. 变更影响面（收敛后显著缩小）

| 影响区域 | 文件 / 模块 | 变更内容 | 改动量 |
| --- | --- | --- | --- |
| result 自描述 | `marivo/analysis/frames/base.py` + 各 frame | 新增 `schema_descriptor()` projection，复用现有 meta；**不改 family / 不改下游签名** | 小-中 |
| 教学错误统一 | `marivo/analysis/errors.py`、各 intent guard、`_shape.py` | 统一 `ShapeUnsatisfiedError` 字段；guard 复用同一基类；**gate 位置不变** | 中 |
| `explain` composite | 新增 `marivo/analysis/intents/explain.py` + 树状 result 实验 | axis discovery + joint/nested + mix-rate + budget；内部复用 `decompose` | 大（新分析能力，但隔离在 composite） |
| 算法不变量 + oracle | 新增测试 + 合成 fixture | §3.5 全部不变量的 test oracle | 中-大（先行） |
| `derive` 加固 | `marivo/analysis/escape_hatch.py` | governed exploration + lineage/cost/issues；保留显式 anchor 与 validator 校验 | 中 |
| 文档 / 技能 | 本 spec、`python-analysis-design.md`（增补 explain composite 节）、`marivo-analysis` skill / cheatsheet（新增 explain，不改旧名） | 增量补充，不重写核心 taxonomy | 中 |
| 快照 / surface 测试 | `tests/test_analysis_session_surface.py`、`__init__.py` `__all__` | 仅新增 `explain` / `derive` 入口；**现有算子名不变** | 小 |

对照 v1：取消了"结果类型系统重写""算子重命名""core taxonomy 替换""canonical/scratch
断崖移除"四项高风险改动，影响面从全面重写降为隔离的增量加固。

## 6. 与现有不变量的关系（收敛后基本无冲突）

- **"每个公开 core operator 唯一 canonical output family"**：保持。`explain` 以
  `exploratory` composite 出现，输出归入现有或经审批的实验 family。
- **"One path per capability / `__all__` snapshot"**：保持。仅新增入口，旧名不动。
- **"public API 不返回 Any / 模糊类型"**：保持。`SchemaDescriptor` / `explain` result
  全字段具体类型。
- **canonical/scratch + 显式 promotion**：保持。`derive` 不取消 anchor 确认关。

唯一需要 owner 拍板的开放项：是否允许 `explain` 的 nested 树状结果作为一个**新的实验
result-family** 进入 family registry（需走现有 registry 审批与 protocol 规范）。

## 7. 迁移路径（每步可独立通过 make test / typecheck）

0. **（P0，激活已有底座）** 填充 frame `content_hash` + `execution_status` + dispatch
   前 cache_hit 短路（§9.5）；失败步返回 `FailedStep` 而非抛异常。这两项让
   write-run-read 在多轮累积脚本下经济成立，是后续一切的前提。
1. **`schema_descriptor()` + 统一教学错误**（最低风险，纯增量，不触 snapshot 语义）。
2. **`explain` 作为 `exploratory` composite**，内部复用 `decompose`；先只支持
   `mode="single"`/`"nested"`，joint / mix-rate 随 §3.5 oracle 落地逐步开启。
3. **§3.5 算法不变量 + test oracle 补齐**，作为 `explain` 任何 canonical 化的前置门槛。
4. **`derive` 加固**为 governed exploration（保留 anchor）。
5. **（开放，gated on evidence）** 仅当 `explain` composite 用真实案例证明价值且
   oracle 全绿后，才讨论是否把 `explain` 提升为 canonical core 算子、以及是否调整
   core taxonomy。**默认不做。**

## 8. 待讨论的开放问题

- nested 树状结果是否值得新建实验 result-family，还是压平进 `AttributionFrame` 的
  multi-level 表示？
- `explain` 的 axis discovery 默认策略（variance_explained 等）是否复用现有
  `discover.driver_axes` 实现，避免两套候选逻辑？
- `derive` 可推断 anchor 的精确边界（time_axis / semantic_kind 可推断，metric /
  measure_column / semantic_model 必须显式）是否需要写成 typed policy？
- 多重比较次数在 `explain`→`hypothesis_test` 链路的传播协议（放 followup params 还是
  result metadata）。
- mix/rate 分解在 derived metric（ratio / weighted_average）上的守恒定义与 v1 §3.5
  缺 denominator 失败条件如何对齐。

## 9. Agent-Loop 驱动的 frame/result 与 agent-facing surface

本节把 frame/result 设计放回 agent 的真实运行模式——**写分析脚本 → 执行 → 读结果**，
且一个 analysis 跨**多个 loop 轮次**——据此重画 frame/result 的职责与暴露面。它细化
§3.1–§3.2，并修正一处现有越界（`recommended_followups`）。

### 9.1 multi-round write-run-read 的五条硬约束

| # | 约束 | loop 真实情形 | 不满足的后果 |
| --- | --- | --- | --- |
| 1 | 重算安全 | 每轮重跑一个**累积脚本** | 执行非纯函数 → 每轮重查、成本爆炸、非确定 |
| 2 | 冷启动重建 | 轮次 N+1 可能是被压缩/新开 context，内存对象丢失 | 只能重跑脚本才知道 frame 是什么 |
| 3 | 读经济学 | 每次读 frame 耗 context token | 反复读大 frame → context 爆炸 |
| 4 | 失败可续 | 多步脚本第 k 步失败，前 k-1 步已物化 | 抛异常 → 整脚本重跑、上游白算 |
| 5 | 决策状态可读 | 一个 analysis = 跨多轮的多 frame，无对象"是"该 analysis | 每轮开头重读一堆 frame 才知道在哪 |

重构判断：**当前 frame 被设计成"一次函数调用的返回值（配好元数据）"；loop 模式要求
它是"持久、可重算、可冷启动重建、可渐进披露的分析 DAG 节点"。**

现状底座（已具备）：`get_frame(ref)` 跨脚本恢复、`frame_summaries()`、`recent_jobs()`、
不可变 frame、`BaseFrameMeta`、`next_intents()`。
关键 gap：`session/_runtime.py` 持久化 frame 时 `content_hash=None`、ref 走 job 派生
——**执行未按内容 memoize**（store 已有 `content_hash` 列但对 frame 闲置）。

### 9.2 计算 vs 判断边界（marivo 只是计算库，无推理能力）

frame/result 只能暴露**库能确定性产出**的东西；一切 salience / 排序 / 推荐 / 结论 /
下一步叙事 / 何时停止都是 **agent 的判断**，库不得产出。

| 库能做（确定性，可由类型/规则/固定算法导出） | 只有 agent 能做（判断） |
| --- | --- |
| 枚举类型合法的下一步算子（`next_intents`） | 决定走哪一步 |
| 标注每个下一步的必填输入 + 前置条件（pass/fail） | 判断候选是否"真有意义" |
| 固定算法算分数/候选（zscore/mad/贡献） | 选哪个 objective / 阈值 / 轴 |
| 机械可解析的 param 预填（可选 axis = catalog refs） | 填判断 param（选哪个 axis） |
| 报告算了什么（事实/统计） | 写结论 / headline / 下一步叙事 |

直接后果：

- **`recap()` 不新增。** 它的增量内容（headline / working_conclusion / "下一步")全是
  判断；剥掉后退化为 `summary() + next_intents() + blocking_issues`，纯冗余。保留单一
  事实摘要 `summary()`，按需补确定性字段（`execution_status` / `freshness` / coverage）。
- **repr 行只放确定性描述符**（ref、kind、execution_status、固定规则取的 total/pct），
  不叫 "headline"、不暗示"这是结论"。
- **`recommended_followups` 越界，需降级**（见 9.3）。这同时修正现有
  `BaseFrameMeta.recommended_followups` 与 spec candidate schema 的同类越界。

### 9.3 `available_next`：affordance 取代 recommended_followups

把"推荐下一步"替换为**非解释性的 affordance 枚举**：

```text
AvailableIntent:                       # 库枚举，无 rank、无 "recommended"
  operator: 算子 id                    # 类型合法（计算）
  required_inputs: typed ref[]          # 缺什么（计算）
  preconditions: [(check, pass|fail)]   # 规则判定，如 ">=2 buckets": fail
  param_template: { 机械槽: 已填, 判断槽: <blank, 待 agent> }
```

库说"这些是门、每扇要什么、哪扇现在打不开"；agent 决定开哪扇并填判断槽。
`session.run(intent)` 消费的是 **agent 把判断槽填满后的** affordance，而非库递来的
"已决定动作"——读→写机械化，但"决定"明确归 agent。

### 9.4 agent-facing surface：recede 而非 delete

目标是降 **agent 要学的面**，不是降**类型面**。二者对 agent 价值不同：精确 nominal
result 类型主要服务静态类型检查器与库内部正确性，而 **agent 不在自己生成的脚本上跑
mypy**，所以 nominal 类型对 agent 直接价值低。结论：让 nominal 类型**退居幕后**，不是
合并删除。

- **不**把 frame 合并成 kind+可选字段的 mega-class。agent-guide 明文："prefer
  closed, kind-dispatched variants over optional-field mega-classes: precise types
  fail loudly, optional-field unions fail silently."mega-class 赔掉 fail-loud。
- **统一句柄靠子类型免费获得**：所有 frame 已 IS-A `BaseFrame`；agent 始终把结果当
  `BaseFrame` + affordance 用，无需 import `AttributionFrame` 等。
- **affordance 是建在类型之上的**：`available_next` 必须知道 kind 才能枚举合法算子；
  弱化底层类型会反噬 affordance 引擎。
- 落地三步（均不弱化类型）：
  1. 定 `BaseFrame` + affordance 为 agent 唯一要学的协议（`kind` /
     `schema_descriptor()` / `summary()` / `available_next()` / `run()`）。
  2. `help()` / discovery 走 base + affordance 为主路径，per-family help 降为 on-demand。
  3. 算子签名保持精确（`compare(MetricFrame, MetricFrame)`），库内部 / preflight /
     evidence / snapshot 继续吃精确类型。

净效果：算子动词不变（~8）、result 变体不变（fail-loud）、**agent 要学的面缩成
1 个协议 + affordance**、串联由 affordance 引导。**统一 result 类型不会减少算子暴露面**
（算子才是 agent 写的 API）。

### 9.5 frame/result 的 loop 属性（增量加在 BaseFrame/session）

| 属性 | 约束 | 内容 | 优先级 |
| --- | --- | --- | --- |
| 内容寻址 memoize | 1 | 填充闲置 `content_hash`：`fingerprint = hash(operator, 输入 fingerprints, 解析 params, semantic_version)`；dispatch 前查表命中即返回，不碰 backend；暴露 `execution_status ∈ {materialized, cache_hit, recomputed, stale_recompute, failed, partial}` | **P0** |
| 失败作为 frame | 4 | 执行返回已完成步 frame + 失败步 `FailedStep`（teaching error 的 expected/received/repair + 已物化上游 refs + 待修 param）；下一轮上游 `cache_hit`，只重算失败步 | **P0** |
| 自描述持久 frame | 2 | 持久化 frame 时一并存确定性决策上下文（question_answered、schema_descriptor、`available_next`、blocking、quality verdict）；`get_frame(ref)` 冷启动 rehydrate 无重查 | P1 |
| 分层 read 协议 | 3 | `repr`(1 行描述符) → `summary()`(事实) → `preview()` → `to_pandas()`；确定性描述符物化时算好持久化，agent 靠 repr+summary 导航，按需下钻 | P1 |
| `Analysis`(thread) 对象 | 5 | frame-like，**严格两分**：库算的事实（steps / refs / `execution_status` / 类型合法 frontier / blocking）vs **agent-authored 注释槽**（结论 / 排除理由，明确标注非库产出）。`recap(budget)` 只投影事实部分 + agent 注释，不产出新判断 | P1 |
| staleness / superseded | 跨轮正确性 | `superseded_by` ref（同问题被后续不同 param 重算）、`freshness`（相对输入/数据/定义版本） | P2 |

P0 两项本质是**激活已有底座**（填 `content_hash` + 失败不抛异常），非新建抽象，风险最低。
