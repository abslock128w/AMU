"""User flow: detect → prepare → edit → save → refreshed documentation graph."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from amu import cli, docgraph, docreview, state
from tests.test_docs import _committed_repo, _sound_signature_diff, _write_doc_map


@pytest.mark.parametrize("suffix,heading,next_heading", [
    ("md", "## public", "## Other"),
    ("rst", "public\n======", "Other\n====="),
])
def test_cli_review_replaces_only_selected_section_and_refreshes_graph(
    fake_entire, monkeypatch, tmp_path, suffix, heading, next_heading,
):
    filename = f"docs/api.{suffix}"
    repo = _committed_repo(tmp_path, {
        "api.py": "def public(value):\n    return value\n",
        filename: f"{heading}\n\nOld usage: def public(value).\n\n{next_heading}\n\nKeep this.\n",
    })
    _write_doc_map(repo, {"api.py#public": [{"file": filename, "section": heading.splitlines()[0]}]})
    monkeypatch.setattr(cli.entire, "diff_json", lambda *_a, **_k: _sound_signature_diff())
    runner = CliRunner()

    def run(*args):
        result = runner.invoke(cli.app, ["docs", *args, "--json", "--repo", str(repo)])
        assert result.exit_code == 0, result.stdout
        return json.loads(result.stdout)

    run("--mode", "draft")
    original = (repo / filename).read_text()
    prepared = run("--prepare", "d1")
    draft = Path(prepared["prepared"]["file"])
    assert "def public(value, *, strict=False)" in draft.read_text()
    assert (repo / filename).read_text() == original
    # Re-preparation must not destroy a developer's draft.
    assert run("--prepare", "d1")["not_applied"]
    draft.write_text("Use public(value, strict=True) to enable strict checks.\nSee `api.py#helper`.\n")
    applied = run("--apply", "d1", "--review")
    assert applied["applied"] == ["d1"]
    result = (repo / filename).read_text()
    assert "Old usage" not in result
    assert f"{next_heading}\n\nKeep this." in result
    assert "Use public(value, strict=True)" in result
    graph = state.read_json("docs-graph.json", str(repo))
    assert any(edge["target"] == "api.py#helper" for edge in graph["edges"])
    assert run("--apply", "d1", "--review")["not_applied"]


def test_review_refuses_document_edited_after_preparation(tmp_path):
    (tmp_path / "docs").mkdir()
    path = tmp_path / "docs/api.md"
    path.write_text("# API\n\nOriginal.\n")
    candidate = {"id": "d1", "candidate": "api.py#public", "class": "body", "confidence": "unknown",
                 "targets": [{"file": "docs/api.md", "section": "# API"}]}
    prepared = docreview.prepare(candidate, str(tmp_path))
    Path(prepared["file"]).write_text("New docs.\n")
    path.write_text("# API\n\nSomeone else's edit.\n")
    with pytest.raises(ValueError, match="document changed"):
        docreview.apply(candidate, str(tmp_path))
    assert "Someone else's edit." in path.read_text()


def test_manual_body_change_review_does_not_claim_known_behavior(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/api.md").write_text("# API\n\nOriginal.\n")
    candidate = {"id": "d1", "candidate": "api.py#public", "class": "body", "confidence": "unknown",
                 "targets": [{"file": "docs/api.md", "section": "# API"}]}
    prepared = docreview.prepare(candidate, str(tmp_path))
    assert "unknown" in prepared["suggestion"]
    with pytest.raises(ValueError, match="unchanged"):
        docreview.apply(candidate, str(tmp_path))
    Path(prepared["file"]).write_text("Reviewed behavior now calls `api.py#helper`.\n")
    assert docreview.apply(candidate, str(tmp_path))
    assert docgraph.build(str(tmp_path))["summary"]["mention_edges"] == 1
