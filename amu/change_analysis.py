"""Bounded syntax evidence for concrete Python changes; project code is never run.

Dependencies are lexical candidates, not a runtime call graph. In particular,
classmethod inheritance, descriptors, aliases and arbitrary decorators require
verification. Hashes cover complete AST records even when display text is cut.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import PurePosixPath

from .outbound import redact

MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_AST_NODES = 50_000
MAX_AST_DEPTH = 200
MAX_ENTITIES = 2_000
MAX_DEPENDENCIES = 2_000
MAX_FINDINGS = 2_000
MAX_TEXT = 1_000
MAX_UNCERTAINTIES = 200
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCOPES = (*_FUNCTIONS, ast.ClassDef, ast.Lambda)
_CACHE_METHODS = {
    "get": "cache_get", "setdefault": "cache_setdefault", "pop": "cache_pop",
    "popitem": "cache_popitem", "update": "cache_update", "clear": "cache_clear",
    "__getitem__": "cache_get", "__setitem__": "cache_set", "__delitem__": "cache_delete",
}
_NOTICE = "bounded static syntax and lexical dependency candidates; runtime behavior requires verification"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _dump(node: ast.AST) -> str:
    return ast.dump(node, include_attributes=False)


def _digest(nodes: list[ast.AST]) -> str:
    return _hash("\0".join(_dump(node) for node in nodes))


def _location(node: ast.AST) -> dict:
    return {"line": getattr(node, "lineno", 1), "column": getattr(node, "col_offset", 0),
            "end_line": getattr(node, "end_lineno", getattr(node, "lineno", 1)),
            "end_column": getattr(node, "end_col_offset", 0)}


def _own_nodes(node: ast.AST):
    """Visit a lexical body without conflating nested functions or classes."""
    pending = list(reversed(list(ast.iter_child_nodes(node))))
    while pending:
        item = pending.pop()
        if isinstance(item, _SCOPES):
            yield item
            continue
        yield item
        pending.extend(reversed(list(ast.iter_child_nodes(item))))


def _target_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        return set().union(*(_target_names(child) for child in node.elts))
    return set()


def _bindings(node: ast.AST) -> set[str]:
    names = set()
    for item in _own_nodes(node):
        if isinstance(item, ast.Name) and isinstance(item.ctx, (ast.Store, ast.Del)):
            names.add(item.id)
        elif isinstance(item, (ast.Import, ast.ImportFrom)):
            names.update(alias.asname or alias.name.split(".")[0] for alias in item.names)
        elif isinstance(item, ast.ExceptHandler) and item.name:
            names.add(item.name)
        elif isinstance(item, (ast.MatchAs, ast.MatchStar)) and item.name:
            names.add(item.name)
        elif isinstance(item, ast.MatchMapping) and item.rest:
            names.add(item.rest)
    # Definitions bind their name in the surrounding scope, despite the walk
    # deliberately excluding their implementation.
    pending = list(getattr(node, "body", []))
    while pending:
        item = pending.pop()
        if isinstance(item, (*_FUNCTIONS, ast.ClassDef)):
            names.add(item.name)
            continue
        if not isinstance(item, ast.Lambda):
            pending.extend(child for child in ast.iter_child_nodes(item) if isinstance(child, ast.stmt))
    if isinstance(node, _FUNCTIONS):
        names.update(arg.arg for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs,
                                        *([node.args.vararg] if node.args.vararg else []),
                                        *([node.args.kwarg] if node.args.kwarg else [])])
    return names


def _binding_counts(node: ast.AST) -> dict[str, int]:
    """Count lexical bind sites, including string-valued pattern/except binds."""
    counts: dict[str, int] = {}

    def add(name):
        counts[name] = counts.get(name, 0) + 1

    for item in _own_nodes(node):
        if isinstance(item, ast.Name) and isinstance(item.ctx, (ast.Store, ast.Del)):
            add(item.id)
        elif isinstance(item, (ast.Import, ast.ImportFrom)):
            for alias in item.names:
                add(alias.asname or alias.name.split(".")[0])
        elif isinstance(item, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and item.name:
            add(item.name)
        elif isinstance(item, ast.MatchMapping) and item.rest:
            add(item.rest)
    pending = list(getattr(node, "body", []))
    while pending:
        item = pending.pop()
        if isinstance(item, (*_FUNCTIONS, ast.ClassDef)):
            add(item.name)
        elif not isinstance(item, ast.Lambda):
            pending.extend(child for child in ast.iter_child_nodes(item) if isinstance(child, ast.stmt))
    return counts


def _dynamic_namespace(node: ast.AST) -> bool:
    return "*" in _bindings(node) or any(
        isinstance(item, ast.Call) and isinstance(item.func, ast.Name)
        and item.func.id in {"exec", "eval", "globals", "locals", "vars", "setattr", "delattr"}
        for item in _own_nodes(node))


class _Phase:
    def __init__(self, path: str, source: str, tree: ast.Module, phase: str):
        self.path, self.source, self.tree, self.phase = path, source, tree, phase
        self.entities: dict[str, dict] = {}
        self.states: dict[str, dict] = {}
        self.classes: dict[str, dict] = {}
        self.events: dict[tuple[str, str], list[dict]] = {}
        self.dependencies: list[dict] = []
        self.unknowns: list[dict] = []
        self.parent = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        self.module_bindings = _bindings(tree)
        self.module_binding_counts = _binding_counts(tree)
        self.module_dynamic = _dynamic_namespace(tree)
        self.ambiguous = set()
        if self.module_dynamic:
            self.unknown("wildcard imports or dynamic namespace operations leave builtin and class bindings unresolved", tree)

    def unknown(self, reason: str, node: ast.AST | None = None):
        record = {"reason": reason[:MAX_TEXT], "phase": self.phase}
        if node is not None:
            record["location"] = _location(node)
        if record in self.unknowns:
            return
        if len(self.unknowns) < MAX_UNCERTAINTIES:
            self.unknowns.append(record)
        elif self.unknowns[-1]["reason"] != "additional uncertainties exceeded the output limit":
            self.unknowns[-1] = {"reason": "additional uncertainties exceeded the output limit", "phase": self.phase}

    def text(self, node: ast.AST) -> str:
        try:
            return redact(ast.unparse(node))[:MAX_TEXT]
        except (ValueError, TypeError, RecursionError):
            return f"<{type(node).__name__}>"

    def evidence(self, node: ast.AST, role: str = "syntax") -> dict:
        # Slice lines directly: ast.get_source_segment repeatedly splits the
        # entire source and can turn a large file into quadratic work.
        location = _location(node)
        lines = self.source.splitlines(keepends=True) if not hasattr(self, "lines") else self.lines
        self.lines = lines
        selected = lines[location["line"] - 1:location["end_line"]]
        if selected:
            selected[-1] = selected[-1].encode("utf-8")[:location["end_column"]].decode("utf-8", errors="replace")
            selected[0] = selected[0].encode("utf-8")[location["column"]:].decode("utf-8", errors="replace")
        raw = "".join(selected)
        safe = redact(raw)
        return {"path": self.path, "location": location, "snippet": safe[:MAX_TEXT], "role": role,
                "content_hash": _hash(raw), "ast_hash": _hash(_dump(node)),
                "snippet_truncated": len(safe) > MAX_TEXT}

    def event(self, entity: str, category: str, node: ast.AST, *, key: ast.AST | None = None,
              semantic: ast.AST | None = None):
        evidence = [self.evidence(node, "state_access" if category == "state_access" else "syntax")]
        if key is not None:
            evidence.insert(0, self.evidence(key, "key_definition"))
        fingerprint = _digest([semantic or node, *([key] if key is not None else [])])
        self.events.setdefault((entity, category), []).append({"hash": fingerprint, "evidence": evidence,
                                                              "key": key is not None})

    def entity(self, name: str, kind: str, nodes: list[ast.AST], context: dict):
        if name in self.entities and (kind != "state" or self.entities[name]["kind"] != "state"):
            self.ambiguous.add(name)
            self.unknown(f"repeated declaration {name} has ambiguous lexical identity", nodes[-1])
            return
        if len(self.entities) >= MAX_ENTITIES and name not in self.entities:
            self.unknown("entity output limit reached; remaining entities were not captured")
            return
        if kind == "state" and name in self.entities:
            self.entities[name]["nodes"].extend(nodes)
        else:
            self.entities[name] = {"kind": kind, "nodes": nodes, "context": context}

    def collect(self, node: ast.AST, prefix: str = "", class_name: str | None = None,
                enclosing: tuple[frozenset, ...] = ()):
        context = {"class": class_name, "enclosing": enclosing}
        for child in getattr(node, "body", []):
            if isinstance(child, (*_FUNCTIONS, ast.ClassDef)):
                name = f"{prefix}.{child.name}" if prefix else child.name
                if isinstance(child, ast.ClassDef):
                    safe = not child.decorator_list and not child.keywords and not child.bases and not _dynamic_namespace(child)
                    self.classes[name] = {"node": child, "safe": safe, "bindings": _bindings(child)}
                    if not safe:
                        self.unknown(f"class {name} has decorators, bases or metaclass syntax; attribute dispatch is unresolved", child)
                    self.entity(name, "class", [child], context)
                    self.collect(child, name, name, enclosing)
                else:
                    kind = "method" if class_name and not enclosing else "async_function" if isinstance(child, ast.AsyncFunctionDef) else "function"
                    self.entity(name, kind, [child], context)
                    self.collect(child, name, None, (*enclosing, frozenset(_bindings(child))))
            elif isinstance(child, (ast.Assign, ast.AnnAssign)) and not isinstance(node, _FUNCTIONS):
                targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                for target in targets:
                    if not isinstance(target, ast.Name):
                        self.unknown("destructured or indirect shared-state declaration is unresolved", target)
                        continue
                    name = f"{prefix}.{target.id}" if prefix else target.id
                    self.entity(name, "state", [child], context)
                    if getattr(child, "value", None) is None:
                        self.unknown(f"annotation-only declaration {name} does not establish a runtime state binding", child)
                        continue
                    safe = not class_name or self._plain_value(child.value)
                    self.states[name] = {"scope": "class" if class_name else "module", "class": class_name,
                                         "safe": safe, "node": child}
                    if not safe:
                        self.unknown(f"class state {name} may be a descriptor; attribute access is unresolved", child)
            elif not isinstance(child, (ast.Expr, ast.Pass)) and not isinstance(node, _FUNCTIONS):
                if any(isinstance(item, (*_FUNCTIONS, ast.ClassDef, ast.Assign, ast.AnnAssign)) for item in ast.walk(child)):
                    self.unknown("conditional shared-state or definition declarations are unresolved", child)

    @staticmethod
    def _plain_value(node: ast.AST) -> bool:
        return isinstance(node, (ast.Constant, ast.Dict, ast.List, ast.Tuple, ast.Set))

    def _scope(self, node: ast.AST, context: dict) -> dict:
        bindings = _bindings(node)
        globals_ = {name for item in _own_nodes(node) if isinstance(item, ast.Global) for name in item.names}
        nonlocals = {name for item in _own_nodes(node) if isinstance(item, ast.Nonlocal) for name in item.names}
        local = bindings - globals_
        receiver = None
        cls = context["class"]
        if isinstance(node, _FUNCTIONS) and cls:
            decorators = node.decorator_list
            if decorators:
                classmethod = (len(decorators) == 1 and isinstance(decorators[0], ast.Name)
                               and decorators[0].id == "classmethod" and "classmethod" not in self.module_bindings
                               and "classmethod" not in self.classes[cls]["bindings"]
                               and not any("classmethod" in bound for bound in context["enclosing"])
                               and cls not in self.ambiguous and not self.module_dynamic
                               and not _dynamic_namespace(self.classes[cls]["node"]))
                positional = [*node.args.posonlyargs, *node.args.args]
                if classmethod and positional:
                    candidate = positional[0].arg
                    if not _binding_counts(node).get(candidate) and candidate not in globals_ | nonlocals:
                        receiver = candidate
                    else:
                        self.unknown(f"classmethod receiver {candidate} is rebound; class-state resolution abstained", node)
                else:
                    self.unknown("arbitrary or shadowed decorators leave method receiver and dispatch unresolved", node)
        return {**context, "local": local, "globals": globals_, "nonlocals": nonlocals,
                "receiver": receiver, "node": node, "binding_counts": _binding_counts(node), "keys": {}}

    def resolve_state(self, node: ast.AST, scope: dict) -> str | None:
        if isinstance(node, ast.Name):
            name = node.id
            if name in scope["nonlocals"]:
                self.unknown(f"nonlocal state {name} requires enclosing-scope resolution", node)
                return None
            if name in scope["local"] and isinstance(scope["node"], _FUNCTIONS):
                return None
            if name not in scope["globals"] and any(name in bound for bound in scope["enclosing"]):
                return None
            qualified = f"{scope['class']}.{name}" if scope["class"] and not isinstance(scope["node"], _FUNCTIONS) else name
            if qualified in self.states:
                return qualified
            return name if name in self.states and self.states[name]["scope"] == "module" else None
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            root = node.value.id
            cls = scope["class"] if root == scope["receiver"] else root
            candidate = f"{cls}.{node.attr}"
            if candidate not in self.states:
                return None
            safe_root = (root == scope["receiver"] or
                         (root not in scope["local"] and not any(root in bound for bound in scope["enclosing"])
                          and cls in self.classes and "." not in cls and cls not in self.ambiguous
                          and self.module_binding_counts.get(root) == 1 and not self.module_dynamic))
            if safe_root and self.classes[cls]["safe"] and self.states[candidate]["safe"]:
                return candidate
            self.unknown(f"shared-state receiver or descriptor for {candidate} is unresolved", node)
        elif isinstance(node, ast.Attribute):
            if node.attr in self.state_attributes:
                self.unknown("indirect shared-state receiver uses an alias or dynamic attribute expression; identity is unresolved", node)
        if isinstance(node, ast.Attribute) and node.attr in self.state_attributes:
            self.unknown("shared-state receiver may be an alias or instance; class identity is unresolved", node)
        return None

    def key_definition(self, key: ast.AST | None, operation: ast.AST, scope: dict) -> ast.AST | None:
        if not isinstance(key, ast.Name):
            return None
        name = key.id
        if name in scope["keys"]:
            candidate = scope["keys"][name]
            return candidate if candidate is not None and (candidate.end_lineno, candidate.end_col_offset) <= (operation.lineno, operation.col_offset) else None
        body = getattr(scope["node"], "body", [])
        binding_count = scope["binding_counts"].get(name, 0)
        parameters = scope["local"] if isinstance(scope["node"], _FUNCTIONS) else set()
        candidates = [item for item in body if isinstance(item, (ast.Assign, ast.AnnAssign))
                      and getattr(item, "value", None) is not None
                      and ((isinstance(item, ast.Assign) and len(item.targets) == 1 and isinstance(item.targets[0], ast.Name) and item.targets[0].id == name)
                           or (isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) and item.target.id == name))]
        # One direct top-level assignment must be the sole binder. Parameter,
        # global/nonlocal, conditional, loop, deletion and rebinding cases abstain.
        is_parameter = isinstance(scope["node"], _FUNCTIONS) and any(
            arg.arg == name for arg in [*scope["node"].args.posonlyargs, *scope["node"].args.args,
                                        *scope["node"].args.kwonlyargs,
                                        *([scope["node"].args.vararg] if scope["node"].args.vararg else []),
                                        *([scope["node"].args.kwarg] if scope["node"].args.kwarg else [])])
        if len(candidates) == binding_count == 1 and not is_parameter and name not in scope["globals"] | scope["nonlocals"]:
            candidate = candidates[0]
            scope["keys"][name] = candidate
            if (candidate.end_lineno, candidate.end_col_offset) <= (operation.lineno, operation.col_offset):
                return candidate
        else:
            scope["keys"][name] = None
        if binding_count or name in parameters:
            self.unknown(f"key {name} lacks a sole prior straight-line lexical assignment; key construction unresolved", key)
        return None

    def dependency(self, state: str, entity: str, access: str, node: ast.AST, key: ast.AST | None, scope: dict):
        if len(self.dependencies) >= MAX_DEPENDENCIES:
            self.unknown("state dependency output limit reached; remaining accesses were not captured")
            return
        definition = self.key_definition(key, node, scope)
        item = {"state_id": f"{self.path}#{state}", "state_scope": self.states[state]["scope"],
                "consumer_id": f"{self.path}#{entity}", "access": access, "phase": self.phase,
                "location": _location(node), "expression": self.text(node),
                "expression_hash": _hash(_dump(node)), "evidence": self.evidence(node, "state_access")}
        if key is not None:
            item["key_expression"] = self.text(key)
            item["key_expression_hash"] = _hash(_dump(key))
        if definition is not None:
            item["key_definition"] = self.evidence(definition, "key_definition")
        self.dependencies.append(item)
        self.event(entity, "state_access", node, key=definition)
        if scope["receiver"] and self.states[state]["scope"] == "class":
            self.unknown("classmethod state dependency is lexical; subclass overrides and runtime dispatch remain unverified", node)

    def scan(self, entity: str, node: ast.AST, context: dict):
        scope = self._scope(node, context)
        for item in _own_nodes(node):
            if isinstance(item, ast.Lambda):
                self.unknown("lambda body semantics are outside supported lexical entity analysis", item)
            elif isinstance(item, (*_FUNCTIONS, ast.ClassDef)) and item not in getattr(node, "body", []):
                self.unknown("conditional nested definition has unresolved lexical identity", item)
            if isinstance(item, ast.Call):
                self.event(entity, "call", item)
                if isinstance(item.func, ast.Attribute):
                    state = self.resolve_state(item.func.value, scope)
                    if state:
                        access = _CACHE_METHODS.get(item.func.attr)
                        if access:
                            key = item.args[0] if item.args and item.func.attr in {"get", "setdefault", "pop", "__getitem__", "__setitem__", "__delitem__"} else None
                            self.dependency(state, entity, access, item, key, scope)
                        else:
                            self.unknown("shared-state method mutation or dynamic dispatch is unresolved", item)
                            self.dependency(state, entity, "read", item, None, scope)
                    else:
                        self.unknown("attribute call target, aliases or dynamic dispatch leave callee identity unresolved", item)
                elif not isinstance(item.func, ast.Name):
                    self.unknown("call target uses dynamic dispatch; callee identity is unresolved", item)
            elif isinstance(item, (ast.Return, ast.Yield, ast.YieldFrom)):
                self.event(entity, "return", item)
            elif isinstance(item, (ast.If, ast.While, ast.IfExp, ast.Assert)):
                self.event(entity, "branch", item.test)
            elif isinstance(item, ast.comprehension):
                for condition in item.ifs:
                    self.event(entity, "branch", condition)
            elif isinstance(item, ast.Match):
                self.event(entity, "branch", item.subject)
                for case in item.cases:
                    self.event(entity, "branch", case.pattern)
                    if case.guard:
                        self.event(entity, "branch", case.guard)
            elif isinstance(item, (ast.Raise, ast.ExceptHandler)):
                self.event(entity, "exception", item)
            elif isinstance(item, (ast.Try, ast.TryStar)):
                shape = ast.Tuple(elts=[ast.Constant(len(item.handlers)), ast.Module(body=item.orelse, type_ignores=[]),
                                        ast.Module(body=item.finalbody, type_ignores=[])], ctx=ast.Load())
                self.event(entity, "exception", item, semantic=shape)
            if isinstance(item, ast.Subscript):
                state = self.resolve_state(item.value, scope)
                if state:
                    access = "cache_set" if isinstance(item.ctx, ast.Store) else "cache_delete" if isinstance(item.ctx, ast.Del) else "cache_get"
                    operation = self.parent.get(item, item) if isinstance(item.ctx, (ast.Store, ast.Del)) else item
                    self.dependency(state, entity, access, operation, item.slice, scope)
            elif isinstance(item, (ast.Name, ast.Attribute)):
                state = self.resolve_state(item, scope)
                if state:
                    parent = self.parent.get(item)
                    if isinstance(parent, ast.Subscript) and parent.value is item:
                        continue
                    if isinstance(parent, ast.Attribute) and parent.value is item and isinstance(self.parent.get(parent), ast.Call):
                        continue
                    # Direct declarations already have their own evidence.
                    if isinstance(item.ctx, ast.Store) and not isinstance(node, _FUNCTIONS) and isinstance(parent, (ast.Assign, ast.AnnAssign)):
                        continue
                    access = "write" if isinstance(item.ctx, ast.Store) else "delete" if isinstance(item.ctx, ast.Del) else "read"
                    operation = parent if access != "read" and isinstance(parent, ast.stmt) else item
                    self.dependency(state, entity, access, operation, None, scope)
                elif isinstance(item, ast.Attribute) and isinstance(item.value, ast.Name) and item.value.id == "cls" and scope["receiver"] != "cls":
                    self.unknown("ordinary cls parameter does not establish a class-state receiver", item)

    def run(self):
        self.collect(self.tree)
        for name in self.ambiguous:
            self.entities.pop(name, None)
            self.states.pop(name, None)
        self.state_attributes = {name.rsplit(".", 1)[-1] for name, state in self.states.items() if state["scope"] == "class"}
        for name, entry in self.entities.items():
            if entry["kind"] == "state":
                for node in entry["nodes"]:
                    self.event(name, "state_declaration", node)
            else:
                self.scan(name, entry["nodes"][0], entry["context"])
        self.scan("<module>", self.tree, {"class": None, "enclosing": ()})
        return self


def _parse(path: str, source: str | None, phase: str) -> tuple[_Phase | None, list[dict]]:
    if source is None:
        source = ""
    try:
        if not isinstance(source, str) or len(source) > MAX_SOURCE_BYTES or len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
            return None, [{"phase": phase, "reason": "Python source exceeds the 2 MiB analysis limit or is not text"}]
        tree = ast.parse(source, filename=path)
        pending = [(tree, 0)]
        count = 0
        while pending:
            node, depth = pending.pop()
            count += 1
            if count > MAX_AST_NODES or depth > MAX_AST_DEPTH:
                return None, [{"phase": phase, "reason": "Python AST node or depth analysis limit reached"}]
            pending.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
        analysis = _Phase(path, source, tree, phase).run()
        return analysis, analysis.unknowns
    except (SyntaxError, ValueError, TypeError, RecursionError, MemoryError, UnicodeError) as exc:
        return None, [{"phase": phase, "reason": f"Python static analysis could not complete: {type(exc).__name__}"}]


def analyze_python_change(path: str, before: str | None, after: str | None) -> dict:
    """Compare bounded source ASTs and return hash-bound static change evidence."""
    result = {"modified_entities": [], "findings": [], "state_dependencies": [], "uncertainties": [],
              "analysis_scope": _NOTICE, "status": "complete"}
    if not isinstance(path, str) or len(path) > 4096 or PurePosixPath(path).suffix not in {".py", ".pyi"}:
        result["status"] = "unsupported"
        result["uncertainties"] = [{"reason": "Python change analysis supports bounded .py and .pyi paths"}]
        return result
    old, old_unknowns = _parse(path, before, "before")
    new, new_unknowns = _parse(path, after, "after")
    result["uncertainties"] = [*old_unknowns, *new_unknowns]
    if old is None or new is None:
        result["status"] = "partial"
        return result
    for name in sorted(old.entities.keys() | new.entities.keys()):
        previous, current = old.entities.get(name), new.entities.get(name)
        previous_hash = _digest(previous["nodes"]) if previous else None
        current_hash = _digest(current["nodes"]) if current else None
        if previous_hash == current_hash:
            continue
        result["modified_entities"].append({
            "id": f"{path}#{name}", "path": path, "qualified_name": name,
            "kind": (current or previous)["kind"],
            "change": "modified" if previous and current else "added" if current else "deleted",
            "before_location": _location(previous["nodes"][0]) if previous else None,
            "after_location": _location(current["nodes"][0]) if current else None,
            "before_ast_hash": previous_hash, "after_ast_hash": current_hash,
        })
    effects = {
        "call": "Inferred: call targets or supplied arguments may change downstream behavior.",
        "return": "Inferred: returned or yielded values may change what consumers receive.",
        "branch": "Inferred: changed conditions may select different execution paths.",
        "exception": "Inferred: raised, caught, or cleanup behavior may change failure handling.",
        "state_declaration": "Inferred: changed shared-state initialization may affect lexical readers or writers.",
        "state_access": "Inferred: changed shared-state access or key construction may alter cache identity, reads, or writes.",
    }
    for entity, category in sorted(old.events.keys() | new.events.keys()):
        previous, current = old.events.get((entity, category), []), new.events.get((entity, category), [])
        previous_hash = _hash("\0".join(item["hash"] for item in previous))
        current_hash = _hash("\0".join(item["hash"] for item in current))
        if previous_hash == current_hash:
            continue
        if len(result["findings"]) >= MAX_FINDINGS:
            result["uncertainties"].append({"reason": "finding output limit reached; remaining changes were not captured"})
            break
        before_evidence = [evidence for item in previous for evidence in item["evidence"]]
        after_evidence = [evidence for item in current for evidence in item["evidence"]]
        uncertainty = ["Static syntax does not establish a runtime effect or regression."]
        if category == "call":
            uncertainty.append("Callee identity, aliases, decorators and dynamic dispatch are not resolved by this analyzer.")
        if category.startswith("state"):
            uncertainty.append("Lexical state candidates do not prove runtime object identity, descriptor behavior or subclass dispatch.")
        key_changed = category == "state_access" and any(item["key"] for item in [*previous, *current])
        observation = "shared-state access or lexical key construction changed" if key_changed else f"{category.replace('_', ' ')} syntax changed"
        result["findings"].append({
            "id": f"{path}#{entity}:{category}:{_hash(previous_hash + current_hash)[:16]}",
            "category": category, "entity_id": f"{path}#{entity}", "observation": observation,
            "before": before_evidence, "after": after_evidence,
            "before_ast_hash": previous_hash, "after_ast_hash": current_hash,
            "evidence_truncated": False, "potential_effect": effects[category],
            "verification": "Inspect the bound before/after source and verify affected behavior with an authorized check; no candidate code was executed.",
            "uncertainties": uncertainty,
        })
    result["state_dependencies"] = sorted([*old.dependencies, *new.dependencies],
                                           key=lambda item: (item["state_id"], item["consumer_id"], item["phase"],
                                                             item["location"]["line"], item["location"]["column"], item["access"]))
    if result["uncertainties"]:
        result["status"] = "partial"
    return result
