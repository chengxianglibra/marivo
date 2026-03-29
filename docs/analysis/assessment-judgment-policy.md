# 评估判断策略

本文档定义 Factum 各 `assessment_type` 的最小判断策略。

状态：draft design。本文是 `docs/analysis/` 下的规范判断策略设计提案，不表示具体规则实现已经存在。

## 目的

`inference-rule-engine-contract.md` 负责定义引擎如何运行，但不应把各类评估的判断门槛留给实现层自行决定。

本文补足：

- 什么算实质支持
- 什么算实质反驳
- 何时进入 `mixed`
- 何时必须保守回到 `insufficient`

## 通用原则

- 判断策略必须是确定性的类型契约，不得由自由文本或临时启发式决定
- `mixed` 表示存在结构化对立证据，不等价于低置信度
- `insufficient` 表示已评估但当前不足以形成更强判断
- 阻塞性缺口可以和 `supported` / `contradicted` 并存，但不能掩盖缺失条件

## Typed Design Sketch

```ts
type AssessmentJudgmentThreshold = {
  assessment_type:
    | "change_assessment"
    | "decomposition_assessment"
    | "anomaly_assessment"
    | "correlation_assessment"
    | "test_hypothesis_assessment"
    | "forecast_assessment";
  support_threshold: string;
  oppose_threshold: string;
  mixed_resolution_policy: string;
  insufficient_fallback_policy: string;
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

- 不定义每条规则的实现细节
- 不定义规则注册表
- 不定义状态/上下文的读取契约
