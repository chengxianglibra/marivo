from __future__ import annotations

import importlib
import sys


def test_app_api_import_defers_app_factory_import() -> None:
    sys.modules.pop("marivo.api", None)
    sys.modules.pop("marivo.api.app_factory", None)

    module = importlib.import_module("marivo.api")

    assert "marivo.api.app_factory" not in sys.modules
    assert callable(module.create_app)
    assert "marivo.api.app_factory" in sys.modules
