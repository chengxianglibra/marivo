from __future__ import annotations

import argparse
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.api.app_factory import create_app
from app.cli._exitcodes import (
    EXIT_CONFIG_INVALID,
    EXIT_RUNTIME_NOT_RUNNING,
    EXIT_WORKSPACE_ROOT_UNAVAILABLE,
)
from app.cli._output import CliError
from app.cli.cmd_init_local import BOOTSTRAP_CONFIG_YAML
from app.cli.cmd_init_local import handle as init_local_handle
from app.cli.cmd_runtime import handle as runtime_handle
from app.cli.cmd_serve_local import handle as serve_local_handle
from app.config import resolve_metadata_path


class ResolveMetadataPathTests(unittest.TestCase):
    def test_local_runtime_config_keeps_metadata_under_workspace_dot_marivo(self) -> None:
        workspace_root = Path("/tmp/example-workspace")
        config_path = workspace_root / ".marivo" / "marivo.yaml"

        resolved = resolve_metadata_path(config_path, ".marivo/metadata.sqlite")

        self.assertEqual(resolved, workspace_root / ".marivo" / "metadata.sqlite")


class LocalCliContractTests(unittest.TestCase):
    def test_init_local_reports_metadata_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resolved_root = Path(tmp).resolve()
            result = init_local_handle(argparse.Namespace(workspace_root=tmp, format="json"))

            self.assertEqual(result["status"], "initialized")
            self.assertEqual(result["workspace_root"], str(resolved_root))
            self.assertEqual(
                result["config_path"],
                str(resolved_root / ".marivo" / "marivo.yaml"),
            )
            self.assertEqual(
                result["metadata_path"],
                str(resolved_root / ".marivo" / "metadata.sqlite"),
            )

    def test_init_local_creates_only_bootstrap_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace_root = Path(tmp).resolve()

            init_local_handle(argparse.Namespace(workspace_root=tmp, format="json"))

            dot_marivo = workspace_root / ".marivo"
            config_path = dot_marivo / "marivo.yaml"
            self.assertTrue(dot_marivo.is_dir())
            self.assertEqual(config_path.read_text(), BOOTSTRAP_CONFIG_YAML)
            self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o644)
            self.assertFalse((dot_marivo / "metadata.sqlite").exists())
            self.assertFalse((dot_marivo / "runtime.json").exists())
            self.assertFalse((dot_marivo / "logs").exists())
            self.assertFalse((dot_marivo / "run").exists())

    def test_init_local_is_idempotent_and_does_not_overwrite_existing_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace_root = Path(tmp).resolve()
            dot_marivo = workspace_root / ".marivo"
            dot_marivo.mkdir()
            config_path = dot_marivo / "marivo.yaml"
            custom_config = "metadata:\n  engine: sqlite\n  path: custom.sqlite\n"
            config_path.write_text(custom_config)

            result = init_local_handle(argparse.Namespace(workspace_root=tmp, format="json"))

            self.assertEqual(result["status"], "already_initialized")
            self.assertEqual(config_path.read_text(), custom_config)

    def test_init_local_maps_workspace_write_failures_to_workspace_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("pathlib.Path.mkdir", side_effect=OSError("permission denied")),
                self.assertRaises(CliError) as exc,
            ):
                init_local_handle(argparse.Namespace(workspace_root=tmp, format="json"))

            self.assertEqual(exc.exception.exit_code, EXIT_WORKSPACE_ROOT_UNAVAILABLE)

    def test_runtime_status_without_manifest_reports_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resolved_root = Path(tmp).resolve()
            with self.assertRaises(CliError) as exc:
                runtime_handle(
                    argparse.Namespace(
                        runtime_command="status",
                        workspace_root=tmp,
                        format="json",
                    )
                )

            self.assertEqual(exc.exception.exit_code, EXIT_RUNTIME_NOT_RUNNING)
            self.assertEqual(
                exc.exception.json_data,
                {
                    "status": "stopped",
                    "workspace_root": str(resolved_root),
                },
            )

    def test_serve_local_rejects_invalid_existing_config_before_spawn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace_root = Path(tmp)
            dot_marivo = workspace_root / ".marivo"
            dot_marivo.mkdir()
            (dot_marivo / "marivo.yaml").write_text("sources:\n  - display_name: invalid\n")

            with self.assertRaises(CliError) as exc:
                serve_local_handle(
                    argparse.Namespace(
                        workspace_root=tmp,
                        host="127.0.0.1",
                        port=0,
                        start_timeout_ms=1000,
                        format="json",
                    )
                )

            self.assertEqual(exc.exception.exit_code, EXIT_CONFIG_INVALID)


class CreateAppLocalConfigTests(unittest.TestCase):
    def test_create_app_resolves_local_bootstrap_metadata_path_under_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace_root = Path(tmp)
            dot_marivo = workspace_root / ".marivo"
            dot_marivo.mkdir()
            config_path = dot_marivo / "marivo.yaml"
            config_path.write_text("metadata:\n  engine: sqlite\n  path: .marivo/metadata.sqlite\n")

            app = create_app(
                db_path=workspace_root / "analytics.duckdb",
                config_path=config_path,
            )

            self.assertTrue((workspace_root / ".marivo" / "metadata.sqlite").exists())
            self.assertIsNotNone(app.state.metadata_store)


if __name__ == "__main__":
    unittest.main()
