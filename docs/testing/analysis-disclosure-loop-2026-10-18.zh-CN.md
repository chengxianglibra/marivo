# Analyze Skill 与 Live Help 渐进披露优化 loop

## 目标与责任

从一个决策所需的信息出发，减少重复披露和无效探索，保留足以执行、修复和解释的契约。
电商数据用于检验通用机制，不决定产品中的分支、术语或 API。

| 信息所有者 | 回答的问题 | 不应承担的责任 |
| --- | --- | --- |
| `marivo-analysis` Skill | 此刻缺什么证据、为什么继续、何时停止、何时交还语义工作 | 签名、字段清单、固定调用配方 |
| Live Help 根与意图导航 | 不知道能力时，从哪里找到下一项相关契约 | 展开全部接口或替 agent 选择业务方法 |
| 聚焦 Help | 这个调用需要什么、返回什么、有何约束和副作用 | 证明当前数据或执行成功 |
| Population / Query contract | 当前逻辑对象捕获了什么、可机械续接什么 | 读取来源、保证业务适用性或执行能力 |
| Artifact / Finding / Evidence | 已提交什么事实、范围和限制是什么 | 恢复计算图、证明当前来源新鲜度或因果 |
| 结构化错误 | 本次实际失败是什么，如何修复 | 从失败自动切换到绕过治理的计算 |

## 迭代方法

1. **基线**：记录源码身份与原有工作树；分工审查 Skill、Help 链路，另用隔离状态验证真实电商数据。
2. **收敛问题**：只接受能用公开入口重现的缺陷或有明确决策影响的责任重复；先确认拥有者，再做最小修复。
3. **反例与验证**：产品测试使用通用小数据；电商验算使用独立基线。覆盖新调查、已有逻辑对象、终端阅读、修复、精确恢复。
4. **交叉复审**：非实现者审查改动；验证者重新运行受影响的数据路径。发现问题则回到第 2 步。
5. **停止**：已确认问题关闭、公开导航和边界检查通过、真实数据吻合、交叉审查无未解决问题。新一轮只因新证据启动，不以增加代码或测试数量为目标。

## 通过条件与证据边界

- 每条新增或改动导航通过公共 `marivo.help(...)` 可达，根保持有界；类型、成员与实例不混淆。
- Help 与逻辑 contract 不读业务数据、不创建 Run；终端结果不被导向不存在的 contract。
- 聚焦示例和错误修复可执行；契约相关回归覆盖通用反例，不匹配 Skill 的偶然措辞。
- 电商结果按完整键、NA/presence、数值与排序核对独立基线，并保存 Session/Artifact 身份。
- 修改的 Python 模块通过 typing；共享披露修改完成 `make check-agent`；站点英中同步并验证内容。
- 区分确定性测试、真实本地 DuckDB 数据、模型自主探索、远端 Runtime 与 release；本轮未运行的层级不宣称通过。

## 本轮记录

- 起始 HEAD：`8569d0706a`。原工作树有三份未跟踪的电商验收文档，保留原样。
- 团队：主 agent 负责契约与实现整合；Help 审查 agent；Skill 审查/修改 agent；电商验证 agent。
- 初始假设：保留现有单一公共 Help 协调器和 owner registry，不增加导航 API、Skill 附件或场景专用能力。

### 第一轮：职责与入口修复

| 发现 | 通用修正 | 验证方式 |
| --- | --- | --- |
| 根、entry、methods 将所有现有对象导向 contract | 明确 Population/Query 与终端 Artifact 的不同读取入口 | 真实 Artifact 阅读与无源冷读；已有逻辑对象 contract 回归 |
| Metric 意图页无法直接找到 observe | observe 的唯一 discovery owner 改为 methods.metric；entry 保留交叉链接 | 公共根→methods→metric→observe 路径及唯一归属检查 |
| Catalog 将表达式当成有 show 的条目 | 区分 collection 发现、describe 定义元数据与 get/require 执行输入 | Metric、Field、Entity 公开流程，禁止打开或执行来源，零 Run |
| 聚焦示例重复 import | renderer 仅补齐 owner 示例缺少的标准前导行 | 所有 callable 示例 AST 检查，无重复 import |
| Skill 重复构造、finalize、版本缓存细则 | 保留分析判断、语义权责与自动 handoff，精确规则交回 Help | packaged shape 与公开链接解析；独立 agent 试验 |

### 第二轮：交叉审查与修复复验

审查发现 Catalog 聚焦叶子仍把执行表达式标为 `immutable_metadata`。
collection 返回类型现从语义 member contract 派生，require/readiness 从语义原 registry
复用 output family，避免新建第二份类型事实。Skill 去掉对 contract 展示完整
“pending checks”的过度承诺；实际 contract 不枚举全部后端或来源检查。

实现过程中曾误用 `SemanticCatalog.readiness` 作为注册目标，引起新进程导入失败。
已改为实际 canonical `readiness`，保留失败证据后重跑；该暂态失败不计为通过。
修改冻结后的四份聚焦 surface 测试共 **24 passed**，相关 lint、import contracts 和 typing 通过。
交叉审查无未解决 actionable finding。

### 电商数据复验

使用原 demo 的隔离副本，23 张表内容指纹通过，原项目保持无修改。
基线与优化后各发布 8 个 Artifact、62 行结果；各通过 180 项独立 SQL 数值检查，
并精确检查两组 Top-3 排序与 48 个小时坐标。8 份优化前后导出逐字节一致。
48 个小时中的 12 个缺失值仍是 NA。原始比率汇总与当前结果行均值按各自含义计算，
没有把均值当成总体比率。

两轮各完成全部 8 个 Artifact 的新进程精确冷读；隔离副本的模型和数据不可用时，
保存的行、NA、顺序、Evidence 仍可读取。跨 Query 的 Column 被结构化错误拒绝，
改用本 Query 的 Column 后成功。优化后的 40 个实际 contract action Help 目标全部可解析。

完整脚本、事件、独立基线、精确身份及暂态失败记录见
[电商证据报告](../../evidence/analysis-disclosure-loop-2026-10-18/ecommerce/REPORT.md)。
这些是源码开发环境中的控制器复验；两轮未分别冻结源码快照，不能将其视为严格的安装产物 A/B。
源码 venv 使用 DuckDB 1.5.3，与旧报告的已安装 wheel 环境不同。

### 第三轮：独立 agent 新问题试验

不读取上述控制脚本，由 agent 依据 Skill 和公共 Help 独立分析东区订单及渠道净收入。
先读取 149,867 条订单属性再派生 Query 群体的分支在 62.82 秒时被手动中断，未产生该分支的 Artifact。
改用已支持的 `session.members(..., where=...)` 表达同一个完整东区群体后，
执行含准备共 2.19 秒，得到 37,467 单、23,216,551.25 元，五个渠道计数和金额均与独立 SQL 一致。
这是并发负载下的单次观测，不是受控性能比较；三单显式身份控制题单独记录，不替代完整问题。

试验发现：逻辑卡片和已保存 read 事实未清楚披露成员选择条件。
read 的 `filters` 只描述来源 stage 自身谓词，不能拿它证明整个 Query 没有成员限制。
此项已完成通用披露修复：共享有界摘要覆盖成员条件、完整坐标域及 Query 输入变换，
执行时保存独立 declaration，保留精确定义 fingerprint；不保存可执行图，不改变 read-local filters。
交叉审查找出的坐标域、PopulationCondition 两处遗漏均补齐，并由不读来源的反例覆盖。
Population 卡片去掉冗长的内部 dataclass/version repr，保留轴、角色、路径、锚点与 Session 身份。
现有上下文预算保持原阈值并通过；没有通过放宽预算接受新增文字。

真实全量东区问题再次通过独立 SQL。完全没有 models/data 的新进程重开了相同范围声明、
Evidence digest 与全部输出。过程、原始失败、精确 ID、脚本和最终复审见
[独立 agent 试验](../../evidence/analysis-disclosure-loop-2026-10-18/independent/trial-report.md)及
[范围披露复审](../../evidence/analysis-disclosure-loop-2026-10-18/independent/scope-review.md)。

附带发现并修复了一个已支持路径的依赖闭包缺陷：成员的标量谓词经关系读取另一个 Entity 时，
原准备代码跳过 RowExpression 的依赖收集，泄漏 KeyError。
修复仅调整通用依赖收集条件，单跳、双跳身份集合与数值回归通过；相关两模块 **40 passed**。
未增加业务分支或公共 API。独立 trial 中较慢的 Query 派生群体路径仍未完成验收，不将其写成通过。

最后的公开反例使用六个含长 Unicode 键的有限域：按字符限额会使摘要超过终端字节预算，
挤掉机械续接。共享摘要现按 UTF-8 字节截断，明确标记省略，所有 action 保持可见。
新增回归通过；最终 helper 下独立完整电商题及无 models/data 的新进程恢复再次通过。

### 最终证据与边界

- 最终源码 `make check-agent`：**3,725 passed**，287 个源码模块 typing、lint/import contracts、API 文档通过。
  默认测试产生 2,509 条 warning；不以默认测试代替 Runtime 或 release 验收。
- 最终八产物复验：180 项数值、3 项精确坐标/排序、40 个实际 Help action 目标和 8 次无源冷读通过；
  8 份 CSV 与上一轮完全一致。contract 与保存的 declaration 都含有范围事实。
- Unicode 字节限额修改后，独立完整题重新执行并与 SQL 核对，新进程再次恢复同一声明和输出；
  最终 helper SHA256 为 `c7ed7005544894dc2f3cee36e62684e5eb90191ec65223c0a022fa3dbe41be04`。
- 确定性上下文预算原阈值通过。新增 scope 的字符测量不等价于模型 token 或首次任务完成率。
- 站点内容 381 文件通过；Astro 检查 0 error / 0 warning，308 条 docs 路由、44 个 latest 页面、343 个内部目标通过。
- 独立 agent 新题是本次团队的实际使用观测，不是隔离模型矩阵或统计效能评估。
  未运行远端 datasource Runtime、全量 slow/release 或 installed-wheel 验收。
- 本轮仅作本地提交，不推送；原三份电商验收文档、AGENTS.md 和 packaged semantic skill 未修改。

门禁日志保存在 [check-agent](../../evidence/analysis-disclosure-loop-2026-10-18/gates/check-agent.log)、
[上下文预算](../../evidence/analysis-disclosure-loop-2026-10-18/gates/context-budget.log)与
[站点构建](../../evidence/analysis-disclosure-loop-2026-10-18/gates/site-build.log)。
三轮均以实际缺陷、反例和非实现者复审收敛；剩余上述验证边界已明确记录，本轮结束。
