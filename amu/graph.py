"""Compatibility shim for non-approval graph helpers.

The old ``impact()`` API returned unstructured provider text and was easy to
mistake for a verified dependency graph.  It is deliberately retired: safe
callers use ``amu map`` / ``amu refresh`` and their structured evidence.
"""

from amu import entire

_FALLBACK_CAPS = {"out_of_coverage": [], "unresolved_patterns": ["dynamic_dispatch", "reflection"]}


def impact(symbol: str, depth: int = 2, repo: str = ".") -> str:
    """Return a parseable retirement notice; never expose raw impact text."""
    del symbol, depth, repo
    return (
        "UNSAFE_LEGACY_GRAPH: raw impact text is retired and cannot support an edit decision; "
        "run `amu map --symbol path/to/file.py#symbol --change <class>` instead"
    )


def capabilities(repo: str = ".") -> dict:
    """Graph parser capabilities feeding the unknown bucket; conservative fallback dict when the CLI is absent."""
    caps = entire.capabilities(repo)
    if "error" in caps:
        return {**_FALLBACK_CAPS, **caps, "capabilities_status": "unavailable", "complete": False}
    return caps
