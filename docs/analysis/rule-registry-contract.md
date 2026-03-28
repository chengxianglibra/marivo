# Rule Registry Contract

本文档定义 Factum inference rule registry 的拟议 contract。

状态：draft design。本文是 `docs/analysis/` 下的 canonical rule metadata 设计提案，不表示对应存储、加载方式或 HTTP endpoint 已经存在。

## 目的

`InferenceRecord` 只持有 `rule_id`，但 state/context surface、replay 与兼容检查需要稳定知道：

- 该 `rule_id` 属于哪个 `rule_family`
- 它服务于哪个 `assessment_type`
- 当前适用的 `rule_version` 与 registry 版本是什么

因此 v1 需要独立的 rule registry contract，避免各读取面或实现层各自维护一套 `rule_id` 解释逻辑。

## 设计决策

### 1. registry 是 canonical metadata contract，不是执行日志

registry 负责定义稳定规则元数据，不记录运行时命中次数、耗时、最近执行结果等 telemetry。

### 2. registry 只解决解引用，不承载 judgment policy

registry 回答的是“这条 rule 是谁、属于哪一个 `rule_family`、服务哪个 `assessment_type`”。

它不回答：

- support / oppose 的实质门槛
- 何时进入 `mixed`
- 何时必须降级为 `insufficient`

这些属于 [`assessment-judgment-policy.md`](assessment-judgment-policy.md)。

### 3. family 归属必须显式声明

不允许通过 `rule_id` 字符串前缀、目录路径或实现类名隐式推断 `rule_family`。

## Typed Design Sketch

```ts
type RuleRegistryEntry = {
  rule_id: string;
  rule_family:
    | "precondition_gate"
    | "quality_gate"
    | "comparability_gate"
    | "support_evidence"
    | "oppose_evidence"
    | "status_resolution"
    | "gap_management"
    | "confidence_shaping"
    | "assessment_transition";
  assessment_type:
    | "change_assessment"
    | "decomposition_assessment"
    | "anomaly_assessment"
    | "correlation_assessment"
    | "test_hypothesis_assessment"
    | "forecast_assessment";
  rule_version: string;
  registry_version: string;
  rule_cluster: string | null;
  status: "active" | "deprecated";
};
```

## Consumption Contract

- `InferenceRecord.rule_id` 必须能完全解引用到唯一 `RuleRegistryEntry`
- state surface 汇总 “命中的规则族” 时，必须以 registry 为准
- context surface 做 rule grouping 时，必须以 registry 为准
- replay / compatibility 检查时，必须显式比较 `rule_version` 与 `registry_version`

## Non-goals

- 不定义规则实现代码的类名、模块路径或加载方式
- 不定义 HTTP 暴露形状
- 不定义 judgment policy 或具体判断门槛
