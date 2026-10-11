"""Read-only local repository selection, without implicit MCP authorization.

Registration checks Git metadata only. In particular, they do not run status,
diff, filters, hooks, builds, tests, imports, graph initialization or cloning.
The recorded graph index describes HEAD, not a continuously checked worktree.
Only an explicitly supplied trusted freshness probe can certify the latter.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from amu import entire, process, state_io
from amu.mcp_access import AccessError, _directory

if TYPE_CHECKING:
    from amu.mcp_access import MCPAccessPolicy

MAX_GIT_METADATA_BYTES = 8192
MAX_INDEX_METADATA_BYTES = 2 * 1024 * 1024
GIT_METADATA_TIMEOUT = 10
_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_UNSAFE_CONFIG = r"^(core\.fsmonitor|filter\..*\.(clean|smudge|process))$"


class RepositoryDiagnosticError(ValueError):
    """A local selection failed without revealing unselected filesystem data."""

    def __init__(self, category: str, kind: str, message: str, hint: str):
        super().__init__(message)
        self.category, self.kind, self.hint = category, kind, hint


@dataclass(frozen=True)
class RepositoryMetadata:
    root: str
    head: str | None
    executable_helpers: bool = False


def _git_environment() -> dict[str, str]:
    # Ambient GIT_DIR/WORK_TREE/CONFIG_* cannot redirect an explicit selection.
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_SYSTEM=os.devnull,
                       GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS="0",
                       GIT_TERMINAL_PROMPT="0", GIT_PAGER="cat")
    return environment


def _metadata_query(repo: str, arguments: list[str]) -> tuple[int, str]:
    result = process.run(
        ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}",
         "-c", "core.pager=cat", "-C", repo, *arguments],
        env=_git_environment(), timeout=GIT_METADATA_TIMEOUT,
        stdout_limit=MAX_GIT_METADATA_BYTES, stderr_limit=MAX_GIT_METADATA_BYTES,
    )
    if result.error:
        raise RepositoryDiagnosticError(
            "INVALID_REPOSITORY", "invalid_repository", "Git metadata could not be checked within its resource limits.",
            "Check Git availability and the local repository metadata; no project command was executed.",
        )
    try:
        output = result.stdout.decode("utf-8", errors="strict")
    except UnicodeError:
        raise RepositoryDiagnosticError(
            "INVALID_REPOSITORY", "invalid_repository", "Git metadata returned an unsupported path encoding.",
            "Select a repository whose Git metadata can be represented as UTF-8.",
        ) from None
    return result.returncode, output


def git_metadata(repo: str) -> RepositoryMetadata:
    """Validate an exact Git root and detect executable helpers, without running them."""
    rc, output = _metadata_query(repo, ["rev-parse", "--show-toplevel", "--is-inside-work-tree"])
    rows = output.splitlines()
    if rc or len(rows) != 2 or rows[1] != "true":
        raise RepositoryDiagnosticError(
            "INVALID_REPOSITORY", "invalid_repository", "Selected directory is not inside a Git worktree.",
            "Choose the exact root of an existing local Git checkout; onboarding does not clone or import automatically.",
        )
    try:
        exact_root = Path(rows[0]).resolve(strict=True) == Path(repo)
    except (OSError, RuntimeError, ValueError):
        exact_root = False
    if not exact_root:
        raise RepositoryDiagnosticError(
            "INVALID_REPOSITORY", "invalid_repository", "Selected directory is not the exact Git repository root.",
            "Select the Git root explicitly. AMU will not select or authorize its parent on your behalf.",
        )
    rc, raw_head = _metadata_query(repo, ["rev-parse", "--verify", "HEAD"])
    head = raw_head.strip() if rc == 0 and _SHA.fullmatch(raw_head.strip()) else None
    # Read configuration without invoking its values. --includes is explicit so
    # a helper hidden in a repository-local include is not silently ignored.
    rc, raw_config = _metadata_query(repo, ["config", "--local", "--includes", "--null", "--get-regexp", _UNSAFE_CONFIG])
    if rc not in (0, 1):
        raise RepositoryDiagnosticError(
            "INVALID_REPOSITORY", "invalid_repository", "Repository execution configuration could not be checked.",
            "Review Git configuration locally before registration or graph initialization.",
        )
    executable_helpers = False
    for entry in raw_config.split("\x00"):
        if not entry:
            continue
        key, separator, value = entry.partition("\n")
        if not separator or not re.fullmatch(_UNSAFE_CONFIG, key, flags=re.IGNORECASE):
            raise RepositoryDiagnosticError(
                "INVALID_REPOSITORY", "invalid_repository", "Repository execution configuration was not parseable.",
                "Review Git configuration locally before registration or graph initialization.",
            )
        if key.lower() == "core.fsmonitor":
            executable_helpers |= value.strip().lower() not in {"", "false", "off", "no", "0", "true", "on", "yes", "1"}
        elif value.strip():
            executable_helpers = True
    return RepositoryMetadata(repo, head, executable_helpers)


def select_local_repository(repo: object, *, access_policy: MCPAccessPolicy | None = None) -> RepositoryMetadata:
    """Return a validated exact-root selection, never modifying grants or workspace state."""
    selected = access_policy.repository(repo) if access_policy is not None else str(_directory(repo))
    metadata = git_metadata(selected)
    if metadata.executable_helpers:
        raise RepositoryDiagnosticError(
            "INVALID_REPOSITORY", "unsafe_repository",
            "Repository configures executable Git filters or fsmonitor helpers; registration was not performed.",
            "Review and disable repository-configured executable helpers locally before registration/indexing. "
            "Onboarding will not execute them or bypass this check.",
        )
    return metadata


def registration_metadata(repo: str) -> dict[str, str]:
    """Read operator-local origin/branch labels through the same bounded Git boundary."""
    origin_rc, origin = _metadata_query(repo, ["remote", "get-url", "origin"])
    branch_rc, branch = _metadata_query(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    return {"origin": origin.strip() if origin_rc == 0 else "",
            "branch": branch.strip() if branch_rc == 0 else ""}


def _row(name: str, status: str, ready: bool, message: str, *, required: bool = True, scope: str | None = None) -> dict:
    row = {"name": name, "status": status, "ready": ready, "required": required, "message": message}
    if scope is not None:
        row["scope"] = scope
    return row


def _report(checks: list[dict], *, category: str | None = None, kind: str | None = None,
            hint: str | None = None, initialization_required: bool | None = None) -> dict:
    return {
        "schema_version": 1,
        "ready": category is None and all(row["ready"] for row in checks if row["required"]),
        "category": category,
        "error_kind": kind,
        "hint": hint,
        "checks": checks,
        "initialization_required": initialization_required,
        "next_actions": [hint] if hint else [],
    }


def _default_provider_probe() -> dict:
    from amu.services.diagnostics import provider_health_ready

    if not entire.available() or not entire.graph_available():
        return {"ready": False}
    health = entire.doctor()
    return {"ready": provider_health_ready(health)}


def repo_preflight(repo: object, *, access_policy: MCPAccessPolicy | None = None,
                   provider_probe: Callable[[], object] | None = None,
                   freshness_probe: Callable[[str], object] | None = None) -> dict:
    """Diagnose only an explicit local selection, with sanitized unsuccessful rows.

    ``provider_probe`` is a trusted dependency injection boundary, not a model
    argument. ``freshness_probe`` is similarly optional; metadata-only preflight
    deliberately does not run Git status (which can invoke clean filters).
    """
    checks: list[dict] = []
    try:
        selected = access_policy.repository(repo) if access_policy is not None else str(_directory(repo))
    except AccessError as exc:
        name = "repository_authorization" if exc.category == "REPOSITORY_NOT_AUTHORIZED" else "repository_access"
        checks.append(_row(name, "failed", False, str(exc)))
        return _report(checks, category=exc.category, kind=exc.kind, hint=exc.hint)
    checks.extend([
        _row("repository_access", "passed", True, "Repository directory is accessible in the server filesystem."),
        _row("repository_authorization", "authorized" if access_policy is not None else "operator_selected", True,
             "Exact-root MCP grant applies." if access_policy is not None else
             "Local operator selection; this does not establish or expand an MCP grant.", required=False),
    ])
    try:
        metadata = git_metadata(selected)
    except RepositoryDiagnosticError as exc:
        checks.append(_row("git_repository", "failed", False, str(exc)))
        return _report(checks, category=exc.category, kind=exc.kind, hint=exc.hint)
    checks.append(_row("git_repository", "passed", True, "Exact local Git worktree root is available."))
    if metadata.executable_helpers:
        checks.append(_row("git_configuration", "blocked", False,
                           "Repository has executable Git filters or fsmonitor helpers; none were executed."))
        return _report(checks, category="INVALID_REPOSITORY", kind="unsafe_repository",
                       hint="Review and disable repository-configured executable helpers locally before registration/indexing.")
    checks.append(_row("git_configuration", "passed", True,
                       "No repository-configured executable Git filters or fsmonitor helpers were found."))
    try:
        provider = (provider_probe or _default_provider_probe)()
        provider_ready = isinstance(provider, Mapping) and provider.get("ready") is True and not provider.get("error")
    except Exception:
        provider_ready = False
    checks.append(_row("graph_provider", "passed" if provider_ready else "failed", provider_ready,
                       "Graph provider is available." if provider_ready else
                       "Graph provider is unavailable or its health probe failed; details are withheld."))
    if not provider_ready:
        return _report(checks, category="GRAPH_PROVIDER_UNAVAILABLE", kind="graph_provider_unavailable",
                       hint="Run amu doctor and repair the operator-selected Entire CLI/graph plugin before indexing.")
    try:
        root = Path(selected) / ".amu"
        index = state_io.read(root / "graph-readiness.json", None, managed_root=root,
                              max_bytes=MAX_INDEX_METADATA_BYTES)
        indexed = (isinstance(index, dict) and index.get("ready") is True and isinstance(index.get("sha"), str)
                   and _SHA.fullmatch(index["sha"]) is not None)
    except (state_io.StateIOError, OSError, ValueError, TypeError):
        index, indexed = None, False
    checks.append(_row("graph_index", "passed" if indexed else "failed", indexed,
                       "Recorded semantic HEAD-index readiness is available." if indexed else
                       "No usable recorded semantic graph index is available; initialization may be required."))
    if not indexed:
        return _report(checks, category="REPOSITORY_NOT_INDEXED", kind="repository_not_indexed",
                       initialization_required=True,
                       hint="The operator can run amu init --repo /absolute/authorized/root, then repeat this preflight. "
                            "Initialization writes graph/AMU state and does not add an MCP grant.")
    if metadata.head is None or index["sha"] != metadata.head:
        checks.append(_row("graph_freshness", "stale", False, "Recorded HEAD index does not match current Git revision.", scope="HEAD"))
        return _report(checks, category="GRAPH_STALE", kind="graph_stale", initialization_required=False,
                       hint="Run the operator-controlled graph sync/init for this selected repository before relying on its HEAD cache.")
    if freshness_probe is not None:
        try:
            evidence = freshness_probe(selected)
            current = (isinstance(evidence, Mapping) and evidence.get("ready") is True
                       and evidence.get("status") == "current" and not evidence.get("error"))
        except Exception:
            current = False
        checks.append(_row("graph_freshness", "current" if current else "unknown", current,
                           "Trusted worktree freshness probe reports current evidence." if current else
                           "Worktree freshness probe did not establish current graph evidence.", scope="worktree"))
        return _report(checks, category=None if current else "GRAPH_FRESHNESS_UNKNOWN",
                       kind=None if current else "graph_freshness_unknown",
                       initialization_required=False,
                       hint=None if current else "Inspect current worktree graph evidence before relying on it; do not infer missing relationships.")
    checks.append(_row("graph_freshness", "current_head", False,
                       "HEAD-index revision matches; uncommitted worktree freshness was not checked by metadata-only onboarding.", scope="HEAD"))
    return _report(checks, category="GRAPH_FRESHNESS_UNKNOWN", kind="graph_freshness_unknown", initialization_required=False,
                   hint="Use live worktree inspection for the selected target. Matching HEAD metadata alone cannot establish current worktree relationships.")


def onboarding_preflight(repo: object, **kwargs) -> dict:
    """Operator-facing selection diagnostic; no cloning, extraction, initialization or grant expansion."""
    return repo_preflight(repo, **kwargs)
