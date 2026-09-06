# 可注入 Datasource 凭证解析设计

Date: 2026-09-05

Revised: 2026-09-06 — 收窄为解析来源注入，保留现有连接生命周期与 backend override 分派。

Status: implemented in the local checkout — 已实现，尚未发布；验收记录见
[implementation acceptance](2026-09-06-injectable-datasource-credentials-acceptance.md)。

## 1. 目标与结论

为 Marivo 提供一个公开、可按执行上下文注入的只读凭证解析接口，使宿主凭证服务可以直接参与 datasource
连接，而不必把秘密值先写入 `os.environ` 或 `~/.marivo/secrets.toml`。

采用一个规范入口：`md.credential_scope(resolver=...)`。独立连接操作捕获当前 resolver，Session 与 reader
的连接 runtime 在创建时捕获 resolver，覆盖显式连接、连接测试、metadata inspection、semantic 数据访问和
analysis 内部连接。scope 只选择解析来源；连接资源继续由现有 connection、service 与 Session 管理。
环境变量与本地缓存保留为未显式注入时的默认来源；显式注入完全替换默认解析链，不隐式 fallback，不回写缓存。

宿主仍负责秘密值存储、用户输入、轮换、授权和跨进程传输。Marivo 负责引用需求、解析调用、来源绑定、
脱敏错误和实际连接结果。本方案不引入 UI、secret manager、Agent orchestration 或新的分析入口。

阅读导航：[实施前基线](#2-实施前事实与问题) · [公开接口](#4-公开接口设计) ·
[作用域](#5-作用域与生命周期) · [连接覆盖](#6-连接调用链与覆盖范围) ·
[错误](#7-失败脱敏与持久化) · [宿主适配](#8-宿主集成与跨进程传输) ·
[验收](#10-实施顺序与验收)。

### 1.1 与现有设计的关系

- [Datasource layer](../../specs/semantic/datasource-layer.md) 继续拥有 datasource 定义与连接行为；本文细化
  credential resolution，并增加显式宿主来源。
- [Semantic overview](../../specs/semantic/overview.md) 与
  [loading/inspection](../../specs/semantic/loading-validation-introspection.md) 的语义、有效性和读取边界不变。
- [Analysis Session runtime](../../specs/analysis/session-state-and-runtime.md) 继续拥有 Session 恢复与连接资源；
  本文增加运行时凭证绑定，不把 resolver 或秘密值放进 Session Store。
- [Agent-facing surface](../../specs/agent-friendly-public-surface.md) 的单一入口、typed error、bounded repr
  和渐进 Help 原则同样适用于新增能力。

本次实现同步公开签名、Help、测试、当前 API 文档与 latest 英中文档。AGENTS.md 与相邻宿主源码保持不变，
未执行发布或部署。

## 2. 实施前事实与问题

设计编写时核对基线：相邻插件实际安装 Marivo 0.5.3，当前仓库 `pyproject.toml` 为 0.5.3.dev0；相关解析路径在两者
一致。以下均是注入能力实施前已读取源码的事实，不是根据类型名称推测的能力。

| 当前能力 | 事实 | 缺口 |
|---|---|---|
| `SecretProvider.get(name)` | 内部 Protocol 已存在 | 不是贯穿公开执行面的宿主接入契约 |
| `secrets.resolve(..., providers=...)` | 底层接受 provider 列表 | backend 构建没有传入该参数 |
| `default_chain()` | 环境变量优先，再读用户本地缓存 | 宿主不能从公共入口禁用或替换这条链 |
| `md.connect/test/inspect` | 公开操作最终构建内部 backend | 没有 credential resolver 注入参数或上下文 |
| Session `backends` / `backend_factory` | 可提供自建 backend | 接管范围过大，且不统一覆盖 datasource 与 semantic 路径 |
| `MARIVO_PERSIST_CREDENTIALS=0` | 禁止缓存写入 | 不禁用已有缓存读取，不等于只信任宿主凭证 |

实现依据：

- [secrets.py](../../../marivo/datasource/secrets.py)：provider、默认链、resolve 与自动缓存。
- [backends.py](../../../marivo/datasource/backends.py)：`_effective_kwargs()` 解析引用，连接 engine builders。
- [manage.py](../../../marivo/datasource/manage.py)：显式连接、测试、deadline worker 与 raw SQL。
- [runtime.py](../../../marivo/datasource/runtime.py)：scoped/session backend 与缓存。
- [semantic reader](../../../marivo/semantic/reader.py)：数据访问的内部连接服务。
- [analysis connection runtime](../../../marivo/analysis/session/_connections.py)：analysis 连接及验证记账。

环境变量是可工作的进程间传递方式，但把“凭证引用”“解析权威”和“传输方式”耦合在一起。全局环境修改
还会扩大值的可见范围；同名引用无法仅靠名称表达宿主操作授权。通过全局 monkey patch 修改 `default_chain`
或 `resolve` 又会产生并发、测试和版本耦合，因此不作为正式接入方式。

## 3. 职责与非目标

| 参与方 | 负责 |
|---|---|
| Datasource 定义 | 声明需要的字段与引用，不保存秘密值 |
| Marivo | 生成真实解析需求，调用 resolver，创建并管理连接，输出安全错误 |
| CredentialResolver | 在宿主授予的范围内读取引用，返回本次使用的秘密值或 typed failure |
| 宿主凭证服务 | 存储、更换、删除、权限、续期与后端来源优先级 |
| 宿主适配器 | 将 Marivo 需求映射到宿主身份；实现必要的 IPC、超时和取消 |
| Agent/UI | 选择操作、让用户填写、解释失败；不通过模型上下文传递秘密值 |

首版不改变 datasource authoring 语法，不增加 URI 引用体系、Workspace credential store、默认云服务 SDK、
OAuth flow、自动重试 UI、secret 导出或值回滚。也不支持把任意 driver auth 对象直接放进 datasource 定义。

CredentialResolver 是读取能力，不包含 `set`、`unset`、`list`、用户提问或挂起到人工响应的方法。缺失凭证
要及时返回；宿主可以结束或挂起自己的工具流程，用户完成填写后再发起一次解析与连接测试。

## 4. 公开接口设计

### 4.1 单一注入入口

公开 API：

```python
from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

class CredentialResolver(Protocol):
    def resolve(self, request: CredentialRequest) -> SecretValue:
        ...

def credential_scope(
    *, resolver: CredentialResolver
) -> AbstractContextManager[None]:
    ...
```

上述类型与函数由 `marivo.datasource` 导出，使用 `import marivo.datasource as md`。内部解析来源 binding
和默认 provider chain 不进入 `md.__all__`。不再增加平行的 `set_resolver`、全局注册表、
每个业务 API 的 `resolver=` 参数或另一套 Runtime facade。

不传 `resolver` 不是这项 API 的一种模式：希望使用既有默认行为时，不进入该 context manager。

以下示例适用于当前本地实现，调用方提供宿主 resolver：

```python
import marivo.datasource as md
import marivo.semantic as ms
import marivo.analysis as mv

def verify(resolver: md.CredentialResolver) -> md.DatasourceTestResult:
    with md.credential_scope(resolver=resolver):
        return md.test("warehouse")

def inspect_orders(resolver: md.CredentialResolver) -> md.SourceInspection:
    with md.credential_scope(resolver=resolver):
        return md.inspect(ms.ref.datasource("warehouse"), md.table("orders"))

def analyze(resolver: md.CredentialResolver) -> None:
    with md.credential_scope(resolver=resolver):
        session = mv.session.get_or_create("credential-integration")
    try:
        # The runtime keeps its resolver after the selection scope exits.
        session.show()
    finally:
        session.close()
```

Session 示例仅展示解析来源捕获与显式释放，不把 `show()` 当作连接或分析验收。宿主须让 resolver 客户端
在使用它的 runtime 存活期间保持可用；退出选择 scope 不会关闭客户端或 Session。

### 4.2 CredentialRequest

请求是 Marivo 创建的只读对象，Host resolver 不从任意用户字典推导它。字段如下：

| 字段 | 类型 | 含义 |
|---|---|---|
| `reference` | `str` | datasource 原始引用名称，不是秘密值或宿主访问 token |
| `project_root` | `Path` | 本次操作已经解析的项目根；不得由 provider 再读取进程 cwd 猜测 |
| `datasource` | `str` | 当前真实 datasource 名称 |
| `fields` | `tuple[str, ...]` | 依赖该引用的连接字段，稳定排序并去重 |
| `deadline_monotonic` | `float \| None` | 当前操作已有的本进程单调时钟 deadline；无外层 deadline 时为 None |
| `cancelled` | `bool` 只读属性 | 读取时反映本次连接操作取消状态，不是创建时的固定快照；scope 退出不触发取消 |

同一次 backend 构建内，相同引用仅解析一次，结果投影到它对应的全部字段；不得让同一字段组因重复读取而混入
两次不同值。不同引用依次解析形成该次连接的 snapshot，不承诺外部存储对多个引用提供全局原子读。

Context 字段帮助宿主做映射和校验，本身不是授权。可信 adapter 必须使用自己预先绑定的宿主 operation/lease
核对请求；不能允许 caller 只修改 datasource 名称就获得另一组秘密值。

Request 的 repr/show 只输出有界的引用、datasource 与字段摘要，不包含 resolver、连接配置或宿主上下文对象。
完整 project 路径不进入默认 repr。普通用户不会被要求手工构造 CredentialRequest。

### 4.3 SecretValue

`SecretValue` 是短生命周期的值封装，构造参数是非空字符串，唯一明确读取方法为 `reveal() -> str`，供
可信连接 adapter 使用。它不支持自动 JSON、pickle、dataclass 展开或作为配置/artifact 字段保存。

`repr` 和 `str` 固定只显示类型与 redacted 标记，不显示值、前后缀、长度、hash 或 provider 对象；不实现
面向 Agent 的 `.show()`、复制或导出功能。它属于凭证能力的受限传递对象，不是分析 terminal result。

`SecretValue` 不承诺阻止有执行权限的 Python 代码主动读取值，也不承诺 Python 字符串可可靠原地擦除。
生命周期要求是缩短引用保留时间、禁止序列化、移除额外缓存，并阻止值进入正常日志和展示路径。

### 4.4 引用语法保持原样

首版继续使用 `password_env="WAREHOUSE_PASSWORD"`、`user_env="WAREHOUSE_USER"` 等现有字段；不新增
`password_ref` 别名，不迁移 datasource 文件。这里的值仍遵守现有引用校验，宿主 adapter 可将该名字映射到
任意凭证服务键，不能要求 datasource 项目配置包含 DSH 专属存储地址。

同名引用在不同项目或 datasource 上可以由同一个 resolver 按 request context 映射到不同宿主键；这种隔离
由宿主映射与授权提供，并不是 Marivo 新建了分项目存储。默认链仍按现有环境名称读取。

`md.describe()` 和 catalog 的静态读取只披露引用，不为显示“配置状态”调用 resolver。存储状态查询、全量
枚举和可写性属于宿主管理 API，不混入只读解析接口。

## 5. 作用域与生命周期

### 5.1 默认与显式模式

| 路径 | 解析权威 | 缺失/失败 | 本地缓存 |
|---|---|---|---|
| 操作或 runtime 捕获默认来源 | 现有默认 provider chain | 保持现有行为与错误 | 保持既有读写规则 |
| 操作或 runtime 捕获显式 resolver | 仅该 resolver | 返回 typed failure，不尝试其他来源 | 不读、不写 |

即使显式 resolver 的值恰好来自环境变量，也不允许 Marivo 依据 provider 类型再次触发默认缓存。持久化策略
由捕获的显式解析模式决定，在 scope 退出后仍然有效，不依赖 `isinstance(EnvProvider)` 或环境开关凑巧关闭。

不提供“自定义失败再试默认链”的组合选项。需要多个宿主来源时，调用者实现一个明确的 resolver，由它自己
定义权威顺序；Marivo 只看见这一个提供方。

### 5.2 嵌套、并发与线程

入口使用 context-local scope，而不是修改进程全局函数或环境。嵌套 scope 中新发起的独立连接操作和新建
runtime 捕获内层 resolver，退出后恢复外层；进入内层不更换已有 runtime 的来源或连接身份。

每次连接操作在调用线程中确定解析来源、项目身份、deadline 与操作取消状态，再显式传到 backend 构建和
deadline worker。已有 runtime 使用自己捕获的来源。不能在 worker 内重新查默认上下文；只加一个 `ContextVar` 而不处理现有
`threading.Thread` 路径是不完整实现。

内部 binding 仅表示捕获的默认来源或 resolver 对象，不引入独立 scope 身份、关闭状态或资源所有权。

同步 Python API 首版只接受同步 resolver。Resolver 必须在有界时间内返回，遵守剩余 deadline 和取消状态；
远端 SDK 的连接/读取 timeout 由 adapter 配置。`deadline_monotonic` 不能原样作为跨主机时间传输，adapter
必须换算剩余预算。异步宿主通过自己的 adapter/IPC 桥接，不由 Marivo 隐式创建事件循环。

context-local scope 只隔离凭证上下文，不宣称解决 Marivo 所有现有 process-current Session 并发限制。
测试必须直接覆盖各自显式持有对象的调用，不能靠共享 `mv.session.current()` 假设整个库已具备多租户隔离。

### 5.3 Runtime 来源绑定与连接获取

Session 与 reader 的连接 runtime 在创建时捕获默认来源或显式 resolver，后续新建连接始终使用该来源，
退出 scope 后仍可继续使用。Session Store 只保留原有分析状态，不保存 resolver、秘密值或可恢复凭证的
通道信息。重新启动进程或恢复 Session 时，新的 runtime 重新捕获调用方当时选择的来源。

Marivo 在 connection service 获取其自行构建的 backend 时，包括命中已有缓存的情况，检查当前是否存在
显式 resolver：若存在且与 runtime 捕获的 resolver 不是同一个 Python 对象，则在连接获取前抛出来源不匹配
错误。默认 runtime 进入显式 scope 也适用此规则。没有显式 scope 时使用 runtime 原有来源，不退回环境变量。
该检查覆盖通过 runtime 获取连接的物化及 timezone 等辅助访问；静态读取和纯 artifact 读取不需要此检查。

这一规则用于避免调用方误以为新 scope 已经替换旧 runtime 的来源。需要更换 resolver 时，使用现有
Session 创建/恢复或 reader 创建入口得到新的 runtime；无需仅因 scope 退出而强制 resume。
同一个 resolver 可以用于多个 scope，不引入 scope ID 或跨 scope 的连接注册表。连接、timezone 和首次验证
等缓存继续由各自 runtime 持有，不在不同 runtime 间共享，也不因进入或退出 scope 自动清理。

`DatasourceConnection.backend` 与 `with md.connect(...) as backend` 继续交出原始 Ibis backend。
交出后的直接执行、已取得的 backend 或 Ibis 表达式的使用，不承诺经过 scope 校验；scope 也不撤销这些引用。
本方案不增加 backend 代理或全执行拦截。宿主需要执行授权或即时撤销时，继续由自己的执行边界负责。

### 5.4 退出与轮换

退出 scope 只恢复外层解析上下文，不关闭连接、Session 或 resolver 客户端，也不取消已经发起的连接操作。
连接继续通过 `with md.connect(...)`、`disconnect()`、service 的既有清理和 `session.close()` 释放。
本次解析 snapshot 和超时后的清理由连接操作负责，不注册到 scope 上。

超时后的迟到解析值不能创建连接，迟到建立的连接必须被关闭，不能进入 Session 缓存或触发秘密值持久化。
Python 不能强杀任意阻塞 callback，因此不承诺不合作的第三方 resolver 线程会立即消失；必须丢弃其迟到结果，
宿主需要硬终止保证时使用进程隔离。不得将“调用返回超时”描述成“后台代码必定已停止”。

每次新建连接重新解析凭证，同一个连接生命周期内使用已经获得的认证材料。凭证轮换不会自动改写现有 driver
连接，也不自动重放 SQL。宿主更新同一 resolver 的材料后，下一次新建连接会重新解析；已有连接按现有
生命周期关闭并重建。需要更换 resolver 对象时创建新的 runtime，不增加热换身份接口。

宿主负责 resolver 服务客户端的生命周期，可以在多个 runtime 中复用它，但必须自行保证并发安全和正确
授权。选择 scope 的存活时间不代表宿主授权有效期；授权过期由 resolver 在后续解析时报告。

## 6. 连接调用链与覆盖范围

新的内部连接流程：

```text
public operation / session runtime
  → select captured runtime resolver or current operation resolver
  → check explicit resolver mismatch at managed backend acquisition
  → load datasource IR from the already bound project
  → group env_refs into CredentialRequest objects
  → explicit resolver OR unchanged default chain
  → operation-local SecretValue snapshot
  → existing engine builder receives connection kwargs
  → existing connection ownership and execution path
```

| 路径 | 必须改动的接入点 | 不应增加的行为 |
|---|---|---|
| `md.connect` | `_connect_internal` 与 deadline worker 传递 binding | 不弹窗、不设置 os.environ |
| `md.test` 与内部 no-persist 测试 | 解析、连接和 roundtrip 共用本次 binding | 不因错误回退来源或回写注入值 |
| `md.inspect`、datasource diagnostics/raw SQL | scoped connection service | 不要求用户先手工 connect |
| semantic preview/source health 等真实数据访问 | reader 创建的 connection service | 不把 resolver 写入 semantic IR |
| analysis observe/其他 materialization | Session connection runtime 与 engine timezone 等内部读取 | 不为每个分析方法增加 resolver 参数 |
| Session resume/cold activation | 重建 runtime 时捕获当前解析来源 | 不从持久化状态恢复旧 resolver |
| 文件 datasource 或无 credential refs 的连接 | 保持普通连接路径 | 不凭空调用 resolver |
| catalog、Help、静态验证和纯 artifact 读取 | 不触发凭证解析 | 不因只是看配置而访问秘密服务 |

现有 Session `backends` / `backend_factory` 保留原有分派规则：先匹配 datasource 的 `backends` override，
再按既有规则使用 `backend_factory` 或 Marivo datasource 构建路径。显式 credential scope 可以与这些
override 共用，不新增互斥错误，也不改变 `backends` 与 `backend_factory` 之间已有的参数约束。

例如 `backends={"local": local_factory}` 可以提供本地 DuckDB，其他 datasource 继续由 Marivo 使用注入的
resolver 连接远端 warehouse。外部 factory 返回的 backend 由调用者负责凭证来源，不应用 Marivo 的解析来源
校验，不为其调用 resolver；连接的使用和清理仍沿用现有 Session 规则。若 factory 自己调用 `md.connect`，
该公开调用按自身捕获的上下文解析，不据此声称 Marivo 控制所有外部 factory。

不将内部的 `build_backend_with_secrets`、`_effective_kwargs` 或默认链实现升级为平行公开入口。凭证注入
走公开 scope，driver 构建继续走既有 engine profile。

## 7. 失败、脱敏与持久化

### 7.1 解析错误

`DatasourceCredentialError`，继承现有 `DatasourceError`，具有受约束的 reason、reference、
datasource、fields，以及安全的 expected/received/repair。Resolver 可以抛出这一公共错误；不返回 None、
空字符串、任意字典或未处理的 secret-manager exception。

| reason | 含义 | 建议修复的拥有方 |
|---|---|---|
| `missing` | 宿主未配置引用 | 宿主 UI 配置后重试 |
| `denied` | 当前宿主授权不允许解析 | 宿主修正权限或申请新的授权 |
| `unavailable` | 凭证服务不可用 | 宿主检查服务与连接 |
| `expired` | 当前操作的授权或凭证材料已过期 | 宿主续期后新建连接 |
| `timeout` | 解析阶段超过剩余预算 | 检查凭证服务延迟与操作预算 |
| `invalid-response` | resolver 返回空值或不符合类型的结果 | adapter 修正实现 |

原始 provider exception 统一包装成安全的 `unavailable`，不把异常 message、payload 或 cause 链带到
用户输出。默认 provider 模式继续使用原有错误；不会因为新增接口给所有旧错误加一层新包装。

`DatasourceCredentialScopeError`，只处理获取 Marivo 构建的连接时，当前显式 resolver 与 runtime
捕获来源不匹配的情况。repair 指向使用原 runtime 的解析上下文，或在所需 resolver 下创建/恢复新的 runtime，
不建议 `export SECRET=...`。scope 退出和外部 backend override 不产生这种错误。

### 7.2 test 与数据库失败

`md.connect` 等直接连接入口传播上述 typed error。`md.test` 的解析失败进入现有失败结果家族，在
`DatasourceFailure.code` 中增加对应的 `credential_missing`、`credential_denied`、`credential_unavailable`、
`credential_expired`、`credential_timeout`、`credential_invalid_response`。沿用现有其他字段与 repair，
无需新增秘密值、provider 详情或宽泛的 context dict。来源不匹配错误在连接获取前直接抛出，不包装成数据库失败。

Resolver 返回成功仅表示取得材料。数据库拒绝账号、网络超时或查询权限不足仍由实际连接/查询层报告；不得
把它们改写成 `credential_missing`。连接测试通过只证明这次基础连接往返，不保证后续查询权限。

宿主据此区分“需要用户配置”“宿主访问被拒”“服务不可用”和“材料已取得但数据库拒绝”，决定等待、续期、
修改或排查。Marivo 不在这些分支自动等待人工输入，不无限重试，也不代表用户发消息。

### 7.3 秘密值传播与展示

显式解析得到的值只能进入本次可信 engine adapter 和 driver 所需的连接参数；不进入 datasource IR、
Session Store、Artifact、Finding、query provenance、事件参数或普通对象 repr。公共错误与正常输出只保留
引用身份、阶段和安全分类。

实施前 `ResolvedSecret` 默认 dataclass repr 可展开 value；实现已收敛相关内部对象的 repr 与跨模块
传递，不能只给新公开包装类加掩码。Driver exception 可能包含认证材料，连接建立时使用本次解析值执行
exact-value redaction；后续 Marivo 受管查询使用 backend 持有的脱敏材料，并抑制原始 traceback/cause。
这些材料仅随连接存活，不是跨连接凭证缓存，不用于省略新连接的解析，也不由 credential scope 回收。

Marivo 不承诺控制调用者主动打印 `SecretValue.reveal()`、交出的原始 backend 的直接输出或异常、任意外部
driver 日志、内存 dump 或调试器；这些属于可信执行环境的治理。测试必须证明 Marivo 自己拥有的正常展示、
错误、日志和持久化路径不泄露。

显式 resolver 模式禁止读取和写入本地秘密缓存，即使缓存已经存在、权限不正确，或 `MARIVO_PERSIST_CREDENTIALS`
没有设置，也不接触它。值来自宿主这一事实不被持久化为可恢复服务访问的地址、token 或对象引用。

## 8. 宿主集成与跨进程传输

### 8.1 同进程 Python 宿主

宿主创建实现 `CredentialResolver` 的 adapter，预先绑定其允许访问的项目、datasource 或服务账号范围。
每次 `resolve` 校验 Marivo request 与已授予范围，再调用宿主只读接口，将字符串封装成 `SecretValue`。
SDK client、连接池和宿主 token 由 adapter 自己持有，不通过 datasource 配置或公共 Request 字段传递。

Marivo 只依赖 Protocol，不依赖 DSH、Vault、系统钥匙串或云 secret-manager 的 SDK。具体接入包归宿主或
独立 adapter 所有，不为了第一个宿主在 Marivo 放专属条件分支。

### 8.2 Node/远端宿主与 Python 子进程

resolver 不会自动解决跨进程问题。宿主仍须选定自己的传输及权限机制：

| 方式 | 用途 | 必须保持的边界 |
|---|---|---|
| 受控 stdin/继承管道传递一次性 snapshot | 已知本次操作全部引用的短命 Python 子进程 | 宿主先授权；值不在 argv/日志；进程内构造只读 resolver，操作后结束 |
| 受限本地 IPC / broker adapter | 需要按实际连接需求读取或续期的宿主 | 每次请求验证当前 operation 授权；身份凭据不放进模型工具参数 |

Marivo 不标准化这些 wire schemas，不在首版实现 broker server，也不要求网络访问才能使用 resolver。
跨主机绝对路径和单调时间不能直接当作对端权威；adapter 用已绑定会话映射项目身份并传递剩余超时预算。

受控管道 snapshot 是宿主传输选择，不是 Marivo 新增 credential cache。宿主选择该方式时必须明确 snapshot
有效期；它不具备自动轮换能力，也不能被存成 Session 的可恢复对象。

### 8.3 对 DSH 插件的作用

DSH Credentials 继续唯一保存值；插件把已授权 execution 的引用映射到 DSH 存储，再经受控 adapter 交给
Python 中的 resolver。Marivo 本身不需要知道 `DSH_DATA_ANALYSIS_CREDENTIAL_<HEX>` 或 Shell lease marker。

这可以让受控 Python bridge 免于把秘密值放入环境变量，但不会取消插件的 Agent/Workspace/operation 授权
和 lease 边界。任意 Agent 生成的 Shell/Python 脚本如何进入可信 credential scope，由插件明确接入；不能
因为 Marivo 增加一个 context manager，就宣称所有既有脚本已经自动改走 resolver。

DSH 弹窗等待、保存、更换、删除与“提交后继续”仍由插件实现。Marivo 缺失结果是该流程的输入，不承接
人工等待。现有插件设计见
[Marivo 凭证管理方案](../../../../dsh-data-analysis/docs/plan/marivo-credentials-design.md)。
本方案的实施只改变 Marivo；DSH 源码不在需要修改的范围。

## 9. 默认行为、Help 与交付边界

首版是执行能力新增，不是 datasource schema 迁移。保留既有 `*_env`、默认 env/cache 链和非注入 Session
用法，是明确保留的产品行为，不增加新旧字段双读、兼容别名或数据库迁移。显式 scope 的失败不回到默认模式。

新增的 canonical Help target 为 `datasource.credential_scope`，解释 resolver、Request、SecretValue、
作用域规则和最小实例；类型与错误通过所属 focused target 渐进披露，不把所有实现类型平铺到根 Help。
`datasource.connect/test/inspect` 与相关 analysis Session 帮助只链接这一权威入口，不复制整套说明。

默认缺失错误可以继续建议环境配置；显式 resolver 缺失错误必须指向宿主操作。两种指导取决于实际捕获的
执行模式，不依赖模型猜测当前是否运行在 DSH 内。

实施时同步 `__all__` 快照、typed signatures、native Help registry、reachability/budget 测试、datasource
与 analysis 当前文档，以及 `site/.../latest/` 英中示例；不改历史 release 文档。内部 `test_no_persist`
若继续存在则必须消费相同 binding，不因本方案额外公开第二个凭证测试入口。

## 10. 实施顺序与验收

### 10.1 实施顺序

1. 引入 Request/SecretValue/Resolver、typed errors 和 scope 实现，建立默认/显式模式的独立测试。
2. 将连接构建统一接入 operation binding，覆盖 manage、deadline worker、scoped service 与 engine builders。
3. 覆盖 semantic 与 analysis runtime 的来源捕获、连接获取校验与既有缓存，保留连接清理和 override 分派规则。
4. 完成脱敏、禁止注入值缓存、错误结果 code 和 Help 的全链同步。
5. 用真实 Python 程序及 host adapter fixture 验证完整调用路径，再交给具体宿主集成；不把直接调用内部
   `secrets.resolve` 成功当作公开能力已完成。

### 10.2 验收矩阵

| 编号 | 场景 | 必须成立 |
|---|---|---|
| R01 | 未注入 resolver 的独立操作或 runtime | 原环境变量与本地缓存行为保持 |
| R02 | 显式 resolver 的同名环境/缓存 canary | 只使用 resolver，默认 canary 从不读取或写入 |
| R03 | resolver missing/denied/unavailable/expired | typed failure，绝不 fallback |
| R04 | 空值、错误类型、原始 SDK 异常 | 安全失败，异常 message/cause 不泄露 canary |
| R05 | `md.connect` / `md.test` | 公开入口真实调用 resolver，不依赖 monkey patch |
| R06 | `md.inspect` / diagnostics / semantic source health | 内部连接同样走显式 resolver |
| R07 | analysis materialization 与 timezone 等辅助连接 | 无旁路默认解析，缓存身份一致 |
| R08 | 同一引用用于多个字段 | 每次新建 backend 只解析一次，字段使用一致 |
| R09 | 不同 resolver 下创建 runtime；旧 runtime 进入另一个显式 scope | 各 runtime 的来源与缓存独立；Marivo backend 获取在来源不匹配时失败，包括默认 runtime 与缓存命中 |
| R10 | 嵌套 scope 与异常退出 | 内层不接管旧 runtime，退出只恢复外层，不自动关闭连接或取消已发起的操作 |
| R11 | deadline worker 与线程关联 | 捕获的显式 binding 正确传递，不在 worker 回到默认链 |
| R12 | callback 超时或返回迟到结果 | 不发布连接、不触发持久化；迟到连接被关闭 |
| R13 | 退出 scope 后 Session 继续物化或新建连接 | 继续使用捕获的 resolver，不 fallback；资源仍由 session.close() 释放 |
| R14 | 在另一个 resolver 下创建/恢复 runtime；同一 resolver 用于多个 scope | 新 runtime 捕获所选来源，不共享旧 runtime 缓存；同一 resolver 对象不因 scope 不同而冲突；Store 没有 resolver/secret |
| R15 | credential 轮换 | 新连接 fresh-resolve；已有连接不偷偷换身份或重放 SQL |
| R16 | 显式 resolver 加本地 backend override 或 backend_factory | 分派规则不变；外部 backend 不额外解析或做来源校验，Marivo 构建的远端连接使用 resolver，清理沿用现有规则 |
| R17 | 无引用 datasource、静态 catalog/Help、artifact 读取 | 不产生不必要 resolver 调用 |
| R18 | repr/show、错误、日志、telemetry、Store/Artifact | canary 值及秘密摘要均不泄露 |
| R19 | 受控子进程 adapter fixture | 不用秘密环境变量/argv，也能完成实际公开连接与测试路径 |
| R20 | 数据库认证拒绝与网络/查询失败 | 不被误报成 credential missing；阶段与 repair 保持真实 |
| R21 | `.backend` 与连接 context manager 交出 backend | 保留原始 backend，scope 进入/退出不拦截其直接执行；连接自身的 context manager 或 disconnect() 负责释放 |

矩阵中的失败、取消和并发用例使用确定性 barrier/fake clock 或受控 worker，不通过随机 sleep 猜测顺序。
成功路径至少通过一个真实 engine 与本地 fixture 服务执行连接往返；远端账号由环境条件决定，未运行的
real-environment 验收必须明确标记，不以单元测试替代。

测试使用仓库 entrypoints 或 `.venv/bin/...`，先收窄到 credential、datasource、semantic 数据访问和
analysis runtime；实施改变共享连接路径后再运行规定的 broad check。文档变更本身只检查链接、渲染和
`git diff --check`，不声称接口或运行时已实现。

## 11. 明确不承诺的能力

- provider 访问权限的自动推导、对恶意 Python 代码的秘密值隔离、Python 字符串的可靠擦除。
- 不合作 callback 的线程强杀、跨系统全局原子读取、已建立数据库连接的即时权限撤销。
- 自动 UI 输入、Host 凭证管理、异步 resolver API、所有 secret-manager SDK 的内置适配。
- 持久化 scope、跨进程自动恢复 resolver、自动重放失败查询或旧 Agent 工具调用。
- backend 代理、交出原始 backend 后的执行授权校验、scope 统一资源注册与退出回收。
- 完整的 Marivo 多租户并发改造，或把环境变量方案在此次设计中立即全面删除。

该能力的完成标准是：宿主可以通过一个公开作用域，将自己授权的只读凭证解析接入 Marivo 构建连接的各条
路径，保持秘密值不落项目状态、runtime 来源不隐式切换，并保留现有连接生命周期与外部 backend 分派规则。
