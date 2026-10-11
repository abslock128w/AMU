"""Editable documentation proposals bound to a candidate and original document."""

from __future__ import annotations

import difflib
import os
import re
from pathlib import Path
from uuid import uuid4

from amu import docgraph, docs, docsave, state, state_io

_MAX_DRAFT_BYTES = 1_000_000


def _read_draft(path: Path) -> str:
    """Read an owned draft through pinned managed-directory descriptors.

    An editor may replace the regular file before this read begins. Changes
    observed while it is opened invalidate the result before it is returned.
    """
    message = "review draft must be a regular file owned by you, without links, no larger than 1 MB"
    root = path.parent.parent
    if root.name != state.AMU_DIR or path.parent.name != "doc-reviews":
        raise ValueError(message)
    try:
        with state_io._directory(path, root, create=False) as directory:
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=directory.descriptor)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                state_io._regular(path, info)
                if info.st_size > _MAX_DRAFT_BYTES:
                    raise ValueError(message)
                content = stream.read(_MAX_DRAFT_BYTES + 1)
                after = os.fstat(stream.fileno())
                current = directory.target(path.name)
                fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode", "st_nlink", "st_uid")
                if (any(getattr(info, field) != getattr(after, field) for field in fields)
                        or any(getattr(info, field) != getattr(current, field) for field in fields)
                        or len(content) != info.st_size):
                    raise ValueError("review draft changed while being read; retry inspection")
                directory.verify()
    except (OSError, state_io.StateIOError) as exc:
        raise ValueError(message) from exc
    if len(content) > _MAX_DRAFT_BYTES:
        raise ValueError(message)
    return content.decode("utf-8")


def _replacement(edited: str, text: str, start: int) -> str:
    replacement = edited.rstrip() + "\n\n"
    if start and text[start - 1] not in "\r\n":
        replacement = "\n" + replacement
    return replacement


def select(candidate: dict, target_index: int | None = None) -> dict:
    targets = candidate.get("targets") or []
    if target_index is None:
        if len(targets) != 1:
            raise ValueError("choose one mapped section with --target-index (1-based)")
        return candidate
    if type(target_index) is not int or not 1 <= target_index <= len(targets):
        raise ValueError("target-index must identify one mapped section (1-based)")
    return {**candidate, "targets": [targets[target_index - 1]]}


def _directory(repo: str, *, create: bool = True) -> Path:
    root = state.amu_dir(repo) if create else Path(repo) / state.AMU_DIR
    if root.is_symlink():
        raise state.StateReadError(".amu: symbolic-link state directories are not supported")
    path = root / "doc-reviews"
    if path.is_symlink():
        raise ValueError("review directory must not be a symbolic link")
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    else:
        # Validate existing directory components with the same no-follow,
        # ownership and permissions boundary used for managed JSON reads.
        # Absence is an empty queue, not permission to initialize state.
        try:
            with state_io._directory(path / "inspection.json", root, create=False):
                pass
        except FileNotFoundError:
            pass
        except state_io.StateIOError as exc:
            raise state.StateReadError(str(exc)) from exc
    return path


def _saved(review_id: str, repo: str) -> tuple[dict, Path]:
    if not re.fullmatch(r"[0-9a-f]{16}", review_id):
        raise ValueError("invalid review id")
    directory = _directory(repo, create=False)
    metadata = state._read_json_path(directory / f"{review_id}.json", default={}, managed_root=Path(repo) / state.AMU_DIR)
    if not isinstance(metadata, dict) or metadata.get("fingerprint") != review_id or not isinstance(metadata.get("target"), dict):
        raise ValueError("review metadata is missing or invalid")
    suffix = Path(str(metadata["target"].get("file", ""))).suffix.lower()
    if suffix not in {".md", ".mdx", ".rst"}:
        raise ValueError("unsupported review document type")
    draft = directory / f"{review_id}{suffix}"
    if draft.is_symlink() or not draft.is_file() or draft.stat().st_size > _MAX_DRAFT_BYTES:
        raise ValueError("review draft must be a regular file no larger than 1 MB")
    return metadata, draft


def preview(review_id: str, repo: str) -> dict:
    metadata, draft = _saved(review_id, repo)
    _, text, start, end = _region({"targets": [metadata["target"]]}, repo)
    edited = _read_draft(draft)
    original = metadata.get("original", text[start:end])
    if not isinstance(original, str):
        raise ValueError("invalid original review text")
    expected_hash = metadata.get("saved_document_hash") if metadata.get("status") == "saved" else metadata.get("document_hash")
    conflict = expected_hash != state.stable_hash(text)
    saved_hash = metadata.get("saved_draft_hash")
    # Older saves did not record a draft hash: compare their normalized patch
    # with the current section instead of assuming all later edits are saved.
    saved_draft = (metadata.get("status") == "saved"
                   and (saved_hash == state.stable_hash(edited) if saved_hash is not None
                        else _replacement(edited, text, start) == text[start:end]))
    status = ("conflict" if conflict else "saved" if saved_draft
              else "edited" if metadata.get("status") == "saved" or edited != original else "prepared")
    return {"review_id": review_id, "candidate": metadata.get("candidate"), "target": metadata["target"],
            "file": str(draft.absolute()), "status": status,
            "diff": "".join(difflib.unified_diff(original.splitlines(keepends=True), edited.splitlines(keepends=True),
                                                   fromfile="original section", tofile="review draft")),
            "note": "Preview only. Saving requires the current candidate and explicit review apply."}


def queue(repo: str) -> list[dict]:
    rows = []
    path = _directory(repo, create=False)
    opened = False
    try:
        with state_io._directory(path / "inspection.json", Path(repo) / state.AMU_DIR, create=False) as directory:
            opened = True
            names = sorted(name for name in os.listdir(directory.descriptor) if name.endswith(".json"))
            directory.verify()
            for name in names:
                review_id = Path(name).stem
                try:
                    row = preview(review_id, repo)
                    rows.append({k: v for k, v in row.items() if k != "diff"})
                except (OSError, ValueError) as exc:
                    rows.append({"review_id": review_id, "status": "unavailable", "reason": str(exc)})
            directory.verify()
    except FileNotFoundError as exc:
        if opened:
            raise state.StateReadError("review directory changed during inspection; retry") from exc
    except state_io.StateIOError as exc:
        raise state.StateReadError(str(exc)) from exc
    except OSError as exc:
        raise state.StateReadError("review directory could not be inspected without following aliases") from exc
    return rows


def discard(review_id: str, repo: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{16}", review_id):
        raise ValueError("invalid review id")
    directory = _directory(repo)
    # Use the same metadata lock as prepare; archive instead of destroying prose.
    with state._json_write_lock(directory / f"{review_id}.json"):
        _, draft = _saved(review_id, repo)
        archive_root = state.amu_dir(repo) / "doc-review-archive"
        if archive_root.is_symlink():
            raise ValueError("review archive must not be a symbolic link")
        archive = archive_root / uuid4().hex
        archive.mkdir(mode=0o700, parents=True)
        archived_draft = archive / draft.name
        draft.rename(archived_draft)
        try:
            (directory / f"{review_id}.json").rename(archive / f"{review_id}.json")
        except BaseException as exc:
            try:
                # Exclusive restoration never overwrites a new developer file.
                os.link(archived_draft, draft, follow_symlinks=False)
                archived_draft.unlink()
                archive.rmdir()
            except OSError:
                raise OSError(f"discard did not finish; draft preserved at {archived_draft}") from exc
            raise
    return {"review_id": review_id, "status": "discarded", "archive": str(archive.resolve()),
            "note": "Draft and metadata archived; documentation was not changed."}


def _region(candidate: dict, repo: str) -> tuple[Path, str, int, int]:
    targets = candidate.get("targets") or []
    if len(targets) != 1:
        raise ValueError("review requires one mapped section; narrow the doc-map target first")
    target = targets[0]
    path = docs._safe_mapped_doc_path(target, repo)
    if path is None:
        raise ValueError("mapped document is missing or outside docs_dir")
    text = path.read_bytes().decode("utf-8")
    if not target.get("section"):
        return path, text, 0, len(text)
    sections = docgraph._sections(target["file"], text)
    section = docgraph._mapped_section(target, sections)
    if section is None or section.get("heading") is None:
        raise ValueError("mapped section is missing or ambiguous; choose a unique section anchor")
    if section.get("structure_error"):
        raise ValueError(section["structure_error"])
    lines = text.splitlines(keepends=True)
    start = section["body_start_line"] - 1
    end = section["body_end_line"]
    return path, text, sum(map(len, lines[:start])), sum(map(len, lines[:end]))


def prepare(candidate: dict, repo: str, *, target_index: int | None = None) -> dict:
    """Save an editable section body; metadata stays separate from the prose."""
    candidate = select(candidate, target_index)
    path, text, start, end = _region(candidate, repo)
    identity = docs.candidate_fingerprint(candidate)
    suffix = path.suffix.lower()
    name = f"doc-reviews/{identity}{suffix}"
    draft_path = _directory(repo) / f"{identity}{suffix}"
    metadata_name = f"doc-reviews/{identity}.json"
    original = text[start:end]
    proposed = original
    old, new = candidate.get("old"), candidate.get("new")
    # Only replace an exact evidenced signature. Behavior changes require
    # human prose; preserving the section is safer than inventing semantics.
    if candidate.get("confidence") != "unknown" and isinstance(old, str) and old and isinstance(new, str):
        proposed = original.replace(old, new)
    if len(proposed.encode("utf-8")) > _MAX_DRAFT_BYTES:
        raise ValueError("review draft must be no larger than 1 MB; choose a smaller mapped section")
    metadata = {
        "schema": 2,
        "region": {"start": start, "end": end},
        "fingerprint": identity,
        "target": candidate["targets"][0],
        "document_hash": state.stable_hash(text),
        "draft": name,
        "original": original,
        "candidate": candidate["candidate"],
        "status": "prepared",
    }
    metadata_path = state.amu_dir(repo) / metadata_name
    with state._json_write_lock(metadata_path):
        if draft_path.exists() or draft_path.is_symlink() or metadata_path.exists() or metadata_path.is_symlink():
            raise ValueError(f"a review draft already exists at {draft_path}; finish it or move it aside before preparing another")
        fd = os.open(draft_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = None
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(proposed)
                stream.flush()
                os.fsync(stream.fileno())
                created = os.fstat(stream.fileno())
            state._write_json_atomic(metadata_path, metadata)
        except BaseException:
            # Roll back only the unchanged file this prepare created. An editor
            # may have already replaced/edited it; preserve that developer prose.
            try:
                current = draft_path.lstat()
                if (created is not None
                        and (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
                        == (created.st_dev, created.st_ino, created.st_size, created.st_mtime_ns)):
                    draft_path.unlink()
            except OSError:
                pass
            raise
    return {
        "file": str(draft_path.resolve()),
        "candidate": candidate["id"],
        "review_id": identity,
        "target": candidate["targets"][0],
        "suggestion": docs.draft(candidate),
        "next": f"edit the draft body, then run amu docs --apply {candidate['id']} --review" + (f" --target-index {target_index}" if target_index is not None else ""),
    }


def apply(candidate: dict, repo: str, *, detailed: bool = False, target_index: int | None = None) -> bool | dict:
    candidate = select(candidate, target_index)
    identity = docs.candidate_fingerprint(candidate)
    metadata_path = _directory(repo) / f"{identity}.json"
    # The draft must stay active until both the document and its review status
    # are saved. Discard/prepare use this same lock for the whole lifecycle.
    with state._json_write_lock(metadata_path):
        metadata = state.read_json(f"doc-reviews/{identity}.json", repo, default={})
        if not isinstance(metadata, dict) or metadata.get("fingerprint") != identity:
            raise ValueError("no review draft exists for this candidate; use --prepare first")
        path, text, start, end = _region(candidate, repo)
        if metadata.get("target") != candidate["targets"][0] or metadata.get("document_hash") != state.stable_hash(text):
            raise ValueError("the document changed since preparation; prepare a fresh review")
        if metadata.get("schema") != 2 or metadata.get("region") != {"start": start, "end": end}:
            raise ValueError("review section boundaries changed; prepare a fresh review")
        # Derive the path ourselves; editable metadata cannot redirect file reads.
        _, draft_path = _saved(identity, repo)
        if draft_path.is_symlink():
            raise ValueError("review draft must be a regular file")
        replacement = _read_draft(draft_path)
        if not replacement.strip():
            raise ValueError("review draft is empty")
        if replacement == text[start:end]:
            raise ValueError("review draft is unchanged; edit the proposed section first")
        saved_draft_hash = state.stable_hash(replacement)
        replacement = _replacement(replacement, text, start)
        result = docsave.save(repo, path, text, start, end, replacement)
        if result["saved"]:
            try:
                state._write_json_atomic(metadata_path, {
                    **metadata, "status": "saved", "save_id": result["save_id"],
                    "saved_draft_hash": saved_draft_hash,
                    "saved_document_hash": state.stable_hash(text[:start] + replacement + text[end:]),
                })
            except (OSError, ValueError) as exc:
                result["review_status_error"] = str(exc)
    return result if detailed else result["saved"]
