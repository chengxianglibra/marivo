"""Closed HTTP JSON sources execute through semantic Analysis inputs."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError

import pytest

import marivo.analysis as mv
from marivo._errors import MarivoError
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import catalog_value, terminal_runs

_PAYLOAD = {
    "data": {
        "vcdn_list": [{"id": "a", "amount": 2, "other": 20}, {"id": "b", "amount": 5, "other": 50}],
        "change_infos": [
            {"specificsource": [{"appid": "a", "amount": 2}, {"appid": "b", "amount": 5}]}
        ],
    }
}


@dataclass
class _Api:
    payload: object = field(default_factory=lambda: _PAYLOAD)
    status: int = 200
    raw: bytes | None = None
    requests: list[tuple[str, str, object, str | None]] = field(default_factory=list)


@contextmanager
def _api_server() -> Iterator[tuple[str, _Api]]:
    api = _Api()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.respond(None)

        def do_POST(self) -> None:
            self.respond(json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0")))))

        def respond(self, body: object) -> None:
            api.requests.append((self.command, self.path, body, self.headers.get("Authorization")))
            payload = api.raw if api.raw is not None else json.dumps(api.payload).encode()
            self.send_response(api.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", api
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _project(
    factory: Callable[[dict[str, str]], SemanticProject],
    sources: tuple[str, ...],
    *,
    datasource_args: str = "",
    separate_datasources: bool = False,
) -> None:
    files = {
        "ops/_domain.py": "import marivo.semantic as ms\nms.domain(name='ops', owner='test', default=True)\n"
    }
    model = "import marivo.datasource as md\nimport marivo.semantic as ms\n"
    for i, source in enumerate(sources):
        datasource = f"api{i}" if separate_datasources else "api"
        files[f"datasources/{datasource}.py"] = (
            f"import marivo.datasource as md\nmd.duckdb(name={datasource!r}{datasource_args})\n"
        )
        model += (
            f"entity{i} = ms.entity(name='entity{i}', datasource=ms.ref.datasource({datasource!r}), source={source}, primary_key=['id'])\n"
            f"amount{i} = ms.measure_column(name='amount{i}', entity=entity{i}, column='amount', additivity=ms.additive_all())\n"
            f"@ms.metric()\ndef total{i}():\n    return amount{i}.sum()\n"
        )
    files["ops/models.py"] = model
    factory(files)


def _source(url: str, *, method: str = "GET", amount: str = "amount", page: int = 1) -> str:
    if method == "POST":
        return (
            f"md.json({url!r}, method='POST', body={{'filter':{{'apps':['a','b']}},'page':{page}}}, "
            "records_path='$.data.change_infos', columns={'id':'specificsource[].appid','amount':'specificsource[].amount'})"
        )
    return (
        f"md.json({url!r}, query_params={{'page_number':{page},'page_size':100,'app':['a','b']}}, "
        f"records_path='$.data.vcdn_list', columns={{'id':'id','amount':{amount!r}}})"
    )


@pytest.mark.parametrize("method", ("GET", "POST"))
def test_closed_requests_support_observe_read_members_and_fixed_continuations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    method: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("TEST_HTTP_JSON_TOKEN", "synthetic-json-token")
    with _api_server() as (url, api):
        _project(
            semantic_project_factory,
            (_source(url + "/api", method=method),),
            datasource_args=f", http_scope={url + '/'!r}, http_bearer_token_env='TEST_HTTP_JSON_TOKEN'",
        )
        session = mv.session.get_or_create("http-json", timezone="UTC")
        entity = catalog_value(session.catalog, "ops.entity0", kind="entity")
        amount = catalog_value(session.catalog, "ops.entity0.amount0", kind="measure")
        total = catalog_value(session.catalog, "ops.total0", kind="metric")
        pending = session.observe({"value": total, "count": entity.count()})
        pending.show()
        assert api.requests == []
        fixed = pending.compute()
        assert fixed.to_pandas()[["value", "count"]].values.tolist() == [[7, 2]]
        assert len(api.requests) == 1
        command, path, body, authorization = api.requests[0]
        assert command == method and authorization == "Bearer synthetic-json-token"
        if method == "GET":
            assert path == "/api?page_number=1&page_size=100&app=a&app=b"
            assert body is None
        else:
            assert path == "/api" and body == {"filter": {"apps": ["a", "b"]}, "page": 1}
        members = session.members(entity)
        assert session.read(members, {"amount": amount}).compute().to_pandas()[
            "amount"
        ].tolist() == [2, 5]
        assert session.frame(members).compute().to_pandas().iloc[:, 0].tolist() == ["a", "b"]
        assert len(api.requests) == 3
        pending.compute()
        assert len(api.requests) == 4
        api.status = 503
        assert fixed.select({"double": fixed["value"] * 2}).compute().to_pandas()[
            "double"
        ].tolist() == [14]
        saved = fixed.persist()
        assert session.persisted_frame(saved.artifact_id).compute().to_pandas()[
            "value"
        ].tolist() == [7]
        assert len(api.requests) == 4
        assert session._runtime.store.resources(session.id) == ()


@pytest.mark.parametrize("same_entity", (True, False))
def test_one_response_is_shared_across_frames_and_distinct_projections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    same_entity: bool,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    with _api_server() as (url, api):
        shadow = Path(url)
        shadow.parent.mkdir(parents=True, exist_ok=True)
        shadow.write_text("[]")
        _project(semantic_project_factory, (_source(url), _source(url, amount="other")))
        session = mv.session.get_or_create("shared-json", timezone="UTC")
        first = session.observe(
            {
                "value": catalog_value(session.catalog, "ops.total0", kind="metric"),
                "again": catalog_value(session.catalog, "ops.total0", kind="metric"),
            }
        )
        reopened = mv.session.get_or_create("shared-json", timezone="UTC")
        second = reopened.observe(
            {
                "value": catalog_value(
                    reopened.catalog, "ops.total0" if same_entity else "ops.total1", kind="metric"
                )
            }
        )
        combined = first.align(second)
        assert combined.compute().to_pandas()[
            ["left.value", "left.again", "right.value"]
        ].values.tolist() == [[7, 7, 7 if same_entity else 70]]
        assert len(api.requests) == 1
        combined.persist()
        assert len(api.requests) == 2


@pytest.mark.parametrize("difference", ("GET", "POST", "datasource"))
def test_different_requests_or_datasource_configs_do_not_share_responses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    difference: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    with _api_server() as (url, api):
        method = "POST" if difference == "POST" else "GET"
        _project(
            semantic_project_factory,
            (
                _source(url, method=method),
                _source(url, method=method, page=1 if difference == "datasource" else 2),
            ),
            separate_datasources=difference == "datasource",
        )
        session = mv.session.get_or_create("different-json", timezone="UTC")
        first = session.observe(
            {"value": catalog_value(session.catalog, "ops.total0", kind="metric")}
        )
        second = session.observe(
            {"value": catalog_value(session.catalog, "ops.total1", kind="metric")}
        )
        assert first.align(second).compute().to_pandas()[
            ["left.value", "right.value"]
        ].values.tolist() == [[7, 7]]
        assert len(api.requests) == 2


@pytest.mark.parametrize("method", ("GET", "POST"))
def test_missing_parameters_in_any_frame_fail_before_all_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    method: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    with _api_server() as (url, api):
        incomplete = (
            _source(url, method=method)
            .replace("'page_number':1", "'page_number':md.source_param('page')")
            .replace("'page':1", "'page':md.source_param('page')")
        )
        _project(semantic_project_factory, (_source(url), incomplete))
        session = mv.session.get_or_create("missing-json", timezone="UTC")
        first = session.observe(
            {"value": catalog_value(session.catalog, "ops.total0", kind="metric")}
        )
        second = session.observe(
            {"value": catalog_value(session.catalog, "ops.total1", kind="metric")}
        )
        with pytest.raises(AnalysisError) as caught:
            first.align(second).compute()
        assert caught.value.expected and "ops.entity1" in caught.value.expected
        assert caught.value.received and "page" in caught.value.received
        assert caught.value.repair and "md.json" in caught.value.repair.action
        assert api.requests == [] and terminal_runs(session) == ()


@pytest.mark.parametrize("failure", ("http", "json", "path", "empty", "null", "type"))
def test_http_failures_are_structured_and_close_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    failure: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    closed: list[SourceSession] = []
    original_close = SourceSession.close

    def close(source: SourceSession) -> None:
        closed.append(source)
        original_close(source)

    monkeypatch.setattr(SourceSession, "close", close)
    with _api_server() as (url, api):
        if failure == "http":
            api.status = 503
        elif failure == "json":
            api.raw = b"invalid JSON"
        elif failure == "path":
            api.payload = {"data": {"vcdn_list": {}}}
        elif failure == "empty":
            api.payload = {"data": {"vcdn_list": []}}
        elif failure == "null":
            api.payload = {"data": {"vcdn_list": [{"id": "a", "amount": None}]}}
        elif failure == "type":
            api.payload = {"data": {"vcdn_list": [{"id": "a", "amount": [1, 2]}]}}
        _project(semantic_project_factory, (_source(url),))
        session = mv.session.get_or_create("failed-json", timezone="UTC")
        pending = session.observe(
            {"value": catalog_value(session.catalog, "ops.total0", kind="metric")}
        )
        with pytest.raises(MarivoError) as caught:
            pending.persist()
        error = caught.value
        assert error.expected and error.received and error.repair
        if failure == "http":
            assert "503" in error.received
        assert "no business row was read" not in str(error).lower()
        assert len(api.requests) == 1 and closed
        assert terminal_runs(session) == () and session._runtime.store.resources(session.id) == ()


@pytest.mark.parametrize("method", ("GET", "POST"))
def test_http_error_response_is_closed_while_exception_is_retained(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    method: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    with _api_server() as (url, api):
        api.status = 503
        _project(semantic_project_factory, (_source(url, method=method),))
        session = mv.session.get_or_create("closed-http-error", timezone="UTC")
        pending = session.observe(
            {"value": catalog_value(session.catalog, "ops.total0", kind="metric")}
        )
        with pytest.raises(AnalysisError) as caught:
            pending.compute()
        cause = caught.value.__cause__
        assert isinstance(cause, HTTPError)
        try:
            assert cause.code == 503
            assert cause.fp is not None and cause.fp.closed
            assert len(api.requests) == 1
            assert terminal_runs(session) == ()
            assert session._runtime.store.resources(session.id) == ()
        finally:
            cause.close()
