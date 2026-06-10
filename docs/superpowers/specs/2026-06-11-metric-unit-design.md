# Metric Unit 字段设计

- 日期:2026-06-11
- 状态:已评审,待实施
- 范围:全特性(语义层 + 分析穿透),一次实施

## 背景与动机

`@ms.metric` / `ms.derived_metric` 目前没有单位概念。单位信息唯一的去处是自由文本,
现行 spec 的真实示例是把 `"Unit is seconds."` 写进 `ai_context.guardrails`
(`docs/specs/semantic/python-semantic-layer.md`)。

三个代码级证据说明结构化单位字段的缺位已经造成实际断点:

1. **evidence 层存在悬空消费者**。`marivo/analysis/evidence/seeding.py` 构造 change
   proposition 时写入 `"unit": payload.get("unit")`,但没有任何上游 finding 往 delta
   payload 里放 `unit`,该槽位自设计起恒为 `None`。
2. **授权流程已把单位歧义定为高危决策**。`marivo/semantic/classifier.py` 中
   `amount_unit` 是独立决策类型,materiality floor 为 `high`(`is_dangerous() == True`)。
   系统会强制澄清"金额是分还是元",但答案没有结构化落点,只能退化为 guardrail 散文,
   机器消费者用不上。
3. **报告层在重复断言本应可推导的信息**。`report_html_adapter.py` 的
   `value_format`(currency/percent/number/compact)由报告作者逐列声明,语义层
   无法提供依据。

## 目标

- metric 获得可选的、机器可读的单位声明,语义为**作者声明**(非系统推断)。
- 单位贯通到 agent 消费路径:catalog details、describe、`mv.help(ref)`。
- 单位贯通到机器消费路径:delta finding payload → evidence proposition。
- agent 授权时有明确的填写策略:显式证据才填,歧义走既有 `amount_unit` 问人流程。

## 非目标

- 不做任何单位换算或归一(所有层只透传、只展示)。
- 不做派生指标单位的系统级自动推导(IR 不自动填;agent 起草声明时可据
  结构推导建议 `1`,见填写策略)。
- 不做跨 metric 单位一致性校验(compare 不校验单位)。
- 不做完整 UCUM 语法校验(仅轻量 lint,见下)。
- `dimension(kind="measure")` 不加 unit(必要性证据均在 metric 侧;日后可按同模式扩展)。
- 报告层不做 unit → format 自动推导。`ReportMetric.format` 默认 `"compact"` 且报告
  spec 由 agent 编写,作者经 `mv.help(metric)` 已可见 unit;自动推导是低收益高耦合。
  **升级触发条件**:出现非 agent 报告作者时再立项。
- 不引入 `unit_kind` 闭集枚举。UCUM 串可解析,日后需要 kind 可机械推导。
  **升级触发条件**:出现跨 metric 单位运算需求(换算、校验、correlate 轴标注聚合)。

## 字段语义

`@ms.metric` 与 `ms.derived_metric` 新增可选 kwarg,落到 IR:

```python
# authoring kwarg(两个声明入口一致)
unit: str | None = None

# marivo/semantic/ir.py MetricIR 末尾追加(frozen dataclass,带默认值)
unit: str | None = None
```

三条铁律:

1. **unit 精确描述指标产出值本身**。指标算出 `0.898` 就声明 `"1"`(UCUM 无量纲),
   算出 `89.8` 才声明 `"%"`。ratio 分解原生产出分数,因此 ratio 派生指标的默认
   正确声明是 `"1"`。
2. **任何层不得依据 unit 做数值换算**。报告层 percent 格式现行注释明确"值即百分点、
   不乘 100",与本约定严格对齐。
3. **`None` 永远合法**。unit 是可选元数据,缺失不是错误,不阻塞 readiness,
   仅由 richness 建议。

存量兼容:语义真相源是 `.marivo/semantic/` Python 文件重执行,IR 不落盘,
追加默认字段对存量项目与直接构造 `MetricIR` 的测试零破坏。

## 取值约定:UCUM(case-sensitive)+ 一条显式扩展

采用 UCUM c/s 词汇,与 OpenTelemetry 的单位约定一致:

| 类别 | 写法 | 示例 |
|---|---|---|
| 时间 | UCUM 码 | `s`、`ms`、`min`、`h`、`d` |
| 字节 | UCUM 码 | `By`、`KiBy`、`MiBy` |
| 百分比 | UCUM 码 | `%`(值为百分点,如 `89.8`) |
| 无量纲分数 | UCUM 码 | `1`(值为 0–1,如 `0.898`) |
| 计数名词 | UCUM annotation,英文单数 | `{order}`、`{user}`、`{pageview}` |
| 复合 / 比率 | UCUM `/` 组合 | `By/s`、`{order}/d`、`CNY/{user}` |
| 货币(**本设计的显式扩展**) | 裸 ISO 4217 大写三字码 | `CNY`、`USD` |

货币扩展的理由:UCUM 与 OTel 均无货币约定,而货币是业务指标第一大单位类别。
裸三字码比 UCUM 注解 `{CNY}` 更可读,且与 ISO 4217 一一对应。

**轻量 lint**(授权时校验,违例抛 `SemanticDecoratorError`,沿用现有 kwarg 校验风格):
非空,且每个字符落在 `0x21–0x7E`(可打印 ASCII 且排除空白)。不解析 UCUM 语法。

## 授权面

- `marivo/semantic/authoring.py`:`metric()` 与 `derived_metric()` 加 kwarg,
  docstring 增补参数说明(含 UCUM 约定简表与示例)。
- semantic help 数据同步(`ms.help('metric')` / `ms.help('derived_metric')`)。
- classifier / ledger / assess_authoring 流程零改动:`amount_unit` 决策类型已存在,
  本设计只是给答案一个结构化落点。

### Agent 填写策略

写入 `marivo-skills/marivo-semantic/references/authoring-patterns.md`(不进 SKILL.md):

**显式证据才填**:
- 列名后缀:`_cents`、`_usd`、`_ms`、`_pct`
- 列注释写明单位(`inspect_source_context` 携带 comments)
- `source_sql` 换算痕迹(如 `/100` 即分转元的强信号)
- count 类指标:名词 = entity(count over orders → `{order}`)
- ratio / weighted_average 派生:结构推导 → `1`

**歧义不填,留 `None` 并走既有 `amount_unit` 高危 AuthoringQuestion**:
- 金额尺度(`19900` 是 ¥199.00 还是 ¥19,900,样本值域真歧义)
- 分数 vs 百分点(`0.85` vs `85`)
- 多币种表(`amount` + `currency_code` 列并存时,该 metric 无常量单位,
  除非建模时归一;agent 能发现问题但不得推断答案)
- 时长 `ms` vs `s` 无显式证据时

推断只是起草手段,字段语义是作者声明;人答后由 agent 回填声明。

## 读取面

**进**:

- `marivo/semantic/catalog.py` `MetricDetails` 增加 `unit: str | None`
  (与 `additivity` 并列;`SemanticObject` 顶层不加,遵循 additivity 先例)。
- 公开读取面以 catalog `details()` 与 `mv.help(ref)` 为准。实施核实:
  `project.describe` 已在公开面收紧(`7f557499`)中移除,spec 里的 describe
  字段清单属遗留描述,本特性不扩展它。
- `mv.help(ref)` 语义对象帮助(`marivo/analysis/help.py` 打印器)在有值时输出
  `unit: <value>` 行——agent 消费单位的主路径。
- richness 增加 `missing_unit` 建议项,照抄 `missing_guardrails` 模式:纯建议、
  不阻塞、不进 readiness 硬门。对所有缺 unit 的 metric 统一提示(UCUM 下任何
  指标都有可写单位),hint 文案为英文(代码内字符串)。

**不进**:

- `project.list_metrics()` 的 `MetricSummary` 不加 unit:列表面保持精简,
  单位不是选型依据,细节经 `describe` / `details()` 一步可达。

## 分析穿透

1. **delta finding payload 盖戳**:observe / compare / decompose 发射 delta 类
   finding(`scalar_delta`、`segmented_delta`、`time_series_delta`、`panel_delta`)时,
   payload 统一加 `"unit"`(`str | None`),取值为**该 finding 的 subject 指标**
   对应 `MetricIR.unit`(decompose 的组件级 finding 取组件指标的 unit)。
   `seeding.py` 的 `payload.get("unit")` 即刻收到真值,seeding 侧零改动;
   unit 为 `None` 时行为与现状完全一致。实施核实:当前唯一的 delta finding
   发射点是 compare 的 commit 路径(`extract_delta_findings`,scalar 与
   segmented 两分支);契约覆盖全部四种 delta kind,未来新增发射点自动继承。
2. **frame 渲染**:`MetricFrame.render()` identity 行在有值时显示 `unit=<value>`
   (`marivo/analysis/frames/base.py`),纯展示。
3. **报告层零代码改动**:在 analysis 侧报告指引文档加一条
   "报告的 format 与单位后缀从 metric 的 unit 取材(经 `mv.help` / `describe`)"。

## 文档更新清单

| 文件 | 更新 |
|---|---|
| `docs/specs/semantic/python-semantic-layer.md` | metric 声明字段表、UCUM 取值约定节 |
| `docs/specs/analysis/python-track-evidence-surface.md` | delta finding payload 的 `unit` 字段说明 |
| `marivo-skills/marivo-semantic/references/authoring-patterns.md` | agent 填写策略(上节全文) |
| `marivo-skills/marivo-analysis/references/final-report.md` | 报告 format 与单位后缀从 metric 的 unit 取材一句 |
| semantic help 数据 | metric / derived_metric 条目 |

## 测试策略

沿 `tests/conftest.py` / `tests/shared_fixtures.py` 共享 fixtures,先窄后宽
(`make test TESTS='...'` → `make test`):

1. authoring:接受合法 unit、默认 `None`、lint 拒绝(空串、含空白、非 ASCII)。
2. catalog / help:unit 透出;无值时不输出该行。
3. richness:缺 unit 的 metric 产生 `missing_unit` 建议。
4. intents:observe / compare / decompose 三处 delta payload 携带 `unit`。
5. seeding:change proposition payload 的 `unit` 不再恒 `None`。
6. frame render:identity 行显示 `unit=<value>`;无值时不显示。

## 实施时序与依赖

- 一次实施(语义层 + 分析穿透同一计划)。
- 前置:observe / compare / decompose 的组件列改名已随 `45cb9957` 落地,
  本特性在其上开工。
