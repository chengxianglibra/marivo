"""Select the Hypothesis settings for an opt-in formal run."""

import os
from importlib import import_module
from typing import Protocol, cast


class _SettingsProfiles(Protocol):
    def register_profile(
        self, name: str, *, max_examples: int, deadline: None, derandomize: bool = False
    ) -> None: ...

    def load_profile(self, name: str) -> None: ...


def pytest_configure() -> None:
    profile = os.environ.get("MARIVO_FORMAL_PROFILE")
    if profile is None:
        return
    # pytest can load this conftest during ordinary discovery without the formal extra.
    settings = cast("_SettingsProfiles", import_module("hypothesis").settings)
    settings.register_profile("ci", derandomize=True, max_examples=30, deadline=None)
    settings.register_profile("deep", derandomize=False, max_examples=300, deadline=None)
    settings.load_profile(profile)
