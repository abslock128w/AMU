"""amu — Anti-Messup (TWC Thugs). Typer entrypoint, also installed as `entire-amu` so `entire amu …` dispatches here.

Exit codes: 0 ok · 1 blocking violation / red · 2 stale contract or map · 3 tooling missing.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer

from amu import (
    __version__,
    brand,
    docgraph,
    entire,
    freshness,
    graph_readiness,
    graph_refresh,
    graphview,
    keys,
    lifecycle,
    memory,
    process,
    render,
    router,
    source_evidence,
    state,
    unknowns,
    workspace,
)
from amu import (
    docreview as docreview,
)
from amu import docs as docsmod
from amu import watch as watchmod
from amu.contract import (
    Severity,
    Violation,
    ViolationKind,
    check_contract,
    parse_graph_diff,
    worktree_semantic_violations,
)
from amu.harness.orchestrator import Orchestrator
from amu.plan import documentation_scope, make_contract, plan_from_map
from amu.services import documentation as docservice
from amu.services import graph_sync as syncservice
from amu.services import mapping as mapservice
from amu.services.mapping import GraphDataError as GraphDataError


class StateGuardGroup(typer.core.TyperGroup):
    """Use the same fail-closed state error shape in console, REPL and MCP."""

    def invoke(self, ctx):
        json_mode = "--json" in ctx.args
        try:
            return super().invoke(ctx)
        except state.StateReadError as exc:
            if json_mode:
                typer.echo(json.dumps({"error": str(exc), "error_kind": "state_unreadable", "exit": 2}))
            else:
                typer.echo(str(exc), err=True)
            raise typer.Exit(code=2) from exc


app = typer.Typer(
    cls=StateGuardGroup,
    help="amu · Anti-Messup · TWC Thugs — scope guardrail for AI code edits, built on the Entire CLI",
    epilog=brand.EPILOG,
    no_args_is_help=False,
    invoke_without_command=True,
    add_completion=False,
)
memory_app = typer.Typer(help="self-renovating CLAUDE.md memory block")
skills_app = typer.Typer(help="built-in and repo skills")
app.add_typer(memory_app, name="memory")
app.add_typer(skills_app, name="skills")

JSON_OPT = typer.Option(False, "--json", help="machine-readable output (branding suppressed)")
_NO_REPO_NEEDED = {"doctor", "list", "run", "add", "use", "remove", "sync", "cd", "set", "status", None}
_CHANGE_CLASSES = {"signature", "remove", "rename", "move", "body"}


def _resolve_repo_cb(ctx: typer.Context, value: str | None) -> str:
    """--repo flag → repo containing $PWD → workspace active repo → error with the add hint."""
    try:
        return workspace.resolve(value)
    except workspace.WorkspaceError as exc:
        if ctx.command.name in _NO_REPO_NEEDED or ctx.resilient_parsing:
            return value or "."
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=exc.code)


REPO_OPT = typer.Option(
    None,
    "--repo",
    help="repository path (default: repo containing $PWD, else the active workspace repo)",
    callback=_resolve_repo_cb,
)
repo_app = typer.Typer(help="workspace: add / list / use / remove / sync repos")
app.add_typer(repo_app, name="repo")
key_app = typer.Typer(help="model credential: set / status / remove (value is never printed)")
app.add_typer(key_app, name="key")


def _out(obj, json_mode: bool):
    if json_mode:
        typer.echo(json.dumps(obj, indent=2))


def _input_error(message: str, json_mode: bool) -> None:
    typer.echo(json.dumps({"error": message, "exit": 2}) if json_mode else message, err=not json_mode)
    raise typer.Exit(code=2)


def _lifecycle_error(message: str, incomplete: list[dict], json_mode: bool, code: int, next_command: str | None = None) -> None:
    """Return a machine-readable phase gate without running a later lifecycle step."""
    next_phase = incomplete[0]["n"] if incomplete else None
    payload = {
        "error": message,
        "incomplete_phases": incomplete,
        "next": next_command or (f"amu check --phase {next_phase}" if next_phase is not None else "amu plan --approve"),
        "exit": code,
    }
    typer.echo(json.dumps(payload) if json_mode else f"{message}\nnext: {payload['next']}", err=not json_mode)
    raise typer.Exit(code=code)


def _repo_local_path(repo: str, raw_path: str) -> bool:
    """Only accept lexical repo-relative map paths, including through symlinks."""
    path = Path(raw_path)
    if not raw_path or path.is_absolute() or ".." in path.parts:
        return False
    try:
        (Path(repo).resolve() / path).resolve().relative_to(Path(repo).resolve())
    except (OSError, RuntimeError, ValueError):
        return False
    return True


def _need_entire(json_mode: bool):
    if not entire.available() or not entire.graph_available():
        _graph_failure("entire CLI or graph plugin missing", json_mode)


def _graph_failure(message: str, json_mode: bool) -> None:
    msg = {"error": message, "install": entire.INSTALL_HINT, "exit": 3}
    typer.echo(json.dumps(msg) if json_mode else f"{message}. Install:\n  {entire.INSTALL_HINT}", err=not json_mode)
    raise typer.Exit(code=3)


def _record_index_readiness(repo: str, index: object) -> dict:
    """Persist cache-index health without mistaking it for live map evidence."""
    return syncservice.record_index_readiness(_graph_service_repo(repo, syncservice.GraphSyncError), index)


def _lookup_factory(repo: str):
    syms = None

    def lookup(token: str) -> list[dict]:
        nonlocal syms
        if syms is None:
            rows, error = entire.symbols_result(repo) if entire.available() else ([], None)
            if error:
                raise GraphDataError(f"symbol lookup unavailable: {error}")
            syms = rows
        hits = [
            {"symbol": s.get("qualified_name") or s.get("name"), "path": s.get("file_path")}
            for s in syms
            if s.get("record_type", "symbol") == "symbol" and token in (s.get("name"), s.get("qualified_name"))
        ]
        return hits

    return lookup


@app.callback()
def main(ctx: typer.Context, version: bool = typer.Option(False, "--version", help="print version and exit")):
    if version:
        typer.echo(f"amu {__version__} · TWC Thugs · entire {entire.version()} · entire-graph {entire.graph_version()}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        _repl(None)


# ---------------------------------------------------------------------------
# init / doctor
# ---------------------------------------------------------------------------


@app.command()
def init(repo: str = REPO_OPT, json_output: bool = JSON_OPT):
    """Index the repo with entire graph, snapshot it, draft config / feature map / doc map / repo skills."""
    brand.header("init", state.repo_name(repo), json_output)
    _need_entire(json_output)
    idx = entire.index(repo)
    readiness = _record_index_readiness(repo, idx)
    if not readiness["ready"]:
        _graph_failure(f"graph index is not semantically ready: {graph_readiness.summary(readiness)}", json_output)
    # Initialization is allowed on an edited checkout; snapshot exactly the
    # worktree that the following map/plan will inspect.
    snap = entire.snapshot(repo, str(state.amu_dir(repo) / "snapshot.ndjson"), worktree=True)
    if "error" in snap:
        _graph_failure(f"entire graph snapshot failed: {snap['error']}", json_output)
    caps = entire.capabilities(repo)
    cfg = {
        **state.config(repo),
        **state.detect_commands(repo),
        "docs_dir": "docs",
        "docs": {"mode": "draft"},
        "languages": list(
            (caps.get("languages") or caps.get("parsed_languages") or {})
            if isinstance(caps.get("languages"), dict)
            else []
        ),
        "unparsed": [pf.get("file_path") for pf in idx.get("partial_failures", []) if pf.get("file_path")],
    }
    state.write_json("config.json", cfg, repo)
    top = sorted(
        {p.split("/")[0] for p in os.listdir(repo) if os.path.isdir(os.path.join(repo, p)) and not p.startswith(".")}
    )
    if not state.read_json("feature_map.json", repo):
        state.write_json("feature_map.json", {d.capitalize(): [f"{d}/**"] for d in top}, repo)
    if not state.read_json("doc-map.json", repo):
        state.write_json("doc-map.json", docsmod.draft_doc_map(repo, cfg["docs_dir"]), repo)
    docs_graph = docgraph.sync(repo, cfg["docs_dir"])
    sk = state.amu_dir(repo) / "skills"
    for name, body in {
        "run-tests": f"- run tests: `{cfg['test_cmd']}`",
        "build": f"- build: `{cfg['build_cmd'] or 'none detected'}`",
        "conventions": "- tests live in tests/, one file per module",
    }.items():
        (sk / name).mkdir(parents=True, exist_ok=True)
        if not (sk / name / "SKILL.md").exists():
            (sk / name / "SKILL.md").write_text(f"# {name}\n\n{body}\n")
    counts = idx.get("counts", {})
    memory.refresh(repo)
    summary = {
        "files": counts.get("files"),
        "symbols": counts.get("symbols"),
        "relations": counts.get("relations"),
        "snapshot_lines": snap.get("lines"),
        "unparsed": cfg["unparsed"],
        "docs_graph": docs_graph["graph"]["summary"],
        "next": "amu map --file <path>",
    }
    _out(summary, json_output)
    if not json_output:
        typer.echo(
            f"indexed {summary['files']} files · {summary['symbols']} symbols · {summary['relations']} relations · snapshot {summary['snapshot_lines']} lines"
        )
        typer.echo(
            f"unparsed  {len(cfg['unparsed'])} file(s): {', '.join(cfg['unparsed'][:5]) or 'none reported by the graph'}"
        )
        typer.echo(f"next      {summary['next']}")


@app.command()
def doctor(
    repo: str = typer.Option(None, "--repo", help="explicit repository root; defaults to current directory without workspace changes"),
    json_output: bool = JSON_OPT,
    mcp: bool = typer.Option(False, "--mcp", help="verify an isolated MCP initialize/tools-list probe (no live config changes)"),
    mcp_python: str = typer.Option(None, "--mcp-python", help="explicit Python executable for --mcp; default is this runtime"),
):
    """Read-only package, graph/repository health and optional MCP diagnostics."""
    from amu.services import diagnostics

    repo = repo or str(Path.cwd())
    brand.header("doctor", Path(repo).name or repo, json_output)
    try:
        d = diagnostics.execute(repo, mcp=mcp, mcp_python=mcp_python)
    except diagnostics.DiagnosticInputError as exc:
        _input_error(str(exc), json_output)
    _out(d, json_output)
    if not json_output:
        typer.echo(f"doctor · {'ready' if d['ready'] else 'not ready'}")
        identity = d["identity"]
        typer.echo(f"amu {identity.get('amu_version', 'unknown')} · Python {identity.get('python_version', 'unknown')} · entire {d['entire']} · graph {d['graph']}")
        typer.echo(f"runtime {identity.get('executable', 'unknown')} · package {identity.get('module', 'unknown')} · build {(identity.get('build_sha256') or 'unknown')[:16]}")
        for check in d["checks"]:
            if check.get("required"):
                kind = f" ({check['error_kind']})" if check.get("error_kind") else ""
                typer.echo(f"{check['name']}: {check['status']}{kind} · {check.get('message', '')}")
        if d["mcp"]["requested"]:
            protocol = d["mcp"].get("protocol", {})
            typer.echo(f"MCP runtime {d['mcp']['selected_executable']} · {d['mcp']['status']}")
            names = protocol.get("tool_names", [])
            if isinstance(names, list):
                typer.echo(f"advertised tools: {', '.join(name for name in names if isinstance(name, str)) or 'not established'}")
        for action in d["repository"].get("next_actions", []):
            typer.echo(f"next: {action}")
        if d["install"]:
            typer.echo(f"install: {d['install']}")
        typer.echo("Local selection does not change MCP repository grants.")
    if not d["ready"]:
        raise typer.Exit(code=d["exit"])


# ---------------------------------------------------------------------------
# map / brief
# ---------------------------------------------------------------------------


def _map_impact_coverage(root_file: str, impacts: dict[str, object], requested_depth: int) -> dict[str, dict]:
    """Persist per-target pagination evidence needed for a later edge delta.

    A map can still be useful when the provider truncates an impact section,
    but it cannot be a comparison baseline: omitted old edges would otherwise
    appear as newly added after an edit.  Store what the provider actually
    returned, after source-evidence filtering, so refresh can reject that map
    precisely instead of guessing.
    """
    return mapservice.impact_coverage(root_file, impacts, requested_depth)


def _map_worktree_fingerprint(repo: str, stage: str) -> dict:
    """Capture a map's editable state or fail before persisting graph claims."""
    return mapservice.worktree_fingerprint(repo, stage)


def _graph_service_repo(repo: str, error_type) -> str:
    """Normalize an adapter-selected path; core never selects a repository."""
    if not isinstance(repo, str) or not repo.strip():
        raise error_type("repo must be an explicit directory")
    try:
        return str(Path(repo).resolve())
    except (OSError, RuntimeError, ValueError) as exc:
        raise error_type("repo must be an existing directory") from exc


def _do_map(repo: str, file: str | None, symbol: str | None, depth: int, change: str) -> dict:
    """Compatibility adapter: repository selection/progress stay in the CLI."""
    selected = _graph_service_repo(repo, mapservice.MapInputError)
    request = mapservice.MapRequest(file=file, symbol=symbol, depth=depth, change=change)
    with brand.progress("resolving…") as sp:
        return mapservice.execute(selected, request, progress=sp.update)


def _map_or_graph_failure(
    repo: str, file: str | None, symbol: str | None, depth: int, change: str, json_mode: bool = False
) -> dict:
    """Build a map only when its required symbol inventory is trustworthy."""
    try:
        return _do_map(repo, file, symbol, depth, change)
    except mapservice.MapInputError as exc:
        _input_error(str(exc), json_mode)
        raise AssertionError("_input_error always exits")  # pragma: no cover
    except GraphDataError as exc:
        _graph_failure(str(exc), json_mode)
        raise AssertionError("_graph_failure always exits")  # pragma: no cover


def _map_approval_problem(repo: str, amu_map: dict) -> str | None:
    """Return why a persisted map cannot be promoted into a scope contract."""
    evidence = amu_map.get("graph_evidence")
    if not isinstance(evidence, dict):
        return "map has no verified live graph evidence; re-run amu map before approving"
    if evidence.get("map_hash") != amu_map.get("map_hash"):
        return "map graph evidence does not match this map; re-run amu map before approving"
    worktree_problem = graph_refresh.map_current_capture_reason(repo, evidence)
    if worktree_problem:
        return f"{worktree_problem}; re-run amu map before approving"
    if not evidence.get("approvable"):
        return f"map graph evidence is incomplete: {graph_readiness.summary(evidence)}"
    root = amu_map.get("root")
    root_file = root.get("file") if isinstance(root, dict) else None
    depth = amu_map.get("depth")
    queried_symbols = evidence.get("queried_symbols")
    if not isinstance(root_file, str) or not root_file or not isinstance(depth, int) or isinstance(depth, bool) or depth < 1:
        return "map has no valid exact-target baseline metadata; re-run amu map before approving"
    if not isinstance(queried_symbols, list) or not queried_symbols:
        return "map has no exact queried-symbol evidence; re-run amu map before approving"
    for symbol in queried_symbols:
        if not isinstance(symbol, str) or not symbol:
            return "map has malformed queried-symbol evidence; re-run amu map before approving"
        target = f"{root_file}#{symbol}"
        baseline = graph_refresh.map_baseline(target, depth, amu_map)
        if not baseline.available:
            return f"map cannot provide a complete exact baseline for {target}: {baseline.reason}; re-run amu map before approving"
    return None


def _plan_target_problem(amu_map: dict, targets: list[dict]) -> str | None:
    """Ensure an approved plan cannot name a target the map never queried.

    A contract's refresh baseline is exact by design.  Letting ``plan
    --approve --targets other.py#symbol`` borrow a map for ``root.py#root``
    creates an apparently approved contract that can only fail later at
    refresh time.  Reject it before any lifecycle state is written.
    """
    root = amu_map.get("root") if isinstance(amu_map, dict) else None
    root_file = root.get("file") if isinstance(root, dict) else None
    evidence = amu_map.get("graph_evidence") if isinstance(amu_map, dict) else None
    queried = evidence.get("queried_symbols") if isinstance(evidence, dict) else None
    if not isinstance(root_file, str) or not root_file or not isinstance(queried, list):
        return "map has no exact queried-target evidence; re-run amu map before approving"
    queried_symbols = {symbol for symbol in queried if isinstance(symbol, str) and symbol}
    for item in targets:
        target = item.get("symbol") if isinstance(item, dict) else None
        if not isinstance(target, str):
            return "approved targets must use path#symbol form; re-run amu map before approving"
        path, separator, symbol = target.partition("#")
        if not separator or not path or not symbol or "#" in symbol:
            return f"approved target {target!r} must use path#symbol form"
        if path != root_file:
            return f"approved target {target} is outside mapped file {root_file}; re-run amu map for that exact target"
        if symbol == "*":
            return "approved targets require exact queried symbols; re-run amu map for the intended target"
        if symbol not in queried_symbols:
            return f"approved target {target} was not queried by the current map; re-run amu map for that exact target"
    return None


@app.command()
def map(
    file: str = typer.Option(None, "--file"),
    symbol: str = typer.Option(None, "--symbol", help="path#name"),
    depth: int = typer.Option(2, "--depth"),
    change: str = typer.Option("signature", "--change", help="signature|remove|rename|move|body"),
    repo: str = REPO_OPT,
    json_output: bool = JSON_OPT,
):
    """Blast map: sound / guessed / unknown nodes with reason + verify, grouped by feature."""
    brand.header("map", state.repo_name(repo), json_output)
    if bool(file) == bool(symbol):
        _input_error("provide exactly one of --file or --symbol path#name", json_output)
    if change not in _CHANGE_CLASSES:
        _input_error(f"invalid --change {change!r}; choose one of: {', '.join(sorted(_CHANGE_CLASSES))}", json_output)
    if depth < 1:
        _input_error("--depth must be at least 1", json_output)
    target_path = file
    if symbol:
        target_path, separator, name = symbol.partition("#")
        if not separator or not target_path or not name:
            _input_error("--symbol must use the form path#name", json_output)
    if not _repo_local_path(repo, target_path or ""):
        _input_error("map target must be a repository-relative path", json_output)
    _need_entire(json_output)
    m = _map_or_graph_failure(repo, file, symbol, depth, change, json_output)
    _out(m, json_output)
    if not json_output:
        render.render_map(m)


@app.command(hidden=True)
def brief(
    symbol: str = typer.Option(..., help="Target symbol"),
    depth: int = typer.Option(2),
    repo: str = REPO_OPT,
    json_output: bool = JSON_OPT,
):
    """Retired raw-impact compatibility command; it cannot make safe graph claims."""
    _input_error(
        "amu brief is retired because it used unverified raw graph text; run "
        "amu map --symbol path/to/file.py#symbol --change <class> instead",
        json_output,
    )


@app.command(name="impact")
def impact_cmd(
    file: str = typer.Option(..., "--file", help="repository-relative current-worktree source file"),
    line: int = typer.Option(..., "--line", help="one-based source line to inspect"),
    proposed_change: str = typer.Option(..., "--proposed-change", help="required change intent; never executed"),
    depth: int = typer.Option(2, "--depth", help="bounded relationship depth, 1–3"),
    change: str = typer.Option("body", "--change", help="signature|remove|rename|move|body"),
    section: str = typer.Option("impact", "--section", help="page impact or retained safety evidence"),
    cursor: str | None = typer.Option(None, "--cursor", help="opaque live-evidence cursor; stale cursors are refused"),
    page_size: int = typer.Option(50, "--page-size", help="page entries, 1–100"),
    repo: str = REPO_OPT,
    json_output: bool = JSON_OPT,
):
    """Inspect potential impact at a source line without saving a map or executing a change."""
    from amu.services import impact as impact_service

    try:
        result = impact_service.execute(repo, impact_service.ImpactRequest(
            file=file, line=line, proposed_change=proposed_change, depth=depth, change_kind=change,
            section=section, cursor=cursor, page_size=page_size,
        ))
    except impact_service.ImpactInputError as exc:
        _out(exc.payload, json_output)
        if not json_output:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=exc.exit_code) from exc
    _out(result, json_output)
    if not json_output:
        target = result.get("target") or {}
        typer.echo(f"impact · {result['status']} · {target.get('file', file)}:{line} · {target.get('symbol') or 'unresolved target'}")
        typer.echo("Potential impact only; this does not establish that an edit is safe. The proposal was not executed.")
        for reason in result.get("assessment", {}).get("reasons", []):
            typer.echo(f"  {reason}")
        typer.echo("Use --json for ranked relationships, source capture and coverage details.")


# ---------------------------------------------------------------------------
# plan / check / verify
# ---------------------------------------------------------------------------


@app.command()
def plan(
    targets: str = typer.Option("", "--targets", help="path#sym:class,…"),
    approve: bool = typer.Option(False, "--approve"),
    symbol: str = typer.Option(None, "--symbol", help="retired; map an exact path#symbol first"),
    repo: str = REPO_OPT,
    json_output: bool = JSON_OPT,
    docs_files: Annotated[list[str] | None, typer.Option("--docs-file", help="explicit existing Markdown/RST path to approve for the final phase; repeatable (max 100)")] = None,
):
    """4-phase plan (additive → leaves → interior → removal). --approve writes .amu/contract.json."""
    brand.header("plan", state.repo_name(repo), json_output)
    try:
        scoped_docs = documentation_scope(repo, docs_files)
    except (OSError, ValueError, RuntimeError) as exc:
        _input_error(f"invalid docs scope: {exc}", json_output)
    if symbol and not targets:
        _input_error(
            "amu plan --symbol is retired because it used unverified raw graph text; run "
            "amu map --symbol path/to/file.py#symbol --change <class>, then amu plan",
            json_output,
        )
    m = state.read_json("map.json", repo)
    if not m:
        typer.echo("no map yet — run amu map first", err=True)
        raise typer.Exit(code=2)
    tl = [
        {
            "symbol": t.rsplit(":", 1)[0],
            "change_class": (t.rsplit(":", 1)[1] if ":" in t else m.get("change", "signature")),
        }
        for t in targets.split(",")
        if t.strip()
    ] or [{"symbol": f"{m['root']['file']}#*", "change_class": m.get("change", "signature")}]
    # A wildcard can describe a draft scope, but cannot be refreshed after
    # edits. Expand only the exact symbols already queried by this map.
    queried = (m.get("graph_evidence") or {}).get("queried_symbols") or []
    exact_symbols = sorted({name for name in queried if isinstance(name, str) and name and name != "*"})
    tl = [
        expanded
        for target in tl
        for expanded in (
            [{**target, "symbol": f"{m['root']['file']}#{name}"} for name in exact_symbols]
            if target["symbol"] == f"{m['root']['file']}#*" and exact_symbols
            else [target]
        )
    ]
    invalid_classes = sorted({target["change_class"] for target in tl if target["change_class"] not in _CHANGE_CLASSES})
    if invalid_classes:
        _input_error(f"invalid target change class: {', '.join(invalid_classes)}", json_output)
    phases = plan_from_map(m, tl, scoped_docs)
    orch = Orchestrator(repo, task=m["map_hash"])
    decision = orch.plan_decision(m)
    path = None
    builder = None
    approval_refused = None
    if approve:
        target_problem = _plan_target_problem(m, tl)
        if target_problem:
            approval_refused = target_problem
        else:
            evidence_problem = _map_approval_problem(repo, m)
            if evidence_problem:
                approval_refused = evidence_problem
        if approval_refused is None and decision.get("decision") != "proceed":
            approval_refused = "planner did not proceed; resolve or narrow the flagged scope before approving"
        elif approval_refused is None:
            # The planner can take arbitrarily long (including an optional
            # model call).  Re-check the graph map immediately before any
            # contract write so a dirty edit that landed after the first
            # approval check cannot be stamped as pre-edit scope evidence.
            final_evidence_problem = _map_approval_problem(repo, m)
            if final_evidence_problem:
                approval_refused = final_evidence_problem
            else:
                base_sha = state.git_head(repo)
                mapped_head = m["graph_evidence"]["worktree"]["after"].get("head")
                if base_sha != mapped_head:
                    approval_refused = "repository changed while approving the map; re-run amu map before approving"
                else:
                    try:
                        final_docs = documentation_scope(repo, docs_files)
                    except (OSError, ValueError, RuntimeError) as exc:
                        _input_error(f"docs scope changed before approval: {exc}", json_output)
                    if final_docs != scoped_docs:
                        _input_error("docs scope changed before approval; review the plan again", json_output)
                    c = make_contract(m, phases, tl, base_sha, state.now_iso())
                    state.validate("contract", c)
                    path = str(state.write_json("contract.json", c, repo))
                    state.write_json(f"contracts/{c['created_at'].replace(':', '')}.json", c, repo)
                    state.write_json(
                        "state.json",
                        {
                            "nodes": {n["id"]: "planned" for n in m["nodes"]},
                            "violations": [],
                            "history": [],
                            "updated_at": state.now_iso(),
                        },
                        repo,
                    )
                    orch.reset_budget()
                    first_phase = next(iter(lifecycle.ordered_phases(c)), None)
                    if first_phase is not None:
                        # This is a persisted, read-only builder brief.  It exposes the
                        # phase scope before a human or external coding agent edits it.
                        builder = orch.phase_brief(c, first_phase["n"], m)
    out = {
        "targets": tl,
        "docs_files": scoped_docs,
        "phases": phases,
        "planner": decision,
        "contract": path,
        "map_hash": m["map_hash"],
        "approval_refused": approval_refused,
        "builder": builder,
    }
    _out(out, json_output)
    if not json_output:
        typer.echo(f"planner    {decision['decision']} (confidence {decision['confidence']}) · {decision['next']}")
        if approval_refused:
            typer.echo(f"approval   refused · {approval_refused}", err=True)
        else:
            render.render_plan(phases, path)
            if builder:
                typer.echo(f"builder    phase {builder['phase']['n']} brief recorded · {builder['next']}")
    if approval_refused:
        raise typer.Exit(code=2)


def _test_command_kind(argv: list[str]) -> str | None:
    """Return a supported runner kind, or None for an unsafe/unsupported command."""
    if not argv:
        return None
    executable = Path(argv[0]).name
    if executable in {"pytest", "py.test"}:
        return "pytest"
    if executable.startswith("python") and argv[1:3] == ["-m", "pytest"]:
        return "pytest"
    if argv[:2] == ["npm", "test"] or argv[:3] == ["npm", "run", "test"]:
        return "npm"
    if argv[:2] == ["go", "test"]:
        return "go"
    return None


def _build_command_kind(argv: list[str]) -> str | None:
    """Return a supported build command kind, or None when it is not safe to run."""
    if not argv:
        return None
    executable = Path(argv[0]).name
    if executable.startswith("python") and argv[1:3] in (["-m", "build"], ["-m", "compileall"]):
        return "python"
    if argv[:3] == ["npm", "run", "build"]:
        return "npm"
    if argv[:2] == ["go", "build"]:
        return "go"
    if argv[:2] == ["cargo", "build"]:
        return "cargo"
    return None


def _test_target_is_local(repo: str, target: str) -> bool:
    """Pytest node ids must resolve inside the checked repository, never outside it."""
    path = target.split("::", 1)[0]
    if not path or path.startswith("-"):
        return False
    root = Path(repo).resolve()
    try:
        (root / path).resolve().relative_to(root)
    except ValueError:
        return False
    return True


def _test_result(command: str, returncode: int | None, passed: bool, summary: str) -> dict:
    return {"command": command, "returncode": returncode, "passed": passed, "summary": summary[:200]}


def _pytest_summary_counts(summary: str) -> dict[str, int] | None:
    """Accept the final pytest terminal summary, not incidental repository stdout."""
    if len(summary) > 2000:
        return None
    cleaned = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", summary).strip()
    count = r"\d+ (?:passed|failed|skipped|deselected|xfailed|xpassed|warnings?|errors?)"
    match = re.fullmatch(
        rf"=*[ \t]*(?P<counts>{count}(?:, {count})*) in \d+(?:\.\d+)?s(?: \([\d:]+\))?[ \t]*=*",
        cleaned,
    )
    if match is None:
        return None
    counts: dict[str, int] = {}
    for entry in match["counts"].split(", "):
        number, category = entry.split(" ", 1)
        category = {"warning": "warnings", "error": "errors"}.get(category, category)
        if category in counts:
            return None
        counts[category] = int(number)
    return counts


RUNNER_STDOUT_LIMIT = 16 * 1024 * 1024
RUNNER_STDERR_LIMIT = 64 * 1024
RUNNER_TIMEOUT_SECONDS = 600


def _run_argv(repo: str, argv: list[str], *, pytest_evidence: bool = False) -> dict:
    """Run validated argv with 16 MiB stdout, 64 KiB stderr and one 600s deadline."""
    command = shlex.join(argv)
    try:
        run = process.run(
            argv, cwd=repo, timeout=RUNNER_TIMEOUT_SECONDS,
            stdout_limit=RUNNER_STDOUT_LIMIT, stderr_limit=RUNNER_STDERR_LIMIT,
        )
    except (ValueError, OSError) as exc:
        return _test_result(command, None, False, f"could not run command: {exc}")
    if run.error:
        summary = {
            "missing": f"runner not found: {argv[0]}",
            "timeout": f"command timed out after {RUNNER_TIMEOUT_SECONDS:g}s; no partial output was accepted",
            "output_limit": (
                f"command exceeded output byte limit (stdout {RUNNER_STDOUT_LIMIT}, stderr {RUNNER_STDERR_LIMIT}); "
                "no partial output was accepted"
            ),
        }.get(run.error, "command failed while reading output; no partial output was accepted")
        return _test_result(command, run.returncode, False, summary)
    stdout = run.stdout.decode("utf-8", errors="replace")
    stderr = run.stderr.decode("utf-8", errors="replace")
    lines = (stdout.strip() or stderr.strip() or "no output").splitlines()
    result = _test_result(command, run.returncode, run.returncode == 0, lines[-1])
    if pytest_evidence:
        stdout_lines = stdout.strip().splitlines()
        final_summary = stdout_lines[-1] if stdout_lines else ""
        counts = _pytest_summary_counts(final_summary)
        passing_verified = counts is not None and counts.get("passed", 0) > 0
        result["evidence"] = {
            "runner": "pytest",
            "source": "final_stdout_summary",
            "summary": final_summary[:200],
            "counts": counts,
            "passing_tests_verified": passing_verified,
            "reason": None if passing_verified else "no_passing_tests" if counts is not None else "summary_unavailable",
        }
        if run.returncode == 0 and not passing_verified:
            result["passed"] = False
            result["summary"] = (
                "pytest exited 0 without verified passing tests; configure pytest -q with a visible final summary; "
                f"reported: {result['summary']}"
            )[:200]
    return result


def _run_tests(repo: str, phase_tests: list[str], cfg: dict) -> dict:
    """Run a declared test command without a shell and return its real outcome.

    `.amu/config.json` is repository data, so it must never be interpreted as a
    shell program. The accepted runners match the commands generated by
    ``state.detect_commands`` and keep per-phase pytest node ids repo-local.
    """
    raw = str(cfg.get("test_cmd") or "pytest -q")
    if any(char in raw for char in ";|&<>"):
        return _test_result(raw, None, False, "unsupported shell control character in test command")
    try:
        argv = shlex.split(raw)
    except ValueError as exc:
        return _test_result(raw, None, False, f"invalid test command: {exc}")
    kind = _test_command_kind(argv)
    if not kind:
        return _test_result(
            raw, None, False, "unsupported test command; use pytest, python -m pytest, npm test, or go test"
        )
    if phase_tests and kind == "pytest":
        bad = next((target for target in phase_tests if not _test_target_is_local(repo, target)), None)
        if bad is not None:
            return _test_result(raw, None, False, f"refusing test target outside the repository: {bad}")
        argv.extend(phase_tests)
    return _run_argv(repo, argv, pytest_evidence=kind == "pytest")


def _run_build(repo: str, cfg: dict) -> dict | None:
    """Run a configured build directly, or report a clear failure instead of a green omission."""
    raw = str(cfg.get("build_cmd") or "").strip()
    if not raw:
        return None
    if any(char in raw for char in ";|&<>"):
        return _test_result(raw, None, False, "unsupported shell control character in build command")
    try:
        argv = shlex.split(raw)
    except ValueError as exc:
        return _test_result(raw, None, False, f"invalid build command: {exc}")
    if not _build_command_kind(argv):
        return _test_result(
            raw,
            None,
            False,
            "unsupported build command; use python -m build/compileall, npm run build, go build, or cargo build",
        )
    return _run_argv(repo, argv)


def _is_graph_relevant_change(repo: str, path: str) -> bool:
    """Conservatively identify a changed path that can alter code relations."""
    return freshness.is_code_path(path)


def _refresh_contract_targets(contract: dict) -> list[tuple[str, str]]:
    """Return exact contract targets and their change class, never guessing wildcards."""
    rows: list[tuple[str, str]] = []
    for item in contract.get("targets", []):
        if not isinstance(item, dict):
            continue
        target = item.get("symbol")
        change = item.get("change_class") or "body"
        if isinstance(target, str) and _refresh_target_parts(target) and not target.endswith("#*"):
            rows.append((target, str(change)))
    return sorted(set(rows))


def _refresh_next(contract: dict) -> str:
    targets = _refresh_contract_targets(contract)
    if not targets:
        return "run amu map for an exact path#symbol, then approve a fresh plan"
    target, change = targets[0]
    extra = " --new-symbol <path#name>" if change in {"rename", "move"} else ""
    return f"amu refresh --symbol {target} --change {change}{extra}"


def _refresh_covered_paths(targets: list[tuple[str, str]], records: dict) -> set[str]:
    """Return files whose graph changes are covered by exact refresh targets.

    A map rooted at ``a.py#root`` can show callers in other files, but an
    impact refresh for that one root is not evidence about every relationship
    newly introduced inside those caller files.  Treat only exact approved
    targets as post-edit graph coverage.  A completed move refresh additionally
    covers its explicitly verified destination path.
    """
    covered: set[str] = set()
    for target, change in targets:
        parts = _refresh_target_parts(target)
        if parts is not None:
            covered.add(parts[0])
        if change != "move":
            continue
        record = records.get(target)
        if not isinstance(record, dict) or record.get("complete") is not True or record.get("change") != "move":
            continue
        live_parts = _refresh_target_parts(record.get("live_target"))
        if live_parts is not None:
            covered.add(live_parts[0])
    return covered


def _refresh_gate_violations(repo: str, contract: dict, changed_files: set[str]) -> list[Violation]:
    """Require post-edit graph evidence before a contract can go green.

    A refresh record is only fresh for both the current commit and the exact
    current worktree digest.  Merely writing a snapshot (as ``done`` does for
    reporting) is never substituted for the target-specific live query.
    """
    if not contract.get("targets"):
        return []
    relevant = sorted(path for path in changed_files if _is_graph_relevant_change(repo, path))
    if not relevant:
        return []
    targets = _refresh_contract_targets(contract)
    if not targets:
        return [
            Violation(
                ViolationKind.GRAPH_REFRESH_REQUIRED,
                "<contract>",
                "graph-relevant files changed but the contract has no exact path#symbol refresh target",
                Severity.ERROR,
            )
        ]
    saved = state.read_json("refresh-state.json", repo, default={})
    records = saved.get("targets") if isinstance(saved, dict) and isinstance(saved.get("targets"), dict) else {}
    covered_paths = _refresh_covered_paths(targets, records)
    failures: list[Violation] = [
        Violation(
            ViolationKind.GRAPH_REFRESH_REQUIRED,
            path,
            "graph-relevant file changed outside the exact mapped targets; re-run amu map and approve a contract that explicitly maps this file",
            Severity.ERROR,
        )
        for path in relevant
        if path not in covered_paths
    ]
    fingerprint = graph_refresh.graph_capture(repo)
    if fingerprint.get("error") or not fingerprint.get("digest"):
        return [
            *failures,
            Violation(
                ViolationKind.GRAPH_REFRESH_REQUIRED,
                "<worktree>",
                f"cannot verify post-edit graph freshness: {fingerprint.get('error') or 'no worktree digest'}",
                Severity.ERROR,
            )
        ]
    current_head = state.git_head(repo)
    current_map = state.read_json("map.json", repo, default={})
    expected_depth = current_map.get("depth") if isinstance(current_map, dict) else None
    current_provider = entire.graph_version()
    for target, _change in targets:
        record = records.get(target)
        if not isinstance(record, dict) or not record.get("complete"):
            failures.append(
                Violation(
                    ViolationKind.GRAPH_REFRESH_REQUIRED,
                    target,
                    "no successful post-edit graph refresh is recorded for this exact target",
                    Severity.ERROR,
                )
            )
            continue
        if record.get("change") != _change:
            failures.append(
                Violation(
                    ViolationKind.GRAPH_REFRESH_REQUIRED,
                    target,
                    "post-edit graph refresh used a different change class than the approved contract; re-run it with the declared class",
                    Severity.ERROR,
                )
            )
            continue
        baseline = record.get("baseline") if isinstance(record.get("baseline"), dict) else {}
        if (
            baseline.get("map_hash") != contract.get("map_hash")
            or baseline.get("depth") != expected_depth
        ):
            failures.append(
                Violation(
                    ViolationKind.GRAPH_REFRESH_REQUIRED,
                    target,
                    "post-edit graph refresh is based on a different map or graph depth than the approved contract; re-run it after mapping",
                    Severity.ERROR,
                )
            )
            continue
        record_sync = record.get("sync") if isinstance(record.get("sync"), dict) else {}
        record_head = record_sync.get("sha")
        record_capture = record.get("worktree") if isinstance(record.get("worktree"), dict) else {}
        recorded_provider = record_sync.get("provider_version")
        provider_changed = "freshness" in record_capture and recorded_provider != current_provider
        if provider_changed or record_head != current_head or not graph_refresh.relation_capture_matches(record_capture, fingerprint):
            failures.append(
                Violation(
                    ViolationKind.GRAPH_REFRESH_REQUIRED,
                    target,
                    ("post-edit graph provider changed; re-run refresh before checking" if provider_changed else
                     "post-edit graph refresh is stale for the current commit or worktree; re-run it before checking"),
                    Severity.ERROR,
                )
            )
    return failures


@app.command()
def check(
    phase: int = typer.Option(None, "--phase"),
    base_ref: str = typer.Option(None, "--base-ref", "--base"),
    head_ref: str = typer.Option("HEAD", "--head-ref", "--head"),
    frozen: str = typer.Option("", help="v0: comma-separated frozen symbols"),
    scope: str = typer.Option("", help="v0: comma-separated in-scope symbols"),
    removals: str = typer.Option("", help="v0: declared removals"),
    strict: bool = typer.Option(False, "--strict"),
    no_tests: bool = typer.Option(False, "--no-tests", help="diagnostic only; skipped tests cannot advance phases (exit 1)"),
    no_live: bool = typer.Option(False, "--no-live", help="print the static tree instead of the live one"),
    repo: str = REPO_OPT,
    json_output: bool = JSON_OPT,
):
    """Contract check; graph-relevant edits require a current matching refresh before green."""
    brand.header("check", state.repo_name(repo), json_output)
    contract = state.read_json("contract.json", repo)
    if not contract:
        if base_ref:
            _input_error(
                "legacy check --base-ref is retired: a commit-only diff cannot verify dirty worktree semantics; run amu map and amu plan --approve first",
                json_output,
            )
        typer.echo("no contract — run amu plan --approve first", err=True)
        raise typer.Exit(code=2)
    contract_base = contract.get("base_sha")
    if base_ref and base_ref != contract_base:
        _input_error(
            "--base-ref cannot override an approved contract baseline; re-map and approve a new contract instead",
            json_output,
        )
    if head_ref != "HEAD":
        _input_error(
            "--head-ref cannot override an approved contract's current HEAD; run check against the active worktree",
            json_output,
        )
    m = state.read_json("map.json", repo)
    if not m:
        typer.echo("no contract — run amu plan --approve first", err=True)
        raise typer.Exit(code=2)
    if contract["map_hash"] != m["map_hash"]:
        typer.echo("contract is stale: map changed since approval (exit 2)", err=True)
        raise typer.Exit(code=2)
    st = state.read_json("state.json", repo, default={"nodes": {}, "violations": [], "history": []})
    source_before = lifecycle.capture_source(repo)
    phase_data = None
    if phase is not None:
        phase_data = next((p for p in contract.get("phases", []) if p.get("n") == phase), None)
        if phase_data is None:
            valid = ", ".join(str(p.get("n")) for p in contract.get("phases", [])) or "none"
            msg = f"invalid phase {phase}; choose one of: {valid}"
            typer.echo(json.dumps({"error": msg, "exit": 2}) if json_output else msg, err=not json_output)
            raise typer.Exit(code=2)
        incomplete = lifecycle.earlier_incomplete(contract, st, phase, source_before)
        if incomplete:
            numbers = ", ".join(str(item["n"]) for item in incomplete)
            _lifecycle_error(
                f"phase {phase} is blocked until earlier phase(s) are green: {numbers}", incomplete, json_output, 2
            )
    covered = lifecycle.covered_phases(contract, phase)
    check_id = uuid4().hex
    contract_hash = state.stable_hash(contract)
    pending = {
        "version": 1, "event": "check", "check_id": check_id, "phase": phase,
        "covered_phases": [p["n"] for p in covered], "contract_hash": contract_hash,
        "passed": False, "source": source_before, "source_stable": False,
        "tests": {"status": "not_run"}, "build": {"status": "not_run"},
        "violations": [], "at": state.now_iso(),
    }

    def start_check(raw: object) -> dict:
        current = raw if isinstance(raw, dict) else {}
        history = current.get("history") if isinstance(current.get("history"), list) else []
        return {**current, "history": [*history, pending], "updated_at": state.now_iso()}

    # Persist the attempt before invoking tools: failure/interruption must not
    # leave a previous success masquerading as the latest completed check.
    state.update_json("state.json", repo, {"nodes": {}, "violations": [], "history": []}, start_check)
    # A green check without current graph evidence would turn the guardrail
    # into a false claim.  The legacy --base/--head path above retains its
    # documented degraded mode; approved contract checks fail closed.
    _need_entire(json_output)
    cfg = state.config(repo)
    base = contract["base_sha"]
    diff = entire.diff_json(repo, base, head_ref) if entire.available() else None
    if diff is None:
        _graph_failure("entire graph diff failed; cannot verify the approved contract", json_output)
    changes = parse_graph_diff(diff) if diff else []
    # The graph diff can see committed changes that are no longer present in
    # the working-tree diff.  Contract scope must use both views; otherwise a
    # graph-reported export can evade file-scope enforcement.
    changed = sorted({*state.git_changed_files(repo, base), *(c["file"] for c in changes if c.get("file"))})
    # Graph diffs compare commit refs.  Keep a separate worktree view so a
    # frozen boundary cannot pass on file-only evidence while its semantic
    # diff is unavailable.
    worktree_changed = set(state.git_changed_files(repo, state.git_head(repo)))
    unknown_files = {n["path"] for n in m["nodes"] if n["confidence"] == "unknown"}
    amu_files = {"CLAUDE.md", "AGENTS.md", "BUILDATHON.md", "README.md", "BUILD_PLAN.md"}
    result = check_contract(contract, changes, changed, phase, unknown_files, amu_files)
    worktree_violations = worktree_semantic_violations(contract, worktree_changed)
    if worktree_violations:
        result.violations.extend(worktree_violations)
        result.passed = False
    # A provider graph diff can surface useful semantic scope evidence even
    # when it names a file that Git does not report as changed locally (for
    # example, a stale remote/cache fixture).  Do not turn that diagnostic
    # alone into a claim that this worktree needs a post-edit refresh.  The
    # refresh gate is tied to actual committed-or-working-tree file content
    # since the approved base; graph-diff rows remain enforced by the normal
    # contract scope check above.
    refresh_violations = _refresh_gate_violations(repo, contract, set(state.git_changed_files(repo, base)))
    if refresh_violations:
        result.violations.extend(refresh_violations)
        result.passed = False
    warnings = []
    # Scope checks already cover earlier phases. Re-run their tests too before
    # refreshing their green evidence for this new working-tree revision.
    phase_tests = (
        list(dict.fromkeys(test for p in covered for test in p.get("tests", [])))
        if phase is not None and covered and all(p.get("tests") for p in covered) else []
    )
    tests = None if no_tests else _run_tests(repo, phase_tests, cfg)
    if not lifecycle.passed_run(tests):
        detail = "tests skipped via --no-tests; a diagnostic check cannot advance the lifecycle" if no_tests else (
            (tests or {}).get("summary") or "test runner returned no valid successful outcome"
        )
        result.violations.append(Violation(ViolationKind.TEST_FAILURE, "tests", detail, Severity.ERROR))
        result.passed = False
    build = _run_build(repo, cfg)
    if (build is not None or source_before["build_required"]) and not lifecycle.passed_run(build):
        result.violations.append(Violation(
            ViolationKind.BUILD_FAILURE, "build",
            (build or {}).get("summary") or "configured build returned no valid successful outcome", Severity.ERROR,
        ))
        result.passed = False
    source_after = lifecycle.capture_source(repo)
    source_stable = lifecycle.valid_source(source_before) and source_before == source_after
    contract_current = state.stable_hash(state.read_json("contract.json", repo, default={})) == contract_hash
    if not source_stable or not contract_current:
        detail = source_before.get("error") or source_after.get("error") or (
            "approved contract changed during the check" if not contract_current
            else "source revision, worktree, or runner configuration changed during the check"
        )
        result.violations.append(Violation(ViolationKind.CHECK_EVIDENCE_INVALID, "check", detail, Severity.ERROR))
        result.passed = False
    red = not result.passed
    phase_nodes = {node for p in covered for node in p.get("nodes", [])} if phase_data else {n["id"] for n in m["nodes"]}
    node_updates = {nid: "red" if red else "green" for nid in phase_nodes}
    for v in result.violations:
        if v.kind.value == "PHASE_DRIFT":
            for n in m["nodes"]:
                if n["path"] == v.symbol:
                    node_updates[n["id"]] = "drift"
    violation_rows = [v.to_dict() for v in result.violations]
    checkpoint = (entire.checkpoint_list() or [{}])[0].get("id", "")

    def record_check(raw: object) -> dict:
        """Merge a completed check without erasing a concurrent watch event."""
        current = raw if isinstance(raw, dict) else {}
        if not contract_current:
            return current
        current_nodes = current.get("nodes") if isinstance(current.get("nodes"), dict) else {}
        nodes = dict(current_nodes)
        for node_id, value in node_updates.items():
            # A save observed while this check was running is newer than its
            # green result.  Preserve it so the phase cannot advance on a
            # stale check; red/drift from the checker itself still wins.
            if value == "green" and nodes.get(node_id) in {"editing", "drift"}:
                continue
            nodes[node_id] = value
        history = current.get("history") if isinstance(current.get("history"), list) else []
        return {
            **current,
            "nodes": nodes,
            "violations": violation_rows,
            "history": [
                {
                    **pending, "passed": result.passed, "source_stable": source_stable,
                    "tests": lifecycle.run_evidence(tests, absent="skipped" if no_tests else "not_run"),
                    "build": lifecycle.run_evidence(build, absent="not_run" if source_before["build_required"] else "not_configured"),
                    "violations": violation_rows, "at": state.now_iso(), "checkpoint": checkpoint,
                } if isinstance(entry, dict) and entry.get("check_id") == check_id else entry
                for entry in history
            ],
            "updated_at": state.now_iso(),
        }

    st = state.update_json(
        "state.json",
        repo,
        {"nodes": {}, "violations": [], "history": []},
        record_check,
    )
    delta = None
    if red:
        v0 = next(v for v in result.violations if v.severity.value == "ERROR")
        consumers = [n["id"] for n in m["nodes"] if n["path"] == v0.symbol.split("#")[0]]
        verify = "revert or re-plan, then amu check --phase %s" % (phase or 1)
        if v0.kind == ViolationKind.UNVERIFIABLE_WORKTREE_SEMANTICS:
            verify = "commit the intended change, run amu sync, then amu check --phase %s (or revert it)" % (phase or 1)
        if v0.kind == ViolationKind.GRAPH_REFRESH_REQUIRED:
            verify = _refresh_next(contract)
        delta = {"node": v0.symbol, "consumers": consumers, "why": v0.detail, "verify": verify}
    phase_order = lifecycle.ordered_phases(contract)
    next_phase = None
    if not red and phase_data is not None:
        current_index = next((index for index, item in enumerate(phase_order) if item["n"] == phase), None)
        if current_index is not None and current_index + 1 < len(phase_order):
            next_phase = phase_order[current_index + 1]
    if red:
        next_action = "fix the delta brief, then re-run"
        if any(v.kind == ViolationKind.UNVERIFIABLE_WORKTREE_SEMANTICS for v in result.violations):
            next_action = "commit the intended change, run amu sync, then re-run this phase (or revert it)"
        if any(v.kind == ViolationKind.GRAPH_REFRESH_REQUIRED for v in result.violations):
            next_action = _refresh_next(contract)
        if any(v.kind == ViolationKind.CHECK_EVIDENCE_INVALID for v in result.violations):
            next_action = "restore a stable approved worktree, refresh changed targets, then run amu check"
    elif next_phase is not None:
        next_action = f"review builder phase {next_phase['n']} brief, edit its allowed files, then amu check --phase {next_phase['n']}"
    else:
        next_action = "amu done"
    out = {
        "phase": phase,
        "base": base,
        "changed_files": changed,
        "worktree_changed_files": sorted(worktree_changed),
        "refresh_required": [v.to_dict() for v in refresh_violations],
        "violations": [v.to_dict() for v in result.violations],
        "warnings": warnings,
        "tests": tests,
        "build": build,
        "passed": result.passed,
        "check_evidence": {
            "contract_hash": contract_hash, "source": source_before, "source_stable": source_stable,
            "covered_phases": [p["n"] for p in covered],
        },
        "delta_brief": delta,
        "next": next_action,
    }
    orch = Orchestrator(repo, task=contract["map_hash"])
    out["checker"] = orch.on_check(out)
    if next_phase is not None:
        out["builder"] = orch.phase_brief(contract, next_phase["n"], m)
    _out(out, json_output)
    if not json_output:
        with render.LiveTree(m, enabled=not no_live and sys.stdout.isatty()) as lt:
            lt.update(st["nodes"])
        render.render_check(out)
        for w in warnings:
            typer.echo(f"  ℹ {w}")
    if red:
        raise typer.Exit(code=1)


@app.command()
def verify(
    node: str = typer.Option(..., "--node"),
    attempt_fallback: bool = typer.Option(False, "--attempt-fallback"),
    repo: str = REPO_OPT,
    json_output: bool = JSON_OPT,
):
    """Print and run a node's verify path. Never changes the contract."""
    brand.header("verify", state.repo_name(repo), json_output)
    m = state.read_json("map.json", repo)
    n = next((x for x in (m or {}).get("nodes", []) if x["id"] == node), None)
    if not n:
        typer.echo(f"node {node} not in the current map", err=True)
        raise typer.Exit(code=2)
    out = {"node": n, "verify": n["verify"], "result": None, "fallback": None}
    configured_test_cmd = str(state.config(repo).get("test_cmd") or "pytest -q").strip()
    verify_cmd = str(n["verify"]).strip()
    if verify_cmd == configured_test_cmd or verify_cmd.startswith(f"{configured_test_cmd} "):
        target = verify_cmd[len(configured_test_cmd) :].strip()
        out["result"] = _run_tests(repo, [target] if target else [], state.config(repo))
    else:
        # Maps created by older versions may retain a shorter runner command.
        # Treat it as a complete, independently validated command rather than
        # trying to splice its arguments into the current config.
        try:
            runnable = _test_command_kind(shlex.split(verify_cmd)) is not None
        except ValueError:
            runnable = False
        if runnable:
            out["result"] = _run_tests(repo, [], {"test_cmd": verify_cmd})
    if attempt_fallback and n["symbol"] and entire.available():
        nb = entire.neighbors_json(repo, n["symbol"], internal_only=True)
        out["fallback"] = {
            "neighbors_matches": None if nb.get("error") else len(nb.get("matches", [])),
            "note": "fallback evidence is heuristic; re-run amu map to recompute confidence",
        }
        if nb.get("error"):
            out["fallback"].update(error=nb["error"], rc=nb.get("rc"))
    _out(out, json_output)
    if not json_output:
        typer.echo(f"{n['id']} {n['path']}#{n['symbol']} · {n['confidence']} · verify: {n['verify']}")
        typer.echo(
            f"result     {(out['result'] or {}).get('summary') if out['result'] else 'not a runnable test; ask a human or use --attempt-fallback'}"
        )
        if out["fallback"]:
            typer.echo(f"fallback   {out['fallback']}")


# ---------------------------------------------------------------------------
# sync / sweep / docs / done
# ---------------------------------------------------------------------------


@app.command(name="answer")
def answer_cmd(
    node: str = typer.Option(None, "--node", help="unknown node in the current map"),
    text: str = typer.Option(None, "--text", help="your answer to that specific question"),
    confirm: str = typer.Option(None, "--confirm", help="confirm a reviewed answer id without changing its author"),
    repo: str = REPO_OPT,
    json_output: bool = JSON_OPT,
):
    """List unknowns and answer proposals, record your answer, or confirm a reviewed proposal."""
    brand.header("answer", state.repo_name(repo), json_output)
    if (node is None) != (text is None) or (confirm is not None and node is not None):
        _input_error("use --node nX --text 'your answer', or --confirm answer-id, or no options to review", json_output)
    try:
        if confirm is not None:
            out = unknowns.confirm_answer(repo, confirm)
        elif node is not None:
            out = unknowns.record_answer(repo, node, text, who="human")
        else:
            out = unknowns.review(repo)
    except (ValueError, OSError) as exc:
        _input_error(str(exc), json_output)
    _out(out, json_output)
    if not json_output:
        if "answers" in out:
            for q in out["questions"]:
                typer.echo(f"[{q['node']}] {q['path']}#{q['symbol'] or '*'} · {q['question']}")
            for a in out["answers"]:
                typer.echo(f"[{a.get('id', '?')}] {a.get('node', '?')} · {a['status']} · author {a.get('who', 'unknown')}")
                typer.echo(f"  {a.get('answer', '')}")
                if a.get("confirmed_by"):
                    typer.echo(f"  confirmed by {a['confirmed_by']}")
                if a.get("stale_reason"):
                    typer.echo(f"  {a['stale_reason']}")
            if out["binding_error"]:
                typer.echo(out["binding_error"])
            typer.echo("next       review an answer, then amu answer --confirm <id>; or --node nX --text 'your answer'")
        else:
            typer.echo(f"answer {out['id']} · {out['node']} · author {out['who']} · confirmed by {out['confirmed_by']}")
            typer.echo("next       amu plan; graph confidence and write restrictions remain unchanged")


def _graph_diff_error(action: str) -> str:
    """A missing semantic diff is incomplete evidence, not an empty diff."""
    return f"entire graph diff failed; cannot {action}"


def _sweep(repo: str) -> dict:
    contract = state.read_json("contract.json", repo, default={})
    m = state.read_json("map.json", repo, default={"nodes": []})
    base = contract.get("base_sha") or "HEAD~1"
    diff = entire.diff_json(repo, base, "HEAD") if entire.available() else None
    if diff is None:
        error = _graph_diff_error("sweep exports outside the contract")
        return {
            "base": base,
            "findings": [],
            "summary": f"sweep incomplete: {error}",
            "next": "repair graph access, then re-run amu sweep",
            "complete": False,
            "error": error,
        }
    changes = parse_graph_diff(diff)
    # A file being allowed is not proof that every one of its public exports
    # was declared.  Preserve the map's confidence when an export is known;
    # only an exact target, a target-file wildcard, or a planned map node is
    # part of the contract.
    known_confidence = {(n.get("path"), n.get("symbol")): n.get("confidence", "guessed") for n in m["nodes"]}
    planned_node_ids = {node_id for phase in contract.get("phases", []) for node_id in phase.get("nodes", [])}
    planned_exports = {(n.get("path"), n.get("symbol")) for n in m["nodes"] if n.get("id") in planned_node_ids}
    target_exports = set()
    wildcard_target_files = set()
    for target in contract.get("targets", []):
        symbol = str(target.get("symbol") or "")
        path, separator, name = symbol.partition("#")
        if not separator:
            continue
        if name == "*":
            wildcard_target_files.add(path)
        else:
            target_exports.add((path, name))
    findings = []
    for c in changes:
        if c["kind"] not in ("function", "class", "method") or c["symbol"].split(".")[-1].startswith("_"):
            continue
        export = (c["file"], c["symbol"])
        if export in target_exports or c["file"] in wildcard_target_files or export in planned_exports:
            continue
        conf = known_confidence.get(export, "guessed")
        findings.append(
            {
                "path": c["file"],
                "symbol": c["symbol"],
                "bucket": "will_break" if c["signature_changed"] or c["change"] == "removed" else "might_break",
                "confidence": conf,
                "reason": f"{c['type']} outside the contract · {c['dependents_count']} dependents",
                "verify": f"amu map --symbol {c['file']}#{c['symbol']} --change {'signature' if c['signature_changed'] else 'body'}",
            }
        )
    depth = m.get("depth", 2)
    n_files = len({c["file"] for c in changes}) if changes else len(state.git_changed_files(repo, base))
    summary = f"swept {n_files} files at depth {depth}, {len(findings)} findings outside the contract"
    return {
        "base": base,
        "findings": findings,
        "summary": summary,
        "next": "amu docs" if findings else "amu done",
        "complete": True,
        "error": None,
    }


def _sync_state(repo: str, *, allow_doc_cache: bool = False) -> dict:
    """Refresh the committed-HEAD cache and save a separate worktree snapshot.

    Entire's index is intentionally a HEAD cache.  The snapshot below is the
    editable worktree evidence used for reporting; it must not be presented as
    proof that the HEAD index contains uncommitted relationships.
    """
    return syncservice.execute(_graph_service_repo(repo, syncservice.GraphSyncError), allow_doc_cache=allow_doc_cache)


@app.command()
def sync(repo: str = REPO_OPT, json_output: bool = JSON_OPT):
    """Refresh the HEAD index + worktree snapshot; writes .amu/sync-state.json."""
    brand.header("sync", state.repo_name(repo), json_output)
    try:
        out = _sync_state(repo)
    except syncservice.GraphSyncError as exc:
        _input_error(str(exc), json_output)
        raise AssertionError("_input_error always exits")  # pragma: no cover
    _out(out, json_output)
    if not json_output:
        typer.echo(f"synced HEAD {out['sha'][:7]} + worktree snapshot · {out['counts'] or out['error']}")
    if out["error"]:
        raise typer.Exit(code=3)


def _refresh_record(repo: str, target: str, value: dict) -> None:
    """Keep one post-edit graph record per target without replacing the plan map.

    ``map.json`` is intentionally the pre-edit scope evidence used by an
    approved contract.  Replacing it after an edit would make a valid contract
    look stale before the agent has had a chance to review the new edges.
    """
    prior = state.read_json("refresh-state.json", repo, default={})
    targets = prior.get("targets") if isinstance(prior, dict) else None
    targets = dict(targets) if isinstance(targets, dict) else {}
    targets[target] = value
    state.write_json(
        "refresh-state.json",
        {"schema": 1, "targets": targets, "last": target, "at": state.now_iso()},
        repo,
    )


def _refresh_target_parts(target: str) -> tuple[str, str] | None:
    """Validate an exact repository-relative ``path#qualified_name`` target."""
    path, separator, name = target.partition("#")
    if not separator or not path or not name or "#" in name:
        return None
    return path, name


def _refresh_graph_for_removed_target(target: str) -> dict:
    """Represent a source-verified deleted focus without inventing a live node."""
    _path, name = _refresh_target_parts(target) or ("?", target)
    return {
        "focus": None,
        "symbol": name,
        "target": target,
        "depth": None,
        "nodes": [],
        "edges": [],
        "counts": {section: 0 for section in ("callers", "callees", "type_consumers", "data_flows")},
        "status": "removed",
        "candidates": [],
        "warnings": [],
    }


def _refresh_failure(
    repo: str,
    target: str,
    out: dict,
    error: str,
    next_step: str,
    code: int = 3,
) -> tuple[dict, int]:
    out["error"] = error
    out["next"] = next_step
    _refresh_record(repo, target, out)
    return out, code


def _refresh_worktree_matches(sync_state: dict, repo: str) -> tuple[bool, dict, str | None]:
    """Reject a snapshot/live query pair if an editor changed the worktree."""
    before = sync_state.get("worktree") if isinstance(sync_state.get("worktree"), dict) else {}
    if before.get("error") or not before.get("digest"):
        return False, {}, f"could not fingerprint worktree before snapshot: {before.get('error') or 'no digest'}"
    after = graph_refresh.graph_capture(repo)
    if after.get("error") or not after.get("digest"):
        return False, after, f"could not fingerprint worktree after live query: {after.get('error') or 'no digest'}"
    if not graph_refresh.relation_capture_matches(before, after):
        return False, after, "worktree changed while graph refresh was running; no delta was trusted"
    return True, after, None


def _refresh_live_evidence(repo: str, target: str, impact_data: dict) -> tuple[dict, dict]:
    """Filter proven false edges and return the safety verdict for one live target."""
    filtered_rows, contradictions, uncertainties = source_evidence.filter_python_call_evidence(repo, {target: impact_data})
    filtered = filtered_rows.get(target)
    filtered = filtered if isinstance(filtered, dict) else {"error": "live graph returned malformed target data"}
    evidence = graph_readiness.with_source_call_evidence(
        graph_readiness.live_target_evidence(target, filtered),
        contradictions,
        uncertainties,
    )
    return filtered, evidence


def _comparison_edges(target: str, edges: list[dict[str, object]]) -> list[dict[str, object]]:
    """Anchor renamed/moved target edges to the pre-edit target for comparison."""
    return [{**edge, "target": target} for edge in edges]


def _old_inbound_relation_evidence(repo: str, target: str, edges: list[dict[str, object]]) -> dict:
    """Prove old direct consumers no longer reference a removed/renamed target.

    Once a graph provider can no longer resolve the old focus, its impact
    response cannot tell us whether a former caller was migrated or merely
    left broken.  Check every saved direct inbound relationship against the
    editable source before reporting it as removed.  Multi-hop relationships
    and unsupported source shapes are intentionally blockers: their removal
    cannot be independently proved from an endpoint alone.
    """
    target_parts = _refresh_target_parts(target)
    assert target_parts is not None  # validated at command boundary
    _old_path, old_name = target_parts
    checked: list[dict] = []
    issues: list[dict] = []
    seen: set[tuple[str, str, int]] = set()
    for edge in edges:
        if edge.get("direction") != "in":
            continue
        related = edge.get("related")
        relation = str(edge.get("relation") or "?")
        edge_depth = edge.get("depth")
        key = (str(related), relation, int(edge_depth) if isinstance(edge_depth, int) and not isinstance(edge_depth, bool) else -1)
        if key in seen:
            continue
        seen.add(key)
        row = {"related": related, "relation": relation, "depth": edge_depth}
        related_parts = _refresh_target_parts(related) if isinstance(related, str) else None
        if edge_depth != 1 or related_parts is None:
            issues.append({**row, "verdict": "unverifiable", "reason": "only an exact direct inbound endpoint can be source-verified"})
            continue
        endpoint_path, endpoint_name = related_parts
        verdict = source_evidence.symbol_reference_evidence(repo, endpoint_path, endpoint_name, old_name)
        checked.append({**row, "verdict": verdict})
        if verdict != "absent":
            if verdict == "endpoint_absent":
                reason = "saved consumer endpoint disappeared; it may have been renamed or moved and must be remapped"
            elif verdict == "present":
                reason = "old target is still referenced"
            else:
                reason = "old relation cannot be source-verified"
            issues.append(
                {
                    **row,
                    "verdict": verdict,
                    "reason": reason,
                }
            )
    return {"approvable": not issues, "checked": checked, "issues": issues}


def _removed_target_relation_evidence(repo: str, target: str, edges: list[dict[str, object]]) -> dict:
    """Combine saved-edge checks with a whole-repository stale-caller scan.

    The pre-edit graph tells us which consumers existed before an edit.  It
    cannot prove that the edit did not add a *new* caller before deleting or
    renaming the old target, so source-verify the whole repository as well.
    """
    prior = _old_inbound_relation_evidence(repo, target, edges)
    path, name = _refresh_target_parts(target) or ("", "")
    scan = source_evidence.repository_symbol_reference_evidence(repo, path, name)
    scan_issues = [
        {
            "related": f"{row.get('path', '<repo>')}#*",
            "relation": "SOURCE_SCAN",
            "depth": 1,
            "verdict": "present" if "still names" in str(row.get("reason")) else "unverifiable",
            "reason": row.get("reason", "repository source scan could not prove old target absence"),
        }
        for row in scan.get("issues", [])
        if isinstance(row, dict)
    ]
    return {
        **prior,
        "repository_scan": scan,
        "issues": [*prior["issues"], *scan_issues],
        "approvable": bool(prior["approvable"] and scan.get("approvable") is True),
    }


def _refresh_payload(
    repo: str,
    target: str,
    depth: int,
    change: str,
    old_map: dict | None,
    new_target: str | None = None,
) -> tuple[dict, int]:
    """Create a bounded, consistent post-edit relation delta.

    This is intentionally not described as atomic: the provider needs separate
    snapshot and impact operations.  A content fingerprint brackets them, and
    any drift, incomplete pagination, wrong focus, degraded analysis, or
    source-contradicted call prevents a delta from being trusted.
    """
    baseline_evidence = graph_refresh.map_baseline(target, depth, old_map)
    baseline = {
        "available": baseline_evidence.available,
        "edge_count": len(baseline_evidence.edges),
        "map_hash": old_map.get("map_hash") if isinstance(old_map, dict) else None,
        "depth": depth,
        "reason": baseline_evidence.reason,
    }
    out = {
        "target": target,
        "live_target": new_target or target,
        "change": change,
        "sync": None,
        "baseline": baseline,
        "graph": None,
        "graph_evidence": None,
        "old_relation_evidence": None,
        "edge_delta": {"added": [], "removed": [], "unchanged": 0},
        "worktree": None,
        "complete": False,
        "error": None,
        "next": "create an exact, complete pre-edit map before comparing post-edit relationships",
    }
    if not baseline_evidence.available:
        return _refresh_failure(repo, target, out, baseline_evidence.reason or "no trustworthy pre-edit map baseline", out["next"], 2)

    sync_state = _sync_state(repo)
    out["sync"] = sync_state
    if sync_state.get("error"):
        return _refresh_failure(
            repo,
            target,
            out,
            str(sync_state["error"]),
            "repair graph sync evidence, then run this refresh again",
            3,
        )
    snapshot_worktree = sync_state.get("worktree") if isinstance(sync_state.get("worktree"), dict) else {}
    if snapshot_worktree.get("error") or not snapshot_worktree.get("digest"):
        return _refresh_failure(
            repo,
            target,
            out,
            f"could not fingerprint worktree before snapshot: {snapshot_worktree.get('error') or 'no digest'}",
            "repair Git worktree access, then run this refresh again",
            3,
        )

    query_target = new_target or target
    query_parts = _refresh_target_parts(query_target)
    assert query_parts is not None  # validated by the command boundary
    query_path, query_name = query_parts
    impact_data = entire.impact_json(repo, query_name, depth, file=query_path)

    # For a declared deletion, an unresolved old focus is expected only when a
    # local Python AST (or missing Python file) independently proves absence.
    if change == "remove":
        exact_old, _reason = graph_refresh.focus_matches_target(target, impact_data)
        if impact_data.get("error"):
            return _refresh_failure(
                repo,
                target,
                out,
                str(impact_data["error"]),
                "repair graph access, then run this refresh again",
                3,
            )
        old_path, old_name = _refresh_target_parts(target) or ("", "")
        presence = source_evidence.symbol_presence(repo, old_path, old_name)
        if exact_old or presence != "absent":
            explanation = "target still resolves in the live graph" if exact_old else "target deletion cannot be verified from editable Python source"
            return _refresh_failure(
                repo,
                target,
                out,
                explanation,
                "restore the target or use a source-supported remove refresh after deleting it",
                2,
            )
        old_relation_evidence = _removed_target_relation_evidence(repo, target, baseline_evidence.edges)
        out["old_relation_evidence"] = old_relation_evidence
        if not old_relation_evidence["approvable"]:
            return _refresh_failure(
                repo,
                target,
                out,
                "a prior or newly introduced source reference to the deleted target remains or cannot be independently verified",
                "migrate/remove every old consumer, then run this remove refresh again",
                2,
            )
        consistent, after_worktree, fingerprint_error = _refresh_worktree_matches(sync_state, repo)
        out["worktree"] = after_worktree
        if not consistent:
            return _refresh_failure(repo, target, out, fingerprint_error or "worktree drift", "stop editing, then run this refresh again", 3)
        out["graph"] = _refresh_graph_for_removed_target(target)
        out["graph_evidence"] = {"approvable": True, "stage": "source-verified-removal", "reasons": []}
        out["edge_delta"] = graph_refresh.delta(baseline_evidence.edges, [])
        out["complete"] = True
        out["next"] = "review removed semantic edges, update the plan/docs if needed, then run amu check"
        _refresh_record(repo, target, out)
        return out, 0

    # A rename/move must point at a concrete new symbol and prove the old
    # Python definition disappeared.  Without this, an accidental copy would
    # be reported as an equivalence-preserving move.
    if change in {"rename", "move"}:
        old_path, old_name = _refresh_target_parts(target) or ("", "")
        new_path, new_name = query_parts
        if change == "rename" and old_path != new_path:
            return _refresh_failure(repo, target, out, "rename must retain the same file; use --change move for a new file", "correct --change/--new-symbol and refresh again", 2)
        old_presence = source_evidence.symbol_presence(repo, old_path, old_name)
        new_presence = source_evidence.symbol_presence(repo, new_path, new_name)
        if old_presence != "absent" or new_presence != "present":
            return _refresh_failure(
                repo,
                target,
                out,
                "rename/move source transition cannot be verified from editable Python source",
                "use an exact Python old/new target or create a new map for the changed surface",
                2,
            )
        old_relation_evidence = _removed_target_relation_evidence(repo, target, baseline_evidence.edges)
        out["old_relation_evidence"] = old_relation_evidence
        if not old_relation_evidence["approvable"]:
            return _refresh_failure(
                repo,
                target,
                out,
                "a prior or newly introduced source reference to the old target remains or cannot be independently verified",
                "migrate/remove every old consumer, then run this rename/move refresh again",
                2,
            )

    filtered_impact, live_evidence = _refresh_live_evidence(repo, query_target, impact_data)
    if change == "signature":
        from amu.compatibility import assess
        current_edges = graph_refresh.live_edges(query_target, filtered_impact)
        callers = sorted({str(edge["related"]) for edge in [*baseline_evidence.edges, *current_edges.edges]
                          if edge.get("relation") == "CALLS" and edge.get("direction") == "in"
                          and edge.get("depth") == 1 and edge.get("related")})
        captured = ((old_map or {}).get("graph_evidence") or {}).get("worktree") or {}
        base_sha = (captured.get("after") or {}).get("head") or ""
        out["compatibility"] = assess(repo, query_target, base_sha, callers)
    consistent, after_worktree, fingerprint_error = _refresh_worktree_matches(sync_state, repo)
    out["worktree"] = after_worktree
    if not consistent:
        return _refresh_failure(repo, target, out, fingerprint_error or "worktree drift", "stop editing, then run this refresh again", 3)
    out["graph_evidence"] = live_evidence
    if not live_evidence.get("approvable"):
        return _refresh_failure(
            repo,
            target,
            out,
            graph_readiness.summary(live_evidence),
            "repair incomplete or contradicted live graph evidence, then run this refresh again",
            3,
        )
    live_edges = graph_refresh.live_edges(query_target, filtered_impact)
    if not live_edges.available:
        return _refresh_failure(
            repo,
            target,
            out,
            live_edges.reason or "live graph response cannot support a complete edge comparison",
            "resolve the exact target and complete graph pagination, then run this refresh again",
            3,
        )
    out["graph"] = graphview.payload_from_impact(query_name, filtered_impact)
    after = _comparison_edges(target, live_edges.edges)
    out["edge_delta"] = graph_refresh.delta(baseline_evidence.edges, after)
    out["complete"] = True
    added = len(out["edge_delta"]["added"])
    removed = len(out["edge_delta"]["removed"])
    if added or removed:
        out["next"] = "review added/removed semantic edges, widen the plan if their files need edits, then run amu check"
    else:
        out["next"] = "no mapped semantic edge changed; run amu check when tests and build are ready"
    binding = out.get("compatibility") or {}
    if any(row.get("status") == "binding_invalid" for row in binding.get("calls", [])):
        out["next"] = "relationship graph refreshed; invalid Python argument bindings remain—review compatibility calls before checking"
    _refresh_record(repo, target, out)
    return out, 0


@app.command()
def compatibility(
    symbol: str = typer.Option(..., "--symbol", help="exact Python path#function"),
    caller: list[str] = typer.Option([], "--caller", help="current path#caller scope; repeat as needed"),
    base: str = typer.Option("HEAD", "--base", help="Git revision containing the old signature"),
    repo: str = REPO_OPT,
    json_output: bool = JSON_OPT,
):
    """Compare static Python argument binding; never claims runtime compatibility."""
    from amu.compatibility import assess
    result = assess(repo, symbol, base, caller)
    _out(result, json_output)
    invalid = sum(row["status"] == "binding_invalid" for row in result["calls"])
    if not json_output:
        typer.echo(f"{len(result['calls'])} call sites assessed · {invalid} invalid bindings · {len(result['unknown'])} unknowns")
        for row in result["calls"]:
            typer.echo(f"{row['caller']}:{row['line']} · {row['status']} · {row.get('after_error') or row.get('reason') or 'arguments bind'}")
        typer.echo("Static argument binding only; runtime behavior, return contracts and unsupplied callers remain unverified.")
    if invalid or result["unknown"]:
        raise typer.Exit(code=1 if invalid else 2)


@app.command()
def refresh(
    symbol: str = typer.Option(..., "--symbol", help="exact approved pre-edit map target: path#name"),
    new_symbol: str = typer.Option(None, "--new-symbol", help="new path#name for a rename or move"),
    depth: int = typer.Option(2, "--depth"),
    change: str = typer.Option("body", "--change", help="must match the approved target: signature|remove|rename|move|body"),
    repo: str = REPO_OPT,
    json_output: bool = JSON_OPT,
):
    """Post-edit refresh from an exact approved map; records edge additions/removals for check/done."""
    brand.header("refresh", state.repo_name(repo), json_output)
    parts = _refresh_target_parts(symbol)
    if not parts:
        _input_error("--symbol must use the form path#name", json_output)
    path, _name = parts
    if not _repo_local_path(repo, path):
        _input_error("refresh target must be a repository-relative path", json_output)
    new_parts = _refresh_target_parts(new_symbol) if new_symbol else None
    if new_symbol and not new_parts:
        _input_error("--new-symbol must use the form path#name", json_output)
    if new_parts and not _repo_local_path(repo, new_parts[0]):
        _input_error("new refresh target must be a repository-relative path", json_output)
    if depth < 1:
        _input_error("--depth must be at least 1", json_output)
    if change not in _CHANGE_CLASSES:
        _input_error(f"invalid --change {change!r}; choose one of: {', '.join(sorted(_CHANGE_CLASSES))}", json_output)
    if change in {"rename", "move"} and not new_symbol:
        _input_error(f"--change {change} requires --new-symbol path#name", json_output)
    if change not in {"rename", "move"} and new_symbol:
        _input_error("--new-symbol is only valid with --change rename or move", json_output)
    _need_entire(json_output)
    out, code = _refresh_payload(repo, symbol, depth, change, state.read_json("map.json", repo), new_symbol)
    _out(out, json_output)
    if not json_output:
        delta = out["edge_delta"]
        typer.echo(
            f"refresh · {symbol} · +{len(delta['added'])} / -{len(delta['removed'])} semantic edges"
            + ("" if out["complete"] else f" · failed: {out['error']}")
        )
        typer.echo(f"next       {out['next']}")
    if code:
        raise typer.Exit(code=code)


@app.command()
def sweep(repo: str = REPO_OPT, json_output: bool = JSON_OPT):
    """Every changed public export since the contract's base_sha that is not in the contract."""
    brand.header("sweep", state.repo_name(repo), json_output)
    out = _sweep(repo)
    machine_out = {**out, "exit": 3, "install": entire.INSTALL_HINT} if out["error"] else out
    _out(machine_out, json_output)
    if not json_output:
        render.render_findings("sweep", out["findings"], out["summary"], out["next"])
    if out["error"]:
        raise typer.Exit(code=3)


def _docs_service_call(operation, repo: str, *args, json_output: bool, **kwargs):
    """Translate expected application failures; business policy lives in core."""
    try:
        # Repository selection belongs to the adapter. The core accepts only
        # an explicit absolute directory and never falls back to the cwd.
        if isinstance(repo, str) and repo.strip():
            try:
                repo = str(Path(repo).resolve())
            except (OSError, RuntimeError, ValueError) as exc:
                raise docservice.DocumentationError("repo must be an existing directory") from exc
        return operation(repo, *args, **kwargs)
    except docservice.DocumentationError as exc:
        _out(exc.payload, json_output)
        if not json_output:
            message = str(exc)
            if exc.payload.get("install"):
                message += f". Install:\n  {exc.payload['install']}"
            typer.echo(message, err=True)
        raise typer.Exit(code=exc.exit_code) from exc


@app.command(name="docs")
def docs_cmd(
    mode: str = typer.Option(None, "--mode", help="flag|draft|auto"),
    apply: str = typer.Option(None, "--apply", help="candidate id from the most recent docs listing"),
    prepare: str = typer.Option(None, "--prepare", help="prepare an editable section draft for a listed candidate"),
    review: bool = typer.Option(False, "--review", help="apply the edited review draft with --apply"),
    target_index: int | None = typer.Option(None, "--target-index", min=1, help="select one mapped section (1-based) for review prepare/apply"),
    repo: str = REPO_OPT,
    json_output: bool = JSON_OPT,
):
    """Doc candidates from graph diff plus worktree evidence; auto applies sound+mapped, unshadowed candidates only."""
    brand.header("docs", state.repo_name(repo), json_output)
    request = docservice.DocsRequest(mode=mode, apply=apply, prepare=prepare, review=review, target_index=target_index)
    out = _docs_service_call(docservice.execute, repo, request, json_output=json_output)
    _out(out, json_output)
    if not json_output:
        prepared = out["prepared"]
        if prepared:
            typer.echo(f"review draft: {prepared['file']}\nnext       {prepared['next']}")
        typer.echo(
            f"docs · mode {out['mode']} · {len(out['candidates'])} candidates · applied {len(out['applied'])} · not applied {len(out['not_applied'])}"
        )
        for c in out["candidates"]:
            detail = c.get("draft") or c["candidate"] + " " + c["class"]
            typer.echo(f"  [{c['id']}] {detail}")
        for saved in out["saves"]:
            typer.echo(f"saved {saved['save_id']} · {saved['file']} · graph refreshed: {saved['graph_refreshed']}")
            if saved.get("graph_error") or saved.get("journal_error"):
                typer.echo(saved.get("graph_error") or saved["journal_error"])
        typer.echo(f"next       {out['next']}")


@app.command(name="docs-reviews")
def docs_reviews_cmd(
    preview: str | None = typer.Option(None, "--preview", help="review id to preview without saving"),
    discard: str | None = typer.Option(None, "--discard", help="archive a draft; preserves a recoverable copy"),
    repo: str = REPO_OPT,
    json_output: bool = JSON_OPT,
):
    """List, preview or archive review drafts independently of candidate freshness."""
    out = _docs_service_call(docservice.review_queue, repo, preview=preview, discard=discard, json_output=json_output)
    _out(out, json_output)
    if not json_output:
        if "reviews" in out:
            for row in out["reviews"]:
                typer.echo(f"{row['review_id']} · {row['status']} · {row.get('target', row.get('reason'))}")
        else:
            typer.echo(out.get("diff") or json.dumps(out, indent=2))


@app.command(name="api-manifest")
def api_manifest_cmd(
    file: str = typer.Option(..., "--file", help="repository-relative Python, JSON or TOML source"),
    base: str = typer.Option("HEAD", "--base", help="baseline Git revision"),
    repo: str = REPO_OPT,
    json_output: bool = JSON_OPT,
):
    """Compare static API surfaces; every finding requires documentation review."""
    from amu import api_manifest

    try:
        result = api_manifest.report(repo, file, base)
    except (OSError, ValueError) as exc:
        _input_error(str(exc), json_output)
    _out(result, json_output)
    if not json_output:
        typer.echo(f"{len(result['changes'])} static changes · {len(result['unknowns'])} coverage gaps")
        for item in result["changes"]:
            typer.echo(f"{item['id']} · {item['change']} · review required")


def _benchmark_relations_input(raw_path: str) -> dict:
    """Accept bounded strict JSON, without mistaking an object for edge rows."""
    from amu import state_io

    path = Path(raw_path).absolute()
    value = state_io.read(path, managed_root=path.parent)
    if isinstance(value, list):
        value = {"edges": value}
    if not isinstance(value, dict) or "edges" not in value:
        raise ValueError("relation input must be a JSON edge list or an object containing edges")
    if set(value) - {"edges", "completeness", "abstentions"}:
        raise ValueError("relation input supports only edges, completeness and abstentions")
    for field in ("edges", "abstentions"):
        rows = value.get(field, [])
        if not isinstance(rows, list):
            raise ValueError(f"relation input {field} must be a list")
        for edge in rows:
            if isinstance(edge, dict):
                parts = [edge.get(key) for key in ("source", "relation", "target")]
            elif isinstance(edge, list) and len(edge) == 3:
                parts = edge
            else:
                raise ValueError("relation edge must contain source, relation, target")
            if not all(isinstance(part, str) and part for part in parts):
                raise ValueError("relation edge source, relation and target must be nonempty strings")
    completeness = value.get("completeness")
    if completeness is not None and not isinstance(completeness, (bool, dict)):
        raise ValueError("relation completeness must be a boolean or an object with a boolean complete field")
    if isinstance(completeness, dict) and not isinstance(completeness.get("complete"), bool):
        raise ValueError("relation completeness object requires a boolean complete field")
    return {"edges": value["edges"], "completeness": completeness, "abstentions": value.get("abstentions", [])}


@app.command(name="benchmark")
def benchmark_cmd(
    corpus: str | None = typer.Option(None, "--corpus", help="versioned corpus JSON or directory"),
    relations: str | None = typer.Option(None, "--relations", help="raw relation JSON list or {edges, completeness?, abstentions?}"),
    validated: str | None = typer.Option(None, "--validated", help="source-validated JSON in the same format; coverage is independent"),
    json_output: bool = JSON_OPT,
):
    """Score labeled fixtures; results describe this corpus rather than arbitrary repositories."""
    from amu import benchmark

    try:
        for option, path in (("--corpus", corpus), ("--relations", relations), ("--validated", validated)):
            if path == "":
                _input_error(f"{option} path must not be empty", json_output)
        if validated is not None and relations is None:
            _input_error("--validated requires --relations", json_output)
        if relations is not None:
            observed = _benchmark_relations_input(relations)
            checked = _benchmark_relations_input(validated) if validated is not None else None
            result = benchmark.run_relations(
                observed["edges"], validated=checked["edges"] if checked else None,
                completeness=observed["completeness"], abstentions=observed["abstentions"],
                validation_completeness=checked["completeness"] if checked else None,
                validation_abstentions=checked["abstentions"] if checked else [],
                corpus_path=corpus, tool_version=__version__,
            )
        else:
            result = benchmark.run_public_api(corpus_path=corpus, tool_version=__version__)
    except (OSError, ValueError) as exc:
        _input_error(str(exc), json_output)
    _out(result, json_output)
    if not json_output:
        typer.echo(f"benchmark · {result['fixture_version']} · tool {result['tool_version']}")
        typer.echo(result.get("scope", "Supplied labeled fixtures only."))
        reports = result.get("reports", {"public_api": result})
        for name, report in reports.items():
            counts = report["counts"]
            typer.echo(
                f"{name}: TP {counts['tp']} · FP {counts['fp']} · FN {counts['fn']} · TN {counts['tn']}"
                f" · abstentions {counts['abstentions']}"
            )
            metrics = " · ".join(
                f"{metric} {'undefined' if value is None else f'{value:.3f}'}"
                for metric, value in report["metrics"].items()
            )
            typer.echo(metrics)
        typer.echo("Scores cover these fixtures, not repository-wide accuracy. Use --json for full evidence.")


@app.command(name="model-preview")
def model_preview_cmd(
    question: str = typer.Argument(..., help="question to preview without contacting a model"),
    repo: str = REPO_OPT,
    json_output: bool = JSON_OPT,
):
    """Show the sanitized initial chat request without making a provider call."""
    from amu.harness.chat import Assistant

    try:
        payload = Assistant(repo, stream=False).preview(question)
    except ValueError as exc:
        _input_error(str(exc), json_output)
    typer.echo(json.dumps({"sent": False, "request": payload}, indent=2))


@app.command(name="docs-saves")
def docs_saves_cmd(
    undo: str = typer.Option(None, "--undo", help="undo this save only if its document has not changed"),
    repo: str = REPO_OPT,
    json_output: bool = JSON_OPT,
):
    """Inspect recoverable documentation saves, or explicitly undo one."""
    result = _docs_service_call(docservice.saves, repo, undo=undo, json_output=json_output)
    typer.echo(json.dumps(result, indent=2))


@app.command(name="docs-graph")
def docs_graph_cmd(repo: str = REPO_OPT, json_output: bool = JSON_OPT):
    """Build the docs relationship graph: mapped sections, mentions, and local links."""
    brand.header("docs graph", state.repo_name(repo), json_output)
    out = _docs_service_call(docservice.graph, repo, json_output=json_output)
    _out(out, json_output)
    if not json_output:
        summary = out["graph"]["summary"]
        typer.echo(
            "docs graph · "
            f"{summary['files']} files · {summary['sections']} sections · "
            f"{summary['documents_edges']} mapped · {summary['mention_edges']} mentions · "
            f"{summary['link_edges']} links"
        )
        review = sum(item.get("status") == "review_required" for item in out["relationship_comparisons"])
        typer.echo(f"comparisons · {review} target(s) require documentation review")


def _done_contract_problem(contract: object, amu_map: object) -> str | None:
    """Return why ``done`` cannot use the current lifecycle state.

    Completion is meaningful only for the map that was approved into the
    current contract.  A missing or stale contract used to be treated like an
    empty, already-green plan, allowing ``done`` to claim completion without
    any scoped check having happened.
    """
    if not isinstance(contract, dict) or not contract:
        return "no approved contract exists; run amu map and amu plan --approve first"
    try:
        state.validate("contract", contract)
    except state.SchemaError as exc:
        return f"approved contract is malformed: {exc}"
    if not isinstance(amu_map, dict) or not amu_map:
        return "the map used to approve this contract is unavailable; re-run amu map and amu plan --approve"
    contract_hash = contract.get("map_hash")
    map_hash = amu_map.get("map_hash")
    if not isinstance(contract_hash, str) or not contract_hash or contract_hash != map_hash:
        return "contract is stale: map changed since approval; re-run amu map and amu plan --approve"
    return None


@app.command()
def done(publish: bool = typer.Option(False, "--publish"), repo: str = REPO_OPT, json_output: bool = JSON_OPT):
    """Finish only after green phases and a current refresh for every graph-relevant edit."""
    brand.header("done", state.repo_name(repo), json_output)
    contract = state.read_json("contract.json", repo, default={})
    amu_map = state.read_json("map.json", repo, default={})
    contract_problem = _done_contract_problem(contract, amu_map)
    if contract_problem:
        _lifecycle_error(f"cannot run amu done: {contract_problem}", [], json_output, 2)
    st = state.read_json("state.json", repo, default={"nodes": {}, "violations": [], "history": []})
    planned_phases = lifecycle.ordered_phases(contract)
    current_source = lifecycle.capture_source(repo)
    incomplete = lifecycle.incomplete_phases(contract, st, current_source)
    if planned_phases and incomplete:
        numbers = ", ".join(str(item["n"]) for item in incomplete)
        _lifecycle_error(
            f"cannot run amu done until planned phase(s) have successful current check evidence: {numbers}",
            incomplete, json_output, 1, next_command="amu check",
        )
    changed_since_contract = set(state.git_changed_files(repo, contract.get("base_sha")))
    refresh_violations = _refresh_gate_violations(repo, contract, changed_since_contract)
    if refresh_violations:
        refresh_error = "; ".join(violation.detail for violation in refresh_violations)
        report = {
            "declared": {"targets": contract.get("targets", []), "files": contract.get("allowed_files", [])},
            "actual": {"changed_files": sorted(changed_since_contract), "node_states": st.get("nodes", {})},
            "findings": [],
            "sweep": "skipped until post-edit graph refresh is current",
            "docs": {"candidates": 0, "mode": state.config(repo).get("docs", {}).get("mode", "draft")},
            "memory": {"entries": 0, "changed_files": []},
            "sync": None,
            "snapshot_lines": None,
            "refresh_required": [violation.to_dict() for violation in refresh_violations],
            "lifecycle": {
                "required": bool(planned_phases),
                "passed": False,
                "phases": lifecycle.phase_statuses(contract, st, current_source),
            },
            "at": state.now_iso(),
            "complete": False,
            "errors": [{"stage": "refresh", "error": refresh_error}],
            "next": _refresh_next(contract),
        }
        state.write_json("report.json", report, repo)
        _out(report, json_output)
        if not json_output:
            render.render_report(report)
        raise typer.Exit(code=1)
    sync_state = _sync_state(repo, allow_doc_cache=True)
    sw = _sweep(repo)
    sweep_error = sw.get("error")
    docs_error = None
    if sweep_error:
        diff = None
        cands = []
        docs_error = f"documentation candidate scan skipped: {sweep_error}"
    else:
        diff = entire.diff_json(repo, contract.get("base_sha") or "HEAD~1", "HEAD") if entire.available() else None
        if diff is None:
            cands = []
            docs_error = _graph_diff_error("determine documentation candidates")
        else:
            cands = docsmod.all_candidates(diff, repo, docsmod.doc_map(repo))
    docs_graph_update = docgraph.sync(repo, state.config(repo).get("docs_dir", "docs"))
    docs_comparisons = docgraph.comparisons(repo, docs_graph_update["graph"])
    mem = memory.refresh(repo)
    sync_error = sync_state["error"]
    graph_errors = [
        {"stage": stage, "error": error}
        for stage, error in (("sync", sync_error), ("sweep", sweep_error), ("docs", docs_error))
        if error
    ]
    sweep_findings = list(sw.get("findings") or [])
    completion_errors = [*graph_errors]
    if sweep_findings:
        completion_errors.append(
            {
                "stage": "sweep-findings",
                "error": f"{len(sweep_findings)} unresolved finding(s) remain outside the approved contract",
            }
        )
    report = {
        "declared": {"targets": contract.get("targets", []), "files": contract.get("allowed_files", [])},
        "actual": {
            "changed_files": state.git_changed_files(repo, contract.get("base_sha")),
            "node_states": st.get("nodes", {}),
        },
        "findings": sw["findings"],
        "sweep": sw["summary"],
        "docs": {
            "candidates": len(cands),
            "mode": state.config(repo).get("docs", {}).get("mode", "draft"),
            "graph": docs_graph_update["graph"]["summary"],
            "graph_hash": docs_graph_update["graph"]["graph_hash"],
            "revisions": docs_graph_update["graph"].get("revisions"),
            "graph_delta": docs_graph_update["delta"],
            "relationship_comparisons": docs_comparisons,
            "relationship_reviews": sum(item.get("status") == "review_required" for item in docs_comparisons),
        },
        "memory": mem,
        "sync": sync_state["error"] or sync_state["counts"],
        "snapshot_lines": sync_state["snapshot_lines"],
        "snapshot": {"ref": sync_state.get("snapshot_ref"), "code_cache_reused": sync_state.get("code_cache_reused", False),
                     "note": sync_state.get("note")},
        "lifecycle": {
            "required": bool(planned_phases),
            "passed": True,
            "phases": lifecycle.phase_statuses(contract, st, current_source),
        },
        "at": state.now_iso(),
        "complete": not completion_errors,
        "errors": completion_errors,
        "next": (
            "repair graph evidence failures, then re-run amu done"
            if graph_errors
            else "resolve or re-map the findings outside the approved contract, then re-run amu done"
            if sweep_findings
            else "commit with `chore: amu memory refresh` if CLAUDE.md changed; open findings need a human decision"
            if sw["findings"] or mem["changed_files"]
            else "start the next task with amu map"
        ),
    }
    if docs_error:
        report["docs"]["error"] = docs_error
    state.write_json("report.json", report, repo)
    if publish and state.config(repo).get("web", {}).get("report_url"):
        report["published"] = "publishing requires network; not attempted in this build"
    _out(report, json_output)
    if not json_output:
        render.render_report(report)
    if graph_errors:
        raise typer.Exit(code=3)
    # Sweep findings are follow-up work, not a contract/check violation.  The
    # close command reports them with ``complete: false`` but follows the PRD's
    # explicit rule that done/sweep/docs reporting never exits 1.


# ---------------------------------------------------------------------------
# workspace: repo add / list / use / remove / sync / cd
# ---------------------------------------------------------------------------


def _ws_fail(exc: workspace.WorkspaceError, json_output: bool):
    typer.echo(json.dumps({"error": str(exc), "exit": exc.code}) if json_output else str(exc), err=not json_output)
    raise typer.Exit(code=exc.code)


@repo_app.command("inspect")
def repo_inspect(
    path: str = typer.Argument(..., help="explicit absolute root of an existing local Git checkout"),
    json_output: bool = JSON_OPT,
):
    """Read-only repository prerequisites; does not initialize, register or grant access."""
    from amu.repository_diagnostics import repo_preflight

    out = repo_preflight(path)
    _out(out, json_output)
    if not json_output:
        typer.echo(f"repository inspection · {'ready' if out['ready'] else 'not ready'}")
        for check in out["checks"]:
            typer.echo(f"{check['name']}: {check['status']} · {check['message']}")
        for action in out.get("next_actions", []):
            typer.echo(f"next: {action}")
        typer.echo("Local selection does not establish or expand an MCP repository grant.")
    if not out["ready"]:
        raise typer.Exit(code=3)


@repo_app.command("add")
def repo_add(
    spec: str = typer.Argument(..., help="local path, owner/repo, or GitHub URL"),
    branch: str = typer.Option(None, "--branch"),
    depth: int = typer.Option(None, "--depth"),
    json_output: bool = JSON_OPT,
):
    """Register a repo (clone if remote), index + snapshot it with entire graph, make it active."""
    brand.header("repo add", spec, json_output)
    if not (entire.available() and entire.graph_available()):
        _need_entire(json_output)
    try:
        with brand.progress("cloning / indexing…"):
            entry = workspace.add(spec, branch, depth)
    except workspace.WorkspaceError as exc:
        _ws_fail(exc, json_output)
    _out(entry, json_output)
    if not json_output:
        c = entry["counts"]
        typer.echo(
            f"active     {entry['name']} · {entry['branch']} · {c.get('files')} files · {c.get('symbols')} symbols · {c.get('relations')} relations"
        )
        typer.echo('next       amu map --file <path>   (or: amu find "what does X do")')


@repo_app.command("list")
def repo_list(json_output: bool = JSON_OPT):
    ws = workspace.load()
    rows = [
        {**r, "indexed_rel": workspace.relative(r.get("indexed_at", "")), "active": r["name"] == ws["active"]}
        for r in ws["repos"]
    ]
    _out({"active": ws["active"], "repos": rows}, json_output)
    if not json_output:
        brand.header("repo list", f"{len(rows)} repos", False)
        render.render_repo_table(rows, ws["active"]) if rows else typer.echo(
            "no repos yet — amu repo add <path|owner/repo>"
        )


@repo_app.command("use")
def repo_use(name: str, json_output: bool = JSON_OPT):
    try:
        r = workspace.use(name)
    except workspace.WorkspaceError as exc:
        _ws_fail(exc, json_output)
    _out(r, json_output)
    brand.header("repo use", r["name"], json_output)


@repo_app.command("remove")
def repo_remove(
    name: str,
    delete_clone: bool = typer.Option(
        False, "--delete-clone", help="delete files only if the clone lives under ~/.amu/repos"
    ),
    json_output: bool = JSON_OPT,
):
    try:
        r = workspace.remove(name, delete_clone)
    except workspace.WorkspaceError as exc:
        _ws_fail(exc, json_output)
    _out(r, json_output)
    if not json_output:
        typer.echo(f"removed {r['name']} · clone {'deleted' if r['deleted_clone'] else 'kept'}")


@repo_app.command("sync")
def repo_sync(
    name: str = typer.Argument(None), all_repos: bool = typer.Option(False, "--all"), json_output: bool = JSON_OPT
):
    """git fetch + re-index + re-snapshot; entity-level changes via entire graph diff."""
    ws = workspace.load()
    names = [r["name"] for r in ws["repos"]] if all_repos else [name or ws["active"]]
    out = []
    for n in names:
        if not n:
            continue
        try:
            out.append(workspace.sync(n))
        except workspace.WorkspaceError as exc:
            _ws_fail(exc, json_output)
    _out(out, json_output)
    if not json_output:
        for r in out:
            typer.echo(
                f"{r['name']} · {r['old_sha'][:7]}→{r['sha'][:7]} · {r['files_changed']} files · {r['entity_changes']} entity changes"
            )


@app.command("cd")
def cd_cmd(name: str):
    """Print the repo path only, so `cd "$(amu cd <name>)"` works."""
    r = workspace.get(name)
    if not r:
        typer.echo(f"no repo named {name}", err=True)
        raise typer.Exit(code=2)
    typer.echo(r["path"])


# ---------------------------------------------------------------------------
# in-repo navigation: find / open / tree / neighbors / where / back / recent
# ---------------------------------------------------------------------------


def _nav_push(repo: str, kind: str, target: str) -> None:
    def push(nav):
        nav["stack"] = (nav["stack"] + [{"kind": kind, "target": target, "at": state.now_iso()}])[-20:]
        return nav
    state.update_json("nav.json", repo, {"stack": []}, push)


def _hits(sr: dict) -> list[dict]:
    return [
        {
            "rank": r.get("rank"),
            "file_path": r.get("file_path"),
            "focus_line": r.get("focus_line") or r.get("start_line"),
            "symbol_name": r.get("symbol_name") or r.get("qualified_name") or "",
            "kind": r.get("kind"),
            "signals": r.get("signals") or [],
            "signature": r.get("signature"),
        }
        for r in sr.get("results") or []
    ]


@app.command()
def find(
    query: str,
    open_hit: str = typer.Option(None, "--open", help="print file:line of hit nN"),
    repo: str = REPO_OPT,
    json_output: bool = JSON_OPT,
    no_history: bool = typer.Option(False, "--no-history", help="inspect without creating or updating navigation state"),
):
    """Ranked hits from `entire graph search`, each with the signals that ranked it."""
    brand.header("find", state.repo_name(repo), json_output)
    _need_entire(json_output)
    sr = entire.search(repo, query)
    hits = _hits(sr)
    if not no_history:
        _nav_push(repo, "find", query)
    if open_hit:
        i = int(open_hit.lstrip("n")) - 1
        if 0 <= i < len(hits):
            typer.echo(f"{hits[i]['file_path']}:{hits[i]['focus_line']}")
            return
        typer.echo(f"no hit {open_hit}", err=True)
        raise typer.Exit(code=2)
    out = {
        "query": query,
        "hits": hits,
        "verify_command": sr.get("verify_command"),
        "coverage_note": sr.get("coverage_note"),
        "error": sr.get("error"),
    }
    _out(out, json_output)
    if not json_output:
        render.render_find(hits, query, sr.get("verify_command"))


@app.command("open")
def open_cmd(target: str, repo: str = REPO_OPT, json_output: bool = JSON_OPT):
    """`entire graph def` for a symbol (or list a file's symbols), then the one-line next."""
    brand.header("open", state.repo_name(repo), json_output)
    _need_entire(json_output)
    _nav_push(repo, "open", target)
    if "/" in target and "#" not in target and Path(repo, target).exists():
        symbols, symbols_error = entire.symbols_result(repo)
        if symbols_error:
            _graph_failure(f"entire graph symbols failed: {symbols_error}", json_output)
        syms = [
            s
            for s in symbols
            if s.get("file_path") == target and s.get("record_type", "symbol") == "symbol"
        ]
        out = {
            "file": target,
            "symbols": [
                {
                    "name": s.get("qualified_name"),
                    "kind": s.get("kind"),
                    "line": s.get("start_line"),
                    "signature": s.get("signature"),
                }
                for s in syms
            ],
            "next": f"amu map --file {target}",
        }
        _out(out, json_output)
        if not json_output:
            for x in out["symbols"]:
                typer.echo(f"  {x['line']:>5}  {x['kind']:<9} {x['signature'] or x['name']}")
            if not out["symbols"]:
                typer.echo("  (no exported symbols in the graph for this file)")
            typer.echo(f"read       /cat {target} 1 80   ·   next       {out['next']}")
        return
    name = target.split("#")[-1]
    text = entire.definition(repo, name)
    out = {"symbol": name, "definition": text, "next": f"amu map --symbol {target} --change signature"}
    _out(out, json_output)
    if not json_output:
        typer.echo(text.rstrip())
        typer.echo(f"next       {out['next']}")


@app.command()
def tree(
    path: str = typer.Argument("."),
    depth: int = typer.Option(2, "--depth"),
    repo: str = REPO_OPT,
    json_output: bool = JSON_OPT,
):
    """Directory tree annotated with export counts and feature labels from feature_map.json."""
    brand.header("tree", state.repo_name(repo), json_output)
    root = Path(repo, path).resolve()
    fm = state.read_json("feature_map.json", repo, default={})
    counts: dict[str, int] = {}
    if entire.available():
        symbols, symbols_error = entire.symbols_result(repo)
        if symbols_error:
            _graph_failure(f"entire graph symbols failed: {symbols_error}", json_output)
        for s_ in symbols:
            if (
                s_.get("record_type", "symbol") == "symbol"
                and s_.get("kind") in ("function", "class")
                and not str(s_.get("name", "_")).startswith("_")
            ):
                counts[s_["file_path"]] = counts.get(s_["file_path"], 0) + 1
    from amu.classify import _feature_for

    rows = {}
    for p in root.rglob("*"):
        if p.is_file() and not any(seg in p.parts for seg in (".git", ".amu", "__pycache__", "node_modules")):
            rel = str(p.relative_to(Path(repo).resolve()))
            feat, src = _feature_for(rel, fm)
            rows[str(p.relative_to(root))] = {
                "exports": counts.get(rel, 0),
                "feature": feat + ("" if src == "map" else " (derived)"),
            }
    _out({"root": str(root), "files": rows}, json_output)
    if not json_output:
        render.render_dir_tree(root, rows, depth)


@app.command()
def neighbors(
    symbol: str,
    relation: str = typer.Option("CALLS", "--relation"),
    direction: str = typer.Option("in", "--direction"),
    repo: str = REPO_OPT,
    json_output: bool = JSON_OPT,
):
    """Explore source-checked neighboring relations (never an approval artifact)."""
    brand.header("neighbors", state.repo_name(repo), json_output)
    _need_entire(json_output)
    raw = entire.neighbors_json(repo, symbol.split("#")[-1], relation, direction)
    if not isinstance(raw, dict):
        raw = {"error": "graph neighbors returned malformed data", "matches": []}
    nb, contradictions, uncertainties = source_evidence.filter_contradicted_python_neighbor_edges(repo, raw, direction)
    evidence = graph_readiness.with_source_call_evidence(
        {
            "approvable": False,
            "stage": "neighbors",
            "reasons": ["neighbors is an exploratory relation query; use an exact amu map and refresh before approving edits"],
        },
        contradictions,
        uncertainties,
    )
    if nb.get("error"):
        evidence["reasons"].append(f"graph neighbors failed: {nb['error']}")
    if nb.get("truncated"):
        evidence["reasons"].append("graph neighbors response is truncated")
    nb["status"] = "candidate"
    nb["source_contradictions"] = contradictions
    nb["source_uncertainties"] = uncertainties
    nb["graph_evidence"] = evidence
    _out(nb, json_output)
    if not json_output:
        for m_ in nb.get("matches") or []:
            if not isinstance(m_, dict):
                continue
            groups = [("incoming", "in"), ("outgoing", "out")]
            if not any(isinstance(m_.get(key), list) for key, _ in groups):
                groups = [("neighbors", direction), ("entries", direction)]
            for key, edge_direction in groups:
                for e in m_.get(key) or []:
                    if not isinstance(e, dict):
                        continue
                    ep = e.get("endpoint") or e.get("symbol") or e
                    ep = ep if isinstance(ep, dict) else {}
                    typer.echo(f"  {e.get('relation', relation)} {edge_direction}  {ep.get('file_path')}#{ep.get('name')}")
        typer.echo(f"matches    {len(nb.get('matches') or [])} · truncated {nb.get('truncated', False)}")
        typer.echo("status     candidate only · run an exact amu map before approving edits")


@app.command()
def where(symbol: str, repo: str = REPO_OPT, json_output: bool = JSON_OPT):
    """Def site + the file's feature + whether it is frozen in the current contract."""
    brand.header("where", state.repo_name(repo), json_output)
    _need_entire(json_output)
    name = symbol.split("#")[-1]
    symbols, symbols_error = entire.symbols_result(repo)
    if symbols_error:
        _graph_failure(f"entire graph symbols failed: {symbols_error}", json_output)
    defs = [
        s_
        for s_ in symbols
        if s_.get("record_type", "symbol") == "symbol" and name in (s_.get("name"), s_.get("qualified_name"))
    ]
    if "#" in symbol:
        defs = [d for d in defs if d.get("file_path") == symbol.split("#")[0]] or defs
    contract = state.read_json("contract.json", repo, default={})
    fm = state.read_json("feature_map.json", repo, default={})
    from amu.classify import _feature_for

    out = {
        "symbol": name,
        "status": "ok" if len(defs) == 1 else ("not_found" if not defs else "ambiguous"),
        "definitions": [
            {
                "file": d.get("file_path"),
                "line": d.get("start_line"),
                "kind": d.get("kind"),
                "feature": _feature_for(d.get("file_path", ""), fm)[0],
                "frozen": f"{d.get('file_path')}#{d.get('qualified_name')}"
                in set(contract.get("frozen_signatures", [])),
            }
            for d in defs
        ],
    }
    _out(out, json_output)
    if not json_output:
        for d in out["definitions"]:
            typer.echo(
                f"  {d['file']}:{d['line']}  {d['kind']}  feature {d['feature']}  {'FROZEN in contract' if d['frozen'] else 'not frozen'}"
            )
        typer.echo(f"status     {out['status']}")


@app.command()
def back(repo: str = REPO_OPT, json_output: bool = JSON_OPT):
    """Return to the previous navigation target (map / find / open) from .amu/nav.json."""
    def pop(nav):
        if len(nav["stack"]) < 2:
            typer.echo("nothing to go back to", err=True)
            raise typer.Exit(code=2)
        nav["stack"].pop()
        return nav
    nav = state.update_json("nav.json", repo, {"stack": []}, pop)
    prev = nav["stack"][-1]
    _out(prev, json_output)
    if not json_output:
        typer.echo(f"back to {prev['kind']} {prev['target']}")
        if prev["kind"] == "map":
            path, _, name = prev["target"].rpartition("#")
            render.render_map(
                _map_or_graph_failure(
                    repo,
                    None if name else path,
                    prev["target"] if name else None,
                    2,
                    state.read_json("map.json", repo, default={}).get("change", "signature"),
                )
            )


@app.command()
def recent(repo: str = REPO_OPT, json_output: bool = JSON_OPT):
    nav = state.read_json("nav.json", repo, default={"stack": []})
    _out(nav["stack"], json_output)
    if not json_output:
        for i, e in enumerate(reversed(nav["stack"]), 1):
            typer.echo(f"  {i:>2}  {e['kind']:<5} {e['target']}  [{e['at']}]")


# ---------------------------------------------------------------------------
# live rendering: graph / watch
# ---------------------------------------------------------------------------


@app.command()
def graph(
    symbol: str,
    depth: int = typer.Option(2, "--depth"),
    relation: str = typer.Option(None, "--relation"),
    fmt: str = typer.Option("text", "--format", help="text|json|dot|mermaid"),
    repo: str = REPO_OPT,
    json_output: bool = JSON_OPT,
):
    """Source-checked relation graph; --format dot / mermaid for PR descriptions."""
    if fmt == "json":
        json_output = True
    brand.header("graph", state.repo_name(repo), json_output or fmt in ("dot", "mermaid"))
    _need_entire(json_output)
    path, _, name = symbol.rpartition("#")
    capture_before = graphview.live_worktree_capture(repo)
    imp = entire.impact_json(repo, name, depth, file=path or None)
    p = graphview.payload_from_live_impact(
        repo, name, imp, relation, target=(symbol if path else None), capture_before=capture_before
    )
    if json_output:
        typer.echo(json.dumps(p, indent=2))
    elif fmt == "mermaid":
        typer.echo(graphview.to_mermaid(p))
    elif fmt == "dot":
        typer.echo(graphview.to_dot(p))
    else:
        typer.echo(graphview.to_ascii(p))
        if p["warnings"]:
            typer.echo(f"warnings   {', '.join(p['warnings'])}")


@app.command("watch")
def watch_cmd(
    phase: int = typer.Option(None, "--phase"),
    no_live: bool = typer.Option(False, "--no-live"),
    seconds: float = typer.Option(None, "--seconds", hidden=True),
    repo: str = REPO_OPT,
):
    """Watch the working tree; update node states (▸ editing · ↯ drift) against the active contract. Never runs tests."""
    brand.header("watch", state.repo_name(repo), False)
    m = state.read_json("map.json", repo)
    if not m:
        typer.echo("no map yet — run amu map first", err=True)
        raise typer.Exit(code=2)
    st = state.read_json("state.json", repo, default={"nodes": {}})
    with render.LiveTree(m, enabled=not no_live and sys.stdout.isatty()) as lt:
        lt.update(st["nodes"])

        def on_update(path, new_state, warnings):
            lt.update(new_state["nodes"], warnings or [f"▸ {path}"])

        try:
            watchmod.run(repo, phase, on_update, stop_after=seconds)
        except KeyboardInterrupt:
            pass


# ---------------------------------------------------------------------------
# key: set / status / remove  (the value is never echoed, logged, or committed)
# ---------------------------------------------------------------------------


@key_app.command("set")
def key_set(
    value: str = typer.Option(None, "--value", help="API key (prefer the hidden prompt or an env var)"),
    provider: str = typer.Option(
        "anthropic", "--provider", help="anthropic|gemini|bedrock|vertex|foundry|openai-compatible|ollama|lmstudio|openrouter"
    ),
    base_url: str = typer.Option(None, "--base-url"),
    model: str = typer.Option(None, "--model"),
    region: str = typer.Option(None, "--region"),
    project: str = typer.Option(None, "--project"),
    resource: str = typer.Option(None, "--resource"),
    json_output: bool = JSON_OPT,
):
    """Bring your own provider. Stores ~/.amu/credentials.json (mode 0600); the key is never printed."""
    brand.header("key set", provider, json_output)
    needs_key = keys.PROVIDERS.get(provider, (None, None, None, True))[3]
    v = (
        value
        if value is not None
        else (
            typer.prompt(f"{provider} API key", hide_input=True)
            if needs_key or provider in ("openai-compatible",)
            else None
        )
    )
    try:
        path = keys.set_credential(
            provider, api_key=v, base_url=base_url, model=model, region=region, project_id=project, resource=resource
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2)
    out = {"stored": path, "source": keys.source(), "masked": keys.masked()}
    _out(out, json_output)
    if not json_output:
        typer.echo(f"stored     {path} (0600) · {out['masked']} · run `amu` for the interactive session")


@key_app.command("status")
def key_status(json_output: bool = JSON_OPT):
    c = keys.credential()
    out = {
        "source": c["source"],
        "provider": c.get("provider"),
        "model": c.get("model"),
        "base_url": c.get("base_url"),
        "masked": keys.masked() if keys.available() else None,
        "providers": list(keys.PROVIDERS),
    }
    _out(out, json_output)
    if not json_output:
        typer.echo(
            f"credential {out['source']}"
            + (
                f" · {out['masked']}"
                if out["masked"]
                else " · run `amu key set [--provider …]`; providers: " + ", ".join(keys.PROVIDERS)
            )
        )


@key_app.command("remove")
def key_remove():
    typer.echo(
        "removed ~/.amu/credentials.json"
        if keys.remove_key()
        else "no stored key (environment credentials, if set, are untouched)"
    )


@app.command()
def ask(
    question: str,
    no_stream: bool = typer.Option(False, "--no-stream"),
    repo: str = REPO_OPT,
    json_output: bool = JSON_OPT,
):
    """Ask the model about this repo; it answers through the graph tools (read-only) and proposes the next amu command."""
    from amu.harness.chat import Assistant

    brand.header("ask", state.repo_name(repo), json_output)
    a = Assistant(
        repo, out=(lambda t: None) if (json_output or no_stream) else None, stream=not (json_output or no_stream)
    )
    answer = a.ask(question)
    if json_output:
        typer.echo(json.dumps({"question": question, "answer": answer, "model": a.model, "turns": len(a.messages), "usage": a.stats}))
    elif no_stream:
        typer.echo(answer)
    else:
        typer.echo("")


# ---------------------------------------------------------------------------
# memory / skills / repl
# ---------------------------------------------------------------------------


@memory_app.command("refresh")
def memory_refresh(
    no_commit: bool = typer.Option(True, "--no-commit/--commit"), repo: str = REPO_OPT, json_output: bool = JSON_OPT
):
    """Rebuild .amu/memory.json and rewrite the managed block in CLAUDE.md + AGENTS.md."""
    brand.header("memory refresh", state.repo_name(repo), json_output)
    from amu import memory_commit

    out = ({**memory.refresh(repo), "committed": False, "commit_status": "not_requested", "refreshed": True}
           if no_commit else memory_commit.refresh(repo))
    _out(out, json_output)
    if not json_output and out.get("refreshed"):
        typer.echo(
            f"memory · {out['entries']} entries · {out['stale']} stale · block {out['block_lines']} lines · changed {out['changed_files'] or 'nothing'}"
        )
    if out.get("commit_error"):
        if not json_output:
            typer.echo(out["commit_error"])
        raise typer.Exit(code=1)


@memory_app.command("show")
def memory_show(repo: str = REPO_OPT, json_output: bool = JSON_OPT):
    mem = state.read_json("memory.json", repo, default={"entries": []})
    for e in mem["entries"]:
        state.validate("memory_entry", e)
    _out(mem, json_output)
    if not json_output:
        for e in mem["entries"]:
            typer.echo(
                f"{e['id']} [{e['kind']}] {e['text']} · evidence {e['evidence']}{' · STALE: ' + e['stale'] if e.get('stale') else ''}{' 📌' if e['pinned'] else ''}"
            )


@memory_app.command("pin")
def memory_pin(entry_id: str, repo: str = REPO_OPT):
    typer.echo("pinned" if memory.pin(entry_id, repo, True) else "no such entry")


@memory_app.command("unpin")
def memory_unpin(entry_id: str, repo: str = REPO_OPT):
    typer.echo("unpinned" if memory.pin(entry_id, repo, False) else "no such entry")


@memory_app.command("forget")
def memory_forget(entry_id: str, repo: str = REPO_OPT):
    typer.echo("forgotten (kept in history)" if memory.forget(entry_id, repo) else "no such entry")


@app.command()
def stale(repo: str = REPO_OPT, json_output: bool = JSON_OPT):
    """Contract/map staleness plus memory entries whose evidence hash moved."""
    m, c = state.read_json("map.json", repo), state.read_json("contract.json", repo)
    mem = state.read_json("memory.json", repo, default={"entries": []})
    out = {
        "contract_stale": bool(m and c and m["map_hash"] != c["map_hash"]),
        "memory_stale": [
            e["id"]
            for e in mem["entries"]
            if e.get("file") and e["file_hash"] and state.file_hash(e["file"], repo) != e["file_hash"]
        ],
    }
    _out(out, json_output)
    if not json_output:
        typer.echo(
            f"contract stale: {out['contract_stale']} · memory entries with moved evidence: {len(out['memory_stale'])}"
        )


def _skill_dirs(repo: str):
    yield from (Path(__file__).parent / "skills").glob("*/SKILL.md")
    yield from (state.amu_dir(repo) / "skills").glob("*/SKILL.md")


@skills_app.command("list")
def skills_list(repo: str = REPO_OPT, json_output: bool = JSON_OPT):
    items = [{"name": p.parent.name, "path": str(p), "builtin": "amu/skills" in str(p)} for p in _skill_dirs(repo)]
    _out(items, json_output)
    if not json_output:
        for i in items:
            typer.echo(f"{'builtin' if i['builtin'] else 'repo   '} {i['name']}")


@skills_app.command("run")
def skills_run(name: str, repo: str = REPO_OPT):
    p = next((p for p in _skill_dirs(repo) if p.parent.name == name), None)
    typer.echo(p.read_text() if p else f"no skill {name}")


@skills_app.command("add")
def skills_add(name: str, text: str = typer.Option("", "--text"), repo: str = REPO_OPT):
    d = state.amu_dir(repo) / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(text or f"# {name}\n\n- (describe the steps)\n")
    typer.echo(f"wrote {d / 'SKILL.md'}")


# ---------------------------------------------------------------------------
# model status + guided key setup (REPL)
# ---------------------------------------------------------------------------

_PROVIDER_MENU = [
    ("anthropic", "Anthropic API key (claude-opus-5)", ["key"]),
    ("ollama", "Ollama, local, no key", ["model"]),
    ("lmstudio", "LM Studio, local, no key", ["model"]),
    ("openrouter", "OpenRouter (key + model, e.g. anthropic/claude-opus-5)", ["key", "model"]),
    ("gemini", "Google Gemini API key (gemini-flash-latest)", ["key"]),
    (
        "openai-compatible",
        "Any OpenAI-compatible endpoint (base URL + model, key optional)",
        ["base_url", "model", "key?"],
    ),
    ("bedrock", "Amazon Bedrock (AWS credentials from your environment)", ["region"]),
    ("vertex", "Google Vertex AI (gcloud ADC)", ["project", "region"]),
    ("foundry", "Microsoft Foundry (resource + key)", ["resource", "key"]),
]


HELP_SECTIONS = [
    (
        "Start here",
        [
            ("/tree [path]", "Browse the project"),
            ("/find <words>", "Search code by meaning"),
            ("/map <file>", "See what an edit could affect"),
        ],
    ),
    (
        "Explore",
        [
            ("/open <file|symbol>", "Open a file or symbol"),
            ("/cat <file> [from] [to]", "Read a section of a file"),
            ("/where <symbol>", "Find a symbol's definition"),
            ("/graph <symbol>", "Show callers and callees"),
            ("/neighbors <symbol>", "List direct relationships"),
            ("/recent", "List recently opened results"),
            ("/back", "Go to the previous saved target"),
        ],
    ),
    (
        "Plan a change",
        [
            ("/plan", "Draft the safe edit order"),
            ("/approve", "Lock the current plan"),
            ("/refresh <path#symbol>", "Recheck relationships; accepts CLI options"),
            ("/check <phase>", "Verify work against the plan"),
            ("/verify <node>", "Run a map item's check"),
            ("/answer [options]", "Review or confirm an unknown's answer"),
            ("/why <node>", "Explain why a map item is included"),
            ("/docs", "Find docs; --prepare or --apply with --review"),
            ("/docs-reviews", "List drafts; --preview or --discard a review id"),
            ("/api-manifest --file <path>", "Review static API changes against a Git revision"),
            ("/benchmark [options]", "Score versioned fixtures; no model request"),
            ("/session", "Show AMU model requests, context usage and remaining limits"),
            ("/done", "Run final checks and report"),
        ],
    ),
    (
        "Workspace",
        [
            ("/repo", "List saved projects"),
            ("/use <name>", "Switch projects"),
            ("/feature <name>", "Show files in a feature"),
            ("/key [add|remove]", "Connect or remove a model"),
            ("/ask <question>", "Ask the connected model about the project"),
            ("/skills", "List available project skills"),
            ("/help", "Show this command guide"),
            ("/quit", "Exit AMU"),
        ],
    ),
]


def print_start_hint() -> None:
    """Show a compact, useful entry point without flooding the terminal."""
    c = brand.err_console()
    c.print(
        "[brand.muted]Start: [/][brand]/tree[/] [brand.muted]browse · [/][brand]/find <words>[/] "
        "[brand.muted]search · [/][brand]/map <file>[/] [brand.muted]plan[/]"
    )
    c.print("[brand.muted]/help shows all commands · /quit exits[/]")


def print_help() -> None:
    """Render a narrow-terminal-friendly command guide with one task per row."""
    from rich.panel import Panel
    from rich.table import Table

    c = brand.err_console()
    c.print(
        Panel(
            "[brand]Use commands with or without /[/]\n"
            "[brand.muted]Example: [/][brand]open amu/plan.py[/] [brand.muted]works just like [/][brand]/open amu/plan.py[/]",
            title="[brand]AMU command guide[/]",
            border_style="brand.rule",
            padding=(0, 1),
            expand=False,
        )
    )
    for title, rows in HELP_SECTIONS:
        table = Table.grid(padding=(0, 2), expand=False)
        table.add_column(style="brand", no_wrap=True)
        table.add_column(style="brand.body", overflow="fold")
        for command, description in rows:
            table.add_row(command, description)
        c.print(f"[brand]{title}[/]")
        c.print(table)
    c.print(
        "[brand.muted]Tip: write [/][brand]rename classify_consumers[/] [brand.muted]to map a change before editing.[/]"
    )


def print_command_error(command: str) -> None:
    c = brand.err_console()
    c.print(f"[risk]Not a command:[/] [brand]/{command}[/]")
    c.print("[brand.muted]Type [/][brand]/help[/] [brand.muted]to see valid commands.[/]")


def model_status_line() -> str:
    """One line for the banner: ● on with provider/model, or ○ off with the two-step guide."""
    if keys.available():
        c = keys.credential()
        return f"[conf.sound]● model on[/] · {c.get('provider')} · {c.get('model')} · via {c['source']}"
    return "[conf.unknown]○ model off[/] · graph commands are ready\n[brand.muted]  /key add enables plain-English questions[/]"


def guided_key_setup() -> bool:
    """Interactive, provider-aware. Returns True when a credential was stored."""
    c = brand.err_console()
    c.print(
        "[brand]add a model[/] [brand.muted]— pick a provider; keys are stored in ~/.amu/credentials.json (0600) and never printed[/]"
    )
    for i, (name, desc, _) in enumerate(_PROVIDER_MENU, 1):
        c.print(f"  [brand.muted]{i}[/]  {name:<18} [brand.body]{desc}[/]")
    try:
        choice = typer.prompt("provider (number or name)", default="1").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    name = choice
    if choice.isdigit() and 1 <= int(choice) <= len(_PROVIDER_MENU):
        name = _PROVIDER_MENU[int(choice) - 1][0]
    entry = next((e for e in _PROVIDER_MENU if e[0] == name), None)
    if not entry:
        c.print(f"[risk]unknown provider {choice}[/] — choose 1-{len(_PROVIDER_MENU)}")
        return False
    fields: dict = {}
    try:
        for f in entry[2]:
            if f == "key":
                fields["api_key"] = typer.prompt(f"{name} API key", hide_input=True)
            elif f == "key?":
                v = typer.prompt("API key (blank if none)", default="", hide_input=True, show_default=False)
                fields["api_key"] = v or None
            elif f == "model":
                default = keys.PROVIDERS[name][1] or ""
                fields["model"] = typer.prompt("model", default=default) if default else typer.prompt("model")
            elif f == "base_url":
                fields["base_url"] = typer.prompt("base URL (…/v1)")
            elif f == "region":
                fields["region"] = typer.prompt("region")
            elif f == "project":
                fields["project_id"] = typer.prompt("GCP project id")
            elif f == "resource":
                fields["resource"] = typer.prompt("Foundry resource")
    except (EOFError, KeyboardInterrupt):
        c.print("[brand.muted]cancelled — nothing stored[/]")
        return False
    try:
        path = keys.set_credential(name, **fields)
    except ValueError as exc:
        c.print(f"[risk]{exc}[/]")
        return False
    c.print(f"[conf.sound]● model on[/] · stored {path} (0600) · {keys.masked()}")
    return True


COMPLETION_SNAPSHOT_BYTES = 2_000_000


def _snapshot_completion_words(path: Path) -> list[str]:
    """A bounded presentation-only prefix; never evidence of graph coverage."""
    import os
    import re
    import stat

    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return []
            prefix = stream.read(COMPLETION_SNAPSHOT_BYTES).decode("utf-8", errors="ignore")
        words = []
        for match in re.finditer(r'"name"\s*:\s*"([A-Za-z_][A-Za-z0-9_.]*)"', prefix):
            if len(match[1]) <= 512:
                words.append(match[1])
                if len(words) >= 5000:
                    break
        return words
    except (OSError, ValueError):
        return []


def _completer(repo: str):
    try:
        from prompt_toolkit.completion import WordCompleter
    except ImportError:
        return None
    words = list(router.SLASH) + [r["name"] for r in workspace.load()["repos"]]
    snap = state.amu_dir(repo) / "snapshot.ndjson"
    words += _snapshot_completion_words(snap)
    for n in state.read_json("map.json", repo, default={"nodes": []})["nodes"]:
        words.append(n["id"])
    return WordCompleter(sorted(set(words)), ignore_case=True)


def _prompt_session(repo: str):
    """Create one prompt-toolkit session for a REPL lifetime, preserving history."""
    if not sys.stdin.isatty():
        return None
    try:
        from prompt_toolkit import PromptSession
    except ImportError:
        return None
    return PromptSession(completer=_completer(repo))


def _prompt_label():
    if not brand.color_enabled():
        return brand.PROMPT
    from prompt_toolkit.formatted_text import FormattedText

    return FormattedText([(brand.PALETTE["gold"], "twc-thugs "), (brand.PALETTE["amber"], "❯ ")])


def _read_line(repo: str, session=None) -> str:
    if session is not None:
        # Rebuild completion candidates when `/use` changes repositories while
        # retaining prompt history and normal up/down arrow navigation.
        session.completer = _completer(repo)
        return session.prompt(_prompt_label())
    return input(brand.PROMPT)


def _repl_cli_command(command: str, arg: str, repo: str) -> None:
    """Use the CLI parser so new options cannot leak Typer defaults into the REPL.

    Keep the interactive refresh shorthand while sharing validation and execution
    with the CLI (also used by MCP). Parser errors must not terminate the session.
    """
    try:
        args = shlex.split(arg)
    except ValueError as exc:
        typer.echo(f"Invalid /{command} arguments: {exc}", err=True)
        return
    if command == "refresh" and args and not args[0].startswith("-"):
        args.insert(0, "--symbol")
    elif command == "map" and args and not args[0].startswith("-"):
        args.insert(0, "--symbol" if "#" in args[0] else "--file")
    try:
        # Let Typer render its own usage errors, then keep its normal CLI exit
        # (including nonzero guard failures) local to this interactive command.
        repo_args = [] if command == "benchmark" else ["--repo", repo]
        app(args=[command, *args, *repo_args], prog_name="amu", standalone_mode=True)
    except SystemExit:
        pass


def _repl(repo: str | None = None):
    try:
        repo = workspace.resolve(repo)
    except workspace.WorkspaceError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=exc.code)
    brand.banner(
        __version__,
        state.repo_name(repo),
        f"{'enabled' if entire.available() else 'missing'} · graph {entire.graph_version()}",
    )
    from amu.harness.chat import Assistant

    assistant = Assistant(repo) if keys.available() else None
    brand.err_console().print(model_status_line())
    print_start_hint()
    lookup = _lookup_factory(repo)
    session = _prompt_session(repo)

    def _cat(arg: str):
        parts = arg.split()
        if not parts:
            typer.echo("usage: /cat <file> [start] [end]")
            return
        p = Path(repo, parts[0])
        if not p.is_file():
            typer.echo(f"no such file: {parts[0]} — try /tree or /find")
            return
        lines = p.read_text(errors="ignore").splitlines()
        start = int(parts[1]) if len(parts) > 1 else 1
        end = int(parts[2]) if len(parts) > 2 else min(len(lines), start + 79)
        for i in range(max(1, start), min(len(lines), end) + 1):
            typer.echo(f"{i:>5}  {lines[i - 1]}")
        if end < len(lines):
            typer.echo(f"… {len(lines) - end} more lines · /cat {parts[0]} {end + 1} {end + 80}")

    def _key_cmd(arg: str):
        nonlocal assistant
        if arg in ("add", "set", "on"):
            if guided_key_setup():
                assistant = Assistant(repo)
        elif arg in ("remove", "off"):
            keys.remove_key()
            assistant = None
            brand.err_console().print(model_status_line())
        else:
            brand.err_console().print(model_status_line())
            if not keys.available():
                brand.err_console().print(
                    "[brand.muted]  /key add  → guided setup (provider, key or model)   ·   /key remove  → forget the stored key[/]"
                )

    while True:
        try:
            line = _read_line(repo, session)
        except (EOFError, KeyboardInterrupt):
            typer.echo("")
            return
        if not line.strip():
            print_start_hint()
            continue
        try:
            r = router.route(line, lookup)
        except GraphDataError as exc:
            typer.echo(str(exc), err=True)
            continue
        if r["status"] == "command":
            arg = r.get("arg", "")
            cmds = {
                "map": lambda: _repl_cli_command("map", arg, repo),
                "plan": lambda: plan(targets="", approve=False, symbol=None, repo=repo, json_output=False),
                "approve": lambda: plan(targets="", approve=True, symbol=None, repo=repo, json_output=False),
                "refresh": lambda: _repl_cli_command("refresh", arg, repo),
                "check": lambda: check(
                    phase=int(arg or 1),
                    base_ref=None,
                    head_ref="HEAD",
                    frozen="",
                    scope="",
                    removals="",
                    strict=False,
                    no_tests=False,
                    no_live=True,
                    repo=repo,
                    json_output=False,
                ),
                "done": lambda: done(publish=False, repo=repo, json_output=False),
                "docs": lambda: _repl_cli_command("docs", arg, repo),
                "docs-reviews": lambda: _repl_cli_command("docs-reviews", arg, repo),
                "docs-graph": lambda: _repl_cli_command("docs-graph", arg, repo),
                "docs-saves": lambda: _repl_cli_command("docs-saves", arg, repo),
                "api-manifest": lambda: _repl_cli_command("api-manifest", arg, repo),
                "benchmark": lambda: _repl_cli_command("benchmark", arg, repo),
                "session": lambda: typer.echo(json.dumps(assistant.session_status() if assistant else {"mode": "manual", "usage": None}, indent=2)),
                "answer": lambda: _repl_cli_command("answer", arg, repo),
                "verify": lambda: verify(node=arg, attempt_fallback=False, repo=repo, json_output=False),
                "skills": lambda: skills_list(repo=repo, json_output=False),
                "why": lambda: typer.echo(
                    json.dumps(
                        next(
                            (
                                n
                                for n in state.read_json("map.json", repo, default={"nodes": []})["nodes"]
                                if n["id"] == arg
                            ),
                            "no such node",
                        ),
                        indent=2,
                    )
                ),
                "feature": lambda: typer.echo(
                    json.dumps(state.read_json("feature_map.json", repo, default={}).get(arg, "unknown feature"))
                ),
                "repo": lambda: repo_list(json_output=False),
                "use": lambda: repo_use(arg, json_output=False),
                "find": lambda: find(arg, open_hit=None, repo=repo, json_output=False),
                "open": lambda: open_cmd(arg, repo=repo, json_output=False),
                "tree": lambda: tree(arg or ".", depth=2, repo=repo, json_output=False),
                "back": lambda: back(repo=repo, json_output=False),
                "graph": lambda: graph(arg, depth=2, relation=None, fmt="text", repo=repo, json_output=False),
                "ask": lambda: (
                    typer.echo(assistant.ask(arg)) if not assistant or not arg else (assistant.ask(arg), typer.echo(""))
                ),
                "key": lambda: _key_cmd(arg),
                "cat": lambda: _cat(arg),
                "where": lambda: where(arg, repo=repo, json_output=False),
                "recent": lambda: recent(repo=repo, json_output=False),
                "neighbors": lambda: neighbors(arg, relation="CALLS", direction="in", repo=repo, json_output=False),
                "quit": lambda: (_ for _ in ()).throw(EOFError()),
                "help": print_help,
            }
            try:
                if r["intent"] not in cmds:
                    print_command_error(line.strip().split(maxsplit=1)[0].lstrip("/"))
                else:
                    cmds[r["intent"]]()
                if r["intent"] == "use" and workspace.get(arg):
                    repo = workspace.get(arg)["path"]
                    lookup = _lookup_factory(repo)
            except EOFError:
                typer.echo("")
                return
            except typer.Exit:
                pass
            continue
        if r["status"] == "explore":
            try:
                open_cmd(r["arg"], repo=repo, json_output=False)
            except typer.Exit:
                pass
            continue
        if r["status"] == "ok":
            t = r["targets"][0]
            typer.echo(f"→ amu map --symbol {t['path']}#{t['symbol']} --change {r['change_class']}")
            try:
                render.render_map(_map_or_graph_failure(repo, None, f"{t['path']}#{t['symbol']}", 2, r["change_class"]))
            except typer.Exit:
                pass
        elif assistant:
            hint = f" (router: {r['status']}{', ambiguous ' + str(r.get('ambiguous')) if r.get('ambiguous') else ''})"
            assistant.ask(line + hint)
            typer.echo("")
        else:
            typer.echo(
                f"router: {r['status']} — name the symbol exactly as `entire graph search` reports it"
                + (f" (ambiguous: {r.get('ambiguous')})" if r.get("ambiguous") else "")
            )
            brand.err_console().print(
                "[conf.unknown]○ model off[/] — free-text questions need a model: type [brand]/key add[/] to connect one (Anthropic, Gemini, Ollama, …); slash commands and change requests work without it"
            )


if __name__ == "__main__":
    app()
