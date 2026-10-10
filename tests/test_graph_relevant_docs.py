"""Source and build inputs stay covered even when stored in docs_dir."""

import pytest

from amu import cli


@pytest.mark.parametrize("path", [
    "docs/example.py", "docs/conf.py", "docs/example.js", "docs/settings.json",
    "docs/example.ipynb", "docs/settings.yaml", "docs/components.mdx",
    "src/components.mdx", "uv.lock", "docs/dependencies.lock", "requirements.txt",
    "requirements-dev.txt", "requirements/docs.txt", "constraints.txt", "CMakeLists.txt",
])
def test_source_like_docs_and_dependency_inputs_are_graph_relevant(monkeypatch, path):
    monkeypatch.setattr(cli.state, "config", lambda _repo: {"docs_dir": "docs"})
    assert cli._is_graph_relevant_change("repo", path)


@pytest.mark.parametrize("path", [
    "docs/guide.md", "docs/guide.rst", "README.md", "docs/notes.txt", "docs/image.png",
    "docs/image.svg", "docs/manual.pdf",
])
def test_plain_prose_and_assets_remain_exempt(monkeypatch, path):
    monkeypatch.setattr(cli.state, "config", lambda _repo: {"docs_dir": "docs"})
    assert not cli._is_graph_relevant_change("repo", path)


@pytest.mark.parametrize("path", [
    "docs/example.py", "docs/conf.py", "docs/example.js", "docs/settings.json",
    "docs/example.ipynb", "docs/components.mdx", "uv.lock", "requirements.txt",
])
def test_refresh_gate_requires_explicit_coverage_for_source_like_documentation(monkeypatch, path):
    target = "pkg/target.py#value"
    contract = {"targets": [{"symbol": target, "change_class": "body"}], "map_hash": "map"}
    refresh = {"targets": {target: {
        "complete": True, "change": "body", "baseline": {"map_hash": "map", "depth": 2},
        "sync": {"sha": "head"}, "worktree": {"digest": "current"},
    }}}

    def read(name, _repo, default=None):
        return {"refresh-state.json": refresh, "map.json": {"depth": 2}}.get(name, default)

    monkeypatch.setattr(cli.state, "read_json", read)
    monkeypatch.setattr(cli.state, "config", lambda _repo: {"docs_dir": "docs"})
    monkeypatch.setattr(cli.state, "git_head", lambda _repo: "head")
    monkeypatch.setattr(cli.state, "worktree_fingerprint",
                        lambda _repo: {"digest": "current", "files": [], "error": None})
    violations = cli._refresh_gate_violations("repo", contract, {"pkg/target.py", path})
    assert len(violations) == 1
    assert violations[0].kind.value == "GRAPH_REFRESH_REQUIRED"
    assert violations[0].symbol == path
    assert "outside the exact mapped targets" in violations[0].detail


def test_doc_only_changes_do_not_request_a_code_refresh(monkeypatch):
    monkeypatch.setattr(cli.state, "config", lambda _repo: {"docs_dir": "docs"})
    contract = {"targets": [{"symbol": "pkg/target.py#value", "change_class": "body"}], "map_hash": "map"}
    assert cli._refresh_gate_violations("repo", contract, {"docs/guide.md", "docs/guide.rst"}) == []
