# Marivo 时区与日历对齐总体设计

状态：implemented design。本文描述 Marivo Python 分析链路当前采用的单系统时区模型。
以下「当前实现摘要」反映已落地行为，设计原理与已知取舍保留不变。

本文回答四个问题：

- 系统时区 / 数据时区 / UTC 是什么关系。
- 时区支持哪些入口。
- 窗口过滤、bucket、相对时间（today/now）计算时怎么考虑时区。
- calendar 节假日数据如何与业务数据对齐。

## 核心决策：只有一个时区

Marivo 不维护多套时区概念。**全链路只有一个时区：运行 Python 进程那台机器的系统
时区（system tz）。** 它同时充当用户提问时的「报表时区」与解释数据列时间的默认时区，
两者合一。

由此推导出四条硬规则（本文其余部分都是它们的展开）：

1. 系统时区即报表时区，二者合一，**不提供任何项目级时区配置文件**。
2. `marivo` Python 库的 public 接口**不接受任何时区参数**，统一默认使用系统时区。
   **唯一例外**：语义层 `time_field` 可声明 `timezone=`，用于标注**无时区的
   datetime/timestamp 列**实际存的是哪个时区；缺省即系统时区（见 §列时区声明）。
3. `date` / `hour` 分区（以及其它 string / integer 墙上时间分区）**统一当作系统时区
   时间**处理。
4. calendar 里的时间，和 string 类型时间分区一样，**当作系统时区**处理。

这套模型用「一个全局约定 + 一个可选列级声明」换掉了「多级配置覆盖」的复杂度。除了
规则 2 那一个 `time_field.timezone` 声明位之外，没有其它时区旋钮。代价是：未声明
`timezone` 时，跨机器、跨时区运行同一份分析结果会不同（见 §已知取舍）。这是有意接受
的权衡。

### 列的三类时间语义

虽然只有一个时区，但物理列分三类，决定是否需要做时区换算：

| 列类别 | 包含的 data_type | 物理语义 | 处理方式 |
| --- | --- | --- | --- |
| 墙上时间列 (wall-clock) | `date`、string/integer 的 `yyyymmdd` / `yyyymmddhh` 等日期与小时分区 | 存的就是「某地墙上时间」，无偏移信息且无法重新解释 | **直接按系统时区解释**，窗口边界在同一墙上时间空间里匹配，不做任何换算 |
| 可声明 naive 列 | naive `datetime` / `timestamp`（无 tz 标签） | 是裸墙上字段，但「它代表哪个时区」可由 `time_field.timezone` 声明 | 把列按声明的 `timezone`（缺省系统时区）localize 成瞬时值后，与瞬时列同样处理 |
| 瞬时列 (instant) | tz-aware `timestamp`、`integer epoch_seconds` | 是一个绝对时刻，物理上以 UTC 锚定 | 窗口边界先按系统时区解释成墙上时间，再换算成 UTC 时刻去比较；bucket 时先转回系统时区再切 |

**为什么 naive 列需要声明**：naive `datetime` 不是一个 UTC long，它存的是一组不带偏移
的裸字段（`2025-12-31 22:00:00`）。「这列其实是 UTC」只活在写数据那个人脑子里，列类型
本身不携带。所以 Marivo 必须替它假设一个时区，而任何固定假设对另一半数据就是错的：存
UTC 的当系统时区会偏、存本地的当 UTC 会偏。`time_field.timezone` 把这个「猜」变成「声明」：
声明 `timezone="UTC"` 的 naive 列被正确当瞬时值处理，偏移消失。

**UTC 在本设计里只出现一个地方**：瞬时列（含已 localize 的 naive 列）天然以 UTC 锚定，
把「系统时区的墙上时间窗口」翻译成它们可比的绝对时刻时需要 UTC 作为中间量。纯墙上时间
列（date / 分区）全程不碰 UTC。

### 系统时区怎么解析（实现注意）

「系统时区」必须解析成带 DST 规则的 IANA 时区，而不是当前那一刻的固定偏移：

- `datetime.now().astimezone().tzinfo` 只给出**当前固定偏移**（`datetime.timezone`），
  丢掉了夏令时切换规则。跨 DST 的窗口/bucket 用它会算错。
- 目标态应解析系统 IANA 名（读 `TZ` 环境变量、`/etc/localtime` 符号链接，或等价
  机制），构造 `ZoneInfo(<iana_name>)`。无法解析出 IANA 名时退回固定偏移，并在 frame
  meta 标注 `tz_resolution="fixed_offset"`，提示跨 DST 结果可能不精确。
- 系统时区在 session 创建时解析一次并写入 session meta（审计用：可回看某次结果用了
  哪个时区），同一 session 内保持稳定。它是从系统派生的事实，不是用户可调旋钮。

## 入口契约：public 接口无时区参数

按规则 2，所有面向用户的入口都不接受时区参数，系统时区在内部统一解析：

- `session.start(...)` / `attach(...)` / `get_or_create(...)`：**移除 `timezone=` 形参**。
- observe / compare 的 `window=`：**移除 `tz` 字段**。窗口只表达边界与粒度，不带时区。
- `@ms.time_dimension(...)`：**新增可选 `timezone=` 字段**，且**只对无时区的
  `datetime` / `timestamp` 列有意义**（见 §列时区声明）。这是全 public surface 上唯一
  的时区入口；缺省即系统时区。
- `calendar`：见 §日历对齐，`Calendar` 模型**删除 `timezone` 字段**，节假日一律按
  系统时区解释。

内部仍保留一个 `Session.tz`（解析后的系统 `ZoneInfo`），供执行层与日历层共用，但它
不暴露为可设置入口。

## 列时区声明（time_field.timezone）

`time_field.timezone` 是本设计唯一的列级时区声明，专门解决 naive 列的固有歧义。

```python
@ms.time_dimension(
    dataset=orders,
    data_type="timestamp",
    granularity="hour",
    timezone="UTC",          # 该 naive 列实际存的是 UTC；不写则按系统时区
)
def created_at(orders):
    return orders.created_at
```

语义与约束：

- **适用范围**：仅 `data_type` 为 `datetime` / `timestamp` 且列**无 tz 标签**（naive）时
  生效。它声明的是「这列裸字段代表哪个时区」。
- **缺省**：省略时按系统时区 localize，等价于「naive 列即系统时区墙上时间」。
- **取值**：合法 IANA 名（如 `"UTC"`、`"Asia/Shanghai"`）。非法名 fail-closed
  （复用现有 `TimezoneInvalidError`）。
- **落地方式**：执行期把列按声明 tz localize 成瞬时值，再走瞬时列管线
  （边界换算、bucket）。声明 `"UTC"` 即让「naive 实存 UTC」的列被正确锚定，偏移消失。
- **对 tz-aware 列**：列已自带时区，`timezone=` 仅做一致性校验；声明与列固有 tz 冲突时
  fail-closed（`TimezoneInvalidError`，details 标注 declared vs actual）。
- **对 date / 分区列无意义**：`date` 与 string/integer 分区是纯墙上时间列，没有亚日精度
  可重新锚定。在这类字段上声明 `timezone=` 应 fail-closed，提示「date/分区列不支持
  timezone 声明，请改用系统时区约定或建成 tz-aware timestamp」。
- 该字段是 time_field 的物理 grounding，不是业务口径；它进 `time_meta`，不进 ai_context。

这条声明把「naive 偏移」从不可避免的宿命降为可消除：写数据的人知道列存什么时区，就在
time_field 上声明一次，跨机器结果即可复现。

## 当前实现摘要

- `Session.tz` 在 session 创建或加载时解析为系统时区，并写入 session meta 的 `tz`、`tz_resolution`、`tz_warning`。
- `session.*(timezone=...`\) 已移除；窗口模型不接受 `tz`。
- `@ms.time_dimension(timezone=...`\) 是唯一 public timezone 入口，仅适用于 naive `datetime` / `timestamp` 的物理列声明。
- `Calendar` 不含 timezone 字段，`.marivo/calendar/*.json` 出现 `timezone` 会被未知字段校验拒绝。


## 计算时怎么考虑

统一管线（取代「按 data_type 各猜一套」）：

```text
用户意图（系统时区的墙上时间窗口 / today / now）
   ↓ ① 解析：window 边界 → 系统时区半开区间 [start, end)
系统时区墙上时间区间
   ↓ ② 按列类别落地
       墙上时间列(date/分区)：在同一墙上时间空间直接匹配（不换算）
       可声明 naive 列：按 time_field.timezone（缺省系统时区）localize → 同瞬时列
       瞬时列：边界 → UTC 时刻 → 比较
   ↓ ③ 分桶：统一回系统时区切桶
typed frame
```

### 绝对窗口边界

给定系统时区区间 `[start, end)`（date-only 的 end 按半开区间取次日系统时区午夜）：

- 墙上时间 `date` / 天分区：在系统时区日期空间直接比较，半开区间靠次日边界。
- 墙上时间小时/字符串分区：边界按系统时区拼成分区字面量后比较。
- naive `timestamp` / `datetime`：列按 `time_field.timezone`（缺省系统时区）localize 成
  瞬时值；边界（系统时区墙上时间）→ UTC 时刻 → `col >= T_start & col < T_end`。
- 瞬时 tz-aware `timestamp`：边界（系统时区墙上时间）→ 系统时区 localize → UTC 时刻，
  同上比较。
- 瞬时 `epoch_seconds`：同上再转 epoch 秒。

半开区间语义（lower 闭、date-only upper 取次日开）统一到所有 data_type。

### 相对窗口与 now

- `today` / `yesterday` / `this X` / `last N X` / `xtd`：先在系统时区取 `now` 的本地
  日期，再纯日期展开（沿用现状算法，只是 tz 固定为系统时区）。
- `as_of` 缺省 = 系统时区的 `now`。
- `week_start` 现状硬编码周一；本设计不引入配置文件，故**保持周一硬编码**（如需可配置
  另行设计，但不通过时区配置承载）。

### 分桶

目标态：**所有 grain 都在系统时区下切桶**，消除现状「day 本地化、week+ 不本地化」。

- 墙上时间列（date / 分区）：列值已是系统时区，直接 `truncate(grain)` / `cast(date)`，
  无需换算。
- naive 列：先按 `time_field.timezone`（缺省系统时区）localize 成瞬时值，再走下一条。
- 瞬时列：优先用 backend 原生时区函数（如 DuckDB `date_trunc(..., timezone(...))`）把
  时刻转系统时区再切，正确处理 DST；backend 不支持时退回单一偏移近似，并在 frame meta
  标注 `bucket_tz_strategy="fixed_offset"` + DST 风险告警，不静默。

### 跨 backend

- 瞬时列的时区换算尽量下推到 backend SQL；Python 侧只做边界解析与字面量生成。
- backend 不支持 tz 函数时归类为结构化 capability 错误，与普通编译失败区分。

## date / hour 分区（规则 3）

`date` 与 `hour` 分区列（含 string / integer 的 `yyyymmdd`、`yyyymmddhh`、
`yyyymmdd-hh` 等）一律是墙上时间列，**按系统时区解释**：

- 窗口边界按系统时区拼成对应分区字面量，与列做字符串/整型比较。
- 不做 UTC 换算（它们本就没有偏移信息）。
- 现状代码对小时分区已是「按有效 tz 拼字面量、不转 UTC」，本规则把那个有效 tz 从
  默认 UTC 明确为系统时区，并去掉 `window.tz` 覆盖入口。

## 日历对齐（规则 4）

calendar 里的节假日时间和 string 时间分区同等对待，**按系统时区**：

- 节假日 ISO date 一律解释为**系统时区的本地日历日**。
- 对齐时数据时间列先分桶到系统时区本地日期，再与节假日比对。瞬时列按系统时区取本地
  日期，墙上时间列直接取其日期。
- **`Calendar` 模型删除 `timezone` 字段**（不考虑兼容与数据迁移）：`.marivo/calendar/*.json`
  不再接受 `timezone` 键，出现即按未知字段 fail-closed（沿用 `extra="forbid"`）。
  `CalendarInfo` 同步删除 `calendar_timezone` 字段。
- 删除后没有「日历时区 vs 系统时区」这一对概念，静默错位一天的冲突面从根上消失——节假日
  只有系统时区一种解释。

## 失败语义

沿用 `analysis/errors.py` 结构化错误风格。模型只有一个时区 + 一个可选列级声明，因此
**没有** `DataTimezoneUndeclared`（缺省即系统时区，永远不缺）这类「未声明」错误；但
`time_field.timezone` 的引入带来两个有限的「声明非法」冲突面。保留 / 关注：

| 触发条件 | 错误 / 行为 | 说明 |
| --- | --- | --- |
| 窗口边界非法 ISO 日期/时间 | `WindowBoundInvalid`（现有） | 不变 |
| 相对表达式输入 | 不支持；使用 `WindowInvalidError` 拒绝 | 当前 API 只接收显式 `timescope` / absolute window |
| `time_field.timezone` 非法 IANA 名 | `TimezoneInvalidError`（现有） | 复用窗口 tz 同款校验 |
| `time_field.timezone` 与 tz-aware 列固有时区冲突 | `TimezoneInvalidError`，details 标 declared vs actual | 列已自带时区，声明只能一致 |
| 在 date / 分区列上声明 `timezone=` | `TimezoneInvalidError`，hint「该 data_type 不支持 timezone 声明」 | 纯墙上时间列无亚日精度可锚定 |
| 系统时区无法解析出 IANA 名 | 退回固定偏移 + frame meta `tz_resolution=fixed_offset` | 不报错，但显式告警 |
| 瞬时/naive 列 bucket 跨 DST 且仅 fixed_offset 近似 | frame meta `bucket_tz_strategy=fixed_offset` 告警 | 不报错；需精确时换支持原生 tz 的 backend |

注意：`TimezoneInvalidError` 现有错误仍复用。移除 `window.tz` / `session.timezone=`
入口后，它的触发面收窄到两类：内部系统 tz 解析异常，以及 `time_field.timezone` 声明
非法/冲突/不适用。

## 迁移与兼容

分阶段，避免一次性破坏行为：

- Phase 1（默认翻转 + 去入口）：
  - 把 session 默认 tz 从 UTC 改为解析出的系统时区；修正 `session.start` docstring。
  - 移除 public 时区入口：`session.*(timezone=)`、`window["tz"]`。保留内部 `Session.tz`。
  - 系统时区按 IANA 解析（带固定偏移退路 + 告警）。
- Phase 2（统一管线 + 列声明）：
  - 执行期落地三分支（墙上时间列 / naive 列 / 瞬时列）。
  - time_field 新增 `timezone`：naive 列按声明（缺省系统时区）localize；声明
    `"UTC"` 即保留旧 naive 行为。tz-aware / date / 分区列上的非法声明 fail-closed。
  - bucket 全 grain 统一系统时区（修 week+），瞬时/naive 列优先 backend 原生 tz。
  - 日历对齐改为系统时区；**删除 `Calendar.timezone` 与 `CalendarInfo.calendar_timezone`**
    （不做兼容，带 `timezone` 键的日历文件直接报错）。
- Phase 3（清理）：
  - 移除残留 tz 相关参数与已失效的回显字段。

每阶段保持三条不变：系统时区是唯一报表时区、除 `time_field.timezone` 外 public 接口无
时区参数、纯墙上时间列（date / 分区）不碰 UTC。

## 已知取舍

- **跨机器不可复现**：结果依赖运行机器的系统时区，不同时区机器跑同一分析会不同。这是
  「不做配置、系统时区合一」的直接代价，已被接受。若未来需要可复现，应作为独立提案
  重新引入显式 tz，而不是悄悄加回配置。
- **naive 列偏移可声明消除**：很多数仓把 naive `timestamp` 实际存 UTC。缺省下本模型把
  naive 列当系统时区墙上时间，故未声明时这类列在非 UTC 机器上窗口/bucket 会偏一个系统
  偏移。解法是在 time_field 上声明 `timezone="UTC"`（或列的真实存储时区）——声明后该列
  按瞬时值正确锚定，跨机器复现。建议：凡是 naive datetime/timestamp 列都显式声明
  `timezone`，把缺省行为只留给「确实按本地墙上时间存」的列。
- **CI / 测试环境**：为结果稳定，CI 应固定 `TZ`（例如 `TZ=UTC` 或团队约定时区），否则
  测试断言会随 runner 时区漂移。

## 测试与维护

改时区行为后用仓库 entrypoints 验证，至少覆盖：

```bash
make test TESTS=tests/test_analysis_windows.py
make test TESTS=tests/test_analysis_executor.py
make test TESTS=tests/test_calendar_align.py
```

重点测试场景（统一在受控 `TZ` 下运行）：

- 同一窗口在不同系统时区下，墙上时间列 vs naive 列 vs 瞬时列的边界落点。
- naive 列声明 `timezone="UTC"` vs 缺省系统时区：同一份数据、同一窗口的命中差一个偏移。
- `time_field.timezone` 非法 / 与 tz-aware 列冲突 / 在 date 列上声明：三类 fail-closed。
- 跨 DST 的 day 与 week/month bucket 边界。
- `as_of` / `today` 在指定系统时区下的日期切换。
- 日历对齐在系统时区下的节假日命中；日历文件含 `timezone` 键时按未知字段 fail-closed。
- public 接口移除 `timezone=` / `window["tz"]` 后旧调用的报错或忽略行为。

维护规则：

- 文档里的 current-state 行为必须与 `marivo.analysis` / `marivo.semantic` 实际实现对齐；
  目标态能力明确标注为目标态。
- 示例不使用 bare `python` / `pytest` / `mypy` / `ruff`。
- 移除 public 时区参数等签名变化，需同步更新
  `marivo-skills/marivo-*/references/examples/` 下示例。
