"""Public line-impact reliability, bounded pagination and current-source binding."""

import base64
import hashlib
import json
from copy import deepcopy

import pytest

from amu.harness.context import estimate
from amu.services import impact, mapping


@pytest.fixture
def context(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "api.py").write_text("def target(value):\n    return value\n")
    observed = {"digest": "revision-a", "rows": [], "contradictions": [], "uncertainties": [], "calls": []}
    monkeypatch.setattr(mapping, "worktree_fingerprint", lambda *_args: {
        "head": "fixture-head", "digest": observed["digest"], "files": [], "error": None,
    })

    def inspect(repo, request):
        observed["calls"].append((repo, request))
        return {
            "map_hash": observed["digest"], "nodes": deepcopy(observed["rows"]),
            "resolution": {"source_contradictions": deepcopy(observed["contradictions"]),
                           "source_uncertainties": deepcopy(observed["uncertainties"])},
            "graph_evidence": {"approvable": True, "sha": "fixture-head", "at": "changing-display-time",
                               "reasons": [], "impact_coverage": {"api.py#target": {"complete": True}},
                               "edge_baseline_errors": {}, "unresolved_focus": []},
        }

    monkeypatch.setattr(mapping, "inspect", inspect)
    return root, observed


def test_public_impact_never_exceeds_response_cap_for_long_source_identity(tmp_path, monkeypatch):
    (tmp_path / "api.py").write_text("def " + "x" * 70000 + "():\n    return 1\n")

    def unavailable(*_args):
        raise mapping.GraphDataError("fixture graph unavailable")

    monkeypatch.setattr(mapping, "worktree_fingerprint", unavailable)
    try:
        packet = impact.execute(str(tmp_path), impact.ImpactRequest("api.py", 2, "change the return value"))
    except impact.ImpactInputError as exc:
        packet = exc.payload
    assert estimate(packet) <= impact.MAX_PACKET_BYTES


def test_paging_retrieves_every_dependency_without_state_writes(context):
    root, observed = context
    observed["rows"] = [{"id": f"n{i}", "path": "consumer.py", "symbol": f"consumer_{i}",
                         "confidence": "sound", "risk": "might_break", "via": "CALLS", "depth": 1}
                        for i in range(125)]
    source = (root / "api.py").read_bytes()
    cursor = None
    found = []
    identities = set()
    for _ in range(10):
        request = impact.ImpactRequest("api.py", 2, "change return value", cursor=cursor, page_size=17)
        packet = impact.execute(str(root), request)
        assert estimate(packet) <= impact.MAX_PACKET_BYTES
        assert packet["assessment"]["safe_to_change"] is None
        identities.add(packet["report_hash"])
        found.extend(row["id"] for row in packet["impact"]["rows"])
        cursor = packet["page"]["next_cursor"]
        if cursor is None:
            break
    assert len(identities) == 1
    assert len(found) == len(set(found)) == 125
    assert set(found) == {f"n{i}" for i in range(125)}
    assert (root / "api.py").read_bytes() == source
    assert not (root / ".amu").exists()


def test_evidence_omission_has_same_tool_retrieval(context):
    root, observed = context
    observed["uncertainties"] = [{"target": "target", "endpoint": f"callee_{i}", "evidence": "unverified"}
                                 for i in range(140)]
    initial = impact.execute(str(root), impact.ImpactRequest("api.py", 2, "change return value", page_size=11))
    assert initial["graph_evidence"]["source_uncertainties_count"] == 140
    assert initial["truncated"] is True
    cursor = initial["retrieval"]["source_uncertainties"]["cursor"]
    found = []
    for _ in range(20):
        page = impact.execute(str(root), impact.ImpactRequest(
            "api.py", 2, "change return value", section="source_uncertainties", cursor=cursor, page_size=11))
        assert estimate(page) <= impact.MAX_PACKET_BYTES
        assert page["assessment"]["safe_to_change"] is None
        found.extend(row["endpoint"] for row in page["details"]["items"])
        cursor = page["page"]["next_cursor"]
        if cursor is None:
            break
    assert len(found) == len(set(found)) == 140


@pytest.mark.parametrize("mutation", ["source", "worktree", "proposal"])
def test_cursor_cannot_stitch_different_analysis_versions(context, mutation):
    root, observed = context
    observed["rows"] = [{"id": f"n{i}", "path": "consumer.py", "symbol": f"consumer_{i}"} for i in range(4)]
    first = impact.execute(str(root), impact.ImpactRequest("api.py", 2, "change return value", page_size=1))
    cursor = first["page"]["next_cursor"]
    proposal = "change return value"
    if mutation == "source":
        (root / "api.py").write_text("def target(value):\n    return None\n")
    elif mutation == "worktree":
        observed["digest"] = "revision-b"
    else:
        proposal = "change exceptions"
    with pytest.raises(impact.ImpactInputError, match="cursor.*stale|different analysis"):
        impact.execute(str(root), impact.ImpactRequest("api.py", 2, proposal, cursor=cursor, page_size=1))


def test_rows_changing_without_git_digest_change_invalidate_cursor(context):
    root, observed = context
    observed["rows"] = [{"id": "n1", "path": "a.py", "symbol": "caller"},
                        {"id": "n2", "path": "b.py", "symbol": "other"}]
    first = impact.execute(str(root), impact.ImpactRequest("api.py", 2, "change return value", page_size=1))
    observed["rows"][1]["symbol"] = "new_caller"
    with pytest.raises(impact.ImpactInputError, match="cursor.*stale|different analysis"):
        impact.execute(str(root), impact.ImpactRequest("api.py", 2, "change return value",
                                                     cursor=first["page"]["next_cursor"], page_size=1))


def test_one_oversized_optional_item_is_not_silently_skipped(context):
    root, observed = context
    observed["rows"] = [{"id": "n1", "path": "consumer.py", "reason": "x" * 100000}]
    packet = impact.execute(str(root), impact.ImpactRequest("api.py", 2, "change return value"))
    assert estimate(packet) <= impact.MAX_PACKET_BYTES
    assert packet["status"] == "uncertain"
    # The initial red test required explicit refusal rather than silently
    # skipping a row. Full hash-checked fragment retrieval is stronger: every
    # byte of that row remains available through the same authorized tool.
    assert packet["page"]["fragmented_items"] == 1
    assert packet["retrieval"]["impact"]["logical_items"] == 1
    fragments = [row["evidence_fragment"] for row in packet["impact"]["rows"]]
    cursor = packet["page"]["next_cursor"]
    while cursor is not None:
        packet = impact.execute(str(root), impact.ImpactRequest("api.py", 2, "change return value", cursor=cursor))
        assert estimate(packet) <= impact.MAX_PACKET_BYTES
        fragments.extend(row["evidence_fragment"] for row in packet["impact"]["rows"])
        cursor = packet["page"]["next_cursor"]
    assert len(fragments) == fragments[0]["fragment_count"]
    assert [fragment["fragment_index"] for fragment in fragments] == list(range(len(fragments)))
    body = b"".join(base64.b64decode(fragment["data"], validate=True) for fragment in fragments)
    assert hashlib.sha256(body).hexdigest() == fragments[0]["item_sha256"]
    decoded = json.loads(body)
    assert decoded["id"] == "n1" and decoded["reason"] == "x" * 100000
    assert packet["assessment"]["safe_to_change"] is None


def test_cursor_rejects_changed_fragment_layout(context, monkeypatch):
    from amu.services import impact_report

    root, observed = context
    observed["rows"] = [{"id": "n1", "path": "consumer.py", "reason": "x" * 20000}]
    first = impact.execute(str(root), impact.ImpactRequest("api.py", 2, "change return value", page_size=1))
    assert first["page"]["next_cursor"]
    monkeypatch.setattr(impact_report, "FRAGMENT_BYTES", 2048)
    with pytest.raises(impact.ImpactInputError, match="cursor.*stale|different analysis"):
        impact.execute(str(root), impact.ImpactRequest("api.py", 2, "change return value",
                                                     cursor=first["page"]["next_cursor"], page_size=1))


def test_report_pages_fit_full_mcp_envelope_not_only_payload(context):
    from amu.mcp_manifest import MCP_WIRE_LIMIT, tool_result_bytes

    root, observed = context
    observed["rows"] = [{"id": str(i), "reason": "x" * 500} for i in range(100)]
    packet = impact.execute(str(root), impact.ImpactRequest("api.py", 2, "x" * 8192, page_size=100))
    assert estimate(packet) <= impact.MAX_PACKET_BYTES
    assert tool_result_bytes(json.dumps(packet, ensure_ascii=False)) <= MCP_WIRE_LIMIT
    assert packet["request"]["page_size"] == 100
    assert packet["page"]["returned"] < 100  # The effective page can shrink, not the echoed input.


@pytest.mark.parametrize("source,line,expected", [
    ("@decorate\ndef target():\n    return 1\n", 1, "target"),
    ("class API:\n    async def target(self):\n        return 1\n", 3, "API.target"),
    ("def outer():\n    def inner():\n        return 1\n    return inner()\n", 3, "outer.inner"),
])
def test_source_location_resolves_innermost_declaration(context, source, line, expected):
    root, observed = context
    (root / "api.py").write_text(source)
    packet = impact.execute(str(root), impact.ImpactRequest("api.py", line, "change return value"))
    assert packet["target"]["qualified_name"] == expected
    assert observed["calls"][0][1].symbol == "api.py#" + expected


def test_module_scope_and_repeated_symbol_do_not_query_guessed_target(context):
    root, observed = context
    for source, line, resolution in [
        ("version = 1\ndef target():\n    return 1\n", 1, "module-scope"),
        ("def target():\n    return 1\ndef target():\n    return 2\n", 4, "ambiguous"),
    ]:
        (root / "api.py").write_text(source)
        packet = impact.execute(str(root), impact.ImpactRequest("api.py", line, "change return value"))
        assert packet["target"]["resolution"] == resolution
        assert packet["status"] in {"uncertain", "unsupported"}
        assert not observed["calls"]


@pytest.mark.parametrize("source,line", [(b"\xff", 1), (b"def broken(:\n", 1), (b"def target():\n    pass\n", 99)])
def test_unparseable_or_out_of_range_source_refused_before_graph(context, source, line):
    root, observed = context
    (root / "api.py").write_bytes(source)
    with pytest.raises(impact.ImpactInputError):
        impact.execute(str(root), impact.ImpactRequest("api.py", line, "change return value"))
    assert not observed["calls"]


def test_source_symlink_and_fifo_refused_without_reading(context, tmp_path):
    import os

    root, observed = context
    outside = tmp_path / "unselected.py"
    outside.write_text("def target():\n    pass\n")
    (root / "alias.py").symlink_to(outside)
    os.mkfifo(root / "pipe.py")
    for name in ("alias.py", "pipe.py"):
        with pytest.raises(impact.ImpactInputError):
            impact.execute(str(root), impact.ImpactRequest(name, 1, "change return value"))
    assert not observed["calls"]


def test_source_changed_during_graph_query_never_returns_completed_capture(context, monkeypatch):
    root, observed = context
    original = mapping.inspect

    def mutate(repo, request):
        result = original(repo, request)
        (root / "api.py").write_text("def target(value):\n    return None\n")
        return result

    monkeypatch.setattr(mapping, "inspect", mutate)
    packet = impact.execute(str(root), impact.ImpactRequest("api.py", 2, "change return value"))
    assert packet["status"] == "uncertain"
    assert packet["assessment"]["provider_reported_coverage_complete"] is False
    cursor = packet["retrieval"]["reasons"]["cursor"]
    # Source races are not converted into an apparently complete zero-row report.
    assert cursor is not None and packet["assessment"]["reasons"]


def test_coverage_preserves_graph_baselines_and_semantic_counts(context, monkeypatch):
    root, _ = context
    original = mapping.inspect
    diagnostics = {"semantic_edges": 37, "unresolved_focus": [], "unverified_python_calls": ["api.py#target"],
                   "edge_baselines": {"api.py#target": [{"relation": "CALLS", "direction": "in", "related": "c.py#caller"}]},
                   "baseline_ready": True, "pre_edit_graph_files": []}

    def with_diagnostics(repo, request):
        result = original(repo, request)
        result["graph_evidence"].update(diagnostics)
        return result

    monkeypatch.setattr(mapping, "inspect", with_diagnostics)
    packet = impact.execute(str(root), impact.ImpactRequest("api.py", 2, "change return value", section="coverage"))
    returned = {row["field"]: row["value"] for row in packet["details"]["items"]}
    for key, value in diagnostics.items():
        assert returned[key] == value


@pytest.mark.parametrize("cursor", ["%%%", "not-a-json-cursor", "eyJ2IjoxfQ"])
def test_malformed_cursor_refused_before_source_or_provider(context, monkeypatch, cursor):
    root, observed = context
    monkeypatch.setattr(impact, "_source", lambda *_args, **_kwargs: pytest.fail("malformed cursor must not read source"))
    with pytest.raises(impact.ImpactInputError, match="cursor is invalid"):
        impact.execute(str(root), impact.ImpactRequest("api.py", 2, "change return value", cursor=cursor))
    assert not observed["calls"]
