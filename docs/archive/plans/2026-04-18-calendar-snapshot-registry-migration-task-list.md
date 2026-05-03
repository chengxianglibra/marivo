# Calendar Snapshot Registry 迁移 Todo Task List

## 概述

本文聚焦一个具体问题：当前 `calendar data` 的可消费 snapshot 通过 `factum.yaml` 的 `calendar.snapshots` 配置在服务启动时加载；这能支撑 v1 跑通，但不适合作为长期方案来承接频繁新增的节假日 / 活动窗口。

一句话结论：

- 保留 `published snapshot only`、`immutable version`、`observe-only calendar_policy_ref` 这些正确边界。
- 逐步移除“手改 `factum.yaml` + 重启服务”作为 calendar snapshot 发布入口。
- 引入运行时可读的 **calendar snapshot registry**，并为 operator 提供最小 admin HTTP 面；MCP 只镜像 canonical HTTP，不单独发明一套平行 contract。

## 背景问题

当前实现的优点：

- runtime 能冻结 `resolved_calendar_source` 与 `resolved_calendar_version`
- observation artifact / lineage / comparability metadata 能复用冻结结果
- 避免了 runtime `latest`、网页抓取、LLM 临场猜测

当前实现的主要问题：

- 新增 holiday / event version 需要修改应用配置
- 新 snapshot 生效依赖服务重启
- 数据发布职责与应用进程生命周期耦合
- 高频活动窗口更新会让 `factum.yaml` 变成半个运营数据目录
- 多环境 / 多 region / 多 source 场景下扩展性差

## 文档依据

- `../factum/docs/semantic/calendar-data-contract.zh.md`
- `../factum/docs/semantic/calendar-version-freeze-policy.zh.md`
- `../factum/docs/semantic/calendar-data-v1-source-note.zh.md`
- `../factum/docs/semantic/calendar-alignment-policy.zh.md`
- `../factum/docs/agent-guide.md`

## 目标与非目标

### 本次必须覆盖

- 定义运行时 `calendar snapshot registry` 的最小 contract
- 明确 snapshot publish / activate / deprecate 的治理流程
- 让 compiler / resolver 从 registry 读取 snapshot，而不是从静态 YAML 清单读取
- 为 operator 提供最小只读 / 写入 admin HTTP 面
- 明确 MCP 是否需要镜像这些 HTTP 面及其边界
- 给出兼容期迁移步骤，保证现有 `observe` 主链路不倒退

### 本次明确不做

- 对分析调用方暴露底层 `calendar data` 明细读取面作为公开分析 contract
- 让 `observe` 直接接收 `calendar_version`
- 让 agent / LLM 在分析请求里直接生成或提交 holiday/event annotation
- 把 `calendar data` 升格为新的 top-level semantic object family
- 在 MCP 先于 HTTP 发明一套 operator-only 管理面

## 设计判断

### 应保留的边界

- `calendar_version` 必须是不可变 snapshot version
- downstream compare-like intents 只能复用已冻结 version，不得二次重选
- `calendar data` 仍是 compiler-owned logical input contract，而不是公开 object family
- MCP 继续作为 HTTP adapter，不单独定义平行语义

### 应调整的边界

- `calendar snapshot registry` 不再由 `factum.yaml` 充当正式发布目录
- `factum.yaml` 只保留本地开发 / fallback / bootstrap 用途
- snapshot 的发布、生效、停用应进入可运行时读取的治理存储

## 目标架构

目标态拆成三层：

### 1. Source Snapshot Layer

- holiday source 发布不可变 `calendar_version`
- event source 发布不可变 `calendar_version`
- source owner 对各自数据质量和 versioning 负责

### 2. Calendar Snapshot Registry Layer

- 记录可供 runtime 消费的 `resolved_calendar_source`
- 记录 `resolved_calendar_version`
- 记录 `region_code`、`effective_start`、`effective_end`
- 记录底层 lineage：holiday source/version、event source/version
- 记录 lifecycle：draft / active / deprecated
- 记录 readiness / validation 结果

### 3. Runtime Consumption Layer

- compiler / resolver 只读取 `active + ready` snapshot
- 每次执行冻结单一 snapshot version
- observation artifact / step metadata 持续记录冻结结果

## 推荐落地策略

采用“两阶段迁移”：

1. 先引入 registry 与 admin HTTP 面，同时保留 `factum.yaml` fallback。
2. 再让 runtime 默认读 registry，并把 YAML 清单降级为仅本地开发 / 测试入口。

## Todo Task List

## 一、Scope 与 Contract 冻结

- [ ] 任务 1.1：冻结 migration scope
  - 交付物：scope note
  - 关键内容：替换的是 snapshot 发布目录，不是替换 calendar data logical contract
  - 验收标准：团队对“保留 immutable version、去掉 YAML 作为正式 registry”无歧义

- [ ] 任务 1.2：冻结 registry object 最小字段集
  - 交付物：schema 草案
  - 最小字段：`snapshot_id`、`resolved_calendar_source`、`resolved_calendar_version`、`region_code`、`effective_start`、`effective_end`、`holiday_source`、`event_source`、`lifecycle_status`、`readiness_status`
  - 验收标准：字段足以驱动 runtime 选择、operator 排障与 lineage 追溯

- [ ] 任务 1.3：冻结 lifecycle / readiness 语义
  - 交付物：状态机说明
  - 范围：`draft`、`active`、`deprecated` 与 `ready`、`not_ready`
  - 验收标准：不会把 `active` 错当成 `ready`

- [ ] 任务 1.4：冻结 YAML fallback 边界
  - 交付物：兼容期说明
  - 关键内容：`factum.yaml` 仅允许本地开发、测试和 bootstrap，不再作为正式生产发布目录
  - 验收标准：新功能文档不再把“改 YAML 并重启”描述成推荐流程

## 二、Registry 数据模型与存储

- [ ] 任务 2.1：选择 registry 持久化位置
  - 交付物：存储决策记录
  - 备选：metadata sqlite 新表、已有 semantic metadata 扩展表、专用治理表
  - 验收标准：读写路径清晰，且能支撑 lifecycle/readiness 查询

- [ ] 任务 2.2：定义 registry lineage 表达
  - 交付物：lineage schema
  - 最小内容：holiday source id/name/table/version；event source id/name/table/version
  - 验收标准：operator 能从逻辑 snapshot 反查到底层版本

- [ ] 任务 2.3：补齐 registry 唯一性与冲突规则
  - 交付物：约束说明
  - 范围：同一 `region_code` + 生效窗口不允许多个 `active + ready` snapshot 同时命中
  - 验收标准：runtime 不会因 registry 冲突产生多候选歧义

## 三、Publish / Validate / Activate 工作流

- [ ] 任务 3.1：定义 snapshot publish workflow
  - 交付物：流程文档
  - 建议步骤：create draft -> validate -> activate -> deprecate old
  - 验收标准：新增 holiday / event version 时无需改应用配置

- [ ] 任务 3.2：实现 snapshot validation
  - 交付物：validate 逻辑
  - 范围：source 已注册、table 已同步、calendar_version 非动态别名、窗口覆盖合法、必填字段可消费
  - 验收标准：不合法 snapshot 不能进入 ready

- [ ] 任务 3.3：实现 activate / deprecate 语义
  - 交付物：lifecycle action
  - 验收标准：新 snapshot 激活后，旧 snapshot 可保留回放能力但不再参与默认解析

## 四、Runtime 读取迁移

- [ ] 任务 4.1：抽象 calendar snapshot provider
  - 交付物：provider interface
  - 范围：registry provider、YAML fallback provider
  - 验收标准：runtime 不再把 snapshot 来源与消费逻辑耦合在一起

- [ ] 任务 4.2：让 `CalendarDataReader` 默认从 registry 读取 snapshot bindings
  - 交付物：runtime 改造
  - 验收标准：新增 snapshot 激活后，无需重启即可对新请求生效

- [ ] 任务 4.3：保留兼容期 fallback
  - 交付物：fallback 规则
  - 范围：当 registry 未启用时，开发环境仍可读取 `factum.yaml`
  - 验收标准：本地测试与 demo 不被阻断

- [ ] 任务 4.4：补齐并发与缓存策略
  - 交付物：cache / invalidation 说明
  - 关键内容：避免每次请求都全量扫 registry，同时确保 activate 后可见性可控
  - 验收标准：行为一致、不会出现长时间脏读

## 五、HTTP 管理面

- [ ] 任务 5.1：定义 admin-only HTTP surface
  - 交付物：API 草案
  - 建议最小面：`create`、`list`、`get`、`validate`、`activate`、`deprecate`
  - 验收标准：足够支撑 snapshot 发布，不额外暴露底层 calendar data 明细分析面

- [ ] 任务 5.2：定义 operator 只读排障面
  - 交付物：read surface 说明
  - 范围：查看 active snapshot、lineage、coverage window、validation blockers
  - 验收标准：operator 不需要读底表 SQL 就能知道当前 runtime 会选哪份 snapshot

- [ ] 任务 5.3：明确权限边界
  - 交付物：auth / authz 约定
  - 验收标准：普通分析调用方不能通过公开分析面管理 snapshot

## 六、MCP 镜像策略

- [ ] 任务 6.1：冻结 MCP 边界
  - 交付物：adapter scope note
  - 关键内容：只有在 canonical HTTP 稳定后才新增 MCP tool；MCP 不先行发明 contract
  - 验收标准：MCP README / inventory 与 HTTP 面保持一一对应

- [ ] 任务 6.2：为 admin HTTP 面补 MCP 镜像计划
  - 交付物：MCP task list
  - 最小范围：list/get/validate/activate/deprecate
  - 验收标准：若 HTTP 面存在，MCP tool 参数仍直接映射 canonical HTTP body/path

- [ ] 任务 6.3：明确不新增的 MCP 面
  - 交付物：boundary note
  - 范围：不新增面向调用方的 `calendar data rows` browse tool
  - 验收标准：不会把 MCP 变成底层 holiday/event 数据浏览器

## 七、测试与验证

- [ ] 任务 7.1：补 registry model / lifecycle tests
  - 交付物：单元测试
  - 验收标准：状态迁移、冲突窗口、非 immutable version 都能稳定失败

- [ ] 任务 7.2：补 runtime migration tests
  - 交付物：集成测试
  - 场景：registry active snapshot 生效、YAML fallback 生效、旧 snapshot 回放不受新 snapshot 影响
  - 验收标准：新增 snapshot 后新请求命中新版本，旧 observation 仍保留旧 lineage

- [ ] 任务 7.3：补 HTTP / MCP contract tests
  - 交付物：接口测试
  - 验收标准：HTTP canonical contract 与 MCP adapter inventory 同步更新

## 八、文档与迁移上线

- [ ] 任务 8.1：更新共享文档
  - 交付物：文档 PR 清单
  - 最少涉及：`docs/agent-guide.md`、calendar version / source note、API 文档、`factum-mcp/README.md`
  - 验收标准：文档不再把 `factum.yaml + 重启` 作为推荐生产流程

- [ ] 任务 8.2：准备迁移手册
  - 交付物：operator guide
  - 内容：如何从 YAML snapshot 迁到 registry snapshot
  - 验收标准：现有环境可以按步骤平滑迁移

- [ ] 任务 8.3：定义回滚策略
  - 交付物：rollout / rollback note
  - 验收标准：新 snapshot 激活出错时，可快速切回旧 active snapshot

## 推荐实施顺序

1. 先完成第 1、2、3 章，冻结 registry contract、状态机和发布流程。
2. 再完成第 4 章，让 runtime 支持 registry provider 与 YAML fallback 双读。
3. 然后完成第 5、6 章，补齐 operator HTTP 面和 MCP 镜像边界。
4. 最后完成第 7、8 章，补测试、文档、迁移和回滚。

## 最小可上线验收口径

- 新增 holiday / event snapshot 不再需要修改 `factum.yaml`
- 激活新 snapshot 后，新请求无需重启服务即可生效
- 旧 observation 的 `resolved_calendar_version` 与 lineage 仍可回放
- runtime 默认只读取 `active + ready` snapshot
- operator 能通过 HTTP 读取当前 active snapshot 及其 lineage
- 如启用 MCP，MCP 只镜像已稳定的 canonical HTTP 管理面

## 风险与前置依赖

- 最大风险不是 runtime 读取改造，而是 registry 状态机与窗口冲突规则不先冻结
- 若 registry 不记录足够 lineage，后续 comparability 排障会退化
- 若过早暴露底层 `calendar data` 明细 browse 面，容易把 compiler-owned logical contract 误升级成公开产品面
- 若 MCP 先于 HTTP 独立扩张，会再次破坏 HTTP-only 产品边界

## 建议的 owner 切分

- Runtime / compiler owner：第 1、4 章
- Metadata / governance owner：第 2、3 章
- API / operator owner：第 5 章
- MCP owner：第 6 章
- QA / rollout owner：第 7、8 章
