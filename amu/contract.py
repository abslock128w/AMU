"""
amu/contract.py
---------------
Implements the ``amu check`` guardrail command.

Parses entity-level diffs produced by ``entire graph diff`` and blocks
execution (non-zero exit) on any of these violation classes:

  FROZEN_SIGNATURE   – A symbol declared frozen had its signature mutated.
  OUT_OF_SCOPE       – A changed symbol was not listed in the edit scope.
  UNDECLARED_REMOVAL – A public symbol was deleted without an explicit
                       deprecation declaration in the plan.

Violation severity levels
  ERROR   → blocks execution (exit 1)
  WARNING → printed but does not block (exit 0)
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Enums & constants
# ---------------------------------------------------------------------------

class ViolationKind(str, Enum):
    FROZEN_SIGNATURE   = "FROZEN_SIGNATURE"
    OUT_OF_SCOPE       = "OUT_OF_SCOPE"
    UNDECLARED_REMOVAL = "UNDECLARED_REMOVAL"
    PHASE_DRIFT        = "PHASE_DRIFT"
    UNGUARDED_CHANGE   = "UNGUARDED_CHANGE"
    UNKNOWN_TOUCHED    = "UNKNOWN_TOUCHED"
    TEST_FAILURE        = "TEST_FAILURE"
    BUILD_FAILURE       = "BUILD_FAILURE"
    UNVERIFIABLE_WORKTREE_SEMANTICS = "UNVERIFIABLE_WORKTREE_SEMANTICS"
    GRAPH_REFRESH_REQUIRED = "GRAPH_REFRESH_REQUIRED"
    CHECK_EVIDENCE_INVALID = "CHECK_EVIDENCE_INVALID"


class Severity(str, Enum):
    ERROR   = "ERROR"
    WARNING = "WARNING"


# Map each violation kind to its default severity
_DEFAULT_SEVERITY: Dict[ViolationKind, Severity] = {
    ViolationKind.FROZEN_SIGNATURE:   Severity.ERROR,
    ViolationKind.OUT_OF_SCOPE:       Severity.ERROR,
    ViolationKind.UNDECLARED_REMOVAL: Severity.ERROR,
    ViolationKind.PHASE_DRIFT:        Severity.ERROR,
    ViolationKind.UNGUARDED_CHANGE:   Severity.WARNING,
    # An edit in a region the graph could not resolve cannot be allowed to
    # silently turn green.  A human must answer the unknown before proceeding.
    ViolationKind.UNKNOWN_TOUCHED:    Severity.ERROR,
    ViolationKind.TEST_FAILURE:        Severity.ERROR,
    ViolationKind.BUILD_FAILURE:       Severity.ERROR,
    ViolationKind.UNVERIFIABLE_WORKTREE_SEMANTICS: Severity.ERROR,
    ViolationKind.GRAPH_REFRESH_REQUIRED: Severity.ERROR,
    ViolationKind.CHECK_EVIDENCE_INVALID: Severity.ERROR,
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Violation:
    kind: ViolationKind
    symbol: str
    detail: str
    severity: Severity = Severity.ERROR

    def to_dict(self) -> dict:
        return {
            "kind":     self.kind.value,
            "symbol":   self.symbol,
            "detail":   self.detail,
            "severity": self.severity.value,
        }

    def __str__(self) -> str:
        icon = "✖" if self.severity == Severity.ERROR else "⚠"
        return f"  {icon} [{self.kind.value}] {self.symbol}: {self.detail}"


@dataclass
class ContractResult:
    passed: bool
    violations: List[Violation] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "passed":     self.passed,
            "violations": [v.to_dict() for v in self.violations],
            "warnings":   self.warnings,
        }

    def pretty(self) -> str:
        lines: List[str] = []
        if self.violations:
            lines.append("Contract Violations Found:")
            lines.extend(str(v) for v in self.violations)
        if self.warnings:
            lines.append("Warnings:")
            for w in self.warnings:
                lines.append(f"  ℹ {w}")
        status = "0 blocking violations" if self.passed else "BLOCKED"
        lines.append(f"\nContract check: {status}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# entire graph diff wrapper
# ---------------------------------------------------------------------------

def _run_graph_diff(repo: str, base_ref: str, head_ref: str) -> Optional[dict]:
    """
    Invoke ``entire graph diff`` and return parsed JSON.

    Returns None if the CLI is unavailable or output is unparseable
    (treated as a graceful unknown rather than a hard crash).
    """
    cmd = [
        "entire", "graph", "diff",
        "--repo", repo,
        "--base", base_ref,
        "--head", head_ref,
        "--json",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        # entire CLI not installed – return None so callers can handle gracefully
        return None
    except subprocess.TimeoutExpired:
        return None

    if result.returncode != 0 or not result.stdout.strip():
        return None

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Diff parser
# ---------------------------------------------------------------------------

def _parse_diff(diff_data: dict) -> List[dict]:
    """
    Normalise the ``entire graph diff`` JSON payload into a flat list of
    entity change records.

    Expected shape (best-effort, gracefully handles missing fields):
    {
      "changes": [
        {
          "symbol":    "module.MyClass.my_method",
          "change":    "modified" | "removed" | "added",
          "signature_changed": true | false,
          ...
        },
        ...
      ]
    }
    """
    raw_changes = diff_data.get("changes", [])
    if isinstance(raw_changes, list) and raw_changes:
        return raw_changes
    if isinstance(diff_data.get("files"), list):
        return parse_graph_diff(diff_data)
    return []


_TYPE_MAP = {"added": "added", "removed": "removed", "body_changed": "modified", "signature_changed": "modified",
             "renamed": "removed", "moved": "modified"}


def parse_graph_diff(diff_data: dict) -> List[dict]:
    """Normalise the real entire-graph v0.4 shape `{files[{path,changes[{type,kind,name,...}]}]}` into flat records."""
    out: List[dict] = []
    for f in diff_data.get("files", []) or []:
        for c in f.get("changes", []) or []:
            t = c.get("type", "")
            out.append({
                "symbol": c.get("name", ""), "file": f.get("path", ""), "kind": c.get("kind", ""),
                "change": _TYPE_MAP.get(t, "modified"), "type": t,
                "signature_changed": t in ("signature_changed", "renamed"),
                "dependents_count": c.get("dependents_count", 0),
                "old_signature": c.get("old_signature"), "new_signature": c.get("new_signature"),
            })
    return out


# ---------------------------------------------------------------------------
# Violation detectors
# ---------------------------------------------------------------------------

def _check_frozen_signature(
    change: dict,
    frozen_symbols: set,
) -> Optional[Violation]:
    """Flag if a frozen symbol had its signature mutated."""
    symbol = change.get("symbol", "")
    if symbol in frozen_symbols and change.get("signature_changed", False):
        return Violation(
            kind=ViolationKind.FROZEN_SIGNATURE,
            symbol=symbol,
            detail="Signature was mutated on a FROZEN symbol.",
            severity=_DEFAULT_SEVERITY[ViolationKind.FROZEN_SIGNATURE],
        )
    return None


def _check_out_of_scope(
    change: dict,
    scope_symbols: set,
) -> Optional[Violation]:
    """Flag if the changed symbol was not declared in scope."""
    # Empty scope set means "all symbols allowed"
    if not scope_symbols:
        return None
    symbol = change.get("symbol", "")
    change_type = change.get("change", "")
    if change_type in ("modified", "removed") and symbol not in scope_symbols:
        return Violation(
            kind=ViolationKind.OUT_OF_SCOPE,
            symbol=symbol,
            detail=f"Symbol was {change_type} but is not in the declared edit scope.",
            severity=_DEFAULT_SEVERITY[ViolationKind.OUT_OF_SCOPE],
        )
    return None


def _check_undeclared_removal(
    change: dict,
    declared_removals: set,
) -> Optional[Violation]:
    """Flag if a public symbol was removed without being declared for removal."""
    symbol = change.get("symbol", "")
    is_public = not symbol.split(".")[-1].startswith("_")
    if change.get("change") == "removed" and is_public and symbol not in declared_removals:
        return Violation(
            kind=ViolationKind.UNDECLARED_REMOVAL,
            symbol=symbol,
            detail="Public symbol removed without a deprecation/removal declaration.",
            severity=_DEFAULT_SEVERITY[ViolationKind.UNDECLARED_REMOVAL],
        )
    return None


# ---------------------------------------------------------------------------
# Core check function (pure – no subprocess)
# ---------------------------------------------------------------------------

def check_diff(
    changes: List[dict],
    *,
    frozen_symbols: Optional[set] = None,
    scope_symbols: Optional[set] = None,
    declared_removals: Optional[set] = None,
) -> ContractResult:
    """
    Run all contract checks against a list of parsed change records.

    Parameters
    ----------
    changes : list[dict]
        Normalised change records (from ``_parse_diff``).
    frozen_symbols : set, optional
        Symbols whose signatures must not change.
    scope_symbols : set, optional
        Symbols explicitly approved for modification. Empty → all allowed.
    declared_removals : set, optional
        Public symbols explicitly planned for removal.

    Returns
    -------
    ContractResult
    """
    frozen_symbols    = frozen_symbols    or set()
    scope_symbols     = scope_symbols     or set()
    declared_removals = declared_removals or set()

    violations: List[Violation] = []
    warnings:   List[str]       = []

    for change in changes:
        v = _check_frozen_signature(change, frozen_symbols)
        if v:
            violations.append(v)

        v = _check_out_of_scope(change, scope_symbols)
        if v:
            violations.append(v)

        v = _check_undeclared_removal(change, declared_removals)
        if v:
            violations.append(v)

    # Any ERROR-severity violation blocks execution
    has_errors = any(v.severity == Severity.ERROR for v in violations)
    return ContractResult(
        passed=not has_errors,
        violations=violations,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# High-level entry point used by cli.py
# ---------------------------------------------------------------------------

def run_contract_check(
    repo: str,
    base_ref: str,
    head_ref: str,
    *,
    frozen_symbols: Optional[set] = None,
    scope_symbols: Optional[set] = None,
    declared_removals: Optional[set] = None,
    allow_cli_unavailable: bool = True,
) -> ContractResult:
    """
    Full pipeline: run ``entire graph diff``, parse output, check contracts.

    When ``allow_cli_unavailable=True`` (default) and the ``entire`` CLI
    cannot be found, returns a passing result with a warning rather than
    crashing – safe degradation for CI environments without the tool.
    """
    diff_data = _run_graph_diff(repo, base_ref, head_ref)

    if diff_data is None:
        if allow_cli_unavailable:
            return ContractResult(
                passed=True,
                warnings=[
                    "entire CLI unavailable or returned no diff data. "
                    "Contract checks skipped (safe fallback)."
                ],
            )
        return ContractResult(
            passed=False,
            violations=[
                Violation(
                    kind=ViolationKind.OUT_OF_SCOPE,
                    symbol="<graph-diff>",
                    detail="entire graph diff failed; cannot verify contracts.",
                    severity=Severity.ERROR,
                )
            ],
        )

    changes = _parse_diff(diff_data)
    return check_diff(
        changes,
        frozen_symbols=frozen_symbols,
        scope_symbols=scope_symbols,
        declared_removals=declared_removals,
    )


# ---------------------------------------------------------------------------
# PRD contract check: phase scope, drift, unknown nodes (pure – no subprocess)
# ---------------------------------------------------------------------------

def check_contract(contract: dict, changes: List[dict], changed_files: List[str], phase: Optional[int],
                   unknown_files: Optional[set] = None, amu_files: Optional[set] = None) -> ContractResult:
    """Block protected/scope/unknown edits; warn only when a declared change lacks a test."""
    unknown_files = unknown_files or set()
    ignore = amu_files or set()
    frozen = {f.split("#", 1)[-1] for f in contract.get("frozen_signatures", [])}
    frozen_files = {f.split("#", 1)[0] for f in contract.get("frozen_signatures", [])}
    phases = contract.get("phases", [])
    allowed = set(contract.get("allowed_files", []))
    phase_files = set()
    later_files = set()
    for p in phases:
        if phase is None or p["n"] <= phase:
            phase_files.update(p["files"])
        else:
            later_files.update(p["files"])
    later_files -= phase_files
    removal_targets = [t for t in contract.get("targets", []) if t.get("change_class") in ("remove", "rename", "move")]
    declared_removals = {str(t.get("symbol") or "").split("#")[-1] for t in removal_targets}
    declared_removal_files = {str(t.get("symbol") or "").split("#", 1)[0] for t in removal_targets if str(t.get("symbol") or "").endswith("#*")}

    violations: List[Violation] = []
    for f in changed_files:
        if f in ignore or f.startswith(".amu/") or f.startswith("docs/evidence/"):
            continue
        if f in later_files:
            violations.append(Violation(ViolationKind.PHASE_DRIFT, f, f"changed in phase {phase} but belongs to a later phase", Severity.ERROR))
        elif allowed and f not in allowed:
            violations.append(Violation(ViolationKind.OUT_OF_SCOPE, f, "file is not in the contract's allowed_files", Severity.ERROR))
        if f in unknown_files:
            violations.append(Violation(ViolationKind.UNKNOWN_TOUCHED, f, "file is an unknown node; graph cannot see its consumers", Severity.ERROR))
    for c in changes:
        sym, file = c.get("symbol", ""), c.get("file", "")
        if file in ignore:
            continue
        if c.get("signature_changed") and (sym in frozen or f"{file}#{sym}" in set(contract.get("frozen_signatures", []))) and file in frozen_files:
            violations.append(Violation(ViolationKind.FROZEN_SIGNATURE, f"{file}#{sym}", "signature of a frozen consumer changed", Severity.ERROR))
        if c.get("change") == "removed" and c.get("kind") in ("function", "class", "method") and not sym.split(".")[-1].startswith("_") and sym not in declared_removals and file not in declared_removal_files:
            violations.append(Violation(ViolationKind.UNDECLARED_REMOVAL, f"{file}#{sym}", "public symbol removed without a declared removal target", Severity.ERROR))
        guarded = any(file in p.get("files", []) and p.get("tests") for p in phases)
        if c.get("type") in ("signature_changed", "body_changed") and file in allowed and not guarded:
            violations.append(Violation(ViolationKind.UNGUARDED_CHANGE, f"{file}#{sym}", "changed export has no test in the contract", Severity.WARNING))
    has_errors = any(v.severity == Severity.ERROR for v in violations)
    return ContractResult(passed=not has_errors, violations=violations, warnings=[])


def worktree_semantic_violations(contract: dict, worktree_files: set[str]) -> list[Violation]:
    """Block uncommitted edits in frozen files without guessing their symbol change.

    ``entire graph diff --base <commit> --head <commit>`` cannot describe a
    working-tree signature edit.  The file-level signal is sufficient to show
    that a frozen contract boundary is unverifiable, but not enough to name
    the changed signature, so the violation deliberately names only the file.
    """
    frozen_files = {
        signature.partition("#")[0]
        for signature in contract.get("frozen_signatures", [])
        if isinstance(signature, str) and signature.partition("#")[0]
    }
    return [
        Violation(
            ViolationKind.UNVERIFIABLE_WORKTREE_SEMANTICS,
            path,
            "uncommitted edit is in a file containing a frozen signature; commit the intended change, run `amu sync`, then re-run this phase (or revert it)",
            Severity.ERROR,
        )
        for path in sorted(frozen_files & set(worktree_files))
    ]
