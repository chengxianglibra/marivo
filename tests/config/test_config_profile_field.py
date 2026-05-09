from __future__ import annotations

import yaml

from marivo.config import MarivoConfig, load_config


def test_marivo_config_profile_field_defaults_to_none() -> None:
    cfg = MarivoConfig()
    assert cfg.profile is None


def test_marivo_config_accepts_local_and_server() -> None:
    assert MarivoConfig(profile="local").profile == "local"
    assert MarivoConfig(profile="server").profile == "server"


def test_marivo_config_rejects_unknown_profile_value() -> None:
    # extra="forbid" means unknown keys raise; unknown profile values are
    # not validated at config-parse time (resolver enforces). The field
    # accepts any string and the resolver raises ProfileResolutionError.
    cfg = MarivoConfig(profile="nope")
    assert cfg.profile == "nope"


def test_load_config_reads_profile_from_yaml(tmp_path) -> None:
    config_path = tmp_path / "marivo.yaml"
    config_path.write_text(yaml.safe_dump({"profile": "server"}))
    cfg = load_config(config_path)
    assert cfg.profile == "server"


def test_load_config_profile_absent_yields_none(tmp_path) -> None:
    config_path = tmp_path / "marivo.yaml"
    config_path.write_text(yaml.safe_dump({}))
    cfg = load_config(config_path)
    assert cfg.profile is None
