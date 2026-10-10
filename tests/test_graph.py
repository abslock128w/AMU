import json
import re
from pathlib import Path

from typer.testing import CliRunner

from amu import cli, graphview

IMP = json.loads((Path(__file__).parent / "fixtures" / "impact.json").read_text())


def _p():
    return graphview.payload_from_impact("classify_consumers", IMP)


def test_payload_has_focus_nodes_edges_with_confidence():
    p = _p()
    assert p["focus"].endswith("classify_consumers") and p["counts"]["callers"] > 0
    assert all(n["confidence"] in ("sound", "guessed", "unknown") and n["reason"] and n["verify"] for n in p["nodes"])
    assert all(e["relation"] for e in p["edges"])


def test_deterministic():
    assert graphview.to_ascii(_p()) == graphview.to_ascii(_p()) and graphview.to_mermaid(_p()) == graphview.to_mermaid(_p())


def test_mermaid_is_valid_shape():
    m = graphview.to_mermaid(_p())
    lines = m.splitlines()
    assert lines[0] == "graph TD"
    edge_lines = [ln for ln in lines if "-->" in ln]
    assert edge_lines and all(re.match(r"^\s+n_\w+ -->\|[A-Z_]+\| n_\w+$", ln) for ln in edge_lines)
    assert all(re.match(r'^\s+n_\w+(\[\[|\(|\[)".*"(\]\]|\)|\])$', ln) for ln in lines if '"' in ln and "-->" not in ln)


def test_dot_is_a_digraph():
    d = graphview.to_dot(_p())
    assert d.startswith("digraph amu {") and d.rstrip().endswith("}") and '-> "n_' in d


def test_ascii_has_glyphs_and_relation_labels():
    a = graphview.to_ascii(_p())
    assert "●" in a and "CALLS" in a and "callers" in a and "edges " in a


def test_ambiguous_focus_is_unknown():
    p = graphview.payload_from_impact("x", {**IMP, "disambiguation_required": True, "definitions": [{}, {}]})
    f = next(n for n in p["nodes"] if n["id"] == p["focus"])
    assert f["confidence"] == "unknown" and "ambiguous" in f["reason"]


def test_relation_filter():
    p = graphview.payload_from_impact("classify_consumers", IMP, relation="DATA_FLOWS")
    assert all(e["relation"] == "DATA_FLOWS" for e in p["edges"])


def test_live_outbound_data_flow_keeps_its_provider_direction():
    imp = {
        "focus": {"id": "focus", "name": "target", "file_path": "pkg/target.py", "kind": "function"},
        "data_flows": {
            "entries": [
                {
                    "endpoint": {"id": "consumer", "name": "consumer", "file_path": "pkg/consumer.py", "kind": "function"},
                    "relation": "DATA_FLOWS",
                    "direction": "out",
                    "depth": 1,
                }
            ]
        },
    }

    payload = graphview.payload_from_impact("target", imp)

    assert payload["edges"] == [
        {"from": "focus", "to": "consumer", "relation": "DATA_FLOWS", "direction": "out", "section": "data_flows", "depth": 1,
         "provenance": "unknown", "confidence": "sound"}
    ]


def _docstring_only_impact() -> dict:
    return {
        "focus": {
            "id": "target-id",
            "name": "target",
            "qualified_name": "target",
            "file_path": "sample.py",
            "kind": "function",
            "start_line": 1,
            "end_line": 3,
        },
        "callees": {
            "total": 1,
            "entries": [
                {
                    "endpoint": {"id": "helper-id", "name": "helper", "file_path": "sample.py", "kind": "function"},
                    "relation": "CALLS",
                    "depth": 1,
                }
            ],
        },
    }


def _write_docstring_only_source(tmp_path):
    (tmp_path / "sample.py").write_text(
        "def target():\n"
        '    """Example only: helper()."""\n'
        "    return 0\n\n"
        "def helper():\n"
        "    return 1\n"
    )


def test_live_payload_excludes_source_proven_docstring_call_and_marks_evidence_unapprovable(tmp_path):
    _write_docstring_only_source(tmp_path)

    payload = graphview.payload_from_live_impact(str(tmp_path), "target", _docstring_only_impact())

    assert payload["edges"] == []
    assert payload["source_contradictions"] == [
        {
            "target": "target",
            "section": "callees",
            "source_path": "sample.py",
            "endpoint": "helper",
            "line": None,
            "evidence": "source:python-ast:docstring-only-call",
        }
    ]
    assert payload["graph_evidence"]["approvable"] is False
    assert payload["status"] == "incomplete"
    assert {node["confidence"] for node in payload["nodes"]} == {"unknown"}
    assert any("source AST excluded 1" in warning for warning in payload["warnings"])
    assert any("not approvable" in warning for warning in payload["warnings"])


def test_live_payload_marks_a_truncated_exact_target_as_not_approvable(tmp_path):
    payload = graphview.payload_from_live_impact(
        str(tmp_path),
        "classify_consumers",
        IMP,
        target="amu/classify.py#classify_consumers",
    )

    assert payload["graph_evidence"]["approvable"] is False
    assert any("truncated" in reason for reason in payload["graph_evidence"]["reasons"])
    assert payload["status"] == "incomplete"
    assert all(node["confidence"] != "sound" for node in payload["nodes"])
    assert all(edge["confidence"] == "unknown" for edge in payload["edges"])
    assert "NOT APPROVAL-GRADE" in graphview.to_ascii(payload)
    assert "NOT APPROVAL-GRADE" in graphview.to_mermaid(payload)
    assert "NOT APPROVAL-GRADE" in graphview.to_dot(payload)


def test_cli_graph_uses_source_checked_live_payload(tmp_path, monkeypatch):
    _write_docstring_only_source(tmp_path)
    monkeypatch.setattr(cli.entire, "available", lambda: True)
    monkeypatch.setattr(cli.entire, "graph_available", lambda: True)
    monkeypatch.setattr(cli.entire, "impact_json", lambda *_args, **_kwargs: _docstring_only_impact())

    result = CliRunner().invoke(
        cli.app,
        ["graph", "sample.py#target", "--json", "--repo", str(tmp_path)],
    )

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["edges"] == []
    assert payload["graph_evidence"]["approvable"] is False
    assert payload["source_contradictions"][0]["endpoint"] == "helper"


def test_legacy_raw_graph_commands_are_retired(tmp_path):
    runner = CliRunner()

    brief = runner.invoke(cli.app, ["brief", "--symbol", "target", "--json", "--repo", str(tmp_path)])
    legacy_plan = runner.invoke(cli.app, ["plan", "--symbol", "target", "--json", "--repo", str(tmp_path)])

    assert brief.exit_code == 2
    assert json.loads(brief.stdout)["error"].startswith("amu brief is retired")
    assert legacy_plan.exit_code == 2
    assert json.loads(legacy_plan.stdout)["error"].startswith("amu plan --symbol is retired")


def test_neighbors_filters_a_docstring_only_direct_edge_and_never_claims_approval(tmp_path, monkeypatch):
    (tmp_path / "target.py").write_text("def target():\n    return 0\n")
    (tmp_path / "consumer.py").write_text(
        "def consumer():\n"
        '    """Example only: target()."""\n'
        "    return 0\n"
    )
    raw = {
        "matches": [
            {
                "symbol": {
                    "name": "target",
                    "qualified_name": "target",
                    "file_path": "target.py",
                    "start_line": 1,
                    "end_line": 2,
                },
                "incoming": [
                    {
                        "endpoint": {
                            "name": "consumer",
                            "qualified_name": "consumer",
                            "file_path": "consumer.py",
                            "start_line": 1,
                            "end_line": 3,
                        },
                        "relation": "CALLS",
                        "depth": 1,
                    }
                ],
            }
        ]
    }
    monkeypatch.setattr(cli.entire, "available", lambda: True)
    monkeypatch.setattr(cli.entire, "graph_available", lambda: True)
    monkeypatch.setattr(cli.entire, "neighbors_json", lambda *_args, **_kwargs: raw)

    result = CliRunner().invoke(cli.app, ["neighbors", "target", "--json", "--repo", str(tmp_path)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["matches"][0]["incoming"] == []
    assert payload["status"] == "candidate"
    assert payload["graph_evidence"]["approvable"] is False
    assert payload["source_contradictions"][0]["endpoint"] == "consumer"
