# Lazy Analysis DSL：执行、保存与结果交接

Date: 2026-10-14

Status: proposed v0.25；尚未实施，不构成当前 API、后端能力或 Runtime 验收声明。
本文承接 [DSL 定义](lazy-analysis-dsl-redesign.md)，拥有执行图、保存/恢复、Agent 披露和 pandas 导出布局。
参数与算子语义由 DSL 定义拥有；设计取舍与验收见 [场景验证](lazy-analysis-dsl-scenario-validation.md)。
当前实现仍由 [Python Analysis Design](python-analysis-design.md) 和
[Session State and Runtime](session-state-and-runtime.md) 约束。

## 1. 执行、保留与恢复

### 1.1 图与来源分类

复用当前定义图、compiler、方法注册、物化、原子发布和Store，不建第二Runtime。
私有节点区分 Domain 的来源身份选择/显式读取/坐标运算、来源规约、小型 Metric 组合/Over、Observe、
结果选择/对齐/比较/最小规约/粒度变换、Values 与 FixedLeaf；不增加独立成员结果或关系运输节点。
来源Ibis在合法schema/关系绑定后lower；不要求Metric Column、虚拟共同关系或全Ibis图编码。
规范化定义、受限authoring描述、规则版本/依赖和必要状态保存，不pickle callable/Ibis/Catalog。

| 依赖 | execute |
| --- | --- |
| 有来源 | 新明确求值，不用旧同定义结果代替 |
| 只有固定/常量 | 消费精确Artifact，可精确复用相同输入/方法/请求 |
| 固定成员/域/参照加新来源 | 明确stage，新读取不刷新固定主输入 |

混合图是当前Runtime尚未实现的提案变更，须有具体lowering、资源、检查和发布路线。
固定域可用参数化完整元组关系或合格本地关联；不支持就报能力缺口，
不回退全源扫描、来源写入或另一算法。动态域若无法覆盖本次分类按缺键失败，
不称跨读不一致，不补域或删贡献。
reduce/top及自动 share 消费各自节点的同一实际输入，不能分别重跑来源拼接；
独立Metric根仍不要求共同读取时点。

#### 1.1.1 共享逻辑节点与固定输入

共享非持久化Domain/Metric/Query只是定义依赖，不保证单次执行或相同值。
这一规则适用于同一次 execute 内及不同 execute 之间；共同执行不提供数据库快照或共同读取时点。
实现可以复用/暂存，也可以独立执行，不把选择变成公共一致性保证。
不追加跨读对账、重试或快照阻断来制造支持证明。
r=q.execute() 不将 q 替换成 r；要固定值引用 r，固定任何域统一使用 query(domain).execute().domain()。
固定身份不冻结后续属性、关系或事实；需要旧属性时保存 read 的值或 classify(by=...) 的分类坐标。
份额支持和单次 reduce 输入分别按局部实际输入合同保证，
不提升为整图共同快照，不追加跨读检测。

Query/Result.share 的自动总体和分组总体在该算子内部消费同一份接收者输入，
从所选量的实际贡献或充分信息构造分子和分母；不能仅展开为两个可独立读取的来源 Query。
后置 where/top 只限制输出，不能下推后改变分母及其必要检查；前置筛选按调用顺序进入总体。
来源模式缺合格共同输入路线时拒绝，并给出先 execute 固定接收者再 share 的修复。
固定模式不读取原来源。

### 1.2 默认保存信息

默认保存键、类型化轴、定义、Cells、必要证据及存在的 Top-N 展示次序；
合格sum/count/min/max/mean/weighted_mean路线须保存低成本精确信息，
包括sum/count、组件角色、政策、覆盖和必要支持；ratio依组件保存。
不保存全贡献ID、精确distinct集合/quantile bag或全部细网格。
常量/坐标Result不虚构来源组件；不保存已退出的结果分类节点。
身份属于实际坐标时随键保存；不为汇总额外捕获每组名单，不另存主体角色索引。
share 保存所选量、自动总体的实际范围与 partition_by、分母 Cells、映射及支持依据；
后续筛选不能丢弃恢复固定分母所需的信息。原列信息独立保留，新增份额列不取得原指标续算能力。
Top-N 的选中键展示次序随固定结果保存，运输与失效规则由 [DSL §4.6](lazy-analysis-dsl-redesign.md#46-查询结果与值表达式) 拥有；
它不新增业务轴，恢复展示不要求评分列仍在输出中，也不重新读取来源计算排序值。

本节是 Result 唯一的保存信息合同；execute 不接受保存请求，不另行捕获实际读取字段/范围/出现或端点细域。
身份域使用统一的坐标 Result 保存；比较端点信息分别保存，比较不拥有专用两侧细状态。
固定续算只能消费已有信息，不补来源；需要细域、更早历史或其它窗口时从来源 Query 重新构造并 execute。
失败不发布部分答案。
执行返回与冷恢复的定义、数据、证据及固定能力必须等价，不依赖内存缓存或当前Catalog。

### 1.3 跨轮和跨进程恢复

项目.marivo保存Session和精确Result；result(id)不选最新、不重新读源。
恢复的Result不降级成表，也不变回来源Query；缺信息的操作前后都拒绝。
保存类型化轴、实际字段分类与父子映射、参照和支持证据。
比较恢复后仍只选旧值或组合固定端点；需要细项时在原端点来源 Query 上 drill 或明确新 observe。
只有比较ID不能回放原来源。
新观察解析新依赖，旧Result始终不变；定义/单位/身份漂移精确失败，不替换旧定义。
删除当前来源声明不影响信息充分的固定续算，恢复失败保留文件。
不增加迁移/双读/隐式重建或一致性模式。

### 1.4 数学定律的实现边界

优化须保持身份、贡献出现、参照、完整坐标、政策和定义域；
m/m不能约成1，乘零/空集不能消除必要错误。
浮点遵守方法精度，不承诺逐位重排等价。
定律中的固定实例与实际读取保证采用 §1.1.1，等式只对满足前提的同实际输入成立。

## 2. Agent 读取与错误契约

Help拥有静态语法，Metric.contract拥有纯定义及绑定要求，
Query/Result.contract拥有实际上下文、输入权威和保存信息；show展示固定答案，有 Top-N 展示次序时沿用，否则按完整键规范排序。
默认政策由 [语义提案 §3.4](../semantic/expression-model-redesign.md#34-样本政策的归属) 和
[DSL §4.4](lazy-analysis-dsl-redesign.md#44-最小规约与有效样本) 拥有，实例只显示其已解析值。
同次读取/固定输入合同集中在§1.1.1，各算子说明自身需要的局部依据。

### 2.1 repr 与输入权威

所有公开对象提供 bounded 单行 repr，不触发业务或schema读取。
Query repr至少包含kind、逻辑身份、inputs=source/fixed/mixed及contract定位；
Domain同样显示静态依赖类别，未知键集不提前枚举行数。
Result repr包含结果kind、session/id及fixed标记，指向show/contract。

inputs说明整图依赖，不代替操作的输入权威。
混合Query中某个参照来自source，固定主输入仍只能按保存信息rollup等合法续算，不提供drill。
contract按相关操作列明主输入、参照/域依赖、固定信息缺口和新增读取范围；
多个输入分别披露，不能把mixed摘要解释为整图均可回源。
来源观察 Query 的 drill 说明新增细化绑定；
compare指向明确端点 Query 细化/新observe，不显示自动drill。
share 的 contract 披露所选量、分母范围、partition_by、映射及实际支持依据，
不执行分母；固定结果的 show 以有界摘要显示份额列与已保存总体，标量例为 sales.share、当前全部地区、总额470。
分组总体按规范键有界展示，并指向 contract；后续仅选 East 时仍说明分母来自原全部地区。

### 2.2 域测试的未绑定与已绑定披露

in_domain构造时repr显示domain的有序轴、身份/字段角色、已知依赖及binding=unbound；
未提供接收者时只陈述可检查的域形状，不能预称将读取字段或过滤结果。
where绑定后，接收者contract披露实际测试：
member_identity、contribution_identity、field_tuple或result_coordinate，
并显示完整匹配轴、实际投影/父子映射、域依赖和新增字段/来源读取。
同一字段域用于 Session.members 的来源条件时可新增字段读取，用于 Domain/Result.where 时只测试已有坐标。
Domain.where 不接受 FieldPredicate；需要当前字段条件时用来源选择的 where，
或先对固定身份 read 再以结果列筛选。接收者决定读取边界，不能静默升级为属性查询。
不拆出第二套in_domain入口，不按列名猜轴，不提前求键或Python列表。

### 2.3 跨阶段错误给出有效修复

错误由所属层SemanticError/AnalysisError拥有expected、received、location和typed repair。
修复候选来自真实语义声明/已保存状态，只构造或描述合法下一步，不自动执行。

| 错误输入或位置 | 具体修复方向 |
| --- | --- |
| 来源构造器收到结果Column/统计请求 | 需要语义续算的 sum/mean/row_count 使用 reduce；其它结果处理按 §3 交 pandas；新来源指标明确来源定义 |
| 结果select/reduce收到Field/Metric | 新指标先observe，语义字段先合法read/分组；已保存轴使用coord，随后显式对齐 |
| Ibis cases/least 等收到结果 Column | 结果导出后用 pandas 并明确状态政策；若需来源计算则使用受限 Ibis authoring，不自动转换阶段 |
| 来源Metric由裸后端Ibis表/memtable拼入 | 指向合法Entity与声明字段，说明缺少的来源/单位/权限，不能转型绕过 |
| Column属于另一输入域 | 先按已有合法映射align/broadcast，再构造结果表达式 |
| 独立参照列单位缺失或不相容 | 列明参照列及双方实际单位；用 values 的 columns={"budget": "CNY"} 等映射创建新参照，裸常量不携带单位，旧结果不补写单位 |
| values 的 columns 收到嵌套列定义或缺少行中量名 | 直接提供完整的列名到 str / None 单位映射；"1" 为无量纲，None 为明确未声明，量名与每行完全一致 |
| TimeScope.by/coarsen/to_date 的 grain 收到字符串，或 by/coarsen 另收到 count | 使用 mv.grain(unit, count=...) 或已加载认证日历的 grain(level)；粒度消费者仍校验合法分桶、粗化或包含关系 |
| identities/coordinates/values 手写键与轴不相容，或请求 CalendarAxis/CorrespondenceAxis | 列明轴及所需字段 kind 或完整主键元组；身份名单用 identities，coordinates 只接 Field 轴；字段 Null 用 None，量值不接 None；日历用 TimeScope.by/product，已有日历与对应域用 domain/project/where 复用 |
| Domain.classify 的 by 收到日历、对应或其它身份轴 | 只读取单值 Field，并可保留接收者的原 MemberAxis；已有坐标用 project，关系像用 image 的 via 元组 |
| 本地时刻存在 DST 歧义或不存在 | 提供明确偏移或带时区值；范围/since/at 复用精确 InstantAnchor，不用 end 减 tick 近似边界前版本 |
| 要求按观察期间采样/时间 fold 的依赖，或日历输出收到 during=None | 指出真正需要期间的依赖或输出日历，要求显式 during；普通来源规约仅有时间角色不触发此错误。日期过滤或属性时点不替代采样/fold/日历绑定，不读取来源猜范围 |
| Field.via、image/preimage 或 corresponding 的 via 收到单个 RelationshipRef、list、空元组或断开的路径 | 单跳用 (relationship,)，多跳用声明方向连续的非空元组；列实际断点与所需端点，不自动包装、反向或用空路径表达恒等 |
| preimage 的 targets 是字段域或另一 Entity 身份域 | 指出声明关系的目标 Entity；使用该 Entity 的身份域，不能按字段同名自动转换 |
| FieldPredicate与ColumnPredicate混用 | 指出当前作用域及各输入归属，分别在观察/成员或结果上构造条件 |
| 未绑定域或域轴不相容 | 列实际轴/角色及缺失映射；由合法接收者绑定，不能凭标签或改名修复 |
| 固定主输入缺细状态，但图中另有来源 | 指明该主输入缺口；从原来源 Query 重新构造或显式新observe，不借其它来源补旧状态 |
| 结果 sum/mean 收到 Null，或统计收到不获准的Unknown/Undefined | 列明单位/样本、政策和有界坐标；有意改口径时先显式 where 选择样本或交 pandas，保留改变记录 |

裸Ibis错误若发生在进入Marivo前，Marivo不能保证拦截；
静态Help应提供阶段选择示例，进入Marivo后的typed repair才有上述义务。
不承诺让 Ibis 直接接受 Column，也不提供 Field/Metric/Column 之间的自动转换。
已退出的结果处理入口不作为错误修复候选，不用回调或隐式 pandas 执行保留兼容路径。
聚合前业务量的新意义由Measure authoring拥有，阶段修复不授予新权限。

实例其它必要事实仍有界披露：完整轴、时间角色/窗口、主体/归属路径、保存状态、
前提/已检查证据与实际支持依据。compare公共侧名统一current/baseline，
包括端点坐标投影、presence、缺侧原因和错误位置；align继续left/right。
默认来源信任不标为已检查完整性；已有缺口及其作用域、已检查证据与固定信息丢失分别显示。
保存和恢复沿用本次实际读取范围及缺口事实，不追加调用方完整性声明或隐式回源修复。
外部分类/统计不继承 Result 合同；缺细信息不回放 lineage。

## 3. pandas 导出合同

`Result.to_pandas()` 是普通后处理的正式出口。只读这一精确 Result，
无新来源读取、不刷新 Catalog、不创建 Run，也不要求先构造 display/select/reduce。
返回完整隔离副本；修改 DataFrame、其对象值或 attrs 均不改变 Result 或另一份导出。
它受既有已提交数据读取与本地内存预算约束：超预算或无法无损转换时明确失败，
不抽样、截断、转成 float 蒙混过关或返回部分表。Query 不能隐式执行后导出。

采用一种固定的宽表布局，不另设 values-only/flatten 模式：
行使用 RangeIndex，按 Result 完整键的规范顺序排列；它只表示导出行位置，不是业务身份。
列为两层 pandas MultiIndex，层名为 `kind`、`name`。各类按下表顺序出现；
轴按保存的轴顺序，量按保存的命名顺序，presence 按规范业务角色路径序排列。

| kind | name 与内容 | 约束 |
| --- | --- | --- |
| coordinate | 每个实际输出轴的稳定绑定标识 → 完整业务坐标值 | 不用展示标签、Python 地址或行号作键；FieldRef/角色/版本从下述 axes 元数据发现 |
| value | 原输出量名 → 该 Cell 的值 | Defined 保留精确值；其它状态使用 dtype 相容的缺失值，不补零、无穷或展示字符串 |
| state | 与每个 value 完全同名 → Defined/Null/Undefined/Unknown | 非缺失字符串列，每格都有状态；坐标 None 不生成量 Null |
| reason | 与每个 value 完全同名 → 保存的有序原因记录元组 | Defined 为空元组；其余按原 Cell 合同保留原因及业务角色，不只导出渲染文字；纯数据的隔离副本 |
| presence | 当前输出实际拥有的业务侧路径 → 是否存在 | 非缺失布尔；保留 align 的 left/right、compare 的 current/baseline 及嵌套归属，不从 value 缺失猜 presence |

名字空间彼此分开；量名 `state`、`coordinate` 或 `revenue.current` 不会覆盖键或状态列。
coordinate 的 name 是稳定字符串标识；value/state/reason 使用原量名字符串；presence 使用完整角色路径元组，
例如 `("current",)`，嵌套时保留全部分量。结构性 current_present 等在 presence 导出，不重复作为业务量。
完整复合成员身份作为同一轴中的有序键元组导出，所有分量保留原类型，
Entity/主键分量含义在 axes 元数据中记录，不用拼接字符串或某一个分量替代。
字段 None 坐标作为可识别的空分类键保留；外部 groupby 如需该组，显式使用 dropna=False。
日历坐标使用其真实桶起点；名义/实际区间、时区与日历身份随元数据保存，不能只凭起点猜覆盖。
非恒等对齐保留对应坐标及两侧实际投影/缺侧原因，不伪造单一真实日历。

`frame.attrs["marivo"]` 是此次导出的描述性元数据，固定记录：

- `session_id`、`result_id` 与 `layout_version=1`；仅定位原答案，不是新结果认证。
- `axes`：按实际轴顺序的描述记录。每项含 `column`（coordinate 的实际 name）、
  `kind`（member/field/calendar/correspondence）、保存绑定身份、角色和版本解释；
  字段轴有 `field_ref`，成员轴有 Entity 与有序主键说明，日历/对应轴有真实边界和两侧投影说明。
  两侧投影须能按完整输出坐标查得实际坐标、状态和原因，不能只保存映射名称。
  调用方按这些已保存事实定位轴；同 Ref 多角色时明确选择，不取第一个同名项。
- `quantities`：以输出量名定位已保存的 kind、单位、数值/样本政策、定义和阶段说明。
- `evidence`：本次保存的读取范围、已知缺口、声明/前提与已检查事实及对应作用域；默认来源信任不生成完整性检查证据。

元数据使用保存的纯数据记录；`field_ref` 等 Ref 使用规范限定字符串，不返回 Catalog 对象或可执行表达式。
这些记录跟随实际保存内容，不向当前 Catalog 补信息。只有已输出的坐标和值进入行表；
未导出的充分组件不会因导出自动展开。
需要普通 DataFrame 的显式指标组件，可在原观察中把这些指标列为输出；
不能把未导出的内部信息当作 pandas 算法缺陷。Result 仍持有原信息并可单独合法续算。

int64/较大整数、Decimal、布尔、字符串、date、带时区 instant 和 duration 按其保存类型
使用能无损表达的 pandas nullable/Arrow dtype 或精确 Python 标量；
不因出现缺失将大整数/Decimal 强制转 float，也不去时区或降低时间精度。
空结果仍保留全部列、dtype、轴与量元数据；零轴单例与零行空域保持区别。
仅坐标 Result 没有 value/state/reason 列，身份轴仍保留完整键；不插占位 count。

调用方可以显式取出普通值表。以下只接受全部 Defined；这是本段外部统计的输入要求，
不是 to_pandas 限制，导出本身必须保留所有状态：

```python
exported = result.to_pandas()
if not exported["state"].eq("Defined").all().all():
    raise ValueError("Inspect Cell states and reasons before this statistic.")
values = exported["value"].copy()
```

需要忽略 Null 时，先拒绝 Unknown/Undefined，再按明确的样本政策选择 Null；
配对统计用一份共同掩码，零权重不掩盖未定义输入。presence False 和源 Null 不是同一事实。
pandas 的默认 skipna、groupby 丢空键、按 RangeIndex 对齐等行为不承担 Marivo 合同；
跨表计算应明确完整业务键、唯一性和缺侧，而非按导出行位置组合。

pandas 可以继续保留或复制 attrs，但任意变换后的 attrs 均不能证明单位、覆盖、
贡献、充分状态或身份绑定仍有效。外部输出是普通 pandas 数据，不接受回灌成可信 Result，
也不新增 from_pandas/回调/自动 Metric 转换。需要后续主体观察时，
保留原 Result，where 后通过 domain/project 选择已有身份坐标；不能仅凭外部标签恢复权威。
此边界不是扩张 DSL 统计清单的理由；终端分析正常结束于 pandas。

## 4. 实施衔接

本提案复用现有 Runtime；目标表面、mixed graph 和新保存合同仍待实施。
本地忽略计划 `docs/superpowers/plans/2026-10-13-lazy-analysis-dsl-redesign-implementation.md`
映射 R1–R8 模块与验收；它不是分发文档依赖。实施前刷新当前 owner；技能变更仍需独立明确授权。
