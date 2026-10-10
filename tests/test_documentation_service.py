import ast
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from amu import state
from amu.services import documentation as service


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    """Keep application decisions real, with graph/filesystem effects observable."""
    events = []
    candidate = {"id": "d1", "candidate": "api.py#public", "class": "signature", "confidence": "sound",
                 "targets": [{"file": "docs/api.md", "section": "# API"}], "reason": "mapped export"}
    graph = {"summary": {"files": 1}, "graph_hash": "current-graph", "revisions": {"docs": "current-docs"}}
    config = {"docs": {"mode": "draft"}, "docs_dir": "docs"}

    def read(name, repo, default=None):
        events.append(("read", name))
        return {"contract.json": {"base_sha": "baseline"}, "docs-graph.json": graph,
                "docs-graph-state.json": {"delta": {"added": 1}}}.get(name, default)

    def sync(repo, docs_dir="docs"):
        events.append(("sync", repo, docs_dir))
        return {"graph": graph, "delta": {"added": 1}}

    monkeypatch.setattr(service.state, "config", lambda repo: config)
    monkeypatch.setattr(service.state, "read_json", read)
    monkeypatch.setattr(service.entire, "available", lambda: True)
    monkeypatch.setattr(service.entire, "diff_json", lambda *args: events.append(("diff", *args)) or {"files": []})
    monkeypatch.setattr(service.docsmod, "doc_map", lambda repo: {})
    monkeypatch.setattr(service.docsmod, "all_candidates", lambda *args: [candidate])
    monkeypatch.setattr(service.docsmod, "packet", lambda row, *args: {**row, "draft": "SUGGESTED usage"})
    monkeypatch.setattr(service.docsmod, "issue_candidate_manifest", lambda repo, rows: events.append(("issue", rows)))
    monkeypatch.setattr(service.docsmod, "issued_candidate", lambda repo, rows, ident: (rows[0], None))
    monkeypatch.setattr(service.docgraph, "sync", sync)
    monkeypatch.setattr(service.docgraph, "comparisons", lambda *args: [{"status": "review_required"}])
    monkeypatch.setattr(service.docsmod, "worktree_auto_block", lambda *args: None)
    return {"repo": str(tmp_path), "events": events, "candidate": candidate, "config": config, "graph": graph}


def test_request_is_immutable():
    request = service.DocsRequest(mode="draft")
    with pytest.raises(FrozenInstanceError):
        request.mode = "auto"


@pytest.mark.parametrize("docs_request", [
    {}, None, service.DocsRequest(mode=[]), service.DocsRequest(mode=""), service.DocsRequest(mode="invalid"),
    service.DocsRequest(apply=False), service.DocsRequest(apply=""), service.DocsRequest(prepare=[]),
    service.DocsRequest(prepare=" "), service.DocsRequest(review=1), service.DocsRequest(review=True),
    service.DocsRequest(prepare="d1", apply="d1"), service.DocsRequest(target_index=1),
    service.DocsRequest(apply="d1", target_index=1), service.DocsRequest(prepare="d1", target_index=True),
    service.DocsRequest(prepare="d1", target_index=0), service.DocsRequest(prepare="d1", target_index=-1),
    service.DocsRequest(prepare="d1", target_index=1.0), service.DocsRequest(prepare="d1", target_index=float("nan")),
    service.DocsRequest(prepare="d1", target_index=float("inf")), service.DocsRequest(prepare="d1", target_index="1"),
])
def test_invalid_request_is_rejected_before_any_state_access(tmp_path, monkeypatch, docs_request):
    calls = []
    monkeypatch.setattr(service.state, "config", lambda repo: calls.append(repo))
    with pytest.raises(service.DocumentationError) as caught:
        service.execute(str(tmp_path), docs_request)
    assert caught.value.exit_code == 2 and caught.value.payload["exit"] == 2
    assert not calls and not (tmp_path / ".amu").exists()


@pytest.mark.parametrize("repo", [None, False, "", " ", ".", "relative/path"])
@pytest.mark.parametrize("operation", ["execute", "review_queue", "saves", "graph"])
def test_repository_selection_is_explicit_before_state_access(monkeypatch, repo, operation):
    calls = []
    monkeypatch.setattr(service.state, "config", lambda repo: calls.append(repo))
    with pytest.raises(service.DocumentationError, match="explicit absolute"):
        getattr(service, operation)(repo, service.DocsRequest()) if operation == "execute" else getattr(service, operation)(repo)
    assert not calls


@pytest.mark.parametrize("operation", ["execute", "review_queue", "saves", "graph"])
def test_missing_or_non_directory_repository_is_rejected(tmp_path, operation):
    file = tmp_path / "plain-file"
    file.write_text("not a repo")
    for path in (file, tmp_path / "missing"):
        with pytest.raises(service.DocumentationError, match="existing directory"):
            getattr(service, operation)(str(path), service.DocsRequest()) if operation == "execute" else getattr(service, operation)(str(path))
    assert not (tmp_path / ".amu").exists()


def test_repository_alias_is_canonicalized(workflow, tmp_path):
    alias = tmp_path / "repo-alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    service.execute(str(alias), service.DocsRequest())
    assert ("diff", str(tmp_path), "baseline", "HEAD") in workflow["events"]
    assert ("sync", str(tmp_path), "docs") in workflow["events"]


def test_list_payload_and_no_console_output(workflow, capsys):
    result = service.execute(workflow["repo"], service.DocsRequest())
    assert result == {
        "mode": "draft", "prepared": None,
        "candidates": [{**workflow["candidate"], "draft": "SUGGESTED usage"}],
        "applied": [], "saves": [], "not_applied": [], "worktree_blocks": [],
        "docs_graph": {"status": "refreshed", "summary": {"files": 1}, "graph_hash": "current-graph",
                       "revisions": {"docs": "current-docs"}, "delta": {"added": 1}},
        "relationship_comparisons": [{"status": "review_required"}], "commit_hint": None,
        "next": "review SUGGESTED drafts; nothing here claims the docs are up to date",
    }
    assert ("issue", [workflow["candidate"]]) in workflow["events"]
    assert capsys.readouterr() == ("", "")


def test_flag_mode_keeps_raw_candidates(workflow):
    result = service.execute(workflow["repo"], service.DocsRequest(mode="flag"))
    assert result["candidates"] == [workflow["candidate"]]
    assert "draft" not in result["candidates"][0]


def test_default_mode_uses_repository_configuration(workflow):
    workflow["config"]["docs"]["mode"] = "flag"
    assert service.execute(workflow["repo"], service.DocsRequest())["mode"] == "flag"


def test_invalid_configured_mode_cannot_issue_or_sync(workflow):
    workflow["config"]["docs"]["mode"] = "mistyped"
    with pytest.raises(service.DocumentationError, match="invalid docs mode"):
        service.execute(workflow["repo"], service.DocsRequest())
    assert not workflow["events"]


@pytest.mark.parametrize("available", [True, False])
def test_unavailable_graph_is_not_an_empty_success(workflow, monkeypatch, available):
    monkeypatch.setattr(service.entire, "available", lambda: available)
    monkeypatch.setattr(service.entire, "diff_json", lambda *args: None)
    with pytest.raises(service.DocumentationError) as caught:
        service.execute(workflow["repo"], service.DocsRequest())
    assert caught.value.exit_code == 3
    assert caught.value.payload == {"error": "entire graph diff failed; cannot determine documentation candidates",
                                    "install": service.entire.INSTALL_HINT, "exit": 3}
    assert not any(event[0] in {"issue", "sync"} for event in workflow["events"])


@pytest.mark.parametrize("operation", ["prepare", "apply"])
def test_selected_candidate_must_pass_current_listing_gate(workflow, monkeypatch, operation):
    calls = []
    monkeypatch.setattr(service.docsmod, "issued_candidate", lambda *args: (None, "listing is stale"))
    monkeypatch.setattr(service.docreview, "prepare", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(service.docsmod, "apply", lambda *args, **kwargs: calls.append(args))
    result = service.execute(workflow["repo"], service.DocsRequest(**{operation: "d1"}))
    assert result["not_applied"] == [{"id": "d1", "why": "listing is stale"}]
    assert result["prepared"] is None and not result["applied"] and not calls
    assert not any(event[0] == "issue" for event in workflow["events"])


def test_prepare_uses_requested_target_without_document_apply(workflow, monkeypatch):
    calls = []
    monkeypatch.setattr(service.docreview, "prepare", lambda c, repo, **kwargs: calls.append((c, repo, kwargs)) or {"file": "draft"})
    monkeypatch.setattr(service.docsmod, "apply", lambda *args, **kwargs: pytest.fail("prepare applied a document"))
    result = service.execute(workflow["repo"], service.DocsRequest(prepare="d1", target_index=2))
    assert calls == [(workflow["candidate"], workflow["repo"], {"target_index": 2})]
    assert result["prepared"] == {"file": "draft"} and not result["applied"]


@pytest.mark.parametrize("review", [False, True])
def test_explicit_apply_uses_correct_save_path(workflow, monkeypatch, review):
    calls = []
    saved = {"saved": True, "save_id": "save", "file": "docs/api.md", "graph_refreshed": True}
    monkeypatch.setattr(service.docsmod, "apply", lambda c, repo, **kwargs: calls.append(("plain", kwargs)) or saved)
    monkeypatch.setattr(service.docreview, "apply", lambda c, repo, **kwargs: calls.append(("review", kwargs)) or saved)
    result = service.execute(workflow["repo"], service.DocsRequest(apply="d1", review=review,
                                                                  target_index=2 if review else None))
    expected = [("review", {"detailed": True, "target_index": 2})] if review else [("plain", {"detailed": True})]
    assert calls == expected
    assert result["applied"] == ["d1"] and result["saves"] == [saved]


def test_saved_document_with_failed_graph_refresh_remains_saved(workflow, monkeypatch):
    saved = {"saved": True, "save_id": "save", "file": "docs/api.md", "graph_refreshed": False,
             "graph_error": "provider unavailable", "journal_error": "save succeeded; final journal update failed"}
    monkeypatch.setattr(service.docsmod, "apply", lambda *args, **kwargs: saved)
    monkeypatch.setattr(service.docgraph, "comparisons", lambda *args: pytest.fail("compared a stale graph"))
    result = service.execute(workflow["repo"], service.DocsRequest(apply="d1"))
    assert result["applied"] == ["d1"] and result["saves"] == [saved]
    assert not result["not_applied"]
    assert result["docs_graph"]["status"] == "refresh_pending" and result["relationship_comparisons"] == []
    assert result["next"].startswith("document saved;")


@pytest.mark.parametrize("filename", ["docs-graph.json", "docs-graph-state.json"])
@pytest.mark.parametrize("operation", ["apply", "review", "auto"])
def test_post_save_derived_graph_corruption_keeps_known_save_result(workflow, monkeypatch, filename, operation):
    saved = {"saved": True, "save_id": "save", "file": "docs/api.md", "graph_refreshed": True,
             "graph_error": None, "journal_error": None}
    read = service.state.read_json
    saved_already = False

    def save(*args, **kwargs):
        nonlocal saved_already
        saved_already = True
        return saved

    def corrupt_after_save(name, *args, **kwargs):
        if name == filename and saved_already:
            raise state.StateReadError(f"{filename}: corrupt derived metadata")
        return read(name, *args, **kwargs)

    monkeypatch.setattr(service.docsmod, "apply", save)
    monkeypatch.setattr(service.docreview, "apply", save)
    monkeypatch.setattr(service.state, "read_json", corrupt_after_save)
    monkeypatch.setattr(service.docgraph, "comparisons", lambda *args: pytest.fail("compared unreadable derived graph"))
    docs_request = {"apply": service.DocsRequest(apply="d1"), "review": service.DocsRequest(apply="d1", review=True),
                    "auto": service.DocsRequest(mode="auto")}[operation]
    result = service.execute(workflow["repo"], docs_request)
    assert saved_already and result["applied"] == ["d1"] and result["saves"] == [saved]
    assert saved["saved"] is True and saved["graph_refreshed"] is True
    assert not result["not_applied"] and result["relationship_comparisons"] == []
    assert result["docs_graph"]["status"] == "refresh_pending"
    assert filename in result["docs_graph"]["error"]
    assert result["docs_graph"]["summary"] == {} and result["docs_graph"]["delta"] == {}
    assert result["docs_graph"]["graph_hash"] is None and result["docs_graph"]["revisions"] is None
    assert result["next"].startswith("document saved;")


@pytest.mark.parametrize("filename", ["docs-graph.json", "docs-graph-state.json"])
def test_real_saved_document_is_reported_even_if_derived_graph_becomes_corrupt(tmp_path, monkeypatch, filename):
    from tests.test_docs import _committed_repo, _sound_signature_diff, _write_doc_map

    repo = _committed_repo(tmp_path, {"api.py": "def public(value):\n    return value\n",
                                      "docs/api.md": "# API\n\nOld usage.\n"})
    _write_doc_map(repo, {"api.py#public": [{"file": "docs/api.md", "section": "# API"}]})
    monkeypatch.setattr(service.entire, "available", lambda: True)
    monkeypatch.setattr(service.entire, "diff_json", lambda *args: _sound_signature_diff())
    service.execute(str(repo), service.DocsRequest(mode="draft"))
    sync = service.docgraph.sync
    calls = 0

    def corrupt_persisted_graph_only_after_save(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = sync(*args, **kwargs)
        if calls == 2:
            (repo / ".amu" / filename).write_text("{corrupt")
        return result

    monkeypatch.setattr(service.docgraph, "sync", corrupt_persisted_graph_only_after_save)
    result = service.execute(str(repo), service.DocsRequest(apply="d1"))
    assert "def public(value, *, strict=False)" in (repo / "docs/api.md").read_text()
    assert result["applied"] == ["d1"] and result["saves"][0]["saved"] is True
    assert result["docs_graph"]["status"] == "refresh_pending" and filename in result["docs_graph"]["error"]
    assert result["relationship_comparisons"] == []
    history = service.saves(str(repo))["saves"][0]
    assert history["status"] == "matches_saved" and history["recorded_status"] == "saved"


@pytest.mark.parametrize("filename,value", [
    ("docs-graph.json", []), ("docs-graph.json", None), ("docs-graph.json", {"summary": []}),
    ("docs-graph.json", {"graph_hash": 4}), ("docs-graph.json", {"revisions": []}),
    ("docs-graph-state.json", []), ("docs-graph-state.json", {"delta": []}),
])
@pytest.mark.parametrize("save_document", [False, True])
def test_final_derived_graph_shape_is_checked_before_access(workflow, monkeypatch, filename, value, save_document):
    saved = {"saved": True, "save_id": "save", "file": "docs/api.md", "graph_refreshed": True}
    read = service.state.read_json
    monkeypatch.setattr(service.docsmod, "apply", lambda *args, **kwargs: saved)
    monkeypatch.setattr(service.state, "read_json", lambda name, *args, **kwargs: value if name == filename else read(name, *args, **kwargs))
    docs_request = service.DocsRequest(apply="d1") if save_document else service.DocsRequest()
    if save_document:
        result = service.execute(workflow["repo"], docs_request)
        assert result["saves"] == [saved] and result["applied"] == ["d1"]
        assert result["docs_graph"]["status"] == "refresh_pending" and filename in result["docs_graph"]["error"]
        assert result["docs_graph"]["graph_hash"] is None and result["relationship_comparisons"] == []
    else:
        with pytest.raises(state.StateReadError, match=filename):
            service.execute(workflow["repo"], docs_request)


@pytest.mark.parametrize("save_document", [False, True])
def test_final_comparison_state_error_preserves_only_completed_save_outcomes(workflow, monkeypatch, save_document):
    saved = {"saved": True, "save_id": "save", "file": "docs/api.md", "graph_refreshed": True}
    monkeypatch.setattr(service.docsmod, "apply", lambda *args, **kwargs: saved)

    def corrupt_comparison_state(*args):
        raise state.StateReadError("refresh-state.json: invalid object; restore or repair this state file")

    monkeypatch.setattr(service.docgraph, "comparisons", corrupt_comparison_state)
    docs_request = service.DocsRequest(apply="d1") if save_document else service.DocsRequest()
    if save_document:
        result = service.execute(workflow["repo"], docs_request)
        assert result["saves"] == [saved] and result["applied"] == ["d1"]
        assert result["docs_graph"]["status"] == "refresh_pending"
        assert "refresh-state.json" in result["docs_graph"]["error"] and result["relationship_comparisons"] == []
    else:
        with pytest.raises(state.StateReadError, match="refresh-state.json"):
            service.execute(workflow["repo"], docs_request)


def test_unrelated_comparison_runtime_error_is_not_hidden_even_after_save(workflow, monkeypatch):
    saved = {"saved": True, "save_id": "save", "file": "docs/api.md", "graph_refreshed": True}
    monkeypatch.setattr(service.docsmod, "apply", lambda *args, **kwargs: saved)

    def fail_comparison(*args):
        raise RuntimeError("unexpected implementation error")

    monkeypatch.setattr(service.docgraph, "comparisons", fail_comparison)
    with pytest.raises(RuntimeError, match="unexpected implementation error"):
        service.execute(workflow["repo"], service.DocsRequest(apply="d1"))


@pytest.mark.parametrize("filename", ["docs-graph.json", "docs-graph-state.json"])
def test_derived_graph_corruption_without_any_save_still_fails_closed(workflow, monkeypatch, filename):
    read = service.state.read_json

    def corrupt(name, *args, **kwargs):
        if name == filename:
            raise state.StateReadError("unreadable derived graph")
        return read(name, *args, **kwargs)

    monkeypatch.setattr(service.state, "read_json", corrupt)
    with pytest.raises(state.StateReadError, match="unreadable derived graph"):
        service.execute(workflow["repo"], service.DocsRequest())


@pytest.mark.parametrize("confidence,mapped,shadowed", [("unknown", True, False), ("guessed", True, False),
                                                       ("sound", False, False), ("sound", True, True)])
def test_auto_cannot_expand_manual_or_shadowed_save_authority(workflow, monkeypatch, confidence, mapped, shadowed):
    workflow["candidate"]["confidence"] = confidence
    if not mapped:
        workflow["candidate"]["targets"] = []
    if shadowed:
        monkeypatch.setattr(service.docsmod, "worktree_auto_block", lambda *args: {"reason": "local edit",
                                                                                "worktree_candidate": "d2"})
    monkeypatch.setattr(service.docsmod, "apply", lambda *args, **kwargs: pytest.fail("unsafe auto save"))
    result = service.execute(workflow["repo"], service.DocsRequest(mode="auto"))
    assert result["applied"] == [] and len(result["not_applied"]) == 1
    assert bool(result["worktree_blocks"]) is shadowed


def test_sound_mapped_unshadowed_auto_candidate_keeps_save_metadata(workflow, monkeypatch):
    saved = {"saved": True, "save_id": "save", "file": "docs/api.md", "graph_refreshed": True}
    monkeypatch.setattr(service.docsmod, "apply", lambda *args, **kwargs: saved)
    result = service.execute(workflow["repo"], service.DocsRequest(mode="auto"))
    assert result["applied"] == ["d1"] and result["saves"] == [saved]
    assert result["commit_hint"] == "docs: amu auto-update"


@pytest.mark.parametrize("derived_state_corrupt", [False, True])
def test_auto_state_failure_retains_prior_save_and_stops_later_writes(workflow, monkeypatch, derived_state_corrupt):
    rows = [{**workflow["candidate"], "id": f"d{n}", "candidate": f"api.py#public{n}"} for n in range(1, 4)]
    saved = {"saved": True, "save_id": "save1", "file": "docs/api.md", "graph_refreshed": True}
    calls = []
    monkeypatch.setattr(service.docsmod, "all_candidates", lambda *args: rows)

    def apply(candidate, *args, **kwargs):
        calls.append(candidate["id"])
        if candidate["id"] == "d1":
            return saved
        if candidate["id"] == "d2":
            raise state.StateReadError("config.json: invalid state before second save")
        pytest.fail("auto continued writing after unreadable state")

    monkeypatch.setattr(service.docsmod, "apply", apply)
    if derived_state_corrupt:
        read = service.state.read_json

        def corrupt_final_graph(name, *args, **kwargs):
            if name == "docs-graph.json":
                raise state.StateReadError("docs-graph.json: corrupt derived state")
            return read(name, *args, **kwargs)

        monkeypatch.setattr(service.state, "read_json", corrupt_final_graph)
    monkeypatch.setattr(service.docgraph, "comparisons", lambda *args: pytest.fail("incomplete auto compared uncertain graph"))
    with pytest.raises(service.DocumentationError) as caught:
        service.execute(workflow["repo"], service.DocsRequest(mode="auto"))
    assert calls == ["d1", "d2"] and caught.value.exit_code == 2
    result = caught.value.payload
    assert result["exit"] == 2 and result["error_kind"] == "state_unreadable" and result["complete"] is False
    assert result["applied"] == ["d1"] and result["saves"] == [saved]
    assert result["commit_hint"] is None and result["relationship_comparisons"] == []
    assert result["docs_graph"]["status"] == "refresh_pending"
    assert [(row["id"], row["status"]) for row in result["unconfirmed_operations"]] == [("d2", "unconfirmed")]
    assert [(row["id"], row["status"]) for row in result["not_applied"]] == [("d3", "not_attempted")]
    assert "unconfirmed" in result["unconfirmed_operations"][0]["why"] and "not attempted" in result["not_applied"][0]["why"]
    assert "1 confirmed save" in result["error"] and "amu docs-saves" in result["next"]


@pytest.mark.parametrize("second_save_completed", [False, True])
def test_real_auto_save_journal_and_document_survive_later_state_failure(tmp_path, monkeypatch, second_save_completed):
    from tests.test_docs import _committed_repo, _sound_signature_diff, _write_doc_map

    names = ["first", "second", "third"]
    repo = _committed_repo(tmp_path, {
        "api.py": "\n".join(f"def {name}(value):\n    return value\n" for name in names),
        **{f"docs/{name}.md": f"# {name}\n\nOriginal {name} usage.\n" for name in names},
    })
    _write_doc_map(repo, {f"api.py#{name}": [{"file": f"docs/{name}.md", "section": f"# {name}"}] for name in names})
    diff = {"files": [{"path": "api.py", "changes": [_sound_signature_diff(name=name)["files"][0]["changes"][0] for name in names]}]}
    monkeypatch.setattr(service.entire, "available", lambda: True)
    monkeypatch.setattr(service.entire, "diff_json", lambda *args: diff)
    apply = service.docsmod.apply
    calls = []

    def fail_second_save(candidate, *args, **kwargs):
        calls.append(candidate["id"])
        if candidate["candidate"] == "api.py#first":
            return apply(candidate, *args, **kwargs)
        if candidate["candidate"] == "api.py#second" and second_save_completed:
            apply(candidate, *args, **kwargs)
        raise state.StateReadError("config.json: unreadable before this save")

    monkeypatch.setattr(service.docsmod, "apply", fail_second_save)
    with pytest.raises(service.DocumentationError) as caught:
        service.execute(str(repo), service.DocsRequest(mode="auto"))
    result = caught.value.payload
    assert calls == ["d1", "d2"] and result["applied"] == ["d1"] and result["saves"][0]["saved"] is True
    assert "def first(value, *, strict=False)" in (repo / "docs/first.md").read_text()
    if second_save_completed:
        assert "def second(value, *, strict=False)" in (repo / "docs/second.md").read_text()
    else:
        assert (repo / "docs/second.md").read_text() == "# second\n\nOriginal second usage.\n"
    for name in names[2:]:
        assert (repo / f"docs/{name}.md").read_text() == f"# {name}\n\nOriginal {name} usage.\n"
    records = service.saves(str(repo))["saves"]
    assert len(records) == 1 + second_save_completed
    assert all(record["status"] == "matches_saved" and record["recorded_status"] == "saved" for record in records)
    assert result["saves"][0]["save_id"] in {record["save_id"] for record in records}
    assert result["unconfirmed_operations"][0]["status"] == "unconfirmed"
    assert result["not_applied"][0]["id"] == "d3"


@pytest.mark.parametrize("operation", ["prepare", "apply", "review", "auto"])
def test_corrupt_state_is_not_converted_to_successful_skip(workflow, monkeypatch, operation):
    def fail(*args, **kwargs):
        raise state.StateReadError("corrupt recovery state")

    monkeypatch.setattr(service.docreview, "prepare", fail)
    monkeypatch.setattr(service.docreview, "apply", fail)
    monkeypatch.setattr(service.docsmod, "apply", fail)
    request = {"prepare": service.DocsRequest(prepare="d1"), "apply": service.DocsRequest(apply="d1"),
               "review": service.DocsRequest(apply="d1", review=True), "auto": service.DocsRequest(mode="auto")}[operation]
    with pytest.raises(state.StateReadError, match="corrupt recovery state"):
        service.execute(workflow["repo"], request)


@pytest.mark.parametrize("kwargs", [{"preview": "id", "discard": "id"}, {"preview": ""}, {"discard": True}])
def test_invalid_queue_request_never_touches_review_files(tmp_path, monkeypatch, kwargs):
    monkeypatch.setattr(service.docreview, "queue", lambda *args: pytest.fail("invalid request accessed state"))
    with pytest.raises(service.DocumentationError):
        service.review_queue(str(tmp_path), **kwargs)
    assert not (tmp_path / ".amu").exists()


def test_queue_and_preview_do_not_require_graph_or_candidate_freshness(tmp_path, monkeypatch):
    monkeypatch.setattr(service.entire, "available", lambda: pytest.fail("queue queried graph"))
    monkeypatch.setattr(service.docsmod, "issued_candidate", lambda *args: pytest.fail("queue required a candidate"))
    monkeypatch.setattr(service.docreview, "queue", lambda repo: [{"review_id": "review", "status": "edited"}])
    monkeypatch.setattr(service.docreview, "preview", lambda ident, repo: {"review_id": ident, "diff": "+edited"})
    assert service.review_queue(str(tmp_path)) == {"reviews": [{"review_id": "review", "status": "edited"}]}
    assert service.review_queue(str(tmp_path), preview="review") == {"review_id": "review", "diff": "+edited"}


def test_queue_expected_error_preserves_legacy_payload(tmp_path, monkeypatch):
    def fail(*args):
        raise ValueError("invalid review id")
    monkeypatch.setattr(service.docreview, "preview", fail)
    with pytest.raises(service.DocumentationError) as caught:
        service.review_queue(str(tmp_path), preview="bad-id")
    assert caught.value.exit_code == 2 and caught.value.payload == {"error": "invalid review id"}


def test_saves_history_and_explicit_undo(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(service.docsave, "history", lambda repo: [{"id": "save"}])
    monkeypatch.setattr(service.docsave, "undo", lambda repo, ident: calls.append((repo, ident)) or {"saved": True})
    assert service.saves(str(tmp_path)) == {"saves": [{"id": "save"}]}
    assert service.saves(str(tmp_path), undo="save") == {"saved": True}
    assert calls == [(str(tmp_path), "save")] and capsys.readouterr() == ("", "")


@pytest.mark.parametrize("undo", [False, "", " ", 2])
def test_invalid_undo_never_touches_journal(tmp_path, monkeypatch, undo):
    monkeypatch.setattr(service.docsave, "history", lambda *args: pytest.fail("invalid undo accessed state"))
    with pytest.raises(service.DocumentationError):
        service.saves(str(tmp_path), undo=undo)
    assert not (tmp_path / ".amu").exists()


@pytest.mark.parametrize("operation", ["preview", "discard", "history", "undo"])
def test_review_and_recovery_state_errors_keep_their_type(tmp_path, monkeypatch, operation):
    def fail(*args):
        raise state.StateReadError("corrupt journal")
    if operation in {"preview", "discard"}:
        monkeypatch.setattr(service.docreview, operation, fail)

        def call():
            return service.review_queue(str(tmp_path), **{operation: "id"})
    else:
        monkeypatch.setattr(service.docsave, operation, fail)

        def call():
            return service.saves(str(tmp_path), undo="id" if operation == "undo" else None)
    with pytest.raises(state.StateReadError, match="corrupt journal"):
        call()


def test_graph_refresh_uses_configured_docs_directory_and_comparisons(workflow, capsys):
    workflow["config"]["docs_dir"] = "guide"
    result = service.graph(workflow["repo"])
    assert ("sync", workflow["repo"], "guide") in workflow["events"]
    assert result["graph"] == workflow["graph"]
    assert result["relationship_comparisons"] == [{"status": "review_required"}]
    assert capsys.readouterr() == ("", "")


def test_service_does_not_import_transport_or_presentation_modules():
    tree = ast.parse(Path(service.__file__).read_text())
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
            if node.module == "amu":
                imports.extend(f"amu.{alias.name}" for alias in node.names)
    assert not any(name.split(".")[0] in {"typer", "rich"} or name in {"amu.cli", "amu.repl", "amu.render", "amu.brand"}
                   for name in imports)
