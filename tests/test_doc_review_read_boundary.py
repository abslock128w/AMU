"""Temporal managed-directory changes cannot redirect inspection reads."""

import os
import stat
from pathlib import Path

import pytest

from amu import docreview, state
from tests.test_doc_queue import candidate


@pytest.mark.parametrize("operation", ["preview", "apply"])
def test_review_parent_alias_after_metadata_check_never_supplies_draft_bytes(tmp_path, monkeypatch, operation):
    root = tmp_path / "repo"
    root.mkdir()
    c = candidate(root)
    prepared = docreview.prepare(c, str(root), target_index=1)
    original_draft = Path(prepared["file"]).read_bytes()
    reviews = root / ".amu" / "doc-reviews"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / Path(prepared["file"]).name).write_text("Private outside draft bytes.\n")
    saved = docreview._saved

    def replace_parent(*args):
        result = saved(*args)
        reviews.rename(tmp_path / "original-reviews")
        reviews.symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(docreview, "_saved", replace_parent)
    with pytest.raises((ValueError, state.StateReadError)):
        if operation == "preview":
            docreview.preview(prepared["review_id"], str(root))
        else:
            docreview.apply(c, str(root), target_index=1)
    assert "Private outside" not in (root / "docs/a.md").read_text()
    assert (tmp_path / "original-reviews" / Path(prepared["file"]).name).read_bytes() == original_draft


def test_review_parent_alias_during_draft_open_is_rejected(tmp_path, monkeypatch):
    prepared = docreview.prepare(candidate(tmp_path), str(tmp_path), target_index=1)
    draft = Path(prepared["file"])
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / draft.name).write_text("Private outside draft bytes.\n")
    opening = docreview.os.open
    replaced = False

    def replace_before_open(path, *args, **kwargs):
        nonlocal replaced
        if not replaced and os.fspath(path) in {str(draft), draft.name}:
            replaced = True
            draft.parent.rename(tmp_path / "original-reviews")
            draft.parent.symlink_to(outside, target_is_directory=True)
        return opening(path, *args, **kwargs)

    monkeypatch.setattr(docreview.os, "open", replace_before_open)
    with pytest.raises((ValueError, state.StateReadError)):
        docreview.preview(prepared["review_id"], str(tmp_path))
    assert replaced


def test_queue_parent_alias_after_directory_check_never_enumerates_outside_names(tmp_path, monkeypatch):
    managed = tmp_path / ".amu"
    reviews = managed / "doc-reviews"
    reviews.mkdir(mode=0o700, parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private-review-metadata.json").write_text("{}")
    directory = docreview._directory

    def replace_after_validation(*args, **kwargs):
        result = directory(*args, **kwargs)
        reviews.rename(tmp_path / "original-reviews")
        reviews.symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(docreview, "_directory", replace_after_validation)
    with pytest.raises((ValueError, state.StateReadError)):
        docreview.queue(str(tmp_path))


def test_queue_parent_alias_during_listing_is_rejected_without_outside_names(tmp_path, monkeypatch):
    prepared = docreview.prepare(candidate(tmp_path), str(tmp_path), target_index=1)
    reviews = Path(prepared["file"]).parent
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private-review-metadata.json").write_text("{}")
    listing = docreview.os.listdir
    replaced = False

    def replace_during_listing(path):
        nonlocal replaced
        if isinstance(path, int) and not replaced:
            replaced = True
            reviews.rename(tmp_path / "original-reviews")
            reviews.symlink_to(outside, target_is_directory=True)
        return listing(path)

    monkeypatch.setattr(docreview.os, "listdir", replace_during_listing)
    with pytest.raises((ValueError, state.StateReadError)):
        docreview.queue(str(tmp_path))
    assert replaced


def test_review_hardlinked_draft_cannot_supply_other_file_contents(tmp_path):
    prepared = docreview.prepare(candidate(tmp_path), str(tmp_path), target_index=1)
    draft = Path(prepared["file"])
    private = tmp_path / "private.txt"
    private.write_text("Private linked file contents.\n")
    draft.unlink()
    os.link(private, draft)
    with pytest.raises(ValueError, match="regular file"):
        docreview.preview(prepared["review_id"], str(tmp_path))


def test_owned_regular_editor_replacement_is_still_previewable_and_applicable(tmp_path):
    c = candidate(tmp_path)
    prepared = docreview.prepare(c, str(tmp_path), target_index=1)
    draft = Path(prepared["file"])
    replacement = tmp_path / "editor-replacement.md"
    replacement.write_text("Reviewed editor replacement.\n")
    replacement.replace(draft)
    assert "+Reviewed editor replacement." in docreview.preview(prepared["review_id"], str(tmp_path))["diff"]
    assert docreview.apply(c, str(tmp_path), target_index=1)
    assert "Reviewed editor replacement." in (tmp_path / "docs/a.md").read_text()


def test_draft_fifo_is_rejected_before_reading(tmp_path):
    prepared = docreview.prepare(candidate(tmp_path), str(tmp_path), target_index=1)
    draft = Path(prepared["file"])
    draft.unlink()
    os.mkfifo(draft)
    with pytest.raises(ValueError, match="regular file"):
        docreview._read_draft(draft)


def test_draft_owned_by_another_user_is_rejected(tmp_path, monkeypatch):
    prepared = docreview.prepare(candidate(tmp_path), str(tmp_path), target_index=1)
    draft = Path(prepared["file"])
    fstat = docreview.os.fstat

    def foreign_owner(descriptor):
        info = fstat(descriptor)
        if stat.S_ISREG(info.st_mode):
            values = list(info)
            values[4] = info.st_uid + 1
            return os.stat_result(values)
        return info

    monkeypatch.setattr(docreview.os, "fstat", foreign_owner)
    with pytest.raises(ValueError, match="regular file"):
        docreview._read_draft(draft)


def test_review_queue_rejects_shared_writable_managed_directory(tmp_path):
    managed = tmp_path / ".amu"
    managed.mkdir(mode=0o700)
    managed.chmod(0o777)
    with pytest.raises(state.StateReadError):
        docreview.queue(str(tmp_path))
    assert not (managed / "doc-reviews").exists()
