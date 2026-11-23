"""Fresh-process checks for core APIs, selected drivers and installed resources."""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.metadata
import importlib.util
import json
import sys
from pathlib import Path


class MissingNativeMySQL(importlib.abc.MetaPathFinder):
    """Fail if a selected backend indirectly imports the retired C driver."""

    def find_spec(self, fullname: str, path: object = None, target: object = None) -> None:
        if fullname == "MySQLdb" or fullname.startswith("MySQLdb."):
            raise ModuleNotFoundError("native MySQL driver unavailable", name=fullname)


if __name__ == "__main__":
    from tests.packaging.wheel_probe import assert_installed_origin

    assert_installed_origin()
    mode, argument, destination = sys.argv[1:]
    if mode == "surface":
        from tests.packaging.wheel_surface import public_snapshot

        report = public_snapshot()
    else:
        if mode == "dependencies":
            sys.meta_path.insert(0, MissingNativeMySQL())
        from tests.packaging.dependency_probe import dependencies, missing_driver, saved_read

        report = (
            dependencies(argument)
            if mode == "dependencies"
            else saved_read(Path(argument))
            if mode == "saved"
            else missing_driver(argument)
        )
    report["origin"] = assert_installed_origin()
    Path(destination).write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
