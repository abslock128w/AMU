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
import math
from dataclasses import dataclass, field
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Dict, List, Optional, Set

from amu import dependencies
from amu.outbound import redact

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


MAX_PATCH_LOCAL_CALL_FUNCTIONS = 128
MAX_PATCH_LOCAL_CALL_EDGES = 256
MAX_PATCH_LOCAL_CALL_HOPS = 2
MAX_PATCH_TEST_NODES = 512
MAX_PATCH_PARAMETER_CASES = 64
MAX_PATCH_BEHAVIOR_ANALYSES = 4096


def patch_source_call_edges(source_trees: dict, declarations: dict) -> tuple[dict, list[dict]]:
    """Find bounded, unshadowed direct calls between baseline module functions.

    A module-level ``def``/``async def`` must have one resolved declaration and
    no decorator. Calls must name another such function directly. Any local
    binding of that name, dynamic module namespace, or limit exhaustion makes
    this optional path unavailable; there is no runtime dispatch inference.
    """
    from amu.change_analysis import _dynamic_namespace, _location

    result, limitations = {}, []
    for path, source in sorted(source_trees.items()):
        tree = source["tree"]
        definitions = {node.name: node for node in tree.body
                       if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                       and not node.decorator_list
                       and (declarations.get(f"{path}#{node.name}") or {}).get("resolution") == "resolved"}
        if len(definitions) > MAX_PATCH_LOCAL_CALL_FUNCTIONS:
            limitations.append({"path": path, "reason": "local direct-call function limit reached"})
            continue
        adjacency, count = {}, 0
        for caller_name, caller in sorted(definitions.items()):
            if _dynamic_namespace(caller):
                continue
            nodes = list(_patch_scope_nodes(caller.body))
            local = _patch_bound_names(nodes)
            local.update(arg.arg for arg in [*caller.args.posonlyargs, *caller.args.args, *caller.args.kwonlyargs,
                                            *([caller.args.vararg] if caller.args.vararg else []),
                                            *([caller.args.kwarg] if caller.args.kwarg else [])])
            for node in nodes:
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                    continue
                callee_name = node.func.id
                if callee_name not in definitions or callee_name in local:
                    continue
                adjacency.setdefault(caller_name, []).append({
                    "role": "source_call", "path": path, "phase": "before",
                    "caller_id": f"{path}#{caller_name}", "callee_id": f"{path}#{callee_name}",
                    "location": _location(node), "source_sha256": source["sha256"],
                    "evidence": redact(ast.unparse(node))[:400],
                })
                count += 1
                if count > MAX_PATCH_LOCAL_CALL_EDGES:
                    break
            if count > MAX_PATCH_LOCAL_CALL_EDGES:
                break
        if count > MAX_PATCH_LOCAL_CALL_EDGES:
            limitations.append({"path": path, "reason": "local direct-call edge limit reached"})
            continue
        result[path] = {name: sorted(edges, key=lambda edge: (
            edge["location"]["line"], edge["location"]["column"], edge["callee_id"]))
                        for name, edges in adjacency.items()}
    return result, limitations


def _patch_test_reference(source, node, import_line):
    from amu.change_analysis import _location

    return {"role": "test_reference", "path": source["path"], "phase": "before",
            "location": _location(node), "source_sha256": source["sha256"],
            "import_line": import_line, "reference": redact(ast.unparse(node))[:400]}


def _patch_call_associations(source, node, target_path, referenced, affected_names,
                             declarations, source_calls, import_line):
    """Join a directly called baseline source function to changed local callees."""
    if referenced not in source_calls.get(target_path, {}):
        return []
    root_id = f"{target_path}#{referenced}"
    root_declaration = (declarations or {}).get(root_id)
    if not root_declaration or root_declaration.get("resolution") != "resolved":
        return []
    path = [_patch_test_reference(source, node, import_line),
            {"role": "source_declaration", "entity_id": root_id, **root_declaration}]
    queue = [(referenced, path, frozenset({referenced}), 0)]
    associations = {}
    while queue:
        current, evidence, seen, hops = queue.pop(0)
        if hops >= MAX_PATCH_LOCAL_CALL_HOPS:
            continue
        for edge in source_calls[target_path].get(current, []):
            callee_id = edge["callee_id"]
            callee_name = callee_id.split("#", 1)[1]
            if callee_name in seen:
                continue
            declaration = (declarations or {}).get(callee_id)
            if not declaration or declaration.get("resolution") != "resolved":
                continue
            steps = [*evidence, edge, {"role": "source_declaration", "entity_id": callee_id, **declaration}]
            if callee_name in affected_names and callee_id not in associations:
                associations[callee_id] = {
                    "entity_id": callee_id, "relationship": "source-call-path", "resolution": "resolved",
                    "path": steps,
                    "limitation": "A baseline lexical call path does not establish execution, dynamic targets or that the test asserts affected behavior.",
                }
            queue.append((callee_name, steps, seen | {callee_name}, hops + 1))
    return [associations[key] for key in sorted(associations)]


def _patch_reference_associations(source, node, target_path, referenced, linked,
                                  declarations, dependencies, called, import_line):
    """Separate a real reference from an unestablished descendant dependency."""
    reference = _patch_test_reference(source, node, import_line)
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


def _patch_literal(node):
    """A deliberately small static value language for test inputs."""
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, RecursionError, MemoryError):
        return None, False
    if isinstance(value, (str, int, float, bool, type(None))) and not (
            isinstance(value, float) and not math.isfinite(value)):
        return value, True
    return None, False


def _patch_parameter_cases(test):
    """Read literal pytest cases without evaluating the decorator or fixtures."""
    cases = [{}]
    for decorator in test.decorator_list:
        if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "parametrize" and len(decorator.args) >= 2
                and isinstance(decorator.func.value, ast.Attribute)
                and decorator.func.value.attr == "mark"
                and isinstance(decorator.func.value.value, ast.Name)
                and decorator.func.value.value.id == "pytest"):
            continue
        if any(keyword.arg == "indirect" and not (
                isinstance(keyword.value, ast.Constant) and keyword.value.value is False)
               for keyword in decorator.keywords):
            return []
        names, valid = _patch_literal(decorator.args[0])
        if not valid or not isinstance(names, str):
            return []
        parameters = [part.strip() for part in names.split(",")]
        if not parameters or not all(part.isidentifier() for part in parameters):
            return []
        try:
            values = ast.literal_eval(decorator.args[1])
        except (ValueError, TypeError, SyntaxError, RecursionError, MemoryError):
            return []
        if not isinstance(values, (list, tuple)) or len(values) > MAX_PATCH_PARAMETER_CASES:
            return []
        next_cases = []
        for value in values:
            row = value if len(parameters) > 1 and isinstance(value, (list, tuple)) else (value,)
            if len(row) != len(parameters) or not all(isinstance(item, (str, int, float, bool, type(None))) for item in row):
                return []
            for prior in cases:
                next_cases.append({**prior, **dict(zip(parameters, row))})
                if len(next_cases) > MAX_PATCH_PARAMETER_CASES:
                    return []
        cases = next_cases
    return cases


def _patch_import_target(expr, bindings, affected, verified_submodules, excluded=frozenset()):
    """Resolve a direct imported spelling to one baseline module and member."""
    pieces, root = [], expr
    while isinstance(root, ast.Attribute):
        pieces.append(root.attr)
        root = root.value
    if not isinstance(root, ast.Name) or root.id not in bindings or root.id in excluded:
        return None
    module, imported, _ = bindings[root.id]
    submodule = (verified_submodules or {}).get((module, imported)) if imported else None
    if submodule is not None:
        matches = [submodule] if submodule in affected else []
        name = ".".join(reversed(pieces))
    else:
        module_path = module.replace(".", "/")
        matches = [path for path in affected if path in {
            module_path + ".py", module_path + "/__init__.py",
            "src/" + module_path + ".py", "src/" + module_path + "/__init__.py"}]
        name = ".".join(([imported] if imported else []) + list(reversed(pieces)))
    return (matches[0], name) if len(matches) == 1 and name else None


def _patch_test_calls(test, bindings, affected, verified_submodules, declarations, source_trees, excluded):
    """Resolve direct calls and one-assignment constructor receivers in a test."""
    nodes = list(_patch_scope_nodes(test.body))
    if len(nodes) > MAX_PATCH_TEST_NODES:
        return []
    from amu.change_analysis import _binding_counts

    counts = _binding_counts(test)
    instances = {}
    aliases = {}
    calls = []
    mutations = {_patch_import_target(node, bindings, affected, verified_submodules, excluded)
                 for node in nodes if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del))}
    for statement in test.body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
            name = statement.targets[0].id
            if counts.get(name) == 1:
                if isinstance(statement.value, ast.Call):
                    target = _patch_import_target(statement.value.func, bindings, affected, verified_submodules, excluded)
                    if target and (declarations or {}).get(f"{target[0]}#{target[1]}", {}).get("kind") == "class":
                        class_id = f"{target[0]}#{target[1]}"
                        class_decl = declarations[class_id]
                        cls = next((item for item in (source_trees or {}).get(target[0], {}).get("tree", ast.Module(body=[], type_ignores=[])).body
                                    if isinstance(item, ast.ClassDef) and item.name == target[1]), None)
                        if (class_decl.get("resolution") == "resolved" and cls is not None
                                and not cls.bases and not cls.keywords and not cls.decorator_list
                                and not any(isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                                            and child.name in {"__getattribute__", "__getattr__", "__new__"} for child in cls.body)):
                            instances[name] = (target[0], target[1], statement.value)
                elif isinstance(statement.value, (ast.Name, ast.Attribute)):
                    target = _patch_import_target(statement.value, bindings, affected, verified_submodules, excluded)
                    if target:
                        aliases[name] = target
        # Record calls only in direct test statements, including assertion and
        # with bodies. Nested function/class bodies are excluded by scope walk.
        for node in _patch_scope_nodes([statement]):
            if not isinstance(node, ast.Call):
                continue
            target = _patch_import_target(node.func, bindings, affected, verified_submodules, excluded)
            origin = "direct import"
            if isinstance(node.func, ast.Name) and node.func.id in aliases:
                target, origin = aliases[node.func.id], "local alias"
            elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                receiver = node.func.value.id
                if receiver in instances:
                    path, cls, constructor = instances[receiver]
                    target, origin = (path, f"{cls}.{node.func.attr}"), "constructed instance"
                    cls_node = next(item for item in source_trees[path]["tree"].body
                                    if isinstance(item, ast.ClassDef) and item.name == cls)
                    # A method name can be shadowed on the object by its own
                    # initializer or any method, or by a test-side assignment.
                    shadowed = any(isinstance(item, ast.Attribute) and isinstance(item.ctx, (ast.Store, ast.Del))
                                   and item.attr == node.func.attr for item in ast.walk(cls_node))
                    shadowed = shadowed or any(
                        isinstance(item, ast.Attribute) and isinstance(item.value, ast.Name)
                        and item.value.id == receiver and item.attr == node.func.attr
                        and isinstance(item.ctx, (ast.Store, ast.Del)) for item in nodes)
                    shadowed = shadowed or any(isinstance(item, ast.Call) and isinstance(item.func, ast.Name)
                                               and item.func.id in {"setattr", "delattr", "exec", "eval"}
                                               for item in [*ast.walk(cls_node), *nodes])
                    if shadowed:
                        continue
                elif receiver in aliases:
                    path, name = aliases[receiver]
                    target, origin = (path, f"{name}.{node.func.attr}"), "local alias"
            if not target:
                continue
            if any(mutation is not None and target[0] == mutation[0]
                   and (target[1] == mutation[1] or target[1].startswith(mutation[1] + "."))
                   for mutation in mutations):
                continue
            entity_id = f"{target[0]}#{target[1]}"
            if (declarations or {}).get(entity_id, {}).get("resolution") != "resolved":
                continue
            calls.append({"node": node, "entity_id": entity_id, "origin": origin,
                          "constructor": instances.get(node.func.value.id)[2]
                          if origin == "constructed instance" else None})
    return calls


def _patch_call_argument(call, entity_id, parameter, source_trees, cases, *, bound_receiver=False):
    if any(isinstance(argument, ast.Starred) for argument in call.args) or any(
            keyword.arg is None for keyword in call.keywords):
        return None
    path, name = entity_id.split("#", 1)
    body = (source_trees or {}).get(path, {}).get("tree")
    if body is None:
        return None
    parts = name.split(".")
    definitions = body.body
    if len(parts) == 2:
        cls = next((item for item in definitions if isinstance(item, ast.ClassDef) and item.name == parts[0]), None)
        if cls is None:
            return None
        definitions = cls.body
    elif len(parts) != 1:
        return None
    definition = next((item for item in definitions if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                       and item.name == parts[-1]), None)
    if definition is None:
        return None
    parameters = [arg.arg for arg in [*definition.args.posonlyargs, *definition.args.args]]
    classmethod = len(definition.decorator_list) == 1 and isinstance(definition.decorator_list[0], ast.Name) and definition.decorator_list[0].id == "classmethod"
    if len(parts) == 2 and parameters and (bound_receiver or classmethod):
        parameters = parameters[1:]
    if parameter not in parameters and parameter not in {arg.arg for arg in definition.args.kwonlyargs}:
        return None
    index = parameters.index(parameter) if parameter in parameters else len(call.args)
    expressions = [call.args[index]] if parameter in parameters and index < len(call.args) else [keyword.value for keyword in call.keywords if keyword.arg == parameter]
    if len(expressions) != 1:
        return None
    expression = expressions[0]
    value, literal = _patch_literal(expression)
    if literal:
        return [(value, redact(ast.unparse(expression)), None)]
    if isinstance(expression, ast.Name):
        return [(case[expression.id], redact(f"{expression.id}={case[expression.id]!r}"), case_index)
                for case_index, case in enumerate(cases) if expression.id in case]
    return None


def _patch_assertion_for_call(test, call, obligation):
    from amu.change_analysis import _binding_counts

    call_node = call["node"]
    observable = obligation.get("observable") or {}
    kind = observable.get("kind") if isinstance(observable, dict) else None

    def observed_atom(node, result):
        if node is result or isinstance(result, str) and isinstance(node, ast.Name) and node.id == result:
            return True
        return isinstance(node, ast.Attribute) and observed_atom(node.value, result)

    def observes(expression, result):
        if isinstance(expression, ast.Compare):
            return any(observed_atom(item, result) for item in [expression.left, *expression.comparators])
        if isinstance(expression, ast.UnaryOp) and isinstance(expression.op, ast.Not):
            return observed_atom(expression.operand, result)
        return observed_atom(expression, result)

    for node in _patch_scope_nodes(test.body):
        if isinstance(node, ast.Assert) and kind != "exception":
            if observes(node.test, call_node):
                return {"line": node.lineno, "expression": redact(ast.unparse(node.test))[:200],
                        "observes": "called result"}
            for assignment in _patch_scope_nodes(test.body):
                if not (isinstance(assignment, (ast.Assign, ast.AnnAssign)) and assignment.value is call_node):
                    continue
                targets = assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
                if len(targets) != 1 or not isinstance(targets[0], ast.Name):
                    continue
                local_name = targets[0].id
                stores = _binding_counts(test).get(local_name, 0)
                if stores == 1 and assignment.end_lineno < node.lineno and observes(node.test, local_name):
                    return {"line": node.lineno, "expression": redact(ast.unparse(node.test))[:200],
                            "observes": f"result local {local_name}"}
        if kind in {"exception", "exception-or-handler-result"} and isinstance(node, ast.With):
            for item in node.items:
                context = item.context_expr
                if not (isinstance(context, ast.Call) and isinstance(context.func, ast.Attribute)
                        and context.func.attr == "raises" and isinstance(context.func.value, ast.Name)
                        and context.func.value.id == "pytest" and context.args):
                    continue
                expected = ast.unparse(context.args[0])
                condition = obligation.get("condition")
                if (isinstance(condition, dict) and condition.get("exception")
                        and expected != condition["exception"]):
                    continue
                if (isinstance(condition, dict) and condition.get("exceptions")
                        and expected not in condition["exceptions"]):
                    continue
                if any(child is call_node for statement in node.body for child in ast.walk(statement)):
                    return {"line": node.lineno, "expression": redact(f"pytest.raises({expected})"),
                            "observes": "raised exception"}
    return None


def _patch_input_for_call(call, target, condition, source_trees, cases):
    """Evaluate only literal comparisons; never evaluate source expressions."""
    if not isinstance(condition, dict):
        return None
    node = call["node"]
    bound_receiver = call.get("origin") == "constructed instance"
    if condition.get("kind") == "omitted-argument":
        path, name = target.split("#", 1)
        tree = (source_trees or {}).get(path, {}).get("tree")
        definition = next((item for item in (tree.body if tree else [])
                           if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name), None)
        if definition and not any(isinstance(argument, ast.Starred) for argument in node.args) and not any(
                keyword.arg is None for keyword in node.keywords):
            positional = [arg.arg for arg in [*definition.args.posonlyargs, *definition.args.args]]
            parameter = condition["parameter"]
            supplied = parameter in positional and positional.index(parameter) < len(node.args)
            supplied = supplied or any(keyword.arg == parameter for keyword in node.keywords)
            if not supplied:
                return {"line": node.lineno, "condition": f"{parameter} omitted",
                        "argument": "not supplied"}
    if condition.get("kind") == "comparison-boundary" and condition.get("target_value") is not None:
        for value, spelling, case_index in _patch_call_argument(
                node, target, condition.get("parameter"), source_trees, cases, bound_receiver=bound_receiver) or []:
            if value == condition["target_value"] and type(value) is type(condition["target_value"]):
                evidence = {"line": node.lineno, "argument": spelling,
                            "condition": f"{condition['parameter']} == {condition['target_value']!r}"}
                if case_index is not None:
                    evidence["parameter_case"] = case_index
                return evidence
    if condition.get("kind") == "comparison-boundary" and condition.get("target_parameter"):
        for case_index, case in enumerate(cases or [{}]):
            left = _patch_call_argument(node, target, condition["parameter"], source_trees, [case], bound_receiver=bound_receiver) or []
            right = _patch_call_argument(node, target, condition["target_parameter"], source_trees, [case], bound_receiver=bound_receiver) or []
            if len(left) == len(right) == 1 and type(left[0][0]) is type(right[0][0]) and left[0][0] == right[0][0]:
                return {"line": node.lineno, "arguments": [left[0][1], right[0][1]],
                        "condition": f"{condition['parameter']} == {condition['target_parameter']}",
                        "parameter_case": case_index, "reaching_input_proven": False}
    if condition.get("kind") == "branch-path":
        comparisons = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
                       ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
                       "==": lambda a, b: a == b, "!=": lambda a, b: a != b}
        for case_index, case in enumerate(cases or [{}]):
            inputs = []
            for guard in condition.get("guards", []):
                arguments = _patch_call_argument(node, target, guard["parameter"], source_trees, [case],
                                                 bound_receiver=bound_receiver) or []
                if len(arguments) != 1 or guard.get("operator") not in comparisons:
                    break
                value, spelling, _ = arguments[0]
                try:
                    taken = comparisons[guard["operator"]](value, guard["value"])
                except (TypeError, ValueError):
                    break
                if taken != guard["taken"]:
                    break
                inputs.append({"argument": spelling, "condition":
                               f"{guard['parameter']} {guard['operator']} {guard['value']!r}",
                               "taken": guard["taken"]})
            else:
                if inputs:
                    return {"line": node.lineno, "guards": inputs, "parameter_case": case_index,
                            "phase": condition.get("phase"), "reaching_input_proven": False}
    return None


def _patch_state_return_passthrough(source_trees, state_id, consumer_id):
    """Prove only the simplest lexical member-to-return flow."""
    path, state_name = state_id.split("#", 1)
    _, method_name = consumer_id.split("#", 1)
    parts = method_name.split(".")
    if len(parts) != 2 or state_name != f"{parts[0]}.{state_name.rsplit('.', 1)[-1]}":
        return False
    tree = (source_trees or {}).get(path, {}).get("tree")
    if tree is None:
        return False
    cls = next((node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == parts[0]), None)
    method = next((node for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and node.name == parts[1]), None) if cls else None
    if method is None or len(method.body) != 1 or not isinstance(method.body[0], ast.Return):
        return False
    value = method.body[0].value
    receiver = method.args.args[0].arg if method.args.args else None
    return (isinstance(value, ast.Attribute) and value.attr == state_name.rsplit(".", 1)[-1]
            and isinstance(value.value, ast.Name) and value.value.id == receiver)


def _patch_obligation_evidence(test, association, obligation, calls, source_trees, cases, bindings=None):
    unresolved = association.get("resolution") != "resolved"
    if unresolved:
        return {"obligation_id": obligation["id"], "level": "unresolved", "input_evidence": None,
                "assertion_evidence": None, "reason": "The baseline source relationship is unresolved."}
    test_nodes = list(_patch_scope_nodes(test.body))
    if len(test_nodes) > MAX_PATCH_TEST_NODES:
        return {"obligation_id": obligation["id"], "level": "unresolved", "input_evidence": None,
                "assertion_evidence": None, "reason": "Test behavior exceeds the bounded 512-node inspection limit."}
    used_names = {node.id for node in [*test_nodes, *[child for decorator in test.decorator_list for child in ast.walk(decorator)]]
                  if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
    if any(name in used_names and (module == "unittest.mock" or module.startswith("unittest.mock.")
                                  or module == "unittest" and imported == "mock")
           for name, (module, imported, _) in (bindings or {}).items()):
        return {"obligation_id": obligation["id"], "level": "unresolved", "input_evidence": None,
                "assertion_evidence": None, "reason": "Mock imports leave real downstream behavior unresolved."}
    for decorator in test.decorator_list:
        if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "parametrize" and isinstance(decorator.func.value, ast.Attribute)
                and decorator.func.value.attr == "mark" and isinstance(decorator.func.value.value, ast.Name)
                and decorator.func.value.value.id == "pytest"):
            return {"obligation_id": obligation["id"], "level": "unresolved", "input_evidence": None,
                    "assertion_evidence": None, "reason": "Arbitrary test decorators leave the callable's real behavior unresolved."}
    if any(isinstance(node, ast.Call) and (
            isinstance(node.func, ast.Name) and node.func.id in {"patch", "Mock", "MagicMock"}
            or isinstance(node.func, ast.Attribute) and node.func.attr in {"patch", "setattr", "patch.object"}
            and isinstance(node.func.value, ast.Name) and node.func.value.id in {"mock", "monkeypatch"})
           for node in test_nodes):
        return {"obligation_id": obligation["id"], "level": "broad", "input_evidence": None,
                "assertion_evidence": None,
                "reason": "A mock or patch in the test prevents this bounded check from establishing real behavior."}
    target = association["entity_id"]
    for step in association.get("path", []):
        if step.get("role") == "state_access":
            target = step["consumer_id"]
            break
    if association.get("relationship") == "source-call-path":
        return {"obligation_id": obligation["id"], "level": "broad", "input_evidence": None,
                "assertion_evidence": None,
                "reason": "The baseline call path is supported, but argument forwarding and result propagation were not established."}
    relevant_calls = [call for call in calls if call["entity_id"] == target]
    condition = obligation.get("condition")
    input_evidence = assertion_evidence = None
    paired = False
    for call in relevant_calls:
        call_input = _patch_input_for_call(call, target, condition, source_trees, cases)
        call_assertion = _patch_assertion_for_call(test, call, obligation)
        if call_input and call_assertion:
            input_evidence, assertion_evidence, paired = call_input, call_assertion, True
            break
        input_evidence = input_evidence or call_input
        assertion_evidence = assertion_evidence or call_assertion
    state_step = next((step for step in association.get("path", []) if step.get("role") == "state_access"), None)
    if state_step and not (state_step.get("access") == "read" and
                           _patch_state_return_passthrough(source_trees, association["entity_id"], target)):
        # A method can touch state and return something unrelated. Its result
        # assertion then does not establish observation of that state.
        assertion_evidence = None
        paired = False
    # Direct assertion of a changed class/module state is an observation even
    # when no method call is present; it does not prove the changed path ran.
    if assertion_evidence is None and (obligation.get("observable") or {}).get("kind") == "state":
        reference = association.get("path", [{}])[0]
        ref_line = reference.get("location", {}).get("line")
        for node in _patch_scope_nodes(test.body):
            if isinstance(node, ast.Assert) and any(getattr(child, "lineno", None) == ref_line
                                                    and ast.unparse(child) == reference.get("reference")
                                                    for child in ast.walk(node.test)):
                assertion_evidence = {"line": node.lineno, "expression": redact(ast.unparse(node.test))[:200],
                                      "observes": "shared state"}
                break
    level = "input-and-assertion" if paired else "assertion" if assertion_evidence else "input" if input_evidence else "broad"
    reason = {
        "input-and-assertion": "Literal input matches the supported changed condition and an assertion observes that same call result; runtime reachability is unverified.",
        "input": "Literal input matches the supported changed condition; no relevant assertion for that call was established.",
        "assertion": "An assertion observes the related call result; matching the changed input condition was not established.",
        "broad": "The supported source relationship does not establish a matching input or observing assertion.",
    }[level]
    return {"obligation_id": obligation["id"], "level": level,
            "input_evidence": input_evidence, "assertion_evidence": assertion_evidence, "reason": reason}


def patch_test_recommendations(sources: list[dict], affected: dict[str, set[str]], *,
                               declarations: dict | None = None, state_dependencies: list[dict] | None = None,
                               source_calls: dict | None = None,
                               verified_submodules: dict[tuple[str, str], str] | None = None,
                               source_trees: dict | None = None,
                               obligations: list[dict] | None = None) -> list[dict]:
    """Recommend real test declarations with direct, unshadowed import references.

    This reuses the plan's test-path rule, adding syntax evidence rather than
    treating a matching filename as coverage. Application modules are not imported.
    """
    import shlex

    result = []
    behavior_work = 0
    test_contexts = {}
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
            test_id = f"{path}::{name}"
            parents = {child: parent for parent in nodes for child in ast.iter_child_nodes(parent)}
            local = _patch_bound_names(nodes)
            local.update(arg.arg for arg in [*test.args.posonlyargs, *test.args.args, *test.args.kwonlyargs,
                                            *([test.args.vararg] if test.args.vararg else []),
                                            *([test.args.kwarg] if test.args.kwarg else [])])
            test_contexts[test_id] = (source, test, bindings,
                                      _patch_test_calls(test, bindings, affected, verified_submodules,
                                                        declarations, source_trees, local | rebound),
                                      [{key: value for key, value in case.items() if key not in _patch_bound_names(nodes)}
                                       for case in _patch_parameter_cases(test)])
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
                submodule = (verified_submodules or {}).get((module, imported)) if imported else None
                if submodule is not None:
                    matches = [submodule] if submodule in affected else []
                    referenced = ".".join(reversed(pieces))
                else:
                    module_path = module.replace(".", "/")
                    matches = [file for file in affected if file in {module_path + ".py", module_path + "/__init__.py",
                                                                     "src/" + module_path + ".py", "src/" + module_path + "/__init__.py"}]
                    referenced = ".".join(([imported] if imported else []) + list(reversed(pieces)))
                if len(matches) != 1:
                    continue
                target_path = matches[0]
                names = affected[target_path]
                linked = sorted(value for value in names if referenced == value or referenced.startswith(value + ".")
                                or value.startswith(referenced + ".") and referenced)
                parent = parents.get(node)
                called = isinstance(parent, ast.Call) and parent.func is node
                associations = _patch_reference_associations(
                    source, node, target_path, referenced, linked, declarations, state_dependencies or [],
                    called, import_line) if linked else []
                if called and source_calls:
                    associations.extend(_patch_call_associations(
                        source, node, target_path, referenced, names, declarations, source_calls, import_line))
                if not associations:
                    continue
                entry = {"test_id": test_id, "path": path, "source_sha256": source["sha256"],
                         "relationship": "direct-import-source-reference", "coverage_proven": False,
                         "test_source_phase": "before",
                         "entities": sorted({a["entity_id"] for a in associations}), "associations": associations,
                         "evidence": {"import_line": import_line, "reference_line": node.lineno,
                                      "reference": redact(ast.unparse(node))[:400], "test_start_line": test.lineno,
                                      "test_end_line": test.end_lineno},
                         "verification": f"python -m pytest {shlex.quote(test_id)}",
                         "unknown": "A source reference does not establish executed coverage or an expected behavioral outcome."}
                existing = next((row for row in result if row["test_id"] == test_id), None)
                if existing:
                    existing["entities"] = sorted(set(existing["entities"]) | set(entry["entities"]))
                    existing["associations"].extend(a for a in associations if a not in existing["associations"])
                else:
                    result.append(entry)
    by_id = {row["test_id"]: row for row in result}
    for test_id, (source, test, bindings, calls, cases) in test_contexts.items():
        row = by_id.get(test_id)
        if row is None:
            continue
        for call in calls:
            if call["origin"] != "constructed instance":
                continue
            entity_id = call["entity_id"]
            target_path, method_name = entity_id.split("#", 1)
            if method_name not in affected.get(target_path, set()):
                relevant_state = any(dep.get("consumer_id") == entity_id and dep.get("phase") == "before"
                                     and dep.get("state_id", "").split("#", 1)[-1] in affected.get(target_path, set())
                                     for dep in state_dependencies or [])
                if not relevant_state:
                    continue
            cls_name = method_name.split(".", 1)[0]
            class_id = f"{target_path}#{cls_name}"
            method_decl = (declarations or {}).get(entity_id)
            class_decl = (declarations or {}).get(class_id)
            if not method_decl or not class_decl:
                continue
            constructor = call["constructor"]
            root = constructor.func
            while isinstance(root, ast.Attribute):
                root = root.value
            import_line = bindings.get(root.id, (None, None, None))[2] if isinstance(root, ast.Name) else None
            path_steps = [_patch_test_reference(source, call["node"].func, import_line),
                          {"role": "constructed_instance", "path": source["path"], "phase": "before",
                           "location": {"line": constructor.lineno, "column": constructor.col_offset},
                           "evidence": redact(ast.unparse(constructor))[:200]},
                          {"role": "source_declaration", "entity_id": class_id, **class_decl},
                          {"role": "source_declaration", "entity_id": entity_id, **method_decl}]
            if method_name in affected.get(target_path, set()):
                row["associations"].append({"entity_id": entity_id, "relationship": "constructed-instance-method",
                                            "resolution": "resolved", "path": path_steps,
                                            "limitation": "A baseline constructor and method call do not prove runtime dispatch or assertion coverage."})
            for dep in state_dependencies or []:
                state_id = dep.get("state_id")
                if dep.get("phase") != "before" or dep.get("consumer_id") != entity_id or not state_id:
                    continue
                if state_id.split("#", 1)[-1] not in affected.get(target_path, set()):
                    continue
                state_decl = (declarations or {}).get(state_id)
                if not state_decl or state_decl.get("resolution") != "resolved":
                    continue
                row["associations"].append({"entity_id": state_id, "relationship": "constructed-instance-state-path",
                                            "resolution": "resolved", "path": [*path_steps,
                                                {"role": "state_access", "entity_id": state_id,
                                                 "consumer_id": entity_id, "access": dep["access"],
                                                 "phase": "before", "path": target_path,
                                                 "location": dep["location"], "evidence": dep["evidence"]}],
                                            "limitation": "A lexical state path does not prove execution, dynamic dispatch or assertion coverage."})
    for entry in result:
        # A redundant ancestor load inside an explicit member expression is not
        # a separate affected entity when only that member was requested.
        direct = {a["entity_id"] for a in entry["associations"] if not a["relationship"].startswith("broad-")}
        entry["associations"] = [a for a in entry["associations"] if not (
            a["relationship"].startswith("broad-")
            and a["entity_id"].split("#", 1)[1] not in affected.get(a["entity_id"].split("#", 1)[0], set())
            and any(entity.startswith(a["entity_id"] + ".") for entity in direct))]
        entry["entities"] = sorted({a["entity_id"] for a in entry["associations"]})
        if obligations is not None:
            source, test, bindings, calls, cases = test_contexts[entry["test_id"]]
            for association in entry["associations"]:
                matched = [item for item in obligations if item.get("entity_id") == association["entity_id"]]
                available = max(0, MAX_PATCH_BEHAVIOR_ANALYSES - behavior_work)
                association["omitted_obligation_evidence"] = max(0, len(matched) - available)
                if association["omitted_obligation_evidence"]:
                    entry["behavioral_analysis_limit"] = "Behavioral test evidence reached its 4096 obligation/association work limit; candidates remain retrievable."
                matched = matched[:available]
                behavior_work += len(matched)
                association["obligation_evidence"] = [
                    _patch_obligation_evidence(test, association, item, calls, source_trees, cases, bindings)
                    for item in matched if item.get("id")]
            ranks = {"input-and-assertion": 0, "assertion": 1, "input": 2, "broad": 3, "unresolved": 4}
            entry["behavioral_rank"] = min((ranks[evidence["level"]] for association in entry["associations"]
                                            for evidence in association["obligation_evidence"]), default=4)
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
