from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.models import SessionCreateRequest as ApiSessionCreateRequest
from app.main import create_app
from app.models import SessionCreateRequest
from tests.shared_fixtures import get_seeded_duckdb_path


class ApiBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        db_path = Path(cls.tmp.name) / "test.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.app = create_app(db_path=db_path)
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.tmp.cleanup()

    def test_legacy_models_reexport_api_models(self) -> None:
        self.assertIs(SessionCreateRequest, ApiSessionCreateRequest)

    def test_app_state_exposes_service_bundle_and_legacy_aliases(self) -> None:
        services = self.app.state.services
        self.assertIs(services.service, self.app.state.service)
        self.assertIs(services.source_service, self.app.state.source_service)
        self.assertIs(services.semantic_service, self.app.state.semantic_service)
        self.assertIs(services.catalog_runtime, self.app.state.catalog_runtime)
