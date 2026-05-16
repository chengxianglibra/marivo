from __future__ import annotations

import importlib
import sys


def test_app_api_import_defers_app_factory_import() -> None:
    sys.modules.pop("marivo.transports.http", None)
    sys.modules.pop("marivo.transports.http.app_factory", None)

    module = importlib.import_module("marivo.transports.http")

    assert "marivo.transports.http.app_factory" not in sys.modules
    assert callable(module.create_app)
    assert "marivo.transports.http.app_factory" in sys.modules
