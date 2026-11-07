"""Request-owned execution preserves fixed results and publication boundaries."""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import os
import py_compile
import signal
import subprocess
import sys
from pathlib import Path
from threading import Event

import pytest
from uvicorn._types import ASGIReceiveEvent

from marivo.server.endpoints import current, endpoint_lock, read_result
from marivo.server.http import ReadApplication
from marivo.server.protocol import ROUTES
from marivo.server.refresh import Refreshes
from marivo.server.wire import Budget, Limits, ServiceError


def script(root: Path, body: str, *, returned: str = "pd.DataFrame", name: str = "total") -> Path:
    path = root / "endpoints" / (name + ".py")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "import os\nfrom pathlib import Path\nimport pandas as pd\nimport marivo.analysis as mv\n"
        f"def main(session: mv.Session) -> {returned}:\n"
        + "\n".join("    " + line for line in body.splitlines())
        + "\n"
    )
    return path


def budget(seconds: int = 30) -> Budget:
    limits = Limits(seconds=seconds)
    return Budget(limits, "refresh", limits.deadline(), Event())


async def wait_for_file(path: Path) -> None:
    async def wait() -> None:
        while not path.exists():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait(), 20)


@pytest.mark.parametrize("with_initializer", [False, True])
def test_script_session_and_log_isolation_and_fixed_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_initializer: bool
) -> None:
    # An installed package must not hide the project's namespace package.
    vendor = tmp_path / "vendor"
    foreign = vendor / "endpoints"
    foreign.mkdir(parents=True)
    (foreign / "__init__.py").write_text("raise RuntimeError('foreign endpoints package')\n")
    monkeypatch.setenv("PYTHONPATH", str(vendor))
    path = script(
        tmp_path,
        "print('x' * 100000)\nfrom ._helper import value\nreturn pd.DataFrame({'value': [value]})",
    )
    helper = path.with_name("_helper.py")
    helper.write_text("value = 10\n")
    if with_initializer:
        path.with_name("__init__.py").write_text("from ._helper import value\n")
    py_compile.compile(str(helper), doraise=True)
    helper_stat = helper.stat()

    async def scenario() -> None:
        service = Refreshes(tmp_path, 1)
        try:
            first: dict[str, object] = json.loads(await service.run("total", budget()))
            old = current(tmp_path, "total")
            assert first["data"] == {"layout": ["value"], "rows": [["10"]], "row_count": 1}
            assert isinstance(first["session_id"], str)
            helper.write_text("value = 20\n")
            os.utime(helper, ns=(helper_stat.st_atime_ns, helper_stat.st_mtime_ns))
            script(tmp_path, "from ._helper import value\nreturn pd.DataFrame({'value': [value]})")
            second: dict[str, object] = json.loads(await service.run("total", budget()))
            assert second["ref"] != first["ref"] and second["session_id"] != first["session_id"]
            path.unlink()
            assert json.loads(read_result(tmp_path, old, budget())) == first
            assert second["data"] == {"layout": ["value"], "rows": [["20"]], "row_count": 1}
        finally:
            await service.close()

    asyncio.run(scenario())


def test_disconnect_cancels_real_worker_before_releasing_capacity(tmp_path: Path) -> None:
    script(tmp_path, "Path('ready').touch()\nimport time\ntime.sleep(120)\nreturn pd.DataFrame()")

    async def scenario() -> None:
        app = ReadApplication(tmp_path, b"token", frozenset(), Limits())
        messages: asyncio.Queue[ASGIReceiveEvent] = asyncio.Queue()
        await messages.put({"type": "http.request", "body": b"", "more_body": False})
        route = next(route for route in ROUTES if route.name == "refresh")
        task = asyncio.create_task(app._request(route, {"endpoint": "total"}, {}, messages.get))
        try:
            await wait_for_file(tmp_path / "ready")
            await messages.put({"type": "http.disconnect"})
            assert await asyncio.wait_for(task, 10) is None
            assert app.refreshes.active == 0 and not app.refreshes.processes
            with endpoint_lock(tmp_path, "total"):
                assert not (tmp_path / ".marivo/server/endpoints/total.json").exists()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await app.refreshes.close()
            app.pool.shutdown()
            await app.catalog.close()

    asyncio.run(scenario())


def script_with_descendant(root: Path, *, pause: bool, detached: bool = False) -> None:
    child = (
        "import fcntl, os, time\nfrom pathlib import Path\n"
        "lock = open('descendant.lock', 'w')\nfcntl.flock(lock, fcntl.LOCK_EX)\n"
        "Path('ready').write_text(str(os.getpid()))\ntime.sleep(120)\n"
    )
    script(
        root,
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}], start_new_session={detached})\n"
        "while not Path('ready').exists():\n    time.sleep(0.01)\n"
        + ("time.sleep(120)\n" if pause else "")
        + "return pd.DataFrame({'value': [10]})",
    )


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("detached", [False, True])
def test_refresh_finishes_with_descendants_holding_stdout(
    tmp_path: Path, cancel: bool, detached: bool
) -> None:
    script_with_descendant(tmp_path, pause=cancel, detached=detached)

    async def scenario() -> None:
        service = Refreshes(tmp_path, 1)
        task = asyncio.create_task(service.run("total", budget()))
        try:
            await wait_for_file(tmp_path / "ready")
            if cancel:
                task.cancel()
            done, _ = await asyncio.wait({task}, timeout=3)
            assert task in done, "A descendant holding stdout prevented worker cleanup"
            if cancel:
                with pytest.raises(asyncio.CancelledError):
                    task.result()
                assert not (tmp_path / ".marivo/server/endpoints/total.json").exists()
            else:
                assert json.loads(task.result())["data"]["rows"] == [["10"]]
            if not detached:
                with (tmp_path / "descendant.lock").open() as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert service.active == 0 and not service.processes
        finally:
            if (tmp_path / "ready").exists():
                with contextlib.suppress(ProcessLookupError):
                    os.kill(int((tmp_path / "ready").read_text()), signal.SIGKILL)
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await service.close()

    asyncio.run(scenario())


def test_script_import_cannot_redirect_injected_session(tmp_path: Path) -> None:
    foreign = tmp_path / "other_project"
    foreign.mkdir()
    path = script(
        tmp_path,
        "return pd.DataFrame({'root': [str(session._runtime.store.project_root)]})",
    )
    path.write_text(
        f"import os\nos.environ['MARIVO_PROJECT_ROOT'] = {str(foreign)!r}\n" + path.read_text()
    )

    async def scenario() -> None:
        service = Refreshes(tmp_path, 1)
        try:
            result = json.loads(await service.run("total", budget()))
            assert result["data"]["rows"] == [[str(tmp_path)]]
            assert not (foreign / ".marivo").exists()
        finally:
            await service.close()

    asyncio.run(scenario())


def test_parent_process_loss_exits_worker_and_releases_inherited_lock(tmp_path: Path) -> None:
    script_with_descendant(tmp_path, pause=True)
    source = """
import asyncio
import sys
from pathlib import Path
from threading import Event
from marivo.server.refresh import Refreshes
from marivo.server.wire import Budget, Limits
limits = Limits()
asyncio.run(Refreshes(Path(sys.argv[1]), 1).run('total', Budget(limits, 'refresh', limits.deadline(), Event())))
"""

    async def scenario() -> None:
        with subprocess.Popen(
            [sys.executable, "-c", source, str(tmp_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        ) as parent:
            try:
                await wait_for_file(tmp_path / "ready")
                with pytest.raises(ServiceError) as failure, endpoint_lock(tmp_path, "total"):
                    pytest.fail("A running worker must retain the endpoint lock")
                assert failure.value.code == "refresh_in_progress"
                parent.kill()
                parent.wait(timeout=10)

                async def released() -> None:
                    while True:
                        try:
                            with endpoint_lock(tmp_path, "total"):
                                pass
                        except ServiceError as error:
                            assert error.code == "refresh_in_progress"
                            await asyncio.sleep(0.01)
                            continue
                        try:
                            with (tmp_path / "descendant.lock").open() as lock:
                                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            return
                        except BlockingIOError:
                            await asyncio.sleep(0.01)

                await asyncio.wait_for(released(), 10)
                with (tmp_path / "descendant.lock").open() as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                assert not (tmp_path / ".marivo/server/endpoints/total.json").exists()
            finally:
                if parent.poll() is None:
                    parent.kill()
                parent.wait(timeout=10)
                if (tmp_path / "ready").exists():
                    with contextlib.suppress(ProcessLookupError):
                        os.kill(int((tmp_path / "ready").read_text()), signal.SIGKILL)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "body,returned",
    [
        ("return None", "pd.DataFrame"),
        ("return pd.DataFrame({'value': [1]})", "mv.AnalysisFrame"),
        (
            "return session.values(columns={'value': None}, rows=(((), {'value': 1}),)).persist()",
            "mv.AnalysisFrame",
        ),
        (
            "other = mv.session.get_or_create('foreign')\nreturn other.values(columns={'value': None}, rows=(((), {'value': 1}),))",
            "mv.AnalysisFrame",
        ),
        ("return pd.DataFrame({'value': [1]})", "pd.DataFrame | mv.AnalysisFrame"),
    ],
)
def test_script_output_contract_preserves_current(tmp_path: Path, body: str, returned: str) -> None:
    script(tmp_path, "return pd.DataFrame({'value': [10]})")

    async def scenario() -> None:
        service = Refreshes(tmp_path, 1)
        try:
            await service.run("total", budget())
            old = current(tmp_path, "total")
            script(tmp_path, body, returned=returned)
            with pytest.raises(ServiceError) as failure:
                await service.run("total", budget())
            assert failure.value.code == "script_contract_error"
            assert failure.value.target == "analysis.results.service.scripts"
            assert current(tmp_path, "total") == old
            assert service.active == 0 and not service.processes
        finally:
            await service.close()

    asyncio.run(scenario())


def test_running_refresh_excludes_competitors_and_cancellation_reaps_worker(tmp_path: Path) -> None:
    script(tmp_path, "return pd.DataFrame({'value': [10]})")
    script(tmp_path, "return pd.DataFrame({'value': [30]})", name="other")

    async def scenario() -> None:
        service = Refreshes(tmp_path, 1)
        second = Refreshes(tmp_path, 1)
        try:
            await service.run("total", budget())
            old = current(tmp_path, "total")
            script(
                tmp_path,
                "Path('ready').touch()\nimport time\ntime.sleep(120)\nreturn pd.DataFrame({'value': [20]})",
            )
            task = asyncio.create_task(service.run("total", budget()))
            try:
                await wait_for_file(tmp_path / "ready")
                assert json.loads(read_result(tmp_path, old, budget()))["data"]["rows"] == [["10"]]
                for contender in (service, second):
                    with pytest.raises(ServiceError) as failure:
                        await contender.run("total", budget())
                    assert failure.value.code == "refresh_in_progress"
                with pytest.raises(ServiceError) as failure:
                    await service.run("other", budget())
                assert failure.value.code == "service_unavailable"
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert service.active == 0 and not service.processes
            assert current(tmp_path, "total") == old
            script(tmp_path, "return pd.DataFrame({'value': [40]})")
            updated = json.loads(await second.run("total", budget()))
            assert updated["data"]["rows"] == [["40"]]
        finally:
            await service.close()
            await second.close()

    asyncio.run(scenario())


def test_encoding_and_pointer_failure_never_switch_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script(tmp_path, "return pd.DataFrame({'value': [10]})")

    async def scenario() -> None:
        service = Refreshes(tmp_path, 1)
        try:
            await service.run("total", budget())
            old = current(tmp_path, "total")
            script(
                tmp_path,
                "from marivo.analysis.datasets.errors import DatasetConstructionError\n"
                "raise DatasetConstructionError(expected='a positive value', received='-1', "
                "repair='Supply a positive value.', location='input', help_target='session.values')",
            )
            with pytest.raises(ServiceError) as failure:
                await service.run("total", budget())
            assert failure.value.code == "refresh_failed"
            assert failure.value.repair == "Supply a positive value."
            assert failure.value.target == "analysis.session.values"
            assert current(tmp_path, "total") == old
            for body, returned, code in (
                (
                    "raise RuntimeError('injected computation failure')",
                    "pd.DataFrame",
                    "refresh_failed",
                ),
                (
                    "import pyarrow.parquet as pq\n"
                    "def fail(*args, **kwargs):\n    raise OSError('injected Parquet write failure')\n"
                    "pq.write_table = fail\nreturn pd.DataFrame({'value': [20]})",
                    "pd.DataFrame",
                    "result_publication_failed",
                ),
                (
                    "frame = session.values(columns={'value': None}, rows=(((), {'value': 20}),)).compute()\n"
                    "def fail(*args, **kwargs):\n    raise OSError('injected Store publication failure')\n"
                    "type(frame._local).publish = fail\nreturn frame",
                    "mv.AnalysisFrame",
                    "result_publication_failed",
                ),
            ):
                script(tmp_path, body, returned=returned)
                with pytest.raises(ServiceError) as failure:
                    await service.run("total", budget())
                assert failure.value.code == code
                assert current(tmp_path, "total") == old
            script(tmp_path, "return pd.DataFrame({'value': ['x' * 1000] * 1200})")
            limits = Limits(mib=1)
            with pytest.raises(ServiceError) as failure:
                await service.run("total", Budget(limits, "refresh", limits.deadline(), Event()))
            assert failure.value.code == "response_too_large"
            assert current(tmp_path, "total") == old
            script(tmp_path, "return pd.DataFrame({'value': [20]})")

            def fail(source: Path, target: Path) -> None:
                raise OSError("injected reference publication failure")

            monkeypatch.setattr("marivo.server.endpoints.os.replace", fail)
            with pytest.raises(ServiceError) as failure:
                await service.run("total", budget())
            assert failure.value.code == "result_publication_failed"
            assert current(tmp_path, "total") == old
        finally:
            await service.close()

    asyncio.run(scenario())
