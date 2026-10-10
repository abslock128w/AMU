"""Journaled, optimistic documentation saves and conservative patch undo."""

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from uuid import uuid4

from amu import docgraph, state


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _target(repo: str, filename: str) -> Path:
    from amu import docs

    path = docs._safe_mapped_doc_path({"file": filename}, repo)
    lexical = Path(repo).resolve() / filename
    if path is None or path != lexical or lexical.is_symlink():
        raise ValueError("save target must be a regular document inside docs_dir, without symlinks")
    return path


def _temporary(path: Path, content: bytes, mode: int) -> Path:
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _write_record(repo: str, record: dict) -> None:
    directory = state.amu_dir(repo) / "doc-saves"
    if directory.is_symlink():
        raise ValueError("save journal directory must not be a symlink")
    directory.mkdir(exist_ok=True)
    path = directory / f"{record['id']}.json"
    temporary = _temporary(path, (json.dumps(record) + "\n").encode(), 0o600)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save(repo: str, path: Path, expected: str, start: int, end: int, replacement: str,
         *, undo_of: str | None = None) -> dict:
    """Serialize AMU writers; recheck external edits immediately before replacement.

    Editors that ignore this lock can still race the final check/rename pair.
    The write-ahead journal preserves the expected original and proposed text.
    """
    filename = path.relative_to(Path(repo).resolve()).as_posix()
    with state._json_write_lock(state.amu_dir(repo) / "doc-save-transaction"):
        path = _target(repo, filename)
        if not 0 <= start <= end <= len(expected):
            raise ValueError("invalid documentation patch boundaries")
        if path.read_bytes().decode("utf-8") != expected:
            raise ValueError("document changed before saving; review it again")
        before_stat = path.stat()
        updated = expected[:start] + replacement + expected[end:]
        if updated == expected:
            raise ValueError("documentation patch is unchanged")
        record = {
            "schema": 1, "id": uuid4().hex, "repo": str(Path(repo).resolve()), "file": filename,
            "at": state.now_iso(), "status": "prepared", "graph_refreshed": False,
            "before": expected, "after": updated, "before_hash": _hash(expected), "after_hash": _hash(updated),
            "start": start, "end": end, "replacement": replacement, "undo_of": undo_of,
        }
        # No document write occurs unless its recovery record was written first.
        _write_record(repo, record)
        temporary = _temporary(path, updated.encode("utf-8"), stat.S_IMODE(before_stat.st_mode))
        try:
            current = _target(repo, filename)
            current_stat = current.stat()
            if (current.read_bytes().decode("utf-8") != expected
                    or (current_stat.st_dev, current_stat.st_ino, current_stat.st_mtime_ns, current_stat.st_size)
                    != (before_stat.st_dev, before_stat.st_ino, before_stat.st_mtime_ns, before_stat.st_size)):
                raise ValueError("document changed during saving; no patch applied")
            os.replace(temporary, current)
        finally:
            temporary.unlink(missing_ok=True)
        result = {"saved": True, "save_id": record["id"], "file": filename,
                  "graph_refreshed": False, "graph_error": None, "journal_error": None,
                  "undo_of": undo_of}
        record["status"] = "saved"
        try:
            docgraph.sync(repo)
            result["graph_refreshed"] = True
        except Exception as exc:
            result["graph_error"] = f"{type(exc).__name__}: {exc}"
        record.update({k: result[k] for k in ("graph_refreshed", "graph_error")})
        try:
            _write_record(repo, record)
        except Exception as exc:
            # Never relabel an already-replaced document as 'not applied'.
            result["journal_error"] = f"save succeeded; final journal update failed ({type(exc).__name__})"
        return result


def _load(repo: str, save_id: str) -> dict:
    if not isinstance(save_id, str) or not re.fullmatch(r"[0-9a-f]{32}", save_id):
        raise ValueError("invalid documentation save id")
    path = state.amu_dir(repo) / "doc-saves" / f"{save_id}.json"
    if path.is_symlink():
        raise ValueError("save journal must not be a symlink")
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError("invalid documentation save journal")
    if (type(record.get("schema")) is not int or record["schema"] != 1 or record.get("id") != save_id
            or record.get("repo") != str(Path(repo).resolve())
            or not all(isinstance(record.get(k), str) for k in ("file", "before", "after"))
            or _hash(record["before"]) != record.get("before_hash")
            or _hash(record["after"]) != record.get("after_hash")):
        raise ValueError("invalid documentation save journal")
    return record


def history(repo: str) -> list[dict]:
    rows = []
    for path in sorted((state.amu_dir(repo) / "doc-saves").glob("*.json")):
        try:
            record = _load(repo, path.stem)
            current = _target(repo, record["file"]).read_bytes().decode("utf-8")
            status = "matches_saved" if current == record["after"] else (
                "matches_original" if current == record["before"] else "changed_since_save")
            rows.append({"save_id": record["id"], "file": record["file"], "status": status,
                         "graph_refreshed": record.get("graph_refreshed", False),
                         "recorded_status": record["status"], "undo_of": record.get("undo_of")})
        except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
            rows.append({"save_id": path.stem, "status": "unavailable", "error": str(exc)})
    return rows


def undo(repo: str, save_id: str) -> dict:
    record = _load(repo, save_id)
    path = _target(repo, record["file"])
    # Conservative: a subsequent edit blocks undo, rather than guessing where
    # a patch moved or overwriting the developer's newer work.
    return save(repo, path, record["after"], 0, len(record["after"]), record["before"], undo_of=save_id)
