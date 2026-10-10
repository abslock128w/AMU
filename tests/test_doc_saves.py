from pathlib import Path

import pytest

from amu import docgraph, docreview, docs


def setup_doc(tmp_path):
    (tmp_path / "docs").mkdir()
    path = tmp_path / "docs/api.md"
    path.write_text("# API\n\nOld prose.\n\n# Next\nKeep.\n")
    candidate = {"id": "d1", "candidate": "api.py#f", "class": "body", "confidence": "unknown",
                 "targets": [{"file": "docs/api.md", "section": "# API"}]}
    return path, candidate


@pytest.mark.parametrize("review", [False, True])
def test_graph_failure_reports_saved_document_not_failed_apply(tmp_path, monkeypatch, review):
    path, candidate = setup_doc(tmp_path)
    original = path.read_bytes()
    if review:
        draft = docreview.prepare(candidate, str(tmp_path))
        Path(draft["file"]).write_text("New prose.\n")

    def fail(*args, **kwargs):
        raise OSError("simulated graph write failure")

    monkeypatch.setattr(docgraph, "sync", fail)
    result = (docreview.apply if review else docs.apply)(candidate, str(tmp_path), detailed=True)
    assert result["saved"] is True
    assert result["graph_refreshed"] is False
    assert result["save_id"]
    assert path.read_bytes() != original


def test_conflict_during_save_preserves_external_edit(tmp_path, monkeypatch):
    from amu import docsave

    path, _ = setup_doc(tmp_path)
    original = path.read_text()
    write = docsave._write_record

    def change_after_journal(*args):
        write(*args)
        path.write_text("Someone else's edit\n")

    monkeypatch.setattr(docsave, "_write_record", change_after_journal)
    with pytest.raises(ValueError, match="changed"):
        docsave.save(str(tmp_path), path, original, 0, len(original), "New prose\n")
    assert path.read_text() == "Someone else's edit\n"


def test_atomic_replace_failure_keeps_original_and_recovery_record(tmp_path, monkeypatch):
    from amu import docsave

    path, _ = setup_doc(tmp_path)
    original = path.read_text()
    replace = docsave.os.replace

    def fail_document(source, destination):
        if Path(destination) == path:
            raise OSError("simulated replacement failure")
        return replace(source, destination)

    monkeypatch.setattr(docsave.os, "replace", fail_document)
    with pytest.raises(OSError, match="replacement failure"):
        docsave.save(str(tmp_path), path, original, 0, len(original), "Updated\n")
    assert path.read_text() == original
    assert docsave.history(str(tmp_path))[0]["status"] == "matches_original"
    assert not list(path.parent.glob("*.tmp"))


def test_final_journal_failure_still_reports_saved(tmp_path, monkeypatch):
    from amu import docsave

    path, _ = setup_doc(tmp_path)
    write = docsave._write_record

    def fail_final(repo, record):
        if record["status"] == "saved":
            raise OSError("final journal failure")
        return write(repo, record)

    monkeypatch.setattr(docsave, "_write_record", fail_final)
    result = docsave.save(str(tmp_path), path, path.read_text(), 7, 7, "Added\n")
    assert result["saved"] and result["journal_error"]
    assert docsave.history(str(tmp_path))[0]["status"] == "matches_saved"


def test_interruption_after_replace_can_be_inspected_and_undone(tmp_path, monkeypatch):
    from amu import docsave

    path, _ = setup_doc(tmp_path)
    original = path.read_text()
    sync = docgraph.sync

    def interrupt(*args):
        raise KeyboardInterrupt()

    monkeypatch.setattr(docgraph, "sync", interrupt)
    with pytest.raises(KeyboardInterrupt):
        docsave.save(str(tmp_path), path, original, 7, 7, "Added\n")
    row = docsave.history(str(tmp_path))[0]
    assert row["status"] == "matches_saved" and row["recorded_status"] == "prepared"
    monkeypatch.setattr(docgraph, "sync", sync)
    result = docsave.undo(str(tmp_path), row["save_id"])
    assert result["saved"] and result["graph_refreshed"]
    assert path.read_text() == original


def test_undo_refuses_to_overwrite_later_edits(tmp_path):
    from amu import docsave

    path, _ = setup_doc(tmp_path)
    saved = docsave.save(str(tmp_path), path, path.read_text(), 7, 7, "Added\n")
    path.write_text(path.read_text() + "Later user edit\n")
    with pytest.raises(ValueError, match="changed"):
        docsave.undo(str(tmp_path), saved["save_id"])
    assert "Later user edit" in path.read_text()


def test_cli_reports_saved_when_post_save_graph_refresh_fails(fake_entire, tmp_path, monkeypatch):
    import json

    from typer.testing import CliRunner

    from amu import cli
    from tests.test_docs import _committed_repo, _sound_signature_diff, _write_doc_map

    repo = _committed_repo(tmp_path, {"api.py": "def public(value):\n    return value\n",
                                      "docs/api.md": "# API\n\nOld prose.\n"})
    _write_doc_map(repo, {"api.py#public": [{"file": "docs/api.md", "section": "# API"}]})
    monkeypatch.setattr(cli.entire, "diff_json", lambda *_a, **_k: _sound_signature_diff())
    runner = CliRunner()
    assert runner.invoke(cli.app, ["docs", "--repo", str(repo), "--json"]).exit_code == 0
    sync, calls = docgraph.sync, []

    def fail_after_initial_sync(*args, **kwargs):
        calls.append(1)
        if len(calls) > 1:
            raise OSError("simulated post-save graph failure")
        return sync(*args, **kwargs)

    monkeypatch.setattr(docgraph, "sync", fail_after_initial_sync)
    result = runner.invoke(cli.app, ["docs", "--apply", "d1", "--repo", str(repo), "--json"])
    assert result.exit_code == 0, result.output
    output = json.loads(result.stdout)
    assert output["applied"] == ["d1"] and output["not_applied"] == []
    assert output["docs_graph"]["status"] == "refresh_pending"
    assert output["relationship_comparisons"] == []
    assert output["saves"][0]["saved"] and not output["saves"][0]["graph_refreshed"]
    history = runner.invoke(cli.app, ["docs-saves", "--repo", str(repo), "--json"])
    assert json.loads(history.stdout)["saves"][0]["status"] == "matches_saved"
