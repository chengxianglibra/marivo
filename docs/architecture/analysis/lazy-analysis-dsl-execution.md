# Lazy Analysis DSL：执行、保存与结果交接

Status: current AnalysisFrame execution contract, clarified 2026-10-21 without new rewrite qualification.
[AnalysisFrame](query-result-reuse.md) 是最终数据合同；实际接受源码与剩余 gate 由实施进度拥有。
历史 Query/provider 记录保留原范围，不等于新 Frame pipeline 的 Runtime/发布资格。

## 1. 执行、保留与恢复

### 1.1 图与来源分类

来源 Frame 显式捕获受控 Metric/Field/Population 依赖；字面量 Frame 无来源；固定输入
消费完整内存数据或精确存储引用。一个计划可组合显式来源与固定数据，但固定根不携带
旧生产图、模型、连接、原语义对象或内部 aggregate state。不新增可执行 origin/历史自动复用。
构造、repr、Help/show 不读业务行/schema、不创建 Run；compute/persist 才触发求值。

#### 1.1.1 共享逻辑节点与执行内输入

共享定义不是固定答案。继续使用返回 compute/persist Frame 才固定数据；重用 pending
来源表达式可能读到变化。调度器按实际输入身份共享读取并按消费者释放，自组合读取一次
但保留两项消费。compatible reopened stored handles 使用同一精确 input ID；指纹不重绑句柄。
多来源独立读取没有共同事务快照保证，不通过重试/对账伪造。

#### 1.1.2 Metric 显式域依赖

Population 保留自己期间、属性时点、条件、完整键/绑定与 Session。来源 Metric.per/where
显式捕获其域；内部样本域和外部指定输出域独立。Population 只携带完整键与依赖，
membership 由 observe/per 各自的 memberships 参数捕获，外层与内层不互相继承。
P.count() 捕获自己的完整元组域；P/Q 分别调度，字段 Null 与无事实身份均参与计数，
不从 by/where 推断分母，也不隐式读取分组属性。新属性须先显式 classify 并保留要计数的身份。
空域计 0，零轴单元域计 1；无时间轴的域可广播到输出时间轴，已有时间轴按冻结坐标匹配。
Frame ordinary-axis bridge 的 on 必填、
可为 ()，先完成输入选择/检查，再联合投影去重，最后执行明确的新来源依赖。
固定 Top/where 名单不重排，新来源值仍可变化；interval/日历/配对/任意值不桥接。

#### 1.1.3 五项扩展的执行边界

Metric 完整子公式、时间/内部样本、近似方法、membership 与窗口保持语义 owner；
未知 Deferred resolver/operation、任意表程序与回调拒绝。dtype 不授予业务可加性。
多个根逐根分类/规约再组合，成员路径保留相关联合键和去重身份，不能把 fanout 当事实。

#### 1.1.4 Fanout 的执行准入

存在性 preimage、真实关联次数与显式分配根是不同问题。单值路径与多值 membership
各有明确检查；缺失/冲突路径不猜测。必要祖先身份/映射错误在消费域上保持，即使输出为空。

### 1.2 默认保存信息

compute 产出完整 immutable data 与一份 audit，无持久 Run/Artifact 或临时结果文件。
persist 对表达式先完整求值，再将已有 result ID 交给同一 Store 15 writer transaction。
内存 persist 不求值；存储 persist 验证 commit 元数据后返回自身。派生 expression 不能返回祖先
Artifact。每次内存发布可有新提交，但 result ID/求值时间/审计不因载体复制改变。

按 marivo.frame_output/v4 保存闭合值/普通、interval、日历、单层配对轴目录/独立 presence/必要 typed attachments 与 receipts。
来源事实按值继承、只去重完全相同记录；当前 evaluation 记录本次输入身份/操作/检查，不累计
中间事件。Run 的输入是已有结果 ID，表示发布，不声称当时读取来源。
源读时间、evaluation 完成、admission 与 commit 时间保持独立。

### 1.3 跨轮和跨进程恢复

保存 Session ID 与 frame.artifact.id，resume 同 Session 后 persisted_frame(str ID)。打开
commit metadata 不加载 Catalog、来源 schema/连接、模型或时区规则，不读取 payload。
恢复所得 Frame 可直接做受支持数据运算；Artifact audit 不提供数据导出或 DSL 操作数。
丢失/损坏的数据直接失败，不作为缓存 miss 自动回源。

存储 compute 完整加载到独立内存根；旧文件删除后该内存根仍可读/派生/发布，但旧存储根
完整读取仍失败。Frame/Column/Axis 强持必要输入与同一 local services，无反向缓存/producer
Session 引用，不根据路径重新接入 Session。跨 Session 运算始终拒绝。

### 1.4 数学定律的实现边界

当前行 reduce 使用 represented values 的独立有效样本，不恢复原贡献。
行均值 55 与来源整体 1000/91、distinct 4 与并集 3 是独立验收反例。
Frame 原量 rollup/drill/share、广播/任意关系对应、日历粗化、分区 Top 与再次对齐配对轴延期。
原公式与新事实问题显式重新 observe；不能伪装为 reduce 或从历史来源图恢复。
sum/mean 沿用当前值表达式单位，不检查原 Metric 在被移除轴上的可加性，也不因此授予原量上卷能力。

数学定律不自动授权编译器消除中间节点。当前 L1 仅融合同实际输入、同布局、单消费者、
无待完成检查的闭合 int64 比较过滤，并保持 nullable True-only 选择政策。
相邻 ReduceValues 的 L8 候选始终记录 refused；中间掩码、数值边界与原完成检查尚无固定状态证明。
通用映射复合与完整原像限制没有注册改写。编号和实际入口见[理论覆盖表](algebra-theory.md#实现编号与理论定律的对应)。
这些边界不限制合法显式算子组合，也不承诺实数等式在浮点重排、溢出或不同检查范围下保持等价。

### 1.5 单一执行路线与终端读写

同一 scheduler 处理显式来源、固定/字面量与本地算子，compiler 固定完整 key/value/check
需求和资源生命周期。strict 检查不能被后续 projection、where、Top-0 取消。
来源 Ibis 经 SourceSession.execute 返回 pandas；本地现有核消费 pandas；PyArrow 拥有存储
与合格临时键运输。无第二引擎/recursive compute/自动不支持方法重试。

#### 1.5.1 Ibis 原生返回与 pandas 类型交接

原生 Ibis/provider 数值精度限制属于来源边界。DuckDB 的受治理 SourceSession 读取与合格的
typed PostgreSQL Analysis 读取先取原生 Ibis Arrow 结果，再以 nullable Arrow integer dtype
交接 pandas；能由原生 Arrow 结果类型表示的整数与 null 共存时不经过 float64，保持精确。
非整数保留原生 Ibis Arrow 类型、可表示性与精度限制。普通 PostgreSQL authoring/basic/
collect_bounded 读取与其他 provider 仍走原生 Ibis pandas 路线，保留其精度与表示限制。
handoff 按实际返回值
确定有效类型，不能 cast 回声明类型假装恢复精度；键、null、单位、时间与必要检查仍严格。
原生 sum/mean/weighted/ratio 有 PG/MySQL/Trino/ClickHouse qualified 路线，并非 DuckDB-only。
Exact quantile 与 approximate 方法按真实 backend/type/method 分别准入，不互相替代。
源 qualification 不是新 Frame fixed-data/Runtime 路线的自动证书。

#### 1.5.2 来源关系融合与原指标重算

来源纯关系/数值计划保留实际 compiler/method/topology 资格；按真实消费者保留内部
mean/per 等状态，不因退役 Query receiver 全局删除 components。固定 Frame operands
保持本地消费与显式 typed key transport，不上传固定数据或重用旧 source-fusion 保证。
完整 null-safe 元组、key capacity/collation、source owner 与必须失败的检查保持原边界。
已固定选择不得因优化重新运行来源 Top 换名单，跨读取也不承诺相同贡献值。

#### 1.5.3 显式 Attribute 与完整事实

`Session.attribute` 捕获两个 AttributeEndpoint、完整父对应和新增业务分类；默认路径只做合法
原观察与差值，不准备贡献证明。贡献路径在同一调度中复用合格的 physical DuckDB TableSourceIR
root/fiber statement 共同输入，保留完整父/细组/原式和必要检查。文件、远端以及未证明的 per/
窗口形态不因 provider 名或共享图而合格；失败不自动退回默认请求。

原式与数值检查一起保留实际 native carrier；静态未知 Decimal fallback 不是已完成数值政策。
有限方法事实校验复用现有数值 owner，不另建舍入/分解内核。执行内原始组件在消费后释放，
历史方法事实通过真实 producer-expression/input association 在 source cleanup 后按表达式折叠。
每节点本次事实先于继承，binary 完整 leftFacts 后 rightFacts；固定结果作为自包含叶。
两个不同 pending producer 保持不同 origin，同 producer 重复输入按相同 origin 去重。

Store15 的 `marivo.frame_output/v4`、`frame.v4`、`frame-output-v4` 适用于所有 Frame。
完整解释记录写入自己的方法表附件，manifest 只持闭合描述符；数据和方法收据共享既有原子
发布、readback 和 lost-commit 协调。派生保存结果不依赖原 Artifact/来源或 calendar 重建。

## 2. Agent 读取与错误契约

### 2.1 repr 与输入权威

Frame/Axis repr 有界并指向 show。pending show 披露当前结构、scope、输入类型和 pending
operation；不伪造行数或父输出。属性/静态方法发现归 native Help，不执行 getter。
FrameAxis 绑定精确输入，axis(name) 精确查目录，不重名取首项；coord 只接单物理分量轴。

### 2.2 域测试的未绑定与已绑定披露

原生 Deferred 谓词使用位置决定作用域。来源 observe.where 可引入明确来源条件；
Frame.where 只接同 Frame boolean Column。population_from 是显式普通轴桥，不是自动
恢复语义对象。by=Population 选择完整输出键；where=mv.in_population(P) 选择事实贡献，两者可分别使用。

### 2.3 跨阶段错误给出有效修复

| 情形 | 修复边界 |
| --- | --- |
| 来源入口收到结果 Column/Artifact/ID | 给明确来源 Deferred/Population；固定数据统计在 Frame 上，恢复字符串 ID 先 persisted_frame |
| 列/轴不属于输入 | 从精确 receiver 取得句柄，或完整显式 align/on；不按名字/指纹重绑 |
| 原整体或新属性问题 | 显式提供 Metric/Field/Entity 与新源 scope；reduce 不代替原量 |
| 日历 partial/规则/附件缺失 | 构造合法完整来源桶或修复实际存储；不重建 TZ/静默改 match |
| 必需数据损坏/缺失 | 报告精确消费文件/结构；不能回源伪造恢复 |
| 发布结果未知 | reconcile 原 Run/journal，不重复求值 |

### 2.4 检查时机与能力披露

已知非法在最早边界拒绝，不为提前判断读取业务行。实际 schema/method 在准备期检查；
实际完整键、数值、覆盖与附件在消费前验证。值名非法构造时给重命名修复，不静默转义。
静态 Help、捕获 show、实际错误与技能 workflow 各有唯一 owner。

## 3. pandas 导出合同

Frame.to_pandas 只对 complete memory/stored root 读数据，不自动执行派生 expression。
当前数据完整读取验证必要 payload/attachments：receipts、实际 dtype/nullability、完整键唯一性、
有限值、presence 与 frozen directory/map 引用。元数据可打开不证明 payload 完整。
to_pandas 不读历史方法载荷；method_facts getter 单独读取/校验完整 owned 方法事实而不读主表。
stored compute 与派生求值消费两者；方法文件损坏可使完整消费失败，当前行导出仍可用。
getter 后的 facts.show(n=0) 只控制内存显示，不意味着此前没有方法文件读取。

导出 RangeIndex 与 `(kind,name)` MultiIndex：coordinate 按 axis.token，value 按完整值名，
presence 按完整路径。原始物理键和精确行序不变。attrs 的 axes 描述含 token/name/role/key_names/
at/closed descriptor；units 为值名/单位对；comparisons 为隔离的有限命名值/presence 绑定；
frozen_coordinates 为 name/schema/隔离 typed rows。
这些深隔离数据没有 reader/Frame/Store/语义对象，不能反向导入为计算权威。

Frame.show(n=0) 与 Artifact.show/findings/evidence_digest 不读数据或坐标附件；n 校验先于 IO。
正 n 解码实际前缀并解析所需 frozen 标签、名义/实际 bounds 和端点映射。直接/命名比较
预览校验其必需有限来源端/目标端目录和映射权威，主表仍限 n 行；普通/paired-only 预览
保留原选中坐标边界。不完整 export.head、不采样或静默降为元数据。receipt hashing/解压
可能读更多字节，成功不证明未读部分。没有新可见 projection 列或 endpoint API。

## 4. 实施衔接

Store 15 拒绝旧 generation 全部数据读且旧字节不变，无 migration/dual read。recent
缺当前代可为空页而不暴露旧数据；不能 create 旧项目旁路。原版本导出或兼容
环境选择见[cutover](analysis-frame-cutover.md)。RawSqlResult reader 独立，不进入 Frame Store。
开发/合同审查/业务 oracle、provider Runtime、slow 与 release 各自报告；不把代码路线或
历史 Query-to-Artifact provider 结果提升为新 Frame 资格。最终 source-frozen broad gate 归 S8。
