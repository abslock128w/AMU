"""Source identity controls for the concrete-patch graph prerequisite."""

import copy

import pytest

from amu import graph_readiness, graph_refresh, source_evidence


def _repo(tmp_path, source, initializer="", module="def target(value):\n    return value\n"):
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text(initializer)
    (package / "api.py").write_text(module)
    (package / "client.py").write_text(source)
    return str(tmp_path)


def _impact(name, source_path="pkg/client.py", start=2, end=3):
    return {
        "focus": {"id": "repo:python:" + source_path + ":consumer", "name": "consumer", "qualified_name": "consumer", "file_path": source_path,
                  "start_line": start, "end_line": end},
        "callers": {"total": 0, "entries": []},
        "callees": {"total": 1, "entries": [{
            "endpoint": {"name": name.rsplit(".", 1)[-1], "qualified_name": name,
                         "id": "external:symbol:" + name, "external": True},
            "relation": "CALLS", "direction": "out", "depth": 1,
        }]},
        "type_consumers": {"total": 0, "entries": []},
        "data_flows": {"total": 0, "entries": []},
    }


@pytest.mark.parametrize("statement,call", [
    ("from pkg import api", "api.target(value)"),
    ("from pkg import api as chosen", "chosen.target(value)"),
    ("from . import api", "api.target(value)"),
])
def test_package_submodule_call_has_exact_source_identity(tmp_path, statement, call):
    repo = _repo(tmp_path, f"{statement}\ndef consumer(value):\n    return {call}\n")
    raw = _impact("pkg.target")
    filtered, contradicted, uncertain = source_evidence.filter_python_call_evidence(repo, {"consumer": raw})
    entry = filtered["consumer"]["callees"]["entries"][0]
    assert entry["endpoint"]["file_path"] == "pkg/api.py"
    assert entry["endpoint"]["qualified_name"] == "target"
    assert entry["source_resolution"]["provider_endpoint"] == raw["callees"]["entries"][0]["endpoint"]
    assert not contradicted and not uncertain
    assert graph_refresh.live_edges("pkg/client.py#consumer", filtered["consumer"]).available
    assert source_evidence.graph_call_evidence(
        repo, "pkg/client.py", "target", target_path="pkg/api.py", scope_start=2, scope_end=3,
    ) == "verified"


@pytest.mark.parametrize("initializer", [
    "api = OTHER\n", "from elsewhere import api\n", "from elsewhere import *\n",
    "def __getattr__(name):\n    return UNKNOWN\n", "globals()['api'] = OTHER\n",
])
def test_package_rebinding_remains_incomplete(tmp_path, initializer):
    repo = _repo(tmp_path, "from pkg import api\ndef consumer(value):\n    return api.target(value)\n", initializer)
    raw = _impact("pkg.target")
    filtered, contradicted, uncertain = source_evidence.filter_python_call_evidence(repo, {"consumer": raw})
    assert filtered["consumer"] == raw
    assert uncertain and not contradicted
    assert not graph_refresh.live_edges("pkg/client.py#consumer", filtered["consumer"]).available


def test_package_module_file_collision_remains_unresolved(tmp_path):
    repo = _repo(tmp_path, "from pkg import api\ndef consumer(value):\n    return api.target(value)\n")
    (tmp_path / "pkg.py").write_text("api = OTHER\n")
    filtered, _, uncertain = source_evidence.filter_python_call_evidence(repo, {"consumer": _impact("pkg.target")})
    assert uncertain
    assert not graph_refresh.live_edges("pkg/client.py#consumer", filtered["consumer"]).available


@pytest.mark.parametrize("statement,call,identity", [
    ("import hashlib", "hashlib.sha256(value)", "hashlib.sha256"),
    ("import hashlib as digest", "digest.sha256(value)", "hashlib.sha256"),
    ("from pathlib import PurePosixPath", "PurePosixPath(value)", "pathlib.PurePosixPath"),
])
def test_source_established_stdlib_boundary_is_lossless(tmp_path, statement, call, identity):
    repo = _repo(tmp_path, f"{statement}\ndef consumer(value):\n    return {call}\n")
    raw = _impact(identity)
    filtered, contradicted, uncertain = source_evidence.filter_python_call_evidence(repo, {"consumer": raw})
    entry = filtered["consumer"]["callees"]["entries"][0]
    assert "file_path" not in entry["endpoint"]
    assert entry["external_boundary"]["identity"] == identity
    assert entry["external_boundary"]["source_path"] == "pkg/client.py"
    assert entry["external_boundary"]["locations"] == [{"line": 3, "end_line": 3}]
    assert entry["external_boundary"]["runtime_verified"] is False
    assert not contradicted and not uncertain
    edges = graph_refresh.live_edges("pkg/client.py#consumer", filtered["consumer"])
    assert edges.available
    assert edges.edges[0]["related"] == "external:python:" + identity
    assert edges.edges[0]["external_boundary"] == entry["external_boundary"]
    assert filtered["consumer"]["callees"]["total"] == raw["callees"]["total"]
    assert graph_refresh.delta(edges.edges, edges.edges)["removed"] == []


@pytest.mark.parametrize("statement,call,identity", [
    ("import thirdparty", "thirdparty.target(value)", "thirdparty.target"),
    ("import hashlib", "value.sha256(value)", "hashlib.sha256"),
    ("import hashlib", "hashlib.sha256(value)", "other.sha256"),
])
def test_unestablished_external_candidate_remains_a_blocker(tmp_path, statement, call, identity):
    repo = _repo(tmp_path, f"{statement}\ndef consumer(value):\n    return {call}\n")
    filtered, _, uncertain = source_evidence.filter_python_call_evidence(repo, {"consumer": _impact(identity)})
    assert uncertain
    assert not graph_readiness.live_map_evidence([], filtered, root_file="pkg/client.py")["approvable"]


def test_local_stdlib_shadow_and_parameter_shadow_cannot_be_external(tmp_path):
    repo = _repo(tmp_path, "import hashlib\ndef consumer(value):\n    return hashlib.sha256(value)\n")
    (tmp_path / "hashlib.py").write_text("def sha256(value):\n    return value\n")
    filtered, _, uncertain = source_evidence.filter_python_call_evidence(repo, {"consumer": _impact("hashlib.sha256")})
    assert not uncertain
    assert filtered["consumer"]["callees"]["entries"][0]["endpoint"]["file_path"] == "hashlib.py"
    assert "external_boundary" not in filtered["consumer"]["callees"]["entries"][0]
    (tmp_path / "hashlib.py").unlink()
    (tmp_path / "pkg/client.py").write_text("import hashlib\ndef consumer(hashlib):\n    return hashlib.sha256(hashlib)\n")
    filtered, _, uncertain = source_evidence.filter_python_call_evidence(repo, {"consumer": _impact("hashlib.sha256")})
    assert uncertain


def test_external_annotation_without_source_establishment_is_not_accepted(tmp_path):
    raw = _impact("thirdparty.target")
    raw["callees"]["entries"][0]["external_boundary"] = {"identity": "thirdparty.target"}
    assert not graph_refresh.live_edges("pkg/client.py#consumer", raw).available
    truncated = copy.deepcopy(raw)
    truncated["callees"]["total"] = 2
    assert not graph_refresh.live_edges("pkg/client.py#consumer", truncated).available


def test_external_boundaries_survive_saved_baseline_and_refresh(tmp_path):
    repo = _repo(tmp_path, "import hashlib\ndef consumer(value):\n    return hashlib.sha256(value)\n")
    target = "pkg/client.py#consumer"
    impacts, _, _ = source_evidence.filter_python_call_evidence(repo, {"consumer": _impact("hashlib.sha256")})
    raw = impacts["consumer"]
    baselines, errors = graph_refresh.edge_baselines_from_impacts("pkg/client.py", impacts, 1)
    assert not errors
    capture = {"stable": True, "before": {"head": "base", "digest": "source"},
               "after": {"head": "base", "digest": "source"}, "pre_edit_graph_files": []}
    saved = {"root": {"file": "pkg/client.py"}, "depth": 1, "map_hash": "identity",
             "graph_evidence": {"queried_symbols": ["consumer"], "unresolved_focus": [],
                                "map_hash": "identity", "worktree": capture,
                                "impact_coverage": {target: graph_refresh.coverage_from_impact(target, 1, raw)},
                                "edge_baselines": baselines}}
    before = graph_refresh.map_baseline(target, 1, saved)
    after = graph_refresh.live_edges(target, raw)
    assert before.available and after.available
    assert before.edges == after.edges
    assert before.edges[0]["external_boundary"]["provider_endpoint"] == _impact("hashlib.sha256")["callees"]["entries"][0]["endpoint"]
    changed = copy.deepcopy(raw)
    changed["callees"] = {"total": 0, "entries": []}
    delta = graph_refresh.delta(before.edges, graph_refresh.live_edges(target, changed).edges)
    assert delta["removed"] == before.edges
    damaged = copy.deepcopy(saved)
    damaged["graph_evidence"]["edge_baselines"][target][0]["external_boundary"]["source_sha256"] = "missing"
    assert not graph_refresh.map_baseline(target, 1, damaged).available


@pytest.mark.parametrize("source", [
    "from pkg import api\ndef consumer(api):\n    return api.target(api)\n",
    "from pkg import api\napi = OTHER\ndef consumer(value):\n    return api.target(value)\n",
])
def test_submodule_shadowing_does_not_resolve_a_pathless_edge(tmp_path, source):
    repo = _repo(tmp_path, source)
    start = 2 if "def consumer(api)" in source else 3
    filtered, _, uncertain = source_evidence.filter_python_call_evidence(
        repo, {"consumer": _impact("pkg.target", start=start, end=start + 1)},
    )
    assert uncertain
    assert "file_path" not in filtered["consumer"]["callees"]["entries"][0]["endpoint"]


def test_decorated_submodule_target_is_still_an_explicit_unknown(tmp_path):
    repo = _repo(tmp_path, "from pkg import api\ndef consumer(value):\n    return api.target(value)\n",
                 module="@unresolved_decorator\ndef target(value):\n    return value\n")
    filtered, _, uncertain = source_evidence.filter_python_call_evidence(repo, {"consumer": _impact("pkg.target")})
    assert uncertain
    assert not graph_refresh.live_edges("pkg/client.py#consumer", filtered["consumer"]).available


def test_plain_stdlib_frozen_dataclass_has_a_source_identity(tmp_path):
    repo = _repo(
        tmp_path,
        "from pkg import api\ndef consumer(value):\n    return api.MapRequest(value)\n",
        module=("from dataclasses import dataclass\n\n"
                "@dataclass(frozen=True)\nclass MapRequest:\n    value: int\n"),
    )
    filtered, contradicted, uncertain = source_evidence.filter_python_call_evidence(
        repo, {"consumer": _impact("pkg.MapRequest")},
    )
    endpoint = filtered["consumer"]["callees"]["entries"][0]["endpoint"]
    assert endpoint["file_path"] == "pkg/api.py"
    assert endpoint["qualified_name"] == "MapRequest"
    assert endpoint["kind"] == "class"
    assert not contradicted and not uncertain
    assert graph_refresh.live_edges("pkg/client.py#consumer", filtered["consumer"]).available


@pytest.mark.parametrize("decorator,extra,shadow", [
    ("@custom", "", False),
    ("@dataclass(init=False)", "", False),
    ("@dataclass(frozen=True)", "dataclass = custom\n", False),
    ("@dataclass(frozen=True)", "globals()['dataclass'] = custom\n", False),
    ("@dataclass(frozen=True)", "", True),
])
def test_unestablished_dataclass_decorator_remains_unresolved(tmp_path, decorator, extra, shadow):
    repo = _repo(
        tmp_path,
        "from pkg import api\ndef consumer(value):\n    return api.MapRequest(value)\n",
        module=("from dataclasses import dataclass\n" + extra + "\n"
                + decorator + "\nclass MapRequest:\n    value: int\n"),
    )
    if shadow:
        (tmp_path / "dataclasses.py").write_text("def dataclass(*args, **kwargs):\n    return lambda cls: cls\n")
    filtered, _, uncertain = source_evidence.filter_python_call_evidence(repo, {"consumer": _impact("pkg.MapRequest")})
    assert uncertain
    assert "file_path" not in filtered["consumer"]["callees"]["entries"][0]["endpoint"]


def test_descriptor_or_class_method_target_is_not_inferred_from_spelling(tmp_path):
    repo = _repo(tmp_path, "from pkg import api\ndef consumer(value):\n    return api.Container.target(value)\n",
                 module="class Container:\n    def target(self, value):\n        return value\n")
    filtered, _, uncertain = source_evidence.filter_python_call_evidence(repo, {"consumer": _impact("pkg.Container.target")})
    assert uncertain
    assert not graph_refresh.live_edges("pkg/client.py#consumer", filtered["consumer"]).available


def test_static_resolution_never_executes_package_or_candidate_code(tmp_path):
    marker = tmp_path / "executed"
    repo = _repo(tmp_path, "from pkg import api\ndef consumer(value):\n    return api.target(value)\n",
                 initializer=f"from pathlib import Path\nPath({str(marker)!r}).write_text('execution')\n")
    source_evidence.filter_python_call_evidence(repo, {"consumer": _impact("pkg.target")})
    assert not marker.exists()
