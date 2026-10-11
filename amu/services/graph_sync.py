"""Committed-index and worktree-snapshot workflows shared by entry points.

The provider's index is a committed-HEAD cache, not evidence of uncommitted
relationships. A separate snapshot carries worktree evidence. Cache reuse is
limited to validated source-equivalent, documentation-only edits; it does not
replace completion's current test, scope or contract checks.
"""

from __future__ import annotations

from pathlib import Path

from amu import entire, graph_readiness, graph_refresh, state


class GraphSyncError(ValueError):
    """Invalid application input, independent of a transport's error renderer."""

    def __init__(self, message: str, *, exit_code: int = 2):
        super().__init__(message)
        self.exit_code = exit_code
        self.payload = {"error": message, "exit": exit_code}


def _repo(repo: str) -> str:
    """Require an explicit existing directory and canonicalize its aliases."""
    if not isinstance(repo, str) or not repo.strip() or not Path(repo).is_absolute():
        raise GraphSyncError("repo must be an explicit absolute directory")
    try:
        root = Path(repo).resolve(strict=True)
        if not root.is_dir():
            raise GraphSyncError("repo must be an existing directory")
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, GraphSyncError):
            raise
        raise GraphSyncError("repo must be an existing directory") from exc
    return str(root)


def record_index_readiness(repo: str, index: object) -> dict:
    """Persist cache-index health without mistaking it for live map evidence."""
    repo = _repo(repo)
    evidence = {
        **graph_readiness.index_evidence(index),
        "sha": state.git_head(repo),
        "at": state.now_iso(),
    }
    state.write_json("graph-readiness.json", evidence, repo)
    return evidence


def execute(repo: str, *, allow_doc_cache: bool = False) -> dict:
    """Refresh the HEAD cache and persist a separate worktree snapshot.

    Ordinary provider failures are returned as explicit unsuccessful attempt
    records. Unreadable saved state raises ``StateReadError`` unchanged rather
    than being replaced by empty state. Failed attempts never rebind prior
    snapshot bytes to their new source capture.
    """
    if type(allow_doc_cache) is not bool:
        raise GraphSyncError("allow_doc_cache must be a boolean")
    repo = _repo(repo)
    provider_version = entire.graph_version()
    graph_depth = state.read_json("map.json", repo, default={}).get("depth")
    previous = state.read_json("sync-state.json", repo, default={})
    if allow_doc_cache:
        current = graph_refresh.graph_capture(repo)
        old_capture = previous.get("worktree") if isinstance(previous, dict) else None
        if (isinstance(old_capture, dict) and "freshness" in old_capture and not previous.get("error")
                and isinstance(previous.get("readiness"), dict)
                and previous.get("readiness", {}).get("ready") is True
                and isinstance(provider_version, str) and provider_version not in {"missing", ""}
                and previous.get("provider_version") == provider_version
                and previous.get("sha") == current.get("head") and previous.get("index_ref") == "HEAD"
                and previous.get("snapshot_ref") in {"worktree", "code-worktree-cache"}
                and previous.get("graph_depth") == graph_depth
                and graph_refresh.relation_capture_matches(old_capture, current)
                and old_capture.get("digest") != current.get("digest")
                and previous.get("snapshot_hash")
                and previous["snapshot_hash"] == graph_refresh.snapshot_hash(repo)):
            out = {**previous, "snapshot_ref": "code-worktree-cache", "code_cache_reused": True,
                   "current_freshness": current.get("freshness"), "reused_at": state.now_iso(),
                   "note": "source snapshot reused; cached documentation records may describe the earlier prose revision"}
            state.write_json("sync-state.json", out, repo)
            return out
    idx = entire.index(repo) if entire.available() else {"error": "entire missing"}
    readiness = record_index_readiness(repo, idx)
    # Capture next to the snapshot. Refresh independently compares this after
    # its live query and rejects an editor race; a capture alone is no proof.
    snapshot_worktree = graph_refresh.graph_capture(repo)
    snap = (
        entire.snapshot(repo, str(state.amu_dir(repo) / "snapshot.ndjson"), worktree=True)
        if readiness["ready"]
        else {}
    )
    error = (
        idx.get("error") or (None if readiness["ready"] else graph_readiness.summary(readiness)) or snap.get("error")
    )
    out = {
        "sha": state.git_head(repo),
        "index_ref": "HEAD",
        "snapshot_ref": "worktree",
        "counts": idx.get("counts"),
        "snapshot_lines": snap.get("lines"),
        "worktree": snapshot_worktree,
        "provider_version": provider_version,
        "graph_depth": graph_depth,
        "snapshot_hash": graph_refresh.snapshot_hash(repo) if not error else None,
        "code_cache_reused": False,
        "readiness": readiness,
        "at": state.now_iso(),
        "error": error,
    }
    if error:
        # Attempt metadata is never a fresh binding for the previous snapshot.
        out["snapshot_ref"] = "unavailable"
        out["snapshot_lines"] = None
        keys = ("sha", "worktree", "snapshot_hash", "snapshot_lines", "provider_version", "graph_depth", "snapshot_ref", "at")
        if previous and not previous.get("error") and previous.get("snapshot_hash"):
            out["prior_snapshot"] = {key: previous.get(key) for key in keys}
        elif isinstance(previous.get("prior_snapshot"), dict):
            out["prior_snapshot"] = previous["prior_snapshot"]
    state.write_json("sync-state.json", out, repo)
    return out
