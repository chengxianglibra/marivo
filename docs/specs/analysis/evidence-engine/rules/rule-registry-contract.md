# 规则注册表契约

本文档定义 Marivo 推断规则注册表（inference rule registry）的元数据契约补充。

状态：draft design。本文只保留 registry 自身的 metadata shape 与 consumption contract；rule family 的固定顺序、主题边界与 registry 在 inference 主线中的角色，统一以 [`evidence-engine/inference-and-gap-engine.md`](../inference-and-gap-engine.md) 为准。

## 目的

`InferenceRecord` 只持有 `rule_id`，但状态面/上下文面、重放与兼容检查仍需要稳定知道：

- 该 `rule_id` 属于哪个规则族
- 它服务于哪个 `assessment_type`
- 当前适用的规则版本与注册表版本是什么

因此仍需要独立的 registry metadata contract，避免各读取面或实现层各自维护一套 `rule_id` 解释逻辑。

## 设计边界

- 注册表是规范元数据契约，不是执行日志
- 注册表只解决稳定解引用，不承载 judgment threshold
- 规则族归属必须显式声明，不得由字符串前缀、目录路径或实现类名隐式推断

更细的 judgment threshold 仍以下游 [`assessment-judgment-policy.md`](assessment-judgment-policy.md) 为准。

## Typed Design Sketch

```ts
type RuleRegistryEntry = {
  rule_id: string;
  rule_family:
    | “precondition_gate”
    | “quality_gate”
    | “comparability_gate”
    | “support_evidence”
    | “oppose_evidence”
    | “status_resolution”
    | “gap_management”
    | “confidence_shaping”
    | “assessment_transition”;
  assessment_type:
    | “change_assessment”
    | “decomposition_assessment”
    | “anomaly_assessment”
    | “correlation_assessment”
    | “test_hypothesis_assessment”
    | “forecast_assessment”;
  rule_version: string;
  registry_version: string;
  rule_cluster: string | null;
  status: “active” | “deprecated”;
};
```

## Consumption Contract

- `InferenceRecord.rule_id` 必须能完全解引用到唯一 `RuleRegistryEntry`
- 状态面汇总”命中的规则族”时，必须以注册表为准
- 上下文面做规则分组时，必须以注册表为准
- 重放 / 兼容检查时，必须显式比较规则版本与注册表版本

## Non-goals

- 不定义规则实现代码的类名、模块路径或加载方式
- 不定义 HTTP 暴露形状
- 不定义判断策略或具体判断门槛
