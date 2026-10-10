"""Rich terminal rendering + agent-card JSON. This module never computes anything; it formats what it is given."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.tree import Tree

from amu import brand

GLYPH = {"sound": "●", "guessed": "◐", "unknown": "○"}
STATE_GLYPH = {"planned": "·", "editing": "▸", "green": "✓", "red": "✗", "drift": "↯"}
RISK_TAG = {"will_break": "will_break", "might_break": "might_break", "unknown": "unknown", "none": "no-risk"}


def _console() -> Console:
    return brand.out_console()


def _narrow() -> bool:
    return shutil.get_terminal_size((100, 20)).columns < 100


def _conf(n: dict) -> str:
    return f"[conf.{n['confidence']}]{GLYPH[n['confidence']]} {n['confidence']}[/]"


def _risk(n: dict) -> str:
    tag = RISK_TAG.get(n["risk"], n["risk"])
    return f"[risk]{tag}[/]" if n["risk"] == "will_break" else f"[brand.muted]{tag}[/]"


def _display_nodes(nodes: list[dict]) -> list[dict]:
    """Render uncertainty first without changing stable node IDs in the JSON map."""
    confidence_order = {"unknown": 0, "guessed": 1, "sound": 2}
    return sorted(
        nodes,
        key=lambda node: (
            confidence_order.get(node.get("confidence"), 3),
            node.get("order", 99),
            node.get("path", ""),
            node.get("symbol") or "",
            node.get("id", ""),
        ),
    )


def map_tree(m: dict, states: dict | None = None) -> Tree:
    r = m["root"]
    tree = Tree(f"[brand]{r['file']}[/] [brand.muted]· {r['exports']} exports · {r.get('internal', 0)} internal helpers · feature {r['feature']} ({r['feature_source']}) · change {m.get('change', '?')}[/]",
                guide_style="brand.rule")
    for n in _display_nodes(m["nodes"]):
        st = (states or {}).get(n["id"])
        bits = []
        if states is not None:
            bits.append(f"[state.{st or 'planned'}]{STATE_GLYPH.get(st or 'planned', '·')}[/]")
        bits += [_conf(n), _risk(n), f"[brand.body]{n['path']}[/]" + (f"#{n['symbol']}" if n["symbol"] else ""),
                 f"[brand.muted]order {n['order']} · {n['action']}[/]"]
        if not _narrow():
            bits.append(f"[brand.muted]{n['feature']}[/]")
            if n["via"]:
                bits.append(f"[brand.muted]via {n['via']}[/]")
        leaf = tree.add(f"[brand.muted]{n['id']:>4}[/] " + " ".join(bits))
        leaf.add(f"[brand.label]reason:[/] {n['reason']}")
        leaf.add(f"[brand.label]verify:[/] {n['verify']}")
    ripples = m.get("ripples") or []
    if ripples:
        branch = tree.add(f"[brand.label]ripples[/] [brand.muted]· {len(ripples)} graph-backed relation(s)[/]")
        for ripple in ripples:
            endpoint = ripple.get("endpoint") or {}
            branch.add(
                f"[conf.{ripple.get('confidence', 'unknown')}]{GLYPH.get(ripple.get('confidence', 'unknown'), '○')}[/] "
                f"{ripple.get('relation', '?')} → {endpoint.get('path', '?')}#{endpoint.get('symbol') or '?'} "
                f"[brand.muted]· {ripple.get('reason', '')}[/]"
            )
    return tree


def _map_footer(c: Console, m: dict) -> None:
    f = " · ".join(f"{k} {v}" for k, v in m["features"].items()) or "none"
    rad = m["radius"]
    res = m["resolution"]
    c.print(f"[brand.label]features  [/] {f}")
    c.print(f"[brand.label]radius    [/] {rad['files']} files · {rad['packages']} packages · {rad['exports']} exports · {rad['consumers']} consumers · {rad['tests']} tests")
    c.print(f"[brand.label]resolution[/] [conf.sound]{res['sound']} sound[/] · [conf.guessed]{res['guessed']} guessed[/] · [conf.unknown]{res['unknown']} unknown[/] — {res['qualifier']}")
    c.print(f"[brand.label]next      [/] [brand]{m['next']}[/]")


def render_map(m: dict, states: dict | None = None) -> None:
    c = _console()
    c.print(map_tree(m, states))
    _map_footer(c, m)


class LiveTree:
    """Redraws the map tree in place (~4 fps). Pass states from .amu/state.json; nothing is computed here."""

    def __init__(self, m: dict, enabled: bool = True):
        self.m = m
        self.enabled = enabled
        self.live = Live(map_tree(m, {}), console=_console(), refresh_per_second=4, transient=False) if enabled else None
        self.warnings: list[str] = []

    def __enter__(self):
        if self.live:
            self.live.__enter__()
        return self

    def update(self, states: dict, warnings: list[str] | None = None) -> None:
        if warnings:
            self.warnings = (self.warnings + warnings)[-6:]
        tree = map_tree(self.m, states)
        for w in self.warnings:
            tree.add(f"[state.drift]{w}[/]")
        if self.live:
            self.live.update(tree)
        else:
            _console().print(tree)

    def __exit__(self, *exc):
        if self.live:
            self.live.__exit__(*exc)


def render_plan(phases: list[dict], contract_path: str | None) -> None:
    c = _console()
    for p in phases:
        t = Tree(f"[brand]phase {p['n']} · {p['kind']}[/] [brand.muted]· {len(p['nodes'])} nodes · {len(p['files'])} files[/]", guide_style="brand.rule")
        t.add(f"[brand.label]reason:[/] {p['reason']}")
        t.add(f"[brand.label]nodes:[/] {', '.join(p['nodes']) or '—'}")
        t.add(f"[brand.label]files:[/] {', '.join(p['files']) or '—'}")
        t.add(f"[brand.label]verify:[/] {p['verify']}")
        c.print(t)
    q = phases[0].get("open_questions", []) if phases else []
    c.print(f"[brand.label]open questions[/] {len(q)}" + (": " + " | ".join(q) if q else ""))
    c.print(f"[brand.label]contract  [/] {'written to ' + contract_path if contract_path else 'not written (add --approve)'}")


def render_check(result: dict) -> None:
    c = _console()
    v = result["violations"]
    blocking = [x for x in v if x["severity"] == "ERROR"]
    warn = [x for x in v if x["severity"] != "ERROR"]
    c.print(f"[brand]phase {result.get('phase', 'all')}[/] · changed files {len(result.get('changed_files', []))} · [{'risk' if blocking else 'conf.sound'}]blocking {len(blocking)}[/] · warnings {len(warn)}")
    for x in v:
        c.print(f"  [{'risk' if x['severity'] == 'ERROR' else 'conf.guessed'}]{'✖' if x['severity'] == 'ERROR' else '⚠'} {x['kind']}[/] {x['symbol']}: {x['detail']}")
    if result.get("tests"):
        c.print(f"[brand.label]tests     [/] {result['tests']}")
    if result.get("build"):
        c.print(f"[brand.label]build     [/] {result['build']}")
    if result.get("delta_brief"):
        d = result["delta_brief"]
        c.print(f"[risk]delta brief:[/] {d['node']} · consumers {len(d['consumers'])} · {d['why']}")
    c.print(f"[brand.label]next      [/] [brand]{result['next']}[/]")


def render_findings(title: str, findings: list[dict], summary: str, nxt: str) -> None:
    c = _console()
    c.print(f"[brand]{title}[/] · {summary}")
    for f in findings:
        c.print(f"  [conf.{f.get('confidence', 'unknown')}]{GLYPH.get(f.get('confidence', 'unknown'), '○')}[/] {f.get('bucket', '?')} {f['path']}#{f.get('symbol')} — {f['reason']} · verify: {f['verify']}")
    c.print(f"[brand.label]next      [/] [brand]{nxt}[/]")


def render_report(report: dict) -> None:
    c = _console()
    c.print("[brand]amu done[/]")
    for k in ("declared", "actual", "findings", "docs", "snapshot", "memory", "next"):
        val = report.get(k)
        if isinstance(val, (list, dict)):
            val = json.dumps(val)
        c.print(f"[brand.label]{k:<10}[/] {val}")


def render_find(hits: list[dict], query: str, verify: dict | None) -> None:
    c = _console()
    c.print(f"[brand]find[/] [brand.muted]· {query} · {len(hits)} hits[/]")
    for i, h in enumerate(hits, 1):
        c.print(f"  [brand.muted]n{i}[/] [brand.body]{h['file_path']}:{h['focus_line']}[/] [brand]{h['symbol_name']}[/] [brand.muted]{h['kind']}[/]")
        c.print(f"      [brand.label]why:[/] {' · '.join(h['signals']) or 'no signals reported'}")
    if verify:
        c.print(f"[brand.label]verify    [/] {verify.get('command')} [brand.muted]({verify.get('tier')})[/]")


def render_repo_table(repos: list[dict], active: str | None) -> None:
    t = Table(box=None, header_style="brand.label", pad_edge=False)
    for col in ("", "name", "branch", "files/symbols/relations", "indexed", "path"):
        t.add_column(col)
    for r in repos:
        cnt = r.get("counts") or {}
        style = "brand" if r["name"] == active else "brand.body"
        t.add_row("▶" if r["name"] == active else " ", f"[{style}]{r['name']}[/]", r.get("branch", "?"),
                  f"{cnt.get('files', '?')}/{cnt.get('symbols', '?')}/{cnt.get('relations', '?')}", r.get("indexed_rel", "never"), f"[brand.muted]{r['path']}[/]")
    _console().print(t)


def render_dir_tree(root: Path, rows: dict[str, dict], depth: int) -> None:
    """rows: relative path → {exports, feature} (computed by the caller)."""
    tree = Tree(f"[brand]{root.name}/[/]", guide_style="brand.rule")

    def walk(d: Path, node: Tree, level: int) -> None:
        if level > depth:
            return
        for p in sorted(d.iterdir(), key=lambda x: (x.is_file(), x.name)):
            if p.name.startswith(".") or p.name in ("__pycache__", "node_modules"):
                continue
            rel = str(p.relative_to(root))
            if p.is_dir():
                walk(p, node.add(f"[brand.body]{p.name}/[/]"), level + 1)
            else:
                meta = rows.get(rel, {})
                extra = f" [brand.muted]· {meta['exports']} exports · {meta['feature']}[/]" if meta else ""
                node.add(f"{p.name}{extra}")
    walk(root, tree, 1)
    _console().print(tree)


def agent_card(m: dict) -> dict:
    return {"brand": "TWC Thugs", "tiles": {**{k: m["resolution"][k] for k in ("sound", "guessed", "unknown")}, "ripples": len(m.get("ripples", []))},
            "rows": [{"id": n["id"], "path": n["path"], "symbol": n["symbol"], "badges": [n["confidence"], n["risk"]], "verify": n["verify"]} for n in m["nodes"]],
            "approve_enabled": m.get("planner_decision") == "proceed"}
