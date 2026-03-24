# Factum 改进路线图

> 基于设计文档评审（2026.03），覆盖 Evidence Engine 深化、因果推理框架、Synthesis 触发机制三个核心主题。

## 背景

设计评审识别出三个结构性缺失：

1. **Extractor 覆盖面不足**：仅 2 个提取器（ComparisonRowExtractor、AggregateRowExtractor），但承诺 7 种 observation 类型
2. **不区分相关性与因果性**：Claim 的 confidence 分数混合两个维度，误导决策者
3. **Agent 控制流边界未定义**：synthesize_findings 触发时机是悬而未决的核心问题

修改方向：**先把证据层做实，然后用可靠的证据层去喂 reflection loop——这个顺序不能反。**

---

## 第一批：P0 — 结构性基础

> 目标：解决 Factum 与 Agent 之间的控制流如何运作。
> 依赖关系：M-12 → M-03 → M-04；M-01 和 M-02 可并行。

### M-12 Factum-Agent 控制流边界约定

**优先级**：P0（第一个实施）
**影响**：新增章节（已写入设计文档 §13.8.5）
**工作量**：设计文档已完成，代码层面需要在 API response 中体现职责边界

内容：
- 明确 Factum 的职责：测量与暴露（提取 observations、计算 confidence、检测 contradictions、增量合成、返回 readiness signal）
- 明确 Agent 的职责：解释与决策（解读 readiness signal、决定下一步、触发 synthesis、做 tradeoff）
- 核心原则：Factum 不"决定"，Agent 不"猜"

实施要点：
- [ ] 在 API 文档中明确标注哪些字段是"信号"（Factum 提供）、哪些是"决策"（Agent 负责）
- [ ] 审计现有 endpoint response，确保不隐含决策逻辑

### M-03 增量合成重构

**优先级**：P0
**影响**：`app/evidence_engine/`、`app/service.py`、`app/analysis_core/step_runners/`
**工作量**：大（核心逻辑重构）
**依赖**：M-12（先明确原则）

内容：
- 将 synthesize_findings 从"大爆炸式终态步骤"重构为"增量合成 + Promotion"
- 每次 step 执行完毕后自动执行增量合成：
  - 归入已有 claims（scope 匹配）
  - 创建 tentative claim
  - 检测矛盾
  - 更新 confidence
- synthesize_findings 变为 promotion 操作

实施要点：
- [ ] 在 `app/evidence_engine/` 中新增 `incremental_synthesizer.py`
- [ ] Claim 表增加 `status` 枚举：`tentative` / `confirmed` / `insufficient`
- [ ] 在每个 step runner 的 post-execution hook 中调用增量合成
- [ ] 重写 `synthesize_findings` runner 为 promotion 逻辑
- [ ] 更新 `app/storage/schema.py` 中 claims 表 DDL
- [ ] 迁移测试：确保旧 synthesize_findings 测试在新模式下通过

### M-04 Readiness Signal API

**优先级**：P0
**影响**：`app/api/sessions.py`、`app/service.py`
**工作量**：中
**依赖**：M-03（需要 tentative claims 和增量合成数据）

内容：
- 在每个 step response 中增加 `readiness` 字段
- 五个维度：goal_coverage、evidence_sufficiency、contradiction_resolution、budget_remaining、diminishing_returns
- 输出排序的 `suggested_actions` 列表

实施要点：
- [ ] 新增 `app/evidence_engine/readiness.py`：计算五维信号
- [ ] 在 step response 中增加 `live_claims` 和 `readiness` 字段
- [ ] `suggested_actions` 包含：continue_exploring、synthesize、resolve_contradiction、stop（budget exhausted）
- [ ] 更新 `app/api/models.py` 中的响应模型
- [ ] 单元测试：各种场景下的 readiness 信号计算

### M-01 Extractor 注册表

**优先级**：P0
**影响**：`app/evidence_engine/`
**工作量**：大（需要实现 5 个新 Extractor）

内容：
- 建立 StepType → ExtractorType → ObservationType 的形式化映射
- 每个 Extractor 声明：消费的 artifact 类型、产出的 observation 类型、前置条件
- 优先补齐 FunnelExtractor、AnomalyExtractor、ContributionShiftExtractor

实施要点：
- [ ] 新增 `app/evidence_engine/registry.py`：Extractor Registry
- [ ] 定义 `ExtractorContract` 基类（artifact_type、observation_types、preconditions）
- [ ] 实现 `FunnelExtractor` → funnel_drop
- [ ] 实现 `AnomalyExtractor` → anomaly_detection
- [ ] 实现 `ContributionShiftExtractor` → contribution_shift（从 AggregateRowExtractor 分化）
- [ ] QoE/Ad/Recommendation Extractor 可延后（需要对应的 step type 支持）
- [ ] 在 step runner 中通过 registry 查找 extractor，替代硬编码

### M-02 inference_level 字段

**优先级**：P0（可与 M-01 并行）
**影响**：`app/storage/schema.py`、`app/evidence_engine/`、`app/models.py`
**工作量**：中

内容：
- 在 Claim 上增加 `inference_level`（L0-L5）和 `inference_justification`（JSON 列表）
- 默认 L0（co-occurrence），由因果检验器自动升级

实施要点：
- [ ] Claims 表增加 `inference_level TEXT DEFAULT 'L0'` 和 `inference_justification_json TEXT`
- [ ] 更新 Claim 生成逻辑，初始化为 L0
- [ ] 在增量合成中，当检测到跨切片一致性时自动升至 L1
- [ ] API response 中暴露 inference_level
- [ ] 更新证据图 API 响应

---

## 第二批：P1 — 因果推理与可解释性

> 目标：在第一批基础上增强证据质量和可解释性。
> 依赖：全部依赖第一批完成。

### M-07 扩展 Evidence Edge 类型

**优先级**：P1
**影响**：`app/storage/schema.py`、`app/evidence_engine/`
**依赖**：M-02

内容：
- 基础层保留：supports、contradicts、justifies
- 增加因果增强层：correlates_with (L0/L1)、temporally_precedes (L2)、mechanistically_explains (L3)、eliminates_alternative (L4)、experimentally_confirms (L5)
- Synthesizer 根据 edge 类型分布自动推断 inference_level

实施要点：
- [ ] evidence_edges 表的 edge_type 扩展
- [ ] 更新 synthesizer，基于 edge 类型自动推断 inference_level
- [ ] 保持向后兼容：基础层 edge 不受影响

### M-08 时序标注

**优先级**：P1
**影响**：`app/storage/schema.py`、`app/evidence_engine/`
**依赖**：M-07

内容：
- Observation 增加 `observed_window`（时间窗口）和 `temporal_order`（发现顺序）
- Edge 增加 `precedes` 类型

实施要点：
- [ ] observations 表增加 `observed_window_json TEXT` 和 `temporal_order INTEGER`
- [ ] step runner 在生成 observation 时填充时序信息
- [ ] 时序信息流入因果检验器（M-09）

### M-09 确定性因果检验器

**优先级**：P1
**影响**：新增 `app/evidence_engine/causal_checkers.py`
**依赖**：M-07 + M-08
**工作量**：大

内容：
- 跨切片一致性检验（L0→L1）
- 时间先后检验（L1→L2）
- 剂量-反应检验（L1 bonus）
- 逆转检验（L2 bonus）

实施要点：
- [ ] 新增 `app/evidence_engine/causal_checkers.py`
- [ ] `CrossSliceConsistencyChecker`：分组后统计效应方向一致率
- [ ] `TemporalPrecedenceChecker`：比较 period_start，检测 lag 一致性
- [ ] `DoseResponseChecker`：Spearman 相关系数
- [ ] `ReversalChecker`：干预前后指标反转检测
- [ ] 在增量合成流程中自动调用检验器，升级 inference_level
- [ ] 全面单元测试

### M-06 Synthesizer 拆分

**优先级**：P1（可独立推进）
**影响**：`app/evidence_engine/synthesizers/`
**工作量**：中

内容：
- Scope Clustering → Signal Alignment → Claim Formulation
- 每步日志输出

实施要点：
- [ ] 将 synthesizer 拆分为三个独立函数/类
- [ ] 每步生成审计日志（scope_clusters、alignment_scores、formulation_decisions）
- [ ] 审计日志持久化到 step artifacts

### M-05 置信度校准

**优先级**：P1（可并行启动，需要数据积累）
**影响**：`app/evidence_engine/scoring.py`
**工作量**：中（基础设施）+ 长期（数据积累）

内容：
- 暴露 raw_score + calibrated_confidence
- 收集人类判断基线
- 校准方法：isotonic regression 或分箱映射

实施要点：
- [ ] Confidence 对象从标量改为 `{raw_score, calibrated_confidence}` 结构
- [ ] 新增 `app/evidence_engine/calibration.py`
- [ ] 提供收集人类判断的 API endpoint（可选）
- [ ] calibrated_confidence 初始为 null
- [ ] 向后兼容：API 消费者可继续使用 raw_score

---

## 第三批：P2 — 工程优化与 Reflection Loop

### M-10 Recommendation 因果标签

**优先级**：P2
**影响**：`app/evidence_engine/synthesizers/`、`app/storage/schema.py`
**依赖**：M-02 + M-09

内容：
- Recommendation 增加 `causal_basis` 字段
- 包含：inference_level、最强证据摘要、未解决混淆因素、建议验证方式

实施要点：
- [ ] recommendations 表增加 `causal_basis_json TEXT`
- [ ] recommendation 生成时自动从 supporting claims 提取因果信息
- [ ] API response 中暴露

### M-11 Reflection Loop

**优先级**：P2
**影响**：`app/planner/`、新增 `app/reflection/`
**工作量**：大（核心新能力）

内容：
- 暂停 catalog adapter 横向扩展
- 最小 reflection loop：step 完成后，evidence graph 快照序列化为结构化 prompt
- LLM 回答三个问题：证据够不够？还有什么假设没验证？下一步查什么？
- LLM 回答 parse 成 plan patch，走现有 planning 系统执行

实施要点：
- [ ] 新增 `app/reflection/` 模块
- [ ] `EvidenceGraphSerializer`：将 evidence graph 序列化为 LLM-friendly 结构化 prompt
- [ ] `ReflectionPromptBuilder`：构建三问 prompt
- [ ] `PlanPatchParser`：将 LLM 回答 parse 为 plan patch
- [ ] 集成到 `ReplanningService`
- [ ] 可配置 LLM backend（Claude API / OpenAI API / local）

---

## 持续 / 未来工作

| 项目 | 状态 | 备注 |
|------|------|------|
| Auth / RBAC | 未开始 | 生产部署前必须 |
| 生产级 job queue | 未开始 | 替换当前后台线程 + 同步降级 |
| Streaming step execution | 未开始 | 大表场景需要 |
| Lineage graph | 未开始 | 已有 provenance 跟踪 |
| Snowflake adapter | 未开始 | 优先级低于证据层深化 |

---

## 实施时间线建议

```
Phase 1 (P0): ~6-8 weeks
  Week 1-2: M-12 控制流边界 + M-02 inference_level
  Week 2-4: M-03 增量合成重构
  Week 4-6: M-04 Readiness Signal + M-01 Extractor Registry
  Week 6-8: 集成测试 + API 文档

Phase 2 (P1): ~6-8 weeks
  Week 1-3: M-07 Edge 类型 + M-08 时序标注
  Week 3-6: M-09 因果检验器
  Week 4-6: M-06 Synthesizer 拆分（并行）
  Week 1-8: M-05 置信度校准（并行，持续数据积累）

Phase 3 (P2): ~4-6 weeks
  Week 1-2: M-10 Recommendation 因果标签
  Week 2-6: M-11 Reflection Loop
```

---

## 验收标准

### Phase 1 完成标准

- [ ] 每个 step response 包含 `live_claims` 和 `readiness` 字段
- [ ] synthesize_findings 表现为 promotion 操作（tentative → confirmed）
- [ ] 每个 Claim 包含 `inference_level` 和 `inference_justification`
- [ ] Extractor Registry 至少注册 4 种 extractor（2 现有 + 2 新增）
- [ ] 所有现有测试通过 + 新增测试覆盖新功能

### Phase 2 完成标准

- [ ] Evidence edge 支持因果增强类型
- [ ] Observation 包含时序标注
- [ ] L0→L1→L2 升级路径有确定性检验器支持
- [ ] Synthesizer 生成审计日志
- [ ] Confidence 对象暴露 raw_score + calibrated_confidence

### Phase 3 完成标准

- [ ] Recommendation 包含 causal_basis
- [ ] Reflection loop 能自动生成 plan patch 并执行
