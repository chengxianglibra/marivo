"""
Pitfall: declaring a dataset whose datasource= argument was never registered.
When triggered: the agent passes a datasource ref that has no matching @ms.datasource declaration.

Expected output:
    DatasourceNotRegisteredError: Dataset 'orders' references missing datasource
    正确写法:
      @ms.datasource(name="tiny_orders", backend_type="duckdb")
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import marivo.semantic_py as ms


def _write_broken_project(root: Path) -> None:
    sales = root / "sales"
    sales.mkdir()
    (sales / "_model.py").write_text(
        'import marivo.semantic_py as ms\nms.model(name="sales")\n',
        encoding="utf-8",
    )
    (sales / "datasets.py").write_text(
        "import marivo.semantic_py as ms\n"
        "\n"
        '@ms.dataset(name="orders", datasource=ms.ref("datasource.tiny_orders"))\n'
        "def orders(backend):\n"
        '    return backend.table("orders")\n',
        encoding="utf-8",
    )


with TemporaryDirectory() as tmp:
    project = ms.SemanticProject(root=tmp)
    _write_broken_project(Path(tmp))
    try:
        ms.reload(project)
    except ms.errors.SemanticLoadError as e:
        for err in e.errors:
            if isinstance(err, ms.errors.DatasourceNotRegisteredError):
                print(err)
                break
        else:
            raise
    else:
        raise AssertionError("expected DatasourceNotRegisteredError")
