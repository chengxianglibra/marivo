# Marivo 开发规范

## 代码风格

### 基本规范
- 遵循 PEP 8 Python代码风格指南
- 使用 Ruff 进行代码格式化和linting
- 行长度限制：100字符
- 缩进：4个空格
- 使用类型注解（Type Hints）

### 命名规范
- 类名：`PascalCase`（例如：`SemanticService`, `QueryRouter`）
- 函数/变量：`snake_case`（例如：`create_entity`, `table_name`）
- 常量：`UPPER_SNAKE_CASE`（例如：`MAX_RETRIES`, `DEFAULT_TIMEOUT`）
- 私有成员：`_leading_underscore`（例如：`_internal_method`）

### 导入组织
导入语句按以下顺序组织：
1. Future imports (`from __future__ import annotations`)
2. 标准库导入
3. 第三方库导入
4. 本地应用导入

使用 Ruff 的 isort 功能自动排序。

### 类型注解
- 所有公共函数必须包含类型注解
- 使用 `from __future__ import annotations` 启用延迟注解
- 使用 `TYPE_CHECKING` 处理循环导入
- 使用 `| None` 而不是 `Optional[]`（Python 3.10+语法）

## 项目结构

```
marivo/
  datasource/    # 数据源 API 与引擎
  semantic/      # 语义定义、加载与校验
  analysis/      # 分析 API、Frame 与执行
  ontology/      # 本体 API
  introspection/ # 运行时检查
  skills/        # 随包分发的 Agent skills
tests/           # 按 datasource、semantic、analysis 等领域组织
docs/            # 架构与 API 文档源文件
site/            # 用户文档站点
scripts/         # 开发与检查脚本
```

## 架构约束

- `core/` 不得导入 adapter、transport 或存储库（由 import-linter 在 CI 中强制执行）
- Surface 层（transports / api）只做协议转换，必须通过 Runtime 层访问业务逻辑
- 详细约束参见 `docs/architecture-invariants.md`

## 开发工具

### 安装开发依赖
```bash
.venv/bin/pip install -e ".[dev,trino]"
```

### Pre-commit Hooks
开发依赖已包含 pre-commit。安装 hooks 以在提交前自动检查代码：
```bash
.venv/bin/pre-commit install
```

手动运行所有检查：
```bash
.venv/bin/pre-commit run --all-files
```

提交 hooks 执行 Ruff、通用文件检查，以及针对 Python 文件的 import-linter 和 mypy；当前
配置不包含测试 hook。默认回归测试通过 `make test` 单独运行，`make check` 也包含
该目标。提交 hooks 不执行完整 Runtime 验收，也不为提交启动 MinIO。已有检查通过且
文件未变时，提交准备不再重复运行同一批检查，提交 hooks 仍照常执行。

### 代码格式化
在最终检查和候选版本摘要冻结前，只对本次修改的 Python 文件执行：

```bash
.venv/bin/ruff check --fix path/to/changed.py
.venv/bin/ruff format path/to/changed.py
```

需要格式化整个工作区时才使用：

```bash
make format
```

### Linting
```bash
# 检查代码问题
make lint
```

### 类型检查
```bash
make typecheck
```

标准入口默认使用精简输出，并保留错误和退出状态。可通过
`TYPECHECK_TARGETS='marivo/path/to/module.py'` 缩小检查范围。

### 构建 API 文档
使用 Sphinx 从公共模块（`marivo.datasource` / `marivo.semantic` /
`marivo.analysis`）的 docstring 生成 HTML API 参考。输出位于
`site/public/api/`（已在 `.gitignore` 中忽略），由 Astro 站点在 `/api/` 路径发布。

```bash
# 安装文档依赖
.venv/bin/pip install -e ".[docs]"

# 生成 API 文档
make docs-api
```

完整站点构建会先自动生成 API 文档（`site` 的 npm `prebuild` 脚本会调用
`make docs-api`），因此发布构建需在具备 Python 环境的主机上运行：

```bash
cd site && npm run build
```

## 测试

### 运行测试
```bash
# 运行本地回归测试（并行，目标三分钟内完成）
make test

# Run a focused local recovery file, including cases marked slow
make test TESTS='tests/analysis/materialization/test_numeric_recovery.py'

# Run focused checks against already-running external services
make runtime-test TESTS='tests/datasource/test_source_profiles.py'

# Run fast daily tests, static checks, and API documentation checks
make check

# Run a specific test file with compact output
make test TESTS='tests/analysis/session/test_analysis_session_core.py'

# Run a specific test method
make test TESTS='tests/analysis/session/test_analysis_session_core.py::test_session_public_fields_are_read_only'

# 显示详细输出
.venv/bin/pytest -v

# 显示print输出
.venv/bin/pytest -s
```

`make test` 默认精简输出，使用短 traceback，累计五项失败后停止，同时保留失败
退出状态，默认使用 4 个 pytest-xdist worker。可用 `PYTEST_WORKERS=<count>` 覆盖；
含 `::` 的单个测试节点仍串行执行。默认将 OpenBLAS 与 Arrow 的底层线程预算设为 1，
避免每个 worker 再启动一组计算或 I/O 线程；可用 `PYTEST_NATIVE_THREADS=<count>` 覆盖。
该预算通过环境传给测试及其子进程，不改变生产库默认设置。需要更多诊断时，仅对失败范围使用
`.venv/bin/pytest` 并指定详细输出。
日常开发仅在修改需要时执行相关的 `runtime-test TESTS=...`；不自动执行完整
Runtime suite。`make runtime-test` 使用精简输出。

The Runtime Make target defaults to two workers to bound load on shared external
datasource services. Use `RUNTIME_WORKERS=<count>` to override the
per-invocation limit after measuring host capacity. Explicit `TESTS` node ids
containing `::` run serially. Account for other concurrent test tasks on the host.

`make test` 运行日常本地回归，包括未标记为 `release` 的 `slow` 坐标统计、worker、
独立进程恢复和组合场景。经测量较重的子进程、CPU 密集型、类型检查及查询/恢复用例保留原断言，
由 `make release-check` 执行；日常测试目标为三分钟内完成。`runtime` 仅标记需要已启动外部数据源服务的用例，
覆盖基本数据源能力和后端特有风险。相同数据源共享服务容器，每个用例独立管理
自己的表、schema 和连接；pytest 不启停服务。

`make check` 执行完整本地检查。发布准备与发布 CI 通过
`make release-check` 加上 Runtime、安装和打包检查。Runtime 不重复全部本地
分析验收矩阵。Runtime coverage notes and local acceptance records are kept in the local-only `docs/testing/` directory.

For test performance investigations, measure the same suite with
`.venv/bin/pytest -m runtime -n 2 --durations=40 <selected-tests>`, then compare
explicit worker counts and simultaneous invocations. Compiler oracle tests can use
`assert_compiled_validations` from `tests/analysis/graph/execution_fixtures.py` to check
every named validation in one query without repeatedly compiling shared Ibis
nodes. Keep independent budget checks parametrized so xdist can distribute them.

### 测试覆盖率

覆盖率用于寻找未执行的重要分支；必须结合独立预期值、有效断言和关键风险评审。
不设置全项目统一的最低百分比，也不通过重复用例或放宽断言追求数字。下面命令使用
默认测试选择，不能将其报告视为完整 slow、Runtime 或 release 覆盖。

```bash
# 生成覆盖率报告
.venv/bin/pytest --cov=marivo --cov-report=term-missing

# 生成HTML覆盖率报告
.venv/bin/pytest --cov=marivo --cov-report=html
# 然后打开 htmlcov/index.html

# 生成XML覆盖率报告（用于CI）
.venv/bin/pytest --cov=marivo --cov-report=xml
```

### 测试要求

- 新功能、行为变化和缺陷修复应有相关行为证据，优先扩展已有测试；保持行为的重构
  可以复用已有有效覆盖。选择能真实暴露错误且成本合理的测试边界。
- 新增或实质修改的测试应说明当前契约、具体失败模式、独立预期值、必要边界及增量
  价值；通常由名称、输入和断言表达，不要求每个用例单独填写文档。
- 数值测试使用手算、独立参考实现或适用的数学性质；不得调用被测生产逻辑计算预期值。
  按契约检查键、单位、NULL/presence、精度和顺序，不能只检查非空或成功执行。
- 评审同时检查“错误实现能否被识别”和“等价重构能否继续通过”。只有交互本身属于
  契约时才断言调用次数或顺序；公开 exports 快照与必要的 Help 契约检查继续保留。
- 合并或删除测试前，说明独特失败模式由哪个测试保留，或其契约为何退役；慢、失败、
  相同覆盖率或存在端到端用例均不足以证明冗余。保留必要的 native、损坏输入和冷恢复边界。
- 按变更范围通过要求的门禁，报告实际运行范围与阻塞；默认 `make test` 包含单元及
  本地集成测试，其通过不代表完整 Runtime 或发布验收通过。不得通过 skip、xfail 或
  修改 marker 隐藏回归。
- 仅文档改动检查内容、链接和差异，不自动运行行为测试。共享 fixture 或行为变化按
  仓库规则扩大验证，相关 Runtime、slow 和发布证据分别说明。
- 测试文件命名：`test_*.py`
- 测试函数命名：`test_*`
- 优先使用 pytest 测试函数与 fixtures；保留现有 `unittest.TestCase` 时，方法命名为 `test_*`。
  当前配置不收集普通 pytest 测试类，不因命名约定引入无法收集的测试。

详细测试质量规范见已跟踪的 `.agents/skills/marivo-review/references/test-quality.md`。本地覆盖责任记录位于
`docs/testing/runtime-coverage.md`，fixture 操作指引位于
`.agents/skills/marivo-test-fixtures/SKILL.md`；后两者由 Git 忽略，
干净 checkout 不依赖它们，以本节及 `AGENTS.md` 中可用的规则为准。

## 提交规范

### 提交前检查清单

按变更适用范围检查；仅文档改动遵循上面的文档检查要求。

- [ ] 已格式化本次修改的 Python 文件
- [ ] 已通过适用的 lint 检查
- [ ] 已通过修改模块所需的类型检查
- [ ] 已通过本次要求的门禁，并明确本地、slow、Runtime、发布检查的实际范围或阻塞
- [ ] 关键行为有有效断言和独立依据；删除合并的独特覆盖已有去向
- [ ] 更新了相关文档

### 提交信息格式
使用清晰、描述性的提交信息：
```
简短的总结（50字符以内）

详细描述（如果需要）：
- 为什么做这个变更
- 解决了什么问题
- 有什么影响
```

## CI/CD

项目使用GitHub Actions进行持续集成：
- 自动运行linting和类型检查
- 自动运行测试套件
- 生成测试覆盖率报告
- 所有检查必须通过才能合并PR

## 常见问题

### Q: 如何修复格式问题？
A: 运行 `make format` 自动格式化所有文件（包含 import 排序）。

### Q: 如何修复import顺序问题？
A: `make format` 会自动修复 import 排序。

### Q: Mypy报告类型错误怎么办？
A: 添加正确的类型注解。如果是第三方库缺少类型定义，可以在pyproject.toml中配置忽略。

### Q: 测试失败怎么办？
A: 检查错误信息，修复代码或测试。使用 `.venv/bin/pytest -v -s` 查看详细输出。
