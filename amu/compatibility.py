"""Bounded Python call binding checks. Never imports or executes project code.

This checks current direct calls against old and new *declared* signatures.
It does not prove runtime behavior, type/return contracts, or public API safety.
"""

from __future__ import annotations

import ast
import inspect
import subprocess
from pathlib import PurePosixPath

from amu import source_evidence

_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_MAX_BYTES = 2_000_000
_MAX_CALLERS = 100
_MAX_CALLS = 1000
_VALUE = object()


def _parts(target: str) -> tuple[str, str]:
    path, sep, symbol = target.partition("#")
    p = PurePosixPath(path)
    if not sep or p.is_absolute() or ".." in p.parts or p.suffix != ".py" or not symbol or "#" in symbol:
        raise ValueError("use a repository-relative Python path#qualified_symbol")
    return path, symbol


def _git(repo: str, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise ValueError("baseline revision or source blob is unavailable")
    return result.stdout.strip()


def _definition(tree: ast.AST, symbol: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    # Methods/descriptors and decorated/redefined functions require a resolver
    # with runtime semantics. Do not infer binding from their printed names.
    hits = [n for n in getattr(tree, "body", []) if isinstance(n, _FUNCTIONS) and n.name == symbol]
    if not symbol.isidentifier() or len(hits) != 1 or hits[0].decorator_list:
        raise ValueError("target is not one undecorated module-level Python function")
    return hits[0]


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> inspect.Signature:
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    first_default = len(positional) - len(args.defaults)
    params = [inspect.Parameter(arg.arg,
              inspect.Parameter.POSITIONAL_ONLY if i < len(args.posonlyargs) else inspect.Parameter.POSITIONAL_OR_KEYWORD,
              default=_VALUE if i >= first_default else inspect.Parameter.empty)
              for i, arg in enumerate(positional)]
    if args.vararg:
        params.append(inspect.Parameter(args.vararg.arg, inspect.Parameter.VAR_POSITIONAL))
    params.extend(inspect.Parameter(arg.arg, inspect.Parameter.KEYWORD_ONLY,
                  default=_VALUE if default is not None else inspect.Parameter.empty)
                  for arg, default in zip(args.kwonlyargs, args.kw_defaults))
    if args.kwarg:
        params.append(inspect.Parameter(args.kwarg.arg, inspect.Parameter.VAR_KEYWORD))
    return inspect.Signature(params)


def _rebound(tree: ast.AST, base: str) -> bool:
    """Reject competing module bindings, including conditional assignments."""
    bindings = []
    class Bindings(ast.NodeVisitor):
        def visit_FunctionDef(self, node):  # noqa: N802
            bindings.append(node.name)
        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef

        def visit_Name(self, node):  # noqa: N802
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                bindings.append(node.id)

        def visit_alias(self, node):  # noqa: N802
            bindings.append(node.asname or node.name.split(".")[0])
    Bindings().visit(tree)
    # One static import or one local definition is supported. All competing
    # writes invalidate this identity rather than selecting the last one.
    return bindings.count(base) != 1


def _bind(signature: inspect.Signature, call: ast.Call) -> str | None:
    try:
        keys = [kw.arg for kw in call.keywords]
        if len(set(keys)) != len(keys):
            return "duplicate keyword argument"
        signature.bind(*([_VALUE] * len(call.args)), **{kw.arg: _VALUE for kw in call.keywords})
    except TypeError as exc:
        return str(exc)
    return None


def assess(repo: str, target: str, base: str, callers: list[str]) -> dict:
    report = {"target": target, "base": base, "scope": "static_argument_binding_only",
              "runtime_compatible": None, "calls": [], "unknown": [],
              "limitations": ["Only supplied current caller scopes are checked, not every runtime consumer.",
                              "Return values, types, defaults' behavior, exceptions, monkey-patching and native code are not verified."]}
    try:
        path, symbol = _parts(target)
        sha = _git(repo, "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}")
        report["base"] = sha
        blob = f"{sha}:{path}"
        if int(_git(repo, "cat-file", "-s", blob)) > _MAX_BYTES:
            raise ValueError("baseline source exceeds analysis byte limit")
        old_tree = ast.parse(_git(repo, "show", blob))
        current = source_evidence._source_tree(repo, path)
        if current is None:
            raise ValueError("current target source is missing, unreadable or unsupported")
        new_tree = current[0]
        before = _signature(_definition(old_tree, symbol))
        after = _signature(_definition(new_tree, symbol))
        if _rebound(old_tree, symbol) or _rebound(new_tree, symbol):
            raise ValueError("target has competing module bindings")
    except (ValueError, SyntaxError, OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
        report["unknown"].append({"target": target, "reason": str(exc)})
        return report
    unique = sorted(set(callers))
    if not unique:
        report["unknown"].append({"target": target, "reason": "no caller scopes supplied; absence of input is not compatibility proof"})
    if len(unique) > _MAX_CALLERS:
        report["unknown"].append({"reason": "caller scope limit reached", "omitted": len(unique) - _MAX_CALLERS})
    for caller in unique[:_MAX_CALLERS]:
        try:
            caller_path, caller_name = _parts(caller)
            parsed = source_evidence._source_tree(repo, caller_path)
            if parsed is None:
                raise ValueError("caller source unavailable")
            tree = parsed[0]
            scope = source_evidence._qualified_definition(tree, caller_name)
            if not isinstance(scope, _FUNCTIONS):
                raise ValueError("caller function scope unavailable")
            count = 0
            for call in source_evidence._scoped_calls(tree, scope.lineno, scope.end_lineno):
                chain = source_evidence._dotted_expr(call.func)
                if (not chain or _rebound(tree, chain[0]) or
                        source_evidence._call_identity(tree, caller_path, path, symbol, call, scope.lineno, scope.end_lineno) != "verified"):
                    continue
                count += 1
                if len(report["calls"]) >= _MAX_CALLS:
                    raise ValueError("call-site limit reached; remaining sites were not assessed")
                row = {"caller": caller, "line": call.lineno, "provenance": "parser", "status": "unknown", "newly_invalid": False}
                if any(isinstance(a, ast.Starred) for a in call.args) or any(k.arg is None for k in call.keywords):
                    row["reason"] = "dynamic *args or **kwargs cannot be bound statically"
                    report["unknown"].append({"target": caller, "line": call.lineno, "reason": row["reason"]})
                else:
                    old_error, new_error = _bind(before, call), _bind(after, call)
                    row.update(status="binding_invalid" if new_error else "binding_valid",
                               before_error=old_error, after_error=new_error,
                               newly_invalid=old_error is None and new_error is not None)
                report["calls"].append(row)
            if not count:
                raise ValueError("no unambiguous static call to this exact target in the supplied scope")
        except (ValueError, SyntaxError) as exc:
            report["unknown"].append({"target": caller, "reason": str(exc)})
    return report
