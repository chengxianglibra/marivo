# Factum + Agent 数据分析能力缺口与架构增强路线

## 1. 目的

本文基于当前 Factum 的设计架构，以及围绕 `agent + Factum` 在互联网企业数据分析场景中的适用性讨论，整理出一份面向产品与架构演进的路线文档。

文档采用如下组织方式：

- **能力缺口**
- **推荐架构模块**
- **对应支持的互联网场景**

目标不是定义精确的实施排期，而是澄清：

1. 当前 `Factum + 通用 agent` 为什么难以覆盖复杂真实场景
2. Factum 需要补哪些系统能力，才能成为更强的数据分析底座
3. 哪些增强属于高优先级基础设施，哪些属于更高阶的分析能力

---

## 2. 当前判断摘要

当前 Factum 的强项在于：

- typed intent
- semantic layer
- deterministic evidence packaging
- session / artifact / provenance 基础能力
- 适合被 agent 调用的 HTTP-only 接口

当前 Factum 的弱项在于：

- 对开放式探索支持不足
- 对多实体 / 多域问题支持不足
- 缺少面向 agent 的持久化 workflow / runtime
- 高阶统计、实验、因果能力较弱
- 明细诊断与聚合分析之间缺少过渡层
- 缺少足够成熟的 agent-facing state / context surface

因此，Factum 现在更像：

> **可审计的 typed analysis execution layer**

而不是：

> **可承接复杂互联网数据分析全过程的 analysis operating system**

---

## 3. 能力缺口 → 推荐架构模块 → 对应支持的互联网场景

## 3.1 缺口一：只能较好支持“明确问题”，难以支持“边看边分析”

### 能力缺口

当前 Factum 更适合执行已经收敛成 typed intent 的分析动作，例如：

- `observe`
- `compare`
- `decompose`
- `detect`
- `correlate`
- `test`
- `forecast`

但真实分析过程往往不是一开始就知道该调用哪个 intent。很多互联网场景都要求：

- 先看哪里异常
- 再试几个切片
- 再决定下钻哪个维度
- 再根据中间结果切换分析路径

这类“探索中收敛”的过程，目前更多依赖 agent 在 Factum 外部临时编排，系统内部缺少统一的探索与收敛层。

### 推荐架构模块

引入 **开放式探索层（Exploration Surface）**，并明确与 canonical intent / evidence layer 分层。

建议包含两类接口：

#### A. Metadata Exploration

- entity / metric / dimension discovery
- join path / relationship discovery
- scope legality / grain legality discovery
- capability discovery
- metric family / comparability discovery

作用是回答：

> 有哪些可分析对象？这些对象能以什么方式被分析？

#### B. Bounded Data Exploration

- dimension candidate ranking
- slice scanning
- roll-up / drill-down
- candidate anomaly probing
- bounded aggregate probing
- metric candidate discovery

作用是回答：

> 针对当前问题，接下来最值得往哪查？

关键设计原则：

- exploration 输出不直接等价于 canonical evidence
- exploration 允许更灵活，但输出必须有界
- 只有收敛后的结果才提升为正式 artifact / finding / proposition

### 对应支持的互联网场景

- DAU / 留存下滑后的开放式根因分析
- 电商 GMV 下滑后的逐层排查
- 退款率异常后先扫渠道 / 商家 / 商品 / 区域，再下钻
- 内容平台播放时长下降后先看人群、供给、渠道、版本
- 运营 / 分析师“先看看哪里不对，再决定查什么”的探索式工作流

---

## 3.2 缺口二：agent 能编排，但系统内没有统一 workflow/runtime

### 能力缺口

当前可以让 agent 在 Factum 外编排流程，但如果只靠 agent 编排，会出现几个问题：

- 状态散落在 prompt / memory / 外部脚本里
- 分析步骤之间的依赖关系缺少统一模型
- 出错后难以从中间状态恢复
- 不同 agent 对同类问题会走不同路径，难复现
- 治理、预算、并发、审批等约束难形成硬执行语义

换句话说，agent 可以负责“思考”，但不能替代执行系统。

### 推荐架构模块

引入 **Factum Workflow / Runtime Layer**，作为 agent 的系统化执行层。

建议能力包括：

- typed plan / workflow graph
- checkpoint / resume
- branching / loop / retry / rollback
- artifact dependency graph
- reusable workflow policy hooks
- execution cost accounting
- approval gates
- stateful run log / replay

边界建议：

- **agent 负责决策、解释、探索策略**
- **Factum runtime 负责执行、持久化、约束、记账、回放**

### 对应支持的互联网场景

- 多轮经营诊断：先查趋势，再查漏斗，再做归因
- 异常排查中被中断后续跑
- 多个 agent 分工协作做联合诊断
- 同一类分析模板复用于多个业务团队
- 需要审计“为什么跑了这些步骤”的高价值分析流程

---

## 3.3 缺口三：单指标、单域分析较强，但多实体 / 多域问题支持弱

### 能力缺口

互联网企业里的核心问题通常横跨多个实体与业务域，例如：

- 用户
- 设备
- 订单
- 商品
- 商家
- 广告
- 履约
- 客服
- 风控

现实问题也往往是跨域的：

- GMV 下滑是流量、转化、库存还是履约导致？
- 新客下滑是投放问题、转化问题还是供给问题？
- 推荐 CTR 下滑是召回、排序、内容质量还是实验流量污染导致？

如果 Factum 只围绕单 metric / 单 step 建模，agent 很难稳定做跨域诊断。

### 推荐架构模块

引入 **多实体 / 多域分析层（Multi-Entity / Multi-Domain Analysis Layer）**。

建议包含：

- semantic relationship graph
- entity graph / business object graph
- cross-domain subject model
- join semantics registry
- path-aware federation
- multi-hop analysis primitives
- business process composite objects

建议优先建模的复合对象包括：

- funnel
- journey
- order lifecycle
- supply chain slice
- merchant performance slice
- campaign conversion chain

理论基础方面，可借鉴：

- entity-centric analytics
- multilevel / hierarchical modeling
- panel data analysis
- SCM / DAG
- process mining
- graph analytics

### 对应支持的互联网场景

- 电商 GMV / 转化 / 退款 / 履约联合诊断
- 广告投放到新客 / 留存 / 复购的跨链路分析
- 推荐系统从召回到转化的多环节排查
- 风控系统跨用户 / 设备 / 订单 / 支付关系分析
- 客服、商家、物流、订单联动的问题定位

---

## 3.4 缺口四：统计分析能力有基础，但不足以支撑高价值业务结论

### 能力缺口

当前 Factum 已有：

- `correlate`
- `test`
- `forecast`

但这些更偏轻量基础能力，还不足以稳定支撑：

- 实验分析
- 异质性分析
- 因果判断
- 经营模拟
- 干预效果评估

在真实业务里，用户经常问的不是：

> 两个数是否相关？

而是：

> 这个动作是否导致了结果变化？

### 推荐架构模块

引入 **高级统计与因果能力层（Advanced Stats / Causal Layer）**。

建议分三块建设：

#### A. Experiment Analysis

- multi-arm experiment support
- stratified analysis
- sequential testing
- covariate adjustment
- heterogeneous treatment effect
- experiment quality diagnostics

#### B. Causal Inference

- causal graph / assumption registry
- confounder registry
- DiD
- IV
- matching / weighting
- synthetic control
- sensitivity analysis

#### C. Scenario / Intervention Simulation

- intervention contract
- scenario planner
- lagged / distributed effect analysis
- constraint-aware simulation
- uncertainty propagation

关键原则：

- 证据结构仍保持 deterministic
- LLM 负责解释与表达，不负责定义事实
- 因果强度必须显式分层，不允许把相关直接包装成原因

### 对应支持的互联网场景

- 广告投放减少是否导致新客下滑
- 补贴策略变化是否影响转化和复购
- 多臂 A/B 实验的收益评估
- 推荐策略切换是否导致 CTR / GMV 变化
- 版本发布后留存下降是否是代码改动导致
- 节假日、供给变化、活动变化混在一起时的影响识别

---

## 3.5 缺口五：缺少从聚合分析走向明细诊断的安全过渡层

### 能力缺口

很多互联网分析问题最后一定会走到明细层，例如：

- 哪些订单失败了？
- 哪些用户受影响最严重？
- 哪些错误码集中爆发？
- 哪些 trace / request 最可疑？

但如果 Factum 直接把自己做成无边界明细查询平台，就会破坏当前设计中强调的：

- typed contract
- bounded output
- governance-aware execution

因此问题不是“要不要支持明细”，而是“如何以受控方式接入明细诊断”。

### 推荐架构模块

引入 **聚合到明细的过渡层（Aggregate-to-Detail Drill Bridge）**。

建议能力包括：

- exception cohort extraction
- representative case sampling
- top failure slice materialization
- trace / log linkage refs
- detail handle instead of direct raw dump
- governed detail drill contract

建议输出形式：

- 不直接把大量明细塞进 canonical artifact
- 而是返回可治理、可审计的 detail handle / cohort ref / trace ref

这样 agent 可以继续下钻，但明细访问仍在系统控制之下。

### 对应支持的互联网场景

- 支付成功率突降后的失败订单诊断
- 履约异常后的订单级问题排查
- 风控误杀后的样本回溯
- 推荐 / 广告请求链路中的 trace 级定位
- 客服投诉高发后的 case sampling

---

## 3.6 缺口六：缺少真正面向 agent 的 state / context / gap surface

### 能力缺口

如果 agent 要持续分析，它需要的不只是某一步结果，而是：

- 当前 session 到底分析到哪了
- 已经形成了哪些 proposition / hypothesis
- 哪些 proposition 已被支持、反驳或阻塞
- 当前缺的关键证据是什么
- 下一步最值得做什么

当前 Factum 在这方面已有设计方向，但还需要更完整、正式、稳定的读面。

### 推荐架构模块

引入并完善 **Agent State Surface**。

建议包含：

- canonical session state
- proposition context
- gap registry
- readiness surface
- next-best-step suggestion surface
- confidence / provenance / cost / quality joint read surface
- reflection / planning context 的正式化 contract

这层的作用不是替 agent 决策，而是给 agent 一个统一、机器可消费的“分析状态面”。

### 对应支持的互联网场景

- 长链路分析任务的持续推进
- 多轮、多天的经营问题排查
- 多个 agent 或人机协同分析
- 需要中间汇报和阶段性决策的业务诊断
- 需要知道“还缺什么证据才能下结论”的高风险场景

---

## 3.7 缺口七：通用 agent 可用，但缺少面向数据分析的专用行为约束

### 能力缺口

通用 agent（Claude Code、Codex、OpenCode、OpenClaw 等）有明显优势：

- 任务拆解能力强
- 解释能力强
- 交互能力强
- 能较好调用工具

但它们在数据分析场景里也有天然短板：

- 不天然遵守统计与因果规范
- 容易过度探索、成本高
- 对 metric / entity / scope / comparability 不敏感
- 同题不稳定，分析路径漂移大
- 容易把相关说成原因
- 不擅长把探索收敛成标准化证据

### 推荐架构模块

在 Factum 之上建设 **Factum-native Analysis Agent**，或至少为通用 agent 提供强约束的分析代理层。

建议包括：

- analysis playbooks
- intent selection policies
- metric / entity aware prompting contracts
- statistical guardrails
- causal language guardrails
- bounded exploration heuristics
- evidence-first answer policy

推荐落地方式：

1. 早期先用通用 agent 验证 Factum 可用性
2. 中期补 Factum-native analysis agent profile
3. 后期形成专用分析 agent 或分析代理框架

### 对应支持的互联网场景

- 标准化经营诊断
- 指标异常周报分析
- 实验结论自动生成
- 运营 / 产品 / 商业分析协作场景
- 需要低成本、可复用、可审计分析路径的日常数据分析

---

## 4. 优先级建议

如果只从“尽快让 `Factum + agent` 支持更多真实互联网分析场景”的角度看，建议分三层推进。

### 第一优先级：先补基础执行与探索骨架

包括：

- workflow / runtime
- exploration surface
- session state / proposition context / gap surface

这是让 agent 能稳定“边看边分析”的最小基础。

### 第二优先级：再补跨域问题的语义与分析建模

包括：

- multi-entity / multi-domain semantic graph
- business object composites
- cross-domain analysis primitives

这是让 Factum 能承接互联网经营分析、链路诊断、跨团队问题排查的关键。

### 第三优先级：最后补高阶判断能力

包括：

- advanced stats
- experiment analysis
- causal inference
- scenario / intervention simulation
- aggregate-to-detail drill bridge

这是从“能分析”走向“能支持关键业务决策”的关键。

---

## 4.1 典型互联网场景与能力需求映射

为了让路线图更容易用于产品决策和架构评审，可以把常见互联网分析场景进一步拆成：

- 场景本身
- 当前主要卡点
- 关键能力需求

### 场景映射表

| 互联网场景 | 当前主要卡点 | 关键能力需求 |
|---|---|---|
| DAU / 留存下滑根因分析 | 需要先广泛试探，再逐步收敛 | exploration surface、branching workflow、候选维度 / 指标排序 |
| GMV 下滑跨链路诊断 | 同时涉及流量、转化、库存、履约等多域 | 多实体 / 多域模型、semantic relationship graph、cross-domain proposition |
| 广告投放效果判断 | 相关不等于因果，且存在大量混淆因素 | experiment analysis、causal layer、confounder registry |
| 推荐 CTR 下滑分析 | 召回、排序、供给、实验、流量结构多环节联动 | multi-hop analysis、process / journey objects、detail drill bridge |
| 支付成功率异常排障 | 最终必须落到订单、错误码、trace | aggregate-to-detail bridge、cohort refs、trace / log handles |
| 多臂 A/B 实验分析 | 当前检验能力太轻，无法覆盖复杂设计 | multi-arm experiment、covariate adjustment、heterogeneous effect |
| 风控误杀 / 欺诈分析 | 用户、设备、订单、支付之间关系复杂 | entity graph、network / path analysis、case sampling |
| 运营自由探索式分析 | 问题和路径会不断变化 | exploration state、checkpoint、next-best-step |
| 多轮人机协作分析 | 中途切换 agent / 人工时缺少统一状态基线 | session state、proposition context、workflow runtime |
| 经营预测与策略推演 | forecast 不等于 intervention，缺少干预语义 | scenario simulation、lag effect、constraint-aware planning |

### 五类核心能力需求归纳

从上表可以进一步归纳出五类高频、通用、且对复杂互联网场景最关键的能力需求。

#### 1. 探索能力

目标是把模糊问题逐步收敛为可执行 intent。

包括：

- metadata exploration
- bounded data exploration
- 候选切片 / 维度 / 指标排序
- 基于中间结果的路径试探

最典型支持的场景：

- DAU 下滑原因探索
- 退款率异常定位
- 运营自由探索式分析

#### 2. 执行能力

目标是让复杂分析流程成为可恢复、可回放、可治理的系统执行过程。

包括：

- workflow / runtime
- checkpoint / resume
- branching / retry
- run log / replay

最典型支持的场景：

- 多轮经营问题排查
- 多 agent 协作分析
- 高价值问题的审计与复盘

#### 3. 跨域能力

目标是支持跨实体、跨链路、跨业务域的问题诊断。

包括：

- multi-entity / multi-domain graph
- entity relationship semantics
- multi-hop reasoning
- business process composite objects

最典型支持的场景：

- GMV 下滑跨链路诊断
- 推荐链路问题分析
- 风控与支付联合分析

#### 4. 高阶分析能力

目标是让系统不仅能发现现象，还能支持更接近业务决策的问题回答。

包括：

- experiment analysis
- causal inference
- scenario / intervention simulation
- uncertainty-aware assessment

最典型支持的场景：

- 广告投放效果判断
- 策略调整影响评估
- 实验收益和风险分析

#### 5. 证据桥接能力

目标是让分析能从聚合证据自然走向明细诊断，而不破坏治理与有界输出原则。

包括：

- aggregate-to-detail drill bridge
- cohort refs
- representative case sampling
- trace / log handles

最典型支持的场景：

- 支付异常排障
- 履约失败定位
- 风控误杀样本回溯

---

## 5. 推荐的目标边界

长期看，Factum 不应退化成：

- 通用 SQL 工作台
- 无边界 BI 工具
- 原始日志平台
- 只会返回图表和 rows 的查询代理

更合理的目标边界是：

> **Factum = agent 的分析执行与证据化平台**

它应当负责：

- 语义理解底座
- typed analysis execution
- exploration-to-evidence 的系统化收敛
- workflow / runtime
- provenance / governance / audit
- 多实体 / 多域分析底座
- 统计 / 因果 /模拟等高阶分析能力

而 agent 负责：

- 问题理解
- 路径选择
- 分析策略
- 交互与解释

---

## 6. 一句话总结

如果要让 Factum 真正配合 agent 支持复杂互联网数据分析，最核心的演进方向不是“再多加几个 intent”，而是把 Factum 从：

> **typed analysis action library**

升级为：

> **有状态、可探索、可收敛、可证据化、可治理的 analysis operating layer**
