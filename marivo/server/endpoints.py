"""Project script discovery and atomic references to immutable results."""

from __future__ import annotations

import ast
import fcntl
import json
import logging
import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import TypeAlias

from marivo.server.wire import HELP, SCRIPTS_HELP, Budget, JsonObject, ServiceError, bad_request

_SEGMENT = r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*"
_ACTIVE: set[Path] = set()
_MUTEX = Lock()


def endpoint_name(value: str) -> str:
    if len(value) > 1024 or re.fullmatch(rf"{_SEGMENT}(?:/{_SEGMENT})*", value) is None:
        raise bad_request(
            "endpoint", "Invalid endpoint path.", "Use lowercase snake_case segments."
        )
    return value


def dataframe_id(value: str) -> str:
    if re.fullmatch(r"df_[0-9a-f]{32}", value) is None:
        raise bad_request("result_id", "Invalid DataFrame identity.", "Use a saved DataFrame ref.")
    return value


def missing(location: str) -> ServiceError:
    return ServiceError(
        "result_not_found",
        "No saved result exists at this identity.",
        location,
        "Check the saved ref, or explicitly refresh the named endpoint to create its first result.",
        HELP,
    )


def contract(location: str, received: str) -> ServiceError:
    return ServiceError(
        "script_contract_error",
        f"Invalid endpoint script: {received}.",
        location,
        "Define main(session: mv.Session) with one concrete AnalysisFrame or pandas.DataFrame return type.",
        SCRIPTS_HELP,
    )


def publication_error(location: str) -> ServiceError:
    return ServiceError(
        "result_publication_failed",
        "The result or its current reference could not be published.",
        location,
        "Check storage permissions and capacity; read the current reference before refreshing again.",
        HELP,
    )


def state_path(root: Path, relative: str) -> Path:
    path = root / ".marivo" / "server" / relative
    # Project state must not redirect either reads or writes outside this project.
    if any(item.is_symlink() for item in (path, *path.parents) if item != root.parent):
        raise ServiceError(
            "invalid_result_reference",
            "Service state uses an indirect filesystem path.",
            "state",
            "Keep service state in the bound project's local directory.",
            HELP,
        )
    return path


def _identity(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 4096
        and value.isprintable()
        and "/" not in value
        and "\\" not in value
        and value not in (".", "..")
    )


@dataclass(frozen=True)
class FrameReference:
    store_id: str
    session_id: str
    artifact_id: str

    def payload(self) -> JsonObject:
        return {
            "kind": "analysis_frame",
            "store_id": self.store_id,
            "ref": {
                "schema": "marivo.frame_ref/v1",
                "session_id": self.session_id,
                "artifact_id": self.artifact_id,
            },
        }


@dataclass(frozen=True)
class DataFrameReference:
    result_id: str

    def payload(self) -> JsonObject:
        return {"kind": "dataframe", "ref": self.ref()}

    def ref(self) -> JsonObject:
        return {"schema": "marivo.dataframe_ref/v1", "result_id": self.result_id}


ResultReference: TypeAlias = FrameReference | DataFrameReference


def read_result(root: Path, reference: ResultReference, budget: Budget) -> bytes:
    from marivo.server import dataframes
    from marivo.server.frames import FrameReads

    if isinstance(reference, DataFrameReference):
        return dataframes.report(root, reference.result_id, budget)
    frames = FrameReads(root)
    if frames.store_id() != reference.store_id:
        raise ServiceError(
            "invalid_result_reference",
            "The result belongs to a different Store.",
            "store_id",
            "Use the original project Store, or explicitly refresh the endpoint.",
            HELP,
        )
    return frames.report(reference.session_id, reference.artifact_id, budget)


def parse_reference(value: object) -> ResultReference:
    if isinstance(value, dict) and isinstance(ref := value.get("ref"), dict):
        if (
            value.get("kind") == "analysis_frame"
            and set(value) == {"kind", "store_id", "ref"}
            and set(ref) == {"schema", "session_id", "artifact_id"}
        ):
            store, session, artifact = value["store_id"], ref["session_id"], ref["artifact_id"]
            if (
                ref["schema"] == "marivo.frame_ref/v1"
                and isinstance(store, str)
                and isinstance(session, str)
                and isinstance(artifact, str)
                and all(_identity(item) for item in (store, session, artifact))
            ):
                return FrameReference(store, session, artifact)
        if (
            value.get("kind") == "dataframe"
            and set(value) == {"kind", "ref"}
            and set(ref) == {"schema", "result_id"}
        ):
            result_id = ref["result_id"]
            if (
                ref["schema"] == "marivo.dataframe_ref/v1"
                and isinstance(result_id, str)
                and re.fullmatch(r"df_[0-9a-f]{32}", result_id)
            ):
                return DataFrameReference(result_id)
    raise ServiceError(
        "invalid_result_reference",
        "The saved result reference is invalid.",
        "ref",
        "Restore the original reference, or explicitly refresh its endpoint.",
        HELP,
    )


@contextmanager
def publication_file(path: Path) -> Iterator[Path]:
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, name = tempfile.mkstemp(prefix=".publishing-", dir=path.parent)
        os.close(descriptor)
        temporary = Path(name)
        yield temporary
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        raise publication_error("state") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def current(root: Path, name: str) -> ResultReference:
    path = state_path(root, f"endpoints/{endpoint_name(name)}.json")
    try:
        value: object = json.loads(path.read_bytes())
    except FileNotFoundError:
        raise missing("endpoint") from None
    except (OSError, ValueError):
        raise ServiceError(
            "invalid_result_reference",
            "The current reference cannot be read.",
            "endpoint",
            "Restore its saved reference file.",
            HELP,
        ) from None
    return parse_reference(value)


def publish_current(root: Path, name: str, reference: ResultReference) -> None:
    with publication_file(state_path(root, f"endpoints/{endpoint_name(name)}.json")) as path:
        path.write_text(json.dumps(reference.payload(), separators=(",", ":")), encoding="utf-8")


@contextmanager
def endpoint_lock(root: Path, name: str) -> Iterator[int]:
    path = state_path(root, f"locks/{endpoint_name(name)}.lock")
    busy = ServiceError(
        "refresh_in_progress",
        "This endpoint is already being refreshed.",
        "endpoint",
        "Read its saved result or wait for the current refresh to finish.",
        HELP,
    )
    with _MUTEX:
        if path in _ACTIVE:
            raise busy
        _ACTIVE.add(path)
    descriptor: int | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise busy from None
        yield descriptor
    except OSError:
        raise publication_error("endpoint_lock") from None
    finally:
        if descriptor is not None:
            # Close instead of LOCK_UN: a still-running child retains exclusion.
            os.close(descriptor)
        with _MUTEX:
            _ACTIVE.discard(path)


def script_source(root: Path, name: str) -> tuple[Path, str]:
    path = root / "endpoints" / (endpoint_name(name) + ".py")
    base = root / "endpoints"
    if not path.resolve().is_relative_to(base.resolve()) or base.is_symlink():
        raise contract(name, "script resolves outside the endpoint directory")
    try:
        source = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ServiceError(
            "not_found",
            "The endpoint script does not exist.",
            "endpoint",
            "Restore the script, or read its saved result without refresh.",
            HELP,
        ) from None
    except (OSError, UnicodeError):
        raise contract(name, "script is not readable UTF-8 Python") from None
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as error:
        raise contract(name, f"syntax error at line {error.lineno}") from None
    entries = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main"
    ]
    if len(entries) != 1 or not isinstance(entries[0], ast.FunctionDef):
        raise contract(name, "expected one top-level synchronous main function")
    function = entries[0]
    args = function.args
    positional = [*args.posonlyargs, *args.args]
    if (
        len(positional) != 1
        or positional[0].arg != "session"
        or positional[0].annotation is None
        or function.returns is None
        or args.defaults
        or args.kwonlyargs
        or args.vararg
        or args.kwarg
    ):
        raise contract(
            name, "expected one annotated session parameter without defaults or variadic arguments"
        )
    return path, source


class EndpointScripts:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.names: set[str] = set()
        base = root / "endpoints"
        for directory, children, files in os.walk(base, followlinks=False):
            children[:] = [child for child in children if re.fullmatch(_SEGMENT, child)]
            for filename in sorted(files):
                if not filename.endswith(".py") or not re.fullmatch(_SEGMENT, filename[:-3]):
                    continue
                name = (Path(directory) / filename).relative_to(base).with_suffix("").as_posix()
                self.names.add(name)
                try:
                    script_source(root, name)
                except ServiceError as error:
                    logging.getLogger(__name__).warning("Endpoint %s: %s", name, error.message)

    def require(self, name: str) -> None:
        endpoint_name(name)
        if name not in self.names:
            raise ServiceError(
                "not_found",
                "The endpoint was not discovered by this service.",
                "endpoint",
                "Add its script under endpoints/ and restart the service.",
                HELP,
            )
        script_source(self.root, name)
