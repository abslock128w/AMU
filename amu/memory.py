"""Self-renovating CLAUDE.md / AGENTS.md managed block, backed by .amu/memory.json.
Memory is instructions, not code: the only file writes here are inside the markers."""

from __future__ import annotations

import glob
import json
import re
from pathlib import Path

from amu import state, unknowns

START = "<!-- amu:memory start · do not edit inside · regenerate with `amu memory refresh` -->"
END = "<!-- amu:memory end -->"
SECTIONS = [
    ("hot_spot", "Hot spots (history: went red in past checks)"),
    ("frozen_interface", "Frozen / risky interfaces (from approved contracts)"),
    ("answered_unknown", "Reviewed answers (valid only for their recorded question and source)"),
    ("answer_suggestion", "Model suggestions awaiting human confirmation"),
    ("feature_path", "Features → paths"),
    ("convention", "Conventions the graph confirmed"),
    ("open_question", "Open questions (carry into the next task)"),
    ("stale", "Stale (decisions whose evidence changed — re-verify before trusting)"),
]
MAX_LINES, MAX_PER_SECTION = 40, 8
SECRET_RX = re.compile(
    r"(sk-ant-|(?:ANTHROPIC|GEMINI|GOOGLE)_API_KEY\s*=|AIza[A-Za-z0-9_-]{20,}|AQ\.[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{10,}|AKIA[0-9A-Z]{12,}|-----BEGIN)"
)
BANNED_RX = re.compile(r"\b(safe|clean|verified|OK)\b")


class MemoryRejected(ValueError):
    pass


def _entry(kind: str, text: str, evidence: list[str], repo: str, file_for_hash: str | None = None, pinned: bool = False) -> dict:
    if SECRET_RX.search(text):
        raise MemoryRejected("memory entry looks like it contains a secret")
    text = BANNED_RX.sub(lambda m: {"OK": "0-blocking", "safe": "0-blocking"}.get(m.group(0), "unverdicted"), text)
    eid = state.stable_hash([kind, text])[:8]
    return {"id": eid, "kind": kind, "text": text, "evidence": evidence, "sha": state.git_head(repo),
            "file_hash": state.file_hash(file_for_hash, repo) if file_for_hash else "", "file": file_for_hash or "",
            "created": state.now_iso(), "last_seen": state.now_iso(), "pinned": pinned}


def _checkpoint_id(repo: str) -> str:
    from amu import entire
    cps = entire.checkpoint_list()
    if cps:
        c = cps[0]
        return str(c.get("id") or c.get("checkpoint_id") or c.get("ID") or "")[:26]
    return "none-yet"


def _tombstones(mem: dict) -> dict:
    return {**{e["id"]: e["forgotten_at"] for e in mem.get("history", [])
               if isinstance(e, dict) and e.get("id") and e.get("forgotten_at")},
            **mem.get("tombstones", {})}


def collect(repo: str = ".", *, previous_memory: dict | None = None) -> list[dict]:
    """Rebuild entries from sources. Existing pinned/forgotten/answered entries are carried over."""
    old = previous_memory if previous_memory is not None else state.read_json("memory.json", repo, default={"entries": [], "history": []})
    carry = {e["id"]: e for e in old.get("entries", []) if e.get("pinned") or e["kind"] == "answered_unknown"}
    entries: list[dict] = []
    st = state.read_json("state.json", repo, default={"nodes": {}, "violations": [], "history": []})
    reds: dict[str, list] = {}
    for h in st.get("history", []):
        for v in h.get("violations", []):
            reds.setdefault(v["symbol"], []).append((v["kind"], h.get("checkpoint", "")))
    total = max(len(st.get("history", [])), 1)
    for sym, hits in sorted(reds.items(), key=lambda kv: -len(kv[1])):
        f = sym.split("#")[0]
        if "__pycache__" in f or f.endswith(".pyc"):
            continue
        entries.append(_entry("hot_spot", f"`{sym}` — red {len(hits)}/{total} · last: {hits[-1][0]} (chk {hits[-1][1] or 'n/a'}) · verify: amu check", [sym], repo, f))
    freq: dict[str, int] = {}
    n_contracts = 0
    for cp in sorted(glob.glob(str(Path(repo) / ".amu" / "contracts" / "*.json"))):
        n_contracts += 1
        try:
            c = json.loads(Path(cp).read_text())
        except json.JSONDecodeError:
            continue
        for fs in c.get("frozen_signatures", []):
            freq[fs] = freq.get(fs, 0) + 1
    for fs, k in sorted(freq.items(), key=lambda kv: -kv[1]):
        entries.append(_entry("frozen_interface", f"`{fs}` — signature frozen in {k} of last {n_contracts} tasks", [fs], repo, fs.split("#")[0]))
    for rp in sorted(glob.glob(str(Path(repo) / ".amu" / "run" / "*.json"))):
        try:
            r = json.loads(Path(rp).read_text())
        except json.JSONDecodeError:
            continue
        for a in r.get("answers", []):
            if not isinstance(a, dict) or not isinstance(a.get("question"), str) or not isinstance(a.get("answer"), str):
                continue
            # Old chat records mislabeled model output as human. Neither their
            # prose nor a legacy `who` field is sufficient authorship evidence.
            e = _entry("answered_unknown", f"{a['question']} → \"{a['answer']}\" (legacy; author unverified)", [Path(rp).name], repo)
            e["stale"] = "legacy answer has no question/source binding; record or confirm a current answer"
            entries.append(e)
    answer_review = unknowns.review(repo)
    for answer in answer_review["answers"]:
        kind = "answered_unknown" if answer["status"] == "confirmed" else "answer_suggestion"
        binding = answer.get("binding") if isinstance(answer.get("binding"), dict) else {}
        attribution = f"author {answer.get('who', 'unknown')}"
        if answer.get("confirmed_by"):
            attribution += f"; confirmed by {answer['confirmed_by']}"
        e = _entry(
            kind,
            f"{answer.get('node', '?')} {binding.get('path', '?')}: {binding.get('question', '?')} → \"{answer.get('answer', '')}\" ({attribution})",
            [f"{unknowns.STORE}#{answer.get('id', '?')}"], repo,
        )
        e.update(answer_id=answer.get("id"), who=answer.get("who"), confirmed_by=answer.get("confirmed_by"))
        if answer["status"] == "stale":
            e["stale"] = answer["stale_reason"]
        entries.append(e)
    fm = state.read_json("feature_map.json", repo, default={})
    for feat, globs in fm.items():
        entries.append(_entry("feature_path", f"{feat}: {', '.join(globs)}", ["feature_map.json"], repo))
    for sp in sorted(glob.glob(str(Path(repo) / ".amu" / "skills" / "*" / "SKILL.md"))):
        first = next((ln.strip("# ").strip() for ln in Path(sp).read_text().splitlines() if ln.startswith("- ")), "")
        if first:
            entries.append(_entry("convention", f"{first} (source: {Path(sp).parent.name})", [str(Path(sp).relative_to(repo))], repo, str(Path(sp).relative_to(repo))))
    m = state.read_json("map.json", repo)
    if m:
        for n in m["nodes"]:
            if n.get("confidence") == "unknown" and n["id"] not in answer_review["confirmed"]:
                entries.append(_entry("open_question", f"{n['id']} {n['path']} {n['reason']} → {n['verify']}", [n["id"]], repo, n["path"]))
    cfg = state.read_json("config.json", repo, default={})
    for f in cfg.get("unparsed", [])[:MAX_PER_SECTION]:
        entries.append(_entry("open_question", f"{f} was not parsed by the graph; its consumers are invisible → amu verify --attempt-fallback", [f], repo, f))
    for e in carry.values():
        if e["id"] not in {x["id"] for x in entries}:
            if e["kind"] in {"answered_unknown", "answer_suggestion"}:
                e = {**e, "stale": "answer has no current matching source record; review again"}
            entries.append(e)
    previous = {e["id"]: e for e in old.get("entries", [])}
    for e in entries:
        prev = previous.get(e["id"])
        if prev:
            # A repeat observation is not new evidence for an old conclusion.
            # Preserve its original binding so repeated refreshes cannot clear
            # stale flags or erase the user's pin.
            for key in ("created", "pinned", "file_hash", "sha"):
                if key in prev:
                    e[key] = prev[key]
            if prev.get("stale"):
                e["stale"] = prev["stale"]
        if prev and prev.get("file") and prev.get("file_hash") and state.file_hash(prev["file"], repo) != prev["file_hash"]:
            e["stale"] = f"file hash of {prev['file']} moved since {prev['sha'][:7]}"
    forgotten = _tombstones(old)
    return [e for e in entries if e["id"] not in forgotten]


def render_block(entries: list[dict], repo: str = ".") -> str:
    from amu import __version__  # noqa: F401  (keeps header stable)
    lines = [START, f"## What amu has learned about this repo  (TWC Thugs · checkpoint {_checkpoint_id(repo)})"]
    overflow = 0
    budget = MAX_LINES - 2
    for kind, title in SECTIONS:
        items = [e for e in entries if (e["kind"] == kind and not e.get("stale")) or (kind == "stale" and e.get("stale"))]
        items.sort(key=lambda e: (not e["pinned"], e["created"], e["id"]))
        if not items:
            continue
        lines.append(f"### {title}")
        budget -= 1
        shown = 0
        for e in items:
            if shown >= MAX_PER_SECTION or budget <= 1:
                overflow += 1
                continue
            txt = f"- {e['text']}" + (f" — stale: {e['stale']}" if kind == "stale" else "") + (" 📌" if e["pinned"] else "")
            lines.append(txt)
            shown += 1
            budget -= 1
    if overflow:
        lines.append(f"+{overflow} more in .amu/memory.md")
    lines.append(END)
    return "\n".join(lines)


def write_block(path: Path, block: str) -> bool:
    """Create or replace the managed block; bytes outside the markers are untouched. Returns True if changed."""
    old = path.read_bytes().decode("utf-8") if path.exists() else ""
    if START in old and END in old:
        pre, _, rest = old.partition(START)
        _, _, post = rest.partition(END)
        new = pre + block + post
    else:
        new = old + ("\n" if old and not old.endswith("\n") else "") + "\n" + block + "\n"
    if new == old:
        return False
    path.write_bytes(new.encode("utf-8"))
    return True


def refresh(repo: str = ".", files=("CLAUDE.md", "AGENTS.md")) -> dict:
    def rebuild(old):
        return {**old, "schema_version": 1, "entries": collect(repo, previous_memory=old),
                "tombstones": _tombstones(old)}
    saved = state.update_json("memory.json", repo, {"entries": [], "history": []}, rebuild)
    entries = saved["entries"]
    block = render_block(entries, repo)
    (state.amu_dir(repo) / "memory.md").write_text("\n".join(f"- [{e['kind']}] {e['text']} ({', '.join(e['evidence'])})" for e in entries) + "\n")
    changed = [f for f in files if write_block(Path(repo) / f, block)]
    return {"entries": len(entries), "stale": sum(1 for e in entries if e.get("stale")), "changed_files": changed, "block_lines": block.count("\n") + 1}


def pin(entry_id: str, repo: str = ".", value: bool = True) -> bool:
    hit = False
    def change(mem):
        nonlocal hit
        for e in mem["entries"]:
            if e["id"] == entry_id:
                e["pinned"] = value
                hit = True
        return mem
    state.update_json("memory.json", repo, {"entries": [], "history": []}, change)
    return hit


def forget(entry_id: str, repo: str = ".") -> bool:
    found = False
    def change(mem):
        nonlocal found
        gone = [e for e in mem["entries"] if e["id"] == entry_id]
        found = bool(gone)
        for e in gone:
            mem.setdefault("history", []).append({**e, "forgotten_at": state.now_iso()})
        mem["entries"] = [e for e in mem["entries"] if e["id"] != entry_id]
        mem.update(schema_version=1, tombstones=_tombstones(mem))
        return mem
    state.update_json("memory.json", repo, {"entries": [], "history": []}, change)
    return found
