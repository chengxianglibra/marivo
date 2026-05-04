# 评估判断策略

本文档定义 Marivo 各 `assessment_type` 的最小判断门槛补充。

状态：draft design。本文只保留各 `assessment_type` 的 family-specific judgment threshold；评估单元、固定顺序、gap 规则、共享 judgment baseline 与 registry 角色，统一以 [`evidence-engine/inference-and-gap-engine.md`](../inference-and-gap-engine.md) 为准。directional evidence aggregation 与最终 status 决议，则以 [`support-oppose-and-status-resolution.md`](../support-oppose-and-status-resolution.md) 的可执行 contract 为准。

## 目的

[`evidence-engine/inference-and-gap-engine.md`](../inference-and-gap-engine.md) 已固定 judgment layer 的主线主题契约。

本文只补足：

- 各 `assessment_type` 什么算实质支持
- 各 `assessment_type` 什么算实质反驳
- 各 `assessment_type` 的 `mixed` / `insufficient` 最低进入门槛

本文不重复定义：

- evaluation unit 与 fixed evaluation order
- gap reopen、snapshot policy 与 rule family boundary
- `mixed` / `insufficient` / blocking gap 的共享基线
- registry 如何解引用 `rule_id -> rule_family -> assessment_type`

## Threshold Sketch

```ts
type AssessmentJudgmentThreshold = {
  assessment_type:
    | "change_assessment"
    | "decomposition_assessment"
    | "anomaly_assessment"
    | "correlation_assessment"
    | "test_hypothesis_assessment"
    | "forecast_assessment";
  support_requirement_tokens: string[];
  oppose_requirement_tokens: string[];
  mixed_resolution_policy:
    | "prefer_mixed_on_structured_conflict"
    | "prefer_insufficient_when_conflict_not_substantive";
  insufficient_fallback_policy:
    | "fallback_when_no_direction_meets_threshold"
    | "fallback_when_gate_blocks_and_threshold_not_met";
};
```

## Minimum Policy By Assessment Type

### `change_assessment`

- 实质支持：变化方向、量级和可比性同时成立
- 实质反驳：方向被反向证据推翻，或变化量不足以支撑命题
- `mixed`：存在同粒度、同主体的强反向变化证据
- `insufficient`：缺可比性、缺覆盖、或变化幅度尚不足以稳定解释

### `decomposition_assessment`

- 实质支持：主要贡献项覆盖足够大比例且残差可接受
- 实质反驳：主要贡献项与命题宣称的解释方向不一致
- `mixed`：多组高权重贡献项相互竞争且无法收敛为单一解释
- `insufficient`：覆盖不足、残差过高或分解前提不成立

### `anomaly_assessment`

- 实质支持：异常候选具备稳定基线偏离，且不是明显质量噪声
- 实质反驳：候选可被基线波动、节律或质量问题充分解释
- `mixed`：异常信号存在，但反证同样强，无法排除非异常解释
- `insufficient`：历史不足、样本不足或质量门槛未满足

### `correlation_assessment`

- 实质支持：相关方向、强度与对齐方式满足既定阈值
- 实质反驳：相关性弱、方向相反或时间对齐失败
- `mixed`：不同窗口或不同切分下给出稳定冲突信号
- `insufficient`：配对数不足、混杂因素暴露过强或可比性不成立

### `test_hypothesis_assessment`

- 实质支持：检验显著性与效应量同时达到门槛
- 实质反驳：结果显著支持反方向，或效应量明确不支持命题
- `mixed`：不同检验或不同样本切分下结论稳定冲突
- `insufficient`：样本不足、摘要质量不达标或方法前提不满足

### `forecast_assessment`

- 实质支持：预测方向稳定，区间可靠度满足门槛
- 实质反驳：预测结果与命题宣称方向相反，或不确定性过高
- `mixed`：不同预测期 / 模型护栏下出现稳定冲突
- `insufficient`：历史长度不足、区间过宽或基线不稳定

## Non-goals

- 不定义 rule family 的固定执行顺序
- 不定义 gap lifecycle、snapshot policy 或 registry contract
- 不定义状态/上下文的读取契约
- 不替代 [`support-oppose-and-status-resolution.md`](../support-oppose-and-status-resolution.md) 中的 executable resolution contract
