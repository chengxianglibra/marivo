# R10.1 验证记录

基线 `panda` / `61e5a4eed5817c58215d27ca79cee002a0dced7d`。状态：**R10.1 已完成**，交付为未提交工作区。
此记录只拥有 R10.1 工程、公共契约、受影响本地 Runtime 与包内容证据。

## 候选与有限索引

[候选索引](2026-10-06-marivo-r101-inventory/candidate-index.json)绑定 HEAD、分支、未提交代码 diff、新增文件、依赖、767 个 Python 文件的分片 hash、330 个产品代码/资源文件及受影响文档/门禁配置。
代码内容摘要为 `800f7732299444909111694aeb61fdc287daae56c790ddd41f7d28792b9140bc`；未提交代码 diff 摘要为 `dc66b19f1366bf3972042ff4a21b97b63c62e4c9e01ab988535c3fef17279f78`。验收文档和日志索引在执行后写入，不纳入循环候选 hash。

[有限收口台账](2026-10-06-marivo-r101-closure-ledger.md)逐项绑定 C01–C18；基线函数分片保留 4,224 个原身份：3,264 个原 body 保留、93 个当前消费者更新、867 个旧实现断言退役。它不声明函数数量或实现形状一一等价，也不授予运行资格。
当前分析公开面为 182 exports、517 native descriptors；66 个专属旧模块退役。

## 实际门禁

完整命令、退出码、日志 hash/大小与归属在[证据索引](2026-10-06-marivo-r101-inventory/evidence-index.json)。所有下列最终门禁退出码为 0。

| 命令与范围 | 实际结果 | 资格边界 |
| --- | --- | --- |
| `make check-agent` | 790 文件格式、lint/imports 通过；333 文件 typing 通过；**5,044 passed / 1 skipped**；API docs 通过 | 完整默认工程门禁，含 exports、Help、预算、漂移、CLI、工具、SQL、正反 typing 与保留业务/故障断言 |
| 定向 `make runtime-test`，11 个受影响模块，见证据索引实际 TESTS | **61 passed / 9 skipped** | 本地 DuckDB / Parquet / fixed / 新进程冷恢复；包含完整数值、键域、跨 Session、损坏部件、原子失败、当前结果协议及 16 个实际文档运行用例 |
| Lifecycle 全 trace、Anchor overlap/fixed、Retention Ω 的三个测试节点 | **6 passed** | Event/Lifecycle/Anchor 初始化保留；执行时产品及 owning test hash 与最终候选相同 |
| 两个审计脚本的 explicit-package-bases typing | 2 文件通过 | 审计工具 typing，未授予执行能力 |
| exports/retirement/Finding 定向回归；候选冻结定向复验 | 49 passed；14 passed | 均再次被完整工程门禁覆盖，不重复累加资格 |
| `.venv/bin/python -m scripts.r101_closure_audit` | 4,224 身份、66 模块及当前 owner 通过 | `finite_static_disposition_only` |
| R9.5 SQL AST 全库审计 | 328 生产文件、259 个提交/编译/解码调用点、62 个精确 manifest 项；**未批准点 0** | `static_ast_inventory`；provider/query-control/Store/raw SQL 用途仍按原 owner；不声称新的远端执行 |
| `site/`: `npm run verify:content` / `npm run build` | 343 必需文件；Astro check/build；321 页面及中英文安装脚本输出通过 | latest 双语代码一致；当前生成 API 已纳入最终 build |
| `.venv/bin/python -m build --outdir /tmp/marivo-r101/delivery-dist` / contents / twine | wheel/sdist 构建及 metadata 通过；330 当前代码/资源精确一致、66 退役路径缺席 | **archive_contents_only**，未安装候选包 |

唯一默认跳过来自 `test_datasource_metadata_schema_only.py:81`：owner 把 channel failure 交给 dispatcher fallback。9 个 Runtime 跳过来自未 opt-in 的 PostgreSQL/MySQL/ClickHouse/Trino 服务；它们没有通过资格。

当前 wheel 为 `marivo-0.5.3.dev0-py3-none-any.whl`，SHA-256 `81570760e8fa4d74e32ce63b7b547f357f264da985585f40770a9f1096ec7044`；sdist SHA-256 `3a82457b5e598a6a83f211c7dda86cf48f982230f6ba87884419ca38a8f912cf`。
现有 `.venv` 的 marivo distribution metadata 为 `0.5.5.dev0`，仅记录为环境输入。测试读取工作区代码；它既不是本次 wheel 的安装来源证明，也不要求在 R10.1 更改版本。

## 独立 oracle、修复与证据保留

source 重复执行产生新身份；fixed exact hit 保持 Run 数；断源新进程恢复完整 A/B/C/D 键及 60/120/0/0、总计 180，且没有 source submission。Artifact、storage、Evidence 三轴各自核验并保持 SQLite 原文。原 graph publication tests 继续拥有跨 Session、primary/part 损坏、资源、原子失败和 lost-ack 断言。

旧 computed-measure 算术 oracle 保留为 Semantic bounded preview；graph 仍在业务读取与 Run 前明确拒绝未获资格的 computed measure。冷恢复保留 54.14、3.85、3 和 5/3，当前 scale-6 mean 为 18.046667。它们不能被包内容或静态台账替代。

迁移后的示例在真实公共 API 执行：Jan–Mar 10/20/30、Q4 10/20/30 与三组实际 month/region 元组、July 渠道 77、无时间限制 web 927 / mobile 249，以及同一 July 1–3 业务范围的 48 小时格和 2 日格，总计 660。daily 是明确的新 source observation。地区/渠道需在 observe 保留 coordinates；Q4 示例显式 UTC。完整键域按实际完整元组 union 判定，不补造 month×region 笛卡尔积。

迭代的失败与修复日志全部保留本地，包括初始旧调用/时区/fixture 问题，以及新增审计脚本和运行中候选冻结交叉造成的 `candidate drift: files`。稳定候选的完整复验通过；没有通过修改 R9 资格记录来隐藏失败。原始日志和构建产物在 ignored `evidence/r101/final-01/`，tracked 索引保存路径、hash、大小及用途；新测试不依赖本地证据目录。

## 出口与未执行范围

退役公共链、v6 schema/读写/generation selector、专属注册/codec/编译和发布消费者已退出，没有 alias。Session 的 semantic/source/Event/Lifecycle/Anchor 初始化直接绑定当前 owner；Store 7 是构造和只读打开的唯一代际，旧项目结构化拒绝且保持原文件。独立辅助工具、ontology、Evidence 与现行共享数学保留；公开类型、原生 Help、动态 guidance、API 与 latest 双语文档一致。上述相关最终门禁通过，登记 R10.1 完成。

AGENTS.md 与两份 packaged semantic/analysis skills 保持基线字节。未提交、推送或发布。R10.2–R10.5、隔离安装、同包完整旅程、真实 Agent、完整 Runtime 和 release-check 未执行。R9 原 394 IDs、379 有限 owner-proof 绑定、15 个 authorized cost skips 与 V17 四个未验证 producer 绑定不变，不提升资格。
