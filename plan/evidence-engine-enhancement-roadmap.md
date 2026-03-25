# Evidence Engine Enhancement Roadmap

> 基于2026-03-24 oneservice集群排队时间归因分析的实际使用反馈

## 背景

### 分析场景

**问题**：分析sys_titan用户在oneservice集群出现0.45秒排队时间的原因，评估对sys_oneservice用户的影响

**实际执行步骤**（session: `sess_75f337c1c684`，共9步）：
1. `compare_metric(query_queued_time, dod, group_by=user)` → 发现sys_titan +58.5%，sys_oneservice -71.5%
2. `aggregate_query(group_by=user, where=昨天)` → 各用户排队时间绝对值分布
3. `aggregate_query(group_by=hour, where=sys_titan+昨天)` → 发现排队集中在凌晨02-04时
4. `aggregate_query(group_by=resource_group+user)` → 发现资源组隔离：sys_titan在others，sys_oneservice在oneservice
5. `compare_metric(query_count, dod, group_by=user)` → 发现sys_titan查询量+30%
6. `compare_metric(query_cpu_time, dod, group_by=user)` → sys_titan CPU仅+8.6%，排除查询变重
7. `aggregate_query(group_by=user+hour, where=02-04时)` → 确认sys_oneservice在高峰时段排队接近0
8. `compare_metric(query_queued_time, wow, group_by=user)` → 发现WoW暴增290647%（上周17K→本周130K查询）
9. `synthesize_findings` → 综合所有证据，确认13个claims，标记3个insufficient

### 期望 vs 实际

| 能力 | 期望 | 实际 | Gap |
|-----|------|------|-----|
| Observation自动提取 | 结构化事实提取 | ✅ 49个observation，含significance/quality判断 | **达成** |
| Claim聚合与置信度 | 多observation支撑同一claim | ✅ 16个claims，部分由4+个obs支撑，consistency从0.693→0.949 | **达成** |
| Insufficient过滤 | 自动标记低质量证据 | ✅ sys_trino_test（3条查询）被标为insufficient | **达成** |
| Readiness引导 | 告知何时停止探索 | ✅ goal_coverage 0.4→1.0，suggested_action从continue→synthesize | **达成** |
| 因果推理升级 | L0→L2+的inference chain | ❌ 全部claims停留在L0 | **未达成** |
| 跨claim关联 | 自动发现query_count↑与queued_time↑的因果关系 | ❌ 50条edges全为supports/justifies，无correlates_with | **未达成** |
| 行动建议 | 整合多claim生成针对性建议 | ❌ 唯一建议为"Drill into sys_titan"，未利用已有证据 | **未达成** |

### Evidence Graph 统计

```
Session: sess_75f337c1c684
Steps:          9
Observations:  49
Claims:        16 (13 confirmed, 3 insufficient)
Edges:         50 (49 supports, 1 justifies)
Recommendations: 1
Inference Level: 全部 L0
```

### 核心发现

**有价值的部分**：

1. **Observation自动提取**（★★★★☆）：每个step自动生成结构化observation，含subject/payload/significance/quality。`practical_significance=false`和`sample_size_ok=false`帮助自动过滤了sys_trino_test噪音。

2. **Claim聚合机制**（★★★☆☆）：同一slice的多次观测被聚合到同一claim。例如sys_titan的aggregate claim从1个obs增长到4个后，consistency从0.693提升到0.949，confidence从0.61提升到0.67。

3. **Readiness信号**（★★★☆☆）：`goal_coverage`从0.4→0.8→1.0的渐进式演进，`suggested_action`从`continue_exploring`→`synthesize`的自动转换，提供了有效的停止探索信号。

4. **Synthesize的confirmed/insufficient分类**（★★★☆☆）：13个claims被confirmed，3个sys_trino_test相关claims被标记insufficient（confidence<0.5），符合预期。

**未达预期的部分**：

1. **Inference Level全部L0**（★★☆☆☆）：Causal checkers未触发任何升级。分析路径中有明显的时间先后（query_count先观测到，queued_time后观测到）和跨指标相关性（query_count↑ + cpu_time↑ → queued_time↑），但均未被识别。

2. **Evidence Graph扁平化**（★★☆☆☆）：50条edges中49条是`supports`（observation→claim），1条`justifies`（recommendation→claim）。完全缺失`contradicts`、`temporally_precedes`、`correlates_with`、`mechanistically_explains`等推理edge。Graph本质上是观测-结论的单层映射，不是推理链。

3. **Recommendation太泛**（★★☆☆☆）：唯一的P1建议"Drill into sys_titan to identify root cause"——但分析已经完成了，根因就是查询量暴增+资源组配额。`unresolved_confounders`建议"normalize by workload volume"缺乏针对性。

4. **跨claim因果关系缺失**（★★☆☆☆）：`query_count +30%(DoD) / +7.5x(WoW)` 和 `queued_time +58.5%` 之间的因果关系需要人工在脑中建立。`resource_group=global.oneservice.others`作为共同维度出现在多个claim中，但系统未自动发现这一模式。

---

## Problem Analysis

### P1: Evidence Edges 仅为 supports，缺乏推理 Edge

**现象**：
```
=== Edge Types ===
  supports: 49    # observation → claim 的事实支撑
  justifies: 1    # recommendation → claim 的引用
  (缺失: temporally_precedes, correlates_with, mechanistically_explains)
```

Evidence Graph 有50条 edge，但全部是"事实记录"类型，没有"推理"类型的 edge。

**期望**：
```
claim_a6177ad8b32f (query_count↑30% for sys_titan)
    --[temporally_precedes]-->
claim_6d4dbfa0c098 (queued_time↑58.5% for sys_titan)

claim_a6177ad8b32f (query_count↑30%)
    --[correlates_with]-->
claim_680fb460e5a0 (cpu_time↑8.6%)

claim_42a64d44c2ca (sys_titan in global.oneservice.others)
    --[mechanistically_explains]-->
claim_d9f7f724977c (sys_oneservice in global.oneservice.oneservice → 不受影响)
```

**根因**：
- `IncrementalSynthesizer` 只在单个claim内部聚合observations，不做跨claim edge创建
- `supports` edge 由 observation→claim 关联自动创建，但 `correlates_with`/`temporally_precedes` 等需要跨claim推理
- `CrossScopeCorrelationChecker` 只检查同一metric的cross-slice一致性（用于L0→L1），不检查跨metric相关性
- Evidence edge DDL和API都存在且正常工作（synthesize返回了justifies edge），缺的是生成逻辑

**影响**：
- Inference Level全部停留在L0
- 用户需要手动在脑中建立 query_count↑ → queued_time↑ 的因果关系
- Recommendation引擎无法利用推理链生成针对性建议

---

### P2: Recommendations 未利用已收集的丰富证据

**现象**（实际返回）：
```json
{
  "rec_id": "rec_2b3b84478915",
  "action_text": "Drill into cluster=k8soneservice-oneservice / user=sys_titan to identify root cause of query_queued_time (observed baseline_sessions=103,424) and consider targeted experiments.",
  "priority": "P1",
  "causal_basis": {
    "inference_level": "L0",
    "strongest_evidence_summary": "query_queued_time increased 58.5% for cluster=k8soneservice-oneservice, user=sys_titan (confidence=0.94)",
    "unresolved_confounders": [{
      "key": "normalise_workload_volume",
      "text": "check whether overall workload volume accounts for the difference"
    }],
    "suggested_validation": "Run aggregate_query to compute the metric normalised by total query count..."
  }
}
```

**问题**：
1. 建议"Drill into sys_titan to identify root cause"——但session已经有13个confirmed claims，根因已明确
2. `unresolved_confounders`建议normalize workload——但我们已经有query_count DoD +30%/WoW +7.5x的confirmed claim，这正是confounder的答案
3. 只生成了1条recommendation，没有整合16个claims的多角度发现
4. 未提及资源组隔离这个关键发现（sys_titan在others，sys_oneservice在oneservice）

**期望**：
```json
{
  "action_text": "sys_titan查询量DoD增长30%（WoW增长7.5倍），集中在凌晨02-04时的批处理高峰，打满global.oneservice.others资源组配额，导致avg排队时间从0.45s升至0.71s。建议：(1)排查sys_titan批量任务增长原因 (2)评估others资源组配额扩容",
  "causal_basis": {
    "inference_level": "L2",
    "supporting_claims": ["claim_a6177ad8b32f", "claim_6d4dbfa0c098", "claim_42a64d44c2ca"],
    "causal_chain": "query_count↑30%(DoD) → others资源组并发打满 → queued_time↑58.5%",
    "resolved_confounders": ["workload volume: confirmed +30% DoD via claim_a6177ad8b32f"]
  }
},
{
  "action_text": "sys_oneservice不受影响：位于独立资源组global.oneservice.oneservice，昨天avg排队0.0004s，高峰时段max仅0.18s。无需干预。",
  "priority": "P3",
  "supporting_claims": ["claim_23e8d93894a4", "claim_d9f7f724977c", "claim_33af0fd6482a"]
}
```

**根因**：
- `RecommendationEngine`只基于最高confidence的单个claim生成建议，不聚合相关claims
- 没有检查`unresolved_confounders`是否已被其他claims解答（claim_a6177ad8b32f已回答了workload volume问题）
- 没有利用resource_group维度的cross-claim模式（同一resource_group出现在多个claims中）
- 缺少"无需行动"类型的recommendation（对sys_oneservice的评估结论）

---

### P3: Cross-Scope Correlation Missing

**现象**：
session中存在明显的跨metric关联，但系统未识别：

```
claim_a6177ad8b32f: query_count +30% for user=sys_titan      (confirmed, conf=0.91)
claim_6d4dbfa0c098: queued_time +58.5% for user=sys_titan    (confirmed, conf=0.94)
claim_680fb460e5a0: cpu_time +8.6% for user=sys_titan        (confirmed, conf=0.74)
```

这三个claims共享完全相同的scope（`cluster=k8soneservice-oneservice, user=sys_titan`），且在同一时间窗口观测到，存在明显的因果关联（量增→资源争抢→排队），但系统未创建任何cross-claim edge。

同时，resource_group维度的claims也存在互补关系：
```
claim_42a64d44c2ca: sys_titan在global.oneservice.others (avg_queue=0.71s)
claim_d9f7f724977c: sys_oneservice在global.oneservice.oneservice (avg_queue≈0)
```
两个用户在不同资源组，这直接解释了"为何sys_oneservice不受影响"，但系统未建立`mechanistically_explains` edge。

**期望**：
```
claim_a6177ad8b32f (query_count↑30%)
    --[correlates_with, r=0.95]--> claim_6d4dbfa0c098 (queued_time↑58.5%)
claim_42a64d44c2ca (sys_titan in others)
    --[mechanistically_explains]--> claim_d9f7f724977c (sys_oneservice in oneservice → 不受影响)
UPGRADE: claims升级到L1+
```

**根因**：
- 没有`CrossMetricCorrelationChecker`——现有checkers只处理单metric的cross-slice一致性
- scope overlap检测逻辑不存在——没有机制发现"两个claims的slice完全相同"这一模式
- `IncrementalSynthesizer`按(metric, slice) key去重claims，不做跨key的关联分析

---

### P4: Temporal Precedence Not Detected

**现象**：
session中的observations有清晰的`temporal_order`时序：
```
obs_95374ab7659f: queued_time DoD变化    (temporal_order: 0)  ← step 1
obs_204ab5e31f7a: sys_titan绝对值分布    (temporal_order: 3)  ← step 2
obs_6e051e7eaf54: 小时级排队分布         (temporal_order: 6)  ← step 3
obs_d44c93dc6b16: query_count DoD变化    (temporal_order: 35) ← step 5
obs_7f3c14f490ad: queued_time WoW变化    (temporal_order: 47) ← step 8
```

这些observations记录了分析的时序顺序（先看到排队问题，后发现查询量是原因），但TemporalPrecedenceChecker没有触发L2升级。

更重要的是，小时级聚合数据显示：
```
sys_titan 02-04时: avg_queue 1.14s→1.53s→0.15s（先升后降）
sys_titan 00-01时: avg_queue ≈ 0（排队时间极低）
```
这种时间窗口内的模式（凌晨批处理高峰→排队→高峰过后恢复）是典型的temporal precedence信号，但未被识别。

**期望**：
```
CLAIM UPGRADE: L0 → L2
JUSTIFICATION: "queued_time spikes temporally align with query volume peaks at 02-04h,
                and subside as volume decreases at 05h+"
EDGE: claim(hourly_pattern) --[temporally_precedes]--> claim(queued_time↑)
```

**根因**：
- `TemporalPrecedenceChecker`只检查同一claim内多个observations是否有`observed_window`字段，用于跨天的时间先后判断
- 不支持小时级/窗口内的temporal pattern检测
- 不支持跨claim的temporal_order比较（query_count claim vs queued_time claim的观测先后）
- `temporal_group_by_columns`在aggregate_query中使用了，但产生的时序observations没有被checker利用

---

### P5: Confounder Auto-Resolution Missing

**现象**：
synthesize_findings生成的recommendation中包含：
```json
"unresolved_confounders": [{
  "key": "normalise_workload_volume",
  "text": "check whether overall workload volume accounts for the difference"
}]
```

但session中已有 `claim_a6177ad8b32f`（query_count +30% for sys_titan, confirmed, conf=0.91）直接回答了这个confounder。系统未能将已有的confirmed claim与unresolved confounder进行匹配。

**期望**：
```json
"resolved_confounders": [{
  "key": "normalise_workload_volume",
  "resolved_by": "claim_a6177ad8b32f",
  "text": "workload volume confirmed +30% DoD for sys_titan (7.5x WoW)"
}],
"unresolved_confounders": []
```

**根因**：
- Confounder列表是基于模板硬编码生成的，不会检查session中已有的claims
- 没有confounder→claim的匹配逻辑（例如：confounder提到"workload volume"，claim的metric_name包含"query_count"）
- 这是recommendation引擎最可立竿见影的改进点——现有数据已经足够，只需要匹配逻辑

### P6: 缺少"无需行动"类型的Recommendation

**现象**：
分析目标明确包含"评估对sys_oneservice用户的影响"，session中有多个confirmed claims表明sys_oneservice不受影响：
- `claim_23e8d93894a4`: queued_time下降71.5%（confirmed, conf=0.94）
- `claim_d9f7f724977c`: 位于独立资源组global.oneservice.oneservice（confirmed）
- `claim_33af0fd6482a`: avg_queue≈0，高峰时段max仅0.18s（confirmed）

但recommendation引擎只生成了1条P1建议（针对sys_titan），没有为sys_oneservice生成"无需干预"的结论性recommendation。

**期望**：
```json
{
  "action_text": "sys_oneservice不受影响：位于独立资源组，排队时间DoD下降71.5%，高峰时段max仅0.18s。无需干预。",
  "priority": "P3",
  "type": "no_action_required"
}
```

**根因**：
- Recommendation引擎只生成"需要行动"的建议
- 没有"排除影响/确认安全"类型的recommendation模板
- 分析目标解析（goal parsing）不够精细——目标中的"评估对sys_oneservice的影响"是一个独立子问题，需要独立的结论

---

## TODO List

### Phase 1: Recommendation Engine Quick Wins (P1 — 最高 ROI)

> 这些改进不需要修改 causal checkers 或 inference level，只需更好地利用已有数据

#### 1.1 Confounder Auto-Resolution（对应 P5）
**文件**: `app/evidence_engine/synthesizers.py` 或 recommendation 生成逻辑

- [ ] synthesize_findings时，遍历recommendation的`unresolved_confounders`列表
- [ ] 对每个confounder，在session的confirmed claims中搜索匹配：
  - confounder key含"workload_volume" → 匹配metric含"query_count"的claim
  - confounder key含"seasonality" → 匹配WoW comparison的claim
- [ ] 匹配到的confounder从`unresolved`移至`resolved`，附上claim_id和摘要
- [ ] 添加单元测试：session含query_count confirmed claim时，workload confounder自动resolved

**验收标准**：
```python
# session中已有 query_count +30% confirmed claim
rec = synthesize_findings(session)
assert len(rec.causal_basis.unresolved_confounders) == 0
assert rec.causal_basis.resolved_confounders[0].resolved_by == "claim_a6177ad8b32f"
```

---

#### 1.2 Multi-Claim Recommendation Aggregation（对应 P2）
**文件**: `app/evidence_engine/synthesizers.py`

- [ ] 识别共享相同slice的confirmed claims群组（例如所有`user=sys_titan`的claims）
- [ ] 为每个群组生成一条整合recommendation，而非每个claim独立生成
- [ ] `supporting_claims`字段列出所有相关claim_id
- [ ] `action_text`整合多个metric的变化趋势

**验收标准**：
```python
# 3个confirmed claims共享 user=sys_titan slice
rec = synthesize_findings(session)
assert len(rec.supporting_claims) >= 3
assert "query_count" in rec.action_text and "queued_time" in rec.action_text
```

---

#### 1.3 "No Action Required" Recommendation Type（对应 P6）
**文件**: `app/evidence_engine/synthesizers.py`, `app/evidence_engine/schemas.py`

- [x] 新增recommendation type: `no_action_required`
- [x] 当confirmed claim显示指标正常/改善/不受影响时，生成该类型recommendation
- [x] 触发条件：metric `desired_direction` 与 delta_pct 方向对齐，或 delta_pct 绝对值<5%
- [x] Priority默认P3

**验收标准**：
```python
# sys_oneservice queued_time declined 71.5% (confirmed)
recs = synthesize_findings(session).recommendations
no_action = [r for r in recs if r.type == "no_action_required"]
assert len(no_action) >= 1
assert "sys_oneservice" in no_action[0].action_text
```

---

### Phase 2: Cross-Claim Edge Generation (P1)

#### 2.1 Scope-Based Cross-Claim Edge Discovery（对应 P1, P3）
**文件**: `app/evidence_engine/incremental_synthesizer.py`

- [ ] synthesize_findings时新增 `_create_cross_claim_edges()` 阶段
- [ ] 遍历所有confirmed claims对，计算scope overlap（slice字段交集/并集比）
- [ ] scope overlap ≥ 0.8 且 metric不同 → 创建 `correlates_with` edge
- [ ] scope完全互补（同一维度不同值，如`resource_group=others` vs `resource_group=oneservice`）→ 创建 `mechanistically_explains` edge
- [ ] Edge存入`evidence_edges`表，返回在evidence graph API中

**Edge生成规则**：
| Condition | Edge Type | Example |
|-----------|-----------|---------|
| 同一slice，不同metric，同方向变化 | `correlates_with` | query_count↑ & queued_time↑ for sys_titan |
| 同一slice，不同metric，反方向变化 | `contradicts` | 如果cpu_time↓但queued_time↑ |
| 同维度不同值，互补解释 | `mechanistically_explains` | others有排队 → oneservice无排队 |
| claim_A.metric变化可能导致claim_B.metric变化 | `justifies` | query_count↑ justifies queued_time↑ |

**验收标准**：
```python
edges = get_evidence_graph(session).edges
correlates = [e for e in edges if e.edge_type == "correlates_with"]
assert len(correlates) >= 1  # query_count ↔ queued_time
```

---

#### 2.2 Implement CrossMetricCorrelationChecker（对应 P3）
**文件**: `app/evidence_engine/causal_checkers.py`

- [ ] 新增 `CrossMetricCorrelationChecker` 注册到 `CausalCheckerRegistry`
- [ ] 输入：session中所有confirmed claims
- [ ] 检测：不同metric在同一slice的方向一致性（同升/同降）
- [ ] 输出：L0→L1升级（"cross-metric consistency"，类比现有CrossSliceConsistencyChecker的"cross-slice consistency"）
- [ ] 当≥3个不同metric在同一slice方向一致，升级为L1

**验收标准**：
```python
# query_count↑, cpu_time↑, queued_time↑ 三者方向一致 for sys_titan
checker = CrossMetricCorrelationChecker()
result = checker.check(claims, observations)
assert result.upgrade_to == "L1"
assert result.justification == "3 metrics directionally consistent for user=sys_titan"
```

---

#### 2.3 Enhance TemporalPrecedenceChecker（对应 P4）
**文件**: `app/evidence_engine/causal_checkers.py`

- [ ] 扩展支持跨claim的temporal_order比较（当前仅检查单claim内的observed_window）
- [ ] 当同一slice的两个claims，claim_A的max(temporal_order) < claim_B的min(temporal_order)，创建`temporally_precedes` edge
- [ ] 扩展支持`temporal_group_by_columns`产生的时序observations：检测"高峰→衰减"模式
- [ ] 触发L1→L2升级

**验收标准**：
```python
# query_count观测(temporal_order=35)在queued_time WoW(temporal_order=47)之前
# 但这里temporal_order反映的是step执行顺序，不是数据的时间先后
# 更好的信号是：小时级数据显示query_count高峰(02-03h) → queued_time高峰(02-03h)
edges = get_evidence_graph(session).edges
temporal = [e for e in edges if e.edge_type == "temporally_precedes"]
assert len(temporal) >= 1
```

---

### Phase 3: Recommendation Templates (P2)

#### 3.1 Template-Based Recommendation Generation
**文件**: `app/evidence_engine/recommendation_templates.py` (新建)

- [ ] 定义基于claim pattern的recommendation模板
- [ ] 支持变量插值：`{{metric}}`, `{{slice}}`, `{{delta_pct}}`, `{{baseline_value}}`, `{{current_value}}`
- [ ] 模板按(claim_type, inference_level, edge_types)索引

**模板示例**：
```python
TEMPLATES = {
    ("root_cause_candidate", "L1+", "correlates_with"): {
        "template": "{{primary_metric}}变化({{delta_pct}}%)与{{correlated_metrics}}在{{slice}}上方向一致，"
                    "建议优先排查{{primary_metric}}的上游变化",
        "priority_fn": lambda claims: "P1" if max(c.confidence for c in claims) > 0.8 else "P2"
    },
    ("no_impact", "L0+", None): {
        "template": "{{entity}}不受影响：{{isolation_reason}}。{{metric}}昨天{{current_value}}，"
                    "环比{{delta_pct}}%。无需干预。",
        "priority": "P3"
    }
}
```

---

#### 3.2 Causal Chain Narrative Generation
**文件**: `app/evidence_engine/synthesizers.py`

- [ ] 利用Phase 2产生的evidence edges，构建因果链叙述
- [ ] 从最高confidence的confirmed claim出发，沿edges遍历
- [ ] 生成`causal_chain`字段：`"query_count↑30% → others资源组并发打满 → queued_time↑58.5%"`
- [ ] 附在recommendation的`causal_basis`中

---

### Phase 4: Enhanced Observations (P2)

#### 4.1 Add Cross-Metric Observation Type
**文件**: `app/evidence_engine/schemas.py`, `app/evidence_engine/extractors/`

- [ ] 新增 `cross_metric_correlation` observation type
- [ ] 当同一step或同一session中的两个metric在同一slice方向一致时自动生成
- [ ] 包含 `correlation_direction`("both_up"/"both_down"/"divergent"), `scope_overlap`, `sample_sizes`

---

#### 4.2 Temporal Pattern Observation Type
**文件**: `app/evidence_engine/extractors/`

- [ ] 新增 `temporal_pattern` observation type
- [ ] 当`temporal_group_by_columns`产生的时序数据显示明显的"尖峰→回落"或"持续升高"模式时自动生成
- [ ] 包含 `pattern_type`("spike_and_decay"/"sustained_increase"/"sustained_decrease"), `peak_window`, `magnitude`
- [ ] 为TemporalPrecedenceChecker提供更好的输入信号

---

### Phase 5: API & UX Improvements (P2)

#### 5.1 Add Session Debug Endpoint
**文件**: `app/api/sessions.py`

- [ ] 新增 `GET /sessions/{id}/debug` endpoint
- [ ] 返回每个causal checker的执行日志：哪些被触发、检查了哪些claims、为何未升级
- [ ] 返回edge生成决策路径：哪些claim pairs被检查、scope overlap值、为何未创建edge
- [ ] 帮助用户理解为何所有claims停留在L0

**示例输出**：
```json
{
  "checker_logs": [
    {
      "checker": "CrossSliceConsistencyChecker",
      "claims_checked": 3,
      "result": "no_upgrade",
      "reason": "only 1 slice per metric (need ≥2 for cross-slice consistency)"
    },
    {
      "checker": "TemporalPrecedenceChecker",
      "claims_checked": 16,
      "result": "no_upgrade",
      "reason": "all observations within same observed_window (2026-03-24)"
    }
  ]
}
```

---

#### 5.2 Enhance Evidence Graph API
**文件**: `app/api/sessions.py`

- [ ] `GET /sessions/{id}/evidence` 增加`edge_types`过滤参数
- [ ] 增加`claims_only=confirmed`过滤参数（默认返回全部，减少噪音）
- [ ] 增加`include_debug=true`参数，附上checker决策日志

---

## Success Metrics

### Quantitative（基于 sess_75f337c1c684 基线）

| Metric | Current (基线) | Phase 1 Target | Phase 2+ Target |
|--------|---------------|----------------|-----------------|
| Evidence edges per session | 50 (49 supports + 1 justifies) | 50+ (不变) | 55+ (新增 5+ 推理 edges) |
| 推理类 edges (非supports) | 1 (justifies only) | 1 (不变) | ≥5 (correlates_with, mechanistically_explains, temporally_precedes) |
| Claims with L1+ inference_level | 0/16 (0%) | 0% (不变) | ≥30% (5/16) |
| Recommendations count | 1 | ≥2 (含no_action_required) | ≥3 (多claim聚合) |
| Recommendations with multi-claim support | 0/1 (0%) | ≥1 (50%) | ≥2 (80%) |
| Unresolved confounders（可auto-resolve的） | 1/1 (100%) | 0/1 (0%) | 0 |
| Recommendation actionability（人工评分1-5） | 2 ("drill into X") | 4 (含具体数值+建议) | 5 (含因果链+验证方案) |

### Qualitative

- [ ] Recommendations整合多个confirmed claims，包含具体数值（"+30% DoD"，"凌晨02-04时高峰"）
- [ ] 已回答的confounders自动标记为resolved，不再误导用户
- [ ] 分析目标的子问题（"影响评估"）有对应的结论性recommendation
- [ ] Evidence graph包含跨metric的correlates_with edge，可视化推理路径
- [ ] Session debug endpoint帮助用户理解inference level为何未升级

---

## Implementation Priority

Phase 1 先行——不需要动 causal checkers / inference level 机制，只改 recommendation 生成逻辑，ROI 最高。

```
Phase 1 (Recommendation Quick Wins — 最高ROI):
├── 1.1 Confounder Auto-Resolution ──────────── Week 1     (P5)
├── 1.2 Multi-Claim Recommendation Aggregation ── Week 1-2   (P2 部分)
├── 1.3 No-Action-Required Recommendation Type ── Week 2     (P6)
│
Phase 2 (Cross-Claim Edge Generation):
├── 2.1 Scope-Based Cross-Claim Edge Discovery ── Week 3-4   (P1, P3)
├── 2.2 CrossMetricCorrelationChecker ─────────── Week 4-5   (P3)
├── 2.3 Enhanced TemporalPrecedenceChecker ────── Week 5     (P4)
│
Phase 3 (Recommendation Templates):
├── 3.1 Template-Based Recommendation Generation ─ Week 6    (P2 完整)
├── 3.2 Causal Chain Narrative Generation ────────── Week 6-7
│
Phase 4 (Enhanced Observations):
├── 4.1 Cross-Metric Observation Type ─────────── Week 7
├── 4.2 Temporal Pattern Observation Type ────── Week 7-8   (P4 增强)
│
Phase 5 (API & UX):
├── 5.1 Session Debug Endpoint ────────────────── Week 8
├── 5.2 Enhanced Evidence Graph API ───────────── Week 8
```

### Phase依赖关系
```
Phase 1 (独立，无依赖) ─────────────────────────────────┐
                                                         ├→ Phase 3 (依赖 Phase 1 + 2)
Phase 2 (独立，无依赖) ─────────────────────────────────┘
Phase 4 (独立，但为 Phase 2 提供更好输入)
Phase 5 (独立，可随时开始)
```

---

## References

### Related Code

- `app/evidence_engine/incremental_synthesizer.py` — 主synthesizer，claim聚合/升级/edge创建的核心
- `app/evidence_engine/causal_checkers.py` — CausalCheckerRegistry + 4个checker (CrossSlice, TemporalPrecedence, DoseResponse, Reversal)
- `app/evidence_engine/scoring.py` — confidence scoring（effect_strength, consistency, sample_score等）
- `app/evidence_engine/readiness.py` — readiness信号计算
- `app/evidence_engine/extractors/` — observation提取器（comparison, aggregate, funnel, anomaly, contribution_shift）
- `app/evidence_engine/schemas.py` — observation/claim/edge/recommendation数据结构
- `app/analysis_core/step_runners/synthesis.py` — synthesize_findings step runner
- `app/analysis_core/step_runners/attribution.py` — attribute_change step runner
- `app/service.py` — SemanticLayerService.run_step()，调用evidence engine的入口

### Related Tests

- `tests/test_incremental_synthesis.py` — 增量synthesis e2e (5 tests)
- `tests/test_causal_integration.py` — 因果推理集成: L0→L2升级链, evidence graph API (17 tests)
- `tests/test_evidence.py` — Observation factories, claim synthesis, confidence scoring (26 tests)
- `tests/test_evidence_plugins.py` — Evidence engine plugin tests (3 tests)

### Design Docs

- `CLAUDE.md` — Evidence packaging章节（core design concept）
- `app/evidence_engine/` — Evidence engine代码即文档

### Baseline Session

- Session ID: `sess_75f337c1c684`
- 可通过 `GET /sessions/sess_75f337c1c684/evidence` 查看完整evidence graph
- 用作所有改进的验收基线

---

## Appendix: Target State Evidence Graph

> 基于 sess_75f337c1c684 的实际claims，展示改进后的预期输出

```json
{
  "session_id": "sess_75f337c1c684",
  "claims": [
    {
      "claim_id": "claim_a6177ad8b32f",
      "text": "query_count increased 30.0% DoD (7.5x WoW) for user=sys_titan",
      "inference_level": "L1",
      "status": "confirmed",
      "confidence": 0.91,
      "note": "L0→L1: CrossMetricCorrelationChecker detected 3 metrics directionally consistent"
    },
    {
      "claim_id": "claim_6d4dbfa0c098",
      "text": "query_queued_time increased 58.5% DoD for user=sys_titan",
      "inference_level": "L2",
      "status": "confirmed",
      "confidence": 0.94,
      "note": "L1→L2: TemporalPrecedenceChecker detected hourly peak alignment (02-04h)"
    },
    {
      "claim_id": "claim_42a64d44c2ca",
      "text": "sys_titan routed to global.oneservice.others (avg_queue=0.71s)",
      "inference_level": "L1",
      "status": "confirmed",
      "confidence": 0.61
    },
    {
      "claim_id": "claim_d9f7f724977c",
      "text": "sys_oneservice routed to global.oneservice.oneservice (avg_queue≈0)",
      "inference_level": "L1",
      "status": "confirmed",
      "confidence": 0.61
    },
    {
      "claim_id": "claim_23e8d93894a4",
      "text": "query_queued_time declined 71.5% for user=sys_oneservice",
      "inference_level": "L0",
      "status": "confirmed",
      "confidence": 0.94
    }
  ],
  "evidence_edges": [
    {
      "from_id": "claim_a6177ad8b32f",
      "to_id": "claim_6d4dbfa0c098",
      "edge_type": "correlates_with",
      "weight": 0.92,
      "evidence": {
        "scope_overlap": 1.0,
        "direction": "both_up",
        "shared_slice": {"cluster": "k8soneservice-oneservice", "user": "sys_titan"}
      }
    },
    {
      "from_id": "claim_680fb460e5a0",
      "to_id": "claim_6d4dbfa0c098",
      "edge_type": "correlates_with",
      "weight": 0.78,
      "evidence": {
        "scope_overlap": 1.0,
        "direction": "both_up",
        "note": "cpu_time↑8.6% + queued_time↑58.5% for same user"
      }
    },
    {
      "from_id": "claim_42a64d44c2ca",
      "to_id": "claim_d9f7f724977c",
      "edge_type": "mechanistically_explains",
      "weight": 0.88,
      "evidence": {
        "mechanism": "resource_group_isolation",
        "note": "sys_titan(others) and sys_oneservice(oneservice) are in separate resource groups"
      }
    },
    {
      "from_id": "obs_0f61be7a13e0",
      "to_id": "obs_43e28c50be18",
      "edge_type": "temporally_precedes",
      "weight": 0.85,
      "evidence": {
        "pattern": "spike_02h→peak_03h→decay_04h",
        "peak_window": "02:00-04:00"
      }
    }
  ],
  "recommendations": [
    {
      "rec_id": "rec_001",
      "type": "action_required",
      "priority": "P1",
      "action_text": "sys_titan查询量DoD增长30%（WoW增长7.5倍），集中在凌晨02-04时批处理高峰，打满global.oneservice.others资源组配额。建议：(1) 排查sys_titan凌晨批量任务增长原因 (2) 评估others资源组配额扩容或为sys_titan分配独立资源组",
      "supporting_claims": ["claim_a6177ad8b32f", "claim_6d4dbfa0c098", "claim_42a64d44c2ca"],
      "causal_basis": {
        "inference_level": "L2",
        "causal_chain": "query_count↑30%(DoD)/7.5x(WoW) → others资源组并发打满(02-04h) → queued_time↑58.5%",
        "resolved_confounders": [{
          "key": "normalise_workload_volume",
          "resolved_by": "claim_a6177ad8b32f",
          "text": "workload volume confirmed +30% DoD, +7.5x WoW for sys_titan"
        }],
        "unresolved_confounders": []
      }
    },
    {
      "rec_id": "rec_002",
      "type": "no_action_required",
      "priority": "P3",
      "action_text": "sys_oneservice不受影响：位于独立资源组global.oneservice.oneservice，昨天avg排队0.0004s，高峰时段(02-04h)max仅0.18s，DoD排队时间下降71.5%。无需干预。",
      "supporting_claims": ["claim_23e8d93894a4", "claim_d9f7f724977c", "claim_33af0fd6482a"]
    }
  ]
}
```