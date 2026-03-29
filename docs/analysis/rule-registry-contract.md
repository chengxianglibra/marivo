# 规则注册表契约

本文档定义 Factum 推断规则注册表（inference rule registry）的拟议契约。

状态：draft design。本文是 `docs/analysis/` 下的规范规则元数据设计提案，不表示对应存储、加载方式或 HTTP endpoint 已经存在。

## 目的

`InferenceRecord` 只持有 `rule_id`，但状态面/上下文面、重放与兼容检查需要稳定知道：

- 该 `rule_id` 属于哪个规则族
- 它服务于哪个 `assessment_type`
- 当前适用的规则版本与注册表版本是什么

因此 v1 需要独立的规则注册表契约，避免各读取面或实现层各自维护一套 `rule_id` 解释逻辑。

## 设计决策

### 1. 注册表是规范元数据契约，不是执行日志

注册表负责定义稳定规则元数据，不记录运行时命中次数、耗时、最近执行结果等遥测数据。

### 2. 注册表只解决解引用，不承载判断策略

注册表回答的是”这条规则是谁、属于哪一个规则族、服务哪个 `assessment_type`”。

它不回答：

- 支持 / 反驳的实质门槛
- 何时进入 `mixed`
- 何时必须降级为 `insufficient`

这些属于 [`assessment-judgment-policy.md`](assessment-judgment-policy.md)。

### 3. 族归属必须显式声明

不允许通过 `rule_id` 字符串前缀、目录路径或实现类名隐式推断规则族。

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
