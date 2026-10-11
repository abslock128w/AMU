"""Opt-in proof that a real Entire graph follows cross-file edits in a tiny repo.

The regular test suite uses recorded graph responses on purpose, so it cannot
prove behavior of the installed graph provider after a source file changes.
This module creates an isolated, disposable Git repository instead.  It never
touches the AMU checkout or a user project.

Run deliberately, with the graph binary made explicit::

    AMU_RUN_EXTERNAL_GRAPH_REFRESH=1 \\
    AMU_EXTERNAL_ENTIRE_BIN=/absolute/path/to/entire \\
    python -m pytest -q tests/test_external_graph_refresh.py

The test queries the live worktree (never ``--head``): it proves that a
cross-file ``CALLS`` edge is present, disappears after the source call is
removed, and appears for a newly added caller.  It is intentionally opt-in
because it writes the real provider's cache outside this repository.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from amu import cli


def _external_binary() -> Path:
    """Return a deliberately supplied real graph executable or skip safely."""
    if os.environ.get("AMU_RUN_EXTERNAL_GRAPH_REFRESH") != "1":
        pytest.skip(
            "set AMU_RUN_EXTERNAL_GRAPH_REFRESH=1 and AMU_EXTERNAL_ENTIRE_BIN=/absolute/path/to/entire "
            "to validate real graph refreshes"
        )
    raw_binary = os.environ.get("AMU_EXTERNAL_ENTIRE_BIN")
    if not raw_binary:
        pytest.fail("AMU_RUN_EXTERNAL_GRAPH_REFRESH=1 requires AMU_EXTERNAL_ENTIRE_BIN")
    binary = Path(raw_binary).expanduser()
    if not binary.is_absolute():
        pytest.fail("AMU_EXTERNAL_ENTIRE_BIN must be an absolute path")
    binary = binary.resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        pytest.fail(f"AMU_EXTERNAL_ENTIRE_BIN is not an executable file: {binary}")
    version = _run(binary, Path.cwd(), ["version"], timeout=30).strip()
    assert version and "fake" not in version.lower(), f"expected a real Entire binary, got {version!r}"
    return binary


def _run(binary: Path, repo: Path, args: list[str], *, timeout: int = 180) -> str:
    """Run a graph command without a shell and fail with useful provider output."""
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


def _json_graph(binary: Path, repo: Path, args: list[str]) -> dict[str, Any]:
    raw = _run(binary, repo, args)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        pytest.fail(f"entire graph emitted non-JSON output for {' '.join(args)}: {exc}: {raw[:1000]!r}")
    assert isinstance(payload, dict), f"expected object graph response, got {type(payload).__name__}"
    return payload


def _git(repo: Path, *args: str) -> None:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)
    if result.returncode:
        pytest.fail(f"git {' '.join(args)} failed: {result.stderr[-1000:]}")


def _write(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _callers(impact: dict[str, Any]) -> set[tuple[str, str]]:
    """Return resolved direct CALLS endpoints as (relative file, function name)."""
    callers = impact.get("callers")
    entries = callers.get("entries") if isinstance(callers, dict) else []
    result: set[tuple[str, str]] = set()
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or entry.get("relation") != "CALLS" or entry.get("depth") != 1:
            continue
        endpoint = entry.get("endpoint")
        if isinstance(endpoint, dict):
            path = endpoint.get("file_path")
            name = endpoint.get("name")
            if isinstance(path, str) and isinstance(name, str):
                result.add((path, name))
    return result


def _edge_identities(delta: dict, kind: str) -> list[dict]:
    """Compare exact relationship identity, while checking added evidence fields."""
    edges = delta[kind]
    for edge in edges:
        assert edge.get("provenance") in {"parser", "compiler/indexer", "heuristic", "runtime", "unknown"}
        assert edge.get("confidence") in {"sound", "guessed", "requires_verification", "unknown"}
    return [{key: edge[key] for key in ("target", "related", "relation", "direction", "depth")} for edge in edges]


def _impact_target(binary: Path, repo: Path) -> dict[str, Any]:
    # ``--head`` is deliberately absent: the product must reflect uncommitted
    # edits made by an agent before it decides what else could break.
    return _json_graph(
        binary,
        repo,
        [
            "impact",
            "--repo",
            str(repo),
            "--symbol",
            "target",
            "--file",
            "pkg/target.py",
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


def _refresh_provider(binary: Path, repo: Path) -> dict[str, Any]:
    """Refresh committed cache plus current-worktree snapshot before a live query."""
    _json_graph(binary, repo, ["index", "--repo", str(repo), "--format", "json"])
    # Snapshot is the durable post-edit graph record.  The worktree flag is
    # essential: index is a committed-tree cache by design.
    _run(binary, repo, ["snapshot", "--repo", str(repo), "--worktree", "--format", "compact-ndjson"])
    return _impact_target(binary, repo)


def test_real_entire_graph_refresh_tracks_added_and_removed_cross_file_calls(tmp_path: Path) -> None:
    """A fresh live graph must surface both edge removal and edge addition."""
    binary = _external_binary()
    repo = tmp_path / "tiny-cross-file-graph"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "amu-test@example.invalid")
    _git(repo, "config", "user.name", "AMU graph-refresh test")
    _write(repo / "pkg/__init__.py", "")
    _write(repo / "pkg/target.py", "def target() -> int:\n    return 1\n")
    _write(
        repo / "pkg/caller.py",
        "from .target import target\n\n\ndef original_caller() -> int:\n    return target()\n",
    )
    _git(repo, "add", "pkg")
    _git(repo, "commit", "-qm", "initial cross-file call")

    initial = _refresh_provider(binary, repo)
    assert ("pkg/caller.py", "original_caller") in _callers(initial), initial

    # Simulate an agent's completed bug fix: remove the direct relationship.
    _write(
        repo / "pkg/caller.py",
        "from .target import target\n\n\ndef original_caller() -> int:\n    return 0\n",
    )
    after_removal = _refresh_provider(binary, repo)
    assert ("pkg/caller.py", "original_caller") not in _callers(after_removal), after_removal

    # Then simulate a new relationship introduced by a follow-up fix.
    _write(
        repo / "pkg/added.py",
        "from .target import target\n\n\ndef newly_added_caller() -> int:\n    return target()\n",
    )
    after_addition = _refresh_provider(binary, repo)
    callers = _callers(after_addition)
    assert ("pkg/caller.py", "original_caller") not in callers, after_addition
    assert ("pkg/added.py", "newly_added_caller") in callers, after_addition


def test_real_amu_refresh_reports_added_and_removed_cross_file_calls(tmp_path: Path, monkeypatch) -> None:
    """Exercise the user-facing command against the real graph provider end to end."""
    binary = _external_binary()
    # The normal suite intentionally hides Homebrew's graph binary.  This test
    # has an explicit opt-in executable, so expose only that directory to AMU.
    monkeypatch.setenv("PATH", f"{binary.parent}{os.pathsep}{os.environ['PATH']}")
    repo = tmp_path / "tiny-amu-refresh"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "amu-test@example.invalid")
    _git(repo, "config", "user.name", "AMU graph-refresh test")
    _write(repo / "pkg/__init__.py", "")
    _write(repo / "pkg/target.py", "def target() -> int:\n    return 1\n")
    _write(
        repo / "pkg/caller.py",
        "from .target import target\n\n\ndef original_caller() -> int:\n    return target()\n",
    )
    _git(repo, "add", "pkg")
    _git(repo, "commit", "-qm", "initial cross-file call")

    mapped = CliRunner().invoke(
        cli.app,
        ["map", "--symbol", "pkg/target.py#target", "--change", "body", "--json", "--repo", str(repo)],
    )
    assert mapped.exit_code == 0, mapped.output

    # A bug fix removes one old caller and adds a new caller in a different file.
    _write(
        repo / "pkg/caller.py",
        "from .target import target\n\n\ndef original_caller() -> int:\n    return 0\n",
    )
    _write(
        repo / "pkg/added.py",
        "from .target import target\n\n\ndef newly_added_caller() -> int:\n    return target()\n",
    )

    refreshed = CliRunner().invoke(
        cli.app,
        ["refresh", "--symbol", "pkg/target.py#target", "--change", "body", "--json", "--repo", str(repo)],
    )

    assert refreshed.exit_code == 0, refreshed.output
    payload = json.loads(refreshed.stdout)
    assert payload["complete"] is True
    assert payload["sync"]["snapshot_ref"] == "worktree"
    # A provider can also expose a DATA_FLOWS relation for the same call.  The
    # regression contract is that the direct cross-file CALLS edges are
    # present; it must not suppress other independently resolved semantics.
    assert {
        "target": "pkg/target.py#target",
        "related": "pkg/added.py#newly_added_caller",
        "relation": "CALLS",
        "direction": "in",
        "depth": 1,
    } in _edge_identities(payload["edge_delta"], "added")
    assert {
        "target": "pkg/target.py#target",
        "related": "pkg/caller.py#original_caller",
        "relation": "CALLS",
        "direction": "in",
        "depth": 1,
    } in _edge_identities(payload["edge_delta"], "removed")


def test_real_amu_refresh_tracks_a_renamed_target_and_new_cross_file_caller(tmp_path: Path, monkeypatch) -> None:
    """A rename must query the new target while preserving the old-map comparison identity."""
    binary = _external_binary()
    monkeypatch.setenv("PATH", f"{binary.parent}{os.pathsep}{os.environ['PATH']}")
    repo = tmp_path / "tiny-amu-rename-refresh"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "amu-test@example.invalid")
    _git(repo, "config", "user.name", "AMU graph-refresh test")
    _write(repo / "pkg/__init__.py", "")
    _write(repo / "pkg/target.py", "def target() -> int:\n    return 1\n")
    _write(
        repo / "pkg/caller.py",
        "from .target import target\n\n\ndef original_caller() -> int:\n    return target()\n",
    )
    _git(repo, "add", "pkg")
    _git(repo, "commit", "-qm", "initial target")

    mapped = CliRunner().invoke(
        cli.app,
        ["map", "--symbol", "pkg/target.py#target", "--change", "rename", "--json", "--repo", str(repo)],
    )
    assert mapped.exit_code == 0, mapped.output

    # A real bug fix renames the target, updates the existing caller, and adds
    # a second caller.  The old target must disappear from editable source;
    # AMU refuses to call this a rename if it was merely copied.
    _write(repo / "pkg/target.py", "def renamed_target() -> int:\n    return 2\n")
    _write(
        repo / "pkg/caller.py",
        "from .target import renamed_target\n\n\ndef original_caller() -> int:\n    return renamed_target()\n",
    )
    _write(
        repo / "pkg/added.py",
        "from .target import renamed_target\n\n\ndef newly_added_caller() -> int:\n    return renamed_target()\n",
    )

    refreshed = CliRunner().invoke(
        cli.app,
        [
            "refresh",
            "--symbol",
            "pkg/target.py#target",
            "--change",
            "rename",
            "--new-symbol",
            "pkg/target.py#renamed_target",
            "--json",
            "--repo",
            str(repo),
        ],
    )

    assert refreshed.exit_code == 0, refreshed.output
    payload = json.loads(refreshed.stdout)
    assert payload["complete"] is True
    assert payload["target"] == "pkg/target.py#target"
    assert payload["live_target"] == "pkg/target.py#renamed_target"
    assert payload["sync"]["snapshot_ref"] == "worktree"
    # The delta deliberately remains anchored to the old target.  That lets an
    # agent compare its approved pre-edit map directly with the renamed live
    # node, instead of receiving a misleading remove-plus-unrelated-add pair.
    assert {
        "target": "pkg/target.py#target",
        "related": "pkg/caller.py#original_caller",
        "relation": "CALLS",
        "direction": "in",
        "depth": 1,
    } not in _edge_identities(payload["edge_delta"], "removed")
    assert {
        "target": "pkg/target.py#target",
        "related": "pkg/added.py#newly_added_caller",
        "relation": "CALLS",
        "direction": "in",
        "depth": 1,
    } in _edge_identities(payload["edge_delta"], "added")


def test_real_amu_refresh_reports_source_verified_target_removal(tmp_path: Path, monkeypatch) -> None:
    """A deleted Python focus produces removed pre-edit edges, never a stale live node."""
    binary = _external_binary()
    monkeypatch.setenv("PATH", f"{binary.parent}{os.pathsep}{os.environ['PATH']}")
    repo = tmp_path / "tiny-amu-remove-refresh"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "amu-test@example.invalid")
    _git(repo, "config", "user.name", "AMU graph-refresh test")
    _write(repo / "pkg/__init__.py", "")
    _write(repo / "pkg/target.py", "def target() -> int:\n    return 1\n")
    _write(
        repo / "pkg/caller.py",
        "from .target import target\n\n\ndef original_caller() -> int:\n    return target()\n",
    )
    _git(repo, "add", "pkg")
    _git(repo, "commit", "-qm", "initial target")

    mapped = CliRunner().invoke(
        cli.app,
        ["map", "--symbol", "pkg/target.py#target", "--change", "remove", "--json", "--repo", str(repo)],
    )
    assert mapped.exit_code == 0, mapped.output

    # Keep the module and caller syntactically valid while deleting the exact
    # mapped definition.  This is the provider path that cannot resolve a
    # deleted focus; AMU independently verifies the absence in Python AST.
    _write(repo / "pkg/target.py", "def replacement() -> int:\n    return 2\n")
    _write(repo / "pkg/caller.py", "def original_caller() -> int:\n    return 0\n")

    refreshed = CliRunner().invoke(
        cli.app,
        [
            "refresh",
            "--symbol",
            "pkg/target.py#target",
            "--change",
            "remove",
            "--json",
            "--repo",
            str(repo),
        ],
    )

    assert refreshed.exit_code == 0, refreshed.output
    payload = json.loads(refreshed.stdout)
    assert payload["complete"] is True
    assert payload["graph"]["status"] == "removed"
    assert payload["graph"]["focus"] is None
    assert payload["edge_delta"]["added"] == []
    assert {
        "target": "pkg/target.py#target",
        "related": "pkg/caller.py#original_caller",
        "relation": "CALLS",
        "direction": "in",
        "depth": 1,
    } in _edge_identities(payload["edge_delta"], "removed")
