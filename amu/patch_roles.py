"""Source-only role and test-edit provenance for concrete patch reports."""

import ast
import fnmatch
import hashlib
from pathlib import PurePosixPath

from amu.outbound import redact

ROLES = ("production", "verification", "documentation", "uncertain")


def test_declarations(tree):
    """Use the same declarations as the bounded recommendation inventory."""
    result = {}
    for node in tree.body if tree else []:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            result[node.name] = node
        elif isinstance(node, ast.ClassDef) and (node.name.startswith("Test") or any(
                isinstance(base, ast.Name) and base.id == "TestCase" or
                isinstance(base, ast.Attribute) and base.attr == "TestCase" for base in node.bases)):
            result.update({f"{node.name}.{child.name}": child for child in node.body
                           if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name.startswith("test_")})
    return result


def file_role(path, before, after, tests, config):
    from amu.plan import _is_test_path

    if PurePosixPath(path).suffix in {".md", ".markdown"}:
        return {"role": "documentation", "basis": ["Validated Markdown content-only patch adapter"],
                "uncertainty": "Prose or fenced examples may still require review."}
    roots = config.get("testpaths", [])
    configured = any(path.startswith(root.rstrip("/") + "/") or path == root for root in roots)
    declarations = bool(test_declarations(before) or test_declarations(after))
    discovered = _is_test_path(path)
    # A directory becomes a test root only with parsed test declarations, not
    # because a helper happens to have a test-like filename.
    corroborated = path.startswith("tests/") and any(
        source["path"].startswith("tests/") and test_declarations(source["tree"]) for source in tests)
    basis = []
    if configured:
        basis.append("Baseline pyproject.toml [tool.pytest.ini_options].testpaths")
    if discovered:
        basis.append("AMU baseline test-inventory path convention")
    if declarations:
        basis.append("Parsed test function or Test/TestCase method declaration")
    if corroborated:
        basis.append("tests/ directory corroborated by parsed baseline test declarations")
    if configured or discovered and (declarations or corroborated):
        return {"role": "verification", "basis": basis,
                "uncertainty": "Role is inferred from source/configuration; collection and helper execution are not proved."}
    if discovered or declarations:
        return {"role": "uncertain", "basis": basis,
                "uncertainty": "Test-like naming alone does not establish collection or a verification role."}
    module = path.removesuffix(".py").replace("/", ".")
    packaged = any(fnmatch.fnmatchcase(module, pattern) for pattern in config.get("package_include", []))
    syntax = any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign))
                 for tree in (before, after) if tree for node in tree.body)
    if packaged or syntax:
        return {"role": "production", "basis": [
            "Baseline setuptools package include rule" if packaged else
            "Executable Python declarations outside corroborated test scope"],
            "uncertainty": "Inferred production role; runtime entry points and custom test collection are unverified."}
    return {"role": "uncertain", "basis": ["Parsed Python outside corroborated test scope"],
            "uncertainty": "No production or verification declaration basis established."}


def _snapshot(node):
    if node is None:
        return None
    assertions = [{"line": item.lineno, "expression": redact(ast.unparse(item.test))[:200],
                   "ast_sha256": hashlib.sha256(ast.dump(item.test).encode()).hexdigest()}
                  for item in ast.walk(node) if isinstance(item, ast.Assert)]
    return {"line": node.lineno, "end_line": node.end_lineno,
            "ast_sha256": hashlib.sha256(ast.dump(node).encode()).hexdigest(),
            "assertions": assertions}


def test_provenance(before, after, *, changed_file=False):
    old, new = _snapshot(before), _snapshot(after)
    status = ("added" if old is None else "removed" if new is None else
              "unchanged" if old["ast_sha256"] == new["ast_sha256"] else "modified")
    return {"status": status, "before": old, "after": new, "changed_file": changed_file,
            "baseline_evidence_only": True,
            "independent_proof": False,
            "note": "Unchanged syntax is not an executed protected check. Candidate assertions are not independent proof of their expectations."}
