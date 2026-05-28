from __future__ import annotations

import argparse
import io
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from marivo.config import resolve_metadata_path
from marivo.transports.cli import _build_parser, _format_text_result
from marivo.transports.cli._exitcodes import (
    EXIT_CONFIG_INVALID,
    EXIT_FAILURE,
    EXIT_HEALTH_CHECK_FAILED,
    EXIT_INVALID_USAGE,
    EXIT_RUNTIME_NOT_RUNNING,
)
from marivo.transports.cli._manifest import RuntimeManifest
from marivo.transports.cli._output import CliError
from marivo.transports.cli.cmd_agent import handle as agent_handle
from marivo.transports.cli.cmd_doctor import handle as doctor_handle
from marivo.transports.cli.cmd_runtime import handle as runtime_handle
from marivo.transports.http.app_factory import create_app


class _HealthResponse:
    def __init__(self, status_code: int, body: dict[str, object]) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict[str, object]:
        return self._body


_BOOTSTRAP_YAML = (
    "metadata:\n"
    "  engine: sqlite\n"
    "  path: .marivo/metadata.sqlite\n"
    "\n"
    "observability:\n"
    "  log_level: INFO\n"
    "  metrics_enabled: true\n"
)


def _create_bootstrap_yaml(workspace_root: Path) -> None:
    """Create .marivo/ with a minimal YAML config for doctor tests."""
    dot_marivo = workspace_root / ".marivo"
    dot_marivo.mkdir(parents=True, exist_ok=True)
    (dot_marivo / "marivo.yaml").write_text(_BOOTSTRAP_YAML)


class ResolveMetadataPathTests(unittest.TestCase):
    def test_local_runtime_config_keeps_metadata_under_workspace_dot_marivo(self) -> None:
        workspace_root = Path("/tmp/example-workspace")
        config_path = workspace_root / ".marivo" / "marivo.yaml"

        resolved = resolve_metadata_path(config_path, ".marivo/metadata.sqlite")

        self.assertEqual(resolved, workspace_root / ".marivo" / "metadata.sqlite")


class LocalCliContractTests(unittest.TestCase):
    def test_top_level_help_omits_calendar_command(self) -> None:
        help_text = _build_parser().format_help()

        self.assertNotIn("calendar", help_text)
        self.assertNotIn("Calendar data management", help_text)

    def test_calendar_command_is_rejected_by_argparse(self) -> None:
        parser = _build_parser()

        with (
            patch("sys.stderr", new_callable=io.StringIO),
            self.assertRaises(SystemExit) as exc,
        ):
            parser.parse_args(["calendar"])

        self.assertEqual(exc.exception.code, 2)

    def test_agent_sync_skills_parser_accepts_supported_agents(self) -> None:
        parser = _build_parser()

        for agent in ("codex", "claude", "opencode", "openclaw", "hermes"):
            args = parser.parse_args(["agent", "sync-skills", "--agent", agent, "--dry-run"])
            self.assertEqual(args.command, "agent")
            self.assertEqual(args.agent_command, "sync-skills")
            self.assertEqual(args.agent, agent)
            self.assertTrue(args.dry_run)

    def test_agent_sync_skills_parser_rejects_unknown_agent(self) -> None:
        parser = _build_parser()

        with (
            patch("sys.stderr", new_callable=io.StringIO),
            self.assertRaises(SystemExit) as exc,
        ):
            parser.parse_args(["agent", "sync-skills", "--agent", "unknown"])

        self.assertEqual(exc.exception.code, 2)

    def test_agent_sync_skills_rejects_all_with_target(self) -> None:
        with self.assertRaises(CliError) as exc:
            agent_handle(
                argparse.Namespace(
                    agent_command="sync-skills",
                    agent=None,
                    all=True,
                    target="/tmp/skills",
                    dry_run=True,
                    force=False,
                    format="json",
                )
            )

        self.assertEqual(exc.exception.exit_code, EXIT_INVALID_USAGE)

    def test_agent_sync_skills_target_only_uses_custom_agent(self) -> None:
        with patch("marivo.transports.cli.cmd_agent.sync_skills") as sync:
            sync.return_value = {
                "status": "ok",
                "dry_run": True,
                "marivo_version": "0.1.0",
                "results": [],
            }

            result = agent_handle(
                argparse.Namespace(
                    agent_command="sync-skills",
                    agent=None,
                    all=False,
                    target="/tmp/skills",
                    dry_run=True,
                    force=False,
                    format="json",
                )
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(sync.call_args.kwargs["agent"], "custom")
        self.assertEqual(sync.call_args.kwargs["target_root"], Path("/tmp/skills"))

    def test_runtime_status_without_manifest_reports_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resolved_root = Path(tmp).resolve()
            with self.assertRaises(CliError) as exc:
                runtime_handle(
                    argparse.Namespace(
                        runtime_command="status",
                        workspace=tmp,
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
                patch("marivo.transports.cli.cmd_runtime.os.kill"),
                patch(
                    "marivo.transports.cli.cmd_runtime.httpx.get",
                    return_value=_HealthResponse(200, {"status": "ok"}),
                ),
            ):
                result = runtime_handle(
                    argparse.Namespace(
                        runtime_command="status",
                        workspace=tmp,
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
                        workspace=tmp,
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
                patch("marivo.transports.cli.cmd_runtime.os.kill", side_effect=ProcessLookupError),
                self.assertRaises(CliError) as exc,
            ):
                runtime_handle(
                    argparse.Namespace(
                        runtime_command="status",
                        workspace=tmp,
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
                patch("marivo.transports.cli.cmd_runtime.os.kill"),
                patch(
                    "marivo.transports.cli.cmd_runtime.httpx.get",
                    return_value=_HealthResponse(200, {"status": "starting"}),
                ),
                self.assertRaises(CliError) as exc,
            ):
                runtime_handle(
                    argparse.Namespace(
                        runtime_command="status",
                        workspace=tmp,
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
                        workspace=tmp,
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

            with patch("marivo.transports.cli.cmd_runtime.os.kill", side_effect=ProcessLookupError):
                result = runtime_handle(
                    argparse.Namespace(
                        runtime_command="stop",
                        workspace=tmp,
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
                        workspace=tmp,
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
                    "marivo.transports.cli.cmd_runtime.os.kill",
                    side_effect=[None, None, ProcessLookupError],
                ) as kill,
                patch("marivo.transports.cli.cmd_runtime.time.sleep"),
            ):
                result = runtime_handle(
                    argparse.Namespace(
                        runtime_command="stop",
                        workspace=tmp,
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
                patch("marivo.transports.cli.cmd_runtime.os.kill"),
                patch(
                    "marivo.transports.cli.cmd_runtime.time.monotonic", side_effect=[0.0, 0.0, 1.0]
                ),
                patch("marivo.transports.cli.cmd_runtime.time.sleep"),
                self.assertRaises(CliError) as exc,
            ):
                runtime_handle(
                    argparse.Namespace(
                        runtime_command="stop",
                        workspace=tmp,
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
                    "marivo.transports.cli.cmd_runtime.os.kill",
                    side_effect=[None, PermissionError],
                ),
                self.assertRaises(CliError) as exc,
            ):
                runtime_handle(
                    argparse.Namespace(
                        runtime_command="stop",
                        workspace=tmp,
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
                patch("marivo.transports.cli.cmd_runtime.os.kill") as kill,
                patch(
                    "marivo.transports.cli.cmd_runtime.time.monotonic", side_effect=[0.0, 0.0, 1.0]
                ),
                patch("marivo.transports.cli.cmd_runtime.time.sleep"),
            ):
                result = runtime_handle(
                    argparse.Namespace(
                        runtime_command="stop",
                        workspace=tmp,
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
                        workspace=tmp,
                        force=False,
                        timeout_ms=0,
                        format="json",
                    )
                )

            self.assertEqual(exc.exception.exit_code, EXIT_INVALID_USAGE)

    def test_doctor_reports_runtime_not_running_without_mutating_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace_root = Path(tmp).resolve()
            _create_bootstrap_yaml(workspace_root)
            before_paths = sorted(p.relative_to(workspace_root) for p in workspace_root.rglob("*"))

            with self.assertRaises(CliError) as exc:
                doctor_handle(argparse.Namespace(workspace=tmp, format="json"))

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
            _create_bootstrap_yaml(workspace_root)
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
                patch("marivo.transports.cli.cmd_doctor.os.kill"),
                patch(
                    "marivo.transports.cli.cmd_doctor.httpx.get",
                    return_value=_HealthResponse(200, {"status": "ok"}),
                ),
            ):
                result = doctor_handle(argparse.Namespace(workspace=tmp, format="json"))

            self.assertTrue(result["ok"])
            self.assertEqual(result["summary"], "6/6 checks passed")
            self.assertTrue(all(check["ok"] for check in result["checks"]))

    def test_doctor_invalid_manifest_is_configuration_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace_root = Path(tmp).resolve()
            _create_bootstrap_yaml(workspace_root)
            dot_marivo = workspace_root / ".marivo"
            (dot_marivo / "runtime.json").write_text("{}")

            with self.assertRaises(CliError) as exc:
                doctor_handle(argparse.Namespace(workspace=tmp, format="json"))

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
