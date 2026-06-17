# Marivo Evidence And Claim Workflow Discussion

记录日期：2026-06-16

说明：以下为本轮关于 Marivo 证据链、claim workflow、agent 边界的讨论记录。本文档不是设计记录，不对讨论内容做设计化整理。

## 1. Marivo 的可信证据链是否真的可信

**User**

marivo的可信证据链是否真的可信，是否真的能够在agent分析过程中构成证据链？

**Assistant**

结论：**Marivo 现在能构成一条“可追踪、可审计的本地分析证据链”，但还不是强意义上的“可信证明链”**。它可信的部分是：每个 operator 产物会有稳定 artifact id、frame meta evidence 字段、SQLite `judgment.db` 中的 artifact/finding/proposition/assessment/followup 记录，以及 `session.knowledge()` / `session.evidence.trace()` 读面。它不可信的部分是：不能防篡改、不能自动保证最终自然语言报告逐句有证据，也还没有把执行 SQL/job/source step 完整并入 `EvidenceTrace`。

代码依据：

- 设计文档明确 evidence 是 step commit 的副作用，且分为 result-bound、session-bound、object-bound 三层；但该文档状态是 `target design`，不能单独当实现事实看。[docs/specs/analysis/python-analysis-design.md](/Users/lichengxiang/source/oss/marivo/docs/specs/analysis/python-analysis-design.md:3), [docs/specs/analysis/python-analysis-design.md](/Users/lichengxiang/source/oss/marivo/docs/specs/analysis/python-analysis-design.md:40)
- 当前实现里 `BaseFrameMeta` 确实统一挂了 `artifact_id/evidence_status/quality/blocking_issues/followups`。[marivo/analysis/frames/base.py](/Users/lichengxiang/source/oss/marivo/marivo/analysis/frames/base.py:153)
- `commit_result` 会生成确定性 artifact id、写 Parquet、算 frame hash、抽取 findings、seed propositions、生成 assessments/followups，最后回写 frame meta。[marivo/analysis/evidence/pipeline.py](/Users/lichengxiang/source/oss/marivo/marivo/analysis/evidence/pipeline.py:601), [marivo/analysis/evidence/pipeline.py](/Users/lichengxiang/source/oss/marivo/marivo/analysis/evidence/pipeline.py:627), [marivo/analysis/evidence/pipeline.py](/Users/lichengxiang/source/oss/marivo/marivo/analysis/evidence/pipeline.py:812)
- `judgment.db` schema 有 artifacts、findings、propositions、assessment_snapshots、assessment_edges、blocking_issues、followups，足够支撑本地审计账本。[marivo/analysis/evidence/store.py](/Users/lichengxiang/source/oss/marivo/marivo/analysis/evidence/store.py:25)
- `session.knowledge()` 和 `session.evidence.*` 是公开读面；`trace()` 可以从 proposition 找回 seed/support/oppose findings 和 source artifacts。[marivo/analysis/session/core.py](/Users/lichengxiang/source/oss/marivo/marivo/analysis/session/core.py:565), [marivo/analysis/session/core.py](/Users/lichengxiang/source/oss/marivo/marivo/analysis/session/core.py:1541), [marivo/analysis/evidence/audit.py](/Users/lichengxiang/source/oss/marivo/marivo/analysis/evidence/audit.py:210)

关键边界：

1. **能证明“这个结论来自哪个 Marivo artifact / finding / assessment”**，但不能证明底层数据源没变、语义定义没被改、本地 SQLite/Parquet 没被人手工篡改。
2. `EvidenceTrace.source_steps` 类型存在，但当前 `build_evidence_trace()` 返回时写死为空列表；也就是说 trace 还没有完整回接执行 step/job/query。[marivo/analysis/evidence/types.py](/Users/lichengxiang/source/oss/marivo/marivo/analysis/evidence/types.py:254), [marivo/analysis/evidence/audit.py](/Users/lichengxiang/source/oss/marivo/marivo/analysis/evidence/audit.py:248)
3. observe 的 job record 里会记录 query 信息，但这是 job 记录，不是 Surface 3 trace 的一部分。[marivo/analysis/intents/observe.py](/Users/lichengxiang/source/oss/marivo/marivo/analysis/intents/observe.py:1859)
4. agent 如果跳出 typed intent，用自由 pandas/SQL 推理，或者最终报告不引用 artifact/fact/trace，那么 Marivo 证据链不会自动覆盖那部分推理。

所以回答你的问题：**在 agent 分析过程中，它可以构成证据链，但前提是 agent 严格走 `session.observe/compare/decompose/discover/...`、读 `frame.meta`/`session.knowledge()`/`session.evidence.trace()`，并在最终回答里显式绑定 artifact/fact。** 如果你期待的是“结论级强可信、可防篡改、可回放到原始 SQL 和源数据版本”的证据链，现在还差几块：把 job/query/source steps 接进 `EvidenceTrace`、对 `frame_sha` 做公开校验、暴露 artifact audit row、强制 report citation/checker、以及对 datasource snapshot/version 做更完整绑定。

我没有跑测试；这次是基于当前代码和已有测试的静态审阅判断。

## 2. Artifact -> finding -> assessment -> proposition 链路与假设证明范式

**User**

当前artifact -> finding -> assessment -> preposition 链路是否完整，更抽象的问题是，是否可以，是否应该把agent基于marivo的数据分析流程转变为： “提出假设” -> "形式化证明假设"的范式

**Assistant**

**链路现状**

先纠正术语和方向：当前实现里应是 **artifact -> finding -> proposition -> assessment**，不是 `assessment -> proposition`，也不是 `preposition`。`assessment` 是对某个 `proposition` 的最新判断快照，`assessment_edges` 再把这个判断连回支持/反对它的 findings。

这条链路在当前实现里是**基本完整的本地 evidence graph**：

- `commit_result()` 生成 deterministic `artifact_id`，写 Parquet，并记录 `frame_sha`。[marivo/analysis/evidence/pipeline.py](/Users/lichengxiang/source/oss/marivo/marivo/analysis/evidence/pipeline.py:601)
- `_extract_findings()` 从 artifact/frame 中抽取 findings。[marivo/analysis/evidence/pipeline.py](/Users/lichengxiang/source/oss/marivo/marivo/analysis/evidence/pipeline.py:199)
- `_seed_for_finding()` 将部分 finding 家族 seed 成 proposition。[marivo/analysis/evidence/pipeline.py](/Users/lichengxiang/source/oss/marivo/marivo/analysis/evidence/pipeline.py:362)
- `_insert_assessment()` 写 assessment，并通过 `assessment_edges` 连接 support/oppose findings。[marivo/analysis/evidence/pipeline.py](/Users/lichengxiang/source/oss/marivo/marivo/analysis/evidence/pipeline.py:512)
- `session.evidence.trace(proposition_id)` 可以重建 proposition、latest assessment、seed/support/oppose findings、source artifacts。[marivo/analysis/evidence/audit.py](/Users/lichengxiang/source/oss/marivo/marivo/analysis/evidence/audit.py:210)

但它还不是完整的“证明链”。明显缺口有三个：

1. `EvidenceTrace.source_steps` 当前存在字段，但实现里固定返回 `[]`，所以 trace 没有完整接回 job/query/operator step。[marivo/analysis/evidence/audit.py](/Users/lichengxiang/source/oss/marivo/marivo/analysis/evidence/audit.py:248)
2. proposition 当前主要是 system-seeded，不是 agent 显式提出的 first-class hypothesis。设计文档甚至把 `agent_authored proposition 写入面`列为非目标。[docs/specs/analysis/python-analysis-design.md](/Users/lichengxiang/source/oss/marivo/docs/specs/analysis/python-analysis-design.md:20)
3. assessment 的语义是规则化评估，不是数学证明。例如 change assessment 只是判断 seed delta direction 是否匹配 interest；test assessment 只是根据 `reject_null` 映射为 validated/refuted/inconclusive。[marivo/analysis/evidence/assessment.py](/Users/lichengxiang/source/oss/marivo/marivo/analysis/evidence/assessment.py:31), [marivo/analysis/evidence/assessment.py](/Users/lichengxiang/source/oss/marivo/marivo/analysis/evidence/assessment.py:230)

**是否应该转成“提出假设 -> 形式化证明假设”**

可以转，但我建议换成更准确的范式：**“提出可检验主张 -> 形式化验证计划 -> evidence-backed assessment”**。

原因是数据分析里大多数结论不是 proof，而是 bounded claim：在某个 metric、window、grain、slice、quality scope、alignment policy 下，当前证据支持/反对/无法判断某个主张。把它叫“证明”会误导 agent 和用户，容易把统计显著、归因贡献、相关性、预测区间都包装成过强结论。

更好的 agent workflow 是混合式：

1. 探索：`observe/compare/discover/decompose/correlate` 生成候选现象和候选解释。
2. 主张化：agent 把最终要写进报告的关键句子转成 typed proposition/claim。
3. 验证：每个 claim 编译成 operator obligations，比如 compare、hypothesis_test、assess_quality、decompose。
4. 裁决：输出 `validated/refuted/inconclusive/pending`，并绑定 `assessment_id`、`finding_id`、`artifact_id`。
5. 报告：没有 assessment 的句子只能作为假设/建议/待验证项，不能作为结论。

所以方向是对的，但落点不应该是“让 agent 证明一切”，而是让 agent 的结论必须进入 **claim ledger**。Marivo 负责把可计算部分变成 artifact/finding/proposition/assessment，agent 负责选择问题、提出解释、处理业务语境，并诚实标注未被验证的部分。

如果后续要产品化这个范式，最关键的新 surface 不是再加一个 report API，而是类似：

```python
claim = session.claim(
    statement="revenue increase is primarily driven by US segment",
    subject=...,
    predicate="primary_driver",
    obligations=[...],
)
assessment = session.verify(claim)
trace = session.evidence.trace(assessment.proposition_id)
```

但这需要同时补齐 `agent_authored proposition`、`source_steps` trace、query/job 回链、report citation checker。否则现在只能说 Marivo 已经有 evidence graph 的骨架，还没有完整的 hypothesis verification runtime。

我这次没有跑测试；判断基于当前代码和已有测试覆盖的静态审阅。

## 3. 主张/验证/裁决流程相比纯探索流程的好处与依据

**User**

不受marivo当前实现限制，你提出的探索->主张->验证->裁决->报告的agent分析流程，相比于探索->结果->探索->结果->...->报告的分析流程有什么好处？是否有业界的实践证明或相关论文

**Assistant**

有好处，但不能把它理解成“探索不好，证明才好”。更准确是：**探索负责发现可能性，主张/验证/裁决负责约束最终结论**。相比 `探索 -> 结果 -> 探索 -> 结果 -> ... -> 报告`，它最大的收益是把 agent 的自由游走变成可审计的判断过程。

**核心好处**

1. **防止“看到什么写什么”**

   纯探索循环很容易让 agent 把中途发现包装成结论，尤其是反复切窗口、切维度、换口径后，总能找到一个看起来有解释力的结果。Gelman 和 Loken 的 “garden of forking paths” 讨论的正是这种问题：即使没有主观 p-hacking，数据依赖的分析路径也会制造过强结论。[Gelman & Loken PDF](https://sites.stat.columbia.edu/gelman/research/unpublished/p_hacking.pdf)

2. **让最终报告里的每个关键句子都有状态**

   主张化以后，每个结论可以被标成：

   - `validated`
   - `refuted`
   - `inconclusive`
   - `pending`
   - `exploratory only`

   这比“这里有个图，看起来像……”更适合 agent，因为 agent 很容易过度综合、过度叙事。

3. **把“分析过程”变成“证据账本”**

   `探索 -> 结果` 只留下结果；`主张 -> 验证 -> 裁决` 会留下：

   - 主张是什么
   - 验证用什么数据、窗口、指标、维度
   - 用了什么方法
   - 哪些 evidence 支持或反对
   - 结论适用范围是什么

   这对复盘、审查、多 agent 协作、报告 QA 都更强。

4. **降低报告阶段的幻觉**

   agent 最大风险不是算错一张表，而是把几张表串成一个“听起来合理”的故事。claim-ledger 流程会强迫报告只引用已裁决主张，未验证内容只能写成假设或后续工作。

5. **更容易自动化质量门禁**

   例如最终报告生成前可以检查：

   - 每个结论是否有 claim id
   - 每个 claim 是否有 assessment
   - 是否存在 `inconclusive` 却被写成确定结论
   - 是否混淆相关性、归因、因果
   - 是否缺少样本量、窗口、口径、置信范围

   纯探索链很难做这种结构化检查。

**但它不应该替代探索**

探索仍然必要。Tukey 的 EDA 传统本来就是用来发现异常、生成假设、检查假设前提，而不是直接完成确认性结论。[Learning Data Science: EDA](https://learningds.org/ch/10/eda_intro.html)

所以好的 agent 流程不是：

```text
只做假设检验
```

而是：

```text
探索发现候选现象
-> 明确哪些要进入报告
-> 把这些写成可检验主张
-> 验证/反驳/标记不确定
-> 报告只提升已裁决主张
```

**业界和学术上的相邻实践**

没有直接针对“LLM agent 数据分析流程”的成熟大规模证明，这个领域还新。但这个范式有很强的相邻依据：

- **CRISP-DM**：经典数据挖掘流程不是无限探索，而是从业务理解、数据理解进入建模、评价、部署。它强调 evaluation 作为交付前阶段。[CRISP-DM 1.0 PDF](https://public.dhe.ibm.com/software/analytics/spss/documentation/modeler/14.2/es/CRISP-DM.pdf)
- **SEMMA**：SAS 的数据挖掘流程是 Sample、Explore、Modify、Model、Assess，最后也有 assess，而不是只持续 explore。[SEMMA overview](https://www.datascience-pm.com/semma/)
- **预注册 / Registered Reports**：开放科学实践明确区分 hypothesis-generating 和 hypothesis-testing。Center for Open Science 认为预注册的价值之一就是区分计划内和计划外分析，避免同一批数据既生成又“确认”假设。[COS Preregistration](https://www.cos.io/initiatives/prereg), [COS Registered Reports](https://www.cos.io/initiatives/registered-reports)
- **A/B testing / 产品实验**：产品增长和实验平台的基本范式就是先定义假设、指标和成功标准，再运行实验和裁决。HBS 关于 A/B testing adoption 的研究认为实验会改善组织学习，并帮助创业公司开发和识别可扩展产品。[HBS working paper PDF](https://www.hbs.edu/ris/Publication%20Files/AB_Testing_R_R_08b97538-ed3f-413e-bc38-c239b175d868.pdf)
- **统计学对 p-value 的警告**：ASA 的 p-value 声明强调，统计结果不能脱离分析选择、方法和解释上下文。这个正支持“记录验证路径和裁决依据”的必要性。[ASA p-value statement PDF](https://www.amstat.org/asa/files/pdfs/p-valuestatement.pdf)

**我的判断**

对 agent 来说，`探索 -> 主张 -> 验证 -> 裁决 -> 报告` 明显优于纯 `探索 -> 结果 -> ... -> 报告`，因为 agent 的弱点正是路径依赖、过度叙事、选择性引用和无法自觉区分探索/确认。

但它不应叫“形式化证明”。更合适的名字是：

```text
claim-based analytical reasoning
evidence-backed assessment
hypothesis-to-assessment workflow
```

也就是说：不是证明真理，而是把每个结论变成**有边界、有证据、有状态的可审查主张**。

## 4. 主张定义、类型、验证操作与裁决规则

**User**

主张应该如何定义，有哪些类型？是否根据主张自动生成验证操作，是的话，如何生成，有哪些规则？验证操作结果如何裁决主张？这些有理论基础么？一个典型的完整流程范式是什么样的？

**Assistant**

可以把“主张”定义成一个 **有作用域、可检验、有裁决标准的数据断言**。重点不是让 agent 写一句自然语言结论，而是把这句话编译成可执行的验证计划。

**1. 主张如何定义**

一个主张至少应包含这些字段：

```text
Claim {
  statement: 自然语言断言
  type: 主张类型
  subject: 指标 / 实体 / 人群 / 业务对象
  scope: 时间窗口 / 粒度 / 筛选条件 / 数据源版本
  predicate: 断言关系，例如 increase, decrease, primary_driver, correlated_with
  comparator: baseline / previous_period / control_group / forecast_baseline
  threshold: 最小效应、显著性、业务阈值、排名阈值
  required_evidence: 需要哪些验证操作
  assumptions: 方法假设和适用条件
  decision_use: 这个主张会支撑什么决策
}
```

一句“收入下降主要由华东地区贡献”不能直接进报告。它应被结构化成：

```text
type: driver_claim
metric: revenue
predicate: primary_driver
scope: current week vs previous week
dimension: region
target_segment: East China
threshold: contribution_share >= 50%
required_evidence:
  - compare revenue current vs baseline
  - decompose delta by region
  - assess data quality
```

**2. 主张类型**

我会分成十类，越往后证明要求越高：

| 类型 | 例子 | 典型验证 |
|---|---|---|
| Descriptive | “本周收入为 120 万” | observe |
| Change | “收入环比下降 8%” | observe A/B + compare |
| Segment / Rank | “华东是收入最大地区” | observe segmented + rank |
| Anomaly | “6 月 12 日是异常低点” | observe time series + anomaly detection |
| Driver / Attribution | “下降主要由华东贡献” | compare + decompose |
| Association | “退款率和延迟正相关” | observe two metrics + correlate |
| Statistical Hypothesis | “当前均值显著低于基线” | hypothesis_test |
| Forecast | “下周收入会继续下降” | forecast + interval/backtest |
| Causal / Intervention | “改版导致转化率提升” | experiment / DiD / IV / causal model |
| Recommendation | “应优先修复华东渠道” | 依赖多个 lower-level claims |

关键规则：**不能把低阶主张升级成高阶主张**。相关性不是因果，归因分解也不是因果，异常发现也不是业务解释。

Pearl 的因果层级可以作为理论边界：association、intervention、counterfactual 是不同层级，不能用观测相关性直接回答干预或反事实问题。[Pearl causal hierarchy PDF](https://web.cs.ucla.edu/~kaoru/3-layer-causal-hierarchy.pdf)

**3. 是否根据主张自动生成验证操作**

应该可以，而且这是这个范式最有价值的部分。

生成方式不是让 LLM 自由发挥，而是 **claim type -> verification template**：

```text
ChangeClaim
  -> observe(current)
  -> observe(baseline)
  -> compare(current, baseline)
  -> assess_quality(current, baseline, delta)

DriverClaim
  -> verify parent ChangeClaim
  -> decompose(delta, axis)
  -> check contribution threshold
  -> check residual / coverage / segment quality

AssociationClaim
  -> observe(metric_a)
  -> observe(metric_b)
  -> align windows
  -> correlate
  -> assess sample size / missingness
  -> mark as non-causal unless causal design exists

CausalClaim
  -> require experimental or quasi-experimental design
  -> check treatment/control, pre-period balance, interference, assumptions
  -> if absent: unsupported_as_causal
```

生成规则可以分三层。

**A. 作用域绑定规则**

验证操作必须继承主张的 scope：

- metric id 必须一致
- 时间窗口必须明确
- grain 必须明确
- filter 必须明确
- baseline/control 必须明确
- 数据源和语义版本必须可追踪

如果 scope 不完整，不能生成验证，只能返回 `needs_clarification` 或 `unsupported`.

**B. 类型到操作规则**

每种主张只允许映射到白名单操作。比如：

- `change` 不能直接用 `decompose`
- `driver` 必须先有可验证的 change
- `forecast` 必须包含 horizon
- `causal` 必须要求 causal design，不能由 `correlate` 自动满足

**C. 证据充分性规则**

每个 claim 有 minimum evidence set：

```text
validated 需要：
  required operations 全部成功
  effect direction 匹配
  effect size 达到阈值
  quality 无 blocking issue
  scope 与 claim 完全兼容
  没有更强反证

refuted 需要：
  required evidence 与 predicate 相反，或阈值不成立

inconclusive 发生于：
  方向弱、样本不足、质量问题、置信区间过宽、结果对口径敏感

unsupported 发生于：
  claim 类型需要的设计不存在，例如没有实验却提出因果主张
```

**4. 验证结果如何裁决主张**

裁决不应该只看一个 p-value。更稳妥是规则化裁决：

```text
ClaimAssessment {
  status: validated | refuted | inconclusive | pending | unsupported
  strength: exploratory | suggestive | confirmatory | causal
  confidence: number or band
  evidence_refs: artifacts/findings/tests
  counter_evidence_refs: ...
  assumptions_satisfied: yes/no/partial
  scope_limitations: ...
}
```

示例：

```text
主张：华东是收入下降的主要贡献者

裁决：
  status: validated
  strength: suggestive
  because:
    - 总收入环比下降 8.2%
    - 华东贡献了 61% 的下降
    - 数据质量无 blocking issue
  limitations:
    - 这是分解归因，不是因果证明
    - 未验证华东下降由渠道故障导致
```

如果 decomposition 显示华东只贡献 18%，则 `refuted`。如果数据缺失率高或口径变更，则 `inconclusive`。

**5. 理论基础**

有，来自几个成熟方向，但不是一个完全相同的“LLM agent 分析标准”。

- **Toulmin argument model**：把论证拆成 claim、grounds/evidence、warrant、backing、qualifier、rebuttal。很适合 claim ledger 设计，因为它要求说明“证据如何支持主张”。[Purdue OWL: Toulmin Argument](https://owl.purdue.edu/owl/general_writing/academic_writing/historical_perspectives_on_argumentation/toulmin_argument.html)
- **Claim-Evidence-Reasoning**：科学教育里常用的 CER 框架，核心就是主张、证据、推理链条。[Edutopia CER](https://www.edutopia.org/blog/science-inquiry-claim-evidence-reasoning-eric-brunsell)
- **Assurance cases / GSN**：安全关键系统里用 structured claims、arguments、evidence 来证明系统满足某些性质。这个和“数据分析结论可审计”非常接近。[ICO assurance cases](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/artificial-intelligence/explaining-decisions-made-with-artificial-intelligence/annexe-5-argument-based-assurance-cases/), [NIST assurance case PDF](https://tsapps.nist.gov/publication/get_pdf.cfm?pub_id=902688)
- **预注册 / 开放科学**：区分 exploratory 和 confirmatory，避免同一批数据既生成假设又确认假设。这个直接支持“探索结果必须主张化并标注证据等级”。[Center for Open Science preregistration](https://www.cos.io/initiatives/prereg)
- **统计检验和 p-hacking 反思**：Gelman 的 forking paths 和 ASA p-value 声明都强调分析路径、选择空间、解释边界必须透明。[Gelman & Loken PDF](https://sites.stat.columbia.edu/gelman/research/unpublished/p_hacking.pdf), [ASA p-value statement](https://www.amstat.org/asa/files/pdfs/p-valuestatement.pdf)

**6. 一个典型完整流程**

```text
1. 问题定义
   “为什么本周收入下降？”

2. 探索
   observe revenue
   compare current vs baseline
   discover anomalies / slices

3. 候选主张生成
   C1: 本周收入显著下降
   C2: 下降主要由华东地区贡献
   C3: 下降与支付失败率上升相关
   C4: 支付系统改版导致收入下降

4. 主张规范化
   给每个 claim 绑定 metric、window、grain、baseline、threshold、type。

5. 自动生成验证计划
   C1 -> observe + compare + quality
   C2 -> C1 + decompose(region) + contribution threshold
   C3 -> observe failure_rate + correlate + quality
   C4 -> require experiment/quasi-experiment; no design => unsupported_as_causal

6. 执行验证
   产出 artifacts、findings、assessments。

7. 裁决
   C1 validated
   C2 validated / inconclusive
   C3 suggestive association
   C4 unsupported as causal

8. 报告
   只把 validated / suggestive claims 写成结论。
   unsupported causal claim 写成：“当前证据不足以证明改版导致下降，需要实验或准实验验证。”
```

最终形态可以概括为：

```text
Explore to discover.
Claim to constrain.
Verify to test.
Assess to decide.
Report only what survived.
```

这比纯探索流程强，因为它让 agent 的每个结论都有类型、有验证路径、有裁决状态、有适用边界。关键不是减少探索，而是防止探索结果未经裁决就变成报告结论。

## 5. Agent 和 Marivo 的边界

**User**

在这个部分，如何划分清楚agent和marivo的边界，哪些是marivo应该做的，能够做的，哪些是应该交给agent发挥LLM越来越强的能力的。

**Assistant**

最清楚的边界是这句话：

**Marivo 负责把“可计算、可复现、可审计”的部分做成确定性运行时；agent 负责把“开放问题、业务语境、假设生成、解释取舍”做好。**

不要让 Marivo 冒充分析师，也不要让 agent 手搓数据事实。

**边界原则**

| 判断标准 | 应归 Marivo | 应归 agent |
|---|---|---|
| 是否需要确定性、可重放 | 是 | 否 |
| 是否能用封闭类型表达 | 是 | 不一定 |
| 是否需要业务语境和语义判断 | 少量 | 是 |
| 是否会影响证据可信度 | 是 | 受 Marivo 约束 |
| 是否是开放式探索和解释 | 否 | 是 |
| 是否适合被测试、版本化、审计 | 是 | 只产结构化输入 |

**Marivo 应该做什么**

1. **定义数据和指标的可信边界**

Marivo 应该负责：

- semantic catalog
- metric/dimension/entity ref
- datasource binding
- timescope/grain/filter 规范化
- metric definition version / datasource snapshot / query record
- frame artifact identity

也就是说，agent 不能自由说“收入”“本周”“华东”就开始算，必须落到 Marivo 能解析的 typed ref 和 scope。

2. **定义 claim schema 和 claim type system**

Marivo 应该提供结构化 claim 类型，而不是只收自然语言：

```text
ChangeClaim
DriverClaim
AnomalyClaim
AssociationClaim
ForecastClaim
CausalClaim
RecommendationClaim
```

但 Marivo 不应该替 agent 随便创造业务主张。Marivo 提供类型系统、校验器、字段约束、可支持的 predicate。

3. **把主张编译成验证 obligations**

这是 Marivo 很应该做的部分。

例如：

```text
ChangeClaim
 -> observe(current)
 -> observe(baseline)
 -> compare
 -> assess_quality

DriverClaim
 -> verify parent ChangeClaim
 -> decompose(delta, axis)
 -> check contribution threshold

AssociationClaim
 -> observe metric A
 -> observe metric B
 -> align
 -> correlate
 -> quality checks
```

这个编译器应该是 deterministic 的：同一个 claim，同一个 catalog，同一个策略，生成同一组 verification plan。

4. **执行 typed operators**

Marivo 应该执行：

- observe
- compare
- decompose
- discover
- correlate
- hypothesis_test
- forecast
- assess_quality
- transform
- replay
- trace

agent 不应该自己拼 SQL、自己算同比、自己判断 join 逻辑，除非是明确 escape hatch，并且结果重新注册回 Marivo。

5. **裁决主张**

Marivo 应该基于规则裁决：

```text
validated
refuted
inconclusive
pending
unsupported
```

例如：

- direction 不匹配 -> `refuted`
- quality blocking issue -> `inconclusive`
- causal claim 但只有 observational correlation -> `unsupported_as_causal`
- forecast 尚无 actual -> `pending`
- effect size 达标且 quality 合格 -> `validated`

裁决规则必须可测试、可解释、可追溯。

6. **维护 evidence ledger**

Marivo 应该负责：

```text
claim -> verification_plan -> artifacts -> findings -> assessment -> report citations
```

每个最终结论都能追到：

- 运行了什么
- 输入是什么
- 输出 artifact 是什么
- finding 是什么
- assessment 为什么这样裁决
- 哪些 evidence 支持或反对

7. **做报告前 QA**

Marivo 应该能检查：

- 报告中的关键结论是否绑定 claim
- claim 是否有 assessment
- `inconclusive` 是否被写成确定结论
- association 是否被写成 causation
- exploratory result 是否被写成 confirmatory result
- 数据质量 blocking issue 是否被忽略

这不是让 Marivo 写报告，而是让 Marivo 审报告。

**Agent 应该做什么**

1. **理解用户问题和业务目标**

例如用户问“为什么收入下降”，agent 要判断：

- 用户关心总收入、净收入还是 GMV
- 是要解释、归因、告警、复盘还是决策建议
- 业务上哪些维度可能重要
- 什么程度的变化值得关注

这些不能交给 Marivo 自动猜。

2. **生成候选假设**

Agent 擅长开放式推理：

```text
可能是地区结构变化
可能是支付失败率上升
可能是大客户流失
可能是价格策略变化
可能是节假日/活动影响
```

Marivo 可以提供 discover 辅助，但不应该冒充业务洞察生成无限 followup。

3. **选择探索路径和优先级**

比如先看时间趋势还是先看渠道？先排查数据质量还是先做分解？哪些维度值得 drill down？

这属于 agent 的规划能力。Marivo 可以告诉它“哪些操作合法”“哪些证据不足”，但不该替它做开放式策略选择。

4. **把自然语言问题转成 claim draft**

Agent 可以产出：

```text
C1: 本周收入相比上周显著下降
C2: 下降主要由华东地区贡献
C3: 华东下降与支付失败率上升同步
C4: 支付系统改版可能导致下降
```

然后交给 Marivo validate/compile。Marivo 如果发现字段不完整，就返回缺什么。

5. **处理业务解释和 caveat**

Marivo 可以说：

```text
华东贡献 61% 的下降
支付失败率与收入下降相关系数 -0.72
```

Agent 应该负责解释：

```text
这支持“华东是主要贡献区域”，但不证明支付失败导致收入下降。需要结合改版时间、错误码、实验或准实验设计继续验证。
```

6. **最终报告写作**

报告应由 agent 写，因为需要：

- answer-first synthesis
- 读者语气
- 优先级判断
- caveat 表达
- 决策建议
- 后续行动设计

但报告里的事实和结论应受 Marivo claim ledger 约束。

**不要让 Marivo 做的事**

Marivo 不应该：

- 自动声称“根因是 X”
- 自动生成业务策略建议
- 把相关性升级成因果
- 在没有业务阈值时擅自定义“显著”
- 自动枚举所有维度并宣称找到最优解释
- 写开放式 narrative conclusion
- 替用户决定什么是重要问题

这些是 agent 和用户的判断空间。

**不要让 agent 做的事**

Agent 不应该：

- 自己拼核心统计口径
- 自己在脑中计算指标
- 自己把 dataframe 结果当作证据链
- 自己决定 evidence status
- 自己裁决 claim validated/refuted
- 自己把 exploratory 结果写成 confirmatory
- 自己绕过 semantic catalog 解释指标

这些必须落到 Marivo。

**推荐架构**

```text
User
  -> Agent: 理解问题、生成候选主张、选择分析路径

Agent
  -> Marivo: submit claim draft

Marivo
  -> 返回: normalized claim / missing fields / unsupported type

Agent
  -> 补 scope、threshold、dimension、baseline

Marivo
  -> compile verification plan
  -> execute typed operators
  -> produce artifacts/findings/assessments
  -> return claim assessments

Agent
  -> 综合多个 assessments
  -> 写报告
  -> Marivo report checker 验证引用和结论强度

Final
  -> answer + evidence-backed claims + caveats + next actions
```

**一句话结论**

Marivo 的边界应该是 **typed analytical operating system**：它保证数据事实、操作、证据、裁决可复现、可审计。

Agent 的边界应该是 **analytical reasoning partner**：它负责提出问题、生成假设、选择路径、解释含义、形成报告。

最好的设计不是削弱 agent，而是让 agent 的创造力进入一个有护栏的证据系统里：**agent 可以大胆假设，但 Marivo 必须负责小心求证。**

## 6. 典型互联网公司分析域是否能覆盖

**User**

对于典型互联网公司数据分析的问题域，主张->验证->裁决 这条基于规则的定义/分析表达/推导链条能够覆盖么？无法覆盖大部分的话，这个范式就没有任何意义。

**Assistant**

能覆盖，但要先把“覆盖”定义对：**它不能覆盖整个业务思考过程，但可以覆盖大部分互联网数据分析报告里的“事实性/证据性结论单元”。**

如果目标是让规则链条自动完成所有分析，那覆盖不了，也不该这么做。
如果目标是让 agent 的关键结论必须落成可验证主张，再由系统给出 `validated/refuted/inconclusive/unsupported`，这个范式是有意义的，而且覆盖面不低。

**互联网分析问题可以分层**

| 层级 | 例子 | 规则化覆盖度 |
|---|---|---|
| Measurement | “DAU 是 1200 万” | 高 |
| Change | “DAU 环比下降 5%” | 高 |
| Segment / Funnel | “下降主要在 Android 新用户” | 高 |
| Attribution / Contribution | “Android 新用户贡献了 62% 的下降” | 高 |
| Anomaly | “6 月 12 日异常” | 高 |
| Association | “失败率上升与转化下降同步” | 中高 |
| Experiment | “改版提升转化率” | 中高，前提是有实验设计 |
| Causal diagnosis | “支付失败导致收入下降” | 中低，必须有实验/准实验/强设计 |
| Business recommendation | “应该优先回滚版本” | 低，只能证据支撑，不能规则裁决 |
| Strategy judgment | “应该进入某市场” | 低 |

所以它不是覆盖“所有问题”，而是覆盖分析里的**可证据化原子结论**。

**典型互联网公司问题，大多可以拆成 claim**

比如“为什么 DAU 掉了”：

```text
C1: DAU 本周相比上周下降 5%
C2: 下降主要发生在 Android
C3: Android 下降主要来自新用户
C4: 新用户下降与注册失败率上升同步
C5: 注册失败率上升由 6.10 版本导致
C6: 应该回滚 6.10 版本
```

裁决结果可能是：

```text
C1 validated
C2 validated
C3 validated
C4 suggestive association
C5 unsupported as causal, unless有灰度/实验/发布分组证据
C6 recommendation, requires human/agent decision
```

这已经非常有价值。它不能替你直接“证明根因”，但能阻止 agent 把 C4 写成 C5，把 C5 直接写成 C6。

**哪些互联网分析场景覆盖很好**

覆盖很好的是这些：

- 指标看板解释：DAU、收入、转化、留存、时长、播放量、失败率
- 环比/同比/AB 组间变化
- 漏斗拆解
- 人群/渠道/地区/版本/设备维度归因
- 异常检测和异常窗口定位
- Top driver / contribution 分解
- 实验结果裁决
- 数据质量检查
- 预测和实际偏差
- 指标口径一致性检查
- 报告结论审计

这些本来就是互联网公司数据分析的高频主体。

**哪些覆盖不好**

覆盖不好但仍可被证据链约束的是：

- “真正根因是什么”
- “用户为什么这么做”
- “这个功能是否值得长期投入”
- “策略 A 和策略 B 该选哪个”
- “竞品变化是否导致流失”
- “内容生态是否变差”
- “品牌感知是否下降”

这些需要业务知识、用户研究、产品判断、外部信息、组织约束。Marivo/规则链不能裁决最终答案，但可以裁决其中的证据主张。

比如“内容生态变差导致留存下降”不能直接验证，但可以拆成：

```text
低质内容曝光占比是否上升
负反馈率是否上升
高质量作者供给是否下降
留存下降是否集中在高负反馈用户
实验中降低低质曝光是否改善留存
```

规则链覆盖这些子主张，agent 负责综合解释。

**关键不是覆盖问题，而是覆盖报告里的断言**

互联网分析报告通常混合三类句子：

```text
事实：DAU 下降 5%
证据性解释：Android 新用户贡献 62% 下降
推测性解释：可能与 6.10 注册链路异常有关
建议：优先回滚或修复注册链路
```

规则链至少应该覆盖前两类，并约束第三类，标注第四类依赖哪些已验证主张。哪怕最终建议不能自动裁决，这个系统也已经减少了最危险的问题：**把推测写成事实，把相关写成因果，把局部发现写成全局结论。**

**我的判断**

对典型互联网公司数据分析，`主张 -> 验证 -> 裁决` 可以覆盖大部分**数据证据层**，但不能覆盖全部**业务决策层**。

这不是缺陷，而是合理边界：

```text
Marivo/规则链：事实、变化、分解、相关、实验、质量、证据状态
Agent：问题理解、假设生成、路径选择、业务解释、优先级、建议
```

如果系统只覆盖 20% 的报告结论，确实意义不大。
但如果设计得好，它能覆盖 60-80% 的事实性和证据性断言，并把剩下的 20-40% 明确标成推测、建议或待验证。这对 agent 分析已经是很强的质量提升。

## 7. Agent 为什么要提出“DAU 本周相比上周下降 5%”这类主张

**User**

我对于agent如何提出主张还有很大的疑问，例如示例中的主张“DAU 本周相比上周下降 5%”， 如果agent已经通过compare拿到了本周相比上周下降 5%的数据，提出这个主张的意义何在？如果是agent没有compare过，它自己猜一个本周相比上周下降的数字作为主张去验证？

**Assistant**

你的疑问是对的。**“主张”不应该等同于 agent 在验证前编一个具体结果。**
如果 agent 没有 compare 过，它不应该猜“下降 5%”。它最多提出：

```text
DAU 本周相比上周是否下降？
DAU 本周相比上周是否下降超过 3%？
DAU 本周相比上周是否发生了业务显著下降？
```

然后由 Marivo 执行 compare，得到 `-5%`，再把结果实例化成一个已裁决主张：

```text
Claim: DAU 本周相比上周下降 5.0%
Status: validated
Evidence: compare artifact ...
```

所以要区分两种东西。

**1. 待验证主张：prospective claim**

这是验证前的结构化问题或假设，不应该包含未知精确结果。

正确：

```text
DAU 本周相比上周是否下降？
DAU 本周相比上周是否下降超过 3%？
华东地区是否贡献了超过 50% 的收入下降？
支付失败率上升是否与注册转化下降同步？
```

错误：

```text
DAU 本周相比上周下降 5%
华东贡献了 62% 的下降
```

除非这些数字来自已有 artifact，否则这是 agent 瞎猜。

**2. 结果主张：result-derived claim**

这是验证后从结果中抽出的结论单元。它的意义不是“再验证一次”，而是：

- 把结果变成报告可引用的结论对象
- 记录这个结论来自哪个 artifact
- 记录 scope、metric、window、baseline、quality
- 后续报告 QA 可以检查这句话有没有证据
- 后续更细的主张可以依赖它

例如 compare 后得到：

```text
delta = -5.0%
```

系统自动生成：

```text
Claim: DAU 本周相比上周下降 5.0%
Type: change_observation
Status: validated
Evidence: compare artifact
Scope: this_week vs previous_week
Quality: complete
```

这个 claim 的价值不是发现新事实，而是**把事实登记成可审计、可组合、可报告的证据节点**。

**所以主张有两种生命周期**

```text
验证前：
  hypothesis / question / expectation
  “是否下降？”
  “是否下降超过 3%？”

验证后：
  assessed claim / fact
  “下降 5%”
  “下降超过 3% 成立”
```

对你举的例子，完整流程应该是：

```text
用户问：为什么 DAU 掉了？

Agent:
  我需要先确认 DAU 是否真的下降。
  生成待验证主张：
    C1: DAU 本周相比上周是否下降超过业务阈值 3%？

Marivo:
  observe current
  observe baseline
  compare
  得到 delta = -5%

Marivo:
  裁决 C1:
    status = validated
    measured_effect = -5%
    threshold = -3%

系统生成结果主张：
  F1: DAU 本周相比上周下降 5%
```

如果没有业务阈值，agent 不应该硬造阈值。可以使用默认裁决：

```text
C1: DAU 本周相比上周是否非零变化？
```

或者要求用户/配置提供 materiality threshold。

**哪些主张适合验证前提出**

验证前适合提出的是：

```text
方向型：是否上升/下降？
阈值型：是否超过 3%？
排名型：是否 Top 1 driver？
贡献型：是否贡献超过 50%？
相关型：是否正相关/负相关？
实验型：是否 treatment 优于 control？
因果型：是否有足够设计支持因果？
```

验证前不适合提出的是未知精确数值：

```text
下降 5%
贡献 62%
相关系数 0.71
p-value 0.03
```

这些应该由验证结果生成。

**那 result-derived claim 是否冗余？**

不冗余。它不是为了“发现”，而是为了“治理结论”。

没有 result-derived claim，报告阶段只有一堆 artifact 和 dataframe，agent 会自由总结：

```text
DAU 明显下降，主要来自 Android，新版本可能是根因。
```

有 claim ledger 后，报告必须绑定：

```text
C1 validated: DAU WoW -5%
C2 validated: Android contributes 68% of DAU decline
C3 suggestive: Android decline overlaps with v6.10 rollout
C4 unsupported_as_causal: current evidence cannot prove v6.10 caused decline
```

这就是意义：**把分析结果变成有状态的结论资产，而不是一次性中间输出。**

**更准确的命名**

你指出的问题说明“提出主张”这个词容易混淆。我建议流程改成：

```text
探索
-> 生成待验证问题/假设
-> 执行验证
-> 生成已裁决主张
-> 报告引用已裁决主张
```

也就是：

```text
Question/Hypothesis before evidence.
Claim/Fact after evidence.
```

如果硬把两者都叫 claim，也要分：

```text
DraftClaim       # 验证前，不含未知精确数值
AssessedClaim    # 验证后，含测量结果和裁决状态
```

一句话：**agent 不应该猜 5%；Marivo 应该算出 5%；claim 的作用是把“算出 5%”登记成可审计、可引用、可组合的结论。**

## 8. Marivo 作为 Python 库如何从问题转成结论

**User**

marivo只是个python库，没有LLM能力，如何能够将主张内容 从“DAU 本周相比上周是否下降？” 转化为 “DAU 本周相比上周下降 5.0%”

**Assistant**

Marivo 不能、也不应该直接把自然语言：

```text
DAU 本周相比上周是否下降？
```

理解并转成：

```text
DAU 本周相比上周下降 5.0%
```

这个过程应该拆成两段。

**第一段：agent 做语义解析**

Agent 把自然语言转成 Marivo 能理解的结构化查询/待验证主张：

```python
DraftClaim(
    type="change_check",
    metric="product.dau",
    current_window={"start": "2026-06-08", "end": "2026-06-15"},
    baseline_window={"start": "2026-06-01", "end": "2026-06-08"},
    predicate="decrease",
    threshold=None,
)
```

这里 agent 负责理解：

- “DAU” 对应哪个 metric id
- “本周” 是哪个日期区间
- “上周” 是哪个 baseline
- “是否下降” 是 direction predicate
- 用户是否需要业务阈值

Marivo 不做 LLM 理解，只校验这些字段是否合法。

**第二段：Marivo 做计算和结构化投影**

Marivo 执行确定性操作：

```python
cur = session.observe(metric, timescope=current_window)
base = session.observe(metric, timescope=baseline_window)
delta = session.compare(cur, base)
```

然后根据 `delta` 的数值结果填充一个 assessed claim：

```python
AssessedClaim(
    type="change_result",
    metric="product.dau",
    current_window=...,
    baseline_window=...,
    predicate="decrease",
    status="validated",
    measured_change_pct=-0.05,
    rendered_fact="DAU 本周相比上周下降 5.0%",
    evidence_refs=[delta.meta.artifact_id],
)
```

注意：`rendered_fact` 不需要 LLM。它可以是模板化生成：

```text
{metric_label} {current_label} 相比 {baseline_label} {direction_label} {abs(delta_pct):.1%}
```

只要 metric label、window label、direction、delta_pct 都是结构化字段，Python 库完全能生成这句话。

**所以真正的转换不是：**

```text
自然语言问题 -> 自然语言结论
```

而是：

```text
自然语言问题
  --agent-->
结构化 DraftClaim
  --Marivo-->
typed operators + measured values + assessment
  --Marivo 或 agent-->
结构化/自然语言 AssessedClaim
```

Marivo 能做的是后半段：

```text
predicate="decrease" + measured_change_pct=-0.05
=> status="validated"
=> fact value: decreased by 5.0%
```

Marivo 不能做的是前半段：

```text
“DAU 本周相比上周是否下降？”
=> metric="product.dau", current_window=..., baseline_window=...
```

这个必须交给 agent，或者由用户/上层应用直接提供结构化参数。

**更准确的边界**

Marivo API 不应该设计成：

```python
session.verify("DAU 本周相比上周是否下降？")
```

除非外面包了 LLM。

Marivo 应该设计成：

```python
session.verify(
    mv.ChangeClaim(
        metric=session.catalog.get("product.dau"),
        current=mv.TimeWindow(...),
        baseline=mv.TimeWindow(...),
        predicate=mv.ChangePredicate(direction="decrease"),
    )
)
```

然后返回：

```python
ChangeAssessment(
    status="validated",
    measured_change_pct=-0.05,
    evidence_refs=[...],
)
```

**一句话**

Marivo 不负责“理解一句话”。
Marivo 负责“给定结构化主张后，算出结果、裁决主张、生成可审计事实”。
从自然语言到结构化主张，是 agent 的工作；从结构化主张到 `下降 5.0%`，是 Marivo 可以确定性完成的工作。

## 9. Marivo 如何约束 agent 使用 claim workflow

**User**

marivo如何约束agent使用主张-验证-裁决 来生成可审计事实，而不是直接使用typed intents分析数据，直接得出结论给到用户

**Assistant**

Marivo 不能约束一个任意 agent 的嘴。它只是 Python 库，agent 永远可以：

```python
delta = session.compare(cur, base)
print(delta.to_pandas())
```

然后自己写一句“DAU 下跌，原因是 Android”。**这件事 Marivo 无法从技术上禁止**，除非你控制 agent 使用的工具入口、报告生成入口或发布入口。

所以正确设计不是“禁止 agent 使用 typed intents”，而是：

**typed intents 可以用于探索，但只有 claim workflow 产出的结论才是可审计事实，只有可审计事实才能进入正式报告/发布物。**

**1. 分清 artifact 和 conclusion**

Typed intents 产出的是 evidence artifact，不是最终结论。

```text
observe -> MetricFrame
compare -> DeltaFrame
decompose -> AttributionFrame
correlate -> AssociationResult
```

这些对象只能说明：

```text
这里有一个计算结果
```

不能自动等价于：

```text
这是报告可用结论
```

Marivo 应该在对象层显式区分：

```text
Artifact:
  evidence_status: complete
  claim_status: unbound | supporting | assessed
```

直接调用 `compare()` 得到的 delta，默认是：

```text
evidence_status = complete
claim_status = unbound
```

意思是：它是证据，但还不是已裁决主张。

**2. 提供一个正式结论入口**

Marivo 应该提供类似：

```python
claim = session.claims.create(
    type="change",
    metric=dau,
    current=this_week,
    baseline=last_week,
    predicate="decrease",
)

assessment = session.claims.verify(claim)
```

或者：

```python
assessment = session.verify(
    mv.ChangeClaim(...)
)
```

这个入口内部可以复用 typed intents：

```text
verify(ChangeClaim)
  -> observe
  -> observe
  -> compare
  -> assess_quality
  -> produce AssessedClaim
```

也就是说 typed intents 不是废掉，而是变成 claim verification 的执行层。

**3. 直接 intent 结果不能直接 publish**

真正的约束点应该在报告/发布层，而不是计算层。

例如：

```python
report = session.report(
    claims=[assessment_1, assessment_2, assessment_3],
)
```

或者：

```python
session.report.check(markdown)
```

规则：

- 报告里的关键结论必须引用 `AssessedClaim`
- 引用 raw frame / raw delta 只能作为证据，不可作为裁决结论
- `unbound artifact` 不能被写成 “validated fact”
- `inconclusive` 不能写成确定结论
- `association` 不能写成 `causal`
- `unsupported_as_causal` 必须保留 caveat

这样 agent 仍然能探索，但正式输出被 claim ledger 约束。

**4. 把 API 做成两层**

推荐分成：

```text
Exploration API:
  session.observe
  session.compare
  session.decompose
  session.discover
  session.correlate
  session.to_pandas
  允许自由使用，但只产 evidence artifact

Assurance API:
  session.claims.create
  session.claims.verify
  session.claims.assessments
  session.report.from_claims
  session.report.check
  产出可审计事实和正式报告依据
```

这样不会压制 agent 的探索能力，但最终结论要走更严格路径。

**5. 给 direct intents 降级标识**

直接 typed intent 结果可以自动生成 observation finding，但不要自动升级成 assessed claim。

例如：

```python
delta = session.compare(cur, base)
```

得到：

```text
Artifact: art_delta_123
Finding: fnd_change_456
Claim: none
Report eligibility: evidence_only
```

如果 agent 想把它写成结论，需要显式绑定：

```python
claim = session.claims.from_artifact(
    delta,
    type="change",
    predicate="decrease",
    threshold=None,
)
assessment = session.claims.assess(claim)
```

这个动作的意义是：声明“我准备把这个结果作为一个结论使用”，Marivo 才做裁决和登记。

**6. 用 session mode 约束默认行为**

可以提供两种 session 模式：

```python
session = mv.session.get_or_create(name="x", mode="explore")
session = mv.session.get_or_create(name="x", mode="audited")
```

在 `audited` 模式下：

- direct intents 仍可运行
- 但 `.summary_for_report()`、`.render_report_fact()`、`.publish()` 只接受 assessed claims
- `frame.show()` 明确标注 `not claim-assessed`
- `to_pandas()` 输出带 warning metadata
- report checker 对 unassessed artifact 报错

这不是安全沙箱，而是工作流约束。

**7. 给 agent 明确 affordance**

Agent 往往会走最顺手的路径。Marivo 应该让正确路径最顺手：

```python
session.answer(question)
```

不建议，因为 Marivo 没 LLM。

更合理：

```python
plan = session.claims.plan(
    goal="diagnose_change",
    metric=dau,
    current=this_week,
    baseline=last_week,
)
```

返回结构化 verification templates，agent 填缺失字段：

```text
Need dimensions to test driver claims:
- platform
- region
- app_version
```

agent 决定选哪些，Marivo 执行和裁决。

**8. 最终约束必须在集成层完成**

如果 agent 是 Codex/Claude 这类外部系统，Marivo 单库无法阻止它直接回复用户。真正有效的是工具层协议：

```text
Agent final answer policy:
  final answer must call session.report.check(...)
  or final answer must cite AssessedClaim ids
```

也可以通过 MCP/tool wrapper 限制：

- 对 agent 暴露 `verify_claim` / `report_from_claims`
- 不直接暴露 raw pandas
- 或 raw pandas 标记为 exploration-only
- 最终回答前强制 report checker

如果只是普通 Python 库，被 agent 任意 import，Marivo 只能提供约束机制，不能强制 agent 遵守。

**推荐结论**

不要试图禁止 typed intents。它们是 agent 探索和生成候选主张的基础。

应该建立这条规则：

```text
Typed intents produce evidence.
Claim verification produces conclusions.
Reports may cite only assessed conclusions.
```

也就是：

```text
observe/compare/decompose = 证据生产层
claim/verify/assess = 结论裁决层
report/check/publish = 输出约束层
```

这样 Marivo 不需要有 LLM，也能约束 agent：**agent 可以自由探索，但只要它想生成“可审计事实”或“正式报告”，就必须经过主张-验证-裁决链条。**

## 10. Marivo 对 agent 最终报告没有硬控制能力

**User**

marivo没有agent的总结/判断能力，所以最终的分析报告肯定是agent生成的，marivo只能是提供证据信息，但是agent如何使用/是否使用， marivo是没有强制控制能力的，最多是通过skill引导agent。所以你上面说的“真正的约束点应该在报告/发布层”，感觉实际不可行。

**Assistant**

你这个判断是对的。**如果 Marivo 只是一个普通 Python 库，且最终回答由外部 agent 直接发给用户，那么 Marivo 对“agent 是否使用证据”没有硬控制能力。** 我前面说“约束点在报告/发布层”，只有在 Marivo 或 Marivo 的集成层真的拥有报告产物生成/发布入口时才成立；对普通 chat final answer 不成立。

所以要把目标从“强制 agent”降级成三个现实层级。

**1. Marivo 库内能保证的事**

Marivo 能保证：

```text
typed intent -> artifact
artifact -> finding
finding -> fact/assessment
fact/assessment -> evidence trace
```

也就是：只要 agent 调了 Marivo，Marivo 可以把计算结果整理成结构化证据对象。
但 Marivo 不能保证 agent 最后一定引用这些对象。

所以库内最实际的方向不是“禁止直接结论”，而是让直接 intent 自动产生尽可能好的 evidence/fact surface：

```python
delta = session.compare(cur, base)

delta.meta.artifact_id
session.knowledge().facts(kind="change")
session.evidence.trace(...)
```

这样 agent 即使不用 claim workflow，也自然更容易拿到“可审计事实”。

**2. Skill 能做的事**

Skill 只能做软约束：

```text
最终回答必须基于 session.knowledge().facts()
不要只根据 frame.show()/to_pandas() 写结论
相关性不能写成因果
inconclusive 必须保留 caveat
```

这有用，但不是强制。它依赖 agent 遵守 instruction。对 Codex/Claude 这类 agent，skill 能显著影响行为；但不能形成安全边界。

**3. 真正能强制的地方只在集成层**

只有当上层产品/工具链控制 agent 的出口时，才有硬约束：

- MCP tool wrapper 不暴露 raw Python，只暴露 `verify_claim` / `facts` / `report_check`
- BI/report artifact 只能由 `marivo.publish(...)` 生成
- CI 检查报告文件必须包含 claim/evidence ids
- 企业内部平台规定“正式分析报告必须通过 evidence checker”
- agent final answer 前必须调用一个 checker tool

如果没有这些集成，Marivo 不能阻止 agent 绕过。

**设计上的修正**

因此，claim workflow 不应该被设计成“强制 agent 守规矩”的唯一手段。更合理的是：

```text
第一优先级：typed intents 自动产出高质量 evidence/facts
第二优先级：claim workflow 用于更高阶、需要裁决的结论
第三优先级：report checker / publish 作为可选集成能力
第四优先级：skill 文档引导 agent 使用这些事实
```

也就是说，不要指望 agent 每次都先手写 claim。高频互联网分析里，很多事实应该由操作结果自动 materialize：

```text
compare -> 自动生成 ChangeFact
decompose -> 自动生成 DriverFact
correlate -> 自动生成 AssociationFact
hypothesis_test -> 自动生成 TestedHypothesisFact
forecast -> 自动生成 ForecastFact
```

Agent 的工作是读取这些 facts，并在需要更复杂判断时再显式创建 claim。

**claim workflow 仍然有意义，但意义不是强制**

它的意义是：

1. 给复杂结论一个结构化承载物
   例如“Android 新用户下降是本周 DAU 下滑的主要驱动”。

2. 给报告 QA 一个可检查对象
   即使不能强制 final answer，也可以检查 Markdown/报告产物。

3. 给多轮分析一个记忆结构
   下一轮 agent 可以恢复“已经验证了什么，还没验证什么”。

4. 给组织流程一个审计标准
   正式报告、复盘、看板注释可以要求 claim ids。

但如果你的应用场景只是“用户在 chat 里问，agent 用 Marivo 算完直接自然语言回答”，且没有 MCP wrapper、report artifact、CI、平台发布入口，那么 claim workflow 的强制价值确实有限。那时更应该投资在：

```text
result.meta 证据字段
session.knowledge().facts()
清晰的 frame.show()
更好的 fact rendering
skill 引导
```

而不是重型 claim verification 系统。

**我会把结论改成这样**

Marivo 不能控制 agent 的最终判断；Marivo 只能做到：

```text
让正确使用证据变得最容易
让不使用证据变得可见
让正式产物在有集成时可检查
```

所以这个范式是否值得做，取决于产品形态：

| 产品形态 | claim workflow 价值 |
|---|---|
| 普通 Python 库 + 任意 agent | 中低，主要是 evidence/fact surface |
| Codex/Claude skill 引导 | 中，能改善习惯但不能强制 |
| MCP/tool wrapper 控制操作入口 | 高 |
| Marivo 生成/发布正式报告 artifact | 高 |
| 企业分析平台要求审计 | 很高 |

你的怀疑是合理的：**如果 Marivo 永远只是裸 Python 库，且没有任何上层集成，那么“强制主张-验证-裁决”不可行，也不应作为核心卖点。** 核心卖点应改成“typed intents 自动生成可审计事实；agent 可选择引用，集成层可选择强制”。

## 11. 是否暴露 claim 定义/验证接口

**User**

一种是不对agent暴露claim 定义/验证接口，artifact-finding-claim-assessment, typed intent执行后自动触发。第二种是对agent暴露claim 定义/验证接口，由agent显式提出claim，并触发验证。是否需要暴露我觉得最主要的判断标准是一个可以基于规则生成的claim验证任务及对结果的判断对于agent是否足够简单，如果足够简单，agent直接生成typed intent dag任务，更加灵活，表达能力更丰富，结果信息也更多数据细节。如果比较复杂，agent容易出错，那让agent直接写claim就有好处。而是否足够简单就要看基于规则生成的claim验证任务的表达能力怎么样，你是如何看待这个问题的

**Assistant**

我基本同意你的判断标准，而且它比“是否想要 claim 这个概念”更准确：

**只有当 claim -> verification plan -> assessment 这条规则链能替 agent 承担非平凡复杂性时，才值得暴露 claim 定义/验证接口。否则不该暴露，自动从 typed intent 产出 facts 就够了。**

我的判断是：应该分层，不要二选一。

**1. 简单 claim 不值得暴露给 agent**

这类 claim 的验证任务非常直接：

```text
DAU 是否下降？
收入环比变化多少？
本周留存是多少？
某版本错误率是否高于上周？
A/B 实验 treatment 是否高于 control？
```

对应 DAG 很简单：

```text
observe -> observe -> compare
observe -> aggregate
hypothesis_test
assess_quality
```

这些让 agent 直接写 typed intent 更自然，信息也更多。Marivo 应该自动从结果里生成 facts/claims/assessments：

```python
cur = session.observe(dau, timescope=this_week)
base = session.observe(dau, timescope=last_week)
delta = session.compare(cur, base)

# 自动出现
knowledge.facts(kind="change")
```

不需要 agent 显式写：

```python
session.claims.verify(ChangeClaim(...))
```

否则 API 只是套壳，增加心智负担。

**2. 复杂 claim 才值得暴露**

值得暴露的是这些 agent 容易写错、漏步骤、过度推断的 claim：

```text
“DAU 下降主要由 Android 新用户驱动”
“支付失败率上升解释了收入下降”
“版本 6.10 发布导致注册转化下降”
“本次实验提升了长期留存且没有伤害收入”
“推荐回滚版本 6.10”
```

这些不是单个 typed intent 能表达清楚的。它们需要：

- 多个 operator
- 前置 claim 依赖
- 质量门禁
- 阈值判断
- scope 一致性检查
- association vs causation 边界
- 反证检查
- caveat 生成

这里 claim 接口有价值，因为它把 agent 容易犯错的地方变成规则化编译和裁决。

**3. 判断“是否足够简单”的标准**

我会用这几个条件判断。

如果满足以下条件，**不要暴露 claim，自动生成即可**：

```text
1. 单个 intent 或固定 2-3 个 intent 可完成
2. 没有多条候选验证路径
3. 不需要业务阈值之外的复杂判断
4. 输出事实可以直接从 artifact deterministic 投影
5. 裁决规则只有方向/阈值/显著性/质量状态
```

例如：

```text
change
metric value
rank
simple anomaly
simple experiment lift
simple correlation
```

如果满足以下条件，**应该暴露 claim**：

```text
1. 需要组合多个 facts/artifacts
2. 需要选择验证策略
3. 需要先验证子主张
4. 容易混淆相关/归因/因果
5. 需要反证或敏感性检查
6. 需要强制声明 assumptions
7. 结果不是一个 dataframe 能直接表达的
```

例如：

```text
driver explanation
root-cause hypothesis
causal claim
recommendation claim
multi-metric tradeoff claim
long-term effect claim
ecosystem/quality claim
```

**4. claim verification 的表达能力应该是什么**

claim verification 不应该只是把 typed intent 包一层。它必须能表达：

```text
依赖关系：
  C2 depends_on C1

验证 obligation：
  must_run compare
  must_run decompose(axis=platform)
  must_run assess_quality

裁决 predicate：
  total_change < -3%
  contribution_share(platform=Android) >= 50%
  no blocking_quality_issue

反证：
  if iOS contributes more -> refute primary_driver(Android)
  if quality issue exists -> inconclusive
  if only correlation evidence -> unsupported_as_causal

输出强度：
  descriptive
  suggestive
  confirmatory
  causal
```

也就是说，claim 的表达能力不在于替代 `observe/compare/decompose`，而在于表达 typed intents 之间的**逻辑关系和裁决规则**。

**5. 一个合理的 API 分层**

我会设计成三层：

```text
Layer 1: Typed intents
  observe / compare / decompose / correlate / test / forecast
  面向探索和基础事实生产

Layer 2: Auto facts
  typed intents 自动生成 artifact-finding-claim-assessment
  面向简单事实，不要求 agent 显式定义 claim

Layer 3: Explicit claims
  agent 显式提交复杂 claim
  Marivo 编译验证 plan，执行或复用 existing artifacts，裁决 claim
```

这避免“所有东西都 claim 化”的笨重，也保留复杂问题的审计能力。

**6. 典型分流例子**

用户问：“为什么 DAU 下跌？”

Agent 不应该一上来写复杂 claim。它应先用 typed intents 探索：

```text
observe DAU this week
observe DAU last week
compare
discover slices
decompose by platform
```

Marivo 自动产出：

```text
F1: DAU WoW -5%
F2: Android contributes 68% of decline
F3: New users contribute 74% of Android decline
```

到这里不需要 explicit claim。

但如果 agent 想报告：

```text
“DAU 下跌主要由 Android 新用户驱动”
```

这就是一个组合解释 claim，应该显式登记：

```text
Claim:
  type=driver_explanation
  parent_change=F1
  drivers=[platform=Android, user_type=new]
  threshold=primary_driver
```

Marivo 验证：

```text
F1 是否成立
Android 是否 top contributor
Android-new 是否在 Android 内 top contributor
贡献是否超过 threshold
quality 是否通过
是否有更强 competing driver
```

这个 claim 接口才有意义。

**7. 我的结论**

你提出的判断标准是对的，而且可以形成设计原则：

```text
Simple, local, deterministic facts:
  typed intents first, auto claim/fact extraction.

Composite, high-risk, interpretation-heavy conclusions:
  explicit claim interface.

Reports:
  prefer assessed claims, but do not force every artifact through explicit claim.
```

所以我不建议一开始就对 agent 暴露一个泛化的 `Claim` 万能接口。那会让 agent 写大量低价值包装。

更好的路线是：

1. 先把 typed intents 后的自动 facts 做强。
2. 定义少数高价值 explicit claim 类型，而不是开放自由 claim。
3. 每个 explicit claim 类型必须证明它能编译出 agent 容易漏掉的验证 DAG 和裁决规则。
4. 如果某个 claim 类型的编译结果只是一个显而易见的 `compare()`，不要暴露。

一句话：**claim 接口应该只暴露在“它比 agent 直接写 typed intent 更不容易错”的地方。否则，claim 应该是 Marivo 自动生成的证据层对象，而不是 agent 的主要操作入口。**

## 12. 复杂 claim 隐藏内部阈值与限定知识的风险

**User**

一个比较严峻的问题是，复杂的claim，能够通过claim定义+规范化推导裁决，是否推导规则需要依赖大量内部的阈值/限制，如果隐藏大量的内部限定的知识，得出的结论对agent/用户是有价值还是有害的就很难说了。

**Assistant**

你这个担心是对的，而且是 claim workflow 最大的风险之一。

**复杂 claim 最危险的地方不是规则不够强，而是规则看起来太强。**
如果 Marivo 在内部藏了大量阈值、经验限制、启发式，然后输出：

```text
status = validated
```

agent 和用户很可能会误以为这是可靠结论。实际它可能只是：

```text
在 Marivo 默认阈值和默认解释框架下，通过了一组启发式检查
```

这会有害。

**所以复杂 claim 的裁决不能做成黑盒规则**

复杂 claim 不应该输出一个单薄的：

```text
validated / refuted
```

而应该输出：

```text
assessment:
  status: suggestive | supported | contradicted | inconclusive | unsupported
  strength: exploratory | confirmatory | causal
  decision_basis:
    passed_checks: [...]
    failed_checks: [...]
    assumptions: [...]
    thresholds_used: [...]
    defaults_used: [...]
    unresolved_questions: [...]
    competing_explanations: [...]
```

也就是说，复杂 claim 的价值不在于“Marivo 自动得出最终判断”，而在于**系统化暴露判断条件**。

**内部阈值必须可见、可配置、可降级**

规则里凡是涉及这些内容，都不能隐藏：

- primary driver 的贡献阈值
- anomaly threshold
- correlation strength threshold
- minimum sample size
- p-value / confidence interval 标准
- quality blocking threshold
- top-k / coverage / residual threshold
- causal claim 所需设计条件
- “主要由 X 贡献”的定义
- “业务显著”的定义

如果没有用户/项目配置，就不能默认冒充业务标准。只能说：

```text
using default exploratory threshold
```

并把结论强度降级。

例如：

```text
Claim: DAU 下降主要由 Android 新用户驱动

Assessment:
  status: supported
  strength: exploratory
  thresholds_used:
    primary_driver_share >= 50% default
    residual_share <= 20% default
  passed:
    total DAU declined 5.0%
    Android contributed 68%
    Android new users contributed 74% of Android decline
  caveats:
    primary_driver threshold is default, not business-configured
    no causal design
```

这比：

```text
validated
```

安全得多。

**复杂 claim 应该更像 checklist，不像 theorem prover**

对复杂 claim，Marivo 不应该假装“推导出真理”。它应该做的是：

```text
把一个复杂结论拆成一组可检查 obligations
逐项执行
逐项暴露结果
给出有限强度的 assessment
```

比如“支付失败导致收入下降”：

```text
checks:
  revenue declined? yes
  payment failure increased? yes
  temporal ordering holds? yes
  affected segment overlap? partial
  correlation significant? yes
  experiment/quasi-experiment exists? no
  confounders checked? no

assessment:
  unsupported_as_causal
  suggestive_as_association
```

这有价值，因为它阻止 agent 把关联写成因果，同时告诉 agent 还缺什么证据。

**什么时候复杂 claim 有价值**

有价值的前提是：

1. **规则是透明的**

   用户能看到到底检查了什么。

2. **阈值是显式的**

   默认阈值必须标记为 default，不得假装业务标准。

3. **结论强度分级**

   不能只有 `validated`。至少要区分：

   ```text
   descriptive
   exploratory_supported
   statistically_supported
   causally_supported
   decision_ready
   ```

4. **允许 agent/user override**

   agent 可以说：

   ```text
   primary_driver_threshold=0.4
   min_sample_size=10000
   require_causal_design=True
   ```

5. **暴露未满足条件**

   一个复杂 claim 的最大价值往往不是“通过”，而是告诉你：

   ```text
   当前证据不足以支持这个说法，缺 experiment / 缺质量检查 / 缺反证排除。
   ```

**什么时候复杂 claim 有害**

有害的情况：

- 默认阈值太多但不暴露
- 输出强裁决词，比如 `validated`
- 混合统计、业务、因果判断但不分层
- 不说明 claim 是 exploratory 还是 confirmatory
- 不暴露 competing explanations
- agent 不能查看裁决依据
- 用户无法配置业务阈值

这种 claim workflow 会制造“自动化权威幻觉”，比 agent 自己写结论还危险。

**设计建议**

我会避免把复杂 claim 设计成：

```python
assessment = session.verify(claim)
print(assessment.status)  # validated
```

而应该设计成：

```python
assessment = session.verify(claim)
assessment.show()
```

输出类似：

```text
Claim: Android new users primarily drove DAU decline

Conclusion strength: exploratory_supported
Not causal.

Passed checks:
- DAU declined 5.0% WoW
- Android contributed 68% of decline
- New users contributed 74% of Android decline

Defaults used:
- primary_driver_share >= 50%
- residual_share <= 20%

Failed / missing checks:
- No causal design
- No competing driver sensitivity check by app_version
- No business-configured materiality threshold

Recommended next checks:
- decompose Android decline by app_version within Android new users
- inspect registration failure rate
```

这才是 agent/用户真正需要的。

**我的判断**

复杂 claim 可以做，但不应该以“自动裁决最终结论”为目标。它应该以这三个目标为核心：

```text
1. 明确这个复杂结论需要哪些证据
2. 自动执行能执行的检查
3. 透明暴露通过、失败、默认阈值、缺口和结论强度
```

如果做不到透明，就不要做复杂 claim。
宁可让 agent 用 typed intents 自己分析，也不要给它一个黑盒 `validated`。

所以你的担心可以转化成一条产品原则：

**复杂 claim 的 assessment 必须是 evidence checklist + strength label，不是隐藏阈值驱动的 verdict。**

## 13. 暴露判断条件和内部阈值是否有意义

**User**

暴露判断条件/内部阈值有意义么？agent并不了解marivo内部的判断逻辑/阈值使用场景

**Assistant**

有意义，但**只暴露原始阈值本身不够**。如果只是告诉 agent：

```text
primary_driver_share >= 0.5
residual_share <= 0.2
min_sample_size >= 30
```

这对 agent 的帮助有限，甚至会制造新的误解。因为 agent 不知道这些阈值的适用场景、来源、强弱和能否覆盖当前业务问题。

所以应该暴露的不是“内部阈值列表”，而是 **裁决契约**。

**裁决契约应包含什么**

一个 assessment 至少应该告诉 agent：

```text
1. 这个规则想回答什么问题
2. 它使用了哪些检查
3. 每个检查为什么相关
4. 阈值从哪里来：用户配置 / 项目默认 / Marivo 探索默认
5. 阈值适用范围是什么
6. 当前结果离阈值有多远
7. 通过/失败对结论强度意味着什么
8. 哪些情况该降级或不能使用
9. 还缺什么证据
```

例如不要只暴露：

```text
primary_driver_share >= 50%
```

而是暴露：

```text
Check: primary_driver_share
Purpose: 判断某个 segment 是否可以称为“主要贡献项”
Rule: contribution_share >= 50%
Source: Marivo exploratory default
Applicability: 适用于 additive metric 的贡献分解；不表示因果
Observed: Android contributed 68%
Margin: +18 percentage points
Effect on assessment: supports “primary contributor” wording
Caveat: 未配置业务阈值；结论强度为 exploratory_supported
```

这对 agent 是有用的，因为它不需要理解 Marivo 内部实现，只需要理解**这条规则能支持什么措辞、不能支持什么措辞**。

**更重要的是暴露 wording guidance**

Agent 最终要写报告，所以 Marivo 应该把规则结果翻译成可用/不可用的表达边界：

```text
Allowed wording:
  “Android 是本次 DAU 下降的主要贡献项”
  “Android 贡献了 68% 的下降”

Disallowed wording:
  “Android 导致了 DAU 下降”
  “Android 是根因”
  “应立即回滚 Android 版本”
```

这比暴露内部阈值更有价值。

**阈值应该分级，而不是平铺**

阈值来源必须明确：

```text
user_configured:
  可以支撑较强结论

project_default:
  可以支撑项目内一致口径结论

marivo_exploratory_default:
  只能支撑探索性/提示性结论

statistical_convention:
  需要说明 p-value / CI 的解释边界

unsupported:
  没有可用规则，不能裁决
```

Agent 不需要知道每个阈值为什么是 0.5，但必须知道：

```text
这是用户配置的业务阈值，还是 Marivo 默认启发式？
```

这直接决定报告措辞强度。

**如果 agent 不了解规则，assessment 应该自解释**

换句话说，assessment 不能是机器内部对象，而应该是 agent-facing result：

```text
Assessment:
  status: supported
  strength: exploratory
  summary: Android contributed 68% of the DAU decline, passing the default primary-driver threshold of 50%.
  why_this_matters: This supports describing Android as the largest/primary contributor, but not as a causal root cause.
  caveats:
    - threshold is Marivo default, not business-configured
    - attribution is additive decomposition, not causal inference
  next_checks:
    - decompose Android decline by new/returning users
    - inspect app_version and registration errors
```

这才是给 agent 的接口。

**所以暴露判断条件有意义，但目标不是让 agent复刻 Marivo 逻辑**

目标是让 agent 知道：

```text
这个结论为什么被支持
它能写成什么
它不能写成什么
它有多强
它缺什么
```

如果只暴露阈值，没有解释、适用范围、强度和措辞边界，那确实意义不大。

**设计结论**

复杂 claim 的 assessment 不应该暴露“内部阈值”，而应该暴露：

```text
evidence checklist
rule purpose
threshold source
observed value
margin to threshold
assessment strength
allowed wording
disallowed wording
missing evidence
next checks
```

这样即使 agent 不懂 Marivo 内部逻辑，也能安全使用结果。

## 14. 复杂 claim 接口必要性的质疑

**User**

marivo只能暴露结构化数据及其render，根本表达不了你示例中复杂的文本字段。此外你给出的裁决契约示例，我觉得agent理解起来也会很费劲，我现在严重质疑即使对于复杂claim暴露接口的必要性了。

**Assistant**

我倾向于同意你的结论：**在 Marivo 作为 Python 库、只暴露结构化对象和 render 的前提下，复杂 claim 显式接口的必要性很弱，甚至可能有害。**

原因很简单：复杂 claim 的价值依赖“解释裁决边界”。但 Marivo 没有 LLM 能力，也不应该生成大段判断文本；如果只返回一堆结构化字段、阈值、状态码，agent 理解成本很高，还可能误用。那不如让 agent 直接使用 typed intents 组合 DAG，更透明、更灵活。

我会把设计收缩成这样：

```text
不要把 explicit complex claim 作为核心 public API。
核心应该是 typed intents -> 自动 evidence/facts/assessments。
```

也就是说：

```text
observe/compare/decompose/correlate/test/forecast
  -> artifact
  -> finding
  -> fact
  -> lightweight assessment
```

Agent 主要读这些事实，而不是先写复杂 claim。

**应该保留的能力**

Marivo 可以做：

1. **自动事实化**

   `compare()` 后自动产生 `ChangeFact`。
   `decompose()` 后自动产生 `DriverFact`。
   `correlate()` 后自动产生 `AssociationFact`。
   `hypothesis_test()` 后自动产生 `TestedHypothesisFact`。

2. **结构化质量/限制标记**

   用简单 enum，而不是复杂文本：

```text
evidence_status = complete | partial | unavailable
strength = descriptive | suggestive | statistical | causal | unsupported
limitations = [
  DEFAULT_THRESHOLD_USED,
  QUALITY_ISSUE_PRESENT,
  OBSERVATIONAL_ONLY,
  LOW_SAMPLE_SIZE,
  NO_CAUSAL_DESIGN
]
```

3. **查询 session knowledge**

   Agent 可以问：

```python
knowledge.facts(kind="change")
knowledge.facts(kind="driver")
knowledge.open_items()
knowledge.next_steps()
```

4. **提供 analysis recipe，而不是 claim API**

   比如：

```python
session.recipes.diagnose_change(metric, current, baseline, dimensions=[...])
```

这个比：

```python
session.verify(ComplexClaim(...))
```

更实用。它不宣称“验证一个复杂主张”，只产出一组 typed artifacts/facts，agent 自己综合。

**什么时候才值得暴露 claim**

只在非常窄的场景：

- 输入完全结构化
- 规则非常清楚
- 输出不需要复杂解释
- 裁决不会隐藏大量业务假设
- agent 直接写 typed intent 容易漏掉固定步骤

比如实验类 claim 可能值得：

```python
ExperimentClaim(metric, treatment, control, alpha, guardrails)
```

因为实验裁决模板相对稳定。

但像这些我不建议暴露成 claim API：

```text
“主要根因是 X”
“应该回滚版本”
“内容质量下降导致留存下降”
“某策略长期有效”
```

这些应该由 agent 基于多个 facts 综合判断，不应该由 Marivo 裁决。

**更实际的产品方向**

我会把目标从：

```text
复杂 claim -> 自动验证 -> 自动裁决
```

改成：

```text
typed intent DAG -> 自动沉淀事实 -> agent 基于事实总结
```

Marivo 提供的是“证据地基”，不是“结论机器”。

最终边界：

```text
Marivo:
  负责事实、证据、质量、限制、可追溯性。

Agent:
  负责组合事实、解释业务含义、提出判断、写报告。
```

所以你的质疑成立：**如果复杂 claim 的安全使用依赖 agent 理解一大套 Marivo 内部裁决契约，那这个接口就不该暴露。**

更好的路线是先把自动 evidence/fact surface 做强，复杂 claim 先不做 public API，最多作为内部实验或少数 recipe。
