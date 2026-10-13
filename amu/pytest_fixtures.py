"""Source-only fixture bindings for the declared pytest return-fixture subset."""

from __future__ import annotations

import ast
import configparser
import tomllib
from dataclasses import dataclass
from pathlib import PurePosixPath

MAX_FIXTURES = 128
MAX_FIXTURE_HOPS = 8
CONFIG_FILES = ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini")


def implicit_setup(configs):
    """Inspect metadata only; configuration cannot load a plugin here."""
    for path, payload in configs.items():
        try:
            text = payload.decode("utf-8")
            if path == "pyproject.toml":
                data = tomllib.loads(text).get("tool", {}).get("pytest", {}).get("ini_options", {})
            else:
                parser = configparser.ConfigParser(interpolation=None)
                parser.read_string(text)
                section = "tool:pytest" if path == "setup.cfg" else "pytest"
                data = parser[section] if parser.has_section(section) else {}
            if any(data.get(key) for key in ("usefixtures", "addopts", "required_plugins")):
                return True
        except (UnicodeError, ValueError, TypeError, AttributeError, configparser.Error):
            return True
    return False


@dataclass(frozen=True)
class Fixture:
    path: str
    name: str
    node: ast.FunctionDef
    supported: bool

    @property
    def id(self):
        return f"{self.path}#{self.node.name}"


class FixtureIndex:
    """Nearest declarations win, including declarations outside the subset.

    Bodies are separate from runtime callable summaries: a decorated fixture
    is not a function that test code may call directly.
    """

    def __init__(self, index):
        self.index = index
        self.by_path = {}
        self.bodies = {}
        self.blocked = set()
        from .plan import _is_test_path

        for path, source in index.sources.items():
            if not (_is_test_path(path) or PurePosixPath(path).name == "conftest.py"):
                continue
            table = index.tables[path]
            declarations = self.by_path.setdefault(path, {})
            tree = source["tree"]
            if index.lexical_facts(tree)[1] or any(
                name == "pytestmark"
                or name == "pytest_plugins"
                or name.startswith("pytest_")
                or name in {"setup_module", "teardown_module", "setup_function", "teardown_function"}
                for name in table
            ):
                self.blocked.add(path)
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    # Class fixtures and inherited setup require their own
                    # lookup context; never fall through to conftest.
                    if any(
                        self.marker(d, table)
                        for n in node.body
                        if isinstance(n, ast.FunctionDef)
                        for d in n.decorator_list
                    ):
                        self.blocked.add(path)
                    continue
                if not isinstance(node, ast.FunctionDef):
                    continue
                markers = [d for d in node.decorator_list if self.marker(d, table)]
                if not markers:
                    continue
                marker = markers[0]
                name = node.name
                supported = len(node.decorator_list) == 1
                if isinstance(marker, ast.Call):
                    supported = supported and not marker.args
                    seen = set()
                    for kw in marker.keywords:
                        if kw.arg in seen:
                            supported = False
                        seen.add(kw.arg)
                        if not isinstance(kw.value, ast.Constant):
                            self.blocked.add(path)
                            supported = False
                        elif kw.arg == "name" and isinstance(kw.value.value, str) and kw.value.value.isidentifier():
                            name = kw.value.value
                        elif kw.arg == "scope" and kw.value.value == "function":
                            pass
                        elif kw.arg == "autouse" and kw.value.value is False:
                            pass
                        else:
                            supported = False
                            if kw.arg == "autouse":
                                self.blocked.add(path)
                supported = supported and table.get(node.name) == ("definition", node)
                body = [
                    n
                    for n in node.body
                    if not (
                        isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)
                    )
                ]
                supported = (
                    supported
                    and bool(body)
                    and isinstance(body[-1], ast.Return)
                    and all(isinstance(n, (ast.Assign, ast.AnnAssign)) for n in body[:-1])
                )
                supported = supported and not any(
                    isinstance(n, (ast.Yield, ast.YieldFrom, ast.Await, ast.NamedExpr)) for n in ast.walk(node)
                )
                supported = supported and not (
                    node.args.posonlyargs
                    or node.args.kwonlyargs
                    or node.args.vararg
                    or node.args.kwarg
                    or node.args.defaults
                )
                if len(self.bodies) >= MAX_FIXTURES:
                    self.blocked.add(path)
                    index.unknown(path, node, "fixture limit reached; source fixture binding unresolved")
                    continue
                fixture = Fixture(path, name, node, supported)
                declarations[name] = fixture if name not in declarations else None
                self.bodies[fixture.id] = (path, node.name, node)

    @staticmethod
    def marker(decorator, table):
        func = decorator.func if isinstance(decorator, ast.Call) else decorator
        return (
            isinstance(func, ast.Attribute)
            and func.attr == "fixture"
            and isinstance(func.value, ast.Name)
            and table.get(func.value.id) == ("module", "pytest")
        ) or (isinstance(func, ast.Name) and table.get(func.id) == ("import", "pytest", "fixture"))

    def scope(self, test_path):
        parent = PurePosixPath(test_path).parent
        return [test_path, *[str(p / "conftest.py") for p in [parent, *parent.parents]]]

    def lookup(self, test_path, name):
        for path in self.scope(test_path):
            declarations = self.by_path.get(path, {})
            if name in declarations:
                return declarations[name]
            if name in self.index.tables.get(path, {}):
                # An imported/decorated alias can override an outer fixture.
                # Never silently fall back through an unresolved local binding.
                return None
        return None

    def safe_context(self, test_path, test_name):
        if any(path in self.blocked for path in self.scope(test_path)):
            return False
        if "::" in test_name:
            owner_name = test_name.split("::", 1)[0]
            owner = self.index.classes.get(f"{test_path}#{owner_name}")
            if (
                owner is None
                or owner.bases
                or owner.decorator_list
                or any(
                    isinstance(n, ast.FunctionDef)
                    and n.name in {"setup_method", "teardown_method", "setup_class", "teardown_class", "__init__"}
                    for n in owner.body
                )
                or "pytestmark" in self.index.lexical_facts(owner)[0]
            ):
                return False
        return not any(
            name in self.index.tables.get(test_path, {})
            for name in {"setup_module", "teardown_module", "setup_function", "teardown_function"}
        )
