"""Shared relation evidence labels, independent of compatibility predictions."""

from collections.abc import Mapping


def evidence(raw: Mapping, *, heuristic: bool = False) -> dict:
    """Never infer compiler/runtime provenance merely from a resolved edge."""
    provenance = raw.get("provenance")
    if heuristic:
        provenance = "heuristic"
    elif provenance not in ("parser", "compiler/indexer", "heuristic", "runtime"):
        provenance = "unknown"
    confidence = "guessed" if provenance == "heuristic" else "sound"
    if raw.get("confidence") in ("guessed", "unknown"):
        confidence = raw["confidence"]
    return {"provenance": provenance, "confidence": confidence}


def unassessed_compatibility() -> dict:
    return {
        "status": "not_assessed",
        "reason": "potentially affected; a relationship does not prove breakage. "
        "Proposed signatures, argument binding, return behavior, and public exports have not been checked",
    }
