"""Prove concrete R9.6 baseline reuse across the explicitly scoped repairs."""

from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVES = {
    "devtools/r96_cost_scenarios.py": "original_cost_scenarios.py",
    "devtools/analysis_r9_cost.py": "original_cost_collector.py",
    "scripts/r96_cost_results.py": "original_cost_results.py",
}
TEST_ONLY = {"tests/test_r96_cost_scenarios.py"}
ADDED = {"scripts/r96_cost_reuse.py", "tests/test_r96_cost_reuse.py"}
PRESSURE_ARCHIVES = {**ARCHIVES, "scripts/r96_cost_reuse.py": "original_cost_reuse.py"}
PRESSURE_CHANGED = {
    "devtools/r96_cost_scenarios.py",
    "scripts/r96_cost_results.py",
    "scripts/r96_cost_reuse.py",
    "tests/test_r96_cost_reuse.py",
    *TEST_ONLY,
}
PROTECTED = {
    "devtools/r96_cost_observer.py",
    "devtools/r96_cost_cold.py",
    "tests/shared_fixtures.py",
    "tests/r9_source_cases.py",
    "tests/r95_driver_audit.py",
}
DIRECT_SOURCE = "marivo/analysis/materialization/graph_source_execution.py"
DIRECT_ARCHIVE = "original_graph_source_execution.py"
DIRECT_TEST_ONLY = {
    "tests/test_analysis_lowering_r34.py",
    "tests/test_sqlite_semantic_integration.py",
    "tests/test_cutover_documentation_examples.py",
}
DIRECT_ADDED = {"tests/test_r96_native_direct.py"}
KEYED_CHANGED = {
    "devtools/r96_cost_scenarios.py",
    "devtools/analysis_r9_cost.py",
    "scripts/r96_cost_results.py",
    "scripts/r96_cost_reuse.py",
    "tests/test_r96_cost_scenarios.py",
    "tests/test_r96_cost_reuse.py",
    "tests/test_r96_cost_results.py",
    "tests/test_r96_cost_audit.py",
}
KEYED_ADDED = {"devtools/r96_cost_fixture_layout.py", "tests/test_r96_cost_schedule.py"}
LAYOUT_SHA256 = "7e33263db093cca36dc147a736279da4161624521bc02371941391c0eac11934"
KEYED_HELPERS = {
    "_keyed_rows",
    "_keyed_equal",
    "_keyed_parts",
    "_keyed_payload",
    "_keyed_fact",
    "_keyed_instant",
    "_keyed_facts",
    "_keyed_grid",
    "_keyed_time_rows",
    "_keyed_daily",
    "_keyed_input",
    "_keyed_association",
    "_keyed_forecast",
    "_keyed_deviation",
    "_keyed_runs",
    "_keyed_events",
    "_keyed_oracle",
}
KEYED_IMPORT_CODES = (
    "from marivo.analysis.core.model import DomainSignature",
    "from marivo.analysis.materialization import statistical_execution as _statistics",
)
RSS_AUTHORITY = (
    "Reused RSS and lifecycle peaks remain original-candidate observations; execution-path "
    "equivalence does not prove equal current-harness footprint or simultaneous memory usage"
)
OPTIMIZATION_ARCHIVES = {
    "marivo/analysis/materialization/graph_exchange.py": "original_graph_exchange.py",
    "marivo/analysis/materialization/graph_preparation.py": "original_graph_preparation.py",
}
OPTIMIZATION_HASHES = {
    "marivo/analysis/materialization/graph_exchange.py": (
        "4220c8c1191bca87943b80594513bdd07ddaf8d0b1463150cfc14ab24ec9db99",
        "9b3a55a566704721865544551d6aa08499155769a651dd3aa7e5d917060cbafd",
    ),
    "marivo/analysis/materialization/graph_preparation.py": (
        "7aac854af859d6895c4450142baeb02564b573a923a93c54709479b2915f436d",
        "9b5bf02fee8ba0459238dc9a01a381661a50aff8dbd5952619996eafb1e42317",
    ),
}
OPTIMIZATION_TESTS = {
    "tests/test_r96_exchange_validation.py": "5f60ba5f617bbe517ee6b0280274189322c68d1f4e1c5968e027bd82ca8422ed",
    "tests/test_r96_preparation_cache.py": "e7af198a4c68c2c6290fe2d45df881450d311d6bfa5bfd572910cd9c2f7dc683",
}
PERFORMANCE_AUTHORITY = (
    "Reused successful behavior is backed by concrete preserved paths and mechanism regressions; "
    "timing, RSS and deployment observations retain their original execution candidate and cohort"
)
FILE_BUILTIN = "marivo/analysis/methods/builtin.py"
FILE_BUILTIN_HASHES = (
    "3dbb53734c51799d37da4277513936b59d4376bcd5d140963450c3a8ceab29a3",
    "fcbdbd7c9c93c48de27fa6035f09e2b16636a7301ef9ef5b041a98cdf7024c06",
)
FILE_BRANCHES = {
    "deviation": "8b6ed36a105d3b5bcdf250bfc39d2d46b66222fd64a1525aea80880902971db3",
    "state_rollup": "55d55cf64d17b0442d82644bdb437b5179e6195cbca632b2ec7270eed35d2a4b",
}
FILE_TESTS = {
    "tests/test_r94_file_admission.py": "6e046cbe41f1d1cec47ce78a277da6ad90987b78ebb1dfec8466329a96a71cd5",
    "tests/test_r96_local_file_cost_routes.py": "2b5c30eda7470aa48765e80e13a8192e3d64026d7ce5dec4e0e6fbe619b4b726",
    "tests/test_r92_sqlite_decimal_help.py": "c7b0bba71aec45aabc431f0558a3fa858b7a06a7b534204c429adb7163af4aa9",
    "tests/test_r96_cost_audit.py": "059f4d65bfbba79db254170c1af3e8c7650dd1696d2e0cc11eb58e4caa0ac29f",
}
FILE_FIXED_SOURCES = {
    "scripts/r9_qualification_requirements.py": "01af54d102edc24b77061f6c5049309b74754bfc62a61376ec77db6f60596d4d",
    "tests/test_full_algebra_backend_matrix.py": "67d42d7727113ea8b3fba58dd17d4e621334873cc184d5dcf31039cf8488cff4",
    "scripts/r96_cost_results.py": "266d767045a68a5cbcaf9a76e22dde6d058f11e96866fbcf0c871c2631c09db9",
    "tests/r9_source_cases.py": "33b155ac2cf0ece9e8318fcdcd99efa18f0091fd3ed17af82fd2aae39e14cb1c",
    "tests/test_r92_source_profiles.py": "1104cd4030c7fc002a015fdb910fe9f98dba8ed1e756f1e848c2b4bdd6fec643",
    "marivo/datasource/authoring.py": "362c297ea9711683e5d0c6d3f0ff62051ba56f47ecc150f459ed09b3c6f6bce6",
    "marivo/datasource/_capabilities/registry.py": "64564f6ffc1c28deb00e76a60d8fbeebfcd232684569ce0690a36824abaa629f",
}
FILE_CHANGED = {
    "scripts/r9_qualification_requirements.py",
    "tests/test_full_algebra_backend_matrix.py",
    FILE_BUILTIN,
    "scripts/r96_cost_results.py",
    "scripts/r96_cost_reuse.py",
    "tests/test_r96_cost_audit.py",
    "tests/test_r96_cost_reuse.py",
    "tests/test_r94_file_admission.py",
    "tests/test_r92_source_profiles.py",
    "tests/r9_source_cases.py",
    "marivo/datasource/authoring.py",
    "marivo/datasource/_capabilities/registry.py",
}
FILE_ADDED = {"tests/test_r96_local_file_cost_routes.py", "tests/test_r92_sqlite_decimal_help.py"}
EFFICIENCY_ORIGINAL_SHA256 = "c691f94c484b060b371f54eabda46b9aa71422b1bbb777728c633cc3d5d1857a"
EFFICIENCY_CHANGED = {
    "devtools/analysis_r9_cost.py",
    "scripts/r96_cost_results.py",
    "scripts/r96_cost_reuse.py",
    "tests/test_r96_cost_audit.py",
    "tests/test_r96_cost_results.py",
    "tests/test_r96_cost_reuse.py",
    "tests/test_r96_cost_schedule.py",
    "tests/test_r96_cost_scenarios.py",
    "tests/multisource_environment/clickhouse_analysis.py",
    "tests/test_r96_physical_cost_fixtures.py",
}
EFFICIENCY_BOUNDARY_SOURCES = {
    "tests/test_r96_extension_boundaries.py",
    "tests/test_datasource_adapter_contract.py",
    "tests/test_analysis_dsl_exchange.py",
    "tests/test_analysis_domain_preparation_r72.py",
    "tests/conftest.py",
    "tests/shared_fixtures.py",
}
PHYSICAL_BINDING_ORIGINAL_SHA256 = (
    "d5e44141c264bc0cac902dc82522025be295e64653346007dab5e4f3b826a270"
)
PHYSICAL_BINDING_CHANGED = {
    "devtools/r96_cost_scenarios.py",
    "scripts/r96_cost_results.py",
    "scripts/r96_cost_reuse.py",
    "tests/test_r96_cost_results.py",
    "tests/test_r96_cost_reuse.py",
    "tests/test_r96_physical_cost_fixtures.py",
}
PHYSICAL_BINDING_AST: dict[str, dict[str, tuple[str | None, str]]] = {
    "devtools/r96_cost_scenarios.py": {
        "_data": (
            "7ee8f404769c71cc82f5f1bd88d84f9786c2d01f6e67ed440235ddb55e3a88d4",
            "0b136ad2fb531db3b69ade7ca241b588512e537aecc12c1e1f1c430167caa5ca",
        ),
    },
    "scripts/r96_cost_results.py": {
        "_physical_fixed_bindings": (
            "d2bbf7e8750ceb5f8f26d9546024c25c125180f24e2082e7c915dcb3e64cf99a",
            "d063566ef27e879df700867ef6278c4d811ae41698512534209cfe3ffd2c9298",
        ),
        "_retained_boundary_candidate": (
            "f994a14eb740051dfadc46a29a572012325b9ab59b1c21d27d801a31de986741",
            "62081d4567ecbbc0fbe127f56e24030c7d7cf1123afff87f6c843a10d4f9b8a0",
        ),
        "_retained_physical_candidate": (
            None,
            "9e2ab87d27614238cb75ffac7c8d9d67c561b009c9f02139d4b9c53e28c3aebd",
        ),
        "audit": (
            "75602c346bd74e1c68bb9972b3ce98cb538b6d8942434dc152a9d3d7b0a0b85b",
            "66ba188e97118bf7346031f475f8bb5af4b6cdf674f9d9b8172f95cdccf347ac",
        ),
    },
    "scripts/r96_cost_reuse.py": {
        "_efficiency_reuse": (
            "f51fe53391ee1f05716fbe524d7fe9d5ae6cd841813d8c4c44d71de5f5dd09ba",
            "200704470784273abc45ed58dad77a25c4470877d660007a3b97be39da09c30c",
        ),
        "_file_source": (
            "fd68647365b2a68de7c0628b78060c18e64ef804d8c759a64a4c224da81e0499",
            "644bd6d4ae031553775aa6fd8df1e7836c286d302292bda48ceafe39bff9f354",
        ),
        "_physical_binding_reuse": (
            None,
            "741ec430e7487381c266ad04fe30ae2d67bed4b86c047352874f58e3754e2fab",
        ),
        "_physical_binding_source_unchanged": (
            None,
            "487236157052a4acd41bf4203c3b8bd8e7cbc6c149944ab336c2976827254faf",
        ),
        "eligible_physical_binding_sample": (
            None,
            "f69a76535817d9e7d376fe351f46cca922e9471c8dc2e2dd99a3775e39ed40a2",
        ),
        "validate_current_reuse": (
            "a44b69cc21374b7162c41f510b3e9a00ed3173911abd669c9ba588c33be62dd3",
            "00262a4312d2e75b7fb0e6f44720c3300768948b5102e2cd18d14bc4b4bff70c",
        ),
    },
    "tests/test_r96_cost_results.py": {
        "test_physical_fixed_cover_requires_the_actual_source_offline_producer_binding": (
            "057059a724ed144d5b106e6a7c0cb8db55fc85d152c8b9b4a13f3d6442b2298e",
            "57eabf62c7a8fe0b2e4b28e2299c7e4d970ed974c8cb28480965da364802b873",
        ),
        "test_physical_repair_preserves_the_actual_c7_boundary_candidate_chain": (
            None,
            "0fe03cfd6c8b869c8bccb23ddea26e0a2023724af993f49a4cae4dd02d3433d6",
        ),
    },
    "tests/test_r96_cost_reuse.py": {
        "test_physical_binding_authority_retains_only_actual_c8_distributed_scope": (
            None,
            "087009e6fe466325f732ea6c7bd9770a552c312abc1b44d4c46f8c399be19e12",
        ),
        "test_physical_binding_repair_requires_exact_reviewed_owner_ast": (
            None,
            "5426ea769d6a085765875522a6d84ea3f073abd5ea35c7ce36db0e39283691cf",
        ),
        "test_physical_binding_reuse_preserves_products_and_actual_predecessor": (
            None,
            "58a46b98591998630e392079c717818a7d77a03584bd1e33aadc9f21bb82e13f",
        ),
    },
    "tests/test_r96_physical_cost_fixtures.py": {
        "test_cost_fixture_declares_trino_microsecond_time_only": (
            None,
            "3295f43e6fa95c011b370f14b9f11f83dc1b6da88fe1f3811d630687871f3ed3",
        ),
    },
}
REMAINING_1K_ORIGINAL_SHA256 = "619bee5b6db92cea4a3b57d3aaaf14379bad9be48a5d9a6d90e0dc83c2ad1ace"
REMAINING_1K_CHANGED = {
    "devtools/analysis_r9_cost.py",
    "scripts/r96_cost_results.py",
    "scripts/r96_cost_reuse.py",
    "tests/test_r96_cost_schedule.py",
    "tests/test_r96_cost_results.py",
    "tests/test_r96_cost_reuse.py",
}
REMAINING_1K_AST: dict[str, dict[str, tuple[str | None, str]]] = {
    "devtools/analysis_r9_cost.py": {
        "_physical_fixed_binding": (
            "1b2747b43895e0311748d6cc44f96822f190618ac42d051ffbe52cc7eed96670",
            "d09f638f65adafdd7558df73b16378a751173202840ca4a248982b25d699b9ff",
        ),
        "run_group": (
            "59a551bf1c4aa473b490c19ef52c1a6db94383fba0d71ce25c98c0716c577e6a",
            "d069d862856e7a35f6d9cc83fb33b424a2a32714e9d0a0496d1b585e07d9806e",
        ),
        "collect": (
            "abefec7ea324b5bbadf7ac71101966b5af9fb312102a900d865e3a393550a93c",
            "917e2ece75a02daaa74998a49439cd60550a4e5ea289524e74854ff9db7c5b20",
        ),
        "main": (
            "556ebcbb9e89be4902e6482c9b13b6e1b343f693e6dd825835db21aff03d092a",
            "676ae5f1ac7b6cbf0d1343418b02ceae2f5d9850e0f977719325b485cc0dc366",
        ),
    },
    "scripts/r96_cost_results.py": {
        "_efficiency_manifest": (
            "c8309644501c0b0ea6718354fdf86d49798a799781ac373260d566f752cacbf3",
            "11ba60bb386caf4b557ed4151f96e9293eec7c0f4d4543436c406fff5d0d80ba",
        ),
        "_retained_boundary_candidate": (
            "62081d4567ecbbc0fbe127f56e24030c7d7cf1123afff87f6c843a10d4f9b8a0",
            "d588e1105e1346ba259d67e53811662ffa3491e7b143970d1bf7593d11e13e6a",
        ),
        "_retained_physical_candidate": (
            "9e2ab87d27614238cb75ffac7c8d9d67c561b009c9f02139d4b9c53e28c3aebd",
            "7fc4169b38475da0f1710dd13f6c42f94c1e8e43a198f0fb416b42e49db9d725",
        ),
        "_physical_fixed_bindings": (
            "d063566ef27e879df700867ef6278c4d811ae41698512534209cfe3ffd2c9298",
            "654f0ed72b84c5a56f0918fd12f4dc85ab88c6a82586559d6ffcbc59a1e3a52c",
        ),
        "audit": (
            "66ba188e97118bf7346031f475f8bb5af4b6cdf674f9d9b8172f95cdccf347ac",
            "5ad5ff340668db965a4682fd6784e3bb31a0f1baa959dfd7f2522c41c80f58ec",
        ),
    },
    "scripts/r96_cost_reuse.py": {
        "_file_source": (
            "644bd6d4ae031553775aa6fd8df1e7836c286d302292bda48ceafe39bff9f354",
            "9590b21f376f1aee6b7383831d356eadb43175b76a9a9509ce231bd6af5d8bfd",
        ),
        "_physical_binding_reuse": (
            "741ec430e7487381c266ad04fe30ae2d67bed4b86c047352874f58e3754e2fab",
            "8f49e778e32f7255c2e9000de07bd82793d5eca9e3faf90f9ac446f7284ea30f",
        ),
        "_remaining_1k_source_unchanged": (
            None,
            "d1d3d55f58db9fa91bced4649cdf436a6629fb8d63d40673fb8277657058f2a4",
        ),
        "_remaining_1k_reuse": (
            None,
            "d9a7f5eff6b3a5802b003060fcc1e9cd9596153caa3d63530a2b0126356cd36e",
        ),
        "validate_current_reuse": (
            "00262a4312d2e75b7fb0e6f44720c3300768948b5102e2cd18d14bc4b4bff70c",
            "4386c1b901020d2adbe17d399d0e620297011dc1e77f14015579312202dfffe3",
        ),
    },
    "tests/test_r96_cost_results.py": {
        "test_efficiency_audit_preserves_alias_ids_without_inventing_scale_timings": (
            "d75497effe23155eac18b30c438c15c9f2ce910c77bf3a29e450e96e14fcb9f9",
            "0b6e12820d2f0d2595e3864548de52e684d6c47c1bd9c25528a34bafcdcdf85c",
        ),
        "test_physical_fixed_cover_requires_the_actual_source_offline_producer_binding": (
            "57eabf62c7a8fe0b2e4b28e2299c7e4d970ed974c8cb28480965da364802b873",
            "1b722a93205519de23d08235a8ba395f125cf8d2e4fd517a27bf7ee0406caef2",
        ),
        "test_physical_repair_preserves_the_actual_c7_boundary_candidate_chain": (
            "0fe03cfd6c8b869c8bccb23ddea26e0a2023724af993f49a4cae4dd02d3433d6",
            "02d81b495a706407d64f6e2ec6515ccca9dc12c0c5cb6feb3f58f9dc84d4690c",
        ),
        "test_remaining_1k_manifest_refuses_unapproved_scales_and_extra_probes": (
            None,
            "b229cbbe109f0ce8b43b59f2bd6ad464c6ae108ee390bd89086ec8dc1a10f7de",
        ),
    },
    "tests/test_r96_cost_reuse.py": {
        "test_remaining_1k_snapshot_overlay_keeps_actual_c8_collector_bytes": (
            None,
            "bf0a690dfb463c30f6f7de3a1922a70b26a54cd6fc8eeee0fb826bc801d0a5dd",
        ),
        "test_remaining_1k_authority_requires_exact_owner_ast_and_unchanged_oracle": (
            None,
            "a21a6d37d5dd397acc93f93ed04992d5c3076d411ed5df2e1b470b205c857426",
        ),
        "test_remaining_1k_authority_preserves_the_original_nine_repair_objects": (
            None,
            "1d5fce72588ea69e22a34992e718b0fd2b839b666b0157da75547b60ee8eba5f",
        ),
        "test_remaining_1k_reuse_rejects_product_or_snapshot_drift": (
            None,
            "c960504da57cae0303ea6ffc5d9fa45b126147d22cb7019129fec5aae9e90a05",
        ),
    },
    "tests/test_r96_cost_schedule.py": {
        "remaining_1k_args": (
            None,
            "b8efdbeaaa6230fe216a5810a467e74cce32cecb3ebe689b59d0ad6402da13ef",
        ),
        "test_remaining_1k_schedule_measures_40_formal_groups_without_probes": (
            None,
            "3fc53fb2875924d6eccea1c30d89bdffc18ddd58c121f85e3700a575acafd471",
        ),
        "test_remaining_1k_schedule_rejects_extra_scale_or_already_completed_scope": (
            None,
            "d0aff040c920495d71bcfeb1c7a44530e4fe58088a3bddddfc6f469882e31de6",
        ),
        "test_remaining_1k_rounds_capture_once_and_bind_only_the_local_warmup": (
            None,
            "4dc0591d1e92881064df311b441d62caaa254f111edea7cd9bf270dea01dd450",
        ),
        "test_remaining_1k_schedule_never_accepts_an_extra_functional_probe": (
            None,
            "516b0592b50635f84bc5381a9d34a8d526d2404ae129da4ee2140bc0e1581c51",
        ),
    },
}


def _obj(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("Reuse requires a string-keyed candidate object")
    return value


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hashes(candidate: Mapping[str, object]) -> dict[str, str]:
    source = _obj(candidate["source_sha256"])
    if not all(isinstance(value, str) for value in source.values()):
        raise ValueError("Candidate file hashes must be strings")
    result = {key: str(value) for key, value in source.items()}
    encoded = json.dumps(result, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"
    if candidate.get("content_sha256") != _hash(encoded):
        raise ValueError("Candidate aggregate does not bind its file hashes")
    return result


def _dump(node: ast.AST) -> str:
    return ast.dump(node, include_attributes=False)


def _function(module: ast.Module, name: str, owner: str | None = None) -> ast.FunctionDef:
    body = module.body
    if owner is not None:
        owners = [node for node in body if isinstance(node, ast.ClassDef) and node.name == owner]
        if len(owners) != 1:
            raise ValueError(f"Expected one {owner} owner")
        body = owners[0].body
    found = [node for node in body if isinstance(node, ast.FunctionDef) and node.name == name]
    if len(found) != 1:
        raise ValueError(f"Expected one {owner or 'module'}.{name} function")
    return found[0]


def _statement(code: str) -> ast.stmt:
    return ast.parse(code).body[0]


def _argument(
    function: ast.FunctionDef,
    name: str,
    annotation: str,
    *,
    keyword: bool,
    default: bool | str | None = None,
) -> None:
    arguments = function.args.kwonlyargs if keyword else function.args.args
    expected = ast.arg(arg=name, annotation=ast.parse(annotation, mode="eval").body)
    if not arguments or _dump(arguments[-1]) != _dump(expected):
        raise ValueError(f"Unexpected added {name} argument")
    arguments.pop()
    if keyword:
        if not isinstance(function.args.kw_defaults[-1], ast.Constant) or (
            type(function.args.kw_defaults[-1].value) is not type(default)
            or function.args.kw_defaults[-1].value != default
        ):
            raise ValueError(f"Unexpected {name} default")
        function.args.kw_defaults.pop()


def _source_unchanged(old: ast.Module, current: ast.Module) -> None:
    original = _statement("result = prepared.rollup().rollup().execute()")
    replacement = _statement("result = prepared.rollup().execute()")

    class RepairRollup(ast.NodeTransformer):
        count = 0

        def visit_Assign(self, node: ast.Assign) -> ast.AST:
            if _dump(node) == _dump(original):
                self.count += 1
                return replacement
            return self.generic_visit(node)

    repair = RepairRollup()
    repair.visit(_function(old, "source", "Workload"))
    if repair.count != 1:
        raise ValueError("The original baseline must contain exactly the confirmed double rollup")
    author = _function(current, "_author")
    _argument(author, "monkeypatch", "pytest.MonkeyPatch", keyword=False)
    expected = _statement(
        'if "user" in arguments:\n'
        '    reader = arguments.pop("user")\n'
        '    if "user_env" not in arguments:\n'
        '        monkeypatch.setenv("MARIVO_R96_READER", str(reader))\n'
        '        arguments["user_env"] = "MARIVO_R96_READER"\n'
    )
    indices = [index for index, node in enumerate(author.body) if _dump(node) == _dump(expected)]
    if len(indices) != 1 or indices[0] == 0:
        raise ValueError("Authoring repair differs from the confirmed user-env conditional")
    previous = author.body[indices[0] - 1]
    if not (
        isinstance(previous, ast.Assign)
        and len(previous.targets) == 1
        and isinstance(previous.targets[0], ast.Name)
        and previous.targets[0].id == "arguments"
    ):
        raise ValueError("User-env repair must follow the arguments builder")
    author.body.pop(indices[0])
    calls = [
        node
        for node in ast.walk(_function(current, "workload"))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_author"
    ]
    if (
        len(calls) != 1
        or not calls[0].args
        or _dump(calls[0].args[-1]) != _dump(ast.Name(id="monkeypatch", ctx=ast.Load()))
    ):
        raise ValueError("Authoring caller differs from the confirmed monkeypatch handoff")
    calls[0].args.pop()
    if _dump(old) != _dump(current):
        raise ValueError("Workload/oracle changed beyond the two confirmed harness repairs")


def _collector_unchanged(old: ast.Module, current: ast.Module) -> None:
    run = _function(current, "run_group")
    _argument(run, "source_routes", "Sequence[str] | None", keyword=True)
    loops = [
        node
        for node in ast.walk(run)
        if isinstance(node, ast.For)
        and _dump(node.target) == _dump(ast.Name(id="route", ctx=ast.Store()))
        and _dump(node.iter) == _dump(ast.parse("work.routes", mode="eval").body)
    ]
    expected = _statement(
        "if source_routes is not None and route not in source_routes:\n    continue"
    )
    if len(loops) != 1 or not loops[0].body or _dump(loops[0].body[0]) != _dump(expected):
        raise ValueError("Collector route filter differs from the confirmed scheduling-only change")
    loops[0].body.pop(0)
    collect = _function(current, "collect")
    calls = [
        node
        for node in ast.walk(collect)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "run_group"
    ]
    expected_keyword = ast.keyword(
        arg="source_routes", value=ast.parse("args.source_routes", mode="eval").body
    )
    if (
        len(calls) != 1
        or not calls[0].keywords
        or _dump(calls[0].keywords[-1]) != _dump(expected_keyword)
    ):
        raise ValueError("Collector invocation differs from the confirmed route scheduling")
    calls[0].keywords.pop()
    schedule_fields = 0
    for node in ast.walk(collect):
        if isinstance(node, ast.Dict):
            for index in range(len(node.keys) - 1, -1, -1):
                key = node.keys[index]
                if isinstance(key, ast.Constant) and key.value == "scheduled_source_routes":
                    if _dump(node.values[index]) != _dump(expected_keyword.value):
                        raise ValueError("Unexpected scheduled route disclosure")
                    node.keys.pop(index)
                    node.values.pop(index)
                    schedule_fields += 1
    if schedule_fields != 1:
        raise ValueError("Expected one explicit scheduled route disclosure")
    main = _function(current, "main")
    parser = _statement(
        'parser.add_argument("--source-routes", nargs="+", choices=("ibis", "ibis_python"))'
    )
    indices = [index for index, node in enumerate(main.body) if _dump(node) == _dump(parser)]
    if len(indices) != 1:
        raise ValueError("CLI differs from the confirmed source route selection")
    main.body.pop(indices[0])
    if _dump(old) != _dump(current):
        raise ValueError("Collector measurement changed beyond the scheduling-only repair")


def eligible_sample(sample: Mapping[str, object]) -> bool:
    """Limit reuse to successful original ordinary baseline groups unaffected by repairs."""
    if (
        sample.get("status") != "passed"
        or sample.get("scenario") != "baseline"
        or sample.get("cost_scope") != "ordinary"
        or sample.get("profile") != "table"
        or sample.get("facts") not in (1000, 100000)
    ):
        return False
    route = sample.get("requested_route")
    recovery = sample.get("recovery")
    if sample.get("backend") == "duckdb":
        return (route in ("ibis", "ibis_python") and recovery is None) or (
            route == "artifact_python"
            and recovery in (None, "cold")
            and sample.get("fixed_mode") in ("kernel", "exact_hit")
        )
    return sample.get("backend") == "sqlite" and (
        (route == "ibis" and recovery is None)
        or (
            route == "artifact_python"
            and recovery == "cold"
            and sample.get("fixed_mode") in ("kernel", "exact_hit")
        )
    )


def eligible_pre_pressure_sample(sample: Mapping[str, object]) -> bool:
    """Retain unaffected SQLite baseline successes and the actual deadline failure."""
    if (
        sample.get("status") not in ("passed", "failed")
        or sample.get("scenario") != "baseline"
        or sample.get("cost_scope") != "ordinary"
        or sample.get("backend") != "sqlite"
        or sample.get("profile") != "table"
        or sample.get("facts") not in (1000, 100000)
        or sample.get("recovery") is not None
    ):
        return False
    return (
        sample.get("requested_route") == "ibis_python" and sample.get("fixed_mode") is None
    ) or (
        sample.get("requested_route") == "artifact_python"
        and sample.get("fixed_mode") in ("kernel", "exact_hit")
    )


def _harness_reuse(
    old_candidate: Mapping[str, object],
    new_candidate: Mapping[str, object],
    snapshot_directory: Path,
    current_snapshot: Path | None = None,
) -> dict[str, object]:
    """Bind the original candidate and prove only the confirmed scoped harness delta."""
    if old_candidate.get("head") != new_candidate.get("head") or old_candidate.get(
        "dependencies"
    ) != new_candidate.get("dependencies"):
        raise ValueError("Reuse requires unchanged HEAD and dependency versions")
    old_hashes, new_hashes = _hashes(old_candidate), _hashes(new_candidate)
    if not old_hashes.keys() >= PROTECTED or not new_hashes.keys() >= PROTECTED:
        raise ValueError("Missing observer/cold/fixture/driver bindings")
    if old_hashes.keys() - new_hashes.keys() or new_hashes.keys() - old_hashes.keys() - ADDED:
        raise ValueError("Unexpected candidate file additions or removals")
    changed = {path for path in old_hashes if old_hashes[path] != new_hashes[path]}
    if changed - ARCHIVES.keys() - TEST_ONLY:
        raise ValueError("Product, observer, fixture, driver or other code changed")
    snapshots: dict[str, str] = {}
    modules: dict[str, tuple[ast.Module, ast.Module]] = {}
    for path, archive in ARCHIVES.items():
        original = (snapshot_directory / archive).read_bytes()
        current = (
            (current_snapshot / archive).read_bytes()
            if current_snapshot is not None
            else (ROOT / path).read_bytes()
        )
        if _hash(original) != old_hashes.get(path) or _hash(current) != new_hashes.get(path):
            raise ValueError(f"Archived/current source hash mismatch: {path}")
        snapshots[archive] = _hash(original)
        modules[path] = (ast.parse(original), ast.parse(current))
    _source_unchanged(*modules["devtools/r96_cost_scenarios.py"])
    _collector_unchanged(*modules["devtools/analysis_r9_cost.py"])
    original_results, current_results = modules["scripts/r96_cost_results.py"]
    for name in ("valid", "repeated"):
        if _dump(_function(original_results, name)) != _dump(_function(current_results, name)):
            raise ValueError(f"Sample validation changed: {name}")
    return {
        "schema": "marivo.r96.cost-reuse-proof.v1",
        "original_candidate": dict(old_candidate),
        "candidate": dict(new_candidate),
        "snapshot_sha256": snapshots,
        "changed_files": sorted(changed),
        "scope": "Successful ordinary baseline DuckDB source/fixed/cold and SQLite native ibis/cold only",
        "boundary": "Original sample candidates remain unchanged; failed and other groups are historical",
    }


def _pressure_source_unchanged(old: ast.Module, current: ast.Module) -> None:
    original = _statement("amount = (1 if (index // 16) % 2 == 0 else -1) * (2**53 + index % 17)")
    replacement = _statement(
        "amount = (2**53 + 1024 + index % 17 if index < 16 "
        "else (1 if (index // 16) % 2 == 0 else -1) * (index % 17 + 1))"
    )

    class RepairExtremes(ast.NodeTransformer):
        count = 0

        def visit_Assign(self, node: ast.Assign) -> ast.AST:
            if _dump(node) == _dump(original):
                self.count += 1
                return replacement
            return self.generic_visit(node)

    repair = RepairExtremes()
    repair.visit(_function(old, "_rows"))
    if repair.count != 1 or _dump(old) != _dump(current):
        raise ValueError("Pressure fixture changed beyond the confirmed extreme amount assignment")


def _pressure_reuse(
    old_candidate: Mapping[str, object],
    new_candidate: Mapping[str, object],
    snapshot_directory: Path,
    current_snapshot: Path | None = None,
) -> dict[str, object]:
    if old_candidate.get("head") != new_candidate.get("head") or old_candidate.get(
        "dependencies"
    ) != new_candidate.get("dependencies"):
        raise ValueError("Pressure reuse requires unchanged HEAD and dependency versions")
    old_hashes, new_hashes = _hashes(old_candidate), _hashes(new_candidate)
    if not old_hashes.keys() >= PROTECTED or not new_hashes.keys() >= PROTECTED:
        raise ValueError("Missing observer/cold/fixture/driver bindings")
    if old_hashes.keys() != new_hashes.keys():
        raise ValueError("Pressure repair may not add or remove candidate files")
    changed = {path for path in old_hashes if old_hashes[path] != new_hashes[path]}
    if changed - PRESSURE_CHANGED:
        raise ValueError(
            "Product, observer, collector or unrelated code changed after pressure freeze"
        )
    snapshots: dict[str, str] = {}
    modules: dict[str, tuple[ast.Module, ast.Module]] = {}
    for path, archive in PRESSURE_ARCHIVES.items():
        original = (snapshot_directory / archive).read_bytes()
        current = (
            (current_snapshot / archive).read_bytes()
            if current_snapshot is not None
            else (ROOT / path).read_bytes()
        )
        if _hash(original) != old_hashes.get(path) or _hash(current) != new_hashes.get(path):
            raise ValueError(f"Archived/current pressure source hash mismatch: {path}")
        snapshots[archive] = _hash(original)
        modules[path] = (ast.parse(original), ast.parse(current))
    _pressure_source_unchanged(*modules["devtools/r96_cost_scenarios.py"])
    if _dump(modules["devtools/analysis_r9_cost.py"][0]) != _dump(
        modules["devtools/analysis_r9_cost.py"][1]
    ):
        raise ValueError("Collector changed after the pressure freeze")
    original_results, current_results = modules["scripts/r96_cost_results.py"]
    for name in ("valid", "repeated"):
        if _dump(_function(original_results, name)) != _dump(_function(current_results, name)):
            raise ValueError(f"Sample validation changed after pressure freeze: {name}")
    return {
        "original_candidate": dict(old_candidate),
        "candidate": dict(new_candidate),
        "snapshot_sha256": snapshots,
        "changed_files": sorted(changed),
        "scope": "SQLite ordinary baseline local source and warm fixed outcomes, including failures",
        "boundary": "Only the full-training extreme fixture changes; baseline outcomes retain authority",
    }


def validate_reuse(
    old_candidate: Mapping[str, object],
    new_candidate: Mapping[str, object],
    snapshot_directory: Path,
    *,
    pre_pressure_candidate: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Prove the exact two- or three-candidate baseline projection without relabeling."""
    if pre_pressure_candidate is None:
        return _harness_reuse(old_candidate, new_candidate, snapshot_directory)
    intermediate = snapshot_directory / "pre-pressure"
    harness = _harness_reuse(
        old_candidate, pre_pressure_candidate, snapshot_directory, intermediate
    )
    pressure = _pressure_reuse(pre_pressure_candidate, new_candidate, intermediate)
    return {
        "schema": "marivo.r96.cost-reuse-proof.v2",
        "original_candidate": dict(old_candidate),
        "pre_pressure_candidate": dict(pre_pressure_candidate),
        "candidate": dict(new_candidate),
        "repairs": {"harness": harness, "pressure_fixture": pressure},
        "boundary": "All three execution candidates remain unchanged; unaffected deadline failures remain effective",
    }


def _direct_source_unchanged(old: ast.Module, current: ast.Module) -> None:
    function = _function(current, "execute_source_graph")
    assignment = _statement(
        "direct_native = not local and all(\n"
        "    isinstance(stage, LoweredRelation) for stage in lowered.stages\n"
        ")"
    )
    indices = [
        index for index, node in enumerate(function.body) if _dump(node) == _dump(assignment)
    ]
    if len(indices) != 1:
        raise ValueError("Direct execution differs from the confirmed pure-native dispatch")
    function.body.pop(indices[0])
    expected = _statement(
        "if direct_native:\n"
        "    if stage.output == lowered.primary_output:\n"
        "        tables[stage.output] = _read(\n"
        "            source, lowered, stage.expression,\n"
        "            purpose='analysis.graph.stage', replacements={},\n"
        "            cell_reasons=stage.cell_reasons,\n"
        "        )\n"
        "else:\n"
        "    issued = _issue(\n"
        "        source, lowered, stage.expression,\n"
        "        purpose='analysis.graph.stage', replacements=replacements,\n"
        "    )\n"
        "    issued_reads[stage.output] = issued\n"
        "    staged, table = source.stage_derived(issued)\n"
        "    owned.append(staged)\n"
        "    tables[stage.output] = table\n"
        "    replacements[stage.expression.op()] = staged.op()\n"
    )
    assert isinstance(expected, ast.If)

    class RemoveDirect(ast.NodeTransformer):
        count = 0

        def visit_If(self, node: ast.If) -> ast.AST | list[ast.stmt]:
            if _dump(node) == _dump(expected):
                self.count += 1
                return node.orelse
            return self.generic_visit(node)

    repair = RemoveDirect()
    repair.visit(function)
    if repair.count != 1 or _dump(old) != _dump(current):
        raise ValueError("Product changed beyond the confirmed pure-native terminal-read branch")


def _direct_reuse(
    old_candidate: Mapping[str, object],
    new_candidate: Mapping[str, object],
    snapshot_directory: Path,
    current_snapshot: Path,
) -> dict[str, object]:
    if old_candidate.get("head") != new_candidate.get("head") or old_candidate.get(
        "dependencies"
    ) != new_candidate.get("dependencies"):
        raise ValueError("Direct reuse requires unchanged HEAD and dependency versions")
    old_hashes, new_hashes = _hashes(old_candidate), _hashes(new_candidate)
    if (
        old_hashes.keys() - new_hashes.keys()
        or new_hashes.keys() - old_hashes.keys() != DIRECT_ADDED
    ):
        raise ValueError("Direct repair differs from the confirmed regression-file addition")
    changed = {path for path in old_hashes if old_hashes[path] != new_hashes[path]}
    if DIRECT_SOURCE not in changed or changed - {DIRECT_SOURCE} - DIRECT_TEST_ONLY:
        raise ValueError("Unrelated product, observer, fixture, oracle or measurement changed")
    original = (snapshot_directory / DIRECT_ARCHIVE).read_bytes()
    current = (current_snapshot / DIRECT_ARCHIVE).read_bytes()
    if _hash(original) != old_hashes[DIRECT_SOURCE] or _hash(current) != new_hashes[DIRECT_SOURCE]:
        raise ValueError("Archived direct-execution source hash mismatch")
    _direct_source_unchanged(ast.parse(original), ast.parse(current))
    return {
        "original_candidate": dict(old_candidate),
        "candidate": dict(new_candidate),
        "snapshot_sha256": {
            "original": _hash(original),
            "direct_native": _hash(current),
        },
        "changed_files": sorted(changed),
        "scope": "Only ordinary baseline local preparation and fixed/cold costs; no old native timing",
        "boundary": "Preparation returns before the new dispatch; fixed execution does not call source execution",
    }


def eligible_pre_direct_sample(sample: Mapping[str, object]) -> bool:
    """Exclude old native timing and bind local reuse to actual prepared method shapes."""
    backend = sample.get("backend")
    if (
        sample.get("status") not in ("passed", "failed")
        or sample.get("scenario") != "baseline"
        or sample.get("cost_scope") != "ordinary"
        or sample.get("facts") not in (1000, 100000)
        or backend not in ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")
        or sample.get("profile")
        != {"trino": "iceberg", "clickhouse": "mergetree"}.get(str(backend), "table")
    ):
        return False
    if sample.get("status") == "failed":
        return (
            sample.get("requested_route") == "ibis_python"
            and backend == "sqlite"
            and sample.get("facts") == 100000
            and sample.get("error_type") == "DomainPreparationError"
            and "execute deadline exceeded" in str(sample.get("traceback"))
        )
    identity = _obj(sample.get("identity"))
    if identity.get("root_route") != sample.get("requested_route"):
        return False
    if sample.get("requested_route") == "artifact_python":
        observations = _obj(sample.get("observations"))
        return (
            sample.get("fixed_mode") in ("kernel", "exact_hit")
            and sample.get("recovery") in (None, "cold")
            and bool(sample.get("producer_identity"))
            and observations.get("source_submissions") == []
        )
    if sample.get("requested_route") != "ibis_python" or sample.get("recovery") is not None:
        return False
    methods = identity.get("method_bindings")
    if not isinstance(methods, list):
        return False
    local: set[str] = set()
    for value in methods:
        binding = _obj(value)
        key = _obj(binding.get("key"))
        shape = _obj(key.get("shape"))
        expected_time = (
            {"kind": "NoTime"}
            if key.get("method") in ("deviation.zscore@v1", "deviation.read@v1")
            else {"kind": "instant", "timezone": "UTC", "unit": "us"}
        )
        if (
            shape.get("kind") != "SourceShape"
            or shape.get("backend") != backend
            or shape.get("form") != "table"
            or shape.get("table_kind") != "native"
            or shape.get("time") != expected_time
        ):
            return False
        if binding.get("route") == "ibis_python" and key.get("route") == "ibis_python":
            local.add(str(key.get("method")))
    required = (
        {"deviation.zscore@v1", "deviation.read@v1", "state_rollup.sum_zero@v1"}
        if backend == "duckdb"
        else {"metric.sum_zero@v1", "state_rollup.sum_zero@v1"}
    )
    return required <= local and identity.get("actual_routes") == ["ibis", "ibis_python"]


def eligible_direct_sample(sample: Mapping[str, object]) -> bool:
    """Keep only the measured post-D ordinary PostgreSQL/MySQL baseline outcomes."""
    if (
        sample.get("status") not in ("passed", "failed")
        or sample.get("scenario") != "baseline"
        or sample.get("cost_scope") != "ordinary"
        or sample.get("backend") not in ("postgres", "mysql")
        or sample.get("profile") != "table"
        or sample.get("facts") not in (1000, 100000)
    ):
        return False
    route = sample.get("requested_route")
    if sample.get("status") == "failed":
        return route in ("ibis", None)
    identity = _obj(sample.get("identity"))
    return identity.get("root_route") == route and (
        (route == "ibis" and sample.get("recovery") is None)
        or (
            route == "artifact_python"
            and sample.get("recovery") == "cold"
            and sample.get("fixed_mode") in ("kernel", "exact_hit")
            and _obj(sample.get("observations")).get("source_submissions") == []
            and bool(sample.get("producer_identity"))
        )
    )


def _keyed_source_unchanged(old: ast.Module, current: ast.Module) -> list[str]:
    old_names = {node.name for node in old.body if isinstance(node, ast.FunctionDef)}
    added = [
        node
        for node in current.body
        if isinstance(node, ast.FunctionDef) and node.name not in old_names
    ]
    if {node.name for node in added} != KEYED_HELPERS or len(added) != len(KEYED_HELPERS):
        raise ValueError("Keyed oracle differs from the confirmed private helper inventory")
    for node in added:
        current.body.remove(node)
    guard = _statement(
        "if TYPE_CHECKING:\n" + "\n".join("    " + code for code in KEYED_IMPORT_CODES)
    )
    guards = [node for node in current.body if _dump(node) == _dump(guard)]
    if len(guards) != 1:
        raise ValueError("Keyed oracle requires the exact type-only helper import guard")
    current.body.remove(guards[0])
    typing = [
        node
        for node in current.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "typing"
        and any(alias.name == "TYPE_CHECKING" and alias.asname is None for alias in node.names)
    ]
    if len(typing) != 1:
        raise ValueError("Keyed oracle requires exactly one unaliased TYPE_CHECKING import")
    names = typing[0].names
    indices = [
        index
        for index, alias in enumerate(names)
        if alias.name == "TYPE_CHECKING" and alias.asname is None
    ]
    if len(indices) != 1:
        raise ValueError("Keyed oracle requires exactly one TYPE_CHECKING alias")
    names.pop(indices[0])
    validate = _function(current, "validate", "Workload")
    call = _statement("key_binding_digest = _keyed_oracle(self, result)")
    indices = [index for index, node in enumerate(validate.body) if _dump(node) == _dump(call)]
    if len(indices) != 1:
        raise ValueError("Expected the one non-baseline keyed-oracle invocation")
    index = indices[0]
    previous = validate.body[index - 1] if index else None
    baseline = _statement("if self.scenario == 'baseline':\n    pass")
    assert isinstance(baseline, ast.If)
    if not isinstance(previous, ast.If) or _dump(previous.test) != _dump(baseline.test):
        raise ValueError("Keyed oracle must follow the unchanged baseline early return")
    validate.body.pop(index)
    terminal = validate.body[-1]
    if not isinstance(terminal, ast.Return) or not isinstance(terminal.value, ast.Dict):
        raise ValueError("Expected the existing non-baseline oracle dictionary")
    dictionary = terminal.value
    expected = {
        "original_key_binding": ast.Constant(value=True),
        "original_key_digest": ast.Name(id="key_binding_digest", ctx=ast.Load()),
    }
    removed: set[str] = set()
    for position in range(len(dictionary.keys) - 1, -1, -1):
        key = dictionary.keys[position]
        if isinstance(key, ast.Constant) and key.value in expected:
            name = str(key.value)
            if name in removed or _dump(dictionary.values[position]) != _dump(expected[name]):
                raise ValueError("Unexpected keyed-oracle disclosure value")
            removed.add(name)
            dictionary.keys.pop(position)
            dictionary.values.pop(position)
    if removed != set(expected) or _dump(old) != _dump(current):
        raise ValueError("Recipes, baseline oracle or existing helpers changed with keyed repair")
    return sorted(KEYED_HELPERS)


def _keyed_results_unchanged(old: ast.Module, current: ast.Module) -> None:
    validate = _function(current, "valid")
    required = _statement(
        "if sample.get('scenario') not in (None, 'baseline') and (\n"
        "    oracle.get('original_key_binding') is not True or not oracle.get('original_key_digest')\n"
        "):\n"
        "    raise ValueError('A non-baseline sample requires independent original-key binding and digest')"
    )
    indices = [index for index, node in enumerate(validate.body) if _dump(node) == _dump(required)]
    if len(indices) != 1:
        raise ValueError("Result validation differs from the confirmed keyed-oracle requirement")
    validate.body.pop(indices[0])
    for name in ("valid", "repeated"):
        if _dump(_function(old, name)) != _dump(_function(current, name)):
            raise ValueError(f"Baseline sample validation changed with keyed repair: {name}")


def _followup_collector_unchanged(old: ast.Module, current: ast.Module) -> None:
    if _dump(old) == _dump(current):
        return
    imported = _statement(
        "from devtools.r96_cost_fixture_layout import FixtureLayout, fixture_layout"
    )
    indices = [index for index, node in enumerate(current.body) if _dump(node) == _dump(imported)]
    if len(indices) != 1:
        raise ValueError("Expected only the explicit fixture-layout import")
    current.body.pop(indices[0])
    run = _function(current, "run_group")
    _argument(run, "deployment", "Mapping[str, object] | None", keyword=True)
    _argument(run, "layout", "FixtureLayout", keyword=True, default="unindexed")
    _argument(run, "skip_baseline_cold", "bool", keyword=True, default=False)
    expected_items = ast.parse("fixture_layout(layout)", mode="eval").body
    wrappers = [
        node
        for node in ast.walk(run)
        if isinstance(node, ast.With)
        and len(node.items) == 2
        and _dump(node.items[0].context_expr) == _dump(expected_items)
        and node.items[0].optional_vars is None
    ]
    if len(wrappers) != 1:
        raise ValueError("Expected one explicit unindexed-default fixture context")
    wrappers[0].items.pop(0)
    removed = {
        _dump(
            _statement(
                "if deployment is not None:\n"
                "    work.environment['deployment_evidence'] = dict(deployment)\n"
                "    work.environment['deployment_cohort'] = deployment['deployment_cohort']"
            )
        ): 0,
        _dump(
            _statement(
                "if layout != 'unindexed' or deployment is not None:\n"
                "    record['environment'] = work.environment"
            )
        ): 0,
        _dump(_statement("if skip_baseline_cold:\n    continue")): 0,
    }

    class RemoveExplicitBranches(ast.NodeTransformer):
        def visit_If(self, node: ast.If) -> ast.AST | list[ast.stmt]:
            key = _dump(node)
            if key in removed:
                removed[key] += 1
                return []
            return self.generic_visit(node)

    RemoveExplicitBranches().visit(run)
    if set(removed.values()) != {1}:
        raise ValueError(
            "Explicit scheduling/environment branches differ from the confirmed change"
        )
    collect = _function(current, "collect")
    deployment = (
        "deployment = None",
        "if args.deployment_evidence is not None:\n"
        "    data = args.deployment_evidence.read_bytes()\n"
        "    configuration = dict(_mapping(json.loads(data)))\n"
        "    cohort = configuration.get('deployment_cohort')\n"
        "    if not isinstance(cohort, str) or not cohort:\n"
        "        raise ValueError('Deployment evidence requires a nonempty deployment_cohort')\n"
        "    deployment = {\n"
        "        'path': str(args.deployment_evidence.resolve()),\n"
        "        'sha256': digest(data), 'configuration': configuration,\n"
        "        'deployment_cohort': cohort, 'usage_observation': False,\n"
        "    }",
    )
    for code in deployment:
        expected = _statement(code)
        indices = [
            index for index, node in enumerate(collect.body) if _dump(node) == _dump(expected)
        ]
        if len(indices) != 1:
            raise ValueError("Deployment disclosure differs from the confirmed config-only branch")
        collect.body.pop(indices[0])
    calls = [
        node
        for node in ast.walk(collect)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "run_group"
    ]
    if len(calls) != 1:
        raise ValueError("Expected one unchanged measurement invocation")
    for name, value in (
        ("deployment", "deployment"),
        ("layout", "args.fixture_layout"),
        ("skip_baseline_cold", "args.skip_baseline_cold"),
    ):
        expected_keyword = ast.keyword(arg=name, value=ast.parse(value, mode="eval").body)
        if not calls[0].keywords or _dump(calls[0].keywords[-1]) != _dump(expected_keyword):
            raise ValueError("Measurement invocation differs from the explicit option handoff")
        calls[0].keywords.pop()
    fields = {
        "scheduled_baseline_cold": ast.parse("not args.skip_baseline_cold", mode="eval").body,
        "fixture_layout": ast.parse("args.fixture_layout", mode="eval").body,
        "deployment_evidence": ast.Name(id="deployment", ctx=ast.Load()),
    }
    counts = dict.fromkeys(fields, 0)
    for node in ast.walk(collect):
        if isinstance(node, ast.Dict):
            for index in range(len(node.keys) - 1, -1, -1):
                key = node.keys[index]
                if isinstance(key, ast.Constant) and key.value in fields:
                    name = str(key.value)
                    if _dump(node.values[index]) != _dump(fields[name]):
                        raise ValueError("Unexpected followup manifest value")
                    counts[name] += 1
                    node.keys.pop(index)
                    node.values.pop(index)
    if set(counts.values()) != {1}:
        raise ValueError("Expected exactly the three explicit followup manifest fields")
    main = _function(current, "main")
    for code in (
        "parser.add_argument('--skip-baseline-cold', action='store_true')",
        "parser.add_argument('--fixture-layout', choices=('unindexed', 'indexed-keys'), default='unindexed')",
        "parser.add_argument('--deployment-evidence', type=Path)",
    ):
        expected = _statement(code)
        indices = [index for index, node in enumerate(main.body) if _dump(node) == _dump(expected)]
        if len(indices) != 1:
            raise ValueError("CLI differs from the three closed explicit followup options")
        main.body.pop(indices[0])
    if _dump(old) != _dump(current):
        raise ValueError(
            "Measurement or default source/cold schedule changed beyond explicit options"
        )


def _keyed_reuse(
    old_candidate: Mapping[str, object],
    new_candidate: Mapping[str, object],
    snapshot_directory: Path,
    current_snapshot: Path | None = None,
) -> dict[str, object]:
    if old_candidate.get("head") != new_candidate.get("head") or old_candidate.get(
        "dependencies"
    ) != new_candidate.get("dependencies"):
        raise ValueError("Keyed reuse requires unchanged HEAD and dependency versions")
    old_hashes, new_hashes = _hashes(old_candidate), _hashes(new_candidate)
    added = new_hashes.keys() - old_hashes.keys()
    if old_hashes.keys() - new_hashes.keys() or added not in (set(), KEYED_ADDED):
        raise ValueError("Keyed repair differs from the exact optional layout/scheduling additions")
    changed = {path for path in old_hashes if old_hashes[path] != new_hashes[path]}
    if changed - KEYED_CHANGED:
        raise ValueError("Product, collector, observer, cold recovery or fixtures changed")
    snapshots: dict[str, str] = {}
    modules: dict[str, tuple[ast.Module, ast.Module]] = {}
    for path, archive in PRESSURE_ARCHIVES.items():
        original = (snapshot_directory / archive).read_bytes()
        current = (
            (current_snapshot / archive).read_bytes()
            if current_snapshot is not None
            else (ROOT / path).read_bytes()
        )
        if _hash(original) != old_hashes.get(path) or _hash(current) != new_hashes.get(path):
            raise ValueError(f"Archived/current keyed source hash mismatch: {path}")
        snapshots[archive] = _hash(original)
        modules[path] = (ast.parse(original), ast.parse(current))
    helpers = _keyed_source_unchanged(*modules["devtools/r96_cost_scenarios.py"])
    _followup_collector_unchanged(*modules["devtools/analysis_r9_cost.py"])
    if added:
        layout = (
            (current_snapshot / "explicit_cost_fixture_layout.py").read_bytes()
            if current_snapshot is not None
            else (ROOT / "devtools/r96_cost_fixture_layout.py").read_bytes()
        )
        if (
            _hash(layout) != LAYOUT_SHA256
            or new_hashes["devtools/r96_cost_fixture_layout.py"] != LAYOUT_SHA256
        ):
            raise ValueError(
                "Fixture layout differs from the exact nonunique indexed deployment wrapper"
            )
        snapshots["explicit_cost_fixture_layout.py"] = _hash(layout)
    _keyed_results_unchanged(*modules["scripts/r96_cost_results.py"])
    return {
        "original_candidate": dict(old_candidate),
        "candidate": dict(new_candidate),
        "snapshot_sha256": snapshots,
        "changed_files": sorted(changed),
        "added_files": sorted(added),
        "oracle_helpers": helpers,
        "scope": "Post-D ordinary PostgreSQL/MySQL baseline native/cold outcomes only",
        "boundary": "Scalar baseline returns precede keyed checks; default source/cold schedules and unindexed fixtures remain unchanged; alternatives are independent cohorts",
        "rss_authority": RSS_AUTHORITY,
    }


def _optimization_source_unchanged(path: str, old: ast.Module, current: ast.Module) -> list[str]:
    changed: tuple[tuple[str | None, str], ...]
    if path.endswith("graph_preparation.py"):
        changed = ((None, "_observation"),)
        added: tuple[tuple[str | None, str], ...] = ()
    elif path.endswith("graph_exchange.py"):
        changed = (
            ("CheckedStream", "__init__"),
            ("CheckedStream", "_batches"),
            ("CheckedStream", "_validate_cells"),
            (None, "collect"),
            (None, "_table_keys"),
        )
        added = ((None, "_key_rows"), ("CheckedStream", "_complete_key_index"))
    else:
        raise ValueError("Only the two concrete optimized modules may differ")

    def remove(module: ast.Module, owner: str | None, name: str) -> None:
        function = _function(module, name, owner)
        if owner is None:
            module.body.remove(function)
        else:
            classes = [
                node
                for node in module.body
                if isinstance(node, ast.ClassDef) and node.name == owner
            ]
            if len(classes) != 1:
                raise ValueError("Optimization requires one exact class owner")
            classes[0].body.remove(function)

    for owner, name in changed:
        remove(old, owner, name)
        remove(current, owner, name)
    for owner, name in added:
        remove(current, owner, name)
    if _dump(old) != _dump(current):
        raise ValueError("Optimization changed code beyond the exact reviewed function inventory")
    return sorted(
        f"{owner}.{name}" if owner is not None else name for owner, name in (*changed, *added)
    )


def _optimization_reuse(
    old_candidate: Mapping[str, object],
    new_candidate: Mapping[str, object],
    snapshot_directory: Path,
    current_snapshot: Path | None = None,
) -> dict[str, object]:
    if old_candidate.get("head") != new_candidate.get("head") or old_candidate.get(
        "dependencies"
    ) != new_candidate.get("dependencies"):
        raise ValueError("Optimization reuse requires unchanged HEAD and dependency versions")
    old_hashes, new_hashes = _hashes(old_candidate), _hashes(new_candidate)
    changed = {
        path for path in old_hashes if path in new_hashes and old_hashes[path] != new_hashes[path]
    }
    added = new_hashes.keys() - old_hashes.keys()
    if (
        old_hashes.keys() - new_hashes.keys()
        or changed != OPTIMIZATION_ARCHIVES.keys()
        or added != OPTIMIZATION_TESTS.keys()
    ):
        raise ValueError(
            "Optimization differs from the two reviewed products and mechanism test files"
        )
    snapshots: dict[str, str] = {}
    functions: dict[str, list[str]] = {}
    for path, archive in OPTIMIZATION_ARCHIVES.items():
        original = (snapshot_directory / archive).read_bytes()
        current = (
            (current_snapshot / archive).read_bytes()
            if current_snapshot is not None
            else (ROOT / path).read_bytes()
        )
        if (
            (_hash(original), _hash(current)) != OPTIMIZATION_HASHES[path]
            or old_hashes.get(path) != _hash(original)
            or new_hashes.get(path) != _hash(current)
        ):
            raise ValueError(
                f"Optimization differs from its exact reviewed before/after source: {path}"
            )
        snapshots[archive] = _hash(original)
        functions[path] = _optimization_source_unchanged(
            path, ast.parse(original), ast.parse(current)
        )
    for path, expected in OPTIMIZATION_TESTS.items():
        if new_hashes.get(path) != expected or _hash((ROOT / path).read_bytes()) != expected:
            raise ValueError(f"Optimization mechanism regression source differs: {path}")
    return {
        "original_candidate": dict(old_candidate),
        "candidate": dict(new_candidate),
        "snapshot_sha256": snapshots,
        "changed_files": sorted(changed),
        "added_files": sorted(added),
        "reviewed_functions": functions,
        "mechanism_regression_sources": dict(OPTIMIZATION_TESTS),
        "scope": "Ordinary baseline successes and original failures only; no weak scenario oracle reuse",
        "preserved": "Source SQL/check order, recipes/oracles, method arithmetic/state verification, deadline and close/publication owners remain unchanged",
        "performance_authority": PERFORMANCE_AUTHORITY,
        "rss_authority": RSS_AUTHORITY,
    }


def eligible_optimized_sample(sample: Mapping[str, object]) -> bool:
    """Retain only actual C5/C6 ordinary baseline observations under explicit proofs."""
    backend = sample.get("backend")
    if (
        sample.get("status") not in ("passed", "failed")
        or sample.get("scenario") != "baseline"
        or sample.get("cost_scope") != "ordinary"
        or backend not in ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")
        or sample.get("profile")
        != {"trino": "iceberg", "clickhouse": "mergetree"}.get(str(backend), "table")
        or sample.get("facts") not in (1000, 100000)
    ):
        return False
    route = sample.get("requested_route")
    if sample.get("status") == "failed":
        return route in ("ibis", "ibis_python", "artifact_python", None)
    if route not in ("ibis", "ibis_python", "artifact_python"):
        return False
    identity = _obj(sample.get("identity"))
    if identity.get("root_route") != route:
        return False
    if route != "artifact_python":
        return sample.get("recovery") is None and sample.get("fixed_mode") is None
    return (
        sample.get("recovery") in (None, "cold")
        and sample.get("fixed_mode") in ("kernel", "exact_hit")
        and _obj(sample.get("observations")).get("source_submissions") == []
        and bool(sample.get("producer_identity"))
    )


def _file_builtin_unchanged(old: ast.Module, current: ast.Module) -> None:
    original = _function(old, "implementations")
    updated = _function(current, "implementations")
    deviation = [
        node
        for node in original.body
        if isinstance(node, ast.If)
        and _dump(node.test)
        == _dump(ast.parse('method.name.startswith("deviation.")', mode="eval").body)
    ]
    if len(deviation) != 1:
        raise ValueError("File reuse requires the original deviation branch")
    for branch, digest in FILE_BRANCHES.items():
        nodes = [node for node in updated.body if _hash(_dump(node).encode()) == digest]
        if len(nodes) != 1:
            raise ValueError(f"File registration differs from its exact reviewed {branch} branch")
        if branch == "deviation":
            updated.body[updated.body.index(nodes[0])] = deviation[0]
        else:
            updated.body.remove(nodes[0])
    specialized = _function(current, "specialize_numeric")
    prefixes = [
        node
        for node in ast.walk(specialized)
        if isinstance(node, ast.Tuple)
        and node.elts
        and isinstance(node.elts[-1], ast.Constant)
        and node.elts[-1].value == "r96.local_file."
    ]
    if len(prefixes) != 1:
        raise ValueError("File specialization must deny widening of the exact new IDs")
    prefixes[0].elts.pop()
    if _dump(old) != _dump(current):
        raise ValueError("File registration changed an old declaration, arithmetic or module owner")


def _sqlite_disclosure_unchanged(path: str, old: ast.Module, current: ast.Module) -> None:
    if path.endswith("authoring.py"):
        for module in (old, current):
            function = _function(module, "sqlite")
            if (
                not function.body
                or not isinstance(function.body[0], ast.Expr)
                or not isinstance(function.body[0].value, ast.Constant)
                or not isinstance(function.body[0].value.value, str)
            ):
                raise ValueError("SQLite authoring must retain its owning docstring")
            function.body.pop(0)
    elif path.endswith("registry.py"):
        before = "Build a SQLite table/view datasource; median, percentile, and string strptime are unsupported."
        after = "Build a SQLite table/view datasource; native NUMERIC cannot supply exact Decimal. Median, percentile, and string strptime are unsupported."
        literals = [
            node
            for node in ast.walk(_function(current, "_build_registry"))
            if isinstance(node, ast.Constant) and node.value == after
        ]
        if len(literals) != 1:
            raise ValueError("SQLite summary differs from its exact refusal disclosure")
        literals[0].value = before
    else:
        raise ValueError("Only the SQLite authoring docstring and capability summary may differ")
    if _dump(old) != _dump(current):
        raise ValueError("SQLite disclosure changed execution, old Help tests or another surface")


def _sqlite_fixture_unchanged(old: ast.Module, current: ast.Module) -> None:
    classes = [
        node
        for node in current.body
        if isinstance(node, ast.ClassDef) and node.name == "SourceData"
    ]
    field = _statement("sqlite_type_map: dict[str, str] | None = None")
    if len(classes) != 1 or not classes[0].body or _dump(classes[0].body[-1]) != _dump(field):
        raise ValueError("SourceData must add only the optional default-None SQLite test field")
    classes[0].body.pop()
    function = _function(current, "source_case")
    local = [
        node
        for node in function.body
        if isinstance(node, ast.If)
        and _dump(node.test)
        == _dump(ast.parse('backend in {"duckdb", "sqlite"}', mode="eval").body)
    ]
    if len(local) != 1:
        raise ValueError("SQLite test mapping requires the existing local-source branch")
    additions = (
        _statement('fields: dict[str, object] = {"path": str(path), "read_only": True}'),
        _statement(
            'if backend == "sqlite" and data is not None and data.sqlite_type_map is not None:\n    fields["type_map"] = data.sqlite_type_map'
        ),
        _statement("ds = datasource(backend, fields)"),
    )
    starts = [
        i
        for i in range(len(local[0].body) - 2)
        if all(
            _dump(a) == _dump(b) for a, b in zip(local[0].body[i : i + 3], additions, strict=True)
        )
    ]
    if len(starts) != 1:
        raise ValueError("SQLite test type_map changed its exact explicit-only dispatch")
    local[0].body[starts[0] : starts[0] + 3] = [
        _statement('ds = datasource(backend, {"path": str(path), "read_only": True})')
    ]
    if _dump(old) != _dump(current):
        raise ValueError(
            "SQLite test type_map changed original source facts or default provider paths"
        )


def _sqlite_risk_unchanged(old: ast.Module, current: ast.Module) -> None:
    for module in (old, current):
        module.body.remove(_function(module, "test_source_type_risk"))
    for name in (
        "_sqlite_decimal_refusal",
        "test_sqlite_numeric_type_map_cannot_restore_exact_decimal",
    ):
        current.body.remove(_function(current, name))
    imports = [
        node
        for node in current.body
        if isinstance(node, ast.ImportFrom) and node.module == "dataclasses"
    ]
    if (
        len(imports) != 1
        or not imports[0].names
        or _dump(imports[0].names[-1]) != _dump(ast.alias(name="replace"))
    ):
        raise ValueError("SQLite refusal tests require only the additional replace import")
    imports[0].names.pop()
    if _dump(old) != _dump(current):
        raise ValueError(
            "SQLite Decimal amendment changed risk data, original facts or another risk owner"
        )


def _qualification_unchanged(path: str, old: ast.Module, current: ast.Module) -> None:
    if path.startswith("scripts/"):
        expectation = ast.parse(
            '"rejection" if backend == "sqlite" and risk == "decimal-precision-scale" else "success"',
            mode="eval",
        ).body
        calls = [
            node
            for node in ast.walk(_function(current, "build"))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "add"
            and node.args
            and _dump(node.args[-1]) == _dump(expectation)
        ]
        if (
            len(calls) != 1
            or not isinstance(calls[0].args[0], ast.Constant)
            or calls[0].args[0].value != "source-types"
        ):
            raise ValueError(
                "Only the original SQLite Decimal source-type expectation may be amended"
            )
        calls[0].args.pop()
    else:
        current.body.remove(
            _function(current, "test_sqlite_decimal_amendment_changes_only_its_expectation")
        )
        function = _function(current, "test_sql_and_risk_obligations_are_retained")
        updated = _statement(
            'assert len([row for row in rows if row["expectation"] == "rejection"]) == 6'
        )
        assertions = [node for node in function.body if _dump(node) == _dump(updated)]
        if len(assertions) != 1:
            raise ValueError("Only the exact one-extra-refusal count may change")
        function.body[function.body.index(assertions[0])] = _statement(
            'assert len([row for row in rows if row["expectation"] == "rejection"]) == 5'
        )
    if _dump(old) != _dump(current):
        raise ValueError(
            "SQLite qualification amendment changed IDs, other expectations or R9.6 cost rows"
        )


def _binder_unchanged(old: ast.Module, current: ast.Module, *, results: bool) -> list[str]:
    if results:
        changed = {"audit", "main"}
        added = {
            "_group_identity",
            "_accepted_samples",
            "_attachment",
            "_boundary_runs",
            "_cold_resources",
            "_physical_key_available",
        }
        constants = {
            "GROUP_FIELDS",
            "COHORT_FIELDS",
            "HTTP_PROFILES",
            "HISTORICAL_OWNERS",
            "BOUNDARY_CASES",
        }
        imports = {
            "import ast",
            "import subprocess",
            "from typing import Literal",
            "from xml.etree import ElementTree",
        }
    else:
        changed = {"_keyed_reuse", "validate_current_reuse"}
        added = {
            "_optimization_source_unchanged",
            "_optimization_reuse",
            "eligible_optimized_sample",
            "_file_builtin_unchanged",
            "_sqlite_disclosure_unchanged",
            "_sqlite_fixture_unchanged",
            "_sqlite_risk_unchanged",
            "_qualification_unchanged",
            "_binder_unchanged",
            "_file_reuse",
        }
        constants = {
            "OPTIMIZATION_ARCHIVES",
            "OPTIMIZATION_HASHES",
            "OPTIMIZATION_TESTS",
            "PERFORMANCE_AUTHORITY",
            "FILE_BUILTIN",
            "FILE_BUILTIN_HASHES",
            "FILE_BRANCHES",
            "FILE_TESTS",
            "FILE_FIXED_SOURCES",
            "FILE_CHANGED",
            "FILE_ADDED",
        }
        imports = set()
    for name in changed:
        old.body.remove(_function(old, name))
        current.body.remove(_function(current, name))
    for name in added:
        current.body.remove(_function(current, name))
    remaining = set(constants)
    for node in tuple(current.body):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in constants
        ):
            remaining.remove(node.targets[0].id)
            current.body.remove(node)
        elif isinstance(node, (ast.Import, ast.ImportFrom)) and ast.unparse(node) in imports:
            current.body.remove(node)
    if remaining or _dump(old) != _dump(current):
        raise ValueError("Binder changed a preserved proof, sample validation or cohort owner")
    return sorted(changed | added)


def _file_reuse(
    old_candidate: Mapping[str, object],
    new_candidate: Mapping[str, object],
    snapshot_directory: Path,
    current_snapshot: Path | None = None,
) -> dict[str, object]:
    if old_candidate.get("head") != new_candidate.get("head") or old_candidate.get(
        "dependencies"
    ) != new_candidate.get("dependencies"):
        raise ValueError("File/binder reuse requires unchanged HEAD and dependency versions")
    old_hashes, new_hashes = _hashes(old_candidate), _hashes(new_candidate)
    changed = {
        path for path in old_hashes if path in new_hashes and old_hashes[path] != new_hashes[path]
    }
    added = new_hashes.keys() - old_hashes.keys()
    if old_hashes.keys() - new_hashes.keys() or changed != FILE_CHANGED or added != FILE_ADDED:
        raise ValueError(
            "File/binder reuse differs from its concrete product, binder and owning tests"
        )
    for path in changed | added:
        if _hash(_file_source(path, current_snapshot)) != new_hashes[path]:
            raise ValueError(f"File/binder current candidate source differs: {path}")
    snapshots: dict[str, str] = {}
    reviewed: dict[str, list[str]] = {}
    for path, archive in (
        (FILE_BUILTIN, "original_builtin.py"),
        ("scripts/r96_cost_results.py", "original_cost_results.py"),
        ("scripts/r96_cost_reuse.py", "original_cost_reuse.py"),
        ("tests/test_r92_source_profiles.py", "original_r92_source_profiles.py"),
        ("tests/r9_source_cases.py", "original_r9_source_cases.py"),
        ("marivo/datasource/authoring.py", "original_datasource_authoring.py"),
        ("marivo/datasource/_capabilities/registry.py", "original_datasource_registry.py"),
        ("scripts/r9_qualification_requirements.py", "original_qualification_requirements.py"),
        ("tests/test_full_algebra_backend_matrix.py", "original_full_algebra_backend_matrix.py"),
    ):
        original, current = (
            (snapshot_directory / archive).read_bytes(),
            _file_source(path, current_snapshot),
        )
        if _hash(original) != old_hashes.get(path):
            raise ValueError(f"File/binder archive does not bind its original source: {path}")
        snapshots[archive] = _hash(original)
        old_module, current_module = ast.parse(original), ast.parse(current)
        if path == FILE_BUILTIN:
            if (_hash(original), _hash(current)) != FILE_BUILTIN_HASHES:
                raise ValueError(
                    "File registrations differ from the exact reviewed before/after source"
                )
            _file_builtin_unchanged(old_module, current_module)
            reviewed[path] = ["implementations", "specialize_numeric"]
        elif path.endswith("test_r92_source_profiles.py"):
            _sqlite_risk_unchanged(old_module, current_module)
            reviewed[path] = [
                "test_source_type_risk",
                "_sqlite_decimal_refusal",
                "test_sqlite_numeric_type_map_cannot_restore_exact_decimal",
            ]
        elif path.endswith("r9_source_cases.py"):
            _sqlite_fixture_unchanged(old_module, current_module)
            reviewed[path] = [
                "SourceData.sqlite_type_map.default_none",
                "source_case.sqlite.explicit_type_map",
            ]
        elif path.startswith("marivo/datasource/"):
            _sqlite_disclosure_unchanged(path, old_module, current_module)
            reviewed[path] = [
                "sqlite.docstring"
                if path.endswith("authoring.py")
                else "_build_registry.sqlite.summary"
            ]
        elif path.endswith("r9_qualification_requirements.py") or path.endswith(
            "test_full_algebra_backend_matrix.py"
        ):
            _qualification_unchanged(path, old_module, current_module)
            reviewed[path] = (
                ["build.source_types.sqlite_decimal.expectation"]
                if path.startswith("scripts/")
                else [
                    "test_sql_and_risk_obligations_are_retained",
                    "test_sqlite_decimal_amendment_changes_only_its_expectation",
                ]
            )
        else:
            reviewed[path] = _binder_unchanged(
                old_module, current_module, results=path.endswith("results.py")
            )
    for path, expected in {**FILE_TESTS, **FILE_FIXED_SOURCES}.items():
        if new_hashes.get(path) != expected:
            raise ValueError(f"File qualification regression source differs: {path}")
    return {
        "original_candidate": dict(old_candidate),
        "candidate": dict(new_candidate),
        "snapshot_sha256": snapshots,
        "changed_files": sorted(changed),
        "added_files": sorted(added),
        "reviewed_functions": reviewed,
        "regression_sources": dict(FILE_TESTS),
        "fixed_reviewed_sources": dict(FILE_FIXED_SOURCES),
        "scope": "Ordinary baseline paths only; new file qualifications require their own actual costs; SQLite Decimal is refused, not qualified",
        "preserved": "All old table/Parquet registrations, source inputs, int64 recipes/oracles, arithmetic/state verification, SQL/check order, resources and publication owners remain unchanged",
        "performance_authority": PERFORMANCE_AUTHORITY,
        "rss_authority": RSS_AUTHORITY,
    }


def _file_source(path: str, current_snapshot: Path | None) -> bytes:
    root = (
        current_snapshot
        if current_snapshot is not None
        and path in EFFICIENCY_CHANGED | PHYSICAL_BINDING_CHANGED | REMAINING_1K_CHANGED
        and (current_snapshot / path).exists()
        else ROOT
    )
    if (
        root == ROOT
        and current_snapshot is not None
        and current_snapshot.name == "pre-physical-binding"
        and path in REMAINING_1K_CHANGED
        and (current_snapshot.parent / "pre-remaining-1k" / path).exists()
    ):
        root = current_snapshot.parent / "pre-remaining-1k"
    return (root / path).read_bytes()


def _efficiency_source_unchanged(path: str, old: ast.Module, current: ast.Module) -> list[str]:
    if path == "tests/test_r96_cost_audit.py":
        owner = "test_require_complete_cli_distinguishes_partial_report_from_28_id_exit"
        assignment = ast.parse('report["accepted_requirement_ids"] = passed').body[0]
        additions = [
            (node, statement)
            for node in ast.walk(_function(current, owner))
            if isinstance(node, ast.For)
            for statement in node.body
            if _dump(statement) == _dump(assignment)
        ]
        if len(additions) != 1:
            raise ValueError("The CLI mock adds only its actual accepted requirement count")
        loop, statement = additions[0]
        if loop.body.index(statement) != 1:
            raise ValueError("The CLI mock count follows its original per-case counts assignment")
        loop.body.remove(statement)
        if _dump(old) != _dump(current):
            raise ValueError("The CLI mock repair changed an existing test or its environment")
        return [owner + ".accepted_requirement_ids_mock"]
    if path == "tests/multisource_environment/clickhouse_analysis.py":
        original = _function(old, "setup_cluster")
        replacement = _function(current, "setup_cluster")
        policies = [
            node
            for node in ast.walk(original)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("ALTER USER analysis_reader SETTINGS")
        ]
        full = [
            node
            for node in ast.walk(_function(old, "setup"))
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("ALTER USER analysis_reader SETTINGS")
        ]
        if (
            len(policies) != 1
            or len(full) != 1
            or policies[0].value
            != "ALTER USER analysis_reader SETTINGS readonly=1, join_use_nulls=1"
        ):
            raise ValueError("The provisioning repair needs its exact original cluster policy")
        policies[0].value = full[0].value
        if _dump(original) != _dump(replacement) or _dump(old) != _dump(current):
            raise ValueError("Cluster provisioning changed beyond its complete reader policy")
        return ["setup_cluster.reader_policy"]
    if path == "tests/test_r96_physical_cost_fixtures.py":
        current.body.remove(
            _function(current, "test_cluster_reader_policy_matches_single_node_deadline_controls")
        )
        ast_imports = [
            node
            for node in current.body
            if isinstance(node, ast.Import) and ast.unparse(node) == "import ast"
        ]
        if len(ast_imports) != 1:
            raise ValueError("The cluster pin adds only its AST import")
        current.body.remove(ast_imports[0])
        if _dump(old) != _dump(current):
            raise ValueError("The cluster policy pin changed an existing fixture test")
        return ["test_cluster_reader_policy_matches_single_node_deadline_controls"]
    if path == "scripts/r96_cost_results.py":
        changed = {"audit", "main", "_boundary_runs"}
        added = {
            "valid_functional",
            "_functional_group",
            "_efficiency_manifest",
            "_retained_boundary_candidate",
            "_physical_fixed_bindings",
        }
        constants = {"EFFICIENCY_SCHEDULE", "SCENARIO_COVERAGE", "COMPLETE_STATUSES"}
    elif path == "scripts/r96_cost_reuse.py":
        changed = {"_file_reuse", "validate_current_reuse"}
        added = {"_file_source", "_efficiency_source_unchanged", "_efficiency_reuse"}
        constants = {
            "EFFICIENCY_ORIGINAL_SHA256",
            "EFFICIENCY_CHANGED",
            "EFFICIENCY_BOUNDARY_SOURCES",
        }
    elif path == "devtools/analysis_r9_cost.py":
        changed = {"run_group", "collect", "main", "grouped_summary"}
        added = {"_physical_snapshot", "_physical_fixed_binding"}
        constants = {
            "EFFICIENCY_SCHEDULE",
            "EFFICIENCY_SCENARIO_COVERAGE",
            "EFFICIENCY_SCENARIOS",
            "EFFICIENCY_PHYSICAL_PROFILES",
            "MeasurementKind",
        }
        typing = [
            node
            for node in current.body
            if isinstance(node, ast.ImportFrom) and node.module == "typing"
        ]
        if len(typing) != 1 or [item.name for item in typing[0].names] != [
            "TYPE_CHECKING",
            "Literal",
            "TypeAlias",
        ]:
            raise ValueError("Efficiency collector adds only its concrete measurement-kind type")
        typing[0].names = [
            item for item in typing[0].names if item.name not in ("Literal", "TYPE_CHECKING")
        ]
        serializer_imports = [
            node
            for node in current.body
            if isinstance(node, ast.ImportFrom)
            and ast.unparse(node) == "from dataclasses import asdict"
        ]
        blocks = [
            node
            for node in current.body
            if isinstance(node, ast.If)
            and ast.unparse(node)
            == "if TYPE_CHECKING:\n    from devtools.r96_cost_scenarios import Result, Workload"
        ]
        if len(serializer_imports) != 1 or len(blocks) != 1:
            raise ValueError(
                "Physical binding adds only its receipt serializer and type-only workload imports"
            )
        current.body.remove(serializer_imports[0])
        current.body.remove(blocks[0])
    else:
        if path == "tests/test_r96_cost_schedule.py":
            old.body.remove(_function(old, "test_baseline_cold_skip_changes_only_cold_schedule"))
            current.body.remove(
                _function(current, "test_baseline_cold_skip_changes_only_cold_schedule")
            )
            collection_imports = [
                node
                for node in current.body
                if isinstance(node, ast.ImportFrom) and node.module == "collections.abc"
            ]
            if len(collection_imports) != 1 or [
                alias.name for alias in collection_imports[0].names
            ] != ["Iterator", "Mapping"]:
                raise ValueError("The new physical binding stub adds only its Mapping type import")
            collection_imports[0].names = [
                alias for alias in collection_imports[0].names if alias.name != "Mapping"
            ]
            for name in ("argparse", "json"):
                imports = [
                    node
                    for node in current.body
                    if isinstance(node, ast.Import) and ast.unparse(node) == "import " + name
                ]
                if len(imports) != 1:
                    raise ValueError(
                        "Efficiency schedule tests add only their CLI/metadata imports"
                    )
                current.body.remove(imports[0])
        old_functions = {
            node.name: node
            for node in old.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for name in old_functions:
            if _dump(old_functions[name]) != _dump(_function(current, name)):
                raise ValueError("Efficiency tests changed an existing regression owner")
        current.body = [
            node
            for node in current.body
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            or node.name in old_functions
        ]
        if _dump(old) != _dump(current):
            raise ValueError("Efficiency tests changed their existing import/global environment")
        return ["new efficiency metadata regressions"]
    for name in changed:
        old.body.remove(_function(old, name))
        current.body.remove(_function(current, name))
    for name in added:
        current.body.remove(_function(current, name))
    remaining = set(constants)
    for node in tuple(current.body):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in constants
        ):
            remaining.remove(node.targets[0].id)
            current.body.remove(node)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id in constants
        ):
            remaining.remove(node.target.id)
            current.body.remove(node)
    if remaining or _dump(old) != _dump(current):
        raise ValueError("Efficiency controls changed a preserved execution/oracle owner")
    return sorted(changed | added)


def _efficiency_reuse(
    old_candidate: Mapping[str, object],
    new_candidate: Mapping[str, object],
    snapshot_directory: Path,
    current_snapshot: Path | None = None,
) -> dict[str, object]:
    if old_candidate.get("content_sha256") != EFFICIENCY_ORIGINAL_SHA256:
        raise ValueError("Only the actual C7 authority precedes this concrete efficiency amendment")
    if old_candidate.get("head") != new_candidate.get("head") or old_candidate.get(
        "dependencies"
    ) != new_candidate.get("dependencies"):
        raise ValueError("Efficiency reuse requires unchanged HEAD and dependencies")
    original, current = _hashes(old_candidate), _hashes(new_candidate)
    changed = {path for path in original if path in current and original[path] != current[path]}
    if original.keys() != current.keys() or changed != EFFICIENCY_CHANGED:
        raise ValueError("Efficiency reuse differs from its exact provisioning and metadata files")
    reviewed: dict[str, list[str]] = {}
    snapshots: dict[str, str] = {}
    for path, expected in current.items():
        source = _file_source(path, current_snapshot)
        if _hash(source) != expected:
            raise ValueError(f"Efficiency candidate source differs: {path}")
        if path in changed:
            before = (snapshot_directory / path).read_bytes()
            if _hash(before) != original[path]:
                raise ValueError(f"Efficiency snapshot does not bind C7: {path}")
            snapshots[path] = _hash(before)
            reviewed[path] = _efficiency_source_unchanged(
                path, ast.parse(before), ast.parse(source)
            )
    boundary = {
        path: current[path]
        for path in current
        if path.startswith("marivo/") or path in EFFICIENCY_BOUNDARY_SOURCES
    }
    return {
        "original_candidate": dict(old_candidate),
        "candidate": dict(new_candidate),
        "changed_files": sorted(changed),
        "snapshot_sha256": snapshots,
        "reviewed_functions": reviewed,
        "unchanged_boundary_sources": boundary,
        "scope": "Original ordinary baseline outcomes and actual C7 boundary runs only; physical/shared timings are not reused",
        "schedule": "r96-efficiency-42-v1",
        "performance_authority": PERFORMANCE_AUTHORITY,
        "rss_authority": RSS_AUTHORITY,
    }


def eligible_physical_binding_sample(sample: Mapping[str, object]) -> bool:
    """Retain only actual C8 Distributed source outcomes and their offline binding."""
    if (
        _obj(sample.get("candidate", {})).get("content_sha256") != PHYSICAL_BINDING_ORIGINAL_SHA256
        or sample.get("backend") != "clickhouse"
        or sample.get("profile") != "distributed"
        or sample.get("scenario") != "baseline"
        or sample.get("acceptance_schedule") != "r96-efficiency-42-v1"
        or sample.get("status") not in ("passed", "failed")
    ):
        return False
    if sample.get("schema") == "marivo.r96.physical-fixed-binding.v1":
        return (
            sample.get("facts") == 100000
            and sample.get("temperature") == "warmup"
            and type(sample.get("iteration")) is int
            and sample.get("iteration") == 0
            and sample.get("cost_measured") is False
        )
    if (
        sample.get("cost_scope") != "physical"
        or sample.get("requested_route") not in ("ibis", "ibis_python")
        or sample.get("fixed_mode") is not None
        or sample.get("recovery") is not None
    ):
        return False
    return (
        sample.get("schema") == "marivo.r96.functional-probe.v1"
        and sample.get("measurement_kind") == "functional"
        and sample.get("facts") == 1000
        and sample.get("temperature") == "functional"
        and type(sample.get("iteration")) is int
        and sample.get("iteration") == 0
    ) or (
        sample.get("schema") == "marivo.r96.cost-sample.v1"
        and sample.get("measurement_kind") == "cost"
        and sample.get("facts") == 100000
        and (
            (sample.get("temperature") == "warmup" and sample.get("iteration") == 0)
            or (
                sample.get("temperature") == "measured"
                and type(sample.get("iteration")) is int
                and sample.get("iteration") in (1, 2, 3)
            )
        )
    )


def _physical_binding_source_unchanged(
    path: str, old: ast.Module, current: ast.Module
) -> list[str]:
    approved = PHYSICAL_BINDING_AST.get(path)
    if not approved:
        raise ValueError("The physical binding repair needs its exact reviewed AST owners")
    for name, (before, after) in approved.items():
        new_owner = _function(current, name)
        if _hash(_dump(new_owner).encode()) != after:
            raise ValueError(f"The physical binding repair differs from its reviewed owner: {name}")
        current.body.remove(new_owner)
        if before is not None:
            old_owner = _function(old, name)
            if _hash(_dump(old_owner).encode()) != before:
                raise ValueError(
                    f"The physical binding snapshot differs from its reviewed owner: {name}"
                )
            old.body.remove(old_owner)
    if path == "scripts/r96_cost_results.py":
        imports = [
            node
            for node in current.body
            if isinstance(node, ast.ImportFrom)
            and ast.unparse(node) == "from dataclasses import asdict"
        ]
        if len(imports) != 1:
            raise ValueError("Receipt normalization adds only its exact dataclass serializer")
        current.body.remove(imports[0])
    elif path == "tests/test_r96_physical_cost_fixtures.py":
        imports = [
            node
            for node in current.body
            if isinstance(node, ast.ImportFrom) and node.module == "devtools.r96_cost_scenarios"
        ]
        if (
            len(imports) != 1
            or len([item for item in imports[0].names if item.name == "_data"]) != 1
        ):
            raise ValueError("The precision pin adds only its exact fixture builder import")
        imports[0].names = [item for item in imports[0].names if item.name != "_data"]
    elif path == "scripts/r96_cost_reuse.py":
        constants = {
            "PHYSICAL_BINDING_ORIGINAL_SHA256",
            "PHYSICAL_BINDING_CHANGED",
            "PHYSICAL_BINDING_AST",
        }
        for node in tuple(current.body):
            target = (
                node.targets[0]
                if isinstance(node, ast.Assign) and len(node.targets) == 1
                else node.target
                if isinstance(node, ast.AnnAssign)
                else None
            )
            if isinstance(target, ast.Name) and target.id in constants:
                constants.remove(target.id)
                current.body.remove(node)
        if constants:
            raise ValueError("The concrete physical repair declaration is incomplete")
    if _dump(old) != _dump(current):
        raise ValueError("The physical binding repair changed a preserved source or oracle owner")
    return sorted(approved)


def _physical_binding_reuse(
    old_candidate: Mapping[str, object],
    new_candidate: Mapping[str, object],
    snapshot_directory: Path,
    current_snapshot: Path | None = None,
) -> dict[str, object]:
    if old_candidate.get("content_sha256") != PHYSICAL_BINDING_ORIGINAL_SHA256:
        raise ValueError("Only the actual repaired C8 precedes this concrete physical repair")
    if old_candidate.get("head") != new_candidate.get("head") or old_candidate.get(
        "dependencies"
    ) != new_candidate.get("dependencies"):
        raise ValueError("Physical binding reuse requires unchanged HEAD and dependencies")
    original, current = _hashes(old_candidate), _hashes(new_candidate)
    changed = {path for path in original if path in current and original[path] != current[path]}
    if original.keys() != current.keys() or changed != PHYSICAL_BINDING_CHANGED:
        raise ValueError(
            "Physical binding reuse differs from its six exact fixture and metadata files"
        )
    reviewed: dict[str, list[str]] = {}
    snapshots: dict[str, str] = {}
    for path, expected in current.items():
        source = _file_source(path, current_snapshot)
        if _hash(source) != expected:
            raise ValueError(f"Physical binding candidate source differs: {path}")
        if path in changed:
            before = (snapshot_directory / path).read_bytes()
            if _hash(before) != original[path]:
                raise ValueError(f"Physical binding snapshot does not bind C8: {path}")
            snapshots[path] = _hash(before)
            reviewed[path] = _physical_binding_source_unchanged(
                path, ast.parse(before), ast.parse(source)
            )
    boundary = {
        path: current[path]
        for path in current
        if path.startswith("marivo/") or path in EFFICIENCY_BOUNDARY_SOURCES
    }
    return {
        "original_candidate": dict(old_candidate),
        "candidate": dict(new_candidate),
        "changed_files": sorted(changed),
        "snapshot_sha256": snapshots,
        "reviewed_functions": reviewed,
        "unchanged_boundary_sources": boundary,
        "scope": "Original ordinary outcomes and actual C7 boundary runs remain eligible; only C8 Distributed source outcomes, functional probes and its real offline binding are additionally retained",
        "precision_authority": "Trino fixture TIMESTAMP(6) meets existing UTC/us qualification; prior millisecond failures remain unchanged and unqualified",
        "receipt_authority": "Complete canonical Descriptor decoded before exact JSON-normalized dataclass primary and all-part receipt comparison; no raw receipt rewritten",
        "performance_authority": PERFORMANCE_AUTHORITY,
        "rss_authority": RSS_AUTHORITY,
    }


def _remaining_1k_source_unchanged(path: str, old: ast.Module, current: ast.Module) -> list[str]:
    approved = REMAINING_1K_AST.get(path)
    if not approved:
        raise ValueError("The remaining-1k amendment needs its exact reviewed AST owners")
    for name, (before, after) in approved.items():
        new_owner = _function(current, name)
        if _hash(_dump(new_owner).encode()) != after:
            raise ValueError(f"The remaining-1k amendment differs from its reviewed owner: {name}")
        current.body.remove(new_owner)
        if before is not None:
            old_owner = _function(old, name)
            if _hash(_dump(old_owner).encode()) != before:
                raise ValueError(
                    f"The remaining-1k snapshot differs from its reviewed owner: {name}"
                )
            old.body.remove(old_owner)
    if path == "scripts/r96_cost_reuse.py":
        constants = {"REMAINING_1K_ORIGINAL_SHA256", "REMAINING_1K_CHANGED", "REMAINING_1K_AST"}
    elif path in ("devtools/analysis_r9_cost.py", "scripts/r96_cost_results.py"):
        constants = {"REMAINING_1K_SCHEDULE"}
        if path == "devtools/analysis_r9_cost.py":
            constants.add("REMAINING_1K_PHYSICAL_PROFILES")
    else:
        constants = set()
    for node in tuple(current.body):
        target = (
            node.targets[0]
            if isinstance(node, ast.Assign) and len(node.targets) == 1
            else node.target
            if isinstance(node, ast.AnnAssign)
            else None
        )
        if isinstance(target, ast.Name) and target.id in constants:
            if target.id == "REMAINING_1K_SCHEDULE" and _dump(node) != _dump(
                ast.parse("REMAINING_1K_SCHEDULE = 'r96-remaining-1k-v1'").body[0]
            ):
                raise ValueError("The remaining-1k amendment requires its exact finite schedule")
            if target.id == "REMAINING_1K_PHYSICAL_PROFILES" and _dump(node) != _dump(
                ast.parse(
                    "REMAINING_1K_PHYSICAL_PROFILES = tuple(profile for profile in "
                    "EFFICIENCY_PHYSICAL_PROFILES if profile != 'clickhouse:distributed')"
                ).body[0]
            ):
                raise ValueError("The remaining-1k amendment excludes only completed Distributed")
            constants.remove(target.id)
            current.body.remove(node)
    if constants or _dump(old) != _dump(current):
        raise ValueError("The remaining-1k amendment changed a preserved source or oracle owner")
    return sorted(approved)


def _remaining_1k_reuse(
    old_candidate: Mapping[str, object],
    new_candidate: Mapping[str, object],
    snapshot_directory: Path,
) -> dict[str, object]:
    if old_candidate.get("content_sha256") != REMAINING_1K_ORIGINAL_SHA256:
        raise ValueError("Only the actual C9 repair authority precedes the remaining-1k amendment")
    if old_candidate.get("head") != new_candidate.get("head") or old_candidate.get(
        "dependencies"
    ) != new_candidate.get("dependencies"):
        raise ValueError("Remaining-1k reuse requires unchanged HEAD and dependencies")
    original, current = _hashes(old_candidate), _hashes(new_candidate)
    changed = {path for path in original if path in current and original[path] != current[path]}
    if original.keys() != current.keys() or changed != REMAINING_1K_CHANGED:
        raise ValueError(
            "Remaining-1k reuse differs from its six exact scheduling and metadata files"
        )
    reviewed: dict[str, list[str]] = {}
    snapshots: dict[str, str] = {}
    for path, expected in current.items():
        source = (ROOT / path).read_bytes()
        if _hash(source) != expected:
            raise ValueError(f"Remaining-1k candidate source differs: {path}")
        if path in changed:
            before = (snapshot_directory / path).read_bytes()
            if _hash(before) != original[path]:
                raise ValueError(f"Remaining-1k snapshot does not bind C9: {path}")
            snapshots[path] = _hash(before)
            reviewed[path] = _remaining_1k_source_unchanged(
                path, ast.parse(before), ast.parse(source)
            )
    return {
        "original_candidate": dict(old_candidate),
        "candidate": dict(new_candidate),
        "changed_files": sorted(changed),
        "snapshot_sha256": snapshots,
        "reviewed_functions": reviewed,
        "schedule": "r96-remaining-1k-v1",
        "unchanged_boundary_sources": {
            path: current[path]
            for path in current
            if path.startswith("marivo/") or path in EFFICIENCY_BOUNDARY_SOURCES
        },
        "scope": "Forty remaining groups use 1k facts; old ordinary outcomes and actual C8 Distributed 100k costs, 1k probes and offline binding retain their original authorities",
        "scale_authority": "Future 100k executions and growth claims are explicitly waived; 1k formal observations retain complete algorithms, grids, parts and original-key oracles",
        "repair_only_predecessor": "C9 is a static repair and actual engineering authority, not an invented cost phase",
        "performance_authority": PERFORMANCE_AUTHORITY,
        "rss_authority": RSS_AUTHORITY,
    }


def validate_current_reuse(
    authorities: Sequence[Mapping[str, object]], snapshot_directory: Path
) -> dict[str, object]:
    """Bind only the concrete ordered repairs, optimization and file/binder authorities."""
    if len(authorities) not in (4, 5, 6, 7, 8, 9, 10) or len(
        {str(candidate.get("content_sha256")) for candidate in authorities}
    ) != len(authorities):
        raise ValueError(
            "Only the four through ten concrete ordered repair authorities may combine"
        )
    original, harness_candidate, pressure_candidate, direct_candidate = authorities[:4]
    pre_pressure = snapshot_directory / "pre-pressure"
    pre_direct = snapshot_directory / "pre-direct"
    pre_keyed = snapshot_directory / "pre-keyed-oracle"
    pre_optimization = snapshot_directory / "pre-optimization"
    repairs = {
        "harness": _harness_reuse(original, harness_candidate, snapshot_directory, pre_pressure),
        "pressure_fixture": _pressure_reuse(
            harness_candidate, pressure_candidate, pre_pressure, pre_direct
        ),
        "direct_native": _direct_reuse(pressure_candidate, direct_candidate, pre_direct, pre_keyed),
    }
    if len(authorities) >= 5:
        repairs["keyed_oracle"] = _keyed_reuse(
            direct_candidate,
            authorities[4],
            pre_keyed,
            pre_optimization if len(authorities) >= 6 else None,
        )
    if len(authorities) >= 6:
        repairs["common_optimization"] = _optimization_reuse(
            authorities[4],
            authorities[5],
            pre_optimization,
            snapshot_directory / "pre-binder" if len(authorities) >= 7 else None,
        )
    if len(authorities) >= 7:
        repairs["file_and_binder"] = _file_reuse(
            authorities[5],
            authorities[6],
            snapshot_directory / "pre-binder",
            snapshot_directory / "pre-cluster" if len(authorities) >= 8 else None,
        )
    if len(authorities) >= 8:
        repairs["efficiency_schedule"] = _efficiency_reuse(
            authorities[6],
            authorities[7],
            snapshot_directory / "pre-cluster",
            snapshot_directory / "pre-physical-binding" if len(authorities) >= 9 else None,
        )
    if len(authorities) >= 9:
        repairs["physical_binding"] = _physical_binding_reuse(
            authorities[7],
            authorities[8],
            snapshot_directory / "pre-physical-binding",
            snapshot_directory / "pre-remaining-1k" if len(authorities) == 10 else None,
        )
    if len(authorities) == 10:
        repairs["remaining_1k_schedule"] = _remaining_1k_reuse(
            authorities[8], authorities[9], snapshot_directory / "pre-remaining-1k"
        )
    return {
        "schema": f"marivo.r96.cost-reuse-proof.v{len(authorities) - 1}",
        "execution_candidates": [dict(candidate) for candidate in authorities],
        "candidate": dict(authorities[-1]),
        "repairs": repairs,
        "boundary": "Original hashes and failures stay unchanged; old native timing and weak pressure oracles are not reused",
        "rss_authority": RSS_AUTHORITY,
        "performance_authority": PERFORMANCE_AUTHORITY,
    }
