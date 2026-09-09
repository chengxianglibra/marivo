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
  contracts/     # 共享域类型：ID、值对象、错误码
  core/          # 纯域逻辑，零 I/O
  runtime/       # 用例编排层
  ports/         # Protocol 接口定义
  adapters/      # Port 实现（local/server）
  profiles/      # Profile 工厂（create_local_runtime / create_server_runtime）
  transports/    # 传输层（CLI, MCP, HTTP API）
  api/           # FastAPI HTTP 路由
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

普通提交保留已安装的格式、lint、typing 和默认测试 hooks；不执行完整 Runtime
验收，也不为提交启动 MinIO。已有检查通过且文件未变时，提交准备不再重复运行同一批
检查，提交 hooks 仍照常执行。

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
# 运行日常回归测试（并行）
make test

# Run focused Runtime checks when the change needs them
make runtime-test TESTS='tests/test_lazy_local_execution.py'

# Run daily tests, static checks, and API documentation checks
make check-agent

# Run a specific test file with compact output
make test TESTS='tests/test_sessions.py'

# Run a specific test method
make test TESTS='tests/test_sessions.py::SessionAPITests::test_get_session_after_create'

# 显示详细输出
.venv/bin/pytest -v

# 显示print输出
.venv/bin/pytest -s
```

`make test` 默认精简输出，使用短 traceback，累计五项失败后停止，同时保留失败
退出状态。需要更多诊断时，仅对失败范围使用 `.venv/bin/pytest` 并指定详细输出。
日常开发仅在修改需要时执行相关的 `runtime-test TESTS=...`；不自动执行完整
Runtime suite。`runtime-test-agent` 为同一指定范围提供精简输出。

Runtime Make targets default to two workers to leave capacity for their nested
execution and read subprocesses. Use `RUNTIME_WORKERS=<count>` to override the
per-invocation limit after measuring host capacity. Explicit `TESTS` node ids
containing `::` run serially. Account for other concurrent test tasks on the host.

`make check` 和 `make check-agent` 均不执行完整 Runtime suite。完整多阶段分析、
真实数据源、worker 和进程恢复验收归入发布准备与发布 CI，由 `make release-check`
连同日常检查、安装和打包检查一起执行。发布前必须显式配置健康、隔离的 MinIO
测试服务和 `MARIVO_TEST_S3_ENDPOINT`，测试 fixture 负责创建、启用版本控制和清理
隔离 bucket；服务本身由发布操作者或 CI 管理。当前
[`object_connection_access` fixture](tests/conftest.py) 使用仅供测试的
`minioadmin` access key 和 secret key。保留验收候选版本、命令和结果，S3
验收不能因缺少 endpoint 而跳过。

Functional Runtime acceptance primarily uses local files. Retain native engine
cases only for engine-specific execution, receipts, and recovery. The separate
`make object-storage-test` command runs the live MinIO connection/versioning smoke;
`make runtime-test` needs no object service. S3 ownership, exact-version, failure,
and pending-request contracts use SDK stubs and local journal files instead of
repeating the complete functional matrix against MinIO. See
[Runtime test coverage](docs/testing/runtime-coverage.md).

For test performance investigations, measure the same suite with
`.venv/bin/pytest -m runtime -n 2 --durations=40 <selected-tests>`, then compare
explicit worker counts and simultaneous invocations. Compiler oracle tests can use
`assert_compiled_validations` from `tests/lazy_execution_fixtures.py` to check
every named validation in one query without repeatedly compiling shared Ibis
nodes. Keep independent budget checks parametrized so xdist can distribute them.

### 测试覆盖率

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
- 新功能必须包含单元测试
- 测试覆盖率目标：≥80%
- 所有测试必须通过才能合并
- 测试文件命名：`test_*.py`
- 测试类命名：`*Tests`
- 测试方法命名：`test_*`

## 提交规范

### 提交前检查清单
- [ ] 代码已格式化（`make format`）
- [ ] 通过linting检查（`make lint`）
- [ ] 通过类型检查（`make typecheck`）
- [ ] 所有测试通过（`make test`）
- [ ] 测试覆盖率满足要求
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
