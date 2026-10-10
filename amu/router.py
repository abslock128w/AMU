"""Intent router: deterministic keyword rules first, model second when a supported credential is connected.
Symbols are never invented — they must come from the lookup (entire graph search / def)."""

from __future__ import annotations

import re
from typing import Callable

CLASS_RULES = [
    ("rename", r"\brename\b"),
    ("remove", r"\b(remove|delete|drop)\b"),
    ("move", r"\bmove\b"),
    ("signature", r"\b(signature|param(eter)?s?|argument|return type|take[s]? an?|accept|overload)\b"),
    ("body", r"\b(body|implementation|fix|refactor internals|optimi[sz]e|bug)\b"),
]
SLASH = {"/map": "map", "/plan": "plan", "/approve": "approve", "/check": "check", "/refresh": "refresh", "/done": "done", "/docs": "docs", "/answer": "answer",
         "/api-manifest": "api-manifest", "/benchmark": "benchmark",
         "/verify": "verify", "/why": "why", "/feature": "feature", "/skills": "skills", "/help": "help",
         "/repo": "repo", "/use": "use", "/find": "find", "/open": "open", "/tree": "tree", "/back": "back", "/graph": "graph", "/ask": "ask", "/key": "key", "/cat": "cat", "/where": "where", "/recent": "recent", "/neighbors": "neighbors", "/quit": "quit"}
_EXPLORE_RX = re.compile(r"^[A-Za-z0-9_./#-]+$")


def change_class(text: str) -> str:
    t = text.lower()
    for cls, rx in CLASS_RULES:
        if re.search(rx, t):
            return cls
    # Silent on params/shape ⇒ the safe assumption is the breaking one.
    return "signature"


def candidate_tokens(text: str) -> list[str]:
    toks = re.findall(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", text)
    stop = {w for w, _ in CLASS_RULES} | {"the", "a", "an", "to", "and", "of", "in", "so", "it", "that", "this", "make", "change",
                                          "please", "can", "you", "we", "i", "want", "should", "with", "for", "on", "from", "into"}
    return [t for t in toks if t.lower() not in stop and len(t) > 2]


VERBS = {"open", "cat", "find", "search", "tree", "ls", "graph", "map", "where", "neighbors", "plan", "approve", "check", "refresh", "done", "docs",
         "api-manifest", "benchmark",
         "docs-reviews", "docs-graph", "docs-saves", "session", "verify", "answer", "why", "feature", "skills", "repo", "use", "back", "recent", "ask", "key", "help", "quit", "exit"}
_ALIAS = {"search": "find", "ls": "tree", "quit": "quit", "exit": "quit"}


def route(text: str, lookup: Callable[[str], list[dict]]) -> dict:
    """lookup(token) → list of {symbol, path} matches from the graph (verbatim). Returns
    {intent, change_class, targets[], status ∈ ok|not_found|ambiguous|multi_target|command|explore}."""
    text = text.strip()
    head, _, rest = text.partition(" ")
    if text.startswith("/") or head.lower() in VERBS:
        cmd = head.lstrip("/").lower()
        cmd = _ALIAS.get(cmd, cmd)
        return {"intent": SLASH.get("/" + cmd, cmd if cmd in VERBS or cmd in ("cat",) else "unknown"), "arg": rest.strip(), "status": "command", "targets": [], "change_class": None}
    if " " not in text and _EXPLORE_RX.match(text) and ("/" in text or "#" in text or "." in text):
        return {"intent": "explore", "arg": text, "status": "explore", "targets": [], "change_class": None}
    cls = change_class(text)
    found: list[dict] = []
    ambiguous: list[str] = []
    for tok in candidate_tokens(text):
        hits = lookup(tok)
        if len(hits) == 1:
            found.append({**hits[0], "token": tok})
        elif len(hits) > 1:
            ambiguous.append(tok)
    if ambiguous:
        return {"intent": "map", "change_class": cls, "targets": [], "status": "ambiguous", "ambiguous": ambiguous}
    if not found:
        return {"intent": "map", "change_class": cls, "targets": [], "status": "not_found"}
    if len({f["symbol"] for f in found}) > 1:
        return {"intent": "map", "change_class": cls, "targets": found, "status": "multi_target"}
    return {"intent": "map", "change_class": cls, "targets": found[:1], "status": "ok"}
