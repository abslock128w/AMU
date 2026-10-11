"""
amu/plan.py
-----------
Generates a 4-phase topologically-ordered refactoring plan from reverse-dependency edges.

Phases (per Track 2 safe-migration contract):
  1. Additive   – New symbols / overloads that introduce no breakage
  2. Migration  – Callers that must be updated before old signature is removed
  3. Interior   – Internal implementation changes safe after callers migrated
  4. Removal    – Delete / deprecate the original symbol
"""

import ast
import json
from dataclasses import dataclass, field
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Dict, List, Optional, Set

from amu import dependencies

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

PHASES = ["additive", "migration", "interior", "removal"]


@dataclass
class RefactoringPlan:
    symbol: str
    phases: Dict[str, List[str]] = field(default_factory=lambda: {p: [] for p in PHASES})
    cycles_detected: List[List[str]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "phases": self.phases,
            "cycles_detected": self.cycles_detected,
            "warnings": self.warnings,
        }

    def pretty(self) -> str:
        lines = [f"Refactoring Plan for: {self.symbol}", "=" * 50]
        for idx, phase in enumerate(PHASES, 1):
            items = self.phases[phase]
            lines.append(f"\n  Phase {idx} [{phase.upper()}] ({len(items)} item(s))")
            for item in items:
                lines.append(f"    • {item}")
        if self.cycles_detected:
            lines.append("\n  ⚠  CYCLES DETECTED (manually resolve):")
            for cycle in self.cycles_detected:
                lines.append(f"    {' -> '.join(cycle)}")
        if self.warnings:
            lines.append("\n  ℹ  Warnings:")
            for w in self.warnings:
                lines.append(f"    {w}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase classification heuristics
# ---------------------------------------------------------------------------

def _classify_phase(node: str, direct_callers: Set[str], transitive_callers: Set[str]) -> str:
    """
    Assign a node to one of the 4 phases based on its role relative to the
    target symbol's caller graph.

    Rules (deterministic, no ML):
      • 'new:*' prefix  -> additive   (explicitly marked new symbol)
      • direct caller   -> migration  (must update before removal)
      • transitive cal. -> interior   (safe once direct callers migrated)
      • anything else   -> removal    (the target symbol itself, or orphaned)
    """
    if node.startswith("new:"):
        return "additive"
    if node in direct_callers:
        return "migration"
    if node in transitive_callers:
        return "interior"
    return "removal"


# ---------------------------------------------------------------------------
# Core plan builder
# ---------------------------------------------------------------------------

def build_plan(
    symbol: str,
    edges: Dict[str, List[str]],
    direct_callers: Optional[Set[str]] = None,
    transitive_callers: Optional[Set[str]] = None,
) -> RefactoringPlan:
    """
    Build a 4-phase refactoring plan from reverse-dependency edges.

    Parameters
    ----------
    symbol : str
        The symbol being refactored (root node).
    edges : dict
        Adjacency list of reverse-dependency edges:
        ``{ dependent: [dependency, ...], ... }``
        (i.e. "dependent must come *after* dependency")
    direct_callers : set, optional
        Nodes that directly call ``symbol`` (hop-1). If omitted, inferred
        from ``edges`` keys that list ``symbol`` as a dependency.
    transitive_callers : set, optional
        Nodes that transitively call ``symbol`` (hop-2+). If omitted,
        inferred as all nodes not in ``direct_callers``.

    Returns
    -------
    RefactoringPlan
    """
    plan = RefactoringPlan(symbol=symbol)

    # -- Collect all nodes --------------------------------------------------
    all_nodes: Set[str] = set(edges.keys())
    for deps in edges.values():
        all_nodes.update(deps)
    all_nodes.add(symbol)

    # -- Infer caller sets if not provided ----------------------------------
    if direct_callers is None:
        direct_callers = {
            node for node, deps in edges.items() if symbol in deps
        }
    if transitive_callers is None:
        transitive_callers = all_nodes - direct_callers - {symbol}

    # -- Topological sort (stdlib graphlib) ---------------------------------
    sorter = TopologicalSorter(edges)
    try:
        topo_order: List[str] = list(sorter.static_order())
    except CycleError as exc:
        # Gracefully degrade: flatten edges ignoring cycles
        plan.cycles_detected.append(list(exc.args[1]) if exc.args else ["unknown cycle"])
        plan.warnings.append(
            "Cyclic dependency detected; topological order is approximate. "
            "Manually verify phase assignments for cycle members."
        )
        # Fall back to insertion-order flattening
        seen: Set[str] = set()
        topo_order = []
        for node in list(all_nodes):
            if node not in seen:
                topo_order.append(node)
                seen.add(node)

    # -- Assign each node to a phase ----------------------------------------
    for node in topo_order:
        phase = _classify_phase(node, direct_callers, transitive_callers)
        plan.phases[phase].append(node)

    # -- Ensure symbol itself is in removal if not already placed -----------
    if symbol not in plan.phases["removal"] and not any(
        symbol in plan.phases[p] for p in PHASES
    ):
        plan.phases["removal"].append(symbol)

    return plan


# ---------------------------------------------------------------------------
# Convenience: build plan directly from classify_consumers output
# ---------------------------------------------------------------------------

def plan_from_buckets(symbol: str, buckets: dict) -> RefactoringPlan:
    """
    Derive a RefactoringPlan from the 3-bucket output of ``classify_consumers``.

    Edges are reconstructed as:
      will_break items  -> depend on symbol  (direct callers)
      might_break items -> depend on will_break items (transitive)
    """
    direct = {item["symbol"] for item in buckets.get("will_break", [])}
    transitive = {item["symbol"] for item in buckets.get("might_break", [])}

    # Build a minimal edge graph: transitives depend on directs
    edges: Dict[str, List[str]] = {symbol: []}
    for d in direct:
        edges[d] = [symbol]
    for t in transitive:
        edges[t] = list(direct) if direct else [symbol]

    unknown_count = buckets.get("unknown", {}).get("unresolved_callsites", 0)
    plan = build_plan(symbol, edges, direct, transitive)

    if unknown_count:
        plan.warnings.append(
            f"{unknown_count} unresolved callsite(s) excluded from plan "
            "(dynamic dispatch / reflection – manual audit required)."
        )
    return plan


# ---------------------------------------------------------------------------
# JSON serialisation helper
# ---------------------------------------------------------------------------

def plan_to_json(plan: RefactoringPlan) -> str:
    return json.dumps(plan.to_dict(), indent=2)


# ---------------------------------------------------------------------------
# PRD phases from a map: additive → leaves → interior → removal
# ---------------------------------------------------------------------------

PRD_PHASES = ["additive", "leaves", "interior", "removal"]


def _graph_order(node: dict) -> int:
    """Return a sortable graph distance, placing incomplete evidence last."""
    value = node.get("order")
    if isinstance(value, bool):
        return 10_000
    try:
        return int(value)
    except (TypeError, ValueError):
        return 10_000


def _node_order_key(node: dict) -> tuple:
    """Keep phase output stable even when the graph returns nodes in a new order."""
    risk_order = {"will_break": 0, "might_break": 1, "unknown": 2}
    return (
        _graph_order(node),
        risk_order.get(node.get("risk"), 3),
        str(node.get("via") or ""),
        str(node.get("path") or ""),
        str(node.get("symbol") or ""),
        str(node.get("id") or ""),
    )


def _is_test_path(path: str) -> bool:
    return path.rsplit("/", 1)[-1].startswith("test_") or path.startswith("tests/")


def _patch_scope_nodes(body):
    """Visit one lexical body without treating nested bodies as its references."""
    pending = list(reversed(body))
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            pending.extend(reversed(list(ast.iter_child_nodes(node))))


def _patch_bound_names(nodes):
    """Include bind sites whose names are strings rather than Name(Store)."""
    names = set()
    for node in nodes:
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(alias.asname or alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
    return names


def _patch_reference_associations(source, node, target_path, referenced, linked,
                                  declarations, dependencies, called, import_line):
    """Separate a real reference from an unestablished descendant dependency."""
    from amu.change_analysis import _location

    reference = {"role": "test_reference", "path": source["path"], "phase": "before",
                 "location": _location(node), "source_sha256": source["sha256"],
                 "import_line": import_line, "reference": ast.unparse(node)[:400]}
    associations = []

    def broad(name):
        entity_id = f"{target_path}#{name}"
        kind = (declarations or {}).get(entity_id, {}).get("kind")
        return {"entity_id": entity_id,
                "relationship": "broad-class-reference" if kind == "class" else "broad-source-reference",
                "resolution": "unresolved", "path": [reference],
                "limitation": "A containing source reference establishes no member-specific path; absence of a resolved path is not absence of a dependency."}

    for value in linked:
        entity_id = f"{target_path}#{value}"
        declaration = (declarations or {}).get(entity_id)
        if value.startswith(referenced + ".") or (declaration or {}).get("kind") == "class":
            associations.append(broad(referenced if value.startswith(referenced + ".") else value))
            continue
        if "." in value and declarations is not None and (declaration or {}).get("resolution") != "resolved":
            # A dotted spelling alone cannot disambiguate descriptors, rebinding
            # or decorated/inherited methods. Retain the containing candidate.
            associations.append(broad(value.split(".")[0]))
            continue
        resolved = declaration is not None and declaration["resolution"] == "resolved"
        path = [reference]
        if resolved:
            path.append({"role": "source_declaration", "entity_id": entity_id, **declaration})
        associations.append({"entity_id": entity_id,
                             "relationship": "source-member-path" if resolved and "." in value else "direct-source-reference",
                             "resolution": "resolved" if resolved else "unresolved", "path": path,
                             "limitation": "A source path does not establish execution or that the test asserts affected behavior."})
        # Join only an exact, directly called consumer to a baseline lexical
        # access. Merely loading the method/function object is not such a path.
        if not (called and resolved and referenced == value):
            continue
        for dependency in dependencies:
            if dependency["consumer_id"] != entity_id or dependency["phase"] != "before":
                continue
            state_id = dependency["state_id"]
            state = (declarations or {}).get(state_id)
            if not state or state["resolution"] != "resolved":
                continue
            associations.append({
                "entity_id": state_id,
                "relationship": "source-member-path" if "." in state_id.split("#", 1)[1] else "source-state-path",
                "resolution": "resolved",
                "path": [*path, {"role": "state_access", "entity_id": state_id,
                                  "consumer_id": entity_id, "access": dependency["access"],
                                  "phase": "before", "path": target_path,
                                  "location": dependency["location"], "evidence": dependency["evidence"]}],
                "limitation": "A lexical state path does not establish execution, subclass dispatch or that the test asserts affected behavior.",
            })
    unique = []
    for association in associations:
        if association not in unique:
            unique.append(association)
    return unique


def patch_test_recommendations(sources: list[dict], affected: dict[str, set[str]], *,
                               declarations: dict | None = None, state_dependencies: list[dict] | None = None) -> list[dict]:
    """Recommend real test declarations with direct, unshadowed import references.

    This reuses the plan's test-path rule, adding syntax evidence rather than
    treating a matching filename as coverage. Application modules are not imported.
    """
    import shlex

    result = []
    for source in sources:
        path, tree = source["path"], source["tree"]
        bindings = {}
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname or "." not in alias.name:
                        bindings[alias.asname or alias.name] = (alias.name, None, node.lineno)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                for alias in node.names:
                    if alias.name != "*":
                        bindings[alias.asname or alias.name] = (node.module, alias.name, node.lineno)
        # Module-level reassignment makes an import reference unreliable.
        rebound = _patch_bound_names(_patch_scope_nodes([
            statement for statement in tree.body if not isinstance(statement, (ast.Import, ast.ImportFrom))
        ]))
        tests = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                tests.append((node.name, node))
            elif isinstance(node, ast.ClassDef) and (node.name.startswith("Test") or any(
                    isinstance(base, ast.Name) and base.id == "TestCase" or
                    isinstance(base, ast.Attribute) and base.attr == "TestCase" for base in node.bases)):
                tests.extend((f"{node.name}::{child.name}", child) for child in node.body
                             if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name.startswith("test_"))
        for name, test in tests:
            nodes = list(_patch_scope_nodes(test.body))
            parents = {child: parent for parent in nodes for child in ast.iter_child_nodes(parent)}
            local = _patch_bound_names(nodes)
            local.update(arg.arg for arg in [*test.args.posonlyargs, *test.args.args, *test.args.kwonlyargs,
                                            *([test.args.vararg] if test.args.vararg else []),
                                            *([test.args.kwarg] if test.args.kwarg else [])])
            for node in nodes:
                if not isinstance(node, (ast.Name, ast.Attribute)) or not isinstance(node.ctx, ast.Load):
                    continue
                pieces, root = [], node
                while isinstance(root, ast.Attribute):
                    pieces.append(root.attr)
                    root = root.value
                if not isinstance(root, ast.Name) or root.id in rebound | local or root.id not in bindings:
                    continue
                module, imported, import_line = bindings[root.id]
                module_path = module.replace(".", "/")
                matches = [file for file in affected if file in {module_path + ".py", module_path + "/__init__.py",
                                                                 "src/" + module_path + ".py", "src/" + module_path + "/__init__.py"}]
                if len(matches) != 1:
                    continue
                target_path = matches[0]
                referenced = ".".join(([imported] if imported else []) + list(reversed(pieces)))
                names = affected[target_path]
                linked = sorted(value for value in names if referenced == value or referenced.startswith(value + ".")
                                or value.startswith(referenced + ".") and referenced)
                if not linked:
                    continue
                parent = parents.get(node)
                associations = _patch_reference_associations(
                    source, node, target_path, referenced, linked, declarations, state_dependencies or [],
                    isinstance(parent, ast.Call) and parent.func is node, import_line)
                test_id = f"{path}::{name}"
                entry = {"test_id": test_id, "path": path, "source_sha256": source["sha256"],
                         "relationship": "direct-import-source-reference", "coverage_proven": False,
                         "entities": sorted({a["entity_id"] for a in associations}), "associations": associations,
                         "evidence": {"import_line": import_line, "reference_line": node.lineno,
                                      "reference": ast.unparse(node)[:400], "test_start_line": test.lineno,
                                      "test_end_line": test.end_lineno},
                         "verification": f"python -m pytest {shlex.quote(test_id)}",
                         "unknown": "A source reference does not establish executed coverage or an expected behavioral outcome."}
                existing = next((row for row in result if row["test_id"] == test_id), None)
                if existing:
                    existing["entities"] = sorted(set(existing["entities"]) | set(entry["entities"]))
                    existing["associations"].extend(a for a in associations if a not in existing["associations"])
                else:
                    result.append(entry)
    for entry in result:
        # A redundant ancestor load inside an explicit member expression is not
        # a separate affected entity when only that member was requested.
        direct = {a["entity_id"] for a in entry["associations"] if not a["relationship"].startswith("broad-")}
        entry["associations"] = [a for a in entry["associations"] if not (
            a["relationship"].startswith("broad-")
            and a["entity_id"].split("#", 1)[1] not in affected.get(a["entity_id"].split("#", 1)[0], set())
            and any(entity.startswith(a["entity_id"] + ".") for entity in direct))]
        entry["entities"] = sorted({a["entity_id"] for a in entry["associations"]})
    return sorted(result, key=lambda row: row["test_id"])


def documentation_scope(repo: str, requested: object = None) -> list[str]:
    """Validate explicitly requested existing prose, never infer write scope from a save.

    Re-run immediately before approval persists the contract. The docs helper
    applies the configured docs_dir boundary; this narrower planning path also
    refuses executable MDX and every symlink alias, including parent aliases.
    """
    if requested is None:
        return []
    if not isinstance(requested, list) or len(requested) > 100:
        raise ValueError("docs scope must be a list of at most 100 --docs-file paths")
    from amu import docs

    root = Path(repo).resolve()
    canonical: set[str] = set()
    for value in requested:
        if not isinstance(value, str) or not value.strip() or "\x00" in value:
            raise ValueError("docs scope paths must be nonempty strings")
        raw = Path(value)
        if raw.is_absolute() or ".." in raw.parts or raw.suffix.lower() not in {".md", ".rst"}:
            raise ValueError("docs scope requires repository-relative Markdown/RST prose paths without traversal")
        safe = docs._safe_mapped_doc_path({"file": value}, str(root))
        if safe is None or safe != root / raw:
            raise ValueError("docs scope must name an existing Markdown/RST file inside docs_dir, without symlinks")
        canonical.add(safe.relative_to(root).as_posix())
    return sorted(canonical)


def plan_from_map(amu_map: dict, targets: list[dict], docs_files: list[str] | None = None) -> list[dict]:
    """Each phase lists node ids + files + tests + reason + verify. Deterministic, no ML."""
    nodes = sorted(amu_map["nodes"], key=_node_order_key)
    root = amu_map["root"]["file"]
    known = [n for n in nodes if n["confidence"] != "unknown"]
    known, leaf_refs, ordering = dependencies.order(amu_map, known)
    leaves = [n for n in known if n["confidence"] == "sound" and f"{n['path']}#{n.get('symbol')}" in leaf_refs]
    leaf_ids = {n["id"] for n in leaves}
    interior = [n for n in known if n["id"] not in leaf_ids]
    unknown = [n for n in nodes if n["confidence"] == "unknown"]
    tests = sorted({n["path"] for n in known if _is_test_path(n["path"])})
    removal_needed = any(t["change_class"] in ("remove", "rename", "move", "signature") for t in targets)
    phases = [
        {"n": 1, "kind": "additive", "nodes": [], "files": [root], "tests": tests,
         "reason": "introduce the new shape next to the old one; nothing breaks yet",
         "verify": "amu check --phase 1"},
        {"n": 2, "kind": "leaves", "nodes": [n["id"] for n in leaves], "files": sorted({n["path"] for n in leaves}), "tests": tests,
         "reason": "migrate consumers with complete recorded caller coverage and no observed dependants", "verify": "amu check --phase 2"},
        {"n": 3, "kind": "interior", "nodes": [n["id"] for n in interior], "files": sorted({n["path"] for n in interior}), "tests": tests,
         "reason": "migrate in observed consumer-first dependency groups; cycles move together, uncovered/guessed nodes need review", "verify": "amu check --phase 3"},
        {"n": 4, "kind": "removal", "nodes": [], "files": [root] if removal_needed else [], "tests": tests,
         "reason": "remove the old shape only after phases 1-3 are green" if removal_needed else "no removal for a body change",
         "verify": "amu check --phase 4"},
    ]
    for p in phases:
        p["ordering"] = ordering
        p["open_questions"] = [f"{u['id']} {u['path']}: {u['reason']} → {u['verify']}" for u in unknown]
    if docs_files:
        phases[-1]["files"] = sorted(set(phases[-1]["files"]) | set(docs_files))
        phases[-1]["reason"] += "; review and update explicitly scoped documentation after the code phases"
    return phases


def make_contract(amu_map: dict, phases: list[dict], targets: list[dict], base_sha: str, created_at: str) -> dict:
    files = sorted({f for p in phases for f in p["files"]})
    frozen = [f"{n['path']}#{n['symbol']}" for n in amu_map["nodes"]
              if n["confidence"] == "sound" and n["risk"] == "will_break" and n["symbol"]]
    return {"targets": targets, "base_sha": base_sha, "map_hash": amu_map["map_hash"], "phases": phases,
            "frozen_signatures": frozen, "allowed_files": files, "created_at": created_at}
