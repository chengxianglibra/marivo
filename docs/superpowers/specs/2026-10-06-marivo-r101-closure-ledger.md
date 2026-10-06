# R10.1 全库与公共契约收口

基线：`panda` / `61e5a4eed5817c58215d27ca79cee002a0dced7d`。范围只包含 R10.1；候选是未提交的工作区。状态：R10.1 已完成；资格只在本包实际门禁范围内。

## 有限能力去向

每行的“保留”表示当前契约的能力及 owner 保留，不授予新的后端、物理 producer、安装包或 Agent 资格。历史资格继续由原 R0–R9 记录拥有。下列验证入口与 baseline 函数身份清单分开管理。

| ID | 当前唯一 owner / 公共入口 | 旧入口、注册、编译、codec 与消费者去向 | 当前验证入口 |
| --- | --- | --- | --- |
| C01 | `datasource/adapters.py`、`capabilities.py`、`manage.py`；typed source / `md.raw_sql` | 保留六 provider、精确用途的 provider statement 和终端 SQL；R9.5 manifest 只移除退役 owner，不扩大批准规则 | `test_r95_sql_audit.py`、`test_r95_driver_audit.py`、`test_datasource_adapter_contract.py`、`test_datasource_raw_sql.py` |
| C02 | `semantic/validator.py`、`metric_graph_lowering.py`、Event / StateModel / calendar declarations | 保留 authoring、Ref、静态推导、受限函数体与当前 source validation；不借旧 Dataset 注册初始化 semantic authority | `test_semantic_r21_identity_relationship.py`、`test_semantic_r22_metric_graph.py`、`test_semantic_r23_business_order.py` |
| C03 | `Session.members`、`graph_members.py`、`public_dsl.py` | 删除 `Session.population`、Population L/M 家族、base/family registry、专属发布与恢复；身份描述符保留 | `test_analysis_members_r52.py`、`test_analysis_state_r101.py` |
| C04 | `AnalysisDomain.observe`、`graph_observation.py`、`runtime_metric.py` | 删除 `Session.observe`、Metric L/M 家族及其多指标容器/输入 codec；保留单量与 closed RuntimeMetric 构造 | `test_analysis_observation_r53.py`、`test_lazy_computation_runtime.py` |
| C05 | typed `group_by/summarize/rollup`、`graph_relation.py`、method registry | 删除旧 `aggregate/drop_dimensions`、aggregation/rollup/fold 注册；原量部件与当前行统计仍由各自方法拥有 | `test_analysis_coordinates_r54.py`、`test_analysis_numeric_r56.py`、`test_lazy_computation_rollup_runtime.py` |
| C06 | `TimeGrid`、`each`、`compiler/source_time.py`、`materialization/temporal_sql.py` | 删除 `with_time_axis`、v6 temporal compiler/harness；保留共享 native parse、时区、精度、完整格与累计状态 oracle | `test_lazy_temporal_parsing.py`、`test_lazy_temporal_backends.py`、`test_analysis_temporal_r55.py` |
| C07 | typed relation compare / bound field predicates、`core/predicates.py` | 删除 unbound AnalysisPredicate 和 standalone `gt/eq/...`、旧 Dataset fields/predicate compiler；不提供 alias | `test_analysis_comparison_runtime_r62.py`、`test_analysis_predicates_r63.py` |
| C08 | current cohort、reference weights、row methods、ranking/table | 删除通用 Dataset rank/limit/row 容器与消费者；保留主体映射、完整键域和终端 table | `test_analysis_cohort_r63.py`、`test_analysis_references_r64.py`、`test_analysis_display_r65.py` |
| C09 | Difference.attribute、`graph_attribution.py`、methods attribution | 旧 Attribute/Delta Dataset compiler/codec/registry 退出；共享数学与 allocation/Other/完整部件仍保留 | `test_analysis_attribution_r66.py`、`test_analysis_attribution_runtime_r66.py` |
| C10 | current direct distribution observation、numeric methods / state | 删除旧 distinct/distribution Dataset compiler与专属 v6 harness；保留独立 distinct/quantile、精确数值、direct-only continuation 拒绝 | `test_analysis_numeric_r56.py`、`test_analysis_numeric_review_r56.py` |
| C11 | `Session.events`、`graph_journey.py`、matching methods | Event 初始化直接消费 ObservationOwner；不再经 LazySources 的 Population/Metric 注册 | `test_analysis_journey_matching_r73.py`、`test_analysis_funnel_r74.py`、`test_lazy_public_relationships.py` |
| C12 | Journey time-to-event/select-subjects 与 graph fields | 保留 current typed duration/subject mapping；无 EventDataset 转发或旧 family codec | `test_analysis_funnel_r74.py`、`test_analysis_numeric_r56.py` |
| C13 | `Session.lifecycle`、`graph_history.py`、lifecycle methods | 保留 replay/history/状态读取；移除旧家族资格断言与 registry 消费；共享 Event semantic/data builders 保留 | `test_analysis_lifecycle_r75.py`、`test_analysis_history_r76.py` |
| C14 | relation deviation/runs/correlate/forecast、current statistics methods | R8 已退役家族保持退出；旧 Population/Metric 的统计消费者不再存在；current Finding subject/derivation codec 只接受 graph variant | `test_analysis_statistics_kernel_r84.py`、`test_analysis_disclosure_r85.py`、`test_lazy_finding_types.py` |
| C15 | Session、DatasetRuntime、SessionStore、graph_store/graph_protocol/graph_storage | 唯一 Store 7；移除 generation 6 selector/schema/分派、旧 run/descriptor codecs与 writers；Artifact 返回 PublicMaterialized；revalidate 三轴独立核验 | `test_analysis_graph_publication_r44.py`、`test_analysis_state_r101.py`、`test_lazy_runtime_concurrency.py` |
| C16 | native Help、concrete exports、current contract/actions、structured errors | 182 exports、517 native descriptors；删除 family disclosure/shadow registry；latest 双语与 API reference 同步；typing 同时拒绝旧 Session/exports | `test_public_surface.py`、`test_lazy_disclosure.py`、`test_agent_api_drift.py`、`typing/analysis_retirement_r101_contract.py`、`test_cutover_documentation_examples.py` |
| C17 | ontology、project/config/secrets、doctor、telemetry、CLI / install scripts | 保留独立扩展与辅助能力；旧 v6 专属诊断和 harness 退役；安装工具的有效声明/权限断言保留；安装旅程消费者更新不构成安装资格 | `test_ontology_extension.py`、`test_project.py`、`test_config.py`、`test_doctor.py`、`test_telemetry.py`、`test_install_marivo_script.py` |
| C18 | Session.anchors、graph_anchors/graph_retention、current methods | 保留相对窗口、固定 Ω、Unknown 与主体量词；只解耦 semantic owner 初始化，不扩大物理或 producer 资格 | `test_analysis_anchors_r77.py`、`test_analysis_retention_r78.py`、`test_analysis_retention_r78_evidence.py` |

## imports、调用、注册与旧测试的反查

[退役模块清单](2026-10-06-marivo-r101-inventory/retired-modules.json)逐项记录 66 个旧模块的基线 SHA-256、原定义、原 imports 消费者与当前退役检查入口。它包含生产实现、disclosure/registry/codec、测试及工具消费者，不能通过模块名称中的 `Dataset` 字样推断退役。

[基线函数索引](2026-10-06-marivo-r101-inventory/baseline-tests-index.json)保留 4,224 个原测试函数的身份、原 body hash / imports、当前去向及 current hash；参数化展开后的执行数量另列。旧实现断言的退役与业务 invariant 的转移分别记录。[invariant groups](2026-10-06-marivo-r101-inventory/invariant-groups.json)绑定保留的当前验证入口，不宣称测试函数数量或实现形状一一等价。

保留的独立约束包括：完整复合键和空组、二种归约的差异、精确 Decimal/int64 与 2**53 边界、distinct/quantile direct-only 拒绝、业务顺序/DST、原量 state 部件、跨 Session、损坏 receipt/descriptor/evidence、取消/原子失败、original transaction/lost acknowledgement、source 重求值、fixed exact hit 和断源新进程。新 R10.1 revalidation 测试独立检查 Artifact/存储/Evidence 三轴、只读数据库与 secret canary。

旧 computed-measure Dataset 路线没有当前 graph source 资格；其 51.50、18.00/3.25/14.75、8/2/2/6 和 36.5 oracle 转为 Semantic bounded preview 检查，不能冒充 Analysis source 通过。原 v6 Decimal-mean 拒绝由当前 R5 numeric state 取代；54.14 / 3 的 scale-6 结果为 18.046667。冷恢复保留 54.14、3.85、3 和 5/3；Pandas adapter dtype 的旧 Arrow 包装断言不作数值类型 authority。

旧按时间粒度 coarsening 的专属 Dataset 调用退役。latest 日/小时例子保持原日期范围，以明确的 daily grid 再次 source observation；不声称 hourly Artifact 获得尚未批准的 time-grid coarsening K。

## 实际验证与边界

实际命令、候选身份、结果、日志 hash 与 SQL/package 索引在 [R10.1 验证记录](2026-10-06-marivo-r101-validation.md)。未通过的迭代日志保留在本地 evidence 索引中，最终通过只绑定最终候选。

两份 packaged semantic/analysis skills 与 AGENTS.md 保持基线原文。没有提交、推送或发布。R10.2–R10.5、隔离安装、同包完整旅程、真实 Agent、完整 Runtime 和 release-check 未执行。R9 的 15 个授权成本跳过与 V17 内 4 个未验证 producer 绑定原样保留；历史工程通过和原 owner 证明不提升为当前完整执行资格。
