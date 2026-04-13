# Factum Agent 分析链路缺陷清单（基于 2026-04-13 实测）

## 1. 目的

本文记录一次真实 agent 分析任务中暴露出来的 Factum `service / MCP / skill` 问题，并给出尽量具体的可重现步骤。

分析任务约束如下：

- 仅允许使用 `factum` skill
- 仅允许通过 Factum MCP 调用 Factum service
- 不允许通过 Factum 之外的方式做数据分析

本次实际分析目标：

- 分析 `2026-04-09`（上周四）`trino k8soneservice-oneservice` 集群整体情况
- 判断是否存在风险

本次实际使用到的 session：

- `sess_cfdd004c2db3`

说明：

- 下文优先写成“现在还可复现”的步骤
- 若某个问题与当次具体数据窗口强相关，会同时保留本次实测参数

---

## 2. 总结

本次分析可以完成，但 agent 使用体验存在明显短板：

- 标准调查闭环不完整：`intent -> state -> context` 中的 `state` 面不可用
- 时间契约不稳定：schema 合法，不代表运行可行
- derived intent 在真实归因场景下稳定性不足，容易超时
- 错误反馈偏底层，不足以指导 agent 自动恢复
- skill 推荐流程与服务真实行为不完全匹配

对 agent 的直接影响是：

- 首轮成功率低
- 试错成本高
- 回退路径需要 agent 自己发明
- 无法稳定获得 session 级“下一步该看什么”的决策面

---

## 3. 缺陷清单

## 3.1 Service：`get_session_state` / `query_session_state` 返回 500，导致标准调查闭环中断

### 严重级别

- 高

### 问题归属

- 本缺陷归属为 Factum service 的 canonical session state read surface：
  - `GET /sessions/{session_id}/state`
  - `POST /sessions/{session_id}/state/query`
- 本次 agent 是通过 MCP 入口复现该问题，但 MCP 在这里是调用路径，不是缺陷主体

### 影响

- agent 无法按 skill 推荐路径读取 session-level 决策面
- detect 识别出异常后，无法通过 service 的 session state surface 获取 proposition 列表、状态、阻塞信息
- agent 被迫自己记住上游 step/artifact，并手动编排后续动作

### 可重现步骤

1. 通过 MCP 创建 session：
   - `create_session(goal="Assess overall health and risk for Trino cluster k8soneservice-oneservice on 2026-04-09 using existing Trino semantic metrics only.")`
2. 通过 MCP 提交任意一个 typed intent，例如：
   - `detect(metric="metric.trino_query_count", scope.constraints={"dimension.trino_cluster":"k8soneservice-oneservice","dimension.trino_query_state":"FAILED"}, time_scope={mode:"single_window", grain:"day", current:{start:"2026-03-26", end:"2026-04-10"}})`
3. 通过 MCP 调用：
   - `get_session_state(session_id=<上一步 session_id>, limit=100)`
   - 该调用映射到 `GET /sessions/{session_id}/state`
4. 再调用：
   - `query_session_state(session_id=<上一步 session_id>, limit=100)`
   - 该调用映射到 `POST /sessions/{session_id}/state/query`

### 实际结果

- 两次 MCP 调用最终都命中 service 的 session state endpoints
- 两个 service read surface 均返回 `500 Internal Server Error`

### 期望结果

- service 返回 canonical session state
- 至少能列出 detect 已产出的 proposition / assessment / blocking gaps
- 若服务端确实没有可读状态，也应返回结构化空结果，而不是 500

### 说明

- 本问题不是 MCP discovery、tool schema 或 transport 本身失败
- MCP 只是把 agent 调用转发到 service state surface；真正不可用的是 service 读面本身
- 2026-04-13 对仓库实现排查后，已定位到一个直接触发 500 的实现问题：
  - state/context 读面在返回前会执行 canonical payload 边界校验
  - 该校验曾把 `subject_json.metric="metric.*"`、`subject_json.slice["dimension.*"]` 这类 typed semantic 标识也误判为非法 payload
  - 真实 typed intent session 一旦产出带 semantic 标识的 proposition / finding，`/state` 就可能在投影阶段抛异常并表现为 500

### 本次实测样例

- session: `sess_cfdd004c2db3`
- `get_session_state(session_id="sess_cfdd004c2db3", limit=100)` -> `500`
- `query_session_state(session_id="sess_cfdd004c2db3", limit=100)` -> `500`

---

## 3.2 Service：`observe` 的小时粒度与时间输入契约不稳定

### 严重级别

- 高

### 影响

- agent 很难一次写对时间窗口
- schema 看起来允许 `granularity="hour"`，但真实执行对时间字符串格式和底层列类型高度敏感
- agent 无法可靠做单日日内分析

### 可重现步骤 A：日期范围 + 小时粒度

1. 创建 session
2. 调用：

```json
{
  "session_id": "<session_id>",
  "metric": "metric.trino_query_count",
  "time_scope": {
    "kind": "range",
    "start": "2026-04-09",
    "end": "2026-04-10"
  },
  "scope": {
    "constraints": {
      "dimension.trino_cluster": "k8soneservice-oneservice"
    }
  },
  "granularity": "hour"
}
```

### 实际结果 A

- 返回 `422`
- 底层错误为：`'hour' is not a valid DATE field`

### 可重现步骤 B：datetime 范围 + 小时粒度

1. 在同一 session 或新 session 中调用：

```json
{
  "session_id": "<session_id>",
  "metric": "metric.trino_query_count",
  "time_scope": {
    "kind": "range",
    "start": "2026-04-09T00:00:00",
    "end": "2026-04-10T00:00:00"
  },
  "scope": {
    "constraints": {
      "dimension.trino_cluster": "k8soneservice-oneservice"
    }
  },
  "granularity": "hour"
}
```

### 实际结果 B

- 返回 `422`
- 底层错误为：`Value cannot be cast to timestamp: 2026-04-09T00:00:00`

### 期望结果

- 同一份公开 schema 下，输入契约应稳定
- 如果底层时间列仅支持 `DATE`，则 schema 或运行前校验应直接拒绝 `hour`
- 如果需要 timestamp，则错误应明确说明接受的时间格式，例如：
  - `YYYY-MM-DD HH:MM:SS`
  - `YYYY-MM-DDTHH:MM:SSZ`
  - 或必须带时区

### 建议修复方向

- 在 service 层增加 typed preflight
- 对 `granularity=hour` 明确要求底层 `primary_time` 为 timestamp-like
- 返回 Factum 自己的结构化 remediation，而不是直接暴露底层 Trino 报错

---

## 3.3 Service：`attribute` 在真实归因维度组合下容易超时，且缺少降级建议

### 严重级别

- 高

### 影响

- agent 无法稳定使用 derived intent 完成“前一天 vs 当天”的归因分析
- 只能手动退化为 `observe + compare`
- 失去 derived intent 应有的效率优势

### 可重现步骤

1. 创建 session
2. 调用失败查询归因：

```json
{
  "session_id": "<session_id>",
  "metric": "metric.trino_query_count",
  "left": {
    "time_scope": {"kind": "range", "start": "2026-04-09", "end": "2026-04-10"},
    "scope": {
      "constraints": {
        "dimension.trino_cluster": "k8soneservice-oneservice",
        "dimension.trino_query_state": "FAILED"
      }
    }
  },
  "right": {
    "time_scope": {"kind": "range", "start": "2026-04-08", "end": "2026-04-09"},
    "scope": {
      "constraints": {
        "dimension.trino_cluster": "k8soneservice-oneservice",
        "dimension.trino_query_state": "FAILED"
      }
    }
  },
  "dimensions": [
    "dimension.trino_error_type",
    "dimension.trino_resource_group",
    "dimension.trino_catalog",
    "dimension.trino_schema",
    "dimension.trino_query_type",
    "dimension.trino_department",
    "dimension.trino_sla",
    "dimension.trino_query_source"
  ],
  "decomposition_limit": 5
}
```

3. 可选：对 `metric.trino_avg_elapsed_seconds` 复用同样模式，维度数缩减为 6 个左右

### 实际结果

- 返回 `504 timed out`

### 期望结果

- 成功返回 attribution bundle
- 或者在超时前给出结构化降级建议，例如：
  - 建议减少 `dimensions`
  - 建议先按单维度调用 `attribute`
  - 建议使用 `observe + compare + decompose`

### 建议修复方向

- 在 derived intent 内输出阶段性元数据
- 在超时体里暴露执行到哪一步
- 增加自动降级建议

---

## 3.4 Service：`diagnose` 对单点窗口虽能报 warning，但缺少明确的下一步建议

### 严重级别

- 中

### 影响

- agent 知道“这一步现在不可靠”，但不知道“接下来该怎么改”
- 容易把 derived intent 当成“没发现问题”，而不是“窗口设计不成立”

### 可重现步骤

1. 创建 session
2. 调用：

```json
{
  "session_id": "<session_id>",
  "metric": "metric.trino_avg_elapsed_seconds",
  "time_scope": {
    "mode": "single_window",
    "grain": "day",
    "current": {"start": "2026-04-09", "end": "2026-04-10"}
  },
  "scope": {
    "constraints": {
      "dimension.trino_cluster": "k8soneservice-oneservice"
    }
  },
  "candidate_dimensions": [
    "dimension.trino_query_state",
    "dimension.trino_error_type",
    "dimension.trino_resource_group",
    "dimension.trino_catalog",
    "dimension.trino_schema"
  ]
}
```

### 实际结果

- 返回 `diagnosable`
- 同时给出 warning：`Only 1 numeric point(s) found in the scan window; minimum 3 required for reliable detection.`
- 最终 `diagnoses=[]`

### 期望结果

- 除了 warning，还应给 agent 明确下一步建议，例如：
  - 扩大检测窗口到最近 14 天
  - 先做 `detect` 再围绕异常点做 compare/decompose
  - 单天问题推荐直接使用 `attribute` 或前一日对比

### 建议修复方向

- 在 validation/issues 中加入 machine-friendly remediation
- 为单点窗口返回推荐的 fallback recipe

---

## 3.5 Service：错误信息过度泄露底层 Trino 细节，缺少 Factum 层语义化 remediation

### 严重级别

- 中

### 影响

- agent 必须自己理解底层执行引擎错误
- 错误恢复路径不稳定，依赖 agent 是否熟悉 Trino

### 可重现步骤

复用以下任一场景：

- `observe` + `granularity="hour"` + date range
- `observe` + `granularity="hour"` + datetime range
- 宽维度 `attribute`

### 实际结果

- 错误主要表现为：
  - Trino `INVALID_FUNCTION_ARGUMENT`
  - Trino `INVALID_CAST_ARGUMENT`
  - transport `timed out`

### 期望结果

- Factum 返回统一结构化错误，至少包含：
  - 失败阶段：validation / compile / execute / state_projection
  - 触发原因：例如 `hour_granularity_requires_timestamp_primary_time`
  - 推荐动作：例如 `retry_without_hour_granularity`

---

## 3.6 MCP：catalog search 的类型参数不够 agent-friendly

### 严重级别

- 低

### 影响

- agent 首次做 discovery 时容易踩参数约束
- 需要先知道允许值，才能完成一次简单搜索

### 可重现步骤

1. 调用：
   - `search_catalog(q="trino k8soneservice oneservice", type="all")`

### 实际结果

- 返回错误：
  - `type must be one of: asset, binding, dimension, entity, metric, process, time`

### 期望结果

- 支持省略 `type` 代表跨类型搜索
- 或接受 `all` 并在服务端展开
- 或在错误体中返回合法值列表和推荐的重试方式

---

## 3.7 Skill：推荐工作流与实际可用链路不一致

### 严重级别

- 中

### 影响

- skill 推荐的标准流程是：
  - discover
  - create session
  - typed intent
  - read session state
  - drill proposition context
- 但真实使用中 `state` 面坏掉，导致 skill 给出的默认闭环无法执行

### 可重现步骤

1. 阅读 `factum` skill 中的 “Default Investigation Loop”
2. 按该路径执行：
   - `create_session`
   - `observe` 或 `detect`
   - `get_session_state`

### 实际结果

- 第 3 步失败，无法顺着 skill 继续

### 期望结果

- skill 要么只描述当前稳定可用路径
- 要么在文档中明确写出降级路线，例如：
  - 如果 `state` 面不可用，则改用 `observe + compare`
  - 如果 derived intent 超时，则改用 atomic intents

### 建议修复方向

- skill 中增加“known unstable surfaces / fallback paths”
- 不把暂时不稳定的读面当作默认推荐路径

---

## 3.8 Skill + MCP：对“如何做日内分析”的实践指引不足

### 严重级别

- 中

### 影响

- agent 知道有 `granularity="hour"`，但不知道什么时间格式是稳定可用的
- 这类隐式约束没有被放进 tool schema、tool description 或 skill

### 可重现步骤

1. 仅依据当前 skill 和 schema，尝试对 `2026-04-09` 做小时粒度 `observe`
2. 分别尝试：
   - date range
   - ISO datetime range

### 实际结果

- 两种方式都失败，但失败原因不同

### 期望结果

- skill 或 MCP tool 描述中明确：
  - 哪些 `time_scope` 格式在小时粒度下是可用的
  - 如果底层时间列不是 timestamp，应该如何退化

---

## 4. 本次任务中 agent 实际采用的降级路径

由于上述问题存在，本次 agent 实际采用了以下回退路线：

1. 用 `list_metrics / list_dimensions / list_bindings` 完成语义发现
2. 用 `create_session` 建 session
3. 用 `observe` 做日级总览
4. 用 `detect` 在两周窗口内确认真正异常点
5. 发现 `FAILED` 查询数在 `2026-04-09` 为高风险异常
6. 尝试 `diagnose` / `attribute`
7. 因单点窗口不足与超时问题，退化为：
   - `observe(left segmented)`
   - `observe(right segmented)`
   - `compare(segmented)`
8. 最终确认：
   - 风险集中在 `FAILED`
   - 错误类型集中在 `INSUFFICIENT_RESOURCES`
   - 资源组集中在 `global.oneservice.oneservice`

这个结果说明：

- Factum 的底层 typed intent 组合能力是够的
- 但 agent 需要自己承担过多恢复与编排责任

---

## 5. 优先级建议

建议修复优先级如下：

1. 修复 service session state surface（`get_session_state` / `query_session_state`）的 500 问题
2. 修复 `observe + hour granularity` 的时间契约不一致问题
3. 为 `attribute` / `diagnose` 增加结构化降级建议
4. 把错误从底层引擎报错提升为 Factum 层语义化错误
5. 更新 `factum` skill，使其显式描述当前稳定路径和已知回退路径
6. 优化 MCP discovery 参数与 tool description，提升首轮成功率

---

## 6. 验收建议

至少用以下回归用例验证修复：

- 用 MCP 跑 `detect -> get_session_state -> get_proposition_context` 的完整闭环，并确认 `get_session_state` 背后的 service state surface 不再返回 500
- 用 `observe(granularity="hour")` 跑单天窗口，分别覆盖 date / datetime 输入
- 用 `attribute` 对相邻两天做 3 到 8 个维度的归因，验证不会直接 504，或会给出结构化降级建议
- 用 skill 文档从零指导一个 agent 完成一次异常分析，要求不依赖临场试错理解隐藏约束
