"""A deterministic relationship graph for repository documentation.

The code graph remains the authority for code relationships.  This module
records a separate, explicitly lower-confidence graph for documentation:
maintainer-owned symbol mappings, symbol mentions in prose, and local links
between documentation sections.  Comparing the two graphs produces review
recommendations, never a claim that every code edge belongs in user docs.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Mapping

from amu import docstructure, freshness, state

_DOC_SUFFIXES = {".md", ".mdx", ".rst"}
_CODE_REFERENCE = re.compile(
    r"(?<![\w./-])([A-Za-z0-9_./-]+\.(?:py|pyi|ts|tsx|js|jsx|rs|go|java|c|cc|cpp|h|hpp)#[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)"
)
_MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
_RST_DOC_LINK = re.compile(r":doc:`([^`]+)`")
_RST_REF_LINK = re.compile(r":ref:`([^`]+)`")
_RST_LABEL = re.compile(r"^\s*\.\. _([^:]+):\s*$")


def _slug(value: str) -> str:
    value = re.sub(r"`([^`]+)`", r"\1", value).strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "section"


def _safe_docs_root(repo: str, docs_dir: str) -> tuple[Path | None, str | None]:
    root = Path(repo).resolve()
    configured = Path(docs_dir)
    if configured.is_absolute() or ".." in configured.parts:
        return None, "docs_dir must be a repository-relative path"
    try:
        docs_root = (root / configured).resolve()
        docs_root.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return None, "docs_dir resolves outside the repository"
    if not docs_root.is_dir():
        return None, f"documentation directory does not exist: {docs_dir}"
    return docs_root, None


def _heading_rows(lines: list[str], suffix: str) -> list[tuple[int, int, str, str]]:
    rows, _problem = docstructure.headings("\n".join(lines), suffix)
    return [(r["start"], r["level"], r["title"], r["heading"]) for r in rows]


def _sections(path: str, text: str) -> list[dict]:
    lines = text.splitlines()
    headings, problem = docstructure.headings(text, Path(path).suffix.lower())
    if not headings:
        return [
            {
                "id": f"doc:{path}#file",
                "type": "doc_section",
                "file": path,
                "title": Path(path).name,
                "heading": None,
                "line": 1,
                "end_line": max(1, len(lines)),
                "text": text.strip(),
                "structure_error": problem,
            }
        ]
    rows: list[dict] = []
    seen: set[str] = set()
    for position, heading in enumerate(headings):
        start, level, title = heading["start"], heading["level"], heading["title"]
        end = len(lines)
        for following in headings[position + 1 :]:
            if following["level"] <= level:
                end = following["boundary"]
                break
        base = _slug(title)
        slug, count = base, 1
        while slug in seen:
            count += 1
            slug = f"{base}-{count}"
        seen.add(slug)
        content_start = heading["body"]
        body_end = headings[position + 1]["boundary"] if position + 1 < len(headings) else len(lines)
        rows.append(
            {
                "id": f"doc:{path}#{slug}",
                "type": "doc_section",
                "file": path,
                "title": title,
                "heading": heading["heading"],
                "line": start + 1,
                "body_start_line": content_start + 1,
                "body_end_line": body_end,
                "structure_error": problem,
                "end_line": max(start + 1, end),
                "text": "\n".join(lines[content_start:end]).strip(),
            }
        )
    return rows


def _doc_files(repo: str, docs_dir: str) -> tuple[list[tuple[str, str]], list[str]]:
    docs_root, error = _safe_docs_root(repo, docs_dir)
    if docs_root is None:
        return [], [error or "documentation directory is unavailable"]
    root = Path(repo).resolve()
    rows: list[tuple[str, str]] = []
    warnings: list[str] = []
    for path in sorted(docs_root.rglob("*")):
        if path.suffix.lower() not in _DOC_SUFFIXES or not path.is_file():
            continue
        try:
            resolved = path.resolve()
            resolved.relative_to(docs_root)
            rel = resolved.relative_to(root).as_posix()
            rows.append((rel, resolved.read_text(encoding="utf-8", errors="replace")))
        except (OSError, RuntimeError, ValueError) as exc:
            warnings.append(f"could not read documentation file {path.name}: {exc.__class__.__name__}")
    return rows, warnings


def _valid_doc_map(raw: object) -> dict[str, list[dict[str, str]]]:
    if not isinstance(raw, Mapping):
        return {}
    rows = raw.get("entries")
    if not isinstance(rows, list):
        rows = raw.get("mappings")
    if isinstance(rows, list):
        normalized: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            symbol = row.get("symbol")
            filename = row.get("doc_file") or row.get("file")
            section = row.get("anchor") or row.get("section") or ""
            if isinstance(symbol, str) and "#" in symbol and isinstance(filename, str) and filename:
                normalized.setdefault(symbol, []).append(
                    {"file": filename.replace("\\", "/"), "section": str(section).strip()}
                )
        return normalized
    result: dict[str, list[dict[str, str]]] = {}
    for symbol, entries in raw.items():
        if not isinstance(symbol, str) or "#" not in symbol or not isinstance(entries, list):
            continue
        usable = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            filename, section = entry.get("file"), entry.get("section")
            if isinstance(filename, str) and filename and isinstance(section, str):
                usable.append({"file": filename.replace("\\", "/"), "section": section.strip()})
        if usable:
            result[symbol] = usable
    return result


def _mapped_section(target: Mapping[str, str], sections: list[dict]) -> dict | None:
    filename = target["file"]
    requested = target["section"].strip()
    candidates = [section for section in sections if section["file"] == filename]
    if not requested:
        return candidates[0] if candidates else None
    matches = [s for s in candidates if requested in {s.get("heading"), s.get("title")}]
    if matches:
        return matches[0] if len(matches) == 1 else None
    requested_slug = _slug(requested.lstrip("#").strip())
    return next((section for section in candidates if section["id"].endswith(f"#{requested_slug}")), None)


def _link_target(source: dict, raw_target: str, sections: list[dict], *, infer_rst: bool = False, docs_root: str = "docs") -> str | None:
    from urllib.parse import unquote, urlsplit
    try:
        target = urlsplit(raw_target)
    except ValueError:
        return None
    if target.scheme or target.netloc or not raw_target:
        return None
    raw_path, fragment = unquote(target.path), unquote(target.fragment)
    source_parent = PurePosixPath(source["file"]).parent
    normalized = (source["file"] if not raw_path else
                  str(PurePosixPath(docs_root).joinpath(raw_path.lstrip("/"))) if raw_path.startswith("/") else
                  str(source_parent.joinpath(raw_path)))
    parts: list[str] = []
    for part in PurePosixPath(normalized).parts:
        if part == "..":
            if parts:
                parts.pop()
            else:
                return None
        elif part not in {"", "."}:
            parts.append(part)
    normalized = "/".join(parts)
    normalized_options = [normalized]
    if infer_rst and not PurePosixPath(normalized).suffix:
        normalized_options.extend([f"{normalized}.rst", f"{normalized}/index.rst"])
    candidates = [section for section in sections if section["file"] in normalized_options]
    if not candidates:
        return None
    if fragment:
        exact = [section for section in candidates if section["id"].endswith(f"#{fragment}")]
        return exact[0]["id"] if len(exact) == 1 else None
    return candidates[0]["id"]


def _rst_role_target(value: str) -> str:
    titled = re.search(r"<([^<>]+)>\s*$", value)
    return (titled.group(1) if titled else value).strip()


def _rst_labels(files: list[tuple[str, str]], sections: list[dict]) -> dict[str, str]:
    """Resolve Sphinx labels to the section immediately following the label."""
    labels: dict[str, str] = {}
    by_file: dict[str, list[dict]] = {}
    for section in sections:
        by_file.setdefault(section["file"], []).append(section)
    for path, text in files:
        candidates = sorted(by_file.get(path, []), key=lambda section: section["line"])
        for line_number, line in enumerate(text.splitlines(), 1):
            match = _RST_LABEL.match(line)
            if not match or not candidates:
                continue
            following = next((section for section in candidates if section["line"] >= line_number), candidates[0])
            labels[match.group(1).strip()] = following["id"]
    return labels


def _edge_key(edge: Mapping[str, object]) -> tuple[str, str, str, str]:
    return (
        str(edge.get("relation") or ""),
        str(edge.get("source") or ""),
        str(edge.get("target") or ""),
        str(edge.get("evidence") or ""),
    )


def _public_node(node: Mapping[str, object]) -> dict:
    public = {key: value for key, value in node.items() if key != "text"}
    if node.get("type") == "doc_section":
        public["content_hash"] = state.stable_hash(str(node.get("text") or ""))
    return public


def build(repo: str = ".", docs_dir: str | None = None, doc_map: object | None = None) -> dict:
    """Build the current documentation graph without executing project code."""
    revision_before = freshness.capture(repo)
    cfg = state.config(repo)
    docs_dir = docs_dir or str(cfg.get("docs_dir") or "docs")
    raw_map = state.read_json("doc-map.json", repo, default={}) if doc_map is None else doc_map
    mapping = _valid_doc_map(raw_map)
    files, warnings = _doc_files(repo, docs_dir)
    sections = [section for path, content in files for section in _sections(path, content)]
    rst_labels = _rst_labels(files, sections)
    section_ids = {section["id"] for section in sections}
    nodes: dict[str, dict] = {section["id"]: _public_node(section) for section in sections}
    edges: dict[tuple[str, str, str, str], dict] = {}

    for symbol, targets in mapping.items():
        nodes.setdefault(symbol, {"id": symbol, "type": "code_symbol", "symbol": symbol})
        for target in targets:
            section = _mapped_section(target, sections)
            if section is None:
                warnings.append(f"doc-map target not found for {symbol}: {target['file']} {target['section']}")
                continue
            edge = {
                "relation": "DOCUMENTS",
                "source": symbol,
                "target": section["id"],
                "confidence": "mapped",
                "evidence": "doc-map",
            }
            edges[_edge_key(edge)] = edge

    known_symbols = set(mapping)
    names: dict[str, list[str]] = {}
    for symbol in known_symbols:
        names.setdefault(symbol.split("#", 1)[-1], []).append(symbol)
    for section in sections:
        text = section["text"]
        exact_mentions = set(_CODE_REFERENCE.findall(text))
        # A mapped symbol may be written in docs as its short or qualified
        # name.  This is explicitly heuristic mention evidence, not a code
        # relationship and never upgrades a graph claim.
        unqualified_text = _CODE_REFERENCE.sub("", text)
        for leaf, symbols in names.items():
            if re.search(rf"(?<![\w.#/]){re.escape(leaf)}(?![\w.])", unqualified_text):
                if len(symbols) == 1:
                    exact_mentions.add(symbols[0])
                else:
                    warnings.append(f"ambiguous symbol mention {leaf} at {section['file']}:{section['line']}; use path#qualified_symbol")
        for symbol in sorted(exact_mentions):
            nodes.setdefault(symbol, {"id": symbol, "type": "code_symbol", "symbol": symbol})
            edge = {
                "relation": "MENTIONS",
                "source": section["id"],
                "target": symbol,
                "confidence": "heuristic",
                "evidence": f"{section['file']}:{section['line']}",
            }
            edges[_edge_key(edge)] = edge
        for raw_target in _MARKDOWN_LINK.findall(text):
            target = _link_target(section, raw_target, sections, docs_root=docs_dir)
            if target and target in section_ids:
                edge = {
                    "relation": "LINKS_TO",
                    "source": section["id"],
                    "target": target,
                    "confidence": "structural",
                    "evidence": f"{section['file']}:{section['line']}",
                }
                edges[_edge_key(edge)] = edge
            elif not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:|^//", raw_target):
                warnings.append(f"unresolved local link {raw_target} at {section['file']}:{section['line']}")
        if Path(section["file"]).suffix.lower() == ".rst":
            rst_targets = [
                _link_target(section, _rst_role_target(raw_target), sections, infer_rst=True, docs_root=docs_dir)
                for raw_target in _RST_DOC_LINK.findall(text)
            ]
            rst_targets.extend(
                rst_labels.get(_rst_role_target(raw_target)) for raw_target in _RST_REF_LINK.findall(text)
            )
            for target in rst_targets:
                if target and target in section_ids:
                    edge = {
                        "relation": "LINKS_TO",
                        "source": section["id"],
                        "target": target,
                        "confidence": "structural",
                        "evidence": f"{section['file']}:{section['line']}",
                    }
                    edges[_edge_key(edge)] = edge

    ordered_nodes = sorted(nodes.values(), key=lambda node: str(node["id"]))
    ordered_edges = sorted(edges.values(), key=_edge_key)
    graph_hash = state.stable_hash({"nodes": ordered_nodes, "edges": ordered_edges})
    revision_after = freshness.capture(repo)
    revisions = {"status": "unavailable", "reason": revision_after.get("error") or "no versioned source capture"}
    if freshness.valid_capture(revision_before) and freshness.valid_capture(revision_after):
        stable = all(revision_before[key]["digest"] == revision_after[key]["digest"] for key in ("code", "docs"))
        if doc_map is None:
            stable = stable and _valid_doc_map(state.read_json("doc-map.json", repo, default={})) == mapping
        revisions = {
            "status": "captured" if stable else "changed_during_collection",
            "code": {"head": revision_after["head"], "digest": revision_after["code"]["digest"],
                     "config_hash": revision_after["config_hash"]},
            "docs": {"head": revision_after["head"], "digest": revision_after["docs"]["digest"],
                     "content_hash": state.stable_hash(files), "doc_map_hash": state.stable_hash(mapping)},
        }
    return {
        "schema": 1,
        "generated_at": state.now_iso(),
        "docs_dir": docs_dir,
        "doc_map_hash": state.stable_hash(mapping),
        "graph_hash": graph_hash,
        "revisions": revisions,
        "nodes": ordered_nodes,
        "edges": ordered_edges,
        "warnings": sorted(set(warnings)),
        "summary": {
            "files": len(files),
            "sections": len(sections),
            "code_symbols": sum(node["type"] == "code_symbol" for node in ordered_nodes),
            "documents_edges": sum(edge["relation"] == "DOCUMENTS" for edge in ordered_edges),
            "mention_edges": sum(edge["relation"] == "MENTIONS" for edge in ordered_edges),
            "link_edges": sum(edge["relation"] == "LINKS_TO" for edge in ordered_edges),
            "warnings": len(set(warnings)),
        },
    }


def delta(before: object, after: Mapping[str, object]) -> dict:
    old_edges = before.get("edges") if isinstance(before, Mapping) else []
    new_edges = after.get("edges") if isinstance(after, Mapping) else []
    old_edges = old_edges if isinstance(old_edges, list) else []
    new_edges = new_edges if isinstance(new_edges, list) else []
    old = {_edge_key(edge): dict(edge) for edge in old_edges if isinstance(edge, Mapping)}
    new = {_edge_key(edge): dict(edge) for edge in new_edges if isinstance(edge, Mapping)}
    return {
        "added": [new[key] for key in sorted(new.keys() - old.keys())],
        "removed": [old[key] for key in sorted(old.keys() - new.keys())],
        "unchanged": len(old.keys() & new.keys()),
    }


def sync(repo: str = ".", docs_dir: str | None = None) -> dict:
    """Persist a fresh documentation graph and the edge delta from its predecessor."""
    previous = state.read_json("docs-graph.json", repo, default={})
    current = build(repo, docs_dir)
    changes = delta(previous, current)
    state.write_json("docs-graph.json", current, repo)
    state.write_json(
        "docs-graph-state.json",
        {
            "schema": 1,
            "graph_hash": current["graph_hash"],
            "doc_map_hash": current["doc_map_hash"],
            "revisions": current["revisions"],
            "delta": changes,
            "at": state.now_iso(),
        },
        repo,
    )
    return {"graph": current, "delta": changes}


def _mapped_sections(graph: Mapping[str, object], target: str) -> set[str]:
    return {
        str(edge["target"])
        for edge in graph.get("edges", [])
        if isinstance(edge, Mapping) and edge.get("relation") == "DOCUMENTS" and edge.get("source") == target
    }


def _section_mentions(graph: Mapping[str, object], sections: set[str], symbol: str) -> bool:
    for edge in graph.get("edges", []):
        if not isinstance(edge, Mapping) or edge.get("relation") != "MENTIONS" or edge.get("source") not in sections:
            continue
        mentioned = str(edge.get("target") or "")
        if mentioned == symbol:
            return True
    return False


def compare_code_refresh(refresh: object, graph: Mapping[str, object]) -> dict:
    """Compare one post-edit code edge delta with its mapped doc subgraph.

    Code callers/callees and prose are different abstractions.  A mismatch is
    therefore always a review recommendation (``requires_verification``),
    never proof that documentation is wrong.
    """
    if not isinstance(refresh, Mapping):
        return {"target": None, "status": "unavailable", "mapped_sections": [], "items": []}
    target = str(refresh.get("target") or "")
    live_target = str(refresh.get("live_target") or target)
    sections = _mapped_sections(graph, target) | _mapped_sections(graph, live_target)
    if not sections:
        return {"target": target, "live_target": live_target, "status": "unmapped", "mapped_sections": [], "items": []}
    if refresh.get("complete") is not True:
        return {
            "target": target,
            "live_target": live_target,
            "status": "unavailable",
            "mapped_sections": sorted(sections),
            "items": [],
            "reason": "post-edit code relationship delta is incomplete",
        }
    raw_delta = refresh.get("edge_delta")
    raw_delta = raw_delta if isinstance(raw_delta, Mapping) else {}
    items = []
    for change in ("added", "removed"):
        rows = raw_delta.get(change)
        for edge in rows if isinstance(rows, list) else []:
            if not isinstance(edge, Mapping):
                continue
            related = str(edge.get("related") or "")
            if not related:
                continue
            mentioned = _section_mentions(graph, sections, related)
            doc_state = (
                "mentioned" if change == "added" and mentioned
                else "missing_mention" if change == "added"
                else "still_mentioned" if mentioned
                else "not_mentioned"
            )
            if doc_state in {"missing_mention", "still_mentioned"}:
                verb = "gained" if change == "added" else "lost"
                recommendation = (
                    f"Review {', '.join(sorted(sections))}: code {verb} a {edge.get('relation', 'semantic')} "
                    f"relationship with {related}, while the mapped docs "
                    + ("do not mention it." if doc_state == "missing_mention" else "still mention it.")
                )
            else:
                recommendation = "No relationship-specific draft is proposed; review only if this edge is user-facing."
            items.append(
                {
                    "change": change,
                    "edge": dict(edge),
                    "doc_state": doc_state,
                    "confidence": "requires_verification",
                    "recommendation": recommendation,
                }
            )
    review = any(item["doc_state"] in {"missing_mention", "still_mentioned"} for item in items)
    return {
        "target": target,
        "live_target": live_target,
        "status": "review_required" if review else "aligned_no_claim",
        "mapped_sections": sorted(sections),
        "items": items,
    }


def comparisons(repo: str, graph: Mapping[str, object]) -> list[dict]:
    saved = state.read_json("refresh-state.json", repo, default={})
    targets = saved.get("targets") if isinstance(saved, Mapping) else {}
    if not isinstance(targets, Mapping):
        return []
    return [compare_code_refresh(targets[target], graph) for target in sorted(targets)]
