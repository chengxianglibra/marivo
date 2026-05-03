# Execution Auth Session User 实施 Todo Task List

## 概述

本文将 [`docs/service/execution-auth-contract.md`](/Users/lichengxiang/source/oss/marivo/docs/service/execution-auth-contract.md) 拆解为一份可直接落地开发的实施清单，目标是在 **保持 HTTP-only 边界、明确“当前不做用户身份认证”、允许 agent 在 analysis session 级别配置用户信息** 的前提下，为当前已支持的 `duckdb/trino` 落一套最小 execution auth contract：

- `engine.auth.mode = none | username_only`
- `session.execution_identity.session_user`
- runtime 在 Trino 连接阶段把 `session_user` 注入为 connection `user`

一句话结论：

- v1 先做“**contract 冻结 + metadata schema 补齐 + session / engine API 收敛 + runtime username 注入 + tests / docs 收尾**”。
- 不做 password/token/OAuth/Kerberos/proxy user/delegation，不做独立 `engine auth policy`，不做 typed intent 级重复传参。
- 任务拆解围绕六个交付面推进：contract 冻结、schema/存储、session/engine API、runtime 主链路、测试、文档与运维说明。

## 文档依据

- [`docs/service/execution-auth-contract.md`](/Users/lichengxiang/source/oss/marivo/docs/service/execution-auth-contract.md)
- [`docs/service/source-execution-mapping-contract.md`](/Users/lichengxiang/source/oss/marivo/docs/service/source-execution-mapping-contract.md)
- [`docs/api/engines.md`](/Users/lichengxiang/source/oss/marivo/docs/api/engines.md)
- [`docs/agent-guide.md`](/Users/lichengxiang/source/oss/marivo/docs/agent-guide.md)

## 当前实现对照

当前仓库基线的关键事实：

- `engines` 的 public shape 目前仍是 `connection + default_namespace + deployment_capabilities + policy`，尚未引入 `auth` 分层。
- `SessionCreateRequest` 当前只有 `goal/budget/policy`，尚未提供 `execution_identity` 输入。
- `sessions` 表当前只有 `constraints_json/budget_json/policy_json/raw_filter` 等字段，尚未承载 session 级用户信息。
- `SessionManager.create_session(...)` 当前不会冻结 execution user。
- Trino runtime 当前直接从 `connection.user/password/http_headers` 读取连接参数；DuckDB 只读取 `path`。

因此，下一阶段重点不是设计更复杂 auth 能力，而是把“session 级用户名注入”从文档收敛到 schema、API、runtime 与测试主链路。

## 实施范围

### 本次必须覆盖

- 为 engine 定义最小 `auth` shape：`mode/username_source/fallback_username`
- 为 session 定义最小 `execution_identity` shape：`session_user/actor_ref`
- 让 `POST /sessions` 支持 analysis session 级用户配置
- 让 Trino runtime 从 session 读取 `session_user` 并注入 connection `user`
- 让 DuckDB 明确忽略 session 用户信息
- 补齐 schema、API、runtime、测试、文档和 operator 说明

### 本次明确不做

- 用户身份认证
- `credential_ref` / `delegation_ref`
- bearer token / OAuth / Kerberos / client certificate
- proxy user / impersonation / `SET SESSION AUTHORIZATION`
- typed intent payload 级的 `session_user` override
- 多种 auth mode 并行治理
- 独立 `engine auth policy` object

## 交付原则

- 粒度以“单个 owner 可独立完成并验收”为准，避免泛泛的“支持 session user”。
- 边界以 contract 分层为准，不让 session、engine、runtime、typed intent、routing 职责互相渗透。
- 每个任务都必须有明确交付物和验收标准，避免“代码已改但无法判断是否真正闭环”。
- 先冻结最小 contract，再补 schema/API，最后打通 runtime、测试和文档。

## 建议实施顺序

1. T1 冻结 v1 scope、对象边界与失败面
2. T2 补 sessions / engines metadata schema
3. T3 收敛 session create 与 engine 读写 API
4. T4 打通 runtime username 注入主链路
5. T5 补测试矩阵与 golden cases
6. T6 更新文档、示例和 operator 说明

说明：

- T2 是 T3/T4 的前置，因为 API 和 runtime 都依赖稳定存储 shape。
- T3 与 T4 可并行推进，但都必须以同一份 `execution_identity` / `engine.auth` contract 为准。
- T4 不应被扩写成完整 auth framework；只实现 username injection。

## Todo Task List

## 一、Scope 与 Contract 冻结

- [x] 任务 1.1：冻结 v1 产品边界
  - 交付物：scope note / decision record
  - 关键内容：当前只支持 session 级 `session_user` 注入；不支持用户认证、不支持 delegation/proxy/token
  - 验收标准：团队对“当前 execution auth 表达什么、明确不表达什么”无歧义

- [x] 任务 1.2：冻结 engine `auth` 最小字段集
  - 交付物：schema note
  - 最小字段：`mode`、`username_source`、`fallback_username`
  - 验收标准：字段足以驱动 Trino username 解析与 DuckDB 忽略逻辑，不引入多余 auth 维度

- [x] 任务 1.3：冻结 session `execution_identity` 最小字段集
  - 交付物：schema note
  - 最小字段：`session_user`、`actor_ref`
  - 验收标准：字段足以表达“本次 analysis session 使用哪个用户”并支持审计，不引入认证语义

- [x] 任务 1.4：冻结 username resolution 顺序
  - 交付物：resolution note
  - 规则：`session_user` 优先，其次 `fallback_username`，否则明确报错
  - 验收标准：runtime 不再静默猜测 Trino `user`

- [x] 任务 1.5：冻结 v1 失败面
  - 交付物：error taxonomy
  - 范围：`session_user_missing`、`engine_auth_invalid`、`engine_auth_unsupported`、`session_execution_identity_invalid`
  - 验收标准：实现阶段可直接映射结构化错误，不依赖自由文本猜测

### 冻结附录（任务 1.1 - 1.5）

本附录记录 tasks 1.1 - 1.5 的冻结结果，后续 2.x - 6.x 的 schema / API / runtime / tests 改动都以此为准。

#### A. v1 scope / non-goals

- v1 的 execution auth 只表达“本次 analysis session 使用哪个 Trino username”。
- 当前不做用户身份认证，不做 delegation / proxy / token / password 治理。
- 当前不做 typed intent payload 级的 `session_user` override。
- 当前不做独立 `engine auth policy`，也不扩成通用 auth framework。

#### B. 最小 contract 冻结

- engine `auth` 最小字段集固定为：`mode`、`username_source`、`fallback_username`。
- session `execution_identity` 最小字段集固定为：`session_user`、`actor_ref`。
- `actor_ref` 只用于 Marivo 审计，不参与 Trino 认证。
- 后续接口新增边界固定为：
  - `SessionCreateRequest` 只新增 `execution_identity.session_user` 与 `execution_identity.actor_ref`
  - `EngineRegisterRequest` / `EngineResponse` 只新增 `auth.mode`、`auth.username_source`、`auth.fallback_username`

#### C. Username resolution 冻结

- `session_user` 优先，其次 `fallback_username`，否则失败。
- Trino runtime 只能通过统一 resolver 获得最终 `user`，不得在调用点散落 fallback 逻辑。
- DuckDB runtime 不读取 `session_user`，也不因 session 携带用户信息报错。

#### D. 失败面 taxonomy 冻结

- `session_user_missing`
  - 触发条件：`trino + username_only` 时，经 resolution 后仍拿不到 username
  - 责任层：runtime resolver / execution preflight
- `engine_auth_invalid`
  - 触发条件：`auth` shape 非法，或 `username_source/fallback_username` 组合非法
  - 责任层：engine validator / service write path
- `engine_auth_unsupported`
  - 触发条件：engine 类型与 auth mode 组合不支持，例如 `duckdb + username_only`
  - 责任层：engine validator / service write path
- `session_execution_identity_invalid`
  - 触发条件：session 输入中的 `session_user` / `actor_ref` 非法
  - 责任层：session validator / service write path

说明：

- 稳定错误码本轮先落在服务/运行时层。
- HTTP API 本轮仍可继续返回当前 plain `detail` 形态，不扩成结构化错误协议。

## 二、Metadata Schema 与持久化基础

- [x] 任务 2.1：为 `engines` 增加 `auth_json`
  - 交付物：metadata schema / DDL
  - 最小字段：`auth_json TEXT NOT NULL DEFAULT '{}'`
  - 验收标准：engine 可稳定持久化 `mode/username_source/fallback_username`

- [x] 任务 2.2：为 `sessions` 增加 `execution_identity_json`
  - 交付物：metadata schema / DDL
  - 最小字段：`execution_identity_json TEXT NOT NULL DEFAULT '{}'`
  - 验收标准：analysis session 可冻结 `session_user/actor_ref`

- [x] 任务 2.3：更新 metadata template 与 bootstrap 路径
  - 交付物：`tests/shared_fixtures.py` 模板版本与校验器、schema bootstrap 逻辑
  - 验收标准：新字段在全新 metadata store 与测试模板中一致可用

- [x] 任务 2.4：明确旧数据默认值与 degraded-read 行为
  - 交付物：compat note / serializer 约定
  - 关键内容：旧 engine 行默认 `auth.mode=none`；旧 session 行默认 `execution_identity={}`
  - 验收标准：历史行不会因缺少新字段直接触发 500

## 三、Session / Engine API 与读写面收敛

- [ ] 任务 3.1：扩展 `SessionCreateRequest`
  - 交付物：API model 变更
  - 最小字段：`execution_identity.session_user`、`execution_identity.actor_ref`
  - 验收标准：`POST /sessions` 可显式接收 session 级用户信息

- [ ] 任务 3.2：更新 session root read surface
  - 交付物：session response model / serializer
  - 范围：`GET /sessions/{session_id}`、`GET /sessions`
  - 验收标准：session 读面可稳定回显 `execution_identity`，字段语义与 create 面一致

- [ ] 任务 3.3：扩展 engine register/update/read 模型
  - 交付物：engine request/response model
  - 最小字段：`auth.mode`、`auth.username_source`、`auth.fallback_username`
  - 验收标准：engine 读写面能稳定表达 username injection contract

- [ ] 任务 3.4：实现 session `execution_identity` validate
  - 交付物：validator / service 校验
  - 范围：`session_user` 非空字符串、可选规范化；`actor_ref` 可选但非空白
  - 验收标准：非法 session 用户信息在写入前被拦截

- [ ] 任务 3.5：实现 engine `auth` validate
  - 交付物：validator / service 校验
  - 范围：`duckdb` 仅允许 `mode=none`；`trino` 允许 `none|username_only`；`username_source` 与 `fallback_username` 组合合法
  - 验收标准：不支持的 engine/auth 组合稳定失败，不被静默接受

- [ ] 任务 3.6：明确 typed intent 输入边界
  - 交付物：API boundary note / validator
  - 关键内容：typed intent 不接受 `session_user` override；分析用户只在 session create 冻结
  - 验收标准：调用方不会再把 execution user 散落到各个 typed intent payload

## 四、Runtime Username Injection 主链路

- [x] 任务 4.1：为 session service 提供 execution identity 读取能力
  - 交付物：`SessionManager` / orchestration service 改造
  - 验收标准：runtime 在执行前能稳定获取 session 冻结的 `execution_identity`

- [x] 任务 4.2：实现 engine auth resolution
  - 交付物：runtime helper / resolver
  - 规则：读取 engine `auth` 与 session `execution_identity`，产出最终 execution username
  - 验收标准：resolution 顺序与文档一致，不残留分散在调用点的字符串拼装逻辑

- [x] 任务 4.3：在 Trino runtime 注入最终 `user`
  - 交付物：`build_analytics_engine` / Trino builder 改造
  - 关键内容：不再只信任静态 `connection.user`；当 engine 配置为 `username_only` 时，优先写入 resolved username
  - 验收标准：同一个 Trino engine 可随 session 不同生成不同 `user`

- [ ] 任务 4.4：明确 DuckDB 忽略逻辑
  - 交付物：runtime guard
  - 验收标准：DuckDB 不读取 `session_user`，也不会因 session 携带用户信息而失败

- [ ] 任务 4.5：补 execution preflight 失败面
  - 交付物：preflight / diagnostics
  - 场景：`username_only` 且 session 未提供 `session_user`、也没有 `fallback_username`
  - 验收标准：问题在 preflight 暴露，而不是等连接 Trino 时报底层错误

- [ ] 任务 4.6：补审计字段输出
  - 交付物：observability / audit note
  - 最小字段：`session_id`、`engine_id`、`session_user`、`actor_ref`
  - 验收标准：可区分“本次连接用了哪个 execution user”与“哪个 agent 触发了分析”

## 五、测试矩阵与 Golden Cases

- [x] 任务 5.1：补 session create / read API 测试
  - 交付物：API tests
  - 场景：带 `execution_identity` 创建、默认空值创建、回读一致性、非法空白用户名失败
  - 验收标准：session API 对新字段的行为稳定

- [x] 任务 5.2：补 engine auth model 测试
  - 交付物：unit / API tests
  - 场景：`trino + username_only` 合法、`duckdb + username_only` 非法、`fixed` 模式缺少 `fallback_username` 非法
  - 验收标准：engine auth 组合边界稳定

- [x] 任务 5.3：补 runtime resolution 测试
  - 交付物：unit tests
  - 场景：`session_user` 优先、fallback 生效、双缺失失败、DuckDB 忽略
  - 验收标准：username resolution 顺序与失败面稳定

- [x] 任务 5.4：补 Trino execution 注入测试
  - 交付物：Trino mock tests
  - 场景：不同 session 命中同一 engine 时生成不同 `user`
  - 验收标准：session 级用户名注入真正进入 Trino builder，而不是只停留在 session model

- [x] 任务 5.5：补 degraded-read / legacy row 测试
  - 交付物：storage / serializer tests
  - 场景：缺少 `auth_json` 的旧 engine 行、缺少 `execution_identity_json` 的旧 session 行
  - 验收标准：旧数据按默认语义回读，不触发 500

- [x] 任务 5.6：补 docs 示例对应的 golden cases
  - 交付物：integration-ish tests 或 fixture cases
  - 场景：`trino + session_user`、`trino + fixed fallback`、`duckdb ignore`
  - 验收标准：每个样例都对应一个明确 contract 边界或回归风险

## 六、文档与运维收尾

- [x] 任务 6.1：更新 `docs/api/engines.md`
  - 交付物：API 文档
  - 范围：补 engine `auth` 字段说明与 `duckdb/trino` 允许值矩阵
  - 验收标准：外部文档不再把 `connection.user/password/http_headers` 描述成目标态 contract

- [x] 任务 6.2：更新 session 相关 API 文档
  - 交付物：session create / read 文档
  - 范围：补 `execution_identity.session_user/actor_ref` 字段说明
  - 验收标准：调用方知道用户信息应在 session create 提交，而不是在 typed intent 中传

- [ ] 任务 6.3：更新 shared guide
  - 交付物：[`docs/agent-guide.md`](/Users/lichengxiang/source/oss/marivo/docs/agent-guide.md)
  - 内容：execution user 只在 session create 冻结；当前不支持 delegation/proxy/token
  - 验收标准：后续 agent 不会再把这条能力误解成完整身份认证框架

- [x] 任务 6.4：补 operator 示例与排障说明
  - 交付物：operator note / examples
  - 内容：如何配置 `trino.username_only`、如何传 `session_user`、缺失用户时会报什么错
  - 验收标准：运维和调用方不需要读代码即可完成最小配置与排障

## 验收标准

- engine 已能稳定表达 `auth.mode/username_source/fallback_username`
- session 已能稳定表达 `execution_identity.session_user/actor_ref`
- Trino runtime 会按 session 级用户信息注入 connection `user`
- DuckDB 明确忽略 session 用户信息
- typed intent 不再承担 execution user 传参职责
- 旧 metadata 行可按默认语义回读，不会因新字段缺失变成 500
- API、runtime、测试、文档对“当前不做用户身份认证”这一边界描述一致

## 建议 PR 切分

1. PR1：冻结 contract，并补 `engines/sessions` metadata schema 与默认值
2. PR2：补 session / engine API model、validator 与 read surface
3. PR3：打通 Trino username resolution / injection 主链路
4. PR4：补测试矩阵与 degraded-read case
5. PR5：更新 API 文档、guide 与 operator 示例

## 风险提示

- 若把 `session_user` 扩写成“已认证用户”，会把当前最小 contract重新拉回大而全 auth 设计。
- 若允许 typed intent 再传一份 execution user，会重新制造 session 与 step 双权威。
- 若 DuckDB 也被强行纳入同一 username 注入逻辑，会平白增加无意义实现复杂度。
- 若旧 rows 的默认值策略不先明确，schema 落地后容易在 list/get 路径引入回归。
