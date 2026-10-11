from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from amu import cli, docgraph, docs, state


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "api.md").write_text(
        "# API\n\n"
        "## Usage\n\n"
        "`pkg/api.py#public` currently calls `pkg/helpers.py#old_helper`.\n\n"
        "See [details](details.md#details).\n"
    )
    (tmp_path / "docs" / "details.md").write_text("# Details\n\nMore context.\n")
    state.write_json(
        "config.json",
        {**state.config(str(tmp_path)), "docs_dir": "docs", "docs": {"mode": "draft"}},
        str(tmp_path),
    )
    state.write_json(
        "doc-map.json",
        {"pkg/api.py#public": [{"file": "docs/api.md", "section": "## Usage"}]},
        str(tmp_path),
    )
    return tmp_path


def test_documentation_graph_maps_sections_mentions_and_links(tmp_path):
    repo = _repo(tmp_path)

    graph = docgraph.build(str(repo))

    assert graph["summary"]["files"] == 2
    assert {
        (edge["relation"], edge["source"], edge["target"])
        for edge in graph["edges"]
    } >= {
        ("DOCUMENTS", "pkg/api.py#public", "doc:docs/api.md#usage"),
        ("MENTIONS", "doc:docs/api.md#usage", "pkg/api.py#public"),
        ("MENTIONS", "doc:docs/api.md#usage", "pkg/helpers.py#old_helper"),
        ("LINKS_TO", "doc:docs/api.md#usage", "doc:docs/details.md#details"),
    }


def test_documentation_graph_understands_sphinx_doc_and_ref_links(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "index.rst").write_text(
        "Index\n=====\n\nSee :doc:`details` and :ref:`the details <details-label>`.\n"
    )
    (tmp_path / "docs" / "details.rst").write_text(
        ".. _details-label:\n\nDetails\n=======\n\nMore context.\n"
    )
    state.write_json("config.json", {**state.config(str(tmp_path)), "docs_dir": "docs"}, str(tmp_path))

    graph = docgraph.build(str(tmp_path))

    assert {
        (edge["relation"], edge["source"], edge["target"])
        for edge in graph["edges"]
    } >= {("LINKS_TO", "doc:docs/index.rst#index", "doc:docs/details.rst#details")}
    assert graph["summary"]["link_edges"] == 1


def test_documentation_graph_accepts_prd_entries_doc_map_schema(tmp_path):
    repo = _repo(tmp_path)
    graph = docgraph.build(
        str(repo),
        doc_map={
            "version": 1,
            "entries": [
                {
                    "symbol": "pkg/api.py#public",
                    "doc_file": "docs/api.md",
                    "anchor": "## Usage",
                }
            ],
        },
    )

    assert any(
        edge["relation"] == "DOCUMENTS"
        and edge["source"] == "pkg/api.py#public"
        and edge["target"] == "doc:docs/api.md#usage"
        for edge in graph["edges"]
    )


def test_relationship_comparison_flags_added_missing_and_removed_still_mentioned(tmp_path):
    repo = _repo(tmp_path)
    graph = docgraph.build(str(repo))
    refresh = {
        "target": "pkg/api.py#public",
        "complete": True,
        "edge_delta": {
            "added": [
                {
                    "target": "pkg/api.py#public",
                    "related": "pkg/helpers.py#new_helper",
                    "relation": "CALLS",
                    "direction": "out",
                    "depth": 1,
                }
            ],
            "removed": [
                {
                    "target": "pkg/api.py#public",
                    "related": "pkg/helpers.py#old_helper",
                    "relation": "CALLS",
                    "direction": "out",
                    "depth": 1,
                }
            ],
            "unchanged": 0,
        },
    }

    comparison = docgraph.compare_code_refresh(refresh, graph)

    assert comparison["status"] == "review_required"
    assert {item["doc_state"] for item in comparison["items"]} == {"missing_mention", "still_mentioned"}
    assert all(item["confidence"] == "requires_verification" for item in comparison["items"])
    assert all("Review" in item["recommendation"] for item in comparison["items"])


def test_sync_records_doc_graph_delta_after_a_doc_edit(tmp_path):
    repo = _repo(tmp_path)
    first = docgraph.sync(str(repo))
    (repo / "docs" / "api.md").write_text(
        (repo / "docs" / "api.md").read_text().replace("pkg/helpers.py#old_helper", "pkg/helpers.py#new_helper")
    )

    second = docgraph.sync(str(repo))

    saved = json.loads((repo / ".amu" / "docs-graph.json").read_text())
    assert first["graph"]["graph_hash"] != second["graph"]["graph_hash"]
    assert any(edge["target"] == "pkg/helpers.py#old_helper" for edge in second["delta"]["removed"])
    assert any(edge["target"] == "pkg/helpers.py#new_helper" for edge in second["delta"]["added"])
    assert saved == second["graph"]


def test_docs_graph_cli_persists_machine_readable_graph(tmp_path):
    repo = _repo(tmp_path)

    result = CliRunner().invoke(cli.app, ["docs-graph", "--json", "--repo", str(repo)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["graph"]["summary"]["documents_edges"] == 1
    assert payload["graph"]["summary"]["mention_edges"] >= 2
    assert (repo / ".amu" / "docs-graph.json").exists()


def test_applying_a_doc_candidate_refreshes_the_documentation_graph(tmp_path):
    repo = _repo(tmp_path)
    initial_graph = docgraph.sync(str(repo))["graph"]
    before = initial_graph["graph_hash"]
    old_usage_hash = next(
        node["content_hash"] for node in initial_graph["nodes"] if node["id"] == "doc:docs/api.md#usage"
    )
    candidate = {
        "candidate": "pkg/api.py#public",
        "class": "signature",
        "confidence": "sound",
        "old": "def public(value)",
        "new": "def public(value, *, strict=False)",
        "targets": [{"file": "docs/api.md", "section": "## Usage"}],
        "reason": "signature on a public export",
    }

    assert docs.apply(candidate, str(repo)) is True

    saved = state.read_json("docs-graph.json", str(repo))
    assert saved["graph_hash"] != before
    usage = next(node for node in saved["nodes"] if node["id"] == "doc:docs/api.md#usage")
    assert usage["content_hash"] != old_usage_hash
    assert "def public(value, *, strict=False)" in (repo / "docs" / "api.md").read_text()
