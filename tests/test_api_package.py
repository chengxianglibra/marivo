from __future__ import annotations

import importlib
import sys


def test_app_api_import_defers_app_factory_import() -> None:
    sys.modules.pop("app.api", None)
    sys.modules.pop("app.api.app_factory", None)

    module = importlib.import_module("app.api")

    assert "app.api.app_factory" not in sys.modules
    assert callable(module.create_app)
    assert "app.api.app_factory" in sys.modules
