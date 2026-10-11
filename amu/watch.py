"""`amu watch`: render node states live while files change. It never runs tests — it renders, it does not verify."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from amu import state

STATE_GLYPH = {"planned": "·", "editing": "▸", "green": "✓", "red": "✗", "drift": "↯"}
_IGNORED = (".amu/", ".git/", "__pycache__/", ".pytest_cache/", ".ruff_cache/")
_IGNORED_PARTS = {".amu", ".git", ".entire", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache"}
_CONTROL_FILES = {".amu/contract.json", ".amu/map.json", ".amu/config.json"}


def resolve_change(path: str, contract: dict, amu_map: dict, phase: int | None) -> dict:
    """Pure: which nodes change state when `path` is saved, and which warning lines to show."""
    if any(seg in path for seg in _IGNORED) or path.endswith((".pyc", ".DS_Store")):
        return {"nodes": {}, "warnings": [], "ignored": True}
    phases = contract.get("phases", [])
    this_phase = {f for p in phases if phase is None or p["n"] <= phase for f in p["files"]}
    later = {f for p in phases if phase is not None and p["n"] > phase for f in p["files"]} - this_phase
    allowed = set(contract.get("allowed_files", []))
    nodes = {n["id"]: n for n in amu_map.get("nodes", [])}
    hit = [nid for nid, n in nodes.items() if n["path"] == path]
    out: dict = {"nodes": {}, "warnings": [], "ignored": False}
    if path in this_phase:
        for nid in hit:
            out["nodes"][nid] = "editing"
        if not hit:
            out["warnings"].append(f"▸ {path} (phase file, no mapped node)")
    elif path in later:
        for nid in hit:
            out["nodes"][nid] = "drift"
        out["warnings"].append(f"↯ PHASE_DRIFT {path}: belongs to a later phase")
    elif allowed and path not in allowed:
        for nid in hit:
            out["nodes"][nid] = "drift"
        out["warnings"].append(f"↯ {path} is outside the contract")
    for nid in hit:
        if nodes[nid]["confidence"] == "unknown":
            out["warnings"].append(f"⚠ UNKNOWN_TOUCHED {path}: graph cannot see its consumers · {nodes[nid]['verify']}")
    return out


def apply(repo: str, resolved: dict) -> dict:
    """Persist only transient watcher states, never a verification verdict.

    ``green`` is exclusively evidence from ``amu check``'s recorded history.
    The watcher observes filesystem saves and is therefore allowed to mark a
    mapped node editing or drifted, but it must not be another path that can
    manufacture a completed phase.
    """
    updates = {
        node_id: value
        for node_id, value in (resolved.get("nodes") or {}).items()
        if value in {"editing", "drift"}
    }

    def apply_updates(raw: object) -> dict:
        st = raw if isinstance(raw, dict) else {"nodes": {}, "violations": [], "history": []}
        nodes = st.get("nodes") if isinstance(st.get("nodes"), dict) else {}
        st["nodes"] = {**nodes, **updates}
        st["updated_at"] = state.now_iso()
        return st

    return state.update_json("state.json", repo, {"nodes": {}, "violations": [], "history": []}, apply_updates)


def _relative_to_root(path: str | Path, root: Path) -> str | None:
    try:
        return str(Path(path).resolve().relative_to(root))
    except ValueError:
        return None


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """Portable watcher state, including enough metadata to detect additions and deletions."""
    seen: dict[str, tuple[int, int]] = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if name not in _IGNORED_PARTS or (Path(directory) == root and name == ".amu")]
        if Path(directory) == root / ".amu":
            dirs[:] = []
            files = [name for name in files if f".amu/{name}" in _CONTROL_FILES]
        for name in files:
            path = Path(directory) / name
            if path.is_symlink() or name.endswith((".pyc", ".DS_Store")):
                continue
            try:
                info = path.stat()
            except OSError:
                continue
            seen[str(path)] = (info.st_mtime_ns, info.st_size)
    return seen


def _poll(root: Path, handle, debounce_ms: int, stop_after: float | None, started: float) -> None:
    """Fallback polling that notices edits, new files, and removals."""
    seen = _snapshot(root)
    while True:
        time.sleep(debounce_ms / 1000)
        current = _snapshot(root)
        changed = set(seen).symmetric_difference(current)
        changed.update(path for path in set(seen).intersection(current) if seen[path] != current[path])
        if changed:
            handle(changed)
        seen = current
        if stop_after and time.time() - started >= stop_after:
            return


def run(repo: str, phase: int | None, on_update, debounce_ms: int = 300, stop_after: float | None = None) -> None:
    """Loop until Ctrl-C (or `stop_after` seconds, for tests). Uses watchfiles when present, else stat polling."""
    root = Path(repo).resolve()
    started = time.time()

    def handle(paths: set[str]) -> None:
        # Plans can be replaced while the watcher runs. Read each event batch
        # against current state, and never reinterpret corruption as no plan.
        contract = state.read_json("contract.json", repo, default={})
        amu_map = state.read_json("map.json", repo, default={"nodes": []})
        for p in sorted(paths):
            rel = _relative_to_root(p, root)
            if rel is None:
                continue
            r = resolve_change(rel, contract, amu_map, phase)
            if r["ignored"]:
                continue
            on_update(rel, apply(repo, r), r["warnings"])

    try:
        from watchfiles import watch as _watch
    except ImportError:
        _poll(root, handle, debounce_ms, stop_after, started)
        return

    stop = threading.Event()
    timer = threading.Timer(stop_after, stop.set) if stop_after else None
    if timer:
        timer.start()
    try:
        # Let watchfiles choose native notifications or its platform fallback.
        # AMU's polling fallback remains available if notifications fail.
        seen = _snapshot(root)
        for changes in _watch(root, debounce=debounce_ms, stop_event=stop, rust_timeout=500, yield_on_timeout=True):
            # Some hosts silently lose native notifications. Reconcile on idle
            # timeouts so a successful watch never depends solely on them.
            current = _snapshot(root)
            paths = {c[1] for c in changes} | set(seen).symmetric_difference(current)
            paths.update(p for p in seen.keys() & current.keys() if seen[p] != current[p])
            if paths:
                handle(paths)
            seen = current
            if stop_after and time.time() - started >= stop_after:
                return
    except (OSError, RuntimeError):
        _poll(root, handle, debounce_ms, stop_after, started)
    finally:
        if timer:
            timer.cancel()
