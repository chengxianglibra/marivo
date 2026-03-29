# Agent 交互分析接口设计准则

本文档定义 Factum 在 `docs/analysis/` 语境下的 agent interaction contract（Agent 交互分析接口）设计准则。

状态：draft design。本文是 `docs/analysis/` 下关于 agent-first 分析接口的总纲性设计文档，不表示对应 HTTP endpoint 或 session-state wire contract 已经实现。

## 目的

Factum 的主要上层消费者是 agent。对于这类系统，最容易出现的偏移通常不是“算不出结果”，而是把对外接口设计成了错误的主语：

- 用 SQL-shaped API 让 agent 负责 workflow plumbing
- 用 report-shaped API 让 agent 从长文本里反推结构化状态
- 用“万能 explain / diagnose”执行入口把开放式探索硬塞进不可审计的黑箱流程

本文的目标，是为 `docs/analysis/` 下的 typed intent、canonical evidence schema 与 projection 设计提供一组统一准则，使 Factum 面向 agent 的交互接口始终保持：

- typed
- deterministic
- auditable
- machine-readable
- decision-oriented

## 设计结论

面向 agent 的分析接口，应被设计为三层分离的交互面，而不是单一超载接口：

1. analysis action surface：面向 agent 的 typed analysis intents
2. analysis 状态面（分析状态面）：面向 agent 的 canonical analysis state
3. consumer projection surface：面向 agent / UI 的 bounded projection 与 focus view

其中：

- action surface 回答“可以执行什么分析动作”
- 状态面 回答“当前已经知道什么、正在判断什么、还缺什么”
- projection surface 回答“在有限上下文预算下，优先把哪些 canonical 信息交给 consumer”

这三层必须显式区分，不能被压成同一个“执行后顺便返回结论、建议、摘要”的混合契约。

## 标准交互闭环

在 agent-first 设计下，Factum 与 agent 的默认协作应遵循固定闭环，而不是临时拼接的 request/response 模式：

1. agent 将用户问题翻译为 typed intent
2. Factum 执行 intent，并提交 canonical artifact
3. Factum 基于 artifact 更新 canonical evidence state
4. agent 读取 命题中心 状态面
5. agent 决定：
   - 直接回答
   - 追加新的 typed intent
   - 提出新的 hypothesis / proposition
   - 围绕某个 proposition 拉取 context
6. agent 基于确定性 evidence 生成业务解释与建议

该闭环的关键边界是：

- Factum 负责执行、抽取、评估、状态组织
- agent 负责选动作、解释结果、决定下一步

因此，Factum 不应被设计成“自动跑完整调查”的 workflow brain，agent 也不应退化成负责拼接 SQL、手工维护 evidence graph 的 orchestration glue。

## 非目标

Factum 面向 agent 的交互接口不应被设计为：

- text-to-SQL 的语义包装层
- 人类 BI 页面动作集合
- 以自由文本报告为主体的结果接口
- 替 agent 做开放式探索分支决策的 workflow brain

开放式解释、业务叙事、行动建议整合，默认仍属于 agent 的职责；Factum 负责提供确定性执行与结构化证据状态。

## 交互面分层

### 1. Analysis Action Surface

分析执行面应暴露 typed analysis actions，而不是查询形状。

公共契约应围绕以下概念建模：

- semantic metric
- semantic dimension
- entity scope
- time scope
- typed reference

当 contract 同时需要表达 session-level task boundary 与 planning hint 时，二者也应显式分离；不得把 focus hint 混入 enforced scope。对于 Factum 的根容器 schema，这意味着 session root 不应持有 enforced execution scope，执行约束应进入具体 action 的 step params。

而不是围绕以下内容建模：

- raw SQL
- 任意列组合
- 任意 join 策略
- ad hoc 执行计划

action surface 的职责是让 agent 选择与调用分析动作，例如：

- atomic intents：`observe`、`compare`、`decompose`、`detect`、`test`
- derived intents：`attribute`、`diagnose`、`validate`

它不应承担：

- 输出开放式业务结论
- 定义证据状态摘要
- 生成唯一 planning 入口

### 2. Analysis State Surface

分析状态面应暴露 canonical analysis state，而不是要求 agent 反复回读完整 artifact 或从 narrative 中反推结构化状态。

推荐的规范抽象链路为：

`artifact -> finding -> proposition -> assessment -> action proposal`

其中：

- `artifact` 是完整执行结果与权威 lineage 入口
- `finding` 是确定性抽取的事实单元
- `proposition` 是待评估命题
- `assessment` 是当前判断状态
- `action proposal` 是外层动作候选，而不是核心证据层

状态面 的主读取轴应默认围绕 `proposition + latest assessment` 组织，而不是围绕 artifact 列表或 subject 聚合摘要组织。

原因是：

- proposition 是 agent 的稳定判断对象
- assessment 是 agent 的稳定决策对象
- gaps / inference records / action proposals 都天然围绕 proposition 轨道演化

subject-centered 视图可以存在，但更适合作为导航索引，而不是主状态骨架。

状态面 应首先支持 agent 回答以下问题：

- 已经确定知道什么
- 当前在判断什么
- 当前判断到什么程度
- 哪些 evidence gaps 阻塞了推进
- 哪些 action proposals 只是 shortcut，而不是唯一入口

### 3. Consumer Projection Surface

projection surface 是 canonical action / state 的 bounded consumer view。

它可以做：

- 稳定排序
- 稳定截断
- 聚合展示
- token-budget 压缩
- focus view 组织

它不得做：

- 发明 规范状态 中不存在的新事实
- 用 projection ref 替代 canonical source ref
- 改写 规范标识
- 以展示逻辑偷偷重定义分析语义

若 projection / view 对主集合做 top-k 截断，则其返回的 supporting collections 仍必须对 returned canonical objects 保持自洽 closure，不得残留被截断对象的 state members。

## 主读取模式

面向 agent 的主读取模式应区分两类：

### 1. State Surface：全局决策读取

State surface 用于回答“当前 session 整体最值得关注什么”。

其默认内容应围绕：

- active propositions
- latest assessments
- blocking gaps
- optional recommended next actions
- backing findings
- artifact refs

组织。

它的职责是帮助 agent：

- 判断是否已经足够回答用户
- 判断是否还存在关键证据缺口
- 判断下一步最值得跑哪类 intent

### 2. Context Surface（上下文面）：局部最小闭包读取

Context surface 用于回答“围绕一个 proposition / hypothesis，我最少需要哪些 canonical 对象才能解释和决策”。

默认局部闭包应至少包含：

- target proposition
- 最新评估
- supporting findings
- opposing findings
- blocking / non-blocking gaps
- applied inference records
- backing artifact refs

它的职责是帮助 agent：

- 深挖某个判断对象
- 生成可追溯解释
- 判断某个 hypothesis 是否值得继续验证

v1 语义上，上下文面 的 canonical target 应由 typed proposition ref 唯一确定；compact / audit 等读取裁剪属于 projection 扩展，而不是主 query shape。

## Agent 决策原则

本文不是 planner 或 policy 文档，因此不规定 agent 的具体决策算法；但面向 agent 的交互准则至少应鼓励以下消费方式：

- 优先读取 命题中心 state，而不是回读完整 artifact
- 优先识别 blocking gaps，再决定是否追加新的 typed intent
- 把 action proposal 视为 planning shortcut，而不是唯一决策入口
- 将“当前是否已经足够回答用户”视为 agent 基于 assessment、gaps 与业务上下文做出的自主判断

相应地，Factum 应提供的是低歧义、可决策的状态接口，而不是替 agent 固化停止规则、阈值或动作选择算法。

## 核心准则

### 1. 以分析意图为主语，而不是以查询形状为主语

agent 应决定“看哪个 metric、比较哪两边、围绕哪个 hypothesis 做验证”，而不是负责拼接 SQL 结构。

因此对外 contract 应优先暴露 typed analysis intents，不暴露 raw SQL 作为主契约。

### 2. 优先消费 typed refs，而不是重复描述上游语义

当某个动作依赖上游结果时，应优先传 typed refs，例如：

- `compare(left_ref, right_ref)`
- `decompose(compare_ref, dimension)`
- `test(left_ref, right_ref)`

而不是让 agent 重复传递 scope、时间窗与中间语义。

typed refs 必须：

- machine-readable
- 可校验
- 指向 规范对象
- 不退化为裸字符串 locator

### 3. 执行面与状态面必须分离

单次执行返回可以带 lineage、工件引用、bounded projection，但不应顺便承担完整 state snapshot 的职责。

相应地，session / evidence state 的读取接口也不应混入新的执行语义。

如果某个对象同时在表达：

- 执行动作输入
- 当前判断状态
- 行动建议
- 报告式解释

通常说明契约已经混层。

### 4. 证据边界必须 deterministic

facts / evidence 必须由代码确定性抽取。

模型可以：

- 解释 findings / assessments
- 帮助 agent 做业务表达
- 帮助 agent 选择下一步动作

模型不应：

- 定义 evidence structure
- 决定 canonical finding boundary
- 生成不可回放的核心判断对象

### 5. Artifact / State / Projection 必须显式分离

对 agent 友好不意味着把 artifact 做小，也不意味着让 projection 承担 source identity。

正确分工应为：

- artifact：完整、可复现、可引用
- state：可决策、可追溯、可局部读取
- projection：有界、稳定、压缩后的消费视图

### 6. 局部最小闭包读取是主契约的一部分

agent 的典型任务不是“遍历整个 evidence graph”，而是围绕一个 subject、proposition 或当前问题拉取最小必要上下文。

因此面向 agent 的 contract 必须定义：

- 可查询轴
- 默认排序
- 稳定截断
- typed refs 回查方式
- 局部最小闭包

如果一个对象被声明为 agent 可直接消费，但没有定义这些读取规则，它就还不是低歧义的 agent contract。

### 7. 显式暴露不确定性、缺口与规则过程

对 agent 来说，不够的是“结论文本”，而不是“解释文本”。

状态面 至少应让 agent 读到：

- proposition
- 最新评估
- supporting / opposing findings
- blocking / non-blocking gaps
- inference records 或等价规则过程线索

否则 agent 无法可靠决定下一步。

### 8. Recommendation 只能是 shortcut，不是唯一 planning 入口

`action proposal` 可以提高效率，但 agent 必须能够绕过它，直接基于 findings + propositions + assessments 自主规划。

因此 recommendation / action proposal 不能反向定义核心 evidence semantics。

在未定义稳定 session-level policy 的 v1 state schema 中，`recommended next actions` 可以不作为主状态默认字段返回；但若存在该字段，它仍只应被解释为 shortcut，而不是主判断骨架。

### 8.1 Agent-authored proposition 应进入统一 assessment 轨道

当 agent 显式提出 hypothesis 时，系统应允许其以 `agent_authored proposition` 的形式进入 规范状态，而不是停留在自由文本上下文里。

但 agent 不应直接写入 assessment。正确边界应为：

- agent 可以提出 proposition semantics
- Factum 负责将其纳入统一 inference / assessment / gap 流程
- authored proposition 与 system-seeded proposition 共享同一 状态面 和 上下文面

同时应满足以下原则性约束：

- authored proposition 必须落在已定义的 typed proposition family 内
- authored proposition 不得绕过 canonical judgment machinery
- authored proposition 的具体校验、可允许 family、以及与 system-seeded proposition 的冲突处理，应在 proposition / assessment 的具体设计文档中定义

这样既能保留 agent 的开放式分析能力，又不会破坏 evidence state 的 deterministic judgment machinery。

### 9. 稳定语义默认值优先于实现 knobs

应优先暴露：

- `profile`
- `mode`
- `sensitivity`
- `priority policy`

这类稳定语义控制项。

不应把 detector 算法名、SQL 片段或大量低层调参面直接暴露给 agent。

### 10. Derived intent 只封装确定性 DAG

高频完整分析动作可以封装为 derived intent，但前提是：

- 内部展开完全确定
- 内部步骤不依赖外部临时决策
- 最终结果能形成稳定有界 projection

若某能力需要“看完中间结果再决定下一步”，它更适合做 template / policy，而不是 executable intent。

## Projection 一致性原则

projection 的职责是压缩 canonical action / state，而不是形成独立语义层。

因此应遵守以下原则：

- projection 必须从单一 规范状态 snapshot 派生
- projection 的判断语义从属于 规范状态，不得拥有独立判断结论
- 若多个 projection 同时暴露同一 规范对象，它们必须指向同一 source identity
- projection 可以排序、截断、聚焦，但不得制造跨 snapshot 拼接的混合视图

projection 的刷新时机、版本机制与缓存策略属于后续具体 state / runtime 设计文档，不在本文定义。

## 失败与一致性原则

本文不定义 transaction、回滚或并发控制机制，但面向 agent 的交互准则至少应满足以下要求：

- intent 执行失败不得破坏已提交 规范状态
- 读取面不得暴露半更新状态
- 规范状态 的一致性边界优先于 projection 的可见性
- proposition、assessment、gap 与 action proposal 的具体恢复、回滚和版本机制，应在专门的 lifecycle / runtime 设计文档中定义

## 负向契约

为避免文档与实现后续重新滑回混合模型，`docs/analysis/` 下的交互设计应明确拒绝以下形态：

- 把 raw SQL 作为对外主契约
- 用 projection ref 充当 canonical source ref
- 用一个 claim / summary 对象混装事实、判断、建议
- 让自由文本 explanation 成为核心 evidence structure
- 让 recommendation 成为唯一 planning 入口
- 让 derived intent 在执行过程中依赖开放式分支决策

## 与现有文档的关系

本文是 `docs/analysis/` 中面向 agent 交互接口的总纲。

相关分工如下：

- [`agent-first-intent-architecture.md`](agent-first-intent-architecture.md) 负责说明 action surface 中 atomic / derived / template 的架构分层
- [`evidence-engine-design.md`](evidence-engine-design.md) 负责说明 状态面 中 artifact / finding / proposition / assessment / action proposal 的规范分层
- [`state-surface-schema.md`](state-surface-schema.md) 负责说明 命题中心 session state 的 canonical 读取契约
- [`context-surface-schema.md`](context-surface-schema.md) 负责说明 命题中心 上下文面 的 canonical 读取契约
- [`canonical-schema-principles.md`](canonical-schema-principles.md) 负责说明所有 规范对象 在 identity、lineage、nullability、projection、agent consumption contract 上必须遵守的通用原则

若后续新增新的 intent、schema 或 session-state 读取设计，应先检查其是否与本文定义的三层交互面保持一致，再进入具体 schema 细化。
