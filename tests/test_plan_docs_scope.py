"""Documentation edits need explicit pre-edit planning scope, never implicit save authority."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from amu import cli, plan, state
from amu.contract import check_contract
from tests.test_lifecycle_gates import _contract, _map


@pytest.fixture
def scoped_repo(tmp_path, monkeypatch):
    (tmp_path / "docs").mkdir()
    for path, text in {
        "docs/guide.md": "# Guide\n\nOriginal.\n",
        "docs/api.rst": "API\n===\n\nOriginal.\n",
        "docs/component.mdx": "# Component\n\n<Code />\n",
        "docs/example.py": "value = 1\n",
        "README.md": "# Outside configured docs\n",
    }.items():
        (tmp_path / path).write_text(text, encoding="utf-8")
    state.write_json("map.json", _map(), str(tmp_path))
    state.write_json("config.json", {**state.config(str(tmp_path)), "docs_dir": "docs"}, str(tmp_path))
    monkeypatch.setattr(cli.graph_refresh, "map_current_capture_reason", lambda *_args: None)
    monkeypatch.setattr(cli.state, "git_head", lambda _repo: "fixture-head")
    return tmp_path


def _invoke(repo, *args):
    return CliRunner().invoke(cli.app, ["plan", *args, "--json", "--repo", str(repo)])


def test_explicit_documentation_is_visible_in_draft_without_approval(scoped_repo):
    result = _invoke(scoped_repo, "--docs-file", "docs/guide.md", "--docs-file", "docs/api.rst")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["docs_files"] == ["docs/api.rst", "docs/guide.md"]
    assert set(payload["docs_files"]) <= set(payload["phases"][-1]["files"])
    assert all(not set(payload["docs_files"]) & set(phase["files"]) for phase in payload["phases"][:-1])
    assert "documentation" in payload["phases"][-1]["reason"]
    assert payload["contract"] is None
    assert not (scoped_repo / ".amu/contract.json").exists()


def test_explicit_docs_scope_is_approved_only_in_final_phase(scoped_repo):
    result = _invoke(scoped_repo, "--approve", "--docs-file", "docs/guide.md")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    contract = state.read_json("contract.json", str(scoped_repo))
    assert payload["docs_files"] == ["docs/guide.md"]
    assert "docs/guide.md" in contract["allowed_files"]
    assert "docs/guide.md" in contract["phases"][-1]["files"]
    assert all("docs/guide.md" not in phase["files"] for phase in contract["phases"][:-1])
    assert contract["targets"] == [{"symbol": "src.py#work_1", "change_class": "signature"}]
    assert "docs/guide.md" not in contract["frozen_signatures"]
    assert not check_contract(contract, [], ["docs/guide.md"], phase=None).violations
    assert any(row.kind.value == "PHASE_DRIFT" for row in check_contract(
        contract, [], ["docs/guide.md"], phase=1,
    ).violations)


def test_omitted_option_does_not_approve_docs_implicitly(scoped_repo):
    result = _invoke(scoped_repo, "--approve")
    assert result.exit_code == 0, result.output
    contract = state.read_json("contract.json", str(scoped_repo))
    assert json.loads(result.stdout)["docs_files"] == []
    assert "docs/guide.md" not in contract["allowed_files"]
    assert any(row.kind.value == "OUT_OF_SCOPE" for row in check_contract(
        contract, [], ["docs/guide.md"], phase=None,
    ).violations)


def test_duplicate_lexical_spellings_become_one_canonical_docs_path(scoped_repo):
    result = _invoke(scoped_repo, "--docs-file", "docs//./guide.md", "--docs-file", "docs/guide.md", "--approve")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["docs_files"] == ["docs/guide.md"]
    contract = state.read_json("contract.json", str(scoped_repo))
    assert contract["allowed_files"].count("docs/guide.md") == 1
    assert contract["phases"][-1]["files"].count("docs/guide.md") == 1


@pytest.mark.parametrize("path", ["", "   ", "../docs/guide.md", "docs/../docs/guide.md",
                                  "docs/missing.md", "README.md", "docs/component.mdx", "docs/example.py", "docs"])
def test_invalid_docs_scope_is_json_error_and_preserves_existing_contract(scoped_repo, path):
    original = (json.dumps(_contract(), sort_keys=True) + "\n").encode()
    target = scoped_repo / ".amu/contract.json"
    target.write_bytes(original)

    result = _invoke(scoped_repo, "--docs-file", path, "--approve")

    assert result.exit_code == 2
    assert "docs" in json.loads(result.stdout)["error"].lower()
    assert target.read_bytes() == original


def test_absolute_documentation_path_is_rejected(scoped_repo):
    result = _invoke(scoped_repo, "--docs-file", str(scoped_repo / "docs/guide.md"), "--approve")
    assert result.exit_code == 2
    assert "docs" in json.loads(result.stdout)["error"].lower()
    assert not (scoped_repo / ".amu/contract.json").exists()


def test_external_doc_symlink_is_rejected(scoped_repo, tmp_path):
    external = tmp_path.parent / "external-scope.md"
    external.write_text("# Must not be scoped\n", encoding="utf-8")
    (scoped_repo / "docs/link.md").symlink_to(external)
    result = _invoke(scoped_repo, "--docs-file", "docs/link.md", "--approve")
    assert result.exit_code == 2
    assert "docs" in json.loads(result.stdout)["error"].lower()
    assert not (scoped_repo / ".amu/contract.json").exists()


def test_documentation_list_is_bounded_before_deduplication(scoped_repo):
    args = [part for _ in range(101) for part in ("--docs-file", "docs/guide.md")]
    result = _invoke(scoped_repo, *args, "--approve")
    assert result.exit_code == 2
    assert "100" in json.loads(result.stdout)["error"]


def test_one_hundred_explicit_entries_can_be_canonicalized(scoped_repo):
    args = [part for _ in range(100) for part in ("--docs-file", "docs/guide.md")]
    result = _invoke(scoped_repo, *args)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["docs_files"] == ["docs/guide.md"]


def test_scope_uses_configured_documentation_directory(scoped_repo):
    (scoped_repo / "manual").mkdir()
    (scoped_repo / "manual/guide.rst").write_text("Guide\n=====\n", encoding="utf-8")
    state.write_json("config.json", {**state.config(str(scoped_repo)), "docs_dir": "manual"}, str(scoped_repo))

    accepted = _invoke(scoped_repo, "--docs-file", "manual/guide.rst")
    assert accepted.exit_code == 0, accepted.output
    assert json.loads(accepted.stdout)["docs_files"] == ["manual/guide.rst"]
    rejected = _invoke(scoped_repo, "--docs-file", "docs/guide.md")
    assert rejected.exit_code == 2


@pytest.mark.parametrize("requested", ["docs/guide.md", 1, True, [True], [1], [None], [""]])
def test_core_docs_scope_does_not_coerce_input_types(scoped_repo, requested):
    with pytest.raises(ValueError, match="docs"):
        plan.documentation_scope(str(scoped_repo), requested)


@pytest.mark.parametrize("mutation", ["missing", "external_symlink", "internal_symlink"])
def test_docs_paths_are_revalidated_after_planning_before_contract_write(scoped_repo, monkeypatch, mutation):
    previous = (json.dumps(_contract(), sort_keys=True) + "\n").encode()
    (scoped_repo / ".amu/contract.json").write_bytes(previous)
    original_problem = cli._map_approval_problem
    called = 0

    def change_during_approval(repo, amu_map):
        nonlocal called
        called += 1
        result = original_problem(repo, amu_map)
        if called == 2:
            path = scoped_repo / "docs/guide.md"
            path.unlink()
            if mutation == "external_symlink":
                outside = scoped_repo.parent / "later-external.md"
                outside.write_text("# Outside\n", encoding="utf-8")
                path.symlink_to(outside)
            elif mutation == "internal_symlink":
                path.symlink_to(scoped_repo / "docs/api.rst")
        return result

    monkeypatch.setattr(cli, "_map_approval_problem", change_during_approval)

    result = _invoke(scoped_repo, "--docs-file", "docs/guide.md", "--approve")

    assert result.exit_code == 2, result.output
    assert called == 2
    assert "docs" in json.loads(result.stdout).get("approval_refused", json.loads(result.stdout).get("error", "")).lower()
    assert (scoped_repo / ".amu/contract.json").read_bytes() == previous


def test_explicit_docs_scope_does_not_bypass_stale_map_guard(scoped_repo, monkeypatch):
    monkeypatch.setattr(cli.graph_refresh, "map_current_capture_reason", lambda *_args: "mapped worktree is stale")
    result = _invoke(scoped_repo, "--docs-file", "docs/guide.md", "--approve")
    assert result.exit_code == 2
    assert "stale" in json.loads(result.stdout)["approval_refused"]
    assert not (scoped_repo / ".amu/contract.json").exists()
