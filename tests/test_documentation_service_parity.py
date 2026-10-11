"""Real repository workflows shared by the service and its public adapters.

Only graph-provider evidence is recorded here: document parsing, draft editing,
optimistic saves, recovery journals and documentation graph updates are real.
MCP's bounded process boundary invokes the real CLI parser in-process, allowing
the same deterministic evidence to exercise its public protocol wrappers.
"""

from __future__ import annotations

import contextlib
import io
import json
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from amu import cli, docgraph, entire, mcp_server, state

ADAPTERS = ["service", "cli", "repl", "mcp"]


def _diff(*names: str) -> dict:
    return {"files": [{"path": "api.py", "changes": [
        {"type": "signature_changed", "kind": "function", "name": name,
         "old_signature": f"def {name}(value)",
         "new_signature": f"def {name}(value, *, strict=False)"}
        for name in names
    ]}]}


def _repo(tmp_path: Path, *, multiple: bool = False) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docs").mkdir()
    (repo / "api.py").write_text(
        "def public(value):\n    return value\n\ndef second(value):\n    return value\n",
        encoding="utf-8",
    )
    for filename in ("a", "b"):
        (repo / f"docs/{filename}.md").write_text(
            f"# {filename.upper()}\n\n## public\nOld usage: def public(value).\n\n"
            "## Other\n\nKeep this section.\n", encoding="utf-8",
        )
    for args in (["init", "-q"], ["config", "user.email", "parity@example.invalid"],
                 ["config", "user.name", "Parity test"], ["add", "."],
                 ["commit", "-qm", "baseline"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    targets = [{"file": "docs/a.md", "section": "## public"}]
    if multiple:
        targets.append({"file": "docs/b.md", "section": "## public"})
    state.write_json("doc-map.json", {"api.py#public": targets}, str(repo))
    return repo


class _Adapter:
    def __init__(self, kind: str, repo: Path, monkeypatch):
        self.kind, self.repo = kind, str(repo)
        if kind == "mcp":
            def run(command, *, cwd, timeout, stdout_limit, stderr_limit):
                assert command[:4] == [mcp_server.sys.executable, "-I", "-m", "amu.cli"]
                assert cwd == self.repo and timeout == 600
                assert stdout_limit == mcp_server.CLI_STDOUT_LIMIT
                assert stderr_limit == mcp_server.CLI_STDERR_LIMIT
                result = CliRunner().invoke(cli.app, command[4:])
                return SimpleNamespace(stdout=result.stdout.encode(), stderr=result.stderr.encode(),
                                       returncode=result.exit_code, error=None)
            monkeypatch.setattr(mcp_server, "_cli_process", run)

    def call(self, operation="docs", **options):
        if self.kind == "service":
            from amu.services import documentation as service

            try:
                if operation == "docs":
                    payload = service.execute(self.repo, service.DocsRequest(**options))
                elif operation == "docs-reviews":
                    payload = service.review_queue(self.repo, **options)
                elif operation == "docs-saves":
                    payload = service.saves(self.repo, **options)
                else:
                    payload = service.graph(self.repo)
                return 0, payload
            except service.DocumentationError as exc:
                return exc.exit_code, exc.payload
        if self.kind == "mcp":
            if operation == "docs":
                if options.get("prepare"):
                    value = mcp_server.amu_docs_prepare(
                        options["prepare"], target_index=options.get("target_index"), repo=self.repo)
                elif options.get("apply"):
                    value = mcp_server.amu_docs_apply_review(
                        options["apply"], confirm=True,
                        target_index=options.get("target_index"), repo=self.repo)
                else:
                    value = mcp_server.amu_docs(repo=self.repo)
            elif operation == "docs-reviews":
                if options.get("discard"):
                    value = mcp_server.amu_docs_discard(options["discard"], confirm=True, repo=self.repo)
                else:
                    value = mcp_server.amu_docs_reviews(preview=options.get("preview"), repo=self.repo)
            elif operation == "docs-saves":
                value = mcp_server.amu_docs_saves(undo=options.get("undo"), confirm=True, repo=self.repo)
            else:
                value = mcp_server.amu_docs_graph(repo=self.repo)
            payload = json.loads(value)
            return payload.get("exit", 0), payload
        arguments = []
        for name, value in options.items():
            if value is None or value is False:
                continue
            arguments.append("--" + name.replace("_", "-"))
            if value is not True:
                arguments.append(str(value))
        if self.kind == "cli":
            result = CliRunner().invoke(cli.app, [operation, *arguments, "--repo", self.repo, "--json"])
            return result.exit_code, json.loads(result.stdout)
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            cli._repl_cli_command(operation, shlex.join([*arguments, "--json"]), self.repo)
        payload = json.loads(output.getvalue())
        # The interactive parser deliberately consumes SystemExit; its error
        # payload remains visible and the following command still runs.
        return payload.get("exit", 2 if "error" in payload else 0), payload


@pytest.fixture
def evidence(monkeypatch):
    monkeypatch.setattr(state, "now_iso", lambda: "2026-10-09T00:00:00Z")
    monkeypatch.setattr(entire, "available", lambda: True)
    monkeypatch.setattr(entire, "diff_json", lambda *_a, **_k: _diff("public"))


def test_all_adapters_return_the_same_stable_listing_payload(tmp_path, monkeypatch, evidence):
    repo = _repo(tmp_path)
    # Warm the graph so first-generation delta does not make equivalent reads
    # appear different. Timestamp generation is frozen; ids are source-bound.
    service = _Adapter("service", repo, monkeypatch)
    service.call(mode="flag")
    code, expected = service.call(mode="flag")
    assert code == 0
    for kind in ["cli", "repl", "mcp"]:
        actual_code, actual = _Adapter(kind, repo, monkeypatch).call(mode="flag")
        assert actual_code == code and actual == expected


@pytest.mark.parametrize("kind", ["service", "cli", "repl"])
def test_auto_mode_keeps_worktree_shadowed_candidates_manual(kind, tmp_path, monkeypatch, evidence):
    repo = _repo(tmp_path)
    adapter = _Adapter(kind, repo, monkeypatch)
    original = (repo / "docs/a.md").read_text()
    (repo / "api.py").write_text(
        "def public(value, *, strict=False):\n    return value\n", encoding="utf-8")
    code, result = adapter.call(mode="auto")
    assert code == 0 and result["applied"] == [] and result["saves"] == []
    assert result["worktree_blocks"][0]["candidate"] == "api.py#public"
    assert result["not_applied"][0]["blocked_by_worktree"] == "api.py#public"
    assert "shadowed candidates" in result["next"]
    assert (repo / "docs/a.md").read_text() == original


@pytest.mark.parametrize("kind", ADAPTERS)
def test_list_prepare_edit_save_graph_history_and_undo_are_consistent(kind, tmp_path, monkeypatch, evidence):
    repo = _repo(tmp_path)
    adapter = _Adapter(kind, repo, monkeypatch)
    original = (repo / "docs/a.md").read_text()

    code, listing = adapter.call(mode="flag")
    assert code == 0 and listing["applied"] == []
    assert listing["candidates"][0]["candidate"] == "api.py#public"
    assert listing["candidates"][0]["id"] == "d1"
    assert (repo / "docs/a.md").read_text() == original

    code, prepared = adapter.call(mode="draft", prepare="d1")
    assert code == 0 and prepared["prepared"]
    draft = Path(prepared["prepared"]["file"])
    review_id = prepared["prepared"]["review_id"]
    assert "def public(value, *, strict=False)" in draft.read_text()
    assert (repo / "docs/a.md").read_text() == original
    draft.write_text("Use public(value, strict=True). See `api.py#helper`.\n", encoding="utf-8")
    code, preview = adapter.call("docs-reviews", preview=review_id)
    assert code == 0 and preview["status"] == "edited"
    assert "+Use public" in preview["diff"]
    assert (repo / "docs/a.md").read_text() == original

    code, applied = adapter.call(mode="draft", apply="d1", review=True)
    assert code == 0 and applied["applied"] == ["d1"]
    assert applied["saves"][0]["saved"] is True
    assert applied["saves"][0]["graph_refreshed"] is True
    assert applied["docs_graph"]["status"] == "refreshed"
    saved_text = (repo / "docs/a.md").read_text()
    assert "Old usage" not in saved_text and "## Other\n\nKeep this section." in saved_text
    graph = state.read_json("docs-graph.json", str(repo))
    assert any(edge["target"] == "api.py#helper" for edge in graph["edges"])
    assert adapter.call("docs-reviews", preview=review_id)[1]["status"] == "saved"
    code, history = adapter.call("docs-saves")
    assert code == 0 and history["saves"][0]["status"] == "matches_saved"
    code, refreshed = adapter.call("docs-graph")
    assert code == 0 and refreshed["graph"]["graph_hash"] == graph["graph_hash"]
    save_id = applied["saves"][0]["save_id"]
    code, undone = adapter.call("docs-saves", undo=save_id)
    assert code == 0 and undone["saved"] is True and undone["undo_of"] == save_id
    assert (repo / "docs/a.md").read_text() == original


@pytest.mark.parametrize("kind", ADAPTERS)
def test_multiple_targets_require_selection_and_only_selected_document_changes(kind, tmp_path, monkeypatch, evidence):
    repo = _repo(tmp_path, multiple=True)
    adapter = _Adapter(kind, repo, monkeypatch)
    original_a = (repo / "docs/a.md").read_text()
    adapter.call(mode="flag")
    code, unselected = adapter.call(prepare="d1")
    assert code == 0 and "choose one mapped section" in unselected["not_applied"][0]["why"]
    assert not list((repo / ".amu/doc-reviews").glob("*.md"))
    code, invalid = adapter.call(prepare="d1", target_index=3)
    assert code == 0 and "target-index" in invalid["not_applied"][0]["why"]
    prepared = adapter.call(prepare="d1", target_index=2)[1]["prepared"]
    Path(prepared["file"]).write_text("Reviewed B usage.\n", encoding="utf-8")
    code, saved = adapter.call(apply="d1", review=True, target_index=2)
    assert code == 0 and saved["applied"] == ["d1"]
    assert (repo / "docs/a.md").read_text() == original_a
    assert "Reviewed B usage." in (repo / "docs/b.md").read_text()


@pytest.mark.parametrize("kind", ADAPTERS)
def test_stale_ids_and_document_conflicts_never_overwrite_newer_work(kind, tmp_path, monkeypatch, evidence):
    repo = _repo(tmp_path)
    adapter = _Adapter(kind, repo, monkeypatch)
    adapter.call(mode="flag")
    prepared = adapter.call(prepare="d1")[1]["prepared"]
    Path(prepared["file"]).write_text("My intended edit.\n", encoding="utf-8")
    current = "# A\n\n## public\nSomeone else's edit.\n"
    (repo / "docs/a.md").write_text(current, encoding="utf-8")
    assert adapter.call("docs-reviews", preview=prepared["review_id"])[1]["status"] == "conflict"
    code, stale_worktree = adapter.call(apply="d1", review=True)
    assert code == 0 and stale_worktree["applied"] == []
    assert "worktree changed" in stale_worktree["not_applied"][0]["why"]
    # Renewing the candidate listing is not permission to overwrite a document
    # changed since draft preparation: its independent optimistic guard remains.
    adapter.call(mode="flag")
    code, conflict = adapter.call(apply="d1", review=True)
    assert code == 0 and conflict["applied"] == []
    assert "document changed" in conflict["not_applied"][0]["why"]
    monkeypatch.setattr(entire, "diff_json", lambda *_a, **_k: _diff("second", "public"))
    code, stale = adapter.call(apply="d1", review=True)
    assert code == 0 and stale["applied"] == []
    assert "stale or reordered" in stale["not_applied"][0]["why"]
    assert (repo / "docs/a.md").read_text() == current
    assert adapter.call("docs-saves")[1]["saves"] == []


@pytest.mark.parametrize("kind", ADAPTERS)
def test_discard_preserves_edited_prose_and_does_not_need_a_graph(kind, tmp_path, monkeypatch, evidence):
    repo = _repo(tmp_path)
    adapter = _Adapter(kind, repo, monkeypatch)
    original = (repo / "docs/a.md").read_text()
    adapter.call(mode="flag")
    prepared = adapter.call(prepare="d1")[1]["prepared"]
    draft = Path(prepared["file"])
    draft.write_text("Keep this developer prose.\n", encoding="utf-8")
    monkeypatch.setattr(entire, "available", lambda: False)
    assert adapter.call("docs-reviews")[1]["reviews"][0]["status"] == "edited"
    code, discarded = adapter.call("docs-reviews", discard=prepared["review_id"])
    assert code == 0 and discarded["status"] == "discarded"
    archived = Path(discarded["archive"]) / draft.name
    assert archived.read_text() == "Keep this developer prose.\n"
    assert adapter.call("docs-reviews")[1]["reviews"] == []
    assert (repo / "docs/a.md").read_text() == original


@pytest.mark.parametrize("kind", ["service", "cli", "repl"])
@pytest.mark.parametrize("options,message", [
    ({"mode": "invented"}, "invalid docs mode"),
    ({"prepare": "d1", "apply": "d1"}, "use --prepare"),
    ({"review": True}, "use --prepare"),
    ({"target_index": 1}, "--target-index requires"),
])
def test_invalid_requests_fail_before_graph_or_document_work(kind, options, message, tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    adapter = _Adapter(kind, repo, monkeypatch)
    monkeypatch.setattr(entire, "available", lambda: pytest.fail("validation must happen before graph checks"))
    monkeypatch.setattr(docgraph, "sync", lambda *_a, **_k: pytest.fail("invalid request must not refresh graphs"))
    code, failed = adapter.call(**options)
    assert code == 2 and failed["exit"] == 2 and message in failed["error"]
    assert not (repo / ".amu/docs-candidates.json").exists()
    assert not (repo / ".amu/doc-reviews").exists()


@pytest.mark.parametrize("kind", ADAPTERS)
def test_graph_transport_limit_does_not_become_an_empty_listing(kind, tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    adapter = _Adapter(kind, repo, monkeypatch)
    monkeypatch.setattr(entire, "available", lambda: True)
    monkeypatch.setattr(entire, "_run", lambda *_a, **_k: (
        125, "", "entire graph diff exceeded an output byte limit; no partial output was accepted"))
    original = (repo / "docs/a.md").read_text()
    code, failed = adapter.call(mode="flag")
    assert code == 3 and failed["exit"] == 3
    assert "cannot determine documentation candidates" in failed["error"]
    assert "candidates" not in failed and "install" in failed
    assert (repo / "docs/a.md").read_text() == original
    assert not (repo / ".amu/docs-graph.json").exists()


@pytest.mark.parametrize("kind", ADAPTERS)
def test_graph_failure_after_document_write_reports_saved_and_recoverable(kind, tmp_path, monkeypatch, evidence):
    repo = _repo(tmp_path)
    adapter = _Adapter(kind, repo, monkeypatch)
    adapter.call(mode="flag")
    prepared = adapter.call(prepare="d1")[1]["prepared"]
    Path(prepared["file"]).write_text("Saved even if graph refresh fails.\n", encoding="utf-8")
    real_sync = docgraph.sync
    calls = 0

    def fail_only_after_write(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("graph refresh intentionally unavailable")
        return real_sync(*args, **kwargs)

    monkeypatch.setattr(docgraph, "sync", fail_only_after_write)
    code, result = adapter.call(apply="d1", review=True)
    assert code == 0 and result["applied"] == ["d1"]
    assert result["saves"][0]["saved"] is True
    assert result["saves"][0]["graph_refreshed"] is False
    assert "intentionally unavailable" in result["saves"][0]["graph_error"]
    assert result["docs_graph"]["status"] == "refresh_pending"
    assert result["relationship_comparisons"] == []
    assert "document saved" in result["next"]
    assert "Saved even if" in (repo / "docs/a.md").read_text()
    history = adapter.call("docs-saves")[1]["saves"]
    assert history[0]["status"] == "matches_saved" and history[0]["graph_refreshed"] is False


def test_mcp_confirmation_guards_never_enter_the_shared_service(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(mcp_server.process, "run", lambda *_a, **_k: pytest.fail("approval must precede all work"))
    operations = (
        lambda: mcp_server.amu_docs_apply_review("d1", repo=str(repo)),
        lambda: mcp_server.amu_docs_discard("a" * 16, repo=str(repo)),
        lambda: mcp_server.amu_docs_saves(undo="b" * 32, repo=str(repo)),
    )
    for operation in operations:
        failed = json.loads(operation())
        assert failed["exit"] == 2 and "explicit user approval" in failed["error"]
