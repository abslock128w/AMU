"""Documentation workflows without command-line or presentation dependencies.

Requests express the caller's explicit operation. Transport-specific consent,
rendering and repository selection remain the entry point's responsibility.
Candidate freshness, safe document writes and recovery remain enforced by the
same underlying documentation modules used by every entry point.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from amu import docgraph, docreview, docsave, entire, state
from amu import docs as docsmod

_MODES = frozenset({"flag", "draft", "auto"})


class DocumentationError(ValueError):
    """An expected application failure, with a transport-neutral error payload."""

    def __init__(self, message: str, *, exit_code: int = 2, payload: dict | None = None):
        super().__init__(message)
        self.exit_code = exit_code
        self.payload = {"error": message, "exit": exit_code} if payload is None else dict(payload)


@dataclass(frozen=True)
class DocsRequest:
    """One candidate-list, draft-prepare or explicitly selected apply operation."""

    mode: str | None = None
    apply: str | None = None
    prepare: str | None = None
    review: bool = False
    target_index: int | None = None


def _repo(repo: str) -> str:
    """Require an explicitly selected repository; never default to the cwd."""
    if not isinstance(repo, str) or not repo.strip() or not Path(repo).is_absolute():
        raise DocumentationError("repo must be an explicit absolute directory")
    try:
        root = Path(repo).resolve(strict=True)
        if not root.is_dir():
            raise DocumentationError("repo must be an existing directory")
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, DocumentationError):
            raise
        raise DocumentationError("repo must be an existing directory") from exc
    return str(root)


def _optional_text(value: object, option: str) -> None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise DocumentationError(f"{option} must be a nonempty string")


def _mode(value: object) -> str:
    if not isinstance(value, str) or value not in _MODES:
        raise DocumentationError(f"invalid docs mode {value!r}; choose one of: {', '.join(sorted(_MODES))}")
    return value


def _validate(request: DocsRequest) -> None:
    if not isinstance(request, DocsRequest):
        raise DocumentationError("request must be a DocsRequest")
    if request.mode is not None:
        _mode(request.mode)
    _optional_text(request.apply, "--apply")
    _optional_text(request.prepare, "--prepare")
    if type(request.review) is not bool:
        raise DocumentationError("--review must be a boolean")
    if request.target_index is not None and (type(request.target_index) is not int or request.target_index < 1):
        raise DocumentationError("--target-index must be an integer of at least 1")
    if (request.prepare is not None and request.apply is not None) or (request.review and request.apply is None):
        raise DocumentationError("use --prepare dN, or --apply dN --review")
    if request.target_index is not None and not (request.prepare is not None or (request.apply is not None and request.review)):
        raise DocumentationError("--target-index requires --prepare or --apply --review")


def _derived_object(value: object, filename: str) -> dict:
    if not isinstance(value, dict):
        raise state.StateReadError(f"{filename}: expected a JSON object; restore or repair this state file")
    return value


def _derived_graph(value: object) -> dict:
    """Validate only the fields consumed by this application's final report."""
    filename = "docs-graph.json"
    graph = _derived_object(value, filename)
    types = {"summary": dict, "graph_hash": (str, type(None)), "revisions": (dict, type(None))}
    for field, expected in types.items():
        if field in graph and not isinstance(graph[field], expected):
            raise state.StateReadError(f"{filename}: invalid {field} field; restore or repair this state file")
    return graph


def _derived_delta(value: object) -> dict:
    filename = "docs-graph-state.json"
    graph_state = _derived_object(value, filename)
    delta = graph_state.get("delta", {})
    if not isinstance(delta, dict):
        raise state.StateReadError(f"{filename}: invalid delta field; restore or repair this state file")
    return delta


def execute(repo: str, request: DocsRequest) -> dict:
    """Run the selected docs workflow, preserving issued IDs and review gates."""
    _validate(request)
    repo = _repo(repo)
    cfg = state.config(repo)
    mode = _mode(request.mode if request.mode is not None else cfg.get("docs", {}).get("mode", "draft"))
    contract = state.read_json("contract.json", repo, default={})
    base = contract.get("base_sha") or "HEAD~1"
    diff = entire.diff_json(repo, base, "HEAD") if entire.available() else None
    if diff is None:
        message = "entire graph diff failed; cannot determine documentation candidates"
        raise DocumentationError(message, exit_code=3,
                                 payload={"error": message, "install": entire.INSTALL_HINT, "exit": 3})
    # The provider diff is commit-ref based. Worktree evidence must supplement
    # it so uncommitted API edits cannot be mistaken for no documentation impact.
    cands = docsmod.all_candidates(diff, repo, docsmod.doc_map(repo))
    docs_graph_update = docgraph.sync(repo, cfg.get("docs_dir", "docs"))
    applied, skipped, worktree_blocks, save_results = [], [], [], []
    prepared = None
    operation_state_error = None
    unconfirmed_operations = []
    if request.prepare is not None:
        candidate, issue_error = docsmod.issued_candidate(repo, cands, request.prepare)
        if candidate is None:
            skipped.append({"id": request.prepare, "why": issue_error})
        else:
            try:
                prepared = docreview.prepare(candidate, repo, target_index=request.target_index)
            except state.StateReadError:
                raise
            except (OSError, UnicodeError, ValueError) as exc:
                skipped.append({"id": request.prepare, "why": str(exc)})
    elif request.apply is not None:
        # A dN is a display position, not a durable identity. Resolve it only
        # against the exact current listing before any document can be saved.
        candidate, issue_error = docsmod.issued_candidate(repo, cands, request.apply)
        saved = False
        if candidate:
            try:
                saved = (docreview.apply(candidate, repo, detailed=True, target_index=request.target_index)
                         if request.review else docsmod.apply(candidate, repo, detailed=True))
            except state.StateReadError:
                raise
            except (OSError, UnicodeError, ValueError) as exc:
                issue_error = str(exc)
        if candidate and saved:
            applied.append(candidate["id"])
            save_results.append(saved)
        else:
            skipped.append({"id": request.apply, "why": issue_error or "no mapped section could be applied"})
    elif mode == "auto":
        docsmod.issue_candidate_manifest(repo, cands)
        for position, candidate in enumerate(cands):
            block = docsmod.worktree_auto_block(candidate, cands) if candidate["confidence"] == "sound" else None
            if block:
                skipped.append({"id": candidate["id"], "why": block["reason"],
                                "blocked_by_worktree": block["worktree_candidate"]})
                worktree_blocks.append({"id": candidate["id"], "candidate": candidate["candidate"], **block})
            elif candidate["confidence"] == "sound" and candidate["targets"]:
                try:
                    saved = docsmod.apply(candidate, repo, detailed=True)
                    if saved:
                        applied.append(candidate["id"])
                        save_results.append(saved)
                    else:
                        skipped.append({"id": candidate["id"], "why": "no mapped section could be applied"})
                except state.StateReadError as exc:
                    if not save_results:
                        raise
                    operation_state_error = exc
                    # The failing call may have replaced a document before
                    # its state read failed. Only completed return values are
                    # confirmed here; recovery journals can resolve the rest.
                    unconfirmed_operations.append({"id": candidate["id"], "status": "unconfirmed",
                                                   "why": f"save outcome unconfirmed after unreadable state: {exc}; inspect recovery records"})
                    skipped.extend({"id": remaining["id"], "status": "not_attempted",
                                    "why": "not attempted because unreadable state stopped auto updates"}
                                   for remaining in cands[position + 1:])
                    break
                except (OSError, UnicodeError, ValueError) as exc:
                    skipped.append({"id": candidate["id"], "why": str(exc)})
            else:
                skipped.append({"id": candidate["id"], "why": f"{candidate['confidence']} · {candidate['reason']}"})
    else:
        docsmod.issue_candidate_manifest(repo, cands)
    graph_pending = operation_state_error is not None or any(not result["graph_refreshed"] for result in save_results)
    graph_state_error = None
    try:
        current_docs_graph = _derived_graph(state.read_json("docs-graph.json", repo, default=docs_graph_update["graph"]))
        graph_delta = _derived_delta(state.read_json("docs-graph-state.json", repo, default={}))
        relationship_comparisons = [] if graph_pending else docgraph.comparisons(repo, current_docs_graph)
    except state.StateReadError as exc:
        if not save_results:
            raise
        # A document has already been replaced and journaled. Failure to read
        # derived graph metadata must not erase that known save outcome or
        # present the pre-save graph as current. Its refresh can be retried.
        current_docs_graph, graph_delta = {}, {}
        relationship_comparisons = []
        graph_pending = True
        graph_state_error = f"documentation graph metadata could not be read after saving: {exc}"
    out = {
        "mode": mode,
        "prepared": prepared,
        "candidates": [docsmod.packet(candidate, repo, cfg.get("docs_dir", "docs")) if mode != "flag" else candidate
                       for candidate in cands],
        "applied": applied,
        "saves": save_results,
        "not_applied": skipped,
        "worktree_blocks": worktree_blocks,
        "docs_graph": {
            "status": "refresh_pending" if graph_pending else "refreshed",
            "summary": current_docs_graph.get("summary", {}),
            "graph_hash": current_docs_graph.get("graph_hash"),
            "revisions": current_docs_graph.get("revisions"),
            "delta": graph_delta,
            **({"error": graph_state_error} if graph_state_error else {}),
        },
        "relationship_comparisons": relationship_comparisons,
        "commit_hint": "docs: amu auto-update" if applied and mode == "auto" and operation_state_error is None else None,
        "next": (
            "inspect amu docs-saves and affected documents; restore or repair unreadable state; "
            "run amu docs-graph, then amu docs --mode draft before retrying"
            if operation_state_error is not None else
            "document saved; run amu docs-graph to retry graph refresh; amu docs-saves shows recovery records"
            if graph_pending else
            "review worktree-shadowed candidates manually before applying docs"
            if worktree_blocks else
            "review SUGGESTED drafts; nothing here claims the docs are up to date"
        ),
    }
    if operation_state_error is not None:
        message = (f"auto documentation update stopped after {len(save_results)} confirmed save(s) "
                   f"because state is unreadable: {operation_state_error}; inspect recovery records before retrying")
        raise DocumentationError(message, payload={**out, "error": message, "error_kind": "state_unreadable",
                                                   "exit": 2, "complete": False,
                                                   "unconfirmed_operations": unconfirmed_operations}) from operation_state_error
    return out


def review_queue(repo: str, preview: str | None = None, discard: str | None = None) -> dict:
    """List, preview or recoverably archive drafts without a current graph diff."""
    _optional_text(preview, "--preview")
    _optional_text(discard, "--discard")
    if preview is not None and discard is not None:
        raise DocumentationError("choose --preview or --discard")
    repo = _repo(repo)
    try:
        return (docreview.preview(preview, repo) if preview is not None else
                docreview.discard(discard, repo) if discard is not None else {"reviews": docreview.queue(repo)})
    except state.StateReadError:
        raise
    except (OSError, ValueError) as exc:
        raise DocumentationError(str(exc), payload={"error": str(exc)}) from exc


def saves(repo: str, undo: str | None = None) -> dict:
    """List recovery records, or explicitly undo an unchanged saved document."""
    _optional_text(undo, "--undo")
    repo = _repo(repo)
    try:
        return docsave.undo(repo, undo) if undo is not None else {"saves": docsave.history(repo)}
    except state.StateReadError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise DocumentationError(str(exc)) from exc


def graph(repo: str) -> dict:
    """Refresh documentation relationships; code graph confidence is unchanged."""
    repo = _repo(repo)
    out = docgraph.sync(repo, state.config(repo).get("docs_dir", "docs"))
    out["relationship_comparisons"] = docgraph.comparisons(repo, out["graph"])
    return out
