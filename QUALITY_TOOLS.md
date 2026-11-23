# Marivo 工程质量工具快速参考

从仓库根目录运行。环境和依赖安装见[开发规范](CONTRIBUTING.md#开发工具)。

| 目的 | 命令 |
| --- | --- |
| 日常增量检查 | `make check-fast` |
| 检查格式、复杂度、私有成员和完整导入图 | `make lint` |
| 检查库和测试的类型及显式覆写 | `make typecheck` |
| 运行默认本地测试 | `make test` |
| 运行单个测试文件 | `make test TESTS='tests/surface/test_architecture_guard.py'` |
| 完整本地门禁 | `make check` |
| 构建并严格检查 API 文档 | `make docs-api` |
| 运行形式化验证 | `make verify-formal` |
| 运行已配置的 Provider 测试 | `make runtime-test` |
| 构建和验证分发包 | `make pypi-build && make pypi-check` |

`make check` 执行 Lint、类型检查、默认测试和严格 Sphinx 构建。默认测试排除
Runtime 和 release 标记。Provider 测试由本地 Podman 环境和各 Profile 的启用
变量驱动。[Makefile](Makefile) · [测试配置](tests/conftest.py) ·
[Provider 环境](tests/datasource/environment/README.md)

用下列命令查看默认测试的覆盖率。分支和文件范围由项目配置决定。
[覆盖率配置](pyproject.toml)

```bash
.venv/bin/pytest --cov=marivo --cov-report=term-missing
.venv/bin/pytest --cov=marivo --cov-report=html
```

Pre-commit 检查可以通过 `.venv/bin/pre-commit run --all-files` 单独运行。
依赖门禁、冷启动检查及棘轮维护见[架构边界与静态检查](docs/architecture/architecture-boundaries.md)。
