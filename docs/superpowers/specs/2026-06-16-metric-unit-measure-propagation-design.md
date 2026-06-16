# Metric Unit 沿 additivity 模式扩展到 measure-dimension + loader 传播

- 日期:2026-06-16
- 状态:已评审,待实施
- 范围:语义层(IR + 授权 + loader 解析 + validator + richness + 授权 brief),一次实施
- 前置:`docs/superpowers/specs/2026-06-11-metric-unit-design.md`(metric 侧 unit 字段已落地)

## 背景与动机

`2026-06-11-metric-unit-design.md` 给 metric 加了可选 `unit`,语义为**作者声明**,并显式把两件事划为非目标:
unit 不进 `dimension(kind="measure")`;派生指标单位不做系统级自动推导。本设计在已出现的两类需求下
解除这两个限制,但**只做确定性传播,绝不启发式猜测**。

驱动证据:

1. **歧义的物理来源在列上,不在聚合上。** "`amount` 是分还是元、`latency` 是 ms 还是 s" 是物理列
   (measure dimension)的属性,与对它做 sum 还是 mean 无关。一个 measure 常喂多个 metric
   (`latency_ms` 上的 `sum`/`mean`/`p95`),单位应在 measure 上声明一次,避免在 N 个 metric 上重复并漂移。
2. **`additivity` 已是现成先例。** `additivity` 同样是"量在聚合下如何表现"的元数据,且 Marivo 已实现它的
   完整传播链:`DimensionIR.additivity` 声明 → `_resolve_tier1_additivity` 从 measure 推导 →
   `_resolve_derived_additivity` 沿组件 fixpoint 传播(`marivo/semantic/loader.py:325-388`)。
   `unit` 是 `additivity` 的结构性孪生,却一个传播都没做。本设计复用同一套脚手架。
3. **潜在 bug:tier-1 的 unit override 被丢弃。** `aggregate()` 接 `unit` kwarg 并 `_validate_unit`,
   但构造 `MetricIR` 时未传 `unit=unit`(`marivo/semantic/authoring.py:451-469`),override 静默失效。
   本设计的 tier-1 override 路径依赖它,顺手修。

## 字段必填性的根本判断:unit 全程可选,不必填

`additivity` 之所以"该有的地方必有"(tier-2 simple metric 强制声明、被 sum 的 measure 缺了报
`MISSING_MEASURE_ADDITIVITY`),是因为它**载重正确性**——不知道可加性就无法正确聚合 semi-additive 量。
`unit` 不具备这个性质:铁律"任何层不得依据 unit 做数值换算"意味着**有没有 unit,算出的数完全一样**。
因此:

- `unit` 在 measure-dimension 和 metric 上**都是可选**(`str | None = None`),`None` 永远合法。
- 缺失不阻塞 readiness,仅由 richness 建议(沿 `missing_unit` 现状)。
- 真歧义(分/元、分数/百分点)走既有 `amount_unit` 高危 AuthoringQuestion 问人,不许猜。

把 unit 做成必填会主动制造灾难:agent 在"判断不出"时被类型系统逼着填值,恰是单位错 100×、静默且
不可逆那类错误的温床。可选 + `None` 让"未知"能被诚实表达。

> 本设计从 `additivity` 借的是**传播机制**,不是**必填性**。

## 目标

- `DimensionIR(kind=MEASURE)` 获得可选 `unit`,作为单位的权威声明处。
- loader 在 load 时把 unit 从 measure 传播到 tier-1 metric、从组件传播到 derived metric,
  与 `_resolve_metric_additivity` 并列。
- 作者显式声明的 unit 永远赢(override-then-derive)。
- 传播只做确定性推导;无法确定时停在 `None`(不报错、不阻塞);唯一例外见下。
- `linear` 组件单位不可通约时报结构化校验错(unit 唯一不与 additivity 共享的部分)。

## 非目标

- 不做单位换算/归一(所有层只透传、只展示)——继承自 `2026-06-11` 铁律。
- 不构造复合单位(异单位 ratio 不生成 `CNY/{user}`,留 `None` 给作者显式声明)。
- 不做计数名词的单数化启发式(`count` 不把实体名 `orders` 推成 `{order}`)。
- `DimensionDetails` 不透出 unit(镜像 additivity:声明在 measure、消费在 metric)。
- richness 不设独立的"measure 缺 unit"项(metric 级 `missing_unit` 兜底)。
- 不做完整 UCUM 语法校验(沿用现有轻量 lint:非空、字符落在 `0x21–0x7E`)。
- unit 不必填、不进 readiness 硬门。

## 设计

### 1. 数据模型 + 传播语义

`unit` 与 `additivity` 在两个 IR 上并排,签名同款:

```python
# marivo/semantic/ir.py — DimensionIR(kind=MEASURE),权威声明处
additivity: Additivity | None = None
unit: str | None = None

def __post_init__(self) -> None:
    ...
    # mirror the additivity measure-only guard
    if self.unit is not None and self.kind is not DimensionKind.MEASURE:
        raise ValueError(
            f"DimensionIR {self.semantic_id!r}: unit is only valid on measure dimensions"
        )

# marivo/semantic/ir.py — MetricIR,作者声明覆盖,否则 loader 推导
additivity: Additivity | None = None
unit: str | None = None
```

传播语义 = `additivity` 的 override-then-derive 模型:

| 来源 | 规则 |
|---|---|
| 作者显式声明 unit | 永远赢,loader 不覆盖(`replace` 仅在 `unit is None` 时发生) |
| tier-1 未声明 | 从 `measure.unit` 按 agg 转换表推导 |
| tier-2(body)未声明 | 不推导(无 measure 可依);纯作者声明 |
| derived 未声明 | 沿组件 fixpoint 传播(ratio/weighted_average/linear 各自代数) |
| 任一环节缺料 | 停在 `None`,不报错、不阻塞 |

保留 override 而非"总是推导"的理由:派生指标偶尔需要作者钉一个展示单位(如某 ratio 以百分点 `%`
呈现而非推导出的 `1`)。`unit="%"` 一写,传播让路。这正是 additivity 也保留覆盖的原因。

### 2. agg→unit 转换表 + derived 代数

**tier-1(从 measure 推导)**——排除启发式后整张表塌缩成"保持 `measure.unit`,唯独 count 类 → `None`":

| aggregation | unit 结果 | 理由 |
|---|---|---|
| `sum` `min` `max` `mean` `median` `percentile` | `measure.unit`(透传) | U 的值做这些聚合仍是 U:sum(CNY)=CNY、p95(ms)=ms |
| `count` `count_distinct` | `None` | 计数在量纲上无量纲(`1`);携带信息的 `{order}` 注解内容相关且需单数化=启发式,不做 |

count 为何不给 `1`:在 Marivo 约定里 `1` 专指"无量纲分数,值 0–1"(ratio 原生产出分数)。一个 5000 的
订单计数硬标 `1` 会与 loss_rate 那类分数撞,在 display/evidence 里被误读为比值。故 `None`(诚实未标注),
作者想要时声明 `{order}`。

**derived(沿组件传播)**:

| 构造 | 规则 | 失败时 |
|---|---|---|
| `ratio(num, denom)` | 两边单位**已知且相等** → `1`(无量纲,约掉) | 其余一律 `None`(不构造复合单位) |
| `weighted_average(value, weight)` | `value.unit`(加权不改量纲;weight 单位无关) | `value.unit` 缺失 → `None` |
| `linear(terms)` | 所有项单位**已知且一致** → 该单位 | 任一项 `None` → `None`;**≥2 个互异已知单位 → 校验错** |

**原则性的不对称**(量纲分析的正确行为):

- `linear`(加减)要求**可通约**:`CNY + {order}` 无意义,是建模 bug → 报错。
- `ratio`(除)**不要求**:不同单位相除是合法速率(ARPU = CNY/{user})→ 不报错,只是不替你构造复合单位。
- `weighted_average` → `value.unit` → 永不因单位报错。

故**只有 linear 会因单位报错**。这是 unit 唯一不与 additivity 共享的语义(additivity 的 linear "混合"
合法,降级 non_additive 即可;unit 的 linear 单位混合非法)。

### 3. loader 解析

三个解析函数是 `_resolve_metric_additivity`(`marivo/semantic/loader.py:368`)的孪生。

**驱动函数**——关键是 `m.unit is None` 守卫(作者声明赢):

```python
def _resolve_metric_unit(registry: Registry) -> None:
    import dataclasses

    # Phase A: tier-1 simple metrics resolve from their measure dimension.
    for sid, m in list(registry.metrics.items()):
        if m.metric_type == "simple" and m.aggregation is not None and m.unit is None:
            resolved = _resolve_tier1_unit(m, registry)
            if resolved is not None:
                registry.metrics[sid] = dataclasses.replace(m, unit=resolved)

    # Phase B: derived metrics propagate from components (fixpoint over chains).
    for _ in range(len(registry.metrics) + 1):
        changed = False
        for sid, m in list(registry.metrics.items()):
            if m.metric_type == "derived" and m.unit is None:
                resolved = _resolve_derived_unit(m, registry)
                if resolved is not None:
                    registry.metrics[sid] = dataclasses.replace(m, unit=resolved)
                    changed = True
        if not changed:
            break
```

**tier-1 解析**:

```python
def _resolve_tier1_unit(metric: MetricIR, registry: Registry) -> str | None:
    measure = registry.fields.get(metric.measure or "")
    if measure is None:
        return None  # validator: UNKNOWN_MEASURE (existing rule, not new)
    agg = metric.aggregation
    agg_name = agg[0] if isinstance(agg, tuple) else agg
    if agg_name in ("count", "count_distinct"):
        return None  # count noun is content-specific; no heuristic, author declares {order}
    return getattr(measure, "unit", None)  # sum/min/max/mean/median/percentile preserve U
```

**derived 解析**:

```python
def _resolve_derived_unit(metric: MetricIR, registry: Registry) -> str | None:
    comp = metric.composition
    if isinstance(comp, RatioComposition):
        num = registry.metrics.get(comp.numerator)
        den = registry.metrics.get(comp.denominator)
        if num is None or den is None:
            return None
        if num.unit is not None and num.unit == den.unit:
            return "1"  # same unit cancels -> dimensionless
        return None  # differing/unknown -> no compound unit (non-goal)
    if isinstance(comp, WeightedAverageComposition):
        value = registry.metrics.get(comp.value)
        return value.unit if value is not None else None  # weighting preserves value's unit
    assert isinstance(comp, LinearComposition)
    units = []
    for term in comp.terms:
        dep = registry.metrics.get(term.metric)
        if dep is None:
            return None
        units.append(dep.unit)
    if any(u is None for u in units):
        return None  # known+None or all-None -> honest None
    distinct = set(units)
    return next(iter(distinct)) if len(distinct) == 1 else None
    #                              ^ >=2 distinct -> None; conflict reported by validator
```

**调用点**:`marivo/semantic/loader.py:461`,紧挨 `_resolve_metric_additivity(registry)`,二者独立无依赖。
resolution 在 validation 之前,故 validator 能看到已解析的组件单位。

### 4. validator 新增规则:linear 单位不可通约

resolver 对 linear 冲突只返回 `None`(沿用"resolver best-effort、validator 出结构化错"的分工,
与 `_resolve_tier1_additivity` 注释里"validator: INVALID_MEASURE_AGGREGATION"同款)。真正报错在
validator 新增一条规则,resolution 之后执行:

- 扫每个 `LinearComposition` 派生指标,收集组件**已知**单位;`≥2` 个互异 → 抛 `SemanticError`,
  模板携带:期望(可通约)/ 实得(如 `{gross: "CNY", refunds: "{order}"}`)/ 下一步(对齐组件单位或修模型)。
- **边界**:此检查只看 `comp.terms` 的单位,**与该 linear 指标自身是否 override 了 unit 无关**。
  作者把结果硬标成 `unit="CNY"` 也不豁免——`CNY + {order}` 的相加在量纲上依然非法,override 不能否决物理。
- 只在 `≥2` 个互异**已知**单位时报错;任一项 `None` 不算冲突(`None` 是缺失非冲突,报它等于变相必填)。
- **错误分类法(load 期 assembly 家族)**:新增 `ErrorKind.INCOMMENSURABLE_LINEAR_UNITS` +
  `ConstraintId.LINEAR_UNIT_COMMENSURABLE`(category `"assembly"`),与 `MEASURE_ADDITIVITY_REQUIRED` /
  `MEASURE_AGGREGATION_VALID` 同族(`marivo/semantic/constraints.py:62-63`、`errors.py:78-80`)。
  **不复用** `COMPOSITION_SHAPE`——后者是 `"decorator"` 类、只管"用了哪个 builder"的结构形状
  (`constraints.py:217`),而单位通约是 load 期量纲语义。新增枚举成员需同步更新 pinned 快照
  (`tests/test_semantic_imports.py:560,569`)。

### 5. 授权面

| 入口 | 改动 | unit 语义 |
|---|---|---|
| `ms.dimension(kind="measure")` | 新增 `unit=` kwarg + `_validate_unit`(**需泛化 label**:现写死 `"metric <id>:"`,`authoring.py:182`,加 object-kind 参数,measure 上报 `"measure dimension <id>:"`)+ measure-only 守卫(照搬 `authoring.py:697-703` 的 additivity 守卫)+ 传入 `DimensionIR.unit` | 权威声明处 |
| `ms.aggregate()`(tier-1) | 修 bug:补 `unit=unit`;docstring 改为"覆盖从 measure 推导的单位" | override(默认 derive) |
| `ms.simple_metric()`(tier-2) | docstring 澄清"无 measure 可推,此处即声明" | 纯声明(不 derive) |
| `ms.ratio` / `weighted_average` / `linear` | docstring 改为"覆盖从组件推导的单位" | override(默认 derive) |

### 6. 消费面

| 面 | 改动 |
|---|---|
| `MetricDetails.unit` | 已存在(`catalog.py:366`);loader 推导后覆盖更多指标,**零改动** |
| `DimensionDetails` | **不加 unit**——镜像 additivity(声明在 measure IR、消费在 metric)。catalog 覆盖测试逐字段校验,故 `allowed_internal[DimensionIR]` 需加 `"unit"`(与既有 `additivity` 并列,`tests/test_semantic_catalog.py:1476-1495`) |
| `mv.help(ref)` | 打印器 `getattr(ir, "unit", None)` 已通用(`analysis/help.py:956`);derive 后更多指标亮起,**零改动** |
| `richness.missing_unit` | tier-1-从已标注-measure 现在自动有值→不再误报;新增 **count 专属文案**;tier-1-measure-未标注时文案指向源头 measure(避免对同一根因双重报) |
| `DerivedMetricBrief.unit_hint` | 抽出共享代数 helper,**仅覆盖 ratio / weighted_average**——`prepare_derived_metric` 只接 numerator/denominator/weight(`prepare.py:181`),无 linear term 参数,故 linear 预览给 `None`(loader 在 load 期用真实 `LinearComposition.terms` 推导,不受影响)。可表示的构造上,预览**等于** loader 推导 |

### 7. 免费穿透红利(零代码、覆盖变广)

loader 把 tier-1/derived 的 unit 填上后,既有消费者自动拿到更多真值,无需改动:

- `marivo/analysis/evidence/seeding.py` 的 `payload.get("unit")`、`extract_delta_findings` 的 delta payload。
- `MetricFrame` / `DeltaFrame` 的 `render()` identity 行 `unit=<value>`。

## 关键约束 / 不变量

- 继承 `2026-06-11` 三条铁律:unit 精确描述指标产出值;任何层不得依据 unit 换算;`None` 永远合法。
- override 不豁免 linear 单位通约校验。
- `None` 是诚实缺失,不是冲突:除"≥2 互异已知 linear 单位"外,任何不确定都停在 `None`。
- 共享代数:`_resolve_derived_unit` 与 `unit_hint` 预览同源(限 ratio/weighted_average;linear 无 brief term 参数,预览 `None`),可表示处预览即真相。

## 测试策略

沿 `tests/conftest.py` / `tests/shared_fixtures.py` 共享 fixtures,先窄后宽。

1. **authoring**:`dimension()` 接受 measure 上的 unit、`_validate_unit` 拒绝非法值且 label 含 "measure dimension"、categorical/time 上传 unit 报 measure-only 守卫错;`aggregate(unit=...)` override 现在真正落到 IR。
2. **tier-1 解析**:`sum/min/max/mean/median/percentile` 透传 measure.unit;`count`/`count_distinct` → `None`;measure 未标注 → `None`;作者 override 赢。
3. **derived 解析**:ratio 同单位 → `1`、异/缺 → `None`;weighted_average → value.unit;linear 全同 → 该单位、含 `None` → `None`;fixpoint 跨链传播。
4. **validator**:linear `≥2` 互异已知单位报结构化错(`INCOMMENSURABLE_LINEAR_UNITS` / `LINEAR_UNIT_COMMENSURABLE`);override 不豁免;含 `None` 不报。
5. **catalog / help**:派生出的 metric unit 透出;`DimensionDetails` 无 unit 字段;`DimensionIR.unit` 进 `allowed_internal` 后覆盖测试通过。
6. **richness**:count 类产生专属文案;tier-1-measure-未标注的文案指向 measure。
7. **unit_hint**:ratio/weighted_average 预览与 `_resolve_derived_unit` 输出一致;linear 预览为 `None`。
8. **穿透**:derived metric 的 unit 进入 delta payload 与 frame render。
9. **枚举快照**:新增 ErrorKind/ConstraintId 进 `tests/test_semantic_imports.py` 的 pinned 集合。

## 文档更新清单

| 文件 | 更新 |
|---|---|
| `docs/specs/semantic/python-semantic-layer.md` | dimension `unit` 字段;metric unit 改为"derive + override"语义;agg→unit 表;derived 代数 |
| `marivo/skills/marivo-semantic/references/authoring-patterns.md` | 填写策略:单位声明在 measure、metric 上仅 override;count 显式声明 `{order}` |
| semantic help 数据 | `dimension` / `aggregate` / `ratio` / `weighted_average` / `linear` 的 unit kwarg 条目 |

## 实施时序与依赖

- 前置 `2026-06-11-metric-unit-design.md` 已落地(metric 侧 unit 字段存在)。
- 单计划顺序:IR 字段(+守卫)→ 授权(`dimension` kwarg + `_validate_unit` 泛化 + `aggregate` 修 bug)→
  loader 三解析函数 → validator 规则(新 ErrorKind/ConstraintId + 枚举快照)→ catalog `allowed_internal` →
  richness 文案 → `unit_hint` 共享代数 → 测试。
