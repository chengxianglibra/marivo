# Lazy Analysis DSL 定义

Status: AnalysisFrame cutover contract, 2026-10-19.
[AnalysisFrame](query-result-reuse.md) 是本次最终数据/载体合同；本页组织来源与数据表达式边界。
当前实施与验收记录由 implementation progress 拥有；历史 R3-R8 证据不自动成为新 Frame 验收。
静态精确 API、参数、例子与导航以 native Help 的同一 registry 为准。

## 1. 范围

一个不可变 AnalysisFrame 跨待执行表达式、完整内存数据和精确存储引用。Population 仍是
有限完整键域，Column/FrameAxis 绑定精确输入，Artifact 是持久根的提交/审计视图。
结构决定操作，载体决定求值；已完成不撤销数据操作，也不使数据恢复原来源公式。
没有 Query/execute、session.query/session.artifact 或新旧接口并存。
RawSqlResult 保持独立 datasource reader，不进入 Frame Store。

### 语义定义更新原则（2026-10-15 批准）

不要仅因同路径定义变化要求原版本或拒绝已解析输入；用户接受业务后果。
保留 kind、root、完整 grain、unit、time-role 与 null 检查。指纹/缓存描述实际消费定义，
持久记录保留自身合同。完成作用域是解析权威，不刷新已有不可变值或重放模块。
业务版本身份与属性 at 锚点不是原定义版本锁，仍检查其完整数据含义。

### 1.1 普通分析的最短路径

```python
import marivo.analysis as mv

s = mv.session.create("fixed-values", timezone="UTC")
pending = s.values(by=(), columns={"amount": "CNY"}, rows=(((), {"amount": 100}),))
pending.show()
fixed = pending.compute()
saved = fixed.select({"adjusted": fixed["amount"] + 10}).persist()
saved.show(n=5)
saved.artifact.show()
restored = s.persisted_frame(saved.artifact.id)
restored.reduce({"rows": mv.row_count()}).compute().show()
```

这是纯字面量示例，不需要 Catalog/来源。真实业务 Metric 仍从受治理语义输入显式构造。

## 2. 对象模型与 lazy 状态

### 2.1 不同身份不能互相替代

语义 Ref 是查找键，构造表达式是不可变定义；Frame 输入身份绑定具体数据/计划；
result ID 标识完成结果，Artifact ID 标识提交。FrameAxis 只能来自该输入 axes/axis(name)。
同名/同指纹不能转移 Column/轴，两个兼容重开存储句柄可按同一精确输入身份共享。

### 2.2 状态与副作用

构造/repr/Help/show 不求值、不准备来源 schema、不创建 Run。compute 返回完整内存根。
表达式 persist 先完成求值，再发布已有 result ID；内存 persist 不重新求值；存储 persist
验证提交元数据后返回自身。继续使用返回的完成对象才固定输入，原来源表达式可再次读源。

### 2.3 终端 Artifact 与计算边界

持久根 `.artifact` 只读同一提交，提供 show()/findings()/evidence_digest()，没有 n、
to_pandas 或数据算子。Frame 拥有 preview/export 与受支持的续算。派生表达式没有祖先
Artifact 权限，Artifact/ID 对象不是操作数。恢复用同 Session 的 persisted_frame(str ID)。

## 3. 语义交接与输入类型

### 3.1 保留语义层的权威

Entity 提供来源、完整身份与业务版本；Field 提供读取角色/时点；Metric 提供受控
Deferred 公式、贡献根、条件、单位和时间/内部样本政策；Relationship 提供明确方向与完整键。
PeriodCalendar 的认证不证明业务来源完整。普通观察不自动做全来源质量审计。
@ms.measure/@ms.metric 零参数体只构造同一受控行/Metric 协议，命名不执行、不自动发布。
Quantity×price 需要新的业务可加授权，dtype/名字/相消/乘零不授予它；相容已有金额的差可
继承许可交集。裸 Ref 不能参与表达式，capture/finalize/resolve 的次序不变。

### 3.2 Entity 身份与普通维度分组

Orders.customer_id 可作字段分组，不等于 Customer Entity。Entity 身份需要所有主键分量，
普通 Null 字段为合法类别，Entity 身份不能含空分量。组合分类保留完整联合元组，不逐列求积。
显式 identities 只固定引用，不证明来源存在；members 选择已有源身份；frame 接一般完整域，
read 只接受一个身份域与非空字段。新字段/关系/时点读取始终显式。

### 3.3 签名中使用的精确类型

LiteralValue 是闭合字面量联合：bool/int/有限 float/Decimal/str/date/aware datetime/timedelta；
None 可作普通字段 Null 坐标，不是量值或算术字面量。ValueRow 是有序坐标元组与命名值映射。
interval/日历/配对轴不接受手写任意内部键。Deferred 是受控表达式载体，裸 Ibis Table/关系值与任意
回调不是 Metric。Statistic 是既有私有 unfinished statistic 协议，不新增公共同名导出。
FrameAxis 公开 token/name/role/key_names，不公开物理 codec、Ref 或原始载体构造器。

## 4. 核心 API 签名

签名/完整范例由 `marivo.help("analysis")` 下的独立 native 目标拥有；本节只描述分工。
新类型 canonical targets 是 `analysis.analysis_frame` 与 `analysis.frame_axis`。

### 4.1 Session、字段、时间与路径

observe(metrics, *, by=(), where=None)、read(population, fields)、
values(by, columns, rows)、frame(population) 均返回 AnalysisFrame。各入口不以空参数代用。
TimeScope 与 CalendarAxis 都放入 by：前者产生一个 interval 坐标，后者产生冻结日历桶，
最多一个输出时间轴；单桶不自动 squeeze。by=Population 只选择完整联合键并保留无事实坐标。
普通 tuple 的时间加分类先发现真实联合类别，再补时间桶；无分类域不虚构 Null 类别。
where 的 Population 谓词筛选事实贡献；若还保留原字段条件，使用
Metric.where(mv.in_population(P)) 配合 observe.where=field_predicate，不将两种谓词直接 &。

Session instant/time_scope 是唯一分析时间构造器。source read 只在求值发生；字段 via/at
和 Metric 固有条件保持不可变，不因调用方更换期间覆盖旧选择。

#### 4.1.1 精确时刻与版本选择

Session instant/time_scope 的日期和无偏移字符串按冻结报告/显式时区解释，aware 输入保留瞬时。
naive datetime 与歧义/不存在时刻拒绝。业务版本 at(anchor) 按明确边界解释，不用减 tick
猜左极限。更换期间不改写已捕获属性时点；完整身份包含其必需业务版本键。

#### 4.1.2 常量与独立参照列的单位

values.columns 声明每列 str|None 单位。Column 常量位置保留既有单位检查，未知单位不能
由列名或数值大小推断。独立参照先构造覆盖完整目标域的 Frame，再全轴 align/select。
这不提供自动广播或贡献包含证明。冲突单位需明确合法换算，不能隐式换汇。

### 4.2 统一 Population 与明确身份读取

Population 拥有完整轴投影、集合、分类、声明关系 image/preimage 和 membership 谓词。
集合要求相容有序轴/绑定。分类/关系读取保持显式源依赖与完整键检查。
population_from(frame, on=...) 是唯一结果到源键桥；on 必填，可为 ()，选普通字段/完整身份轴，
显式绑定解析 Field/Entity。投影联合键并去重；空输入得到空约束，零轴空域与单例域不同。
构造不读行，消费先完成选择与检查；固定根不重排旧 Top，待执行根每次先求值。interval/日历/配对轴与
任意值列不桥接。where=mv.in_population 是贡献过滤，by=Population 选择指定完整输出域（包含无事实坐标）。

#### 4.2.1 像、逆像与已有坐标投影

Population.project 只选自身现有完整轴，image/preimage 沿显式声明方向与完整身份路径。
普通 Frame bridge 仍需明确提供来源 Field/Entity；丢失身份或历史分类不能从汇总值反推。
字段 Null 是合法类别，缺关系映射是错误，不能将缺映射伪装 Null。

### 4.3 Metric 表达式与明确贡献角色

来源 Measure/Entity/Field 规约与 Metric 算术/per/window 继续由语义协议拥有。
Metric 没有 expr/during/route 或任意 on 树位置参数。where 保留受治理条件/显式域闭包；
内部 per 先逐样本完成整个子公式，再规约；显式样本可保留无贡献样本的 empty 政策。
by=Population 选择指定完整键，不要求覆盖域外来源坐标。P.count() 捕获单 Entity 完整
身份域，返回可组合的惰性 Metric，包含无事实身份；P/Q 分别计数，不从 by/where 推断分母。
事实过滤不重选计数域，空域计 0；只有计数时无效果条件/窗口拒绝。

#### 4.3.4 显式成员关系分桶

membership 明确成员字段、路径与 unmatched 政策；同描述内字段关联，不同描述形成独立因子。
每个贡献/桶去重，不按数值去重。多根逐根绑定/规约后组合，不因另一个根行数增多而放大贡献。
真实关联次数需独立关联 Entity，存在性筛选用 preimage，加权分摊用明确分配根；不混同。
源 schema 与后端方法资格由真实准备检查，匿名表达式不提前连接，未知 operation/回调拒绝。
完整语法与规则见[语义表达式](../semantic/expression-model-redesign.md)。

### 4.4 最小规约与有效样本

来源规约消费贡献；内部 per 消费已完成样本；Frame.reduce 消费当前行。
Column sum/mean 固定忽略 NA；空/全 NA sum 与无样本 mean 是 NA，row_count 数所有当前行。
每项统计独立有效掩码；共同掩码只归加权/配对方法。Statistic 只交给 reduce，不能自行
算术或执行。结果统计没有 empty/nulls 扩展；来源显式 empty="zero" 与 nulls="error" 保持自身政策。

### 4.5 坐标和目标域

完整复合轴是一个句柄，by/on 内部展开全部键。coord 只接受单物理分量轴；getitem 只取
值/presence，不把坐标名当值。axis(name) 精确查找当前目录，不存在/重名给真实候选。
select 保持轴/presence，where/top 保持所选行结构，reduce 只保留已选轴并丢弃 presence。
单标量是零轴 Frame，不新增 ScalarFrame/ComparisonFrame/CoordinateFrame 子类。

### 4.6 查询、结果与值表达式

Frame 支持 select/Column 算术、where(Column)、全局 top、reduce、align/compare 和有限
日历 match。user 值名是单个非空段，无点、反斜线、控制字符或 reserved presence 名。
output_names 仅值，presence_names 独立，两者完整有序。生成路径通过点公开分隔，getitem
按全名精确找值，不按后缀猜原指标。已完成数据运算不隐式回源。

### 4.7 对齐、比较与参照

普通轴要求完整显式 on，零轴及双方唯一总轴为同种 interval/日历时允许规定推导；显式 on 不自动补齐。
跨 Session、已有配对轴、轴数/角色/完整键类型与时间不相容拒绝。exact 检查实际联合键域；
outer 左序后右独有序。align 保留左右值；compare 要求同名相容数值/单位并生成四组：
left/right/absolute/relative。相对变化 (left-right)/abs(right)，只对有效配对应用零政策。
缺端点或 NA 差值为 NA。Duration 左右/绝对仍为 Duration，相对无量纲。
presence 独立布尔，继承项带 left/right 前缀，不因元数据身份变为可求和数值。

ordinal、builtin previous_year/period_offset 消费冻结有限目录；命名日历仅 ordinal。
完整名义分区与实际边界准入先于 actual-row exact/outer；过滤不压缩 ordinal。
单层配对轴保留两端目录/映射，无 endpoint 方法或 time_axis=current 投影。

### 4.8 份额：当前总体与分组总体

原贡献 share、rollup/drill、任意关系 corresponding、broadcast、分区 top 与日历粗化不在
Frame 首版。没有假等价 reduce/align 替代。原整体公式/新属性问题需要显式新来源；
外部固定数据份额要声明分母/样本和证据限度，不能继承贡献包含权威。

### 4.9 滚动窗口、累计与期间比较

Metric.window 绑定 all_history/trailing_duration/trailing_periods/since/to_date；
每个端点先完成完整 W(t)，显示起点不截断回看。来源/报告/日历时区分离，名义桶和截断
实际桶分离。更换窗口或目标历法需新 observe；Frame.reduce 不改原贡献窗口。
保存标签、序号、bounds 与 paired map 自包含，无当前 TZ/model 重建。

#### 4.9.3 覆盖、缺桶与执行检查

by=Population 只在指定完整键上求值，域外来源坐标不引发覆盖错误。分配保留原贡献分母，选择输出桶不重归一化。
认证日历证明有限名义域/边界，不证明来源采集完整。Match 的完整名义桶结构与实际行域
分开检查；缺损目录/映射是错误，不是普通没有对应，where/top 不绕过必要检查。

## 5. 数据、检查与执行约束

### 5.3.3 份额普通比值与贡献证明

普通已对齐值除法不证明贡献包含。固定数据没有原来源集合/充分状态；问原 Metric 需显式
源计算。行 mean 55≠整体 1000/91、distinct 4≠并集 3，信息下界不由新列名改变。

### 5.4 Nullable 值、部分运算与谓词

单一 NA 与独立 presence 保留缺值/缺端点区别。严格 null/overflow/coverage/除零子式
不能被 where/select/Top-0 或父 null 政策掩盖。布尔不是数值，键不经 float，Decimal/
timestamp/Duration 保留实际 qualified 类型。源精度损失与保存值保真是不同边界。

## 6. 求值、保存与披露

完整求值先于 Run，Run 仅发布已有 result ID。Store 13 保存一种闭合 Frame 协议，
来源事实按值继承并精确去重，本次求值记录不累计中间历史。show(n=0)/Artifact 审计不消费载荷，
正 n 只解码前缀及必要坐标记录；full compute/export 验证必要全部数据。
固定内存/存储操作无需模型、来源、语义对象或时区规则；读取失败不隐式回源。

静态 Help/捕获 show/真实错误/工作流技能各守一处权威。exact ID recovery、旧 generation
拒读和兼容环境选择见[切换说明](analysis-frame-cutover.md)；完整顺序与检查见
[执行与交接](lazy-analysis-dsl-execution.md)。发布/Provider/slow/release 资格由实际证据说明。
