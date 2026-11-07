"""One isolated script invocation, with a dedicated result channel."""

from __future__ import annotations

import contextlib
import importlib
import inspect
import json
import logging
import os
import signal
import socket
import sqlite3
import struct
import sys
from importlib.machinery import ModuleSpec
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from threading import Event, Thread
from types import FunctionType, ModuleType
from typing import get_type_hints
from uuid import uuid4

import pandas as pd

import marivo.analysis as mv
from marivo.analysis.core.frame_data import StoredFrameBacking
from marivo.analysis.errors import AnalysisError
from marivo.server.dataframes import publish
from marivo.server.endpoints import (
    FrameReference,
    ResultReference,
    contract,
    publication_error,
    read_result,
    script_source,
)
from marivo.server.wire import HELP, Budget, JsonObject, Limits, ServiceError


def execute(root: Path, endpoint: str, budget: Budget) -> tuple[ResultReference, bytes]:
    path, source = script_source(root, endpoint)
    # Bind the package to this project even when another installed distribution
    # provides a regular "endpoints" package ahead of namespace discovery.
    directory = root / "endpoints"
    initializer = directory / "__init__.py"
    if initializer.is_file():
        spec = spec_from_file_location(
            "endpoints", initializer, submodule_search_locations=[str(directory)]
        )
        assert spec is not None
    else:
        spec = ModuleSpec("endpoints", loader=None, is_package=True)
        spec.submodule_search_locations = [str(directory)]
    package = module_from_spec(spec)
    sys.modules["endpoints"] = package
    if spec.loader is not None:
        spec.loader.exec_module(package)
    module_name = "endpoints." + endpoint.replace("/", ".")
    importlib.import_module(module_name.rpartition(".")[0])
    module = ModuleType(module_name)
    module.__file__ = str(path)
    module.__package__ = module_name.rpartition(".")[0]
    sys.modules[module_name] = module
    exec(compile(source, str(path), "exec"), module.__dict__)
    entry: object = module.__dict__.get("main")
    if (
        not isinstance(entry, FunctionType)
        or inspect.iscoroutinefunction(entry)
        or inspect.isgeneratorfunction(entry)
    ):
        raise contract(endpoint, "expected a synchronous main function returning one result")
    parameters = tuple(inspect.signature(entry).parameters.values())
    if (
        len(parameters) != 1
        or parameters[0].name != "session"
        or parameters[0].kind
        not in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        or parameters[0].default is not inspect.Parameter.empty
    ):
        raise contract(endpoint, "expected main(session) without defaults or additional parameters")
    try:
        hints = get_type_hints(entry)
    except (NameError, TypeError, ValueError):
        raise contract(endpoint, "type annotations could not be resolved") from None
    returned: object = hints.get("return")
    if hints.get("session") is not mv.Session or returned not in (mv.AnalysisFrame, pd.DataFrame):
        raise contract(
            endpoint,
            "expected mv.Session and one concrete AnalysisFrame or pandas.DataFrame return annotation",
        )
    budget.check()
    # Script imports may configure their own environment; the injected Session
    # still belongs to the service's bound project.
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    session = mv.session.get_or_create("api-refresh-" + uuid4().hex)
    result: object = entry(session)
    budget.check()
    reference: ResultReference
    if returned is mv.AnalysisFrame and isinstance(result, mv.AnalysisFrame):
        if result._local.owner != session._runtime.local.owner or isinstance(
            result._backing, StoredFrameBacking
        ):
            raise contract(endpoint, "expected an unpersisted Frame owned by the supplied Session")
        completed = result.compute()
        budget.check()
        try:
            saved = completed.persist()
        except (AnalysisError, OSError, sqlite3.DatabaseError):
            logging.getLogger(__name__).exception("Endpoint %s publication failed", endpoint)
            raise publication_error("analysis_frame") from None
        reference = FrameReference(
            session._runtime.store.store_id, saved.session_id, saved.artifact_id
        )
    elif returned is pd.DataFrame and isinstance(result, pd.DataFrame):
        reference = publish(root, result, session.id)
    else:
        raise contract(
            endpoint,
            f"main returned {type(result).__name__}, inconsistent with its declared output",
        )
    budget.check()
    return reference, read_result(root, reference, budget)


def _watch_parent(channel: socket.socket) -> None:
    with contextlib.suppress(OSError):
        channel.recv(1)
    # Descendants belong to this refresh too, including inherited pipes and locks.
    os.killpg(os.getpgrp(), signal.SIGKILL)


def main() -> None:
    root, endpoint = Path(sys.argv[1]), sys.argv[2]
    channel = socket.socket(fileno=int(sys.argv[3]))
    limits = Limits(int(sys.argv[4]), int(sys.argv[5]))
    budget = Budget(limits, "refresh", float(sys.argv[6]), Event())
    Thread(target=_watch_parent, args=(channel,), daemon=True).start()
    sys.path.insert(0, str(root))
    header: JsonObject
    body = b""
    try:
        reference, body = execute(root, endpoint, budget)
        header = {"reference": reference.payload(), "size": len(body)}
    except ServiceError as error:
        header = error.payload()
    except (Exception, SystemExit) as error:
        logging.getLogger(__name__).exception("Endpoint %s failed", endpoint)
        repair = "Inspect the endpoint script and service logs, then explicitly refresh again."
        target = HELP
        if isinstance(error, AnalysisError) and error.repair is not None:
            repair = error.repair.action
            help_target = error.repair.help_target
            target = help_target.surface
            if help_target.canonical_id is not None:
                target += "." + help_target.canonical_id
        header = ServiceError(
            "refresh_failed", f"The script raised {type(error).__name__}.", endpoint, repair, target
        ).payload()
    data = json.dumps(header, separators=(",", ":")).encode()
    channel.sendall(struct.pack("!I", len(data)) + data + body)
    # The daemon watcher dies with the interpreter; the parent retains its socket until wait().


if __name__ == "__main__":
    main()
