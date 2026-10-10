from pathlib import Path

import pytest

from amu import docreview


def candidate(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/a.md").write_text("# A\n\nOriginal A.\n")
    (tmp_path / "docs/b.md").write_text("# B\n\nOriginal B.\n")
    return {"id": "d1", "candidate": "api.py#f", "class": "body", "confidence": "unknown",
            "targets": [{"file": "docs/a.md", "section": "# A"}, {"file": "docs/b.md", "section": "# B"}]}


def test_multi_target_drafts_are_independent_and_preview_never_saves(tmp_path):
    c = candidate(tmp_path)
    a = docreview.prepare(c, str(tmp_path), target_index=1)
    b = docreview.prepare(c, str(tmp_path), target_index=2)
    assert a["review_id"] != b["review_id"]
    Path(a["file"]).write_text("Reviewed A.\n")
    preview = docreview.preview(a["review_id"], str(tmp_path))
    assert "-Original A." in preview["diff"] and "+Reviewed A." in preview["diff"]
    assert preview["status"] == "edited"
    assert "Original A." in (tmp_path / "docs/a.md").read_text()
    assert len(docreview.queue(str(tmp_path))) == 2
    assert docreview.apply(c, str(tmp_path), target_index=1)
    assert docreview.preview(a["review_id"], str(tmp_path))["status"] == "saved"
    assert "Original B." in (tmp_path / "docs/b.md").read_text()


def test_empty_review_inspection_does_not_create_managed_state(tmp_path):
    assert docreview.queue(str(tmp_path)) == []
    assert not (tmp_path / ".amu").exists()
    with pytest.raises(ValueError, match="missing or invalid"):
        docreview.preview("a" * 16, str(tmp_path))
    assert not (tmp_path / ".amu").exists()


def test_review_queue_preserves_existing_state_without_creating_review_directory(tmp_path):
    from typer.testing import CliRunner

    from amu.cli import app

    managed = tmp_path / ".amu"
    managed.mkdir(mode=0o700)
    config = managed / "config.json"
    config.write_text('{"outbound":{"enabled":false}}')
    before = config.read_bytes()
    result = CliRunner().invoke(app, ["docs-reviews", "--repo", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.stdout
    assert config.read_bytes() == before
    assert sorted(path.name for path in managed.iterdir()) == ["config.json"]


@pytest.mark.parametrize("component", [".amu", "doc-reviews"])
def test_readonly_review_queue_rejects_state_directory_alias(tmp_path, component):
    from amu import state

    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    if component == ".amu":
        (root / ".amu").symlink_to(outside, target_is_directory=True)
    else:
        (root / ".amu").mkdir(mode=0o700)
        (root / ".amu/doc-reviews").symlink_to(outside, target_is_directory=True)
    with pytest.raises((ValueError, state.StateReadError)):
        docreview.queue(str(root))
    assert not list(outside.iterdir())


def test_discard_archives_prose_and_allows_reprepare(tmp_path):
    c = candidate(tmp_path)
    prepared = docreview.prepare(c, str(tmp_path), target_index=1)
    Path(prepared["file"]).write_text("Keep this edited draft.\n")
    result = docreview.discard(prepared["review_id"], str(tmp_path))
    assert (Path(result["archive"]) / Path(prepared["file"]).name).read_text() == "Keep this edited draft.\n"
    assert not docreview.queue(str(tmp_path))
    docreview.prepare(c, str(tmp_path), target_index=1)


def test_stale_draft_is_visible_and_cannot_be_applied(tmp_path):
    c = candidate(tmp_path)
    prepared = docreview.prepare(c, str(tmp_path), target_index=1)
    Path(prepared["file"]).write_text("Draft edit.\n")
    (tmp_path / "docs/a.md").write_text("# A\n\nConcurrent edit.\n")
    assert docreview.preview(prepared["review_id"], str(tmp_path))["status"] == "conflict"
    with pytest.raises(ValueError, match="document changed"):
        docreview.apply(c, str(tmp_path), target_index=1)


@pytest.mark.parametrize("index", [0, -1, 3, True])
def test_invalid_target_index_does_not_write(tmp_path, index):
    with pytest.raises(ValueError):
        docreview.prepare(candidate(tmp_path), str(tmp_path), target_index=index)


def test_invalid_review_identifier_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        docreview.preview("../../secret", str(tmp_path))


def test_queue_cli_works_without_graph_or_current_candidates(tmp_path):
    import json

    from typer.testing import CliRunner

    from amu.cli import app
    prepared = docreview.prepare(candidate(tmp_path), str(tmp_path), target_index=1)
    result = CliRunner().invoke(app, ["docs-reviews", "--preview", prepared["review_id"], "--repo", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "prepared"


def test_mcp_review_saves_need_explicit_confirmation(monkeypatch):
    import json

    from amu import mcp_server
    calls = []
    monkeypatch.setattr(mcp_server, "_cli", lambda *args, **kwargs: calls.append(args) or "{}")
    assert "error" in json.loads(mcp_server.amu_docs_apply_review("d1", repo="/repo"))
    assert "error" in json.loads(mcp_server.amu_docs_discard("a" * 16, repo="/repo"))
    assert "error" in json.loads(mcp_server.amu_docs_saves(undo="save-id", repo="/repo"))
    assert not calls
    mcp_server.amu_docs_prepare("d1", target_index=2, repo="/repo")
    assert calls[-1] == ("docs", "--mode", "draft", "--prepare", "d1", "--target-index", "2")
    mcp_server.amu_docs_apply_review("d1", confirm=True, target_index=2, repo="/repo")
    assert calls[-1] == ("docs", "--mode", "draft", "--apply", "d1", "--review", "--target-index", "2")


def test_review_queue_repl_matches_cli(tmp_path, monkeypatch):
    from amu import cli
    from tests.test_repl_dispatch import _session
    prepared = docreview.prepare(candidate(tmp_path), str(tmp_path), target_index=1)
    outputs = []
    monkeypatch.setattr(cli, "_out", lambda out, *_: outputs.append(out))
    _session(monkeypatch, tmp_path, [f"/docs-reviews --preview {prepared['review_id']}"])
    assert outputs[0]["review_id"] == prepared["review_id"]


def test_review_draft_symlink_is_not_read_or_discarded(tmp_path):
    c = candidate(tmp_path)
    prepared = docreview.prepare(c, str(tmp_path), target_index=1)
    draft = Path(prepared["file"])
    draft.unlink()
    draft.symlink_to(tmp_path / "docs/b.md")
    with pytest.raises(ValueError, match="regular file"):
        docreview.preview(prepared["review_id"], str(tmp_path))
    with pytest.raises(ValueError, match="regular file"):
        docreview.discard(prepared["review_id"], str(tmp_path))
    assert "Original B" in (tmp_path / "docs/b.md").read_text()


def test_cli_selects_only_the_requested_document(fake_entire, monkeypatch, tmp_path):
    import json

    from typer.testing import CliRunner

    from amu import cli
    from tests.test_docs import _committed_repo, _sound_signature_diff, _write_doc_map

    repo = _committed_repo(tmp_path, {
        "api.py": "def public(value):\n    return value\n",
        "docs/a.md": "# A\n\nOld usage: def public(value).\n",
        "docs/b.md": "# B\n\nOld usage: def public(value).\n",
    })
    _write_doc_map(repo, {"api.py#public": [{"file": "docs/a.md", "section": "# A"},
                                          {"file": "docs/b.md", "section": "# B"}]})
    monkeypatch.setattr(cli.entire, "diff_json", lambda *_a, **_k: _sound_signature_diff())
    runner = CliRunner()
    def run(*args):
        result = runner.invoke(cli.app, ["docs", *args, "--repo", str(repo), "--json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)
    run("--mode", "draft")
    prepared = run("--prepare", "d1", "--target-index", "2")["prepared"]
    Path(prepared["file"]).write_text("Reviewed B usage.\n")
    saved = run("--apply", "d1", "--review", "--target-index", "2")
    assert saved["applied"] == ["d1"]
    assert "Old usage" in (repo / "docs/a.md").read_text()
    assert "Reviewed B" in (repo / "docs/b.md").read_text()


def test_real_mcp_subprocess_can_preview_review_queue(tmp_path):
    import json

    from amu import mcp_server

    prepared = docreview.prepare(candidate(tmp_path), str(tmp_path), target_index=1)
    result = json.loads(mcp_server.amu_docs_reviews(preview=prepared["review_id"], repo=str(tmp_path)))
    assert result["review_id"] == prepared["review_id"]
    assert result["status"] == "prepared"


def test_discard_waits_for_apply_and_archives_final_save_metadata(tmp_path, monkeypatch):
    import json
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    c = candidate(tmp_path)
    prepared = docreview.prepare(c, str(tmp_path), target_index=1)
    Path(prepared["file"]).write_text("Reviewed usage.\n")
    save_complete, resume_apply, discard_started, discard_complete = (Event() for _ in range(4))
    save = docreview.docsave.save

    def paused_save(*args, **kwargs):
        result = save(*args, **kwargs)
        save_complete.set()
        assert resume_apply.wait(5)
        return result

    def discard():
        discard_started.set()
        result = docreview.discard(prepared["review_id"], str(tmp_path))
        discard_complete.set()
        return result

    monkeypatch.setattr(docreview.docsave, "save", paused_save)
    with ThreadPoolExecutor(max_workers=2) as pool:
        applying = pool.submit(docreview.apply, c, str(tmp_path), target_index=1, detailed=True)
        assert save_complete.wait(5)
        discarding = pool.submit(discard)
        try:
            assert discard_started.wait(5)
            assert not discard_complete.wait(0.1), "discard raced the save status update"
        finally:
            resume_apply.set()
        saved, discarded = applying.result(timeout=5), discarding.result(timeout=5)
    archived = json.loads((Path(discarded["archive"]) / f"{prepared['review_id']}.json").read_text())
    assert archived["status"] == "saved" and archived["save_id"] == saved["save_id"]
    assert not docreview.queue(str(tmp_path))


def test_failed_prepare_does_not_leave_an_inaccessible_draft(tmp_path, monkeypatch):
    c = candidate(tmp_path)
    write = docreview.state._write_json_atomic

    def fail(*args):
        raise OSError("metadata write failed")

    monkeypatch.setattr(docreview.state, "_write_json_atomic", fail)
    with pytest.raises(OSError, match="metadata write failed"):
        docreview.prepare(c, str(tmp_path), target_index=1)
    monkeypatch.setattr(docreview.state, "_write_json_atomic", write)
    prepared = docreview.prepare(c, str(tmp_path), target_index=1)
    assert docreview.preview(prepared["review_id"], str(tmp_path))["status"] == "prepared"
    assert "Original A" in (tmp_path / "docs/a.md").read_text()


@pytest.mark.parametrize("operation", ["preview", "apply"])
def test_swapped_draft_symlink_is_never_read(tmp_path, monkeypatch, operation):
    c = candidate(tmp_path)
    prepared = docreview.prepare(c, str(tmp_path), target_index=1)
    draft = Path(prepared["file"])
    private = tmp_path / "private.txt"
    private.write_text("Private text must not become documentation.\n")
    saved = docreview._saved
    symlink_check = Path.is_symlink
    armed = False

    def swap_after_validation(*args):
        nonlocal armed
        result = saved(*args)
        if operation == "preview":
            draft.unlink()
            draft.symlink_to(private)
        else:
            armed = True
        return result

    def swap_after_last_check(path):
        nonlocal armed
        result = symlink_check(path)
        if path == draft and armed:
            armed = False
            draft.unlink()
            draft.symlink_to(private)
        return result

    monkeypatch.setattr(docreview, "_saved", swap_after_validation)
    monkeypatch.setattr(Path, "is_symlink", swap_after_last_check)
    with pytest.raises(ValueError, match="regular file"):
        if operation == "preview":
            docreview.preview(prepared["review_id"], str(tmp_path))
        else:
            docreview.apply(c, str(tmp_path), target_index=1)
    assert "Private text" not in (tmp_path / "docs/a.md").read_text()


def test_uppercase_document_suffix_creates_a_usable_draft(tmp_path):
    c = candidate(tmp_path)
    (tmp_path / "docs/a.md").rename(tmp_path / "docs/a.MD")
    c["targets"][0]["file"] = "docs/a.MD"
    prepared = docreview.prepare(c, str(tmp_path), target_index=1)
    assert docreview.preview(prepared["review_id"], str(tmp_path))["status"] == "prepared"
    Path(prepared["file"]).write_text("New usage.\n")
    assert docreview.apply(c, str(tmp_path), target_index=1)


def test_oversized_proposed_draft_does_not_create_a_review(tmp_path):
    c = candidate(tmp_path)
    (tmp_path / "docs/a.md").write_text("# A\n\n" + "é" * 500_001)
    with pytest.raises(ValueError, match="1 MB"):
        docreview.prepare(c, str(tmp_path), target_index=1)
    assert not docreview.queue(str(tmp_path))


def test_discard_metadata_failure_restores_the_active_draft(tmp_path, monkeypatch):
    c = candidate(tmp_path)
    prepared = docreview.prepare(c, str(tmp_path), target_index=1)
    draft = Path(prepared["file"])
    draft.write_text("Preserve this reviewed prose.\n")
    rename = Path.rename

    def fail_metadata(path, destination):
        if path.name == f"{prepared['review_id']}.json":
            raise OSError("archive metadata failed")
        return rename(path, destination)

    monkeypatch.setattr(Path, "rename", fail_metadata)
    with pytest.raises(OSError, match="archive metadata failed"):
        docreview.discard(prepared["review_id"], str(tmp_path))
    assert draft.read_text() == "Preserve this reviewed prose.\n"
    assert docreview.preview(prepared["review_id"], str(tmp_path))["status"] == "edited"


def test_failed_prepare_preserves_a_concurrently_edited_draft(tmp_path, monkeypatch):
    c = candidate(tmp_path)
    identity = docreview.docs.candidate_fingerprint(docreview.select(c, 1))
    draft = tmp_path / ".amu/doc-reviews" / f"{identity}.md"

    def edit_then_fail(*args):
        draft.write_text("Developer started editing.\n")
        raise OSError("metadata write failed")

    monkeypatch.setattr(docreview.state, "_write_json_atomic", edit_then_fail)
    with pytest.raises(OSError, match="metadata write failed"):
        docreview.prepare(c, str(tmp_path), target_index=1)
    assert draft.read_text() == "Developer started editing.\n"


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("edited", ["Later unsaved edit.\n", "Original A.\n"])
def test_editing_a_saved_draft_is_not_reported_as_saved(tmp_path, legacy, edited):
    c = candidate(tmp_path)
    prepared = docreview.prepare(c, str(tmp_path), target_index=1)
    draft = Path(prepared["file"])
    draft.write_text("Saved reviewed text.\n")
    docreview.apply(c, str(tmp_path), target_index=1)
    if legacy:
        docreview.state.update_json(f"doc-reviews/{prepared['review_id']}.json", str(tmp_path), {},
                                   lambda value: {key: item for key, item in value.items() if key != "saved_draft_hash"})
    assert docreview.preview(prepared["review_id"], str(tmp_path))["status"] == "saved"
    draft.write_text(edited)
    assert docreview.preview(prepared["review_id"], str(tmp_path))["status"] == "edited"
    assert "Later unsaved" not in (tmp_path / "docs/a.md").read_text()
