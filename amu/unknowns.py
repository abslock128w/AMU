"""Revision-bound answers; model proposals require an explicit CLI confirmation."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from amu import freshness, graph_refresh, state

STORE = "unknown-answers.json"


def _records(raw: object) -> list[dict]:
    if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1 or not isinstance(raw.get("answers"), list):
        raise ValueError("answer store is malformed; restore it before recording answers")
    return raw["answers"]


def _context(repo: str, amu_map: object) -> dict:
    if not isinstance(amu_map, dict) or not isinstance(amu_map.get("map_hash"), str) or not amu_map["map_hash"]:
        raise ValueError("no current map; run amu map first")
    if state.read_json("map.json", repo) != amu_map:
        raise ValueError("saved map changed; review the current map again")
    evidence = amu_map.get("graph_evidence")
    if not isinstance(evidence, dict) or evidence.get("map_hash") != amu_map["map_hash"]:
        raise ValueError("map has no matching source evidence; run amu map again")
    problem = graph_refresh.map_current_capture_reason(repo, evidence)
    if problem:
        raise ValueError(f"stale map: {problem}")
    captured = evidence["worktree"]["after"]
    if captured.get("head") == "no-git":
        raise ValueError("map has no Git revision; run amu map after committing a baseline")
    context = {
        "repo": str(Path(repo).resolve()),
        "map_hash": amu_map["map_hash"],
        "map_content_hash": state.stable_hash(amu_map),
        "source": {"head": captured["head"], "digest": graph_refresh.relation_digest(captured),
                   **({"domain": "code-v1", "config_hash": captured["freshness"]["config_hash"]}
                      if "freshness" in captured else {})},
    }
    if "freshness" in captured and any(
        isinstance(node, dict) and node.get("confidence") == "unknown"
        and not freshness.is_code_path(node.get("path")) for node in amu_map.get("nodes", [])
    ):
        current = freshness.capture(repo)
        if not freshness.valid_capture(current):
            raise ValueError("cannot capture current documentation evidence for its questions")
        context["doc_source"] = {"digest": current["docs"]["digest"],
                                 "doc_map_hash": state.stable_hash(state.read_json("doc-map.json", repo, default={}))}
    return context


def _binding(context: dict, amu_map: dict, node_id: str) -> dict:
    if not isinstance(node_id, str) or not node_id or not isinstance(amu_map.get("nodes"), list):
        raise ValueError("answer must name an unknown node in the current map")
    matches = [n for n in amu_map.get("nodes", []) if isinstance(n, dict) and n.get("id") == node_id]
    if len(matches) != 1 or matches[0].get("confidence") != "unknown":
        raise ValueError(f"{node_id} is not a unique unknown node in the current map")
    node = matches[0]
    if not all(isinstance(node.get(k), str) and node[k].strip() for k in ("path", "reason")):
        raise ValueError("unknown node is missing its path or question")
    source = dict(context["source"])
    if not freshness.is_code_path(node["path"]) and context.get("doc_source"):
        source.update(domain="code-and-docs-v1", docs=context["doc_source"])
    return {
        **{k: v for k, v in context.items() if k != "doc_source"}, "source": source,
        "node_id": node_id, "path": node["path"], "symbol": node.get("symbol"),
        "question": node["reason"], "node_evidence_hash": state.stable_hash(node),
    }


def _status(record: dict, context: dict | None, amu_map: dict, problem: str | None) -> dict:
    reason = problem
    if not reason:
        try:
            expected = _binding(context, amu_map, record.get("node"))
            if record.get("binding") != expected:
                reason = "answer belongs to different question, map evidence, or source; answer again"
        except ValueError as exc:
            reason = str(exc)
    if (
        type(record.get("version")) is not int or record["version"] != 1
        or record.get("who") not in ("human", "model")
        or not isinstance(record.get("answer"), str) or not record["answer"].strip()
        or type(record.get("confirmed")) is not bool
    ):
        reason = reason or "answer has no valid author or structured evidence; answer again"
    confirmed = (
        record.get("confirmed") is True and record.get("confirmed_by") == "human"
        and isinstance(record.get("confirmed_at"), str) and bool(record["confirmed_at"])
    )
    return {**record, "status": "stale" if reason else "confirmed" if confirmed else "proposed", "stale_reason": reason}


def review(repo: str, amu_map: dict | None = None) -> dict:
    """Revalidate records on every read; memory prose is never decision evidence."""
    amu_map = amu_map if amu_map is not None else state.read_json("map.json", repo, default={})
    context, problem = None, None
    try:
        context = _context(repo, amu_map)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        problem = str(exc)
    try:
        records = _records(state.read_json(STORE, repo, default={"version": 1, "answers": []}))
    except ValueError as exc:
        records, problem = [], str(exc)
    rows = [_status(r, context, amu_map, problem) for r in records if isinstance(r, dict)]
    confirmed = {r["node"]: r for r in rows if r["status"] == "confirmed"}
    nodes = amu_map.get("nodes", []) if isinstance(amu_map, dict) else []
    questions = [
        {"node": n.get("id"), "path": n.get("path"), "symbol": n.get("symbol"), "question": n.get("reason")}
        for n in nodes if isinstance(n, dict) and n.get("confidence") == "unknown"
    ]
    return {
        "questions": questions, "answers": rows, "confirmed": confirmed,
        "unanswered": [q["node"] for q in questions if q["node"] not in confirmed], "binding_error": problem,
    }


def record_answer(repo: str, node: str, answer: str, *, who: str) -> dict:
    from amu.memory import SECRET_RX

    if who not in {"human", "model"}:
        raise ValueError("answer author must be human or model")
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("answer cannot be empty")
    if SECRET_RX.search(answer):
        raise ValueError("answer looks like a secret; not recorded")
    amu_map = state.read_json("map.json", repo, default={})
    binding = _binding(_context(repo, amu_map), amu_map, node)
    record = {
        "version": 1, "id": "a" + uuid4().hex[:12], "node": node, "answer": answer.strip(),
        "who": who, "confirmed": who == "human", "confirmed_by": "human" if who == "human" else None,
        "confirmed_at": state.now_iso() if who == "human" else None,
        "created_at": state.now_iso(), "binding": binding,
    }

    def append(raw: object) -> dict:
        rows = _records(raw)
        current_map = state.read_json("map.json", repo, default={})
        if _binding(_context(repo, current_map), current_map, node) != binding:
            raise ValueError("question or source changed while recording the answer; review again")
        return {"version": 1, "answers": [*rows, record]}

    state.update_json(STORE, repo, {"version": 1, "answers": []}, append)
    return record


def confirm_answer(repo: str, answer_id: str) -> dict:
    """Only the explicit CLI path calls this; the model has no confirmation tool."""
    confirmed = None

    def confirm(raw: object) -> dict:
        nonlocal confirmed
        rows = _records(raw)
        matches = [r for r in rows if isinstance(r, dict) and r.get("id") == answer_id]
        if len(matches) != 1:
            raise ValueError("answer id not found or ambiguous; run amu answer to review candidates")
        amu_map = state.read_json("map.json", repo, default={})
        current = _status(matches[0], _context(repo, amu_map), amu_map, None)
        if current["status"] == "stale":
            raise ValueError(f"stale answer: {current['stale_reason']}")
        confirmed = {**matches[0], "confirmed": True, "confirmed_by": "human", "confirmed_at": state.now_iso()}
        return {"version": 1, "answers": [confirmed if r is matches[0] else r for r in rows]}

    state.update_json(STORE, repo, {"version": 1, "answers": []}, confirm)
    return confirmed
