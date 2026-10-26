"""Keep inspectable stage contracts synchronized with the executable workflow."""

import ast
import json
from pathlib import Path

from tma_qc.api import stages
from tma_qc.config import DEFAULTS
from tma_qc.schema import CORE_STAGES, PAIR_STAGES
from tma_qc.settings import METHODS


def test_stage_contracts_match_executable_order_dependencies_and_parameter_sections():
    path = Path(__file__).resolve().parents[2] / "src/tma_qc/workflows.py"
    syntax = ast.parse(path.read_text())
    functions = {node.name: node for node in syntax.body if isinstance(node, ast.FunctionDef)}
    registry = stages()
    for function, key in (("process_core", "af_stages"), ("process_pair", "pair_stages")):
        calls = sorted(
            (
                node
                for node in ast.walk(functions[function])
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "runner"
                and node.func.attr == "stage"
            ),
            key=lambda node: node.lineno,
        )
        assert [ast.literal_eval(node.args[3]) for node in calls] == [
            row["name"] for row in registry[key]
        ]
        for call, contract in zip(calls, registry[key], strict=True):
            keywords = {node.arg: node.value for node in call.keywords}
            dependencies = ast.literal_eval(keywords["deps"]) if "deps" in keywords else ()
            assert list(dependencies) == contract["dependencies"]
            assert (
                list(ast.literal_eval(keywords["config_sections"])) == contract["config_sections"]
            )


def test_stage_contracts_have_registered_choices_valid_sections_and_json_values():
    registry = stages()
    assert [row["name"] for row in registry["af_stages"]] == list(CORE_STAGES)
    assert [row["name"] for row in registry["pair_stages"]] == list(PAIR_STAGES)
    for row in registry["af_stages"] + registry["pair_stages"]:
        assert row["description"] and row["inputs"] and row["outputs"]
        assert set(row["config_sections"]) <= set(DEFAULTS)
        assert bool(row["method_selectors"]) != bool(row["fixed_algorithm"])
        assert row["methods"] == {key: METHODS[key] for key in row["method_selectors"]}
    assert json.loads(json.dumps(registry, allow_nan=False)) == registry


def test_stage_contracts_do_not_share_mutable_registry_state():
    original = stages()
    changed = stages()
    changed["af_stages"][0]["inputs"].clear()
    geometry = next(row for row in changed["af_stages"] if row["name"] == "geometry")
    geometry["methods"]["geometry_method"]["choices"].clear()
    assert stages() == original
