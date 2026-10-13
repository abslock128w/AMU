"""Independent, local evidence checks for graph CALLS edges.

The graph engine is a useful source of candidates, but an edge is not safe to
promote to ``sound`` merely because it appears in a response.  In particular,
some parsers can mistake code-shaped text in a Python docstring for a call.
This module checks executable Python AST nodes only; it never executes target
repository code.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

CallEvidence = Literal["verified", "contradicted", "unverifiable"]
CallIdentity = Literal["verified", "mismatch", "unverifiable"]
SymbolPresence = Literal["present", "absent", "unverifiable"]
ReferenceEvidence = Literal["present", "absent", "endpoint_absent", "unverifiable"]
_MAX_SOURCE_BYTES = 2_000_000
_MAX_REPOSITORY_SCAN_FILES = 5_000
_NON_PYTHON_CODE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".go", ".java", ".js", ".jsx", ".rs", ".ts", ".tsx"}


def _safe_python_file(repo: str, relative_path: str) -> Path | None:
    """Return a bounded in-repository Python file, never following an escape."""
    path = Path(relative_path)
    if not relative_path or path.is_absolute() or ".." in path.parts or path.suffix != ".py":
        return None
    try:
        root = Path(repo).resolve()
        candidate = (root / path).resolve()
        candidate.relative_to(root)
        if not candidate.is_file() or candidate.stat().st_size > _MAX_SOURCE_BYTES:
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    return candidate


def _called_leaf(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _source_tree(repo: str, relative_path: str) -> tuple[ast.AST, str] | None:
    source_file = _safe_python_file(repo, relative_path)
    if source_file is None:
        return None
    try:
        source = source_file.read_text(encoding="utf-8")
        return ast.parse(source, filename=str(source_file)), source
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
        return None


def symbol_presence(repo: str, relative_path: str, qualified_name: str) -> SymbolPresence:
    """Check whether a Python definition still exists in the editable worktree.

    This is deliberately narrow: it supports a top-level function/class or a
    direct ``Class.method``.  A non-Python file, malformed source, or a more
    exotic qualified name is *unverifiable*, never treated as deleted.  The
    helper lets refresh distinguish a planned deletion from a graph provider
    merely failing to find a symbol.
    """
    path = Path(relative_path)
    if not relative_path or path.is_absolute() or ".." in path.parts or path.suffix != ".py":
        return "unverifiable"
    try:
        root = Path(repo).resolve()
        if not root.is_dir():
            return "unverifiable"
        candidate = (root / path).resolve()
        candidate.relative_to(root)
        if not candidate.exists():
            return "absent"
    except (OSError, RuntimeError, ValueError):
        return "unverifiable"
    parsed = _source_tree(repo, relative_path)
    if parsed is None:
        return "unverifiable"
    tree, _ = parsed
    parts = qualified_name.split(".")
    if not parts or any(not part or not part.isidentifier() for part in parts) or len(parts) > 2:
        return "unverifiable"
    top_level = [
        node
        for node in getattr(tree, "body", [])
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    if len(parts) == 1:
        return "present" if any(node.name == parts[0] for node in top_level) else "absent"
    owner = next((node for node in top_level if isinstance(node, ast.ClassDef) and node.name == parts[0]), None)
    if owner is None:
        return "absent"
    return (
        "present"
        if any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == parts[1] for node in owner.body)
        else "absent"
    )


def _qualified_definition(tree: ast.AST, qualified_name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | None:
    """Find one deliberately simple public definition without guessing scope."""
    parts = qualified_name.split(".")
    if not parts or any(not part or not part.isidentifier() for part in parts) or len(parts) > 2:
        return None
    top_level = [
        node
        for node in getattr(tree, "body", [])
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    if len(parts) == 1:
        return next((node for node in top_level if node.name == parts[0]), None)
    owner = next((node for node in top_level if isinstance(node, ast.ClassDef) and node.name == parts[0]), None)
    if owner is None:
        return None
    return next(
        (
            node
            for node in owner.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == parts[1]
        ),
        None,
    )


def _module_target_aliases(tree: ast.AST, target_leaf: str) -> set[str]:
    """Collect conservative module-level aliases that could name a target.

    Import aliases are only the first hop.  A module can intentionally expose
    ``legacy = imported_target`` and a saved caller can use ``legacy()``; if
    that alias chain is ignored, a remove/rename refresh can falsely claim the
    caller was migrated.  Follow only simple name-to-name assignments at the
    module level, where the link is statically certain.
    """
    aliases: set[str] = set()
    body = getattr(tree, "body", [])
    for node in body:
        if isinstance(node, ast.ImportFrom):
            for name in node.names:
                if name.name == target_leaf:
                    aliases.add(name.asname or name.name)
        elif isinstance(node, ast.Import):
            for name in node.names:
                if name.name.rsplit(".", 1)[-1] == target_leaf:
                    aliases.add(name.asname or name.name.split(".", 1)[0])
    changed = True
    while changed:
        changed = False
        for node in body:
            value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
            if not isinstance(value, ast.Name) or value.id not in aliases | {target_leaf}:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in aliases:
                    aliases.add(target.id)
                    changed = True
    return aliases


class _DirectDefinitionReferences(ast.NodeVisitor):
    """Conservatively collect lexical target references in one definition.

    A post-remove/rename refresh must never call an old incoming edge removed
    while its source still names the old target.  This visitor intentionally
    over-blocks ambiguous dynamic access rather than guessing that it is safe.
    Nested definitions are excluded: they are separate graph symbols and are
    not evidence about the selected endpoint itself.
    """

    def __init__(
        self,
        root: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
        target_leaf: str,
        module_aliases: set[str] | None = None,
    ):
        self.root = root
        self.target_leaf = target_leaf
        self.aliases: set[str] = set(module_aliases or ())
        self.references = False
        self.dynamic = False

    def _visit_root_signature(self) -> None:
        if isinstance(self.root, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in self.root.decorator_list:
                self.visit(decorator)
            self.visit(self.root.args)
            if self.root.returns is not None:
                self.visit(self.root.returns)
        else:
            for decorator in self.root.decorator_list:
                self.visit(decorator)
            for base in self.root.bases:
                self.visit(base)
            for keyword in self.root.keywords:
                self.visit(keyword.value)

    def scan(self) -> None:
        self._visit_root_signature()
        for statement in self.root.body:
            self.visit(statement)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802 - AST visitor API
        if node.id == self.target_leaf or node.id in self.aliases:
            self.references = True

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802 - AST visitor API
        if node.attr == self.target_leaf:
            self.references = True
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802 - AST visitor API
        for name in node.names:
            if name.name == self.target_leaf:
                self.aliases.add(name.asname or name.name)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802 - AST visitor API
        for name in node.names:
            if name.name.rsplit(".", 1)[-1] == self.target_leaf:
                self.aliases.add(name.asname or name.name.split(".", 1)[0])

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 - AST visitor API
        # Direct dynamic lookup is not independently resolvable.  If it names
        # the deleted target, it is a positive reference; any opaque lookup or
        # eval/exec is deliberately a fail-closed uncertainty.
        callee = node.func.id if isinstance(node.func, ast.Name) else None
        if callee in {"eval", "exec", "globals", "locals", "vars"}:
            self.dynamic = True
        if callee in {"getattr", "setattr", "hasattr"}:
            if len(node.args) < 2:
                self.dynamic = True
            else:
                key = node.args[1]
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    if key.value == self.target_leaf:
                        self.references = True
                else:
                    self.dynamic = True
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:  # noqa: N802 - AST visitor API
        # Registry-style calls (``registry["old_target"]()``) are a concrete
        # old reference.  A variable key could select the old target at
        # runtime, so it is uncertainty rather than evidence of absence.
        key = node.slice
        if isinstance(key, ast.Constant):
            if isinstance(key.value, str) and key.value == self.target_leaf:
                self.references = True
        else:
            self.dynamic = True
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802 - AST visitor API
        # ``scan`` visits root contents directly, so every FunctionDef reached
        # here is nested.  It can still carry a stale reference reachable via
        # closure/callback semantics, so do not turn it into proof of absence.
        self.dynamic = True
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802 - AST visitor API
        self.dynamic = True
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802 - AST visitor API
        # Same rule for nested classes.  A class endpoint's direct annotations
        # and assignments are still visited because ``scan`` starts at body;
        # contained methods remain a conservative uncertainty.
        self.dynamic = True
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802 - AST visitor API
        self.dynamic = True
        return


def symbol_reference_evidence(
    repo: str,
    relative_path: str,
    qualified_name: str,
    target_symbol: str,
) -> ReferenceEvidence:
    """Prove whether one exact Python endpoint still references an old target.

    ``absent`` is strong enough for a remove/rename refresh only when the
    exact saved endpoint still exists and its direct executable/type-definition
    scope has no lexical reference to the old target.  An ``endpoint_absent``
    result is deliberately distinct: a function or method may have been
    renamed or moved while retaining the stale reference.  Alias imports and
    dynamic lookups are handled conservatively.  Unsupported paths, scopes,
    and parse failures return ``unverifiable`` so a graph delta cannot falsely
    declare a stale caller or type consumer gone.
    """
    leaf = target_symbol.rsplit(".", 1)[-1].strip()
    if not leaf or not leaf.isidentifier():
        return "unverifiable"
    presence = symbol_presence(repo, relative_path, qualified_name)
    if presence == "absent":
        return "endpoint_absent"
    if presence != "present":
        return "unverifiable"
    parsed = _source_tree(repo, relative_path)
    if parsed is None:
        return "unverifiable"
    node = _qualified_definition(parsed[0], qualified_name)
    if node is None:
        return "unverifiable"
    visitor = _DirectDefinitionReferences(node, leaf, _module_target_aliases(parsed[0], leaf))
    visitor.scan()
    if visitor.references:
        return "present"
    return "unverifiable" if visitor.dynamic else "absent"


class _AnyPythonReference(ast.NodeVisitor):
    """Conservatively detect any executable/type-level mention of one symbol."""

    def __init__(self, leaf: str):
        self.leaf = leaf
        self.present = False
        self.dynamic = False

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802 - AST visitor API
        if node.id == self.leaf:
            self.present = True

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802 - AST visitor API
        if node.attr == self.leaf:
            self.present = True
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802 - AST visitor API
        if any(alias.name.rsplit(".", 1)[-1] == self.leaf for alias in node.names):
            self.present = True

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802 - AST visitor API
        if any(alias.name.rsplit(".", 1)[-1] == self.leaf for alias in node.names):
            self.present = True

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 - AST visitor API
        callee = node.func.id if isinstance(node.func, ast.Name) else None
        if callee in {"eval", "exec", "globals", "locals", "vars"}:
            self.dynamic = True
        if callee in {"getattr", "setattr", "hasattr"}:
            if len(node.args) < 2 or not isinstance(node.args[1], ast.Constant):
                self.dynamic = True
            elif node.args[1].value == self.leaf:
                self.present = True
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:  # noqa: N802 - AST visitor API
        key = node.slice
        if not isinstance(key, ast.Constant):
            self.dynamic = True
        elif key.value == self.leaf:
            self.present = True
        self.generic_visit(node)


def repository_symbol_reference_evidence(repo: str, target_path: str, target_symbol: str) -> dict[str, object]:
    """Fail closed while proving a deleted Python symbol has no repository caller.

    A removal cannot safely rely only on inbound edges saved before the edit:
    the edit may have introduced a fresh consumer the old graph never saw.
    Scan every bounded Python source file, ignoring docstring text but treating
    dynamic lookup, parse failure, and unsupported code languages as explicit
    uncertainty.  This intentionally over-blocks same-name symbols rather
    than claiming a deletion is safe on a filename/leaf-name guess.
    """
    leaf = target_symbol.rsplit(".", 1)[-1].strip()
    root = Path(repo).resolve()
    if not leaf or not leaf.isidentifier() or not root.is_dir():
        return {"approvable": False, "checked": [], "issues": [{"path": "<repo>", "reason": "invalid repository or target for source scan"}]}
    checked: list[str] = []
    issues: list[dict[str, str]] = []
    file_count = 0
    ignored = {".git", ".amu", "__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "venv", "node_modules"}
    for raw_dir, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in ignored]
        for filename in sorted(files):
            candidate = Path(raw_dir) / filename
            try:
                relative = candidate.resolve().relative_to(root).as_posix()
            except (OSError, RuntimeError, ValueError):
                issues.append({"path": str(candidate), "reason": "could not safely resolve source path"})
                continue
            suffix = candidate.suffix.lower()
            if suffix in _NON_PYTHON_CODE_SUFFIXES:
                issues.append({"path": relative, "reason": "repository contains unsupported non-Python source; deletion cannot be source-verified"})
                continue
            if suffix != ".py" or relative == target_path:
                continue
            file_count += 1
            if file_count > _MAX_REPOSITORY_SCAN_FILES:
                issues.append({"path": "<repo>", "reason": f"repository source scan exceeded {_MAX_REPOSITORY_SCAN_FILES} Python files"})
                break
            parsed = _source_tree(repo, relative)
            if parsed is None:
                issues.append({"path": relative, "reason": "Python source could not be parsed for stale references"})
                continue
            visitor = _AnyPythonReference(leaf)
            visitor.visit(parsed[0])
            checked.append(relative)
            if visitor.present:
                issues.append({"path": relative, "reason": f"source still names removed target {leaf}"})
            elif visitor.dynamic:
                issues.append({"path": relative, "reason": "dynamic lookup prevents proving removed target is absent"})
        if file_count > _MAX_REPOSITORY_SCAN_FILES:
            break
    return {"approvable": not issues, "checked": sorted(checked), "issues": sorted(issues, key=lambda row: (row["path"], row["reason"]))}


def _in_scope(node: ast.AST, start_line: int | None, end_line: int | None) -> bool:
    """Whether an AST node starts in an optional inclusive source range."""
    if start_line is None or end_line is None:
        return True
    line = getattr(node, "lineno", None)
    return isinstance(line, int) and start_line <= line <= end_line


def _scope_root(
    tree: ast.AST, start_line: int | None, end_line: int | None
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Find the tightest function whose span is the graph focus range."""
    if start_line is None or end_line is None:
        return None
    candidates = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and isinstance(getattr(node, "lineno", None), int)
        and isinstance(getattr(node, "end_lineno", None), int)
        and node.lineno <= start_line <= end_line <= node.end_lineno
    ]
    return min(candidates, key=lambda node: node.end_lineno - node.lineno) if candidates else None


class _DirectBodyCalls(ast.NodeVisitor):
    """Collect calls executed by one function, excluding nested declarations."""

    def __init__(self, root: ast.FunctionDef | ast.AsyncFunctionDef):
        self.root = root
        self.calls: list[ast.Call] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 - AST visitor API
        self.calls.append(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802 - AST visitor API
        if node is self.root:
            self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802 - AST visitor API
        if node is self.root:
            self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802 - AST visitor API
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802 - AST visitor API
        return


def _scoped_calls(tree: ast.AST, start_line: int | None, end_line: int | None) -> list[ast.Call]:
    """Return executable calls in a direct function scope or whole module."""
    root = _scope_root(tree, start_line, end_line)
    if start_line is not None or end_line is not None:
        if root is None:
            return []
        visitor = _DirectBodyCalls(root)
        visitor.visit(root)
        candidates = visitor.calls
    else:
        candidates = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    return [node for node in candidates if _in_scope(node, start_line, end_line)]


def _matching_calls(tree: ast.AST, target: str, start_line: int | None, end_line: int | None) -> list[ast.Call]:
    """Return scoped calls whose final spelling is ``target``.

    This helper intentionally retains its leaf-name behavior for the narrow
    docstring false-positive detector.  Approval paths additionally call the
    import-aware identity resolver below; a matching leaf by itself is never
    treated as proof that the call reached a particular file/symbol.
    """
    return [node for node in _scoped_calls(tree, start_line, end_line) if _called_leaf(node.func) == target]


def _module_file(source_path: str, module: str | None, level: int = 0) -> str | None:
    """Resolve a simple import module to a repository-relative ``.py`` file.

    This intentionally does not import the target repository or guess package
    export machinery.  A package ``__init__`` or namespace ambiguity remains
    unverified, which is the safe answer for an approval decision.
    """
    source = Path(source_path)
    if source.is_absolute() or ".." in source.parts or source.suffix != ".py":
        return None
    # Absolute imports are rooted at the repository's module namespace, not
    # the importing file's directory.  Package-relative imports alone climb
    # from that file's package.  Source-layout/namespace ambiguity still does
    # not justify guessing a different path.
    directory = list(source.parent.parts) if level else []
    if level:
        climb = level - 1
        if climb > len(directory):
            return None
        directory = directory[: len(directory) - climb]
    parts = [part for part in (module or "").split(".") if part]
    if any(not part.isidentifier() for part in [*directory, *parts]) or not [*directory, *parts]:
        return None
    return "/".join([*directory, *parts]) + ".py"


def _module_parts(path: str) -> tuple[str, ...] | None:
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or candidate.suffix != ".py":
        return None
    parts = list(candidate.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return tuple(parts) if parts and all(part.isidentifier() for part in parts) else None


def _dotted_expr(node: ast.expr) -> tuple[str, ...] | None:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        prefix = _dotted_expr(node.value)
        return (*prefix, node.attr) if prefix is not None else None
    return None


def _package_submodule(repo: str, source_path: str, module: str | None, level: int, name: str) -> str | None:
    """Resolve a plain package submodule only when its package has no competing binding."""
    path = _module_file(source_path, module, level)
    if path is None or not name.isidentifier():
        return None
    package = path[:-3]
    root = Path(repo)
    # A same-named module/package or package export can change import meaning.
    if (root / path).exists() or (root / path).is_symlink():
        return None
    parsed = _source_tree(repo, package + "/__init__.py")
    candidate = package + "/" + name + ".py"
    if parsed is None or _source_tree(repo, candidate) is None:
        return None
    risk = _ModuleBindingRisk(name)
    risk.visit(parsed[0])
    if (risk.bindings or risk.dynamic
            or any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__getattr__"
                   for node in getattr(parsed[0], "body", []))):
        return None
    return candidate


def _module_bindings(
    tree: ast.AST, source_path: str, *, repo: str | None = None
) -> tuple[dict[str, tuple[str, tuple[str, ...]]], dict[str, tuple[str, tuple[str, ...]]]]:
    """Return simple ``from`` and ``import`` bindings visible at module scope."""
    direct: dict[str, tuple[str, tuple[str, ...]]] = {}
    modules: dict[str, tuple[str, tuple[str, ...]]] = {}
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.ImportFrom):
            module_path = _module_file(source_path, node.module, node.level)
            if module_path is None:
                continue
            for alias in node.names:
                parts = tuple(part for part in alias.name.split(".") if part)
                local = alias.asname or (parts[0] if parts else "")
                if local and parts and all(part.isidentifier() for part in parts):
                    submodule = _package_submodule(repo, source_path, node.module, node.level, alias.name) if repo else None
                    if submodule:
                        modules[local] = (submodule, (local,))
                    else:
                        direct[local] = (module_path, parts)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = tuple(part for part in alias.name.split(".") if part)
                module_path = _module_file(source_path, alias.name)
                local = alias.asname or (parts[0] if parts else "")
                if local and module_path and parts and all(part.isidentifier() for part in parts):
                    modules[local] = (module_path, parts)
    return direct, modules


def _scope_binds_name(tree: ast.AST, start_line: int | None, end_line: int | None, name: str) -> bool:
    """Return whether the selected function can shadow a module import.

    Over-blocking a dynamic/rebound name is intentional.  This check is only
    used to decide whether an edge may approve an edit plan.
    """
    root = _scope_root(tree, start_line, end_line)
    if root is None:
        return False
    for node in ast.walk(root):
        if isinstance(node, ast.arg) and node.arg == name:
            return True
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)) and node.id == name:
            return True
        if isinstance(node, ast.alias) and (node.asname or node.name.split(".", 1)[0]) == name:
            return True
    return False


class _ModuleBindingRisk(ast.NodeVisitor):
    """Track syntax that can invalidate a saved top-level import binding."""

    def __init__(self, name: str, allowed_calls: frozenset[ast.Call] = frozenset()):
        self.name = name
        self.allowed_calls = allowed_calls
        self.bindings = 0
        self.dynamic = False

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if node.id == self.name and isinstance(node.ctx, (ast.Store, ast.Del)):
            self.bindings += 1

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        self.bindings += sum((alias.asname or alias.name.split(".", 1)[0]) == self.name for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        self.dynamic = self.dynamic or any(alias.name == "*" for alias in node.names)
        self.bindings += sum((alias.asname or alias.name) == self.name for alias in node.names)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self.bindings += node.name == self.name
        for field, value in ast.iter_fields(node):
            if field == "body":
                continue
            for child in value if isinstance(value, list) else [value]:
                if isinstance(child, ast.AST):
                    self.visit(child)

    visit_AsyncFunctionDef = visit_FunctionDef  # same binding/signature behavior

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self.bindings += node.name == self.name
        for child in [*node.bases, *node.keywords, *node.decorator_list]:
            self.visit(child)

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        self.visit(node.args)  # Defaults execute; the body is a different scope.

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:  # noqa: N802
        self.bindings += node.name == self.name
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        chain = _dotted_expr(node)
        if chain and chain[0] == self.name and isinstance(node.ctx, (ast.Store, ast.Del)):
            self.dynamic = True
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:  # noqa: N802
        chain = _dotted_expr(node.value)
        if chain and chain[0] == self.name and isinstance(node.ctx, (ast.Store, ast.Del)):
            self.dynamic = True
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        chain = _dotted_expr(node.func)
        if chain and ((chain[0] == self.name and node not in self.allowed_calls) or chain == ("exec",) or chain == ("eval",)
                      or chain == ("globals",) or chain == ("locals",)):
            self.dynamic = True
        if chain in {("setattr",), ("delattr",)} and node.args:
            target = _dotted_expr(node.args[0])
            self.dynamic = self.dynamic or bool(target and target[0] == self.name)
        self.generic_visit(node)


def _module_binding_uncertain(tree: ast.AST, name: str, *, allowed_calls: frozenset[ast.Call] = frozenset()) -> bool:
    risk = _ModuleBindingRisk(name, allowed_calls)
    risk.visit(tree)
    return risk.dynamic or risk.bindings > 1


def _imported_definition_mismatch(
    repo: str | None, actual_path: str, actual_symbol: tuple[str, ...]
) -> CallIdentity:
    """Disprove a same-named endpoint only with an unambiguous own definition.

    An imported module is a namespace, not necessarily the defining file:
    explicit/wildcard reexports, generated bindings and dynamic attributes
    can expose definitions from elsewhere.  We do not resolve those chains
    here.  Keep them visible as uncertainty rather than declaring a real edge
    false or promoting an unproved reexport to approval-grade evidence.
    """
    if repo is None or len(actual_symbol) != 1:
        return "unverifiable"
    parsed = _source_tree(repo, actual_path)
    if parsed is None:
        return "unverifiable"
    name = actual_symbol[0]
    definitions = [
        node for node in getattr(parsed[0], "body", [])
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name
    ]
    if len(definitions) != 1 or definitions[0].decorator_list:
        return "unverifiable"
    for statement in getattr(parsed[0], "body", []):
        if statement is definitions[0]:
            continue
        roots = [statement]
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if statement.name == "__getattr__" or statement.decorator_list:
                return "unverifiable"
            # Sibling bodies do not run on import, but defaults/annotations/
            # type-parameter expressions can.  Do not skip that execution.
            roots = [child for field, value in ast.iter_fields(statement) if field != "body"
                     for child in (value if isinstance(value, list) else [value]) if isinstance(child, ast.AST)]
        for root in roots:
            for node in ast.walk(root):
                if isinstance(node, ast.alias) and (node.name == "*" or (node.asname or node.name.split(".", 1)[0]) == name):
                    return "unverifiable"
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
                    return "unverifiable"
                if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)) and node.id == name:
                    return "unverifiable"
                if isinstance(node, ast.Call):
                    return "unverifiable"  # Module execution may generate/rebind the export.
    return "mismatch"


def _call_identity(
    tree: ast.AST,
    source_path: str,
    target_path: str,
    target_symbol: str,
    call: ast.Call,
    start_line: int | None,
    end_line: int | None,
    *,
    repo: str | None = None,
) -> CallIdentity:
    """Resolve a direct Python call against one exact graph endpoint.

    The resolver deliberately supports only static module imports, simple
    aliases, and local definitions.  Everything else stays ``unverifiable``;
    accepting an attribute merely because its last component matches would
    turn ``other.target()`` into false proof for ``target.py#target``.
    """
    expected = tuple(part for part in target_symbol.split(".") if part)
    chain = _dotted_expr(call.func)
    if not expected or chain is None:
        return "unverifiable"
    base = chain[0]
    if _scope_binds_name(tree, start_line, end_line, base) or _module_binding_uncertain(tree, base):
        return "unverifiable"
    direct, modules = _module_bindings(tree, source_path, repo=repo)
    binding = direct.get(base)
    if binding is not None:
        actual_path, prefix = binding
        actual_symbol = (*prefix, *chain[1:])
        if actual_path == target_path and actual_symbol == expected:
            return "verified"
        return _imported_definition_mismatch(repo, actual_path, actual_symbol) if chain[-1] == expected[-1] else "unverifiable"
    module_binding = modules.get(base)
    if module_binding is not None:
        actual_path, module_parts = module_binding
        raw_parts = chain
        # ``import pkg.mod`` binds ``pkg``; an alias binds the complete
        # module directly.  Support both forms without resolving attributes
        # from arbitrary runtime objects.
        if raw_parts[: len(module_parts)] == module_parts:
            actual_symbol = raw_parts[len(module_parts) :]
        else:
            actual_symbol = raw_parts[1:]
        if actual_path == target_path and actual_symbol == expected:
            return "verified"
        return _imported_definition_mismatch(repo, actual_path, actual_symbol) if chain[-1] == expected[-1] else "unverifiable"
    if (
        source_path == target_path
        and chain == expected
        and _qualified_definition(tree, target_symbol) is not None
    ):
        return "verified"
    return "unverifiable"


def _literal_docstring(node: ast.AST) -> tuple[str, int, int] | None:
    body = getattr(node, "body", None)
    if not isinstance(body, list) or not body:
        return None
    first = body[0]
    if (
        not isinstance(first, ast.Expr)
        or not isinstance(first.value, ast.Constant)
        or not isinstance(first.value.value, str)
    ):
        return None
    start = getattr(first, "lineno", None)
    end = getattr(first, "end_lineno", start)
    return (first.value.value, start, end) if isinstance(start, int) and isinstance(end, int) else None


def _docstring_values(tree: ast.AST) -> list[tuple[str, int, int]]:
    """Return literal docstrings and their physical spans without evaluating code."""
    values: list[tuple[str, int, int]] = []
    for node in ast.walk(tree):
        value = _literal_docstring(node)
        if value is not None:
            values.append(value)
    return values


def _overlaps_scope(start: int, end: int, scope_start: int | None, scope_end: int | None, line: int | None) -> bool:
    if line is not None:
        return start <= line <= end
    if scope_start is None or scope_end is None:
        return False
    return start <= scope_end and scope_start <= end


def _has_docstring_call(
    tree: ast.AST, target: str, scope_start: int | None, scope_end: int | None, line: int | None
) -> bool:
    """Recognize only a concrete code-shaped call inside the relevant docstring.

    We intentionally do not treat an absent textual name as a contradiction:
    a graph relation can be valid through an import alias.  A matching example
    in a docstring, paired with no executable AST call, is the specific parser
    failure this independent check can safely disprove.
    """
    if line is None and (scope_start is None or scope_end is None):
        return False
    root = _scope_root(tree, scope_start, scope_end)
    values = (
        [value for value in [_literal_docstring(root)] if value is not None]
        if root is not None
        else _docstring_values(tree)
    )
    pattern = re.compile(rf"(?<![A-Za-z0-9_])(?:[A-Za-z_]\w*\.)*{re.escape(target)}\s*\(")
    return any(
        _overlaps_scope(start, end, scope_start, scope_end, line) and pattern.search(value)
        for value, start, end in values
    )


def _line_value(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def direct_call_evidence(
    repo: str,
    relative_path: str,
    target_symbol: str,
    line: int | None = None,
    *,
    scope_start: int | None = None,
    scope_end: int | None = None,
) -> CallEvidence:
    """Check whether executable Python source contains a direct call to target.

    ``verified`` means a matching :class:`ast.Call` was found.  ``contradicted``
    means this valid Python file contains no such executable call (so a raw
    graph edge is not evidence).  ``unverifiable`` is deliberately conservative
    for unsupported languages, malformed files, unsafe paths, and oversized
    inputs.  The final-name comparison validates a call expression, not import
    resolution; it is intended as an independent precision check, not a second
    language server.
    """
    target = target_symbol.rsplit(".", 1)[-1].strip()
    parsed = _source_tree(repo, relative_path)
    if not target or parsed is None:
        return "unverifiable"
    tree, _ = parsed
    for node in _matching_calls(tree, target, scope_start, scope_end):
        if line is None:
            return "verified"
        end_line = getattr(node, "end_lineno", node.lineno)
        if node.lineno <= line <= end_line:
            return "verified"
    return "contradicted"


def _call_plausibly_names_target(tree: ast.AST, source_path: str, call: ast.Call, target: str) -> bool:
    """Recognize spelling/import aliases as plausibility, never endpoint proof."""
    if _called_leaf(call.func) == target:
        return True
    chain = _dotted_expr(call.func)
    if chain is None or len(chain) != 1:
        return False
    direct, _modules = _module_bindings(tree, source_path)
    links = {}
    for statement in getattr(tree, "body", []):
        value = statement.value if isinstance(statement, (ast.Assign, ast.AnnAssign)) else None
        if not isinstance(value, ast.Name):
            continue
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        links.update({node.id: value.id for node in targets if isinstance(node, ast.Name)})
    name = chain[0]
    seen = set()
    for _ in range(16):
        binding = direct.get(name)
        if binding and binding[1][-1] == target:
            return True
        if name in seen or name not in links:
            return False
        seen.add(name)
        name = links[name]
    return False


def graph_call_evidence(
    repo: str,
    relative_path: str,
    target_symbol: str,
    line: int | None = None,
    *,
    target_path: str | None = None,
    scope_start: int | None = None,
    scope_end: int | None = None,
) -> CallEvidence:
    """Independently validate a graph call candidate against an exact endpoint.

    ``target_path`` turns this into the approval-grade identity check: a call
    must resolve through a simple static import/local definition to that exact
    file and symbol.  Without it, this retains the historical leaf-name mode
    for low-level display tests only; callers must not promote that mode into
    approval evidence.  A known imported namespace with its own unambiguous
    same-named definition can contradict another endpoint.  An import to a
    different file alone cannot: reexports/dynamic bindings remain deliberately
    unverifiable.
    """
    from .semantic import ACTIVE_INDEX, normalized_id, source_calls
    index = ACTIVE_INDEX.get()
    if target_path and index is not None and getattr(index,"repository",None)==str(Path(repo).resolve()):
        expected=normalized_id(index,target_path+"#"+target_symbol)
        rows=source_calls(index,relative_path,scope_start,scope_end)
        rows=[r for r in rows if line is None or r["location"]["line"]<=line<=r["location"]["end_line"]]
        if any(r["source_id"]==expected and r["resolution"]=="resolved" for r in rows):
            return "verified"
        if any(r["source_id"]==expected for r in rows):
            return "unverifiable"
    target = target_symbol.rsplit(".", 1)[-1].strip()
    parsed = _source_tree(repo, relative_path)
    if not target or parsed is None:
        return "unverifiable"
    tree, _ = parsed
    calls = _scoped_calls(tree, scope_start, scope_end)
    candidates = []
    for node in calls:
        end_line = getattr(node, "end_lineno", node.lineno)
        if line is None or node.lineno <= line <= end_line:
            candidates.append(node)
    if target_path is None:
        return "verified" if any(_called_leaf(node.func) == target for node in candidates) else (
            "contradicted" if _has_docstring_call(tree, target, scope_start, scope_end, line) else "unverifiable"
        )
    expected_path = _text(target_path)
    if expected_path is None or _module_parts(expected_path) is None:
        return "unverifiable"
    saw_mismatch = False
    saw_unverified_matching_call = False
    for node in candidates:
        identity = _call_identity(tree, relative_path, expected_path, target_symbol, node, scope_start, scope_end, repo=repo)
        if identity == "verified":
            return "verified"
        saw_mismatch = saw_mismatch or identity == "mismatch"
        saw_unverified_matching_call = saw_unverified_matching_call or (
            identity == "unverifiable" and _call_plausibly_names_target(tree, relative_path, node, target)
        )
    if saw_unverified_matching_call:
        # A real but unresolved call can coexist with a documentation example
        # or a different same-named call.  Neither disproves the candidate.
        return "unverifiable"
    if _has_docstring_call(tree, target, scope_start, scope_end, line):
        return "contradicted"
    if saw_mismatch:
        return "contradicted"
    return "unverifiable"


def _entry_evidence(
    repo: str, raw_symbol: object, impact: Mapping[str, Any], section: str, entry: Mapping[str, Any]
) -> tuple[CallEvidence, dict[str, object] | None]:
    """Return a source-validation verdict and stable metadata for one raw edge."""
    if str(entry.get("relation") or "").strip().upper() not in {"CALLS", "ASYNC_CALLS"}:
        return "unverifiable", None
    if _line_value(entry.get("depth")) != 1:
        return "unverifiable", None
    endpoint = entry.get("endpoint")
    if not isinstance(endpoint, Mapping):
        return "unverifiable", None
    focus = impact.get("focus")
    focus = focus if isinstance(focus, Mapping) else {}
    call_site = entry.get("call_site")
    call_site = call_site if isinstance(call_site, Mapping) else {}
    site_line = _line_value(call_site.get("line")) or _line_value(call_site.get("start_line"))

    if section == "callers":
        site_path = _text(call_site.get("file_path"))
        endpoint_path = _text(endpoint.get("file_path"))
        if site_path and endpoint_path and site_path != endpoint_path:
            return "unverifiable", None
        source_path = site_path or endpoint_path
        target = _text(focus.get("qualified_name")) or _text(focus.get("name")) or _text(raw_symbol)
        target_path = _text(focus.get("file_path"))
        endpoint_start = _line_value(endpoint.get("start_line"))
        endpoint_end = _line_value(endpoint.get("end_line"))
        # Some graph providers omit a call-site span for an incoming CALLS
        # edge.  In that case, the caller's symbol span is the only safe
        # scope for distinguishing a real call from a docstring example.  Do
        # not search the whole file: an unrelated sibling can call the same
        # target and would otherwise make this edge look verified.
        if (
            site_line is None
            and source_path == endpoint_path
            and endpoint_start is not None
            and endpoint_end is not None
            and endpoint_start <= endpoint_end
        ):
            scope_start, scope_end = endpoint_start, endpoint_end
        else:
            scope_start = scope_end = None
        endpoint_name = _text(endpoint.get("qualified_name")) or _text(endpoint.get("name"))
    else:
        site_path = _text(call_site.get("file_path"))
        focus_path = _text(focus.get("file_path"))
        if site_path and focus_path and site_path != focus_path:
            return "unverifiable", None
        source_path = site_path or focus_path
        target = _text(endpoint.get("qualified_name")) or _text(endpoint.get("name"))
        target_path = _text(endpoint.get("file_path"))
        scope_start = _line_value(focus.get("start_line")) if source_path == focus_path else None
        scope_end = _line_value(focus.get("end_line")) if source_path == focus_path else None
        endpoint_name = target

    if not source_path or not target:
        return "unverifiable", None
    if section == "callees" and entry.get("external_boundary"):
        # The filter establishes this syntax boundary independently below;
        # it does not certify the imported callable's implementation.
        return "verified", None
    if not target_path:
        # Entire can emit a named CALLS candidate without a resolved endpoint
        # file.  It is not identity-grade evidence, but the caller's AST can
        # still prove that a purported call exists only in a docstring.  Keep
        # real name-only calls as uncertainty; remove only source-proven false
        # positives.
        verdict = graph_call_evidence(
            repo,
            source_path,
            target,
            site_line,
            scope_start=scope_start,
            scope_end=scope_end,
        )
        evidence = (
            "source:python-ast:docstring-only-call"
            if verdict == "contradicted"
            else "source:python-ast:unresolved-endpoint-path"
        )
        return (
            verdict if verdict == "contradicted" else "unverifiable",
            {
                "target": str(raw_symbol),
                "section": section,
                "source_path": source_path,
                "endpoint": endpoint_name or target,
                "line": site_line,
                "evidence": evidence,
            },
        )
    verdict = graph_call_evidence(
        repo,
        source_path,
        target,
        site_line,
        target_path=target_path,
        scope_start=scope_start,
        scope_end=scope_end,
    )
    evidence = "source:python-ast:docstring-only-call"
    if verdict == "unverifiable":
        evidence = "source:python-ast:unverified-call-identity"
    elif verdict == "contradicted":
        parsed = _source_tree(repo, source_path)
        has_docstring = (
            parsed is not None
            and _has_docstring_call(parsed[0], target.rsplit(".", 1)[-1], scope_start, scope_end, site_line)
        )
        if not has_docstring:
            evidence = "source:python-ast:call-target-identity-mismatch"
    metadata = {
        "target": str(raw_symbol),
        "section": section,
        "source_path": source_path,
        "endpoint": endpoint_name or "?",
        "line": site_line,
        "evidence": evidence,
    }
    return verdict, metadata


def _plain_frozen_dataclass(tree: ast.AST, definition: ast.AST, repo: str) -> bool:
    """Recognize only a top-level class decorated by unshadowed stdlib dataclass.

    The source establishes the declaration's file/name identity. It does not
    establish runtime behavior or make unrelated decorators safe to resolve.
    """
    if not isinstance(definition, ast.ClassDef) or len(definition.decorator_list) != 1:
        return False
    decorator = definition.decorator_list[0]
    if (not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Name)
            or decorator.func.id != "dataclass" or decorator.args or len(decorator.keywords) != 1
            or decorator.keywords[0].arg != "frozen"
            or not isinstance(decorator.keywords[0].value, ast.Constant)
            or decorator.keywords[0].value.value is not True):
        return False
    imports = [alias for node in getattr(tree, "body", [])
               if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "dataclasses"
               for alias in node.names if alias.name == "dataclass" and alias.asname is None]
    if len(imports) != 1 or "dataclasses" not in sys.stdlib_module_names:
        return False
    root = Path(repo)
    if any((root / item).exists() or (root / item).is_symlink() for item in ("dataclasses.py", "dataclasses")):
        return False
    return not _module_binding_uncertain(tree, "dataclass", allowed_calls=frozenset({decorator}))


def _resolve_pathless_callee(repo: str, impact: Mapping[str, Any], entry: Mapping[str, Any]) -> dict:
    """Enrich one direct call from source, retaining its original provider endpoint.

    Only explicit static imports, plain package submodules and unshadowed
    standard-library imports are supported. Unknown third-party imports and
    package exports retain their original unresolved identity.
    """
    result = dict(entry)
    result.pop("external_boundary", None)  # Never accept a provider's source-proof claim.
    endpoint = entry.get("endpoint")
    focus = impact.get("focus")
    if (not isinstance(endpoint, Mapping) or endpoint.get("file_path") or not isinstance(focus, Mapping)
            or _line_value(entry.get("depth")) != 1 or entry.get("relation") not in {"CALLS", "ASYNC_CALLS"}):
        return result
    source_path = _text(focus.get("file_path"))
    identity = _text(endpoint.get("qualified_name")) or _text(endpoint.get("name"))
    parsed = _source_tree(repo, source_path) if source_path else None
    if parsed is None or not identity:
        return result
    tree, source = parsed
    start, end = _line_value(focus.get("start_line")), _line_value(focus.get("end_line"))
    if start is None or end is None:
        return result
    direct, modules = _module_bindings(tree, source_path, repo=repo)
    matches = []
    for call in _scoped_calls(tree, start, end):
        chain = _dotted_expr(call.func)
        if (not chain or chain[-1] != identity.rsplit(".", 1)[-1]
                or _scope_binds_name(tree, start, end, chain[0]) or _module_binding_uncertain(tree, chain[0])):
            continue
        path, symbol = None, ()
        if chain[0] in direct:
            path, prefix = direct[chain[0]]
            symbol = (*prefix, *chain[1:])
        elif chain[0] in modules:
            path, prefix = modules[chain[0]]
            symbol = chain[len(prefix):] if chain[:len(prefix)] == prefix else chain[1:]
        if path is None or not symbol:
            continue
        parts = _module_parts(path)
        if not parts:
            continue
        canonical = ".".join((*parts, *symbol))
        # Some providers collapse `from package import submodule` to the
        # package namespace. Match only that exact documented import form.
        names = {canonical}
        for node in getattr(tree, "body", []):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if (alias.asname or alias.name) == chain[0] and chain[0] in modules:
                        parent = _module_file(source_path, node.module, node.level)
                        if parent:
                            names.add(".".join((*(_module_parts(parent) or ()), *symbol)))
        if identity not in names:
            continue
        target = _source_tree(repo, path)
        if target is not None:
            definition = _qualified_definition(target[0], ".".join(symbol))
            if (len(symbol) == 1 and definition is not None
                    and (not definition.decorator_list or _plain_frozen_dataclass(target[0], definition, repo))
                    and not _module_binding_uncertain(target[0], symbol[0])):
                matches.append((path, ".".join(symbol), definition, call, canonical))
        elif parts[0] in sys.stdlib_module_names:
            # Root shadowing (including packages/symlinks) precludes an
            # established external import in this repository source namespace.
            root = Path(repo)
            if any((root / item).exists() or (root / item).is_symlink()
                   for item in (parts[0] + ".py", parts[0])):
                continue
            matches.append((None, canonical, None, call, canonical))
    identities = {(path, symbol) for path, symbol, _, _, _ in matches}
    if len(identities) != 1:
        return result
    path, symbol, definition, _, _ = matches[0]
    proof = {"source_path": source_path, "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
             "locations": [{"line": call.lineno, "end_line": call.end_lineno} for _, _, _, call, _ in matches]}
    if path is None:
        result["external_boundary"] = {**proof, "identity": symbol, "kind": "python-stdlib-import",
                                       "evidence": "source:python-ast:stdlib-import", "runtime_verified": False,
                                       "provider_endpoint": dict(endpoint)}
    else:
        result["source_resolution"] = {**proof, "evidence": "source:python-ast:package-submodule",
                                       "definition_form": "stdlib-frozen-dataclass" if definition.decorator_list else "undecorated",
                                       "provider_endpoint": dict(endpoint)}
        result["endpoint"] = {"file_path": path, "name": definition.name, "qualified_name": symbol,
                              "kind": "class" if isinstance(definition, ast.ClassDef) else "function",
                              "start_line": definition.lineno, "end_line": definition.end_lineno}
    return result


def filter_python_call_evidence(
    repo: str, impacts: Mapping[str, object]
) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
    """Filter disproved direct calls and disclose unverified call identities.

    This triple separates contradictions from uncertainty: contradicted edges
    are removed, while unresolved identities remain visible and disclosed.
    Approval policy is applied by graph_readiness; a resolved provider edge is
    not contradicted merely because this bounded source resolver abstains.
    """
    filtered: dict[str, object] = copy.deepcopy(dict(impacts))
    contradictions: list[dict[str, object]] = []
    uncertainties: list[dict[str, object]] = []
    for raw_symbol, raw_impact in filtered.items():
        if not isinstance(raw_impact, dict):
            continue
        for section in ("callers", "callees"):
            group = raw_impact.get(section)
            if not isinstance(group, dict) or not isinstance(group.get("entries"), list):
                continue
            retained: list[object] = []
            removed = 0
            for entry in group["entries"]:
                if not isinstance(entry, Mapping):
                    retained.append(entry)
                    continue
                if section == "callees":
                    entry = _resolve_pathless_callee(repo, raw_impact, entry)
                verdict, metadata = _entry_evidence(repo, raw_symbol, raw_impact, section, entry)
                if verdict == "contradicted" and metadata is not None:
                    contradictions.append(metadata)
                    removed += 1
                    continue
                if verdict == "unverifiable" and metadata is not None:
                    uncertainties.append(metadata)
                retained.append(entry)
            group["entries"] = retained
            total = group.get("total")
            if isinstance(total, int) and not isinstance(total, bool):
                group["total"] = max(0, total - removed)
    order = lambda row: (  # noqa: E731 - concise stable audit ordering
        str(row["target"]),
        str(row["section"]),
        str(row["source_path"]),
        str(row["endpoint"]),
        str(row["line"]),
    )
    return filtered, sorted(contradictions, key=order), sorted(uncertainties, key=order)


def filter_contradicted_python_call_edges(
    repo: str, impacts: Mapping[str, object]
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Compatibility wrapper returning only source-proven contradictions.

    Approval-grade paths use :func:`filter_python_call_evidence` so unresolved
    aliases or shadowed same-name calls are never silently accepted.
    """
    filtered, contradictions, _uncertainties = filter_python_call_evidence(repo, impacts)
    return filtered, contradictions


def _filter_neighbor_entries(
    repo: str,
    target: str,
    focus: Mapping[str, Any],
    section: str,
    entries: list[object],
) -> tuple[list[object], list[dict[str, object]], list[dict[str, object]]]:
    """Apply the impact-call guard to one normalized neighbors direction."""
    impact = {target: {"focus": dict(focus), section: {"total": len(entries), "entries": entries}}}
    filtered, contradictions, uncertainties = filter_python_call_evidence(repo, impact)
    row = filtered.get(target)
    group = row.get(section) if isinstance(row, Mapping) else None
    kept = group.get("entries") if isinstance(group, Mapping) else None
    return (kept if isinstance(kept, list) else entries), contradictions, uncertainties


def filter_contradicted_python_neighbor_edges(
    repo: str,
    payload: Mapping[str, object],
    requested_direction: str | None = None,
) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
    """Remove source-proven false CALLS edges from ``graph neighbors`` output.

    Entire's neighbors wire shape groups resolved relationships under a match's
    ``incoming``/``outgoing`` lists; older versions may instead use one generic
    ``neighbors`` or ``entries`` list.  Normalize only enough to reuse the
    deliberately narrow direct-Python-call guard above, retaining every edge
    whose source cannot prove it false.
    """
    filtered: dict[str, object] = copy.deepcopy(dict(payload))
    matches = filtered.get("matches")
    if not isinstance(matches, list):
        return filtered, [], []
    contradictions: list[dict[str, object]] = []
    uncertainties: list[dict[str, object]] = []
    fallback_direction = requested_direction if requested_direction in {"in", "out"} else None
    for match in matches:
        if not isinstance(match, dict):
            continue
        focus = match.get("symbol")
        if not isinstance(focus, Mapping):
            continue
        target = _text(focus.get("qualified_name")) or _text(focus.get("name"))
        if not target:
            continue
        has_directional_groups = False
        for key, section in (("incoming", "callers"), ("outgoing", "callees")):
            entries = match.get(key)
            if not isinstance(entries, list):
                continue
            has_directional_groups = True
            kept, found, unresolved = _filter_neighbor_entries(repo, target, focus, section, entries)
            match[key] = kept
            contradictions.extend(found)
            uncertainties.extend(unresolved)
        if has_directional_groups:
            continue
        for key in ("neighbors", "entries"):
            entries = match.get(key)
            if not isinstance(entries, list):
                continue
            kept: list[object] = []
            for entry in entries:
                if not isinstance(entry, Mapping):
                    kept.append(entry)
                    continue
                direction = _text(entry.get("direction")) or fallback_direction
                section = "callers" if direction == "in" else ("callees" if direction == "out" else None)
                if section is None:
                    kept.append(entry)
                    continue
                retained, found, unresolved = _filter_neighbor_entries(repo, target, focus, section, [entry])
                kept.extend(retained)
                contradictions.extend(found)
                uncertainties.extend(unresolved)
            match[key] = kept
    order = lambda row: (  # noqa: E731 - concise stable audit ordering
        str(row["target"]),
        str(row["section"]),
        str(row["source_path"]),
        str(row["endpoint"]),
        str(row["line"]),
    )
    return filtered, sorted(contradictions, key=order), sorted(uncertainties, key=order)
