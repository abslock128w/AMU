"""Opt-in, real-CLI validation against a NumPy source checkout.

This module is deliberately skipped in the ordinary AMU test suite.  It never
imports or executes NumPy; the independent oracle is ``ast`` over the checked
out source, and the system under test is the real ``entire graph`` executable.

Run it only against a local NumPy checkout::

    AMU_RUN_EXTERNAL_GRAPH=1 \\
    AMU_NUMPY_REPO=/absolute/path/to/numpy \\
    AMU_EXTERNAL_ENTIRE_BIN=/absolute/path/to/entire \\
    python -m pytest -q tests/test_external_numpy_graph.py

Set ``AMU_EXTERNAL_GRAPH_STRICT=1`` to make a detected documentation-only
CALLS edge fail the run.  Without strict mode it is reported as an expected
failure so a known provider defect remains visible without making this
repository's regular test suite red.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from amu import cli, state
from amu.source_evidence import filter_contradicted_python_call_edges

_SOURCE_CANDIDATES = (
    "numpy/lib/_shape_base_impl.py",  # NumPy >= 2
    "numpy/lib/shape_base.py",  # NumPy 1.x
)
_REQUIRED_NAMES = ("_make_along_axis_idx", "take_along_axis", "put_along_axis")
_DOC_CALL = re.compile(r"(?:\b(?:np|numpy)\.)?([A-Za-z_]\w*)\s*\(")
_SCIMATH_SOURCE = "numpy/lib/_scimath_impl.py"
_TYPE_CHECK_SOURCE = "numpy/lib/_type_check_impl.py"
_SCIMATH_CALLER = "_fix_real_lt_zero"
_TYPE_CHECK_CALLEE = "isreal"
_TYPE_CHECK_MODULE = "numpy.lib._type_check_impl"
_ISOLATED_SOURCE = "numpy/_core/_internal.py"
_ISOLATED_FUNCTION = "npy_ctypes_check"


@dataclass(frozen=True)
class _Function:
    name: str
    line: int
    end_line: int
    node: ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True)
class _NumpyGraph:
    repo: Path
    binary: Path
    source: Path
    source_rel: str
    functions: dict[str, _Function]


def _command(binary: Path, repo: Path, args: list[str], timeout: int = 180) -> str:
    """Run only the supplied graph executable; never invoke a shell or NumPy."""
    try:
        result = subprocess.run(
            [str(binary), "graph", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"entire graph timed out after {timeout}s: {' '.join(exc.cmd)}")
    if result.returncode:
        pytest.fail(
            f"entire graph exited {result.returncode}: {' '.join(args)}\n"
            f"stderr: {result.stderr[-2000:]}\nstdout: {result.stdout[-2000:]}"
        )
    return result.stdout


def test_numpy_api_edit_to_reviewed_rst_docs(numpy_graph, tmp_path, monkeypatch):
    """Exercise real diff + worktree API detection + review/save in a local clone."""
    repo = tmp_path / "numpy-review"
    subprocess.run(
        ["git", "clone", "--local", "--quiet", str(numpy_graph.repo), str(repo)],
        check=True, capture_output=True, text=True,
    )
    monkeypatch.setenv("PATH", str(numpy_graph.binary.parent) + os.pathsep + os.environ["PATH"])
    state.write_json("config.json", {**state.config(str(repo)), "docs_dir": "doc"}, str(repo))
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    state.write_json("contract.json", {"base_sha": head}, str(repo))
    source = repo / _SCIMATH_SOURCE
    before = source.read_text()
    assert "def sqrt(x):" in before
    source.write_text(before.replace("def sqrt(x):", "def sqrt(x, *, strict=False):", 1))
    target = f"{_SCIMATH_SOURCE}#sqrt"
    docfile = "doc/source/reference/routines.emath.rst"
    state.write_json("doc-map.json", {target: [{"file": docfile, "section": "Functions"}]}, str(repo))
    runner = CliRunner()

    def run(*args):
        result = runner.invoke(cli.app, ["docs", *args, "--json", "--repo", str(repo)])
        assert result.exit_code == 0, result.stdout
        return json.loads(result.stdout)

    listed = run("--mode", "draft")
    candidate = next(row for row in listed["candidates"] if row["candidate"] == target)
    assert "strict=False" in candidate["new"]
    candidate_id = candidate["id"]
    original_docs = (repo / docfile).read_text()
    prepared = run("--prepare", candidate_id)
    draft_path = Path(prepared["prepared"]["file"])
    draft_path.write_text(
        draft_path.read_text()
        + "\nThe experimental sqrt signature accepts strict=False as a keyword-only argument.\n"
        + f"Source: ``{target}``.\n"
    )
    assert (repo / docfile).read_text() == original_docs
    applied = run("--apply", candidate_id, "--review")
    assert applied["applied"] == [candidate_id]
    assert "experimental sqrt signature" in (repo / docfile).read_text()
    assert applied["docs_graph"]["graph_hash"] != listed["docs_graph"]["graph_hash"]
    graph = state.read_json("docs-graph.json", str(repo))
    assert any(edge["relation"] == "MENTIONS" and edge["target"] == target for edge in graph["edges"])


def _json_command(graph: _NumpyGraph, args: list[str]) -> dict[str, Any]:
    output = _command(graph.binary, graph.repo, args)
    try:
        value = json.loads(output)
    except json.JSONDecodeError as exc:
        pytest.fail(f"entire graph emitted non-JSON output for {' '.join(args)}: {exc}: {output[:1000]!r}")
    assert isinstance(value, dict), f"entire graph returned {type(value).__name__}, not a JSON object"
    return value


def _module_functions(source: Path) -> dict[str, _Function]:
    """Parse source only; do not import a module from the external checkout."""
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    return {
        node.name: _Function(node.name, node.lineno, node.end_lineno, node)
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


class _CallsInFunctionBody(ast.NodeVisitor):
    """Collect direct source calls without traversing nested function definitions."""

    def __init__(self, root: ast.FunctionDef | ast.AsyncFunctionDef):
        self.root = root
        self.names: set[str] = set()

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 - AST visitor API
        if isinstance(node.func, ast.Name):
            self.names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            self.names.add(node.func.attr)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802 - AST visitor API
        if node is self.root:
            for statement in node.body:
                self.visit(statement)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802 - AST visitor API
        if node is self.root:
            for statement in node.body:
                self.visit(statement)


def _body_call_names(function: _Function) -> set[str]:
    visitor = _CallsInFunctionBody(function.node)
    visitor.visit(function.node)
    return visitor.names


def _documented_call_names(function: _Function) -> set[str]:
    docstring = ast.get_docstring(function.node, clean=False) or ""
    return set(_DOC_CALL.findall(docstring))


def _imports_name_from(source: Path, module: str, name: str) -> bool:
    """Return whether a source module directly imports ``name`` from ``module``.

    This is deliberately an AST oracle rather than an import: the opt-in
    validation must never execute code from an external NumPy checkout.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == module
        and any(alias.name == name and alias.asname is None for alias in node.names)
        for node in tree.body
    )


def _repository_direct_call_sites(repo: Path, name: str) -> list[str]:
    """Find executable direct calls by leaf name without importing NumPy.

    This is a bounded oracle for the isolated-function fixture.  It does not
    claim to resolve reflection or aliases; it proves only the static direct
    CALLS category that the graph response reports as empty.
    """
    sites: list[str] = []
    for source in sorted((repo / "numpy").rglob("*.py")):
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        except (OSError, SyntaxError, UnicodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            function = _Function(node.name, node.lineno, node.end_lineno, node)
            if name in _body_call_names(function):
                sites.append(f"{source.relative_to(repo)}:{node.lineno}#{node.name}")
    return sites


def _entries(impact: dict[str, Any], section: str) -> list[dict[str, Any]]:
    group = impact.get(section)
    entries = group.get("entries") if isinstance(group, dict) else None
    return [entry for entry in entries if isinstance(entry, dict)] if isinstance(entries, list) else []


def _endpoint(entry: dict[str, Any]) -> dict[str, Any]:
    value = entry.get("endpoint")
    return value if isinstance(value, dict) else {}


def _call_site_line(entry: dict[str, Any]) -> int | None:
    site = entry.get("call_site")
    if not isinstance(site, dict):
        return None
    for field in ("line", "start_line"):
        value = site.get(field)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


@pytest.fixture(scope="module")
def numpy_graph() -> _NumpyGraph:
    """Require deliberate opt-in because this queries a real external checkout."""
    if os.environ.get("AMU_RUN_EXTERNAL_GRAPH") != "1":
        pytest.skip(
            "set AMU_RUN_EXTERNAL_GRAPH=1 with AMU_NUMPY_REPO and AMU_EXTERNAL_ENTIRE_BIN to run real graph checks"
        )

    raw_repo = os.environ.get("AMU_NUMPY_REPO")
    raw_binary = os.environ.get("AMU_EXTERNAL_ENTIRE_BIN")
    if not raw_repo or not raw_binary:
        pytest.fail("AMU_RUN_EXTERNAL_GRAPH=1 requires AMU_NUMPY_REPO and AMU_EXTERNAL_ENTIRE_BIN")

    repo_input = Path(raw_repo).expanduser()
    binary_input = Path(raw_binary).expanduser()
    if not repo_input.is_absolute() or not binary_input.is_absolute():
        pytest.fail("AMU_NUMPY_REPO and AMU_EXTERNAL_ENTIRE_BIN must be absolute paths")
    repo = repo_input.resolve()
    binary = binary_input.resolve()
    if not repo.is_dir() or not (repo / ".git").exists():
        pytest.fail(f"AMU_NUMPY_REPO must be a local NumPy git checkout: {repo}")
    if not binary.is_file() or not os.access(binary, os.X_OK):
        pytest.fail(f"AMU_EXTERNAL_ENTIRE_BIN is not an executable file: {binary}")

    pyproject = repo / "pyproject.toml"
    if not pyproject.is_file():
        pytest.fail(f"NumPy checkout is missing pyproject.toml: {repo}")
    metadata = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = metadata.get("project")
    if not isinstance(project, dict) or project.get("name") != "numpy":
        pytest.fail(f"AMU_NUMPY_REPO is not a NumPy checkout: {repo}")

    source_rel = next((candidate for candidate in _SOURCE_CANDIDATES if (repo / candidate).is_file()), None)
    if source_rel is None:
        pytest.skip("this NumPy revision does not contain a supported shape_base source layout")
    source = repo / source_rel
    functions = _module_functions(source)
    missing = [name for name in _REQUIRED_NAMES if name not in functions]
    if missing:
        pytest.skip(f"this NumPy revision lacks the stable along-axis relation fixture: {', '.join(missing)}")

    version = _command(binary, repo, ["version"], timeout=30).strip()
    assert version and "fake" not in version.lower(), (
        f"AMU_EXTERNAL_ENTIRE_BIN must select a real graph binary, got {version!r}"
    )
    return _NumpyGraph(repo, binary, source, source_rel, functions)


@pytest.fixture(scope="module")
def along_axis_impacts(numpy_graph: _NumpyGraph) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read live worktree facts; ``--head`` is intentionally never used here."""
    target = numpy_graph.functions["_make_along_axis_idx"]
    take = numpy_graph.functions["take_along_axis"]
    common = ["--repo", str(numpy_graph.repo), "--depth", "1", "--limit", "50", "--profile", "full", "--format", "json"]
    callers = _json_command(numpy_graph, ["impact", "--symbol", f"{numpy_graph.source_rel}:{target.line}", *common])
    callees = _json_command(numpy_graph, ["impact", "--symbol", f"{numpy_graph.source_rel}:{take.line}", *common])
    return callers, callees


def test_real_numpy_graph_has_source_backed_along_axis_callers(
    numpy_graph: _NumpyGraph, along_axis_impacts: tuple[dict[str, Any], dict[str, Any]]
) -> None:
    """Prove two independently parsed, direct calls are present as graph CALLS edges."""
    callers_impact, _ = along_axis_impacts
    target = numpy_graph.functions["_make_along_axis_idx"]
    assert Path(str(callers_impact.get("repo_root", ""))).resolve() == numpy_graph.repo
    assert callers_impact.get("profile") == "full"
    assert callers_impact.get("disambiguation_required") is False
    focus = callers_impact.get("focus")
    assert isinstance(focus, dict), f"no resolved graph focus: {callers_impact}"
    assert focus.get("file_path") == numpy_graph.source_rel
    assert focus.get("name") == target.name
    assert focus.get("start_line") == target.line

    partial_paths = {
        failure.get("file_path") for failure in callers_impact.get("partial_failures", []) if isinstance(failure, dict)
    }
    assert numpy_graph.source_rel not in partial_paths, (
        "the source oracle file was not successfully parsed by the graph"
    )

    entries = _entries(callers_impact, "callers")
    for caller_name in ("take_along_axis", "put_along_axis"):
        caller = numpy_graph.functions[caller_name]
        # Independent source oracle: both functions contain this direct AST call.
        assert target.name in _body_call_names(caller), f"source no longer supports {caller_name} -> {target.name}"
        matches = [
            entry
            for entry in entries
            if entry.get("relation") == "CALLS"
            and entry.get("depth") == 1
            and _endpoint(entry).get("name") == caller_name
            and _endpoint(entry).get("file_path") == numpy_graph.source_rel
        ]
        assert matches, f"missing source-backed direct CALLS edge: {caller_name} -> {target.name}; entries={entries}"
        assert any(
            (line := _call_site_line(entry)) is not None and caller.line <= line <= caller.end_line for entry in matches
        ), f"graph did not locate {caller_name}'s call site inside its source range"


def test_real_numpy_graph_has_source_backed_cross_file_isreal_callee(numpy_graph: _NumpyGraph) -> None:
    """Verify a known cross-file NumPy call against independent source facts.

    ``_fix_real_lt_zero`` has historically imported and called ``isreal`` from
    ``_type_check_impl``.  The source-layout checks intentionally skip rather
    than fail when an upstream NumPy revision legitimately refactors that
    relationship; a matching layout must produce an exact direct CALLS edge.
    """
    caller_source = numpy_graph.repo / _SCIMATH_SOURCE
    callee_source = numpy_graph.repo / _TYPE_CHECK_SOURCE
    if not caller_source.is_file() or not callee_source.is_file():
        pytest.skip("this NumPy revision lacks the scimath/type-check source layout")

    caller = _module_functions(caller_source).get(_SCIMATH_CALLER)
    callee = _module_functions(callee_source).get(_TYPE_CHECK_CALLEE)
    if caller is None or callee is None:
        pytest.skip("this NumPy revision lacks the scimath isreal relation fixture")
    if not _imports_name_from(caller_source, _TYPE_CHECK_MODULE, _TYPE_CHECK_CALLEE):
        pytest.skip("NumPy changed the scimath isreal import layout")
    if _TYPE_CHECK_CALLEE not in _body_call_names(caller):
        pytest.skip("NumPy changed _fix_real_lt_zero's direct isreal call")

    impact = _json_command(
        numpy_graph,
        [
            "impact",
            "--symbol",
            f"{_SCIMATH_SOURCE}:{caller.line}",
            "--repo",
            str(numpy_graph.repo),
            "--depth",
            "1",
            "--limit",
            "50",
            "--profile",
            "fast",
            "--format",
            "json",
        ],
    )
    assert Path(str(impact.get("repo_root", ""))).resolve() == numpy_graph.repo
    assert impact.get("profile") == "fast"
    assert impact.get("disambiguation_required") is False
    focus = impact.get("focus")
    assert isinstance(focus, dict), f"no resolved graph focus: {impact}"
    assert focus.get("file_path") == _SCIMATH_SOURCE
    assert focus.get("name") == _SCIMATH_CALLER
    assert focus.get("start_line") == caller.line

    partial_paths = {
        failure.get("file_path") for failure in impact.get("partial_failures", []) if isinstance(failure, dict)
    }
    assert _SCIMATH_SOURCE not in partial_paths
    assert _TYPE_CHECK_SOURCE not in partial_paths

    matches = [
        entry
        for entry in _entries(impact, "callees")
        if entry.get("relation") == "CALLS"
        and entry.get("depth") == 1
        and _endpoint(entry).get("file_path") == _TYPE_CHECK_SOURCE
        and _endpoint(entry).get("name") == _TYPE_CHECK_CALLEE
        and _endpoint(entry).get("start_line") == callee.line
    ]
    assert matches, (
        "missing source-backed cross-file direct CALLS edge: "
        f"{_SCIMATH_SOURCE}#{_SCIMATH_CALLER} -> {_TYPE_CHECK_SOURCE}#{_TYPE_CHECK_CALLEE}; "
        f"entries={_entries(impact, 'callees')}"
    )
    # A correct raw provider edge must survive AMU's exact source identity
    # check, including absolute imports from within a package directory.
    filtered, contradictions = filter_contradicted_python_call_edges(
        str(numpy_graph.repo), {f"{_SCIMATH_SOURCE}#{_SCIMATH_CALLER}": impact}
    )
    guarded = filtered[f"{_SCIMATH_SOURCE}#{_SCIMATH_CALLER}"]
    assert any(
        entry.get("relation") == "CALLS"
        and _endpoint(entry).get("file_path") == _TYPE_CHECK_SOURCE
        and _endpoint(entry).get("name") == _TYPE_CHECK_CALLEE
        for entry in _entries(guarded, "callees")
    ), f"AMU removed a source-backed absolute import: {contradictions}"


def test_real_numpy_graph_represents_a_source_backed_isolated_function(numpy_graph: _NumpyGraph) -> None:
    """Verify an exact function with no static direct call relationships."""
    source = numpy_graph.repo / _ISOLATED_SOURCE
    if not source.is_file():
        pytest.skip("this NumPy revision lacks the isolated-function source fixture")
    function = _module_functions(source).get(_ISOLATED_FUNCTION)
    if function is None:
        pytest.skip("this NumPy revision lacks the isolated-function definition")
    assert _body_call_names(function) == set()
    assert _repository_direct_call_sites(numpy_graph.repo, _ISOLATED_FUNCTION) == []

    impact = _json_command(
        numpy_graph,
        [
            "impact",
            "--symbol",
            f"{_ISOLATED_SOURCE}:{function.line}",
            "--repo",
            str(numpy_graph.repo),
            "--depth",
            "1",
            "--limit",
            "50",
            "--profile",
            "full",
            "--format",
            "json",
        ],
    )

    focus = impact.get("focus")
    assert isinstance(focus, dict)
    assert focus.get("file_path") == _ISOLATED_SOURCE
    assert focus.get("name") == _ISOLATED_FUNCTION
    assert focus.get("start_line") == function.line
    assert impact.get("degenerate") is True
    assert "no callers" in str(impact.get("degenerate_reason"))
    assert all(_entries(impact, section) == [] for section in ("callers", "callees", "type_consumers", "data_flows"))
    partial_paths = {
        failure.get("file_path") for failure in impact.get("partial_failures", []) if isinstance(failure, dict)
    }
    assert _ISOLATED_SOURCE not in partial_paths


def test_real_numpy_graph_excludes_docstring_only_calls(
    numpy_graph: _NumpyGraph, along_axis_impacts: tuple[dict[str, Any], dict[str, Any]]
) -> None:
    """Catch a false positive when graph parsing mistakes NumPy examples for executable calls."""
    _, callees_impact = along_axis_impacts
    take = numpy_graph.functions["take_along_axis"]
    body_calls = _body_call_names(take)
    documented_only = _documented_call_names(take) - body_calls
    graph_callees = _entries(callees_impact, "callees")
    false_edges = sorted(
        {
            str(_endpoint(entry).get("name"))
            for entry in graph_callees
            if entry.get("relation") == "CALLS" and _endpoint(entry).get("name") in documented_only
        }
    )
    if not false_edges:
        return

    message = (
        "Entire graph reported CALLS edges that occur only in take_along_axis's docstring examples, "
        f"not in executable AST: {false_edges}. Set AMU_EXTERNAL_GRAPH_STRICT=1 to make this provider defect red."
    )
    if os.environ.get("AMU_EXTERNAL_GRAPH_STRICT") == "1":
        pytest.fail(message)
    pytest.xfail(message)


def test_amu_source_guard_removes_numpy_docstring_edges_but_keeps_real_calls(
    numpy_graph: _NumpyGraph, along_axis_impacts: tuple[dict[str, Any], dict[str, Any]]
) -> None:
    """The AMU guard must make a raw provider defect non-actionable.

    This stays green both before and after an upstream parser fix: if the raw
    graph still returns docstring edges, they must be removed and recorded; if
    it no longer does, the executable relationship remains intact.
    """
    _, raw_impact = along_axis_impacts
    take = numpy_graph.functions["take_along_axis"]
    documented_only = _documented_call_names(take) - _body_call_names(take)
    raw_entries = _entries(raw_impact, "callees")
    raw_false_names = {
        str(_endpoint(entry).get("name"))
        for entry in raw_entries
        if entry.get("relation") == "CALLS" and _endpoint(entry).get("name") in documented_only
    }

    filtered, contradictions = filter_contradicted_python_call_edges(str(numpy_graph.repo), {take.name: raw_impact})
    filtered_names = {
        str(_endpoint(entry).get("name"))
        for entry in _entries(filtered[take.name], "callees")
        if entry.get("relation") == "CALLS"
    }
    contradiction_names = {
        str(row["endpoint"]).rsplit(".", 1)[-1] for row in contradictions if row["section"] == "callees"
    }

    assert "_make_along_axis_idx" in filtered_names
    assert not (filtered_names & documented_only)
    assert contradiction_names == raw_false_names


def test_amu_source_guard_preserves_numpy_static_star_reexport(
    numpy_graph: _NumpyGraph, along_axis_impacts: tuple[dict[str, Any], dict[str, Any]]
) -> None:
    """A definition reached through a star reexport is not a proven mismatch."""
    numeric_source = numpy_graph.repo / "numpy/_core/numeric.py"
    numerictypes_source = numpy_graph.repo / "numpy/_core/numerictypes.py"
    if not numeric_source.is_file() or not numerictypes_source.is_file():
        pytest.skip("this NumPy revision lacks the numeric/numerictypes reexport fixture")
    numeric_tree = ast.parse(numeric_source.read_text(encoding="utf-8"))
    assert any(
        isinstance(node, ast.ImportFrom) and node.module == "numerictypes" and node.level == 1
        and any(alias.name == "*" for alias in node.names)
        for node in numeric_tree.body
    )
    numerictypes_tree = ast.parse(numerictypes_source.read_text(encoding="utf-8"))
    assert any(
        isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
        and isinstance(node.value, (ast.List, ast.Tuple))
        and any(isinstance(value, ast.Constant) and value.value == "issubdtype" for value in node.value.elts)
        for node in numerictypes_tree.body
    )
    caller_impact, _ = along_axis_impacts
    target = "numpy/_core/numerictypes.py"
    assert any(
        _endpoint(entry).get("file_path") == target and _endpoint(entry).get("name") == "issubdtype"
        for entry in _entries(caller_impact, "callees")
    ), "provider missed the declared issubdtype reexport target"
    filtered, contradictions = filter_contradicted_python_call_edges(
        str(numpy_graph.repo), {f"{numpy_graph.source_rel}#_make_along_axis_idx": caller_impact}
    )
    guarded = filtered[f"{numpy_graph.source_rel}#_make_along_axis_idx"]
    assert any(
        _endpoint(entry).get("file_path") == target and _endpoint(entry).get("name") == "issubdtype"
        for entry in _entries(guarded, "callees")
    ), f"AMU removed a declared star reexport target: {contradictions}"
