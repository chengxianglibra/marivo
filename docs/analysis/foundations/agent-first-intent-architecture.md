# Agent-First 意图架构

本文档讨论一个前提明确的设计问题：当 Agent 是 Factum 的唯一用户时，Factum 应如何设计原子意图（atomic intents）与派生意图（derived intents），才能既保持 typed、deterministic、auditable 的分析契约，又最大化 LLM 在真实互联网企业数据分析场景中的能力发挥。

状态：draft design。本文是 analysis intent family 的架构性设计说明，不表示文中所有意图都已实现。

## 设计结论

在 Agent-only 的前提下，Factum 不应被设计为：

- 面向人工交互的 BI 页面动作集合
- text-to-SQL 的语义包装层
- 试图替 Agent 做开放式探索决策的 workflow brain

Factum 更适合被设计为三层分析运行时：

1. 原子意图（atomic intents）：稳定、可组合、可审计的分析语义原语
2. 派生意图（derived intents）：面向高频完整分析动作的确定性 DAG 宏
3. 模板 / 策略（templates / policies）：只给 Agent 提供分析套路，不直接作为可执行契约

其中：

- 原子层负责产出硬证据与稳定中间工件
- 派生层负责封装高频、固定、可确定性展开的分析套路
- Agent 负责理解业务问题、选择分析动作、解释结果、决定下一步探索

这个分工最符合 Factum 的核心约束：

- 外部契约应是类型化分析步骤（typed analysis steps），而不是 SQL
- 事实 / 证据必须由代码确定性抽取
- 模型适合做解释（explanation），不适合定义证据结构（evidence structure）

## 为什么 Agent-only 反而更需要清晰分层

如果 Agent 是唯一用户，系统很容易滑向两个极端：

- 极端一：暴露过多底层查询自由度，让 Agent 自己拼所有东西
- 极端二：把开放式分析流程封装进“万能 explain intent”

这两种方向都不理想。

第一种会让 Agent 把大量 token 和决策预算耗在 workflow plumbing 上，例如重复拼接窗口、重复连接上游引用、重复处理排序和截断规则。第二种会把系统推进到不稳定、不可审计的黑箱分析执行模型里。

更好的方式是：

- 用原子意图提供稳定推理语法
- 用派生意图封装高频固定套路
- 把开放式分支探索留给 Agent

这意味着 Factum 的目标不是减少步骤数本身，而是减少无意义的流程拼装成本。

## 面向 Agent 的交互契约边界

从交互面看，Factum 不应向 Agent 提供一个混合了执行、状态、摘要和建议的单一大接口，而应保持三层分离：

1. 分析动作面（analysis action surface）：让 Agent 调用类型化分析意图（typed analysis intents）
2. 分析状态面（analysis state surface）：让 Agent 读取机器可读的证据状态（machine-readable evidence state）
3. 消费者投影面（consumer projection surface）：让 Agent 在有限上下文预算下读取有界投影（bounded projection）/ focus view

在这三层里，本文主要定义第一层的架构边界：

- Agent 通过原子 / 派生意图（atomic / derived intents）发起分析动作
- Factum 负责确定性执行、typed ref 连线、工件（artifact）生成与投影（projection）
- Agent 负责解释业务含义、决定下一步探索、消费分析状态面中的事实单元（findings）/ 评估（assessments）/ 缺口（gaps）

因此，意图层的职责不是输出一份“最终报告”，而是给 Agent 一个稳定的分析动作语法。

关于三层交互面的完整总纲，见 [`agent-interaction-contract-principles.md`](agent-interaction-contract-principles.md)。

## Agent 应该把能力用在哪里

在真实分析任务中，LLM 最有价值的部分不是执行层，而是语义层与解释层。

Agent 最适合承担：

- 把自然语言业务问题翻译成类型化分析请求（typed analysis request）
- 识别本轮任务更适合 `observe`、`attribute` 还是 `diagnose`
- 设定 metric、entity scope、time_scope、candidate dimensions、hypothesis
- 结合多个确定性工件生成业务解释
- 判断还有哪些证据缺口、下一步该问什么

Factum 最适合承担：

- 语义解析后的确定性执行
- 结构化证据抽取
- 中间工件保存、引用、投影（projection）与审计
- 固定分析套路的 DAG 展开

一句话说，Agent 负责“想清楚问什么”，Factum 负责“把回答做成可验证工件”。

## 原子意图的设计目标

原子意图不应该按“页面动作”或“底层算子”来定义，而应该按“最小分析问题”来定义。

一个原子意图应回答一个独立分析问题，并且满足：

- 输入类型稳定
- 输出语义稳定
- 能被后续步骤引用
- 能被确定性压缩为 agent-friendly projection
- 不吸收相邻职责

当前文档中的原子集合方向是合理的：

- `observe`：读取一个类型化观测
- `compare`：量化两个观测之间的差异
- `decompose`：把已知 delta 分配到排序后的贡献项
- `correlate`：估计两个时间序列之间的统计关联
- `detect`：在时间范围中识别异常候选
- `test`：对明确假设执行统计检验
- `forecast`：把有界历史序列投影到未来 bucket

这些意图的价值不在于“覆盖一切分析动作”，而在于它们共同构成了一组低歧义、可组合的分析语义原语。

## 原子意图的 Agent-first 设计原则

### 1. 以 semantic object 为主语，而不是以查询形状为主语

原子意图请求应围绕：

- semantic metric
- semantic dimension
- entity scope
- time scope
- typed reference

而不是围绕：

- SQL 片段
- 任意列组合
- 任意 join 策略
- 任意执行计划

Agent 可以很擅长决定“看哪个指标、按什么 scope、比较哪两边”，但不应该承担“拼什么 SQL 结构”这类低价值工作。

### 2. 优先消费 typed reference

当一个意图依赖上游结果时，最好以 typed reference 为主输入，而不是让 Agent 重复描述上游语义。

例如：

- `compare(left_ref, right_ref)` 优于重新传两份 scope
- `decompose(compare_ref, dimension)` 优于重复描述 compare 两侧
- `correlate(left_ref, right_ref)` 优于让 Agent 手工保证序列对齐

这能减少上下文重复、避免语义漂移，也更利于审计。

### 3. 工件完整，投影有界

原子意图要同时服务两个消费者：

- 下游步骤，需要完整 artifact
- Agent，需要高信号 projection

因此必须坚持 artifact / projection 分层：

- artifact 保证可复现、可引用、可审计
- projection 保证在 token 预算里仍保留主要语义

如果某个意图无法形成稳定有界的 projection，它通常不适合作为面向 Agent 的原子意图。

### 4. 原子意图只交付硬证据，不交付开放式结论

原子意图的核心输出应是：

- observation
- delta
- contribution row
- anomaly candidate
- test result
- forecast point

而不应把以下内容塞进原子契约主体：

- 根因结论
- 行动建议
- 自由文本解释
- 开放式诊断摘要

这些内容可以在更高层由 Agent 基于确定性工件生成。

## 派生意图的设计目标

派生意图的意义，在 Agent-only 场景下会比传统 UI 产品更强。

原因不是 Agent 不能自己拼 DAG，而是大量真实分析问题会稳定重复同一套路。如果系统每次都要求 Agent 手工拼接：

- 上下游步骤引用
- baseline 推导
- follow-up 排序
- top-k 截断
- 中间结果汇总

那么 Agent 会把大量预算浪费在重复性流程控制上，而不是业务分析本身。

因此，派生意图的建模标准应该是：

- 这是用户自然会表达成“完整动作”的分析请求
- 其内部 DAG 可以完全由请求和系统状态确定性展开
- 它在真实业务里高频出现
- 它的结果可以被压缩成有界、稳定、仍有意义的派生工件

## 哪些高层动作适合做派生意图

### 1. `attribute`

`attribute` 适合保留为派生意图，因为“解释一次已知变化”是高频、固定、完整的业务动作。

典型问题：

- 本周 DAU 比上周下降了，主要由哪些渠道驱动
- treatment 比 control uplift 更高，主要由哪些用户段贡献
- GMV 变化主要来自哪些 region 或 category

其展开稳定，适合由系统固定为：

- `observe`
- `observe`
- `compare`
- one or more `decompose`

### 2. `diagnose`

`diagnose` 同样适合作为派生意图，因为“发现异常并跟进量化和归因”是数据分析运营中的典型固定套路。

典型问题：

- 昨晚转化率异常波动，异常点在哪里
- 哪些 slice 的 watch time 在最近一周出现不正常下滑
- 某个核心指标出现 spike/dip 后，主要驱动维度是什么

只要 follow-up 策略、baseline policy、candidate ranking policy 都是固定的，它就适合作为派生意图，而不应每次由 Agent 手工拼装。

### 3. `validate`

`validate` 也很适合作为派生意图。

真实分析里常见的问题不是“有没有数据”，而是“某个怀疑是否站得住”。例如：

- 新策略是否真的提升了留存
- 某段流量的质量差异是否显著
- 某次改版后转化率差异是否可能只是噪音

只要它能稳定展开为“准备观测 + 构造样本 + 执行检验”，就应该提供一个固定契约，而不是要求 Agent 重复手工组织。

## 哪些能力不应该做成派生意图

以下能力看起来“高级”，但不应进入可执行意图层：

- `explain`
- `describe_everything`
- `find_interesting_segments`
- `drill_down_until_root_cause`
- `tell_me_what_happened`

原因是它们在执行过程中依赖开放式决策：

- 看完中间结果后才知道要不要继续
- 不同中间发现会触发不同下钻路径
- “最值得分析的维度”无法仅靠契约固定
- 最终结果缺少稳定的有界输出形式

这类能力更适合做 template、playbook 或 planner hint，而不是 Factum 的 executable intent。

## 在真实互联网企业分析场景中的分工

互联网企业里的常见分析问题，大致都会落在以下结构中：

1. 发现问题
2. 确认问题规模
3. 限定影响范围
4. 找主要驱动因素
5. 验证一个怀疑
6. 形成业务解释与行动建议

其中更适合进入 Factum typed contract 的，是 1 到 5；第 6 步主要应由 Agent 完成。

对应关系可以写成：

- 发现问题：`detect`
- 确认问题规模：`observe` / `compare`
- 限定影响范围：`observe(segmented)` / `decompose`
- 找主要驱动因素：`attribute` / `diagnose`
- 验证一个怀疑：`test` / `validate`
- 形成业务解释与行动建议：Agent 基于 artifacts / projections 完成

这正好利用了双方最擅长的部分：

- Factum 擅长确定性证据生产
- Agent 擅长业务语义理解与解释整合

## Agent 的默认调用策略

当 Agent 是唯一用户时，建议默认遵循以下策略。

### 优先派生，次选原子

若用户问题天然对应一个完整高频分析动作，应优先选择派生意图：

- “解释这次变化” -> `attribute`
- “诊断这次异常” -> `diagnose`
- “验证这个怀疑” -> `validate`

只有当问题不适合任何稳定派生契约时，才退回原子组合。

### 原子层是语义基础，不是默认产品入口

原子层必须存在，因为它定义了 Factum 的语义积木与权威边界。

但在 Agent-first 产品设计里，原子层不应成为默认入口。否则 Agent 会频繁承担：

- 手工连接中间引用
- 重复补默认值
- 重复处理 follow-up 和 truncation policy

这会降低系统整体分析效率。

### 模板只指导，不执行

对于开放式探索类任务，Factum 可以提供：

- 推荐套路
- 分析 playbook
- plan skeleton
- 下一步建议

但它们不应伪装成一个“可执行 intent”。

## 建议的意图层产品原则

如果把 Agent 视为 Factum 的唯一上层产品，那么产品原则应明确为：

- 原子意图服务于分析推理原语
- 派生意图服务于高频完整业务动作
- 模板服务于开放式探索指导
- evidence contract 只承载确定性事实，不承载自由解释
- explanation 与 recommendation 默认由 Agent 负责

这组原则能同时避免两个问题：

- 系统退化为 text-to-SQL
- 系统膨胀成不可审计的开放式 analysis workflow engine

## 对现有设计稿的影响

对现有文档体系而言，本文支持以下方向：

- 继续保持原子意图的窄边界与 typed reference 设计
- 继续把派生意图定义为确定性 atomic DAG 宏
- 继续把 `attribute`、`diagnose`、`validate` 视为更自然的 agent-facing 高层动作
- 明确把开放式 `explain`、`describe`、`find_interesting_segments` 留在模板层

因此，若后续新增分析能力，应优先回答以下问题：

1. 这是一个最小分析问题，还是一个高频完整动作？
2. 它的展开能否完全确定？
3. 它的结果能否形成稳定有界投影？
4. 它是在生产证据，还是在消费证据做解释？

若答案偏向“高频完整动作 + 可确定展开 + 生产证据”，应优先考虑派生意图。若答案偏向“开放式解释 + 依赖中间决策”，则应留给 Agent。

## 一句话总结

在 Agent-only 架构下，Factum 最好的定位不是“替 Agent 分析”，而是“为 Agent 提供稳定、可组合、可审计的分析语义 runtime”。

原子意图是语义原语，派生意图是高频分析宏，开放式探索与业务解释则仍然属于 Agent。
