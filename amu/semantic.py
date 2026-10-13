"""Shared source identities; inferred dispatch is distinct from exact source identity."""

from __future__ import annotations

import ast
import builtins
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .change_analysis import _bindings, _dynamic_namespace, _location

VERSION = "python-value-flow-v4"
MAX_FUNCTIONS = 1024
MAX_TEST_OBSERVERS = 512
MAX_IMPORT_HOPS = 8
MAX_UNKNOWN = 200
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
EXCEPTIONS = frozenset(
    name for name, obj in vars(builtins).items() if isinstance(obj, type) and issubclass(obj, BaseException)
)


def overload_nodes(tree):
    """Typing-only overload stubs of one parsed module.

    The answer depends only on the tree, so it is computed once per tree and
    kept on it. Callers previously repeated two full walks per declaration.
    """
    cached = getattr(tree, "_amu_overload_nodes", None)
    if cached is None:
        cached = tree._amu_overload_nodes = frozenset(_overload_nodes(tree))
    return cached


def _overload_nodes(tree):
    aliases = {
        a.asname or a.name
        for n in tree.body
        if isinstance(n, ast.ImportFrom) and n.module in {"typing", "typing_extensions"}
        for a in n.names
        if a.name == "overload"
    }
    modules = {
        a.asname or a.name
        for n in tree.body
        if isinstance(n, ast.Import)
        for a in n.names
        if a.name in {"typing", "typing_extensions"}
    }
    from .change_analysis import _binding_counts

    counts = _binding_counts(tree)
    aliases = {a for a in aliases if counts.get(a) == 1}
    modules = {a for a in modules if counts.get(a) == 1}
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    result = set()
    for n in ast.walk(tree):
        if not isinstance(n, FUNCTIONS) or len(n.decorator_list) != 1:
            continue
        d = n.decorator_list[0]
        root = (
            d.id
            if isinstance(d, ast.Name)
            else d.value.id
            if isinstance(d, ast.Attribute) and isinstance(d.value, ast.Name)
            else None
        )
        recognized = (
            isinstance(d, ast.Name)
            and root in aliases
            or isinstance(d, ast.Attribute)
            and d.attr == "overload"
            and root in modules
        )
        ancestor = parents.get(n)
        while ancestor is not None and ancestor is not tree:
            if isinstance(ancestor, (ast.ClassDef, *FUNCTIONS)) and root in _bindings(ancestor):
                recognized = False
            ancestor = parents.get(ancestor)
        if recognized and all(
            isinstance(x, ast.Pass)
            or isinstance(x, ast.Expr)
            and isinstance(x.value, ast.Constant)
            and x.value.value is Ellipsis
            for x in n.body
        ):
            result.add(n)
    return result


@dataclass
class Instance:
    type: "Symbol"
    allocation: str
    fields: dict = field(default_factory=dict)
    exact: bool = True
    escaped: bool = False
    provenance: list = field(default_factory=list)


@dataclass(frozen=True)
class Symbol:
    path: str
    name: str
    kind: str

    @property
    def id(self):
        return f"{self.path}#{self.name}" if self.path != "builtins" else f"builtins.{self.name}"


class SemanticIndex:
    """Resolve unique source layouts, explicit exports and stable aliases."""

    def __init__(
        self,
        sources,
        syntax_facts=None,
        *,
        parameterized=None,
        function_limit=MAX_FUNCTIONS,
        observer_limit=MAX_TEST_OBSERVERS,
        provider_candidates=(),
    ):
        self.sources = sources
        self.classes = {}
        self.dynamic_exports = set()
        self.explicit_submodules = set()
        self.members = {}
        self.owners = {}
        self.provider_candidates = tuple(provider_candidates)
        self.parameterized = parameterized or (lambda *args: False)
        self.function_limit = function_limit
        self.observer_limit = observer_limit
        self.tables = {}
        self.unknowns = []
        self._unknown_keys = set()
        self._unknown_limit_reported = False
        self._syntax_facts = syntax_facts if syntax_facts is not None else {}
        self.functions = {}
        self.test_observers = {}
        self.modules = {}
        self.steps = 0
        self.exhausted = False
        for path, source in sorted(sources.items()):
            module = path.removeprefix("src/").removesuffix(".py").removesuffix("/__init__").replace("/", ".")
            self.modules.setdefault(module, []).append(path)
            table = {}
            tree = source["tree"]
            if any(isinstance(n, FUNCTIONS) and n.name == "__getattr__" for n in tree.body):
                self.dynamic_exports.add(path)
            for n in tree.body:
                if isinstance(n, ast.Import):
                    self.explicit_submodules.update((path, a.name) for a in n.names if "." in a.name)
            dynamic = _dynamic_namespace(tree) or any(
                isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names) for n in tree.body
            )
            if path.endswith("/__init__.py"):
                definitions = {n.name: n for n in tree.body if isinstance(n, FUNCTIONS)}
                for statement in tree.body:
                    if isinstance(statement, (*FUNCTIONS, ast.ClassDef)):
                        continue
                    for call in (n for n in ast.walk(statement) if isinstance(n, ast.Call)):
                        if isinstance(call.func, ast.Name) and call.func.id not in definitions:
                            dynamic = True
                            self.dynamic_exports.add(path)
                            self.unknown(path, call, "unknown initializer factory leaves runtime exports unresolved")
            erased = overload_nodes(tree)
            for node in tree.body:
                if node in erased:
                    continue
                pairs = []
                if isinstance(node, (*FUNCTIONS, ast.ClassDef)):
                    pairs = [(node.name, ("definition", node))]
                elif isinstance(node, ast.Import):
                    pairs = [
                        (a.asname or a.name.split(".")[0], ("module", a.name if a.asname else a.name.split(".")[0]))
                        for a in node.names
                    ]
                elif isinstance(node, ast.ImportFrom):
                    module = self.relative_module(path, node.module, node.level)
                    pairs = [(a.asname or a.name, ("import", module, a.name)) for a in node.names if a.name != "*"]
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    pairs = [
                        (n.id, ("alias", node.value)) for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)
                    ]
                else:
                    pairs = [(n, ("unknown",)) for n in _bindings(ast.Module(body=[node], type_ignores=[]))]
                for name, fact in pairs:
                    table[name] = ("unknown",) if name in table or dynamic else fact
            # Attribute mutation of imported objects invalidates that root too.
            for node in tree.body:
                if isinstance(node, (*FUNCTIONS, ast.ClassDef)):
                    continue
                for use in ast.walk(node):
                    if isinstance(use, ast.Attribute) and isinstance(use.ctx, (ast.Store, ast.Del)):
                        root = use.value
                        while isinstance(root, ast.Attribute):
                            root = root.value
                        if isinstance(root, ast.Name):
                            table[root.id] = ("unknown",)
            self.tables[path] = table
        for path, table in self.tables.items():
            for name, fact in table.items():
                if fact[0] == "definition" and isinstance(fact[1], ast.FunctionDef) and not fact[1].decorator_list:
                    if len(self.functions) >= self.function_limit:
                        self.unknown(path, fact[1], "function limit reached; further functions not analyzed")
                        continue
                    self.functions[f"{path}#{name}"] = (path, name, fact[1])
                elif fact[0] == "definition" and isinstance(fact[1], FUNCTIONS) and fact[1].decorator_list:
                    if self.parameterized(fact[1], table):
                        self.add_test_observer(path, name, fact[1])
                    else:
                        self.unknown(path, fact[1], "decorated function behavior unresolved")
                elif fact[0] == "definition" and isinstance(fact[1], ast.ClassDef):
                    owner = fact[1]
                    cls = Symbol(path, owner.name, "class")
                    self.classes[cls.id] = owner
                    erased = overload_nodes(self.sources[path]["tree"])
                    counts = {}
                    for child in owner.body:
                        if isinstance(child, FUNCTIONS) and child not in erased:
                            key = self.accessor(child, table) or child.name
                            counts[key] = counts.get(key, 0) + 1
                    for child in owner.body:
                        if not isinstance(child, ast.FunctionDef) or child in erased:
                            continue
                        accessor = self.accessor(child, table)
                        key = accessor or child.name
                        if counts[key] != 1:
                            self.unknown(path, child, "repeated runtime member is ambiguous")
                            continue
                        known = not child.decorator_list or accessor or self.is_static_method(child, table)
                        if not known:
                            if (
                                owner.name.startswith("Test")
                                and not owner.decorator_list
                                and self.parameterized(child, table, _bindings(owner))
                            ):
                                self.add_test_observer(path, owner.name + "::" + child.name, child)
                            else:
                                self.unknown(
                                    path, child, "decorated member body not composed; descriptor dispatch unresolved"
                                )
                            continue
                        if len(self.functions) >= self.function_limit:
                            self.unknown(path, child, "function limit reached; further methods not analyzed")
                            if owner.name.startswith("Test") and child.name.startswith("test_"):
                                self.add_test_observer(path, owner.name + "::" + child.name, child)
                            continue
                        name = owner.name + ("::" if owner.name.startswith("Test") else ".") + key
                        ident = f"{path}#{name}"
                        self.functions[ident] = (path, name, child)
                        self.owners[ident] = cls
                        self.members[(cls.id, key)] = Symbol(
                            path,
                            name,
                            "property"
                            if accessor
                            else "static_method"
                            if self.is_static_method(child, table)
                            else "method",
                        )
        from .pytest_fixtures import FixtureIndex

        self.fixtures = FixtureIndex(self)

    @staticmethod
    def accessor(node, table):
        if len(node.decorator_list) != 1 or "property" in table:
            return None
        d = node.decorator_list[0]
        if isinstance(d, ast.Name) and d.id == "property":
            return node.name + "@getter"
        if (
            isinstance(d, ast.Attribute)
            and isinstance(d.value, ast.Name)
            and d.value.id == node.name
            and d.attr in {"setter", "deleter"}
        ):
            return node.name + "@" + d.attr
        return None

    @staticmethod
    def is_static_method(node, table):
        return (
            len(node.decorator_list) == 1
            and isinstance(node.decorator_list[0], ast.Name)
            and node.decorator_list[0].id == "staticmethod"
            and "staticmethod" not in table
        )

    def supported_hierarchy(self, cls, seen=()):
        if cls.id in seen or len(seen) >= MAX_IMPORT_HOPS:
            return False
        owner = self.classes.get(cls.id)
        if owner is None or owner.decorator_list or owner.keywords:
            return False
        if any(
            isinstance(n, FUNCTIONS)
            and n.name
            in {
                "__getattribute__",
                "__getattr__",
                "__setattr__",
                "__delattr__",
                "__new__",
                "__init_subclass__",
                "__class_getitem__",
            }
            for n in owner.body
        ):
            return False
        for base in owner.bases:
            if isinstance(base, ast.Name) and base.id == "object" and "object" not in self.tables[cls.path]:
                continue
            parent = self.expr(cls.path, base, {}, set())
            if (
                not isinstance(parent, Symbol)
                or parent.kind != "class"
                or not self.supported_hierarchy(parent, (*seen, cls.id))
            ):
                return False
        return True

    def ordinary_field(self, cls, name):
        owner = self.classes.get(cls.id)
        if owner is None:
            return False
        for statement in owner.body:
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                for target in targets:
                    if isinstance(target, ast.Name) and target.id == name:
                        value = statement.value
                        if value is not None and not isinstance(
                            value, (ast.Constant, ast.List, ast.Tuple, ast.Dict, ast.Set)
                        ):
                            return False
        return self.supported_hierarchy(cls)

    def member(self, cls, name, seen=()):
        if cls.id in seen or len(seen) >= MAX_IMPORT_HOPS:
            return None
        owner = self.classes.get(cls.id)
        if not self.supported_hierarchy(cls):
            return None
        if any(
            isinstance(n, FUNCTIONS)
            and n.name
            in {
                "__getattribute__",
                "__getattr__",
                "__setattr__",
                "__delattr__",
                "__new__",
                "__init_subclass__",
                "__class_getitem__",
            }
            for n in owner.body
        ):
            return None
        result = self.members.get((cls.id, name)) or self.members.get((cls.id, name + "@getter"))
        if result:
            return result
        candidates = []
        for base in owner.bases:
            parent = self.expr(cls.path, base, {}, set())
            if not parent or parent.kind != "class":
                return None
            found = self.member(parent, name, (*seen, cls.id))
            if found and found not in candidates:
                candidates.append(found)
        return candidates[0] if len(candidates) == 1 else None

    def provider_matches(self, source, consumer):
        return [
            x for x in self.provider_candidates if x.get("source_id") == source and x.get("consumer_id") == consumer
        ]

    def add_test_observer(self, path, name, node):
        from .plan import _is_test_path

        if not _is_test_path(path):
            self.unknown(path, node, "parameterized declaration outside baseline test scope remains unresolved")
        elif len(self.test_observers) >= self.observer_limit:
            self.unknown(path, node, "test observer limit reached; further result assertions unresolved")
        elif sum(1 for _ in ast.walk(node)) > 2048:
            self.unknown(path, node, "test observer node limit reached; result assertions unresolved")
        else:
            self.test_observers[f"{path}#{name}"] = (path, name, node)

    @staticmethod
    def relative_module(path, module, level):
        if not level:
            return module or ""
        parts = list(PurePosixPath(path.removeprefix("src/")).parent.parts)
        if level > len(parts):
            return ""
        return ".".join(parts[: len(parts) - level + 1] + ((module or "").split(".") if module else []))

    def lexical_facts(self, node):
        """Reuse immutable AST facts within this request, never dependency facts."""
        if node not in self._syntax_facts:
            self._syntax_facts[node] = (frozenset(_bindings(node)), _dynamic_namespace(node))
        return self._syntax_facts[node]

    def unknown(self, path, node, reason):
        row = {"path": path, "location": _location(node), "reason": reason, "analyzer": VERSION}
        key = (path, tuple(row["location"].values()), reason)
        if key in self._unknown_keys:
            return
        if len(self.unknowns) < MAX_UNKNOWN:
            self._unknown_keys.add(key)
            self.unknowns.append(row)
        elif not self._unknown_limit_reported:
            self._unknown_limit_reported = True
            self.unknowns[-1] = {
                **row,
                "reason": "uncertainty limit reached; additional unresolved boundaries omitted",
            }

    def module(self, name):
        paths = self.modules.get(name, [])
        return Symbol(paths[0], "", "module") if len(paths) == 1 else None

    def export(self, path, name, seen=()):
        key = (path, name)
        if key in seen or len(seen) >= MAX_IMPORT_HOPS:
            return None
        fact = self.tables.get(path, {}).get(name)
        if fact is None:
            if path.endswith("/__init__.py"):
                if path in self.dynamic_exports or _dynamic_namespace(self.sources[path]["tree"]):
                    return None
                module = path.removeprefix("src/").removesuffix("/__init__.py").replace("/", ".")
                return self.module(module + "." + name)
            return Symbol("builtins", name, "exception") if name in EXCEPTIONS else None
        if fact[0] == "definition":
            node = fact[1]
            if node.decorator_list:
                return None
            return Symbol(path, name, "class" if isinstance(node, ast.ClassDef) else "function")
        if fact[0] == "module":
            return self.module(fact[1])
        if fact[0] == "import":
            target = self.module(fact[1])
            return self.export(target.path, fact[2], (*seen, key)) if target else None
        if fact[0] == "alias" and fact[1] is not None:
            return self.expr(path, fact[1], {}, set(), (*seen, key))
        return None

    def expr(self, path, node, env, local, seen=()):
        if isinstance(node, ast.Name):
            value = env.get(node.id)
            if isinstance(value, (Symbol, Instance)):
                return value
            if node.id in local:
                return None
            return self.export(path, node.id, seen)
        if isinstance(node, ast.Attribute):
            root = self.expr(path, node.value, env, local, seen)
            if isinstance(root, Instance):
                return self.member(root.type, node.attr) if not root.escaped else None
            if isinstance(root, Symbol) and root.kind == "class":
                member = self.member(root, node.attr)
                return member if member and member.kind == "static_method" else None
            if root and root.kind == "module":
                module_name = (
                    root.path.removeprefix("src/").removesuffix("/__init__.py").removesuffix(".py").replace("/", ".")
                )
                candidate = module_name + "." + node.attr
                if (path, candidate) in self.explicit_submodules and self.tables.get(root.path, {}).get(
                    node.attr
                ) is None:
                    return self.module(candidate)
                return self.export(root.path, node.attr, seen)
        return None

    def exception(self, path, node, env, local, seen=()):
        symbol = self.expr(path, node.func if isinstance(node, ast.Call) else node, env, local)
        if symbol and symbol.kind == "exception":
            return symbol.id
        if symbol and symbol.kind == "class":
            if symbol.id in seen or len(seen) >= MAX_IMPORT_HOPS:
                return None
            fact = self.tables[symbol.path].get(symbol.name)
            bases = fact[1].bases
            if len(bases) == 1 and self.exception(symbol.path, bases[0], {}, set(), (*seen, symbol.id)):
                return symbol.id
        return None


# Request context is scoped to secure-read baseline trees, never shared across snapshots.

ACTIVE_INDEX = ContextVar("amu_semantic_snapshot", default=None)


def normalized_id(index, ident):
    if ident in index.functions:
        return ident
    if "#" not in ident:
        return ident
    path, name = ident.split("#", 1)
    if path + "#" + name + "@getter" in index.functions:
        return path + "#" + name + "@getter"
    if "." in name:
        owner, member = name.split(".", 1)
        alternative = path + "#" + owner + "::" + member
        if alternative in index.functions:
            return alternative
    return ident


def source_calls(index, path, start=None, end=None):
    """Located source identities, not value effects or runtime dispatch proof."""
    results = []
    for ident, (file, name, function) in index.functions.items():
        if file != path or start is not None and (function.lineno < start or function.end_lineno > end):
            continue
        env = {}
        local = set(_bindings(function))
        owner = index.owners.get(ident)
        if owner and function.args.args:
            env[function.args.args[0].arg] = Instance(owner, "declaring-receiver", exact=False)
        for statement in function.body:
            if (
                isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
            ):
                value = statement.value
                target = index.expr(path, value.func if isinstance(value, ast.Call) else value, env, local)
                if isinstance(value, ast.Call) and target and target.kind == "class":
                    instance = Instance(target, f"{ident}:{statement.lineno}")
                    init = index.members.get((target.id, "__init__"))
                    if init:
                        implementation = index.functions[init.id][2]
                        if any(
                            isinstance(n, ast.Call)
                            and isinstance(n.func, ast.Name)
                            and n.func.id in {"setattr", "delattr"}
                            or isinstance(n, ast.Attribute)
                            and isinstance(n.ctx, (ast.Store, ast.Del))
                            and (target.id, n.attr) in index.members
                            for n in ast.walk(implementation)
                        ):
                            instance.escaped = True
                    target = instance
                env[statement.targets[0].id] = target
            if isinstance(statement, (ast.If, ast.For, ast.While, ast.Try, ast.With)):
                # Branch-local receiver assignments do not establish one receiver after a join.
                for binding in _bindings(ast.Module(body=[statement], type_ignores=[])):
                    env.pop(binding, None)
            pending = [statement]
            owned = []
            while pending:
                node = pending.pop()
                if isinstance(node, (ast.ClassDef, *FUNCTIONS)):
                    continue
                owned.append(node)
                pending.extend(ast.iter_child_nodes(node))
            for node in owned:
                if not isinstance(node, ast.Call):
                    continue
                target = index.expr(path, node.func, env, local)
                if isinstance(target, Symbol) and target.id in index.functions:
                    receiver = (
                        index.expr(path, node.func.value, env, local) if isinstance(node.func, ast.Attribute) else None
                    )
                    results.append(
                        {
                            "source_id": target.id,
                            "consumer_id": ident,
                            "location": _location(node),
                            "resolution": "conditional"
                            if isinstance(receiver, Instance) and not receiver.exact
                            else "resolved",
                            "provenance": "source:shared-semantic-identity",
                        }
                    )
    return results
