# validate 派生意图 Schema

本文档定义 `validate` 派生意图的拟议类型契约。

状态：draft design。本文是规划中的 `validate` 派生意图 Schema 提案，不表示对应 HTTP endpoint 已经实现。

## 目的

`validate` 用于把”定义两个待比较总体””准备可检验观测””执行假设检验”固化成一次稳定分析动作。

它回答一个固定问题：

- 某个已明确的差异假设是否有足够统计证据支持

典型场景：

- 新策略是否真的提升了留存
- 某段流量的转化率差异是否显著
- 某次改版前后数值指标差异是否可能只是噪音

设计目标：

- 让调用方直接请求”验证这个怀疑”，而不是手工拼装 `observe -> observe -> test`
- 把样本准备与检验从 ad hoc workflow 中抽离成稳定类型契约（typed contract）
- 复用 `observe` 与 `test` 既有语义，不重定义推断摘要（inferential summary）或检验语义
- 让结果在有界输出（bounded output）下仍保留主要推断语义（inferential semantics）

## 核心设计决策

`validate` 是派生意图（derived intent），不是开放式推断工作流（inferential workflow）。

v1 明确约束：

- 只围绕单个 metric 的两个显式给定总体展开
- `left` 与 `right` 都必须由调用方显式提供，不自动推导基线（baseline）
- 只支持差异假设族（`difference` hypothesis family）
- 内部只创建两个推断就绪（inferential-ready）`observe` 和一个 `test`
- `sample_kind = “auto”` 只能在系统能唯一确定推断摘要模式（inferential summary mode）时合法
- 不输出因果结论、业务建议或自由文本解释作为证据主体

## 请求形状（Request Shape）

```json
{
  "intent": "validate",
  "metric": "conversion_rate",
  "left": {
    "time_scope": {
      "kind": "range",
      "start": "2024-03-01T00:00:00",
      "end": "2024-04-01T00:00:00"
    },
    "scope": {
      "predicate": {
        "field": "experiment_group",
        "op": "eq",
        "value": "treatment"
      }
    }
  },
  "right": {
    "time_scope": {
      "kind": "range",
      "start": "2024-03-01T00:00:00",
      "end": "2024-04-01T00:00:00"
    },
    "scope": {
      "predicate": {
        "field": "experiment_group",
        "op": "eq",
        "value": "control"
      }
    }
  },
  "sample_kind": "auto",
  "hypothesis": {
    "family": "difference",
    "alternative": "greater",
    "alpha": 0.05,
    "label": "treatment converts better than control"
  },
  "method": "auto"
}
```

## Typed Schema

```ts
type ValidateRequest = {
  intent: "validate";
  metric: string;
  left: ValidateObservationInput;
  right: ValidateObservationInput;
  sample_kind?: "auto" | "numeric" | "rate" | null;
  hypothesis?: ValidateHypothesis | null;
  method?: "auto" | "welch_t" | "two_proportion_z" | null;
};

type ValidateObservationInput = {
  time_scope: TimeScope;
  scope?: Scope | null;
};

type ValidateHypothesis = {
  family?: "difference";
  alternative?: "two_sided" | "greater" | "less";
  alpha?: number;
  label?: string | null;
};

type TimeScope =
  | { kind: "range"; start: string; end: string }
  | { kind: "snapshot_now" }
  | { kind: "latest_available" }
  | { kind: "as_of"; at: string };

type Predicate =
  | { op: "and"; items: Predicate[] }
  | { op: "or"; items: Predicate[] }
  | {
      field: string;
      op:
        | "eq"
        | "neq"
        | "in"
        | "not_in"
        | "gt"
        | "gte"
        | "lt"
        | "lte"
        | "between"
        | "is_null"
        | "is_not_null";
      value?: string | number | boolean | string[] | number[];
    };

type Scope = {
  constraints?: Record<string, string | number | boolean | null> | null;
  predicate?: Predicate | null;
};
```

## 输入规则

v1 支持的输入形态如下：

- `metric` 必须解析到已发布的 semantic metric
- `left` 与 `right` 都必须能确定性展开为 `observe(..., granularity = null, dimensions = null)` 的 inferential-ready scalar observation
- `sample_kind` 省略时默认为 `auto`
- `hypothesis.family` 省略时默认为 `difference`
- `hypothesis.alternative` 省略时默认为 `two_sided`
- `hypothesis.alpha` 省略时默认为 `0.05`
- `method` 省略时默认为 `auto`
- `method` 的合法取值与兼容性完全继承 `test`

输出类型：`validation_bundle`

## v1 不支持的输入

- 只给一侧 scope 再让系统自动推导另一侧
- raw sample arrays
- 把 `compare` / `decompose` / `detect` 输出当作统计样本
- 多个 metric 的联合检验
- `difference` 之外的 hypothesis families
- paired tests
- repeated-measures tests
- multi-arm tests
- equivalence、non-inferiority 或 covariate-adjusted tests
- 任何因果、建议、动作优先级类输出契约

推荐错误码：`INVALID_ARGUMENT`。

## 字段语义

### metric

要被验证的单个 semantic metric。

`validate` 围绕一个已经被业务方明确提出的怀疑展开，不负责发现候选问题。

默认要求左右两侧属于同一 metric；若语义层显式声明某个 metric family 可 cross-group comparable，则可沿用 `test` 的兼容性契约。

### left / right

`left` 与 `right` 定义参与检验的两个总体 scope。

它们继承 `test` 的顺序语义：

- `left` 是主要被考察总体
- `right` 是比较总体
- 最终 estimate sign 按 `left - right` 定义

`validate` 不要求两侧一定是时间上的 current/baseline；只要两侧 scope 可比较，就可以是：

- 两个时间窗口
- 同一时间窗口下的两个实验组
- 同一时间窗口下的两个流量或策略组

其中 `time_scope` 是唯一时间窗口契约；非时间总体约束统一通过 `scope` 表达。

### sample_kind

控制内部 `observe` 应准备哪一类 inferential summary。

v1 支持：

- `auto`
- `numeric`
- `rate`

确定性推导规则：

- `numeric` -> 两个内部 `observe.result_mode = "numeric_sample_summary"`
- `rate` -> 两个内部 `observe.result_mode = "rate_sample_summary"`
- `auto` -> 必须能由 metric capability 唯一确定一种 inferential summary mode

若 `auto` 下同时存在多个合法 inferential-ready mode，则必须直接失败，而不是静默猜测。

### hypothesis

`validate` 复用 `test` 的 hypothesis contract。

v1 仅支持：

- `family = "difference"`

`alternative` 语义：

- `two_sided`：左右不同
- `greater`：左侧大于右侧
- `less`：左侧小于右侧

### method

`validate` 保留显式 `method` 字段，但不引入新的派生层方法体系。

v1 每次请求只支持一种方法：

- `welch_t`
- `two_proportion_z`
- `auto`

`auto` 的确定性规则继承 `test`：

- `numeric_sample_summary -> welch_t`
- `rate_sample_summary -> two_proportion_z`

## 展开契约

给定同一请求与同一系统状态，`validate` 必须展开成同一条逻辑 DAG。

固定展开如下：

1. 根据 `sample_kind` 确定内部 inferential summary mode
2. `observe(metric, left.time_scope, left.scope, result_mode = inferred_mode, granularity = null, dimensions = null)`
3. `observe(metric, right.time_scope, right.scope, result_mode = inferred_mode, granularity = null, dimensions = null)`
4. `test(left_ref, right_ref, hypothesis, method)`

其中：

- 两个 `observe` 必须使用同一个 inferential summary mode
- `left` / `right` 的展开顺序必须继承请求顺序
- `method = "auto"` 时，最终方法选择完全继承 `test`
- `validate` 不得内部创建 `compare`、`decompose`、`detect` 或任何 planner-style follow-up 分支

## 校验规则

`validate` 校验分为三层。

### 1. 请求校验

以下情况应直接失败：

- `metric` 不存在
- `left` 或 `right` 缺少合法 `time_scope`
- `left.scope` 或 `right.scope` 含非法字段或非法时间条件
- `sample_kind` 不是合法枚举
- `hypothesis.family` 不是 `difference`
- `hypothesis.alpha != null && (hypothesis.alpha <= 0 || hypothesis.alpha >= 1)`
- `method` 不是合法枚举

### 2. 展开校验

以下情况应直接失败，而不是退化为 planner 行为：

- metric 不支持所请求的 inferential-ready observation mode
- `sample_kind = "auto"` 时无法唯一确定 inferential summary mode
- `left` 或 `right` 无法被确定性归一化为 inferential-ready scalar observe 请求
- 内部需要额外推导 baseline、候选样本或其他未在契约中声明的执行分支

### 3. 原子兼容性校验

`validate` 不得绕过原子意图的校验规则。

至少要保证：

- 两个内部 `observe` 都能成功产出完整 inferential-ready artifact
- 两边 observation type 必须一致
- 内部 `test` 所请求的方法与 observation type 兼容
- 两边输入满足 `test` 的 comparability、completeness 与 summary-statistics 校验

若检验只达到 `needs_attention`，可带 issue 成功返回，但不得静默替换 metric、scope、sample_kind 或 method。

## Response Shape

```ts
type ValidateResponse = {
  result_type: "validation_bundle";
  artifact_id: string;
  artifact_schema_version: string;
  derivation_version: string;
  metric: string;
  left: ValidateResolvedSide;
  right: ValidateResolvedSide;
  sample_kind: "numeric" | "rate";
  hypothesis: {
    family: "difference";
    alternative: "two_sided" | "greater" | "less";
    alpha: number;
    label: string | null;
  };
  method: "welch_t" | "two_proportion_z";
  validation: ValidateValidation;
  provenance: ValidateProvenance;
  refs: {
    left_observation_ref: ObserveInferentialArtifactRef | null;
    right_observation_ref: ObserveInferentialArtifactRef | null;
    test_ref: TestArtifactRef | null;
  };
  result: ValidateInferenceResult | null;
};

type ObserveInferentialArtifactRef = {
  step_type: "observe";
  session_id: string;
  step_id: string;
  artifact_id: string;
  observation_type:
    | "numeric_sample_summary"
    | "rate_sample_summary";
};

type TestArtifactRef = {
  step_type: "test";
  session_id: string;
  step_id: string;
  artifact_id: string;
  result_type: "hypothesis_test";
};

type ValidateProvenance = {
  session_id: string;
  source_observation_refs: [
    ObserveInferentialArtifactRef | null,
    ObserveInferentialArtifactRef | null
  ];
  source_test_ref: TestArtifactRef | null;
  intent_contract_version: "validate.v1";
  derived_logic_version: string;
};

type ValidateResolvedSide = {
  time_scope: ResolvedTimeScope | null;
  scope: Scope | null;
};

type ResolvedTimeScope =
  | { kind: "range"; start: string; end: string }
  | { kind: "snapshot_now"; observed_at: string }
  | { kind: "latest_available"; data_as_of: string }
  | { kind: "as_of"; at: string };

type ValidateIssue = {
  code:
    | "observe_failed"
    | "observation_type_mismatch"
    | "metric_mismatch"
    | "test_needs_attention"
    | "test_invalid"
    | "sample_kind_ambiguous";
  severity: "error" | "warning";
  message: string;
};

type ValidateValidation = {
  status: "validated" | "needs_attention";
  issues: ValidateIssue[];
};

type ValidateInferenceResult = {
  decision: "reject_null" | "fail_to_reject" | "undetermined";
  p_value: number | null;
  estimate: {
    estimand: "mean_diff" | "rate_diff";
    value: number | null;
    confidence_interval: {
      level: number;
      lower: number | null;
      upper: number | null;
    } | null;
  } | null;
};
```

## 响应语义

`validate` 的最终语义承接内部 `test` 结果，而不是重新发明一套检验解释。

因此：

- `result.decision` 是对 `test.decision.reject_null` 的稳定包装
- `result.estimate` 的 estimand、value 与 confidence interval 继承 `test`
- `validate` 不把统计显著性提升为因果性、业务重要性或上线建议

若 `test` 无法产出完整 inferential result，则 `result.decision = "undetermined"` 或 `result = null`，并通过 `validation.issues` 披露原因。

## Projection Policy

`validate` 的 projection 必须保持确定性压缩。

允许：

- 用顶层 `result` 摘要承接最关键的 inferential semantics
- 把内部两个 `observe` refs 与一个 `test` ref 压缩到顶层 `refs`
- 摘要展示 `p_value`、estimate 与 confidence interval

不得：

- 根据中间结果临时改用另一种 inferential summary 或检验方法
- 隐藏 sample ambiguity、sample insufficiency 或 data completeness issues
- 把统计结果改写成因果结论、业务解释或建议
- 发明工件中不存在的新 claim

## 例子

### 例 1：验证实验组转化率是否更高

请求：

- `metric = "conversion_rate"`
- `left.scope.predicate = experiment_group = treatment`
- `right.scope.predicate = experiment_group = control`
- `sample_kind = auto`
- `hypothesis.alternative = greater`

含义：

- 让系统准备 treatment 与 control 的 rate sample summary
- 再评估 treatment 转化率是否显著高于 control

### 例 2：验证改版前后客单价均值是否不同

请求：

- `metric = "avg_order_value"`
- `left.time_scope = 改版后窗口`
- `right.time_scope = 改版前窗口`
- `sample_kind = numeric`
- `hypothesis.alternative = two_sided`
- `method = welch_t`

含义：

- 先准备两个窗口下的 numeric sample summary
- 再检验两侧均值差异是否显著

### 例 3：`sample_kind = auto` 的歧义失败

请求：

- `metric` 同时支持 `numeric_sample_summary` 与 `rate_sample_summary`
- `sample_kind = auto`

含义：

- `validate` 无法唯一确定应准备哪种 inferential summary
- 系统必须直接返回请求歧义错误，而不是静默挑一种方法继续执行

## v1 Scope Limits

- 只支持单个 metric 的双总体差异检验
- 只支持调用方显式给定 `left` / `right` scope
- 只支持 `difference` hypothesis family
- 只支持由 `observe` 产出的 inferential-ready summary 作为检验输入
- 不支持 baseline 自动推导、paired / multi-arm / equivalence / non-inferiority
- 不支持因果解释、业务建议或开放式 follow-up
