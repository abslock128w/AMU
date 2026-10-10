"""Workspace: many repos, one active. State lives in ~/.amu/workspace.json (override with AMU_HOME)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from amu import entire, graph_readiness, repository_diagnostics, state


class WorkspaceError(RuntimeError):
    def __init__(self, msg: str, code: int = 2):
        super().__init__(msg)
        self.code = code


def home() -> Path:
    p = Path(os.environ.get("AMU_HOME") or Path.home() / ".amu")
    p.mkdir(mode=0o700, parents=True, exist_ok=True)
    return p


def _ws_path() -> Path:
    return home() / "workspace.json"


def load() -> dict:
    try:
        return _validated(state._read_json_path(_ws_path(), {"active": None, "repos": []}, managed_root=home()))
    except state.StateReadError as exc:
        raise WorkspaceError(f"workspace: {exc}") from exc


def _validated(ws: dict) -> dict:
    if not isinstance(ws, dict) or not isinstance(ws.get("repos"), list) or not isinstance(ws.get("active"), (str, type(None))):
        raise WorkspaceError("workspace: invalid saved state; restore or repair workspace.json")
    version = ws.get("schema_version", 0)
    if type(version) is not int or version not in (0, 1):
        raise WorkspaceError("workspace: unsupported schema version")
    if any(not isinstance(r, dict) or not isinstance(r.get("name"), str) or not isinstance(r.get("path"), str) for r in ws["repos"]):
        raise WorkspaceError("workspace: invalid repository record")
    return {**ws, "schema_version": 1}


def save(ws: dict) -> None:
    """Explicit snapshot replacement; use update() for read/modify/write operations."""
    path = _ws_path()
    with state._json_write_lock(path, managed_root=home()):
        state._write_json_atomic(path, _validated(ws), managed_root=home())


def update(change: Callable[[dict], dict]) -> dict:
    """Serialize workspace mutations, including their read, across processes."""
    path = _ws_path()
    with state._json_write_lock(path, managed_root=home()):
        updated = _validated(change(load()))
        state._write_json_atomic(path, updated, managed_root=home())
        return updated


def _git(args: list[str], cwd: str | Path | None = None) -> tuple[int, str]:
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=600)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return 1, str(exc)
    return r.returncode, (r.stdout if r.returncode == 0 else r.stderr).strip()


def git_toplevel(path: str | Path) -> str | None:
    rc, out = _git(["rev-parse", "--show-toplevel"], path)
    return out if rc == 0 else None


def _name_for(path: Path, origin: str) -> str:
    m = re.search(r"([^/:]+)/([^/]+?)(?:\.git)?/?$", origin or "")
    return (m.group(2) if m else path.name).lower()


GITHUB_RX = re.compile(r"^(?:https?://github\.com/|git@github\.com:)?([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")


def is_remote_spec(spec: str) -> bool:
    return not Path(spec).expanduser().exists() and bool(GITHUB_RX.match(spec))


def clone(spec: str, branch: str | None = None, depth: int | None = None) -> Path:
    """Clone into ~/.amu/repos/<owner>__<repo>; reuse an existing clone with fetch. Uses the user's git credentials."""
    m = GITHUB_RX.match(spec)
    if not m:
        raise WorkspaceError(f"not a GitHub spec: {spec}", 2)
    owner, repo = m.group(1), m.group(2)
    url = spec if spec.startswith(("http", "git@")) else f"https://github.com/{owner}/{repo}.git"
    dest = home() / "repos" / f"{owner}__{repo}"
    if dest.exists() and git_toplevel(dest):
        rc, out = _git(["fetch", "--quiet"], dest)
        if rc != 0:
            raise WorkspaceError(f"git fetch failed in {dest}: {out}", 3)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["clone", "--quiet"] + (["--branch", branch] if branch else []) + (["--depth", str(depth)] if depth else []) + [url, str(dest)]
    rc, out = _git(cmd)
    if rc != 0:
        raise WorkspaceError(f"command failed: git {' '.join(cmd)}\n{out}", 3)
    return dest


def index_repo(path: Path) -> dict:
    """Build Entire's committed-HEAD index and save the checked-out worktree snapshot."""
    idx = entire.index(str(path))
    readiness = {
        **graph_readiness.index_evidence(idx),
        "sha": state.git_head(str(path)),
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    state.write_json("graph-readiness.json", readiness, str(path))
    if not readiness["ready"]:
        raise WorkspaceError(f"graph index is not semantically ready: {graph_readiness.summary(readiness)}", 3)
    # A newly registered or refreshed workspace must reflect its checked-out
    # files, including local edits that are not committed yet.
    snap = entire.snapshot(str(path), str(state.amu_dir(str(path)) / "snapshot.ndjson"), worktree=True)
    if "error" in snap:
        raise WorkspaceError(f"entire graph snapshot failed: {snap['error']}", 3)
    if not state.read_json("config.json", str(path)):
        state.write_json("config.json", {**state.config(str(path)), **state.detect_commands(str(path))}, str(path))
    return idx


def add(spec: str, branch: str | None = None, depth: int | None = None, do_index: bool = True) -> dict:
    if is_remote_spec(spec):
        path = clone(spec, branch, depth)
        selection = None
    else:
        path = Path(spec).expanduser().resolve()
        # Registration is an operator action, not an MCP grant. Validate the
        # exact local root without status, filters, hooks or project commands
        # before either index initialization or global workspace mutation.
        try:
            selection = repository_diagnostics.select_local_repository(str(path))
        except (repository_diagnostics.RepositoryDiagnosticError, repository_diagnostics.AccessError) as exc:
            raise WorkspaceError(str(exc), 2) from exc
    top = selection.root if selection is not None else git_toplevel(path)
    if not top:
        raise WorkspaceError(f"{path} is not inside a Git worktree (git rev-parse --show-toplevel failed)", 2)
    path = Path(top)
    if selection is not None:
        try:
            labels = repository_diagnostics.registration_metadata(str(path))
        except repository_diagnostics.RepositoryDiagnosticError as exc:
            raise WorkspaceError(str(exc), 2) from exc
        origin, br = labels["origin"], labels["branch"]
    else:
        _, origin = _git(["remote", "get-url", "origin"], path)
        _, br = _git(["rev-parse", "--abbrev-ref", "HEAD"], path)
    idx = index_repo(path) if do_index else {}
    counts = idx.get("counts", {})
    entry = {"name": _name_for(path, origin), "path": str(path), "origin": origin if "fatal" not in origin else "",
             "indexed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if do_index else "", "graph_hash": idx.get("tree", ""),
             "sha": (selection.head or "no-git") if selection is not None else state.git_head(str(path)),
             "counts": counts, "last_map": "", "branch": br}
    def register(ws):
        ws["repos"] = [r for r in ws["repos"] if r["path"] != entry["path"] and r["name"] != entry["name"]] + [entry]
        ws["active"] = entry["name"]
        return ws
    update(register)
    return entry


def get(name: str) -> dict | None:
    return next((r for r in load()["repos"] if r["name"] == name), None)


def use(name: str) -> dict:
    def select(ws):
        entry = next((r for r in ws["repos"] if r["name"] == name), None)
        if entry is None:
            raise WorkspaceError(f"no repo named {name}; see amu repo list", 2)
        try:
            repository_diagnostics.select_local_repository(entry["path"])
        except (repository_diagnostics.RepositoryDiagnosticError, repository_diagnostics.AccessError) as exc:
            raise WorkspaceError(str(exc), 2) from exc
        ws["active"] = name
        return ws
    return next(r for r in update(select)["repos"] if r["name"] == name)


def _managed_clone_path(path: str) -> Path | None:
    """Return a safe, resolved clone directory below AMU's managed clone root."""
    try:
        # Check both the lexical path and its resolved target.  The former rejects
        # sibling-prefix paths such as ``repos-evil``; the latter rejects a link
        # below ``repos`` that leads somewhere else.
        managed = Path(os.path.abspath(home() / "repos"))
        candidate = Path(os.path.abspath(Path(path).expanduser()))
        candidate.relative_to(managed)
        if managed.is_symlink() or candidate.is_symlink():
            return None
        resolved_managed = managed.resolve(strict=True)
        resolved_candidate = candidate.resolve(strict=True)
        resolved_candidate.relative_to(resolved_managed)
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    if resolved_candidate == resolved_managed or not resolved_candidate.is_dir():
        return None
    return resolved_candidate


def remove(name: str, delete_clone: bool = False) -> dict:
    removed = []
    def unregister(ws):
        removed.extend(x for x in ws["repos"] if x["name"] == name)
        if not removed:
            raise WorkspaceError(f"no repo named {name}", 2)
        ws["repos"] = [x for x in ws["repos"] if x["name"] != name]
        if ws["active"] == name:
            ws["active"] = ws["repos"][-1]["name"] if ws["repos"] else None
        return ws
    update(unregister)
    r = removed[0]
    deleted = False
    clone_path = _managed_clone_path(r["path"]) if delete_clone else None
    if clone_path:
        shutil.rmtree(clone_path)
        deleted = True
    return {**r, "deleted_clone": deleted}


def _fast_forward_tracked_branch(path: str | Path) -> bool:
    """Fast-forward the checked-out branch when it has an upstream branch."""
    rc, upstream = _git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"], path)
    if rc != 0 or not upstream:
        return False
    rc, out = _git(["merge", "--ff-only", "--", upstream], path)
    if rc != 0:
        raise WorkspaceError(f"git fast-forward failed in {path}: {out}", 3)
    return True


def sync(name: str) -> dict:
    r = get(name)
    if not r:
        raise WorkspaceError(f"no repo named {name}", 2)
    rc, out = _git(["fetch", "--quiet"], r["path"])
    if rc != 0:
        raise WorkspaceError(f"git fetch failed in {r['path']}: {out}", 3)
    _fast_forward_tracked_branch(r["path"])
    old = r.get("sha") or "HEAD~1"
    idx = index_repo(Path(r["path"]))
    new = state.git_head(r["path"])
    diff = entire.diff_json(r["path"], old, new) if old != new else {"files": []}
    if diff is None or diff.get("error"):
        detail = diff.get("error") if isinstance(diff, dict) else "complete graph change evidence unavailable"
        raise WorkspaceError(f"entire graph diff failed: {detail}; previous workspace registration retained", 3)
    changes = sum(len(f.get("changes", [])) for f in diff.get("files", []))
    r.update({"indexed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "graph_hash": idx.get("tree", ""), "sha": new, "counts": idx.get("counts", {})})
    def record_sync(ws):
        current = next((x for x in ws["repos"] if x["name"] == name), None)
        if not current or current["path"] != r["path"]:
            raise WorkspaceError("workspace repository changed during sync; its registration was not overwritten")
        ws["repos"] = [{**x, **r, "last_map": x.get("last_map", "")} if x["name"] == name else x for x in ws["repos"]]
        return ws
    update(record_sync)
    return {**r, "old_sha": old, "entity_changes": changes, "files_changed": len(diff.get("files", []))}


def touch(path: str, last_map: str | None = None) -> None:
    """Best-effort recency metadata; mapping a direct repo must not require a writable workspace home."""
    try:
        def record_map(ws):
            for r in ws["repos"]:
                if r["path"] == str(Path(path).resolve()) and last_map:
                    r["last_map"] = last_map
            return ws
        update(record_map)
    except OSError:
        # Workspace registration is optional for `amu map --repo`; preserve
        # the graph result when the global workspace directory is read-only.
        return


def resolve(repo_flag: str | None) -> str:
    """--repo flag → repo containing $PWD → workspace active → error with the add hint."""
    if repo_flag and repo_flag != ".":
        return repo_flag
    top = git_toplevel(os.getcwd())
    if top:
        return top
    ws = load()
    if ws.get("active"):
        r = get(ws["active"])
        if r and Path(r["path"]).exists():
            return r["path"]
    raise WorkspaceError("no repo: not inside a Git worktree and no active workspace repo — run `amu repo add <path|owner/repo>`", 2)


def relative(ts: str) -> str:
    if not ts:
        return "never"
    try:
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return ts
    s = int((datetime.now(timezone.utc) - dt).total_seconds())
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            return f"{s // n}{unit} ago"
    return f"{s}s ago"
