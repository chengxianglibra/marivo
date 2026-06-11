# 采样型半可加指标(Sampled Semi-Additive Metric)设计

- 日期:2026-06-11
- 状态:设计讨论已对齐,spec 待评审
- 范围:语义层声明 + 分析层两段式执行 + 覆盖率证据,分期实施

## 背景与动机

业务场景:设备每 5 分钟上报一条采样记录,包含该采样点的储备带宽(可提供的最大
带宽能力)、上行带宽(实际使用的带宽)与设备/用户/省份/ISP/渠道等维度。分析需要
在 hour / day / week 粒度下支持维度过滤、维度分组与无维度总体查询,产出:

- 储备带宽、上行带宽(周期代表值)
- 带宽平均利用率 = 周期内上行带宽代表值 / 周期内储备带宽代表值
- 带宽 P95 利用率 = 周期内 P95 上行带宽 / 周期内储备带宽代表值

这类指标是采样型半可加指标:同一采样点内可跨设备、用户、省份等空间维度求和,
跨时间不可直接求和,必须按声明的业务语义折叠。这是 Kimball 周期快照
(periodic snapshot)事实表的标准形态。

三个代码级证据说明当前能力缺口:

1. **`additivity` 已参与校验,但尚未驱动半可加执行语义**。base metric 必须
   声明三态 additivity(`marivo/semantic/validator.py` 的 assembly 校验、
   observe planner 的 `missing-additivity` 修复错误),`semi_additive` 还
   参与 `fanout_policy` 门控;但三态取值不改变任何聚合计划——半可加指标
   与可加指标生成完全相同的单相聚合。
2. **observe 是单相聚合,对半可加指标会静默算错**。
   `marivo/analysis/intents/observe.py` 的时间序列路径是:原始行打桶 →
   `group_by(bucket_start, dims)` → 直接套 metric body 的 reduction。一个
   5 分钟采样的带宽指标在 `grain="day"` 下会把 288 个采样点直接 sum——
   正是需求禁止的跨时间求和,当前没有任何机制拦截。
3. **全仓库没有 quantile / percentile 能力**,P95 是全新能力。

三块现成资产可直接复用:

- **动态子日粒度机制**:`grain=(5, "minute")` 已落地,含基础粒度规则与整除日
  规则,为"5 分钟采样点"提供声明锚点与校验先例。
- **derived metric 组件可比性强制**:derived observe 对每个 component 独立
  规划,fail-closed 强制相同维度、过滤、版本——分子分母对齐已被架构结构性保证。
- **时区与日历设计**(`docs/specs/analysis/timezone-and-calendar-design.md`,
  implemented):半开区间 `[start, end)`、单一系统时区、周一起始周。本设计
  不新增任何时间旋钮。

## 业界参照

借鉴原则,不引入新概念:

| 业界方案 | 借鉴的原则 |
|---|---|
| Kimball 周期快照事实表 | 半可加度量的时间折叠(avg/last/min)声明在度量上,永不推断;率与量是两个事实 |
| SSAS / Power BI 半可加聚合函数 | 折叠规则是指标定义的一部分,不是查询参数 |
| dbt MetricFlow | 度量声明不可加维度;ratio 指标由框架强制分子分母共享过滤与粒度 |
| PromQL 惯用法 | 先空间聚合再时间折叠(`sum by` → `avg_over_time` / `quantile_over_time`);分子分母分别聚合最后相除;P95 of P95 无效 |
| Druid / ClickHouse | percentile rollup 只能靠 sketch;不用 sketch 就必须从基础样本重算 |

## 目标

- 语义层可声明采样节奏与时间折叠,缺任一声明 fail closed,不产出看似有效的结果。
- 任意 hour / day / week 粒度、任意维度过滤/分组/无维度总体下,先在同一采样点内
  按查询维度完成空间聚合,再按声明折叠到目标粒度。
- P95 类折叠永远基于目标桶内的 5 分钟空间聚合序列直接计算;折叠结果一律不可
  二次 rollup,需要更粗粒度时从基础样本重算。
- 利用率类指标复用现有 `ms.derived_metric` + `ms.ratio`,分子分母按各自折叠
  独立计算后在相同 (桶, 维度组合) 上对齐相除;"设备级利用率简单平均"在该模型
  下不可表达。
- 每个结果桶输出实际样本数、期望样本数与覆盖状态;缺失采样点缺席不计入,
  绝不静默补 0。
- 给出率(带宽)与量(流量)分离的建模规范:additivity 按指标声明,不按数据集/列。

## 非目标

- fold 封闭集之外的任意时间聚合。`time_fold_expr` 扩展路径只定契约,本期不实现。
- 查询期分位参数(observe 时传 q)。quantile fold 的 q 是 authoring 声明
  常量,每指标一个;不同 q = 不同命名指标(P99 另立指标)。
- 由预聚合 P95(sketch 或数值)rollup 得到更粗粒度 P95。
- 非采样数据集(如日级快照表)上 semi_additive 指标的时间折叠。其现状行为
  不变,是已知缺口,需要单独设计。
- 自动推断储备带宽跨时间应该用 avg、last 还是 min。口径必须显式声明。
- 查询期 fold 覆盖(`observe(fold=...)` 之类)。fold 是业务口径,不是查询参数;
  不同折叠 = 不同命名指标。
- 多指标一步式 bundle observe。一致性由计划确定性 + 样本集 digest 保证(见
  分析层设计);bundle 是后续 composite operator 候选(准入理由:scan bundle
  一致性)。
- 上报单元级完整性(reporting-unit coverage)。本设计的覆盖率是时间槽
  覆盖率;判定"每个槽内设备是否报齐"需要期望群体 roster 的业务声明,属
  独立语义对象。**升级触发条件**:出现可声明的设备 roster 数据集与完整性
  口径需求。
- 时区、周起始新配置。沿用单一系统时区模型与周一起始周。

## 语义层设计

本节及指标族示例使用当前 HEAD authoring 签名(`entity=` / `entities=` /
`root_entity=`)。语义层 spec v1.1 的 dataset 系命名(`dataset=` /
`datasets=` / `root_dataset=`)落地时,本文示例随之机械替换;新增 kwarg
(`sample_interval`、`time_fold`、`fold_time_dimension`)不受该重命名影响。

### time_dimension 新增:采样节奏声明

```python
@ms.time_dimension(
    entity=bw_samples,
    data_type="timestamp",
    granularity="second",            # 列物理精度:上报时间戳抖动到秒
    timezone="UTC",                  # naive 列实存时区,沿用现有声明
    sample_interval=(5, "minute"),   # 新增:采样过程节奏
)
def sample_ts(bw_samples):
    return bw_samples.sample_ts
```

字段语义与约束:

- `sample_interval: tuple[int, str] | None = None`。声明该时间轴是周期采样点,
  归一化复用现有 `Grain` 机制。单位允许 `minute` / `hour`,必须整除一天
  (沿用 grain 整除日规则)。
- 仅当 `data_type` 为 `datetime` / `timestamp` 时合法,且列 `granularity`
  不得粗于 `sample_interval` 的单位;违例在 decorator-time fail closed。
- 与 `granularity` 正交,不重复:`granularity` 声明**列的物理精度**(既有
  字段,驱动解析、data_type 校验与既有粒度下界),`sample_interval` 声明
  **采样过程的节奏**(驱动采样点键、fold 门控、覆盖期望)。精度推不出
  节奏(事件轴有精度无周期),节奏也不替代精度(抖动列精度为 second、
  节奏为 5 分钟)。唯一交叠是有效查询粒度下界取二者更严者。
- 一个 dataset 可有多个 time field,`sample_interval` 按 time field 声明。
  折叠指标通过 `fold_time_dimension` 绑定其中一条 sampled 轴(见 metric 节);
  两段式执行由指标声明激活,不依赖 observe 调用方选轴。

`sample_interval` 不是抽象预留,它有四个承重消费方,且按 fail-closed 文化
只能声明、不能从数据推断:

1. **Phase A 采样点键**:`truncate(时间列, sample_interval)` 归一上报抖动
   (设备在 :00:03 上报不应碎成独立采样点),直接决定空间聚合正确性。
2. **时间轴语义标记**:它把"周期采样轴"与普通事件时间轴区分开,是
   `time_fold` 可声明性的门控——防止对事件轴做无意义的折叠。
3. **覆盖率期望分母**:期望样本数 = 桶时长 ÷ 采样间隔。
4. **查询粒度下界**:请求比采样间隔更细的 grain 走现有
   `GrainUnsupportedError`。

### metric 新增:时间折叠声明

```python
@ms.metric(
    entities=[bw_samples],
    additivity="semi_additive",
    time_fold="mean",                # 新增:跨时间折叠,封闭集
    # fold_time_dimension=sample_ts, # 新增:折叠所跨的时间轴;唯一 sampled 轴时可省略
    decomposition=ms.sum(),          # 描述采样点内空间聚合结构,现状不变
    unit="kbit/s",
    verification_mode="python_native",
)
def upstream_bw(bw_samples):
    return bw_samples.upstream_kbps.sum()   # body = 采样点内空间聚合
```

`time_fold` 是薄的封闭命名层。每个值只是一个规范 Ibis reduction 的名字,
规划器把它编译成对 Phase A 序列关系的标准归约——只有一个表达式层、一条执行
路径,枚举只做命名:

| `time_fold` | 编译目标(对 Phase A 序列 `s`,列 `(sample_point, value)`) | 业务语义 |
|---|---|---|
| `mean` | `s.value.mean()` | 周期平均(上行带宽默认口径) |
| `min` | `s.value.min()` | 最小值(保底储备) |
| `max` | `s.value.max()` | 峰值 |
| `last` | `s.value.argmax(s.sample_point)` | 期末值 |
| `first` | `s.value.argmin(s.sample_point)` | 期初值 |
| `("quantile", q)` | `s.value.quantile(q)` | 分位折叠;q 为声明期常量,如 0.95 / 0.99 / 0.5 |

`time_fold` 的类型形态:
`Literal["mean", "min", "max", "last", "first"] | tuple[Literal["quantile"], float]`。
quantile 的 tuple 简写镜像 grain `(count, unit)` 先例,IR 归一化为 typed
value,catalog 显示 `quantile(0.95)`。q 在授权时校验为 `(0, 1)` 开区间内
数值;q 是口径的一部分,每个指标一个(P99 另立指标),不是查询参数。
带一个受校验的标量参数不构成表达式语言——封闭的是 fold **家族**,与
`Grain(count, unit)`、`ms.ratio(...)` 带参数同构。

quantile 保留在封闭家族而非下放 `time_fold_expr` 的关键理由:**backend
capability 契约依赖 fold 身份可识别**——exact/approx 分类、方法披露、
ClickHouse reservoir 禁令(见分析层)都以"规划器知道这是分位折叠、q 是
多少"为前提;藏进表达式树就只能扫 AST 或放弃 fail-closed 能力门控。

绑定与约束规则(decorator / assembly-time fail closed):

- `time_fold` 要求 `additivity="semi_additive"`,并绑定一条 sampled 时间轴:
  新增可选 kwarg `fold_time_dimension`,取值为 root dataset 上声明了
  `sample_interval` 的 time_dimension ref(decorated ref 或 `ms.ref(...)`)。
  绑定轴是指标口径的一部分——半可加性是"相对某条轴"的陈述,折叠所跨的
  "时间"由绑定给出唯一物理定义(参照 MetricFlow `agg_time_dimension` /
  SSAS 半可加聚合绑定时间维度的先例)。
- `fold_time_dimension` 省略时:root dataset 恰有一条 sampled 时间轴则自动
  解析到它(镜像单 entity base metric 省略 `root_entity` 的先例);存在
  多条 sampled 轴时必填,缺失 fail closed。
- `additivity="semi_additive"` 的 base metric,若其 root dataset 存在
  `sample_interval` 时间轴,则 `time_fold` 必填(`missing_time_fold`)。
  非采样数据集上的 semi_additive 声明不受影响(见非目标)。
- derived metric 拒绝 `time_fold`(同 `fanout_policy` 的拒绝模式)。
- 封闭家族冻结。新增 fold 家族成员必须经 spec 修订,纪律等同
  `AlignmentPolicy.kind`;quantile 的 q 是成员内参数,不是新成员。

三条铁律:

1. **fold 是业务口径,不是查询参数**。需要另一种折叠就声明另一个命名指标
   (如 `reserved_bw_min`),与"折叠规则必须显式声明、不能隐式猜测"一致。
2. **body 表达空间口径,fold 表达时间口径,互不越界**。行级/空间复杂度归
   Ibis body(现状不变);`time_fold` 永远不承载表达式逻辑。
3. **unit 精确描述任意粒度下的产出值**。`mean` 折叠下产出仍是 `kbit/s`;
   这是把跨时间 sum 排除出带宽指标的量纲依据(见率与量)。

### `time_fold_expr` 扩展契约(本期不实现)

封闭集外的时间聚合不通过扩张 `time_fold` 表达,走受限 Ibis 表达式槽:

```python
# 示意:未来"去顶 5% 后取均值"口径——本期只定契约
time_fold_expr=lambda s: s.filter(s.value <= s.value.quantile(0.95)).value.mean()
```

- 函数体接收 Phase A 序列关系(逻辑列 `sample_point`、`value`,分组键由规划
  器持有),必须是单 return 的 Ibis reduction,过与 metric body 同一套 AST
  validator。
- 与 `time_fold` 互斥,二者同时声明 fail closed。
- 该扩展不破坏任何安全性质:规划器从不需要理解 fold 的内部语义——
  "always from base"规则(见分析层)使 fold 可合并性分类无关紧要。封闭集
  存在的理由只是目录可读性(catalog 直接显示 `time_fold: mean`)与授权流程
  可问答性(fold 选择映射为封闭选项的 AuthoringQuestion)。

### 率与量:同一物理列,两个指标

跨时间 sum 的业务语义真实存在,但它是另一个业务对象(累计使用量),不是把
带宽改成 additive 的理由:

```python
# 带宽:率,semi_additive,跨时间按声明折叠
@ms.metric(..., additivity="semi_additive", time_fold="mean", unit="kbit/s")
def upstream_bw(samples):
    return samples.upstream_kbps.sum()

# 累计流量:量,additive,间隔因子写进口径,单相路径,无 fold
@ms.metric(..., additivity="additive", decomposition=ms.sum(), unit="MiBy")
def upstream_volume(samples):
    return (samples.upstream_kbps * 300 / 8 / 1024).sum()
```

不允许一个 semantic id 承载两种折叠语义,依据三条:

- **量纲**:对 `kbit/s` 跨时间求和,产出既不是带宽也不直接是流量(需 ×
  采样间隔),unit 铁律下声明 additive 会让 unit 成为谎言。
- **口径单点声明**:"既可 avg 又可 sum"等于把口径选择推回查询期。
- **缺样语义不同**:`mean` 折叠对缺失采样点是"缺席不计入 + 覆盖率披露";
  sum 路径里缺失样本等效被当 0(少加即是少)。对带宽这被需求明令禁止;对
  流量指标"漏报 = 漏计量"是真实业务事实,只需覆盖率披露低估风险。

同理,`samples.upstream_kbps.quantile(0.95)` 作为单相 body 是合法 Ibis,但它
声明的是"设备×采样点原始记录分布的 P95"——另一个指标,不是本需求的 P95。
本需求的 P95 是对采样点内空间聚合后序列取分位,quantile 与 sum 不可交换,
且空间聚合的分组键 (采样点, 查询维度) 由查询在运行期决定,单 return body
无法内嵌自己的 `group_by`,结构上必须落在两段计划的第二段。

### 语义层失败语义

沿用结构化错误风格,kind 命名实施时与语义层错误目录对齐:

| 触发条件 | 错误 kind(语义层) |
|---|---|
| sampled 数据集上 semi_additive base metric 缺 `time_fold` | `missing_time_fold` |
| `time_fold` 值不在封闭家族,或 quantile 的 q ∉ (0, 1) | `invalid_time_fold` |
| `time_fold` 用于 additive / non_additive metric | `time_fold_requires_semi_additive` |
| `time_fold` 声明但 root dataset 无任何 sampled 时间轴 | `time_fold_requires_sampled_time_field` |
| 多条 sampled 时间轴且未显式 `fold_time_dimension` | `ambiguous_fold_time_dimension` |
| `fold_time_dimension` 引用非 root dataset 字段、非 time field 或无 `sample_interval` | `invalid_fold_time_dimension` |
| derived metric 声明 `time_fold` | 同 `fanout_policy` 拒绝模式 |
| `sample_interval` 不整除一天 / 单位非法 / data_type 不符 / granularity 粗于间隔 | `invalid_sample_interval` |

存量兼容:所有新行为由新声明(`sample_interval`、`time_fold`)opt-in 激活,
未声明的存量项目零破坏。语义真相源是 `.marivo/semantic/` Python 文件重执行,
IR 追加默认字段,直接构造 `MetricIR` 的存量测试零破坏。

### 读取面

- `MetricIR` 追加归一化后的 `time_fold` typed value(kind + 可选 q,
  frozen dataclass 默认 `None`)与解析后的 `fold_time_dimension`;
  time field 的 `time_meta` 追加 `sample_interval`。
- catalog `MetricDetails` 增加 `time_fold` 与解析后的 `fold_time_dimension`
  (与 `additivity`、`unit` 并列);`mv.help(ref)` 在有值时输出
  `time_fold: <value>` 与绑定轴行。
- richness:sampled 数据集上的 semi_additive metric 缺 `time_fold` 属
  readiness 硬错误(fail closed),不是 richness 建议项。
- `ms.help('metric')` / `ms.help('time_dimension')` 同步新 kwarg 与约束。

## 分析层设计

### 两段式 observe 计划

observe 规划器对声明了 `time_fold` 的指标生成嵌套聚合,时间语义固定在
绑定轴上,编译为单条下推 SQL,对 agent 透明:

```text
Phase A(空间,采样点内):
  window / 维度过滤
    → sample_point = truncate(时间列, sample_interval)   # 时区管线之后
    → group_by(sample_point, *查询维度)
    → 套 metric body(空间聚合)→ value

Phase B(时间,折叠到查询粒度):
  bucket_start = 系统时区切桶(sample_point, grain)        # 半开区间、周一起始
    → group_by(bucket_start, *查询维度)
    → 声明折叠(time_fold 编译目标) over (sample_point, value)
    → 同一 aggregate 内附带覆盖率辅助列(见覆盖率证据)
```

要点:

- 折叠指标的窗口过滤、Phase A 采样点、Phase B 切桶全部使用绑定轴
  (`fold_time_dimension`)。调用方未显式传时间轴时,绑定轴优先于 dataset
  的 `is_default`;显式传入其他时间轴 fail closed(见分析层失败语义)。
  `dt` 天分区轴与 sampled 轴并存的常见表结构下,这消除了"默认走 `dt`
  单相聚合"的隐性错误路径。分区协同裁剪属执行优化,不在本设计范围。
- 无 grain 的 scalar / segmented observe 同样两段式:Phase B 以整个请求
  窗口为单一折叠桶,逐维度组折叠;覆盖率期望值按窗口时长 ÷
  `sample_interval` 计算。
- 查询维度由运行期请求决定:按省份查则省份内空间 sum,无维度总体则全局 sum。
  "先空间后时间"对所有维度组合一致成立。
- `truncate` 到采样间隔归一上报抖动。同一 (设备, 采样槽) 重复上报会被空间
  聚合吸收并放大 sum——属数据质量问题,由 `assess_quality` 范畴披露,本设计
  不在执行路径上去重。
- 利用率零新机制:`ms.ratio` 的每个 component 独立走自己的两段计划(分子
  `quantile(0.95)` 折叠、分母按声明折叠),derived 可比性检查保证相同维度/过滤/
  版本,在相同 (桶, 维度组合) 上对齐相除。每桶语义即
  `P95利用率(bucket, dims) = quantile_0.95(上行序列|bucket,dims) / fold(储备序列|bucket,dims)`。

### Always-from-base 与不可再聚

- 任何粒度的请求都从 5 分钟基础样本重算,不存在"小时 P95 聚成天 P95"或
  "avg of avgs"的路径。
- 折叠产出的 `MetricFrame` 在 meta 标记不可再聚(`reaggregatable=False`)。
  `transform.rollup` 对此类 frame fail closed,repair payload 指向"按目标
  粒度重新 observe"。该标记机制是通用的;本设计为折叠 frame 设置,后续可
  推广到 raw-row quantile / count distinct 等非可加口径(已知既有缺口,
  另行处理)。
- `decompose` 边界:非线性折叠(`min` / `max` / `last` / `first` /
  `quantile`)的指标(或任一 derived component 非线性折叠)产生的
  DeltaFrame,沿空间维度的 sum 式归因数学上不成立(分段 P95 之和 ≠ 总体
  P95),`decompose` fail closed,repair 指向 panel observe + 逐 segment
  compare / `discover.interesting_slices`。`mean` 折叠允许 decompose,
  覆盖缺口导致的线性残差记 lineage warning。
- `compare` / `discover` / `forecast` / `correlate` 对折叠 frame 无特殊
  限制——它们消费的是普通 MetricFrame。fold 参与指标定义身份,跨定义版本
  比较由既有 `MetricDefinitionCompatibility` 机制处理。

### 缺样语义

- 缺失采样点缺席,不补零:`mean` 是对实际存在样本的均值;`last` 是桶内最大
  实际 `sample_point` 的值;`quantile` 对实际序列取分位。
- 缺失的信任度问题全部交给覆盖率证据披露,执行路径不做任何插补。需要插补时
  使用显式 `transform.impute_nulls`(既有算子,记录策略)。

### 覆盖率证据

覆盖率是**时间槽覆盖率**(time-slot coverage):度量"该有采样点的时间槽
是否有任意上报",不度量"每个槽内上报单元是否齐全"。每个 (桶, 维度组合)
计算:

| 字段 | 定义 |
|---|---|
| `actual_samples` | Phase A 输出在该组的行数,即有任意上报的采样槽数 |
| `expected_samples` | duration(桶 ∩ 请求窗口, 系统时区) ÷ `sample_interval`。半开区间使窗口截桶精确;DST 日(23/25 小时)自动算对 |
| `coverage_ratio` | actual / expected |
| `coverage_status` | `complete`(ratio = 1)/ `partial`(ratio < 1),客观两态,无阈值旋钮 |

边界与盲区(显式声明):Phase A 空间聚合吸收部分上报——某槽只要有任意
设备上报即计为覆盖。设备群体性漏报(如 100 台中 50 台静默)会压低指标值
而**不降低**时间槽覆盖率;`complete` 不证伪部分上报。上报单元级完整性
需要"期望上报群体"的业务定义(设备 roster 及其有效期),属独立语义对象,
列入非目标。当前建模指引:在同一数据集上声明伴生群体指标(如
`reporting_devices`,`additivity="non_additive"`,body =
`samples.device_id.nunique()`),与带宽指标同 scope 观测,群体跌落由 agent
经 compare / `discover.point_anomalies` 显式发现;同槽重复上报同理不在
执行路径去重,经 `assess_quality` 披露(见两段式要点)。

落点:

- 覆盖率不进主 `_df`(现行 `BaseFrame.to_pandas()` 直接返回 `_df.copy()`,
  不存在列隐藏机制),落为**链接式 sidecar frame**,完全镜像 derived
  components 机制:meta 增加 `coverage_ref`,`frame.coverage()` 按 ref
  加载逐桶覆盖率 frame(确定性 artifact id 兜底,丢失时结构化错误提示
  重跑 observe),与主 frame 同路径持久化。`frame.to_pandas()` 行为不变。
- `meta.quality` 增加覆盖汇总(最小/平均覆盖率、partial 桶数),沿
  "轻量 quality"既有边界。
- **阈值分层与自动告警本期不引入**:"最低可接受覆盖率"是尚未确认的业务
  口径(见待业务确认),业务未给口径前不造阈值旋钮。覆盖事实
  (actual / expected / ratio / status)+ `meta.quality` 汇总已满足
  "状态标记"要求,解读交给 agent 与显式 `assess_quality`——与
  `sample_size_low` 不发 C2 的既有判例同一立场。**升级触发条件**:业务
  确认阈值后,在 sampled 轴声明上承载(如 `min_sample_coverage`),同时
  引入 `insufficient` 分层与 warning 级 `BlockingIssue`。
- 覆盖率证据按 sampled 时间轴生效,对该数据集上**所有**指标产出,包括
  additive 的流量指标(漏报导致的低估不静默)。
- derived ratio 的覆盖率取 component 覆盖率的逐桶最小值。

### 多指标一致性

同一分析中并列请求储备、上行、P95、利用率时:

- derived 组件的一致性由既有可比性机制结构保证。
- 并列 base 指标以相同 (window, grain, dims, filters) observe 时计划是纯函数、
  确定性一致。frame meta 增加 `sample_set_digest`(root dataset id + 绑定
  时间轴 id + 解析后窗口 + 归一化过滤 + 维度字段 id + `sample_interval`
  的摘要),使"同一批过滤后的样本集合"可审计验证:digest 相同即样本集相同。

### quantile 后端落地:capability 契约

- **金标准数值语义**:对桶内序列做线性插值分位(SQL `percentile_cont` /
  R type-7)。测试以手算 type-7 值对账。
- **backend capability 注册表**(分析层静态注册,机制即契约):
  `backend_type → (下推聚合, quantile_mode, quantile_method)`。未注册
  backend 上执行 quantile 折叠 fail closed 为结构化 capability 错误
  (沿时区设计 backend capability gap 先例),不静默降级。
- v1 注册表(下推聚合的 Ibis 编译可达性属实施核实项;机制与披露字段是契约):

| backend_type | 下推聚合 | `quantile_mode` | `quantile_method` |
|---|---|---|---|
| `duckdb` | `quantile_cont(q)` | `exact` | `linear_interpolation`(与金标准一致) |
| `trino` | `approx_percentile(q)` | `approximate` | `qdigest` |
| `clickhouse` | `quantileExactInclusive(q)`;Ibis 编译不可达时注册回退 `quantile`(reservoir) | `exact`(回退时 `approximate`) | `linear_interpolation`(回退时 `reservoir_sampling`) |

- ClickHouse 默认 `quantile` 是 reservoir 采样且结果不确定,严禁登记为
  `exact`;采用回退注册时 mode/method 必须如实披露。
- 近似单遍计算仍满足"基于窗口内 5 分钟序列直接计算"(不是二次聚合),但
  必须披露:frame meta 记 `quantile_mode` 与 `quantile_method`,render
  标注,不静默。金标准对账只对 `exact` 后端承诺;`approximate` 后端测试
  只断言披露正确与序统计合理性(如 P95 ∈ [min, max]、单调于数据平移)。
- hour 粒度下每桶期望仅 12 个样本,P95 统计上接近 max。由覆盖率/样本数证据
  自然披露,不阻塞。

### 时间语义(全部继承,零新旋钮)

- 窗口半开区间 `[start, end)`;系统时区切桶;周一起始周。
- 采样时间戳列若为 naive timestamp,按既有契约在 time field 上声明
  `timezone=`(如 `"UTC"`)。
- 生产分析环境通过固定 `TZ`(如 `Asia/Shanghai`)获得可复现结果,CI 固定
  `TZ` 断言——均为时区设计已写明的实践。

### 分析层失败语义

| 触发条件 | 行为 |
|---|---|
| 请求 grain 细于 `sample_interval` | 既有 `GrainUnsupportedError`,reason 标注采样间隔下界 |
| sampled 数据集上 semi_additive 指标缺 `time_fold` 的时间序列 observe | fail closed,repair code `time-fold-required`(防御性兜底;authoring 层已拦截主路径) |
| observe 显式传入绑定轴(`fold_time_dimension`)之外的时间轴 | fail closed,repair:改用绑定轴,或为该轴另建指标 |
| `transform.rollup` 作用于 `reaggregatable=False` frame | fail closed,repair:按目标粒度重新 observe |
| `decompose` 作用于非线性折叠 DeltaFrame | fail closed,repair:panel observe + 逐 segment compare |

## 指标族示例(目标态 authoring 形态)

```python
# 采样事实表:一行 = 一台设备一个 5 分钟采样点
bw_samples = ms.entity(name="bw_samples", datasource=warehouse,
                       source=ms.table("device_bw_5min"),
                       primary_key=["device_id", "sample_ts"], ...)

@ms.time_dimension(entity=bw_samples, data_type="string",
                   granularity="day", date_format="%Y%m%d", is_default=True)
def dt(bw_samples):
    return bw_samples.dt                # 天分区轴:分区裁剪与可加指标默认轴

@ms.time_dimension(entity=bw_samples, data_type="timestamp",
                   granularity="second", timezone="UTC",
                   sample_interval=(5, "minute"))
def sample_ts(bw_samples):
    return bw_samples.sample_ts         # 唯一 sampled 轴:折叠指标自动绑定到它

@ms.metric(entities=[bw_samples], additivity="semi_additive",
           time_fold="mean", decomposition=ms.sum(), unit="kbit/s", ...)
def upstream_bw(bw_samples):
    return bw_samples.upstream_kbps.sum()

@ms.metric(entities=[bw_samples], additivity="semi_additive",
           time_fold=("quantile", 0.95), decomposition=ms.sum(), unit="kbit/s", ...)
def upstream_bw_p95(bw_samples):
    return bw_samples.upstream_kbps.sum()

@ms.metric(entities=[bw_samples], additivity="semi_additive",
           time_fold="mean",   # 业务口径待确认:mean / min / last
           decomposition=ms.sum(), unit="kbit/s", ...)
def reserved_bw(bw_samples):
    return bw_samples.reserved_kbps.sum()

@ms.metric(entities=[bw_samples], additivity="additive",
           decomposition=ms.sum(), unit="MiBy", ...)
def upstream_volume(bw_samples):
    return (bw_samples.upstream_kbps * 300 / 8 / 1024).sum()

avg_utilization = ms.derived_metric(
    name="avg_utilization", unit="1",
    decomposition=ms.ratio(numerator=upstream_bw, denominator=reserved_bw))

p95_utilization = ms.derived_metric(
    name="p95_utilization", unit="1",
    decomposition=ms.ratio(numerator=upstream_bw_p95, denominator=reserved_bw))
```

| 指标 | additivity | 时间语义 | unit |
|---|---|---|---|
| 上行带宽 | semi_additive | fold=mean | `kbit/s` |
| P95 上行带宽 | semi_additive | fold=quantile(0.95) | `kbit/s` |
| 储备带宽 | semi_additive | fold=业务声明 | `kbit/s` |
| 上行累计流量 | additive | sum(×300s 写进 body) | `MiBy` |
| 平均 / P95 利用率 | derived ratio | 继承组件 | `1` |

## 待业务确认口径

机制不阻塞于这些答案;确认后回填上方示例的声明值:

| 问题 | 建议口径 | 理由 |
|---|---|---|
| 储备带宽跨时间折叠 | `mean`;SLA/保底场景另声明 `reserved_bw_min` | Kimball 对容量类半可加度量的标准处理;与利用率分母对齐 |
| P95 利用率分母 | 同周期 `mean` 储备 | 窗口对齐、稳定、可解释;"P95 对应采样点的储备"在插值分位下定义不良;期末储备在储备趋势变化时有偏。若业务要"逐采样点利用率序列的 P95",那是另一指标(`time_fold_expr` 路径) |
| 最低可接受覆盖率 | 本期不引入阈值旋钮,覆盖状态为客观两态 | 业务口径未确认;确认后以 sampled 轴声明(`min_sample_coverage`)承载 `insufficient` 分层与告警(见覆盖率证据) |
| 周粒度 | ISO 周一起始(已落地行为) | 业务自定义周如有真实需求是独立 calendar 提案 |
| 时间桶时区 | 系统时区模型,生产固定 `TZ=Asia/Shanghai` | 已评审落地的架构决策,不为本需求开新旋钮 |

## 文档更新清单

| 文件 | 更新 |
|---|---|
| `docs/specs/semantic/python-semantic-layer.md` | time_dimension `sample_interval`;metric `time_fold` 封闭家族与绑定规则;率与量建模规则 |
| `docs/specs/analysis/python-analysis-operator-design.md` | observe 两段式计划;rollup / decompose 门控;grain 与采样间隔下界;quantile backend capability 注册表 |
| `docs/specs/analysis/python-track-evidence-surface.md` | `meta.quality` 覆盖汇总;阈值分层与自动 BlockingIssue 推迟(见覆盖率证据节) |
| `marivo-skills/marivo-semantic/references/authoring-patterns.md` | 采样表指标族模式:率/量分离、fold 选择、P95 声明 |
| `marivo-skills/marivo-analysis/references/` | 折叠 frame 的 coverage() 读取、不可再聚与重新 observe 模式 |
| semantic / analysis help 数据 | metric、time_dimension、observe、rollup 条目 |

## 测试策略

沿 `tests/conftest.py` / `tests/shared_fixtures.py` 共享 fixtures,新增
"5 分钟采样带宽表"DuckDB 模板(多设备 × 多省份 × 采样点,刻意留缺样与
窗口截桶场景),先窄后宽(`make test TESTS='...'` → `make test`):

1. authoring:`sample_interval` / `time_fold` 合法与各类 fail-closed;
   `fold_time_dimension` 自动解析(唯一 sampled 轴)/ 歧义必填 / 非法引用
   三态;存量 semi_additive(非采样)零影响。
2. 两段式正确性:hour/day/week × 有/无维度 × 过滤,手算金标准对账;
   mean/min/max/last/first 各 fold;week 直接从 5 分钟样本折叠(非 day 二次聚合);
   无 grain 的 scalar / segmented 全窗口折叠;`is_default` 为 `dt` 时折叠
   指标仍自动走绑定轴。
3. quantile:DuckDB 按 type-7 手算金标准对账(覆盖 q=0.5 / 0.95);capability 注册表三态
   (exact / approximate / 未注册 backend fail closed);`quantile_mode` /
   `quantile_method` 披露。
4. derived ratio:平均/P95 利用率分子分母对齐;分母口径切换只改一处声明;
   "设备级利用率平均"不可表达性(组件机制回归)。
5. 覆盖率:expected/actual/status 三态;窗口截桶;缺样不补零;群体性
   漏报不降低时间槽覆盖率(语义固定化断言)+ 伴生群体指标模式;
   `frame.coverage()` sidecar 加载与丢失时的结构化错误;
   阈值分层不在本期(两态状态断言);additive 流量指标同样产出。
6. 门控:rollup / decompose / grain 下界 / 显式非绑定轴 observe 各
   fail-closed 路径与 repair payload。
7. 一致性:相同 scope 多 observe 的 `sample_set_digest` 相等;不同过滤则不等。

## 实施分期与依赖

每期独立可验收,1+2 落地后 mean 类指标即正确可用:

1. **语义层声明**:`sample_interval` / `time_fold` +
   全部 authoring/assembly 校验 + IR/catalog/help 透出。
2. **两段式 observe**:规划器分支 + 嵌套聚合执行(mean/min/max/last/first)+
   观测期门控 + `reaggregatable` 标记 + rollup/decompose 门控 +
   `sample_set_digest`。
3. **quantile fold**:编译 + exact/approx 披露;derived ratio 接通 P95
   利用率(机制复用,测试验收)。
4. **覆盖率证据**:逐桶辅助列 + `frame.coverage()` + `meta.quality` 汇总 +
   BlockingIssue。
5. **文档与技能**:按文档更新清单同步,示例入 skills references。

前置依赖:metric unit 字段(已落地,`9ea332a4`)、动态子日粒度(已落地)、
时区与日历设计(implemented)。无并行在途冲突。
