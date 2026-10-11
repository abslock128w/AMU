"""State-derived phase gates for the approved-contract lifecycle.

The contract says which phases exist; the state history is the evidence that a
phase actually ran without blocking violations.  Node states are a secondary
cross-check, so a stale green history cannot advance a phase after a later
check has marked one of its nodes red or drifted.
"""

from __future__ import annotations

from amu import state


def capture_source(repo: str) -> dict:
    """Bind a check to HEAD, editable worktree content, and runner configuration."""
    head = state.git_head(repo)
    fingerprint = state.worktree_fingerprint(repo)
    cfg = state.config(repo)
    error = fingerprint.get("error")
    if head == "no-git" or state.git_head(repo) != head:
        error = error or "HEAD is unavailable or changed while capturing check evidence"
    return {
        "head": head,
        "digest": fingerprint.get("digest"),
        "config_hash": state.stable_hash(cfg),
        "build_required": bool(str(cfg.get("build_cmd") or "").strip()),
        "error": error,
    }


def valid_source(source: object) -> bool:
    return (
        isinstance(source, dict)
        and "error" in source and source["error"] is None
        and all(isinstance(source.get(key), str) and source[key] for key in ("head", "digest", "config_hash"))
        and source["head"] != "no-git"
        and type(source.get("build_required")) is bool
    )


def passed_run(run: object) -> bool:
    """Require the actual command's successful outcome, not a truthy placeholder."""
    return (
        isinstance(run, dict) and run.get("passed") is True
        and type(run.get("returncode")) is int and run["returncode"] == 0
        and isinstance(run.get("command"), str) and bool(run["command"].strip())
    )


def run_evidence(run: dict | None, *, absent: str) -> dict:
    if run is None:
        return {"status": absent, "command": None}
    return {**run, "status": "passed" if passed_run(run) else "failed"}


def covered_phases(contract: dict, phase: int | None) -> list[dict]:
    """Phase checks enforce cumulative scope, so they must also rerun cumulative tests."""
    return [p for p in ordered_phases(contract) if phase is None or p["n"] <= phase]


def ordered_phases(contract: dict) -> list[dict]:
    """Return well-formed contract phases in their deterministic numeric order."""
    rows = []
    for position, phase in enumerate(contract.get("phases", [])):
        if not isinstance(phase, dict):
            continue
        number = phase.get("n")
        if isinstance(number, int) and not isinstance(number, bool):
            rows.append((number, position, phase))
    return [phase for _number, _position, phase in sorted(rows)]


def _record_green(entry: dict, contract: dict, source: dict | None, *, milestone: bool) -> bool:
    """Validate positive proof; legacy, incomplete, and stale records fail closed."""
    if type(entry.get("version")) is not int or entry["version"] != 1 or entry.get("event") != "check":
        return False
    if entry.get("passed") is not True or entry.get("source_stable") is not True:
        return False
    if entry.get("contract_hash") != state.stable_hash(contract):
        return False
    recorded = entry.get("source")
    if not valid_source(recorded) or not valid_source(source):
        return False
    # A historical milestone only permits running the next cumulative check.
    # It never certifies green/completion for a changed worktree. HEAD and
    # configuration changes require an explicit all-phase recheck.
    keys = ("head", "config_hash", "build_required") if milestone else ("head", "digest", "config_hash", "build_required")
    if any(recorded[key] != source[key] for key in keys):
        return False
    tests, build = entry.get("tests"), entry.get("build")
    if not passed_run(tests) or tests.get("status") != "passed":
        return False
    if not isinstance(build, dict):
        return False
    if recorded["build_required"]:
        if not passed_run(build) or build.get("status") != "passed":
            return False
    elif build != {"status": "not_configured", "command": None}:
        return False
    violations = entry.get("violations")
    if not isinstance(violations, list):
        return False
    return all(
        isinstance(v, dict) and v.get("severity") == "WARNING"
        and all(isinstance(v.get(key), str) and v[key] for key in ("kind", "symbol", "detail"))
        for v in violations
    )


def _statuses(contract: dict, current_state: dict, source: dict | None, *, milestone: bool) -> list[dict]:
    phases = ordered_phases(contract)
    numbers = {phase["n"] for phase in phases}
    latest: dict[int, tuple[int, bool]] = {}
    history = current_state.get("history", []) if isinstance(current_state, dict) else []
    if isinstance(history, list):
        for index, entry in enumerate(history):
            phase = entry.get("phase") if isinstance(entry, dict) else None
            explicit_phase = isinstance(entry, dict) and "phase" in entry and (
                phase is None or (type(phase) is int and phase in numbers)
            )
            covered = [p["n"] for p in covered_phases(contract, phase)] if explicit_phase else list(numbers)
            declared = entry.get("covered_phases") if isinstance(entry, dict) else None
            valid_coverage = (
                explicit_phase and isinstance(declared, list)
                and all(type(n) is int for n in declared) and declared == covered
            )
            green = bool(valid_coverage and _record_green(entry, contract, source, milestone=milestone))
            # A newer failed/malformed attempt must revoke older success, not
            # disappear and accidentally resurrect the last green entry.
            for number in covered:
                latest[number] = (index, green)

    nodes = current_state.get("nodes", {}) if isinstance(current_state, dict) else {}
    nodes = nodes if isinstance(nodes, dict) else {}
    statuses = []
    for phase in phases:
        number = phase["n"]
        record = latest.get(number)
        phase_nodes = [node for node in phase.get("nodes", []) if isinstance(node, str)]
        nodes_green = all(nodes.get(node) == "green" for node in phase_nodes)
        if record is not None:
            green = record[1] and nodes_green
            status_source = "history"
        else:
            green = False
            status_source = "missing"
        statuses.append({"n": number, "kind": phase.get("kind", ""), "green": green, "source": status_source})
    return statuses


def phase_statuses(contract: dict, current_state: dict, source: dict | None = None) -> list[dict]:
    """Return green only for explicit, successful checks of the current source.

    Omitting current source is deliberately non-certifying. Old unversioned
    histories require a new check; they are never silently upgraded.
    """
    return _statuses(contract, current_state, source, milestone=False)


def earlier_incomplete(contract: dict, current_state: dict, phase: int, source: dict | None = None) -> list[dict]:
    """Historical milestones permit running (not passing) a cumulative check."""
    statuses = _statuses(contract, current_state, source, milestone=True)
    target_index = next((index for index, status in enumerate(statuses) if status["n"] == phase), None)
    if target_index is None:
        return []
    return [status for status in statuses[:target_index] if not status["green"]]


def incomplete_phases(contract: dict, current_state: dict, source: dict | None = None) -> list[dict]:
    """Return every planned phase without current green evidence."""
    return [status for status in phase_statuses(contract, current_state, source) if not status["green"]]
