"""Bounded Python public-surface evidence, without importing project code.

``complete`` means this adapter covered the supported *static declarations* in
the supplied file. It does not establish a runtime API or behavioral contract.
Every entry requires verification, even when ``__all__`` declares its name.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
from pathlib import PurePosixPath

from .outbound import MARKER, redact

MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_AST_NODES = 50_000
MAX_ENTRIES = 2_000
MAX_FACET_ITEMS = 100
MAX_TEXT = 1_000
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCOPES = (*_FUNCTIONS, ast.ClassDef, ast.Lambda)
_BEHAVIOR_NOTICE = "static syntax hints; runtime behavior requires verification"
_CREDENTIAL_NAME = re.compile(r"(?:api[_-]?key|password|passwd|secret|(?:^|[_-])(?:token|auth[_-]?token|access[_-]?token)(?:$|[_-]))", re.I)


def _dump(node: ast.AST | None) -> str:
    return ast.dump(node, include_attributes=False) if node is not None else "None"


def _fingerprint(parts: list[str]) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def _text(node: ast.AST | None) -> str:
    if node is None:
        return "None"
    try:
        return redact(ast.unparse(node))[:MAX_TEXT]
    except (ValueError, TypeError, RecursionError):
        return f"<{type(node).__name__}>"


def _dot_name(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dot_name(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _location(node: ast.AST) -> dict:
    return {"line": getattr(node, "lineno", 1), "end_line": getattr(node, "end_lineno", 1)}


def _names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.List, ast.Tuple)):
        return [name for child in node.elts for name in _names(child)]
    return []


def _scope_nodes(node: ast.AST):
    """Walk one lexical scope, excluding nested definitions and lambdas."""
    pending = list(reversed(list(ast.iter_child_nodes(node))))
    while pending:
        child = pending.pop()
        if isinstance(child, _SCOPES):
            continue
        yield child
        pending.extend(reversed(list(ast.iter_child_nodes(child))))


def _literal_names(node: ast.AST | None) -> set[str] | None:
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return None
    if not all(isinstance(item, ast.Constant) and isinstance(item.value, str) and item.value for item in node.elts):
        return None
    return {item.value for item in node.elts}


def _module_exports(tree: ast.Module) -> tuple[set[str] | None, str | None]:
    exports = None
    for node in tree.body:
        if isinstance(node, _SCOPES):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, (ast.AnnAssign, ast.AugAssign)) else []
        if any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
            names = _literal_names(getattr(node, "value", None))
            if names is None:
                return None, "module __all__ is dynamically constructed"
            if isinstance(node, ast.AugAssign):
                if exports is None or not isinstance(node.op, ast.Add):
                    return None, "module __all__ has an unsupported modification"
                exports.update(names)
            else:
                exports = names
            continue
        if any(isinstance(child, ast.Name) and child.id == "__all__" for child in _scope_nodes(node)):
            return None, "module __all__ is referenced or modified through unsupported syntax"
        if isinstance(node, ast.ImportFrom) and any(alias.name == "__all__" or alias.asname == "__all__" for alias in node.names):
            return None, "module __all__ is imported dynamically"
        if isinstance(node, ast.Import) and any(alias.asname == "__all__" for alias in node.names):
            return None, "module __all__ is imported dynamically"
    return exports, None


def _is_protocol(name: str) -> bool:
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


def _is_generator(node: ast.AST) -> bool:
    if not isinstance(node, _FUNCTIONS):
        return False
    body = ast.Module(body=node.body, type_ignores=[])
    return any(isinstance(child, (ast.Yield, ast.YieldFrom)) for child in _scope_nodes(body))


def _signature(node: ast.AST) -> tuple[str, str]:
    if isinstance(node, _FUNCTIONS):
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        returns = f" -> {_text(node.returns)}" if node.returns is not None else ""
        safe_args = copy.deepcopy(node.args)
        positional = [*safe_args.posonlyargs, *safe_args.args]
        for index, arg in enumerate(positional[len(positional) - len(safe_args.defaults):]):
            if _CREDENTIAL_NAME.search(arg.arg):
                safe_args.defaults[index] = ast.Constant(MARKER)
        for index, arg in enumerate(safe_args.kwonlyargs):
            if _CREDENTIAL_NAME.search(arg.arg) and safe_args.kw_defaults[index] is not None:
                safe_args.kw_defaults[index] = ast.Constant(MARKER)
        display = f"{prefix} {node.name}({_text(safe_args)}){returns}"
        parts = [type(node).__name__, f"generator:{_is_generator(node)}", _dump(node.args), _dump(node.returns)]
    elif isinstance(node, ast.ClassDef):
        bases = [*map(_text, node.bases), *map(_text, node.keywords)]
        display = f"class {node.name}" + (f"({', '.join(bases)})" if bases else "")
        parts = [*map(_dump, node.bases), *map(_dump, node.keywords)]
    else:
        return _text(node), _fingerprint([_dump(node)])
    parts.extend(map(_dump, node.decorator_list))
    parts.extend(map(_dump, getattr(node, "type_params", [])))
    return display[:MAX_TEXT], _fingerprint(parts)


def _body(node: ast.AST) -> str:
    if isinstance(node, ast.ClassDef):
        statements = [child for child in node.body if not isinstance(child, (*_FUNCTIONS, ast.ClassDef))]
    elif isinstance(node, _FUNCTIONS):
        statements = node.body
    else:
        return _fingerprint([_dump(node)])
    # Documentation comments/docstrings are handled separately from behavior.
    if statements and isinstance(statements[0], ast.Expr) and isinstance(statements[0].value, ast.Constant) and isinstance(statements[0].value.value, str):
        statements = statements[1:]
    return _fingerprint([_dump(child) for child in statements])


def _return_shape(node: ast.AST | None) -> str:
    if node is None:
        return "none"
    if isinstance(node, ast.Constant):
        return f"literal:{type(node.value).__name__}"
    if isinstance(node, ast.Call):
        return f"call:{_dot_name(node.func) or 'dynamic'}"
    if isinstance(node, ast.Name):
        return f"name:{node.id}"
    return type(node).__name__.lower()


class _Analysis:
    def __init__(self, tree: ast.Module, path: str):
        self.tree = tree
        self.path = path
        self.entries: dict[str, dict] = {}
        self.unknowns: list[dict] = []
        self.exports, error = _module_exports(tree)
        self.dynamic_exports = error is not None
        if error:
            self.unknown(error)
        self.bindings: dict[str, list[tuple[int, str | None]]] = {}
        self.records: dict[str, list[tuple[str, ast.AST, str | None]]] = {}
        self.output_bytes = 0
        self.output_full = False
        self._signatures: dict[ast.AST, tuple[str, str]] = {}
        self._bodies: dict[ast.AST, str] = {}
        self._facets: dict[ast.AST, dict] = {}

    def signature(self, node: ast.AST) -> tuple[str, str]:
        if node not in self._signatures:
            self._signatures[node] = _signature(node)
        return self._signatures[node]

    def body(self, node: ast.AST) -> str:
        if node not in self._bodies:
            self._bodies[node] = _body(node)
        return self._bodies[node]

    def unknown(self, reason: str, node: ast.AST | None = None):
        if len(self.unknowns) < MAX_FACET_ITEMS:
            item = {"path": self.path, "reason": reason[:2_000]}
            if node is not None:
                item["location"] = _location(node)
            if item not in self.unknowns:
                self.unknowns.append(item)

    def resolve(self, node: ast.AST, line: int) -> str | None:
        name = _dot_name(node)
        if name is None:
            return None
        root, _, tail = name.partition(".")
        history = [item for item in self.bindings.get(root, []) if item[0] < line]
        if not history or history[-1][1] is None:
            return None
        return history[-1][1] + (f".{tail}" if tail else "")

    def public(self, name: str, *, method=False) -> str | None:
        if method:
            return "convention" if not name.startswith("_") or _is_protocol(name) else None
        if name in {"__getattr__", "__dir__"}:
            return "convention"
        if self.dynamic_exports:
            return "unknown" if not name.startswith("_") else None
        if self.exports is not None:
            return "declared" if name in self.exports else None
        return "convention" if not name.startswith("_") else None

    def record(self, name: str, kind: str, node: ast.AST, target: str | None = None):
        self.records.setdefault(name, []).append((kind, node, target))
        self.bindings.setdefault(name, []).append((getattr(node, "lineno", 0), target if kind == "reexport" else None))

    def scan_module(self):
        for node in self.tree.body:
            if isinstance(node, _FUNCTIONS):
                self.record(node.name, "function", node)
            elif isinstance(node, ast.ClassDef):
                self.record(node.name, "class", node)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    self.record(alias.asname or alias.name.split(".")[0], "reexport", node, alias.name if alias.asname else alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "*":
                        self.unknown("wildcard import makes public exports unknown", node)
                    else:
                        module = "." * node.level + (node.module or "")
                        target = f"{module}.{alias.name}" if module and not module.endswith(".") else f"{module}{alias.name}"
                        self.record(alias.asname or alias.name, "reexport", node, target)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        if target.id != "__all__":
                            self.record(target.id, "binding", node)
                    elif _names(target):
                        self.unknown("destructured module binding needs export review", node)
            elif isinstance(node, ast.Delete):
                for target in node.targets:
                    for name in _names(target):
                        self.records.pop(name, None)
                        self.bindings.setdefault(name, []).append((node.lineno, None))
            elif isinstance(node, ast.AugAssign):
                if isinstance(node.target, ast.Name) and node.target.id != "__all__":
                    self.unknown(f"module binding {node.target.id} is modified indirectly", node)
            elif not isinstance(node, (ast.Expr, ast.Pass)):
                # Conditional definitions/imports/assignments cannot prove a
                # single public surface without running the surrounding code.
                if any(isinstance(child, (*_FUNCTIONS, ast.ClassDef, ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign)) for child in ast.walk(node)):
                    self.unknown("conditional module declarations need export review", node)
            if not isinstance(node, _SCOPES) and any(isinstance(child, ast.Call) and _dot_name(child.func) in {"exec", "eval", "globals", "locals"} for child in _scope_nodes(node)):
                self.unknown("dynamic module namespace construction needs export review", node)

    def emit(self, name: str, kind: str, node: ast.AST, visibility: str, *, signature: str | None = None, signature_hash: str | None = None, body_hash: str | None = None, facets: dict | None = None):
        if self.output_full:
            return
        if len(self.entries) >= MAX_ENTRIES:
            self.unknown("public API entry limit reached; remaining declarations were not captured")
            self.output_full = True
            return
        display, digest = self.signature(node)
        entry = {
            "id": f"{self.path}#{name}", "qualified_name": name, "kind": kind,
            "visibility": visibility, "signature": signature if signature is not None else display,
            "signature_fingerprint": signature_hash or digest, "body_fingerprint": body_hash or self.body(node),
            "facets": facets or {}, "provenance": "parser", "confidence": "requires_verification",
            "location": _location(node),
        }
        size = len(json.dumps(entry, ensure_ascii=False).encode("utf-8"))
        if self.output_bytes + size > MAX_MANIFEST_BYTES:
            self.unknown("public API output limit reached; remaining declarations were not captured")
            self.output_full = True
            return
        self.output_bytes += size
        self.entries[name] = entry

    def overload(self, node: ast.AST) -> bool:
        return isinstance(node, _FUNCTIONS) and any(
            self.resolve(decorator.func if isinstance(decorator, ast.Call) else decorator, node.lineno) in {"typing.overload", "typing_extensions.overload"}
            for decorator in node.decorator_list
        )

    def cli_hint(self, call: ast.Call) -> dict | None:
        raw = _dot_name(call.func) or ""
        resolved = self.resolve(call.func, call.lineno)
        if raw.endswith(".add_argument"):
            api, confidence = "argparse-style add_argument", "lexical_receiver_unresolved"
        elif resolved in {"click.option", "typer.Option", "typer.Argument"}:
            api, confidence = resolved, "import_binding_requires_verification"
        else:
            return None
        hint = {"api": api, "options": [arg.value for arg in call.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.startswith("-")], "confidence": confidence, "location": _location(call)}
        hint["dynamic"] = any(not isinstance(arg, ast.Constant) for arg in call.args) or any(keyword.arg is None for keyword in call.keywords)
        for keyword in call.keywords:
            if keyword.arg in {"default", "required", "type", "choices", "help", "dest", "nargs", "action"}:
                hint[keyword.arg] = _text(keyword.value)
        if resolved in {"typer.Option", "typer.Argument"} and call.args:
            hint["default"] = _text(call.args[0])
        return hint

    def function_facets(self, node: ast.AST) -> dict:
        if node not in self._facets:
            self._facets[node] = self._function_facets(node)
        return self._facets[node].copy()

    def _function_facets(self, node: ast.AST) -> dict:
        defaults, annotations = {}, {}
        if isinstance(node, _FUNCTIONS):
            positional = [*node.args.posonlyargs, *node.args.args]
            for arg, default in zip(positional[len(positional) - len(node.args.defaults):], node.args.defaults):
                defaults[arg.arg] = MARKER if _CREDENTIAL_NAME.search(arg.arg) else _text(default)
            for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
                if default is not None:
                    defaults[arg.arg] = MARKER if _CREDENTIAL_NAME.search(arg.arg) else _text(default)
            for arg in [*positional, *node.args.kwonlyargs, *([node.args.vararg] if node.args.vararg else []), *([node.args.kwarg] if node.args.kwarg else [])]:
                if arg.annotation is not None:
                    annotations[arg.arg] = _text(arg.annotation)
            if node.returns is not None:
                annotations["return"] = _text(node.returns)
        decorators = [_text(item) for item in getattr(node, "decorator_list", [])]
        deprecations = [item for item in decorators if "deprecated" in item.lower()]
        exceptions, returns, cli_options = [], [], []
        for child in _scope_nodes(node):
            if isinstance(child, ast.Raise) and child.exc is not None:
                target = child.exc.func if isinstance(child.exc, ast.Call) else child.exc
                exceptions.append(_dot_name(target) or _return_shape(child.exc))
            elif isinstance(child, (ast.Return, ast.Yield, ast.YieldFrom)):
                shape = _return_shape(child.value)
                returns.append(("yield:" if not isinstance(child, ast.Return) else "") + shape)
            elif isinstance(child, ast.Call):
                hint = self.cli_hint(child)
                if hint:
                    cli_options.append(hint)
                if (_dot_name(child.func) or "").endswith("warn") and any((_dot_name(arg) or "").endswith(("DeprecationWarning", "FutureWarning")) for arg in [*child.args, *(keyword.value for keyword in child.keywords)]):
                    deprecations.append(_text(child))
        counts = {"defaults": len(defaults), "annotations": len(annotations), "decorators": len(decorators), "deprecations": len(deprecations), "exceptions": len(exceptions), "returns": len(returns), "cli_options": len(cli_options)}
        if any(count > MAX_FACET_ITEMS for count in counts.values()):
            self.unknown("API facet item limit reached; summaries were truncated", node)
        return {
            "async": isinstance(node, ast.AsyncFunctionDef), "generator": _is_generator(node),
            "defaults": dict(list(defaults.items())[:MAX_FACET_ITEMS]), "annotations": dict(list(annotations.items())[:MAX_FACET_ITEMS]),
            "decorators": decorators[:MAX_FACET_ITEMS], "deprecations": list(dict.fromkeys(deprecations))[:MAX_FACET_ITEMS],
            "exceptions": sorted(set(exceptions))[:MAX_FACET_ITEMS], "returns": sorted(set(returns))[:MAX_FACET_ITEMS],
            "cli_options": cli_options[:MAX_FACET_ITEMS], "behavior_evidence": _BEHAVIOR_NOTICE,
        }

    def function_group(self, name: str, nodes: list[ast.AST], visibility: str, kind="function"):
        overloads = [node for node in nodes if self.overload(node)]
        implementations = [node for node in nodes if not self.overload(node)]
        if len(implementations) > 1 or (len(nodes) > 1 and not overloads):
            self.unknown(f"ambiguous repeated public declaration: {name}", nodes[-1])
            return
        if implementations and overloads and implementations[0].lineno < max(node.lineno for node in overloads):
            self.unknown(f"overload order for {name} needs runtime review", nodes[-1])
            return
        node = implementations[0] if implementations else nodes[-1]
        facets = self.function_facets(node)
        facets["overloads"] = [self.signature(item)[0] for item in overloads][:MAX_FACET_ITEMS]
        if len(overloads) > MAX_FACET_ITEMS:
            self.unknown(f"overload summary limit reached: {name}", node)
        signature, signature_hash = self.signature(node)
        if overloads:
            signature_hash = _fingerprint([signature_hash, *[self.signature(item)[1] for item in overloads]])
        self.emit(name, kind, node, visibility, signature=signature, signature_hash=signature_hash, facets=facets)

    def class_entry(self, name: str, node: ast.ClassDef, visibility: str, *, alias_target: str | None = None):
        facets = {"bases": [_text(base) for base in node.bases][:MAX_FACET_ITEMS], "decorators": [_text(item) for item in node.decorator_list][:MAX_FACET_ITEMS]}
        if alias_target:
            facets["aliases"] = [alias_target]
        self.emit(name, "class", node, visibility, facets=facets)
        records: dict[str, list[ast.AST]] = {}
        for child in node.body:
            if isinstance(child, _FUNCTIONS) and self.public(child.name, method=True):
                records.setdefault(child.name, []).append(child)
            elif isinstance(child, ast.ClassDef) and self.public(child.name, method=True):
                records.setdefault(child.name, []).append(child)
            elif isinstance(child, (ast.Assign, ast.AnnAssign)):
                targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                for target in targets:
                    if isinstance(target, ast.Name) and self.public(target.id, method=True):
                        records.setdefault(target.id, []).append(child)
            elif not isinstance(child, (*_SCOPES, ast.Expr, ast.Pass)) and any(isinstance(item, (*_FUNCTIONS, ast.ClassDef, ast.Assign, ast.AnnAssign)) for item in _scope_nodes(child)):
                self.unknown(f"conditional class declarations need export review: {name}", child)
        for child_name, group in records.items():
            final = group[-1]
            qualified = f"{name}.{child_name}"
            if isinstance(final, _FUNCTIONS):
                if all(isinstance(item, _FUNCTIONS) for item in group):
                    self.function_group(qualified, group, "convention", "method")
                else:
                    self.unknown(f"public class method {qualified} has mixed rebinding forms", final)
            elif isinstance(final, ast.ClassDef):
                if len(group) > 1:
                    self.unknown(f"ambiguous repeated public class: {qualified}", final)
                else:
                    self.class_entry(qualified, final, "convention")
            else:
                target = _dot_name(getattr(final, "value", None))
                if target in records:
                    self.unknown(f"class attribute alias {qualified} needs class-scope binding review", final)
                self.binding_entry(qualified, final, "convention")

    def alias_target(self, value: ast.AST, line: int, depth: int = 0) -> tuple[str, ast.AST | None, str | None] | None:
        """Follow earlier, single direct local bindings; never infer execution."""
        target = _dot_name(value)
        if not target or depth >= 16:
            return None
        if "." in target:
            imported = self.resolve(value, line)
            return ("reexport", None, imported) if imported else None
        records = self.records.get(target, [])
        if len(records) != 1 or getattr(records[0][1], "lineno", 0) >= line:
            return None
        kind, original, imported = records[0]
        if kind in {"function", "class", "reexport"}:
            return kind, original, imported
        child_value = getattr(original, "value", None)
        if child_value is not None and _dot_name(child_value):
            return self.alias_target(child_value, original.lineno, depth + 1)
        return ("binding", original, None) if child_value is not None else None

    def binding_entry(self, name: str, node: ast.AST, visibility: str):
        value = getattr(node, "value", None)
        target = _dot_name(value)
        display_value = MARKER if _CREDENTIAL_NAME.search(name) else _text(value)
        facets = {"value": display_value, "annotations": {"binding": _text(node.annotation)} if isinstance(node, ast.AnnAssign) else {}}
        if isinstance(value, ast.Dict):
            keys = [key.value for key in value.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)]
            facets["config_keys"] = keys[:MAX_FACET_ITEMS]
            facets["config_defaults"] = {key.value: MARKER if _CREDENTIAL_NAME.search(key.value) else _text(item) for key, item in zip(value.keys[:MAX_FACET_ITEMS], value.values[:MAX_FACET_ITEMS]) if isinstance(key, ast.Constant) and isinstance(key.value, str)}
            facets["dynamic_config_keys"] = any(not isinstance(key, ast.Constant) or not isinstance(key.value, str) for key in value.keys)
            if len(keys) > MAX_FACET_ITEMS:
                self.unknown(f"configuration summary limit reached: {name}", node)
        if target:
            facets["aliases"] = [target]
            # Resolve only single direct local definitions. Import identities
            # are captured separately; other rebinding forms remain hints.
            effective = self.alias_target(value, node.lineno)
            if effective and effective[0] == "function":
                original = effective[1]
                signature, signature_hash = self.signature(original)
                target_facets = self.function_facets(original)
                target_facets.update(facets)
                self.emit(name, "alias", node, visibility, signature=signature, signature_hash=_fingerprint([signature_hash, target]), body_hash=self.body(original), facets=target_facets)
                return
            if effective and effective[0] == "class":
                self.class_entry(name, effective[1], visibility, alias_target=target)
                if name in self.entries:
                    self.entries[name]["location"] = _location(node)
                return
            if effective and effective[0] == "reexport":
                facets["reexports"] = [effective[2]]
                self.emit(name, "alias", node, visibility, signature=f"{name} -> {effective[2]}", signature_hash=_fingerprint([target, effective[2] or ""]), body_hash=_fingerprint([]), facets=facets)
                return
            if effective and effective[0] == "binding":
                original_value = getattr(effective[1], "value", None)
                facets["target_value"] = MARKER if _CREDENTIAL_NAME.search(target) or _CREDENTIAL_NAME.search(name) else _text(original_value)
                self.emit(name, "alias", node, visibility, signature=f"{name} = {target}", signature_hash=_fingerprint([target, _dump(original_value), _dump(getattr(node, "annotation", None))]), body_hash=_fingerprint([]), facets=facets)
                return
            self.unknown(f"alias target {target} is unresolved, later, or rebound", node)
        # Initializer changes belong to the declared surface; they are not
        # separately claimed as a runtime behavior change.
        self.emit(name, "alias" if target else "constant" if name.rsplit(".", 1)[-1].isupper() else "binding", node, visibility, signature=f"{name} = {display_value}"[:MAX_TEXT], signature_hash=_fingerprint([_dump(value), _dump(getattr(node, "annotation", None))]), body_hash=_fingerprint([]), facets=facets)

    def run(self) -> dict:
        self.scan_module()
        for name, records in self.records.items():
            if self.output_full:
                break
            visibility = self.public(name)
            if visibility is None:
                continue
            kind, node, target = records[-1]
            if kind == "function":
                if all(item[0] == "function" for item in records):
                    self.function_group(name, [item[1] for item in records], visibility)
                else:
                    self.unknown(f"public function {name} has mixed rebinding forms", node)
            elif kind == "class":
                if len(records) == 1:
                    self.class_entry(name, node, visibility)
                else:
                    self.unknown(f"ambiguous repeated public class: {name}", node)
            elif kind == "reexport":
                imported_modules = [alias.name for alias in node.names if (alias.asname or alias.name.split(".")[0]) == name] if isinstance(node, ast.Import) else [target]
                self.emit(name, kind, node, visibility, signature=f"{name} -> {target}", signature_hash=_fingerprint([target or "", *imported_modules]), body_hash=_fingerprint([]), facets={"reexports": [target], "aliases": [name], "imported_modules": imported_modules})
            else:
                self.binding_entry(name, node, visibility)
            if name in {"__getattr__", "__dir__"}:
                self.unknown(f"module {name} may provide a dynamic public surface", node)
        if self.exports is not None:
            for name in sorted(self.exports - self.records.keys()):
                self.unknown(f"declared export {name} has no unambiguous direct binding")
        return {"language": "python", "status": "partial" if self.unknowns else "complete", "entries": list(self.entries.values()), "unknowns": self.unknowns, "analysis_scope": "bounded static declarations; imports, decorators and project code were not executed"}


def analyze(source: str, path: str) -> dict:
    """Describe supported Python declarations; malformed or bounded input is explicit."""
    result = {"language": "python", "status": "partial", "entries": [], "unknowns": []}
    if not isinstance(path, str) or PurePosixPath(path).suffix not in {".py", ".pyi"}:
        result["status"] = "unsupported"
        result["unknowns"] = [{"path": str(path), "reason": "Python adapter supports .py and .pyi source"}]
        return result
    try:
        if len(path) > 4_096:
            result["unknowns"] = [{"path": path[:4_096], "reason": "Python source path exceeds the analysis limit"}]
            return result
        if not isinstance(source, str) or len(source) > MAX_SOURCE_BYTES or len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
            result["unknowns"] = [{"path": path, "reason": "Python source exceeds the 2 MiB analysis limit or is not text"}]
            return result
        tree = ast.parse(source, filename=path)
        for index, _ in enumerate(ast.walk(tree)):
            if index >= MAX_AST_NODES:
                result["unknowns"] = [{"path": path, "reason": "Python AST node limit reached"}]
                return result
        return _Analysis(tree, path).run()
    except (SyntaxError, ValueError, TypeError, RecursionError, MemoryError, UnicodeError) as exc:
        result["unknowns"] = [{"path": path, "reason": f"Python static analysis could not complete: {type(exc).__name__}"}]
        return result
