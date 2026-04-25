from __future__ import annotations

import argparse
import json
import os
import signal
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.api.app_factory import create_app
from app.cli import _format_text_result
from app.cli._exitcodes import (
    EXIT_CONFIG_INVALID,
    EXIT_FAILURE,
    EXIT_HEALTH_CHECK_FAILED,
    EXIT_INVALID_USAGE,
    EXIT_RUNTIME_NOT_RUNNING,
    EXIT_WORKSPACE_ROOT_UNAVAILABLE,
)
from app.cli._manifest import RuntimeManifest
from app.cli._output import CliError
from app.cli.cmd_doctor import handle as doctor_handle
from app.cli.cmd_init_local import BOOTSTRAP_CONFIG_YAML
from app.cli.cmd_init_local import handle as init_local_handle
from app.cli.cmd_runtime import handle as runtime_handle
from app.cli.cmd_serve_local import handle as serve_local_handle
from app.config import resolve_metadata_path


class _HealthResponse:
    def __init__(self, status_code: int, body: dict[str, object]) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict[str, object]:
        return self._body


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

    def test_init_local_resolves_relative_workspace_root_to_canonical_paths(self) -> None:
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp).resolve()
            workspace_root = tmp_root / "workspace"
            workspace_root.mkdir()

            try:
                os.chdir(tmp_root)
                result = init_local_handle(
                    argparse.Namespace(workspace_root="workspace", format="json")
                )
            finally:
                os.chdir(original_cwd)

            self.assertEqual(result["status"], "initialized")
            self.assertEqual(result["workspace_root"], str(workspace_root))
            self.assertEqual(
                result["config_path"],
                str(workspace_root / ".marivo" / "marivo.yaml"),
            )
            self.assertEqual(
                result["metadata_path"],
                str(workspace_root / ".marivo" / "metadata.sqlite"),
            )
            self.assertFalse((workspace_root / ".marivo" / ".marivo").exists())

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

    def test_serve_local_starts_daemon_after_health_check_and_writes_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace_root = Path(tmp).resolve()
            proc = MagicMock()
            proc.pid = 43210

            with (
                patch("app.cli.cmd_serve_local._discover_port", return_value=49152),
                patch("app.cli.cmd_serve_local.subprocess.Popen", return_value=proc) as popen,
                patch(
                    "app.cli.cmd_serve_local.httpx.get",
                    return_value=_HealthResponse(200, {"status": "ok"}),
                ),
                patch("app.cli.cmd_serve_local.time.sleep"),
            ):
                result = serve_local_handle(
                    argparse.Namespace(
                        workspace_root=tmp,
                        host="127.0.0.1",
                        port=0,
                        start_timeout_ms=1000,
                        format="json",
                    )
                )

            manifest_path = workspace_root / ".marivo" / "runtime.json"
            pid_path = workspace_root / ".marivo" / "run" / "marivo.pid"
            manifest = json.loads(manifest_path.read_text())

            self.assertEqual(result["status"], "serving")
            self.assertEqual(result["base_url"], "http://127.0.0.1:49152")
            self.assertEqual(result["pid"], 43210)
            self.assertEqual(manifest["base_url"], "http://127.0.0.1:49152")
            self.assertEqual(manifest["pid"], 43210)
            self.assertEqual(pid_path.read_text(), "43210\n")
            self.assertTrue((workspace_root / ".marivo" / "logs" / "marivo.log").is_file())
            popen.assert_called_once()

    def test_serve_local_health_timeout_stops_daemon_and_does_not_write_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace_root = Path(tmp).resolve()
            proc = MagicMock()
            proc.pid = 43210

            with (
                patch("app.cli.cmd_serve_local._discover_port", return_value=49152),
                patch("app.cli.cmd_serve_local.subprocess.Popen", return_value=proc),
                patch(
                    "app.cli.cmd_serve_local.httpx.get",
                    return_value=_HealthResponse(200, {"status": "starting"}),
                ),
                patch("app.cli.cmd_serve_local.time.sleep"),
                self.assertRaises(CliError) as exc,
            ):
                serve_local_handle(
                    argparse.Namespace(
                        workspace_root=tmp,
                        host="127.0.0.1",
                        port=0,
                        start_timeout_ms=1,
                        format="json",
                    )
                )

            self.assertEqual(exc.exception.exit_code, EXIT_HEALTH_CHECK_FAILED)
            proc.terminate.assert_called_once()
            self.assertFalse((workspace_root / ".marivo" / "runtime.json").exists())

    def test_serve_local_rejects_invalid_port_and_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for port, timeout in ((70000, 1000), (0, 0)):
                with self.subTest(port=port, timeout=timeout):
                    with self.assertRaises(CliError) as exc:
                        serve_local_handle(
                            argparse.Namespace(
                                workspace_root=tmp,
                                host="127.0.0.1",
                                port=port,
                                start_timeout_ms=timeout,
                                format="json",
                            )
                        )

                    self.assertEqual(exc.exception.exit_code, EXIT_INVALID_USAGE)

    def test_runtime_status_reports_running_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace_root = Path(tmp).resolve()
            dot_marivo = workspace_root / ".marivo"
            dot_marivo.mkdir()
            manifest = RuntimeManifest(
                workspace_root=str(workspace_root),
                host="127.0.0.1",
                port=49152,
                pid=43210,
                config_path=str(dot_marivo / "marivo.yaml"),
                metadata_path=str(dot_marivo / "metadata.sqlite"),
            )
            manifest.write_atomic(dot_marivo / "runtime.json")

            with (
                patch("app.cli.cmd_runtime.os.kill"),
                patch(
                    "app.cli.cmd_runtime.httpx.get",
                    return_value=_HealthResponse(200, {"status": "ok"}),
                ),
            ):
                result = runtime_handle(
                    argparse.Namespace(
                        runtime_command="status",
                        workspace_root=tmp,
                        format="json",
                    )
                )

            self.assertEqual(result["status"], "running")
            self.assertEqual(result["base_url"], "http://127.0.0.1:49152")
            self.assertEqual(result["pid"], 43210)
            self.assertEqual(result["config_path"], str(dot_marivo / "marivo.yaml"))
            self.assertEqual(result["metadata_path"], str(dot_marivo / "metadata.sqlite"))

    def test_runtime_status_reports_invalid_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace_root = Path(tmp).resolve()
            dot_marivo = workspace_root / ".marivo"
            dot_marivo.mkdir()
            (dot_marivo / "runtime.json").write_text("{}")

            with self.assertRaises(CliError) as exc:
                runtime_handle(
                    argparse.Namespace(
                        runtime_command="status",
                        workspace_root=tmp,
                        format="json",
                    )
                )

            self.assertEqual(exc.exception.exit_code, EXIT_CONFIG_INVALID)

    def test_runtime_status_reports_stale_pid_as_stopped_without_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace_root = Path(tmp).resolve()
            dot_marivo = workspace_root / ".marivo"
            dot_marivo.mkdir()
            manifest_path = dot_marivo / "runtime.json"
            RuntimeManifest(
                workspace_root=str(workspace_root),
                host="127.0.0.1",
                port=49152,
                pid=43210,
                config_path=str(dot_marivo / "marivo.yaml"),
                metadata_path=str(dot_marivo / "metadata.sqlite"),
            ).write_atomic(manifest_path)

            with (
                patch("app.cli.cmd_runtime.os.kill", side_effect=ProcessLookupError),
                self.assertRaises(CliError) as exc,
            ):
                runtime_handle(
                    argparse.Namespace(
                        runtime_command="status",
                        workspace_root=tmp,
                        format="json",
                    )
                )

            self.assertEqual(exc.exception.exit_code, EXIT_RUNTIME_NOT_RUNNING)
            json_data = exc.exception.json_data
            assert json_data is not None
            self.assertEqual(json_data["status"], "stopped")
            self.assertTrue(manifest_path.exists())

    def test_runtime_status_reports_unhealthy_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace_root = Path(tmp).resolve()
            dot_marivo = workspace_root / ".marivo"
            dot_marivo.mkdir()
            RuntimeManifest(
                workspace_root=str(workspace_root),
                host="127.0.0.1",
                port=49152,
                pid=43210,
                config_path=str(dot_marivo / "marivo.yaml"),
                metadata_path=str(dot_marivo / "metadata.sqlite"),
            ).write_atomic(dot_marivo / "runtime.json")

            with (
                patch("app.cli.cmd_runtime.os.kill"),
                patch(
                    "app.cli.cmd_runtime.httpx.get",
                    return_value=_HealthResponse(200, {"status": "starting"}),
                ),
                self.assertRaises(CliError) as exc,
            ):
                runtime_handle(
                    argparse.Namespace(
                        runtime_command="status",
                        workspace_root=tmp,
                        format="json",
                    )
                )

            self.assertEqual(exc.exception.exit_code, EXIT_HEALTH_CHECK_FAILED)
            json_data = exc.exception.json_data
            assert json_data is not None
            self.assertEqual(json_data["status"], "unhealthy")

    def test_runtime_stop_without_manifest_reports_already_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resolved_root = Path(tmp).resolve()
            with self.assertRaises(CliError) as exc:
                runtime_handle(
                    argparse.Namespace(
                        runtime_command="stop",
                        workspace_root=tmp,
                        force=False,
                        timeout_ms=5000,
                        format="json",
                    )
                )

            self.assertEqual(exc.exception.exit_code, EXIT_RUNTIME_NOT_RUNNING)
            self.assertEqual(
                exc.exception.json_data,
                {
                    "status": "already_stopped",
                    "workspace_root": str(resolved_root),
                },
            )

    def test_runtime_stop_cleans_stale_manifest_and_pid_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace_root = Path(tmp).resolve()
            dot_marivo = workspace_root / ".marivo"
            run_dir = dot_marivo / "run"
            run_dir.mkdir(parents=True)
            manifest_path = dot_marivo / "runtime.json"
            pid_path = run_dir / "marivo.pid"
            RuntimeManifest(
                workspace_root=str(workspace_root),
                host="127.0.0.1",
                port=49152,
                pid=43210,
                config_path=str(dot_marivo / "marivo.yaml"),
                metadata_path=str(dot_marivo / "metadata.sqlite"),
            ).write_atomic(manifest_path)
            pid_path.write_text("43210\n")

            with patch("app.cli.cmd_runtime.os.kill", side_effect=ProcessLookupError):
                result = runtime_handle(
                    argparse.Namespace(
                        runtime_command="stop",
                        workspace_root=tmp,
                        force=False,
                        timeout_ms=5000,
                        format="json",
                    )
                )

            self.assertEqual(result["status"], "already_stopped")
            self.assertFalse(manifest_path.exists())
            self.assertFalse(pid_path.exists())

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
                    "workspace_root": str(workspace_root),
                },
            )

    def test_runtime_stop_sends_sigterm_and_cleans_runtime_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace_root = Path(tmp).resolve()
            dot_marivo = workspace_root / ".marivo"
            run_dir = dot_marivo / "run"
            run_dir.mkdir(parents=True)
            manifest_path = dot_marivo / "runtime.json"
            pid_path = run_dir / "marivo.pid"
            RuntimeManifest(
                workspace_root=str(workspace_root),
                host="127.0.0.1",
                port=49152,
                pid=43210,
                config_path=str(dot_marivo / "marivo.yaml"),
                metadata_path=str(dot_marivo / "metadata.sqlite"),
            ).write_atomic(manifest_path)
            pid_path.write_text("43210\n")

            with (
                patch(
                    "app.cli.cmd_runtime.os.kill",
                    side_effect=[None, None, ProcessLookupError],
                ) as kill,
                patch("app.cli.cmd_runtime.time.sleep"),
            ):
                result = runtime_handle(
                    argparse.Namespace(
                        runtime_command="stop",
                        workspace_root=tmp,
                        force=False,
                        timeout_ms=5000,
                        format="json",
                    )
                )

            self.assertEqual(result["status"], "stopped")
            self.assertEqual(result["pid"], 43210)
            self.assertEqual(kill.call_args_list[1].args, (43210, signal.SIGTERM))
            self.assertFalse(manifest_path.exists())
            self.assertFalse(pid_path.exists())

    def test_runtime_stop_reports_timeout_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace_root = Path(tmp).resolve()
            dot_marivo = workspace_root / ".marivo"
            dot_marivo.mkdir()
            manifest_path = dot_marivo / "runtime.json"
            RuntimeManifest(
                workspace_root=str(workspace_root),
                host="127.0.0.1",
                port=49152,
                pid=43210,
                config_path=str(dot_marivo / "marivo.yaml"),
                metadata_path=str(dot_marivo / "metadata.sqlite"),
            ).write_atomic(manifest_path)

            with (
                patch("app.cli.cmd_runtime.os.kill"),
                patch("app.cli.cmd_runtime.time.monotonic", side_effect=[0.0, 0.0, 1.0]),
                patch("app.cli.cmd_runtime.time.sleep"),
                self.assertRaises(CliError) as exc,
            ):
                runtime_handle(
                    argparse.Namespace(
                        runtime_command="stop",
                        workspace_root=tmp,
                        force=False,
                        timeout_ms=1,
                        format="json",
                    )
                )

            self.assertEqual(exc.exception.exit_code, EXIT_FAILURE)
            self.assertTrue(manifest_path.exists())
            json_data = exc.exception.json_data
            assert json_data is not None
            self.assertEqual(json_data["status"], "stop_failed")

    def test_runtime_stop_permission_denied_is_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace_root = Path(tmp).resolve()
            dot_marivo = workspace_root / ".marivo"
            dot_marivo.mkdir()
            RuntimeManifest(
                workspace_root=str(workspace_root),
                host="127.0.0.1",
                port=49152,
                pid=43210,
                config_path=str(dot_marivo / "marivo.yaml"),
                metadata_path=str(dot_marivo / "metadata.sqlite"),
            ).write_atomic(dot_marivo / "runtime.json")

            with (
                patch(
                    "app.cli.cmd_runtime.os.kill",
                    side_effect=[None, PermissionError],
                ),
                self.assertRaises(CliError) as exc,
            ):
                runtime_handle(
                    argparse.Namespace(
                        runtime_command="stop",
                        workspace_root=tmp,
                        force=False,
                        timeout_ms=5000,
                        format="json",
                    )
                )

            self.assertEqual(exc.exception.exit_code, EXIT_FAILURE)
            self.assertIn("Permission denied", exc.exception.message)

    def test_runtime_stop_force_sends_sigkill_after_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace_root = Path(tmp).resolve()
            dot_marivo = workspace_root / ".marivo"
            run_dir = dot_marivo / "run"
            run_dir.mkdir(parents=True)
            manifest_path = dot_marivo / "runtime.json"
            pid_path = run_dir / "marivo.pid"
            RuntimeManifest(
                workspace_root=str(workspace_root),
                host="127.0.0.1",
                port=49152,
                pid=43210,
                config_path=str(dot_marivo / "marivo.yaml"),
                metadata_path=str(dot_marivo / "metadata.sqlite"),
            ).write_atomic(manifest_path)
            pid_path.write_text("43210\n")

            with (
                patch("app.cli.cmd_runtime.os.kill") as kill,
                patch("app.cli.cmd_runtime.time.monotonic", side_effect=[0.0, 0.0, 1.0]),
                patch("app.cli.cmd_runtime.time.sleep"),
            ):
                result = runtime_handle(
                    argparse.Namespace(
                        runtime_command="stop",
                        workspace_root=tmp,
                        force=True,
                        timeout_ms=1,
                        format="json",
                    )
                )

            self.assertEqual(result["status"], "stopped")
            self.assertEqual(result["signal"], "SIGKILL")
            self.assertEqual(kill.call_args_list[-1].args, (43210, signal.SIGKILL))
            self.assertFalse(manifest_path.exists())
            self.assertFalse(pid_path.exists())

    def test_runtime_stop_rejects_invalid_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CliError) as exc:
                runtime_handle(
                    argparse.Namespace(
                        runtime_command="stop",
                        workspace_root=tmp,
                        force=False,
                        timeout_ms=0,
                        format="json",
                    )
                )

            self.assertEqual(exc.exception.exit_code, EXIT_INVALID_USAGE)

    def test_doctor_reports_runtime_not_running_without_mutating_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace_root = Path(tmp).resolve()
            init_local_handle(argparse.Namespace(workspace_root=tmp, format="json"))
            before_paths = sorted(p.relative_to(workspace_root) for p in workspace_root.rglob("*"))

            with self.assertRaises(CliError) as exc:
                doctor_handle(argparse.Namespace(workspace_root=tmp, format="json"))

            after_paths = sorted(p.relative_to(workspace_root) for p in workspace_root.rglob("*"))
            self.assertEqual(before_paths, after_paths)
            self.assertEqual(exc.exception.exit_code, EXIT_RUNTIME_NOT_RUNNING)
            json_data = exc.exception.json_data
            assert json_data is not None
            self.assertFalse(json_data["ok"])
            checks = {check["name"]: check for check in json_data["checks"]}
            self.assertFalse(checks["runtime_manifest"]["ok"])
            self.assertEqual(checks["runtime_manifest"]["status"], "not_found")
            self.assertEqual(checks["runtime_health"]["status"], "skipped")
            self.assertIn("runtime not running", json_data["summary"])

    def test_doctor_reports_all_checks_ok_for_healthy_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace_root = Path(tmp).resolve()
            init_local_handle(argparse.Namespace(workspace_root=tmp, format="json"))
            dot_marivo = workspace_root / ".marivo"
            (dot_marivo / "metadata.sqlite").write_text("")
            RuntimeManifest(
                workspace_root=str(workspace_root),
                host="127.0.0.1",
                port=49152,
                pid=43210,
                config_path=str(dot_marivo / "marivo.yaml"),
                metadata_path=str(dot_marivo / "metadata.sqlite"),
            ).write_atomic(dot_marivo / "runtime.json")

            with (
                patch("app.cli.cmd_doctor.os.kill"),
                patch(
                    "app.cli.cmd_doctor.httpx.get",
                    return_value=_HealthResponse(200, {"status": "ok"}),
                ),
            ):
                result = doctor_handle(argparse.Namespace(workspace_root=tmp, format="json"))

            self.assertTrue(result["ok"])
            self.assertEqual(result["summary"], "6/6 checks passed")
            self.assertTrue(all(check["ok"] for check in result["checks"]))

    def test_doctor_invalid_manifest_is_configuration_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace_root = Path(tmp).resolve()
            init_local_handle(argparse.Namespace(workspace_root=tmp, format="json"))
            dot_marivo = workspace_root / ".marivo"
            (dot_marivo / "runtime.json").write_text("{}")

            with self.assertRaises(CliError) as exc:
                doctor_handle(argparse.Namespace(workspace_root=tmp, format="json"))

            self.assertEqual(exc.exception.exit_code, EXIT_CONFIG_INVALID)
            json_data = exc.exception.json_data
            assert json_data is not None
            checks = {check["name"]: check for check in json_data["checks"]}
            self.assertEqual(checks["runtime_manifest"]["status"], "failed")

    def test_cli_text_output_for_local_runtime_commands(self) -> None:
        self.assertEqual(
            _format_text_result(
                {
                    "status": "serving",
                    "base_url": "http://127.0.0.1:49152",
                    "workspace_root": "/tmp/workspace",
                }
            ),
            "Marivo local runtime serving on http://127.0.0.1:49152 (workspace: /tmp/workspace)",
        )
        self.assertEqual(
            _format_text_result(
                {
                    "status": "running",
                    "base_url": "http://127.0.0.1:49152",
                    "pid": 43210,
                }
            ),
            "Marivo local runtime running at http://127.0.0.1:49152 (pid 43210)",
        )
        self.assertEqual(
            _format_text_result({"status": "stopped", "workspace_root": "/tmp/workspace"}),
            "No local runtime running",
        )
        self.assertEqual(
            _format_text_result({"status": "stopped", "pid": 43210}),
            "Stopped Marivo local runtime (pid 43210)",
        )


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
