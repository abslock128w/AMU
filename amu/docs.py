"""Docs candidates from committed graph diffs plus conservative worktree evidence.

Nothing is applied unless a human confirms a SUGGESTED draft or ``--mode
auto`` applies an existing sound-and-mapped graph-diff candidate that is not
shadowed by live worktree evidence. Local worktree evidence is always
manual-only.
"""

from __future__ import annotations

import ast
import glob
import re
import subprocess
from pathlib import Path

from amu import freshness, state
from amu.contract import parse_graph_diff

DOC_CLASSES = {"added": "add_export", "removed": "remove", "renamed": "rename", "signature_changed": "signature", "moved": "move"}

# The graph provider's semantic diff compares commit refs.  The worktree scan
# below deliberately stays local and narrow: Python source is compared with
# Python's AST, while common non-Python source extensions are surfaced as
# explicit unknowns rather than guessed at or silently ignored.
_MAX_WORKTREE_SOURCE_BYTES = 2_000_000
_MAX_WORKTREE_SOURCE_PATHS = 250
_PYTHON_SUFFIXES = {".py", ".pyi"}
_UNSUPPORTED_SOURCE_SUFFIXES = {
    ".c",
    ".cc",
    ".clj",
    ".cljc",
    ".cljx",
    ".cu",
    ".cuh",
    ".cpp",
    ".cs",
    ".cxx",
    ".dart",
    ".ex",
    ".exs",
    ".f",
    ".f03",
    ".f08",
    ".f90",
    ".f95",
    ".for",
    ".fs",
    ".fsx",
    ".go",
    ".groovy",
    ".h",
    ".hh",
    ".hrl",
    ".hpp",
    ".ipynb",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".kts",
    ".m",
    ".mm",
    ".php",
    ".pl",
    ".proto",
    ".pxd",
    ".pxi",
    ".pyx",
    ".r",
    ".rb",
    ".rs",
    ".scala",
    ".sh",
    ".sql",
    ".swift",
    ".ts",
    ".tsx",
    ".vue",
    ".zig",
}
_IRRELEVANT_SUFFIXES = {
    ".7z",
    ".a",
    ".avi",
    ".bmp",
    ".class",
    ".csv",
    ".doc",
    ".docx",
    ".eot",
    ".gif",
    ".gz",
    ".ico",
    ".jar",
    ".jpeg",
    ".jpg",
    ".lock",
    ".map",
    ".md",
    ".mdx",
    ".mp3",
    ".mp4",
    ".odt",
    ".pdf",
    ".png",
    ".pyc",
    ".rst",
    ".so",
    ".svg",
    ".tar",
    ".tgz",
    ".ttf",
    ".txt",
    ".wav",
    ".webp",
    ".woff",
    ".woff2",
    ".xlsx",
    ".zip",
}
_IRRELEVANT_FILENAMES = {
    "bun.lockb",
    "cargo.lock",
    "changelog",
    "composer.lock",
    "contributing",
    "code_of_conduct",
    "copying",
    "dockerfile",
    "gemfile.lock",
    "go.sum",
    "justfile",
    "license",
    "makefile",
    "notice",
    "package-lock.json",
    "pipfile.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "procfile",
    "readme",
    "uv.lock",
    "yarn.lock",
}


def doc_map(repo: str = ".") -> dict:
    """Read a direct map or the PRD's versioned ``entries`` representation."""
    value = state.read_json("doc-map.json", repo, default={})
    if not isinstance(value, dict):
        return {}
    rows = value.get("entries")
    if not isinstance(rows, list):
        rows = value.get("mappings")
    if not isinstance(rows, list):
        return value
    normalized: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = row.get("symbol")
        filename = row.get("doc_file") or row.get("file")
        section = row.get("anchor") or row.get("section") or ""
        if isinstance(symbol, str) and "#" in symbol and isinstance(filename, str) and filename.strip():
            normalized.setdefault(symbol, []).append({"file": filename, "section": str(section)})
    return normalized


def draft_doc_map(repo: str = ".", docs_dir: str = "docs") -> dict:
    """Seed `path#symbol → [{file, section}]` from headings that mention a symbol-looking token."""
    from amu import docgraph

    out: dict = {}
    for md in glob.glob(str(Path(repo) / docs_dir / "**" / "*.md"), recursive=True):
        rel = str(Path(md).relative_to(repo))
        for section in docgraph._sections(rel, Path(md).read_text(errors="ignore")):
            if section.get("heading") and not section.get("structure_error"):
                for tok in re.findall(r"`([A-Za-z_][\w./#]*)`", section["title"]):
                    out.setdefault(tok, []).append({"file": rel, "section": section["heading"]})
    return out


def candidates(diff_data: dict | None, dmap: dict) -> list[dict]:
    """Public-export changes in doc-relevant classes ⇒ candidate; body ⇒ candidate marked unknown."""
    if not diff_data:
        return []
    dmap = dmap if isinstance(dmap, dict) else {}
    out = []
    for c in parse_graph_diff(diff_data):
        if not isinstance(c, dict):
            continue
        kind = c.get("kind")
        symbol = c.get("symbol")
        path = c.get("file")
        change_type = c.get("type")
        if (
            kind not in ("function", "class", "method")
            or not isinstance(symbol, str)
            or not symbol
            or not isinstance(path, str)
            or not path
        ):
            continue
        cls = DOC_CLASSES.get(change_type)
        key = f"{path}#{symbol}"
        mapped = _mapped_targets(path, symbol, dmap)
        # The graph diff alone does not carry enough export information to
        # prove that a private spelling is internal.  It could be deliberately
        # public through ``__all__``, a re-export, or a language protocol such
        # as ``__call__``.  Never auto-write docs for it, but never silently
        # discard it either.
        if symbol.split(".")[-1].startswith("_"):
            out.append(
                {
                    "id": f"d{len(out)+1}",
                    "candidate": key,
                    "class": cls or "body",
                    "confidence": "unknown",
                    "old": c.get("old_signature") if cls else None,
                    "new": c.get("new_signature") if cls else None,
                    "targets": mapped,
                    "reason": "private-named graph export may be public through __all__ or a language protocol; documentation impact requires review",
                }
            )
            continue
        if cls:
            out.append({"id": f"d{len(out)+1}", "candidate": key, "class": cls, "confidence": "sound" if mapped else "guessed",
                        "old": c.get("old_signature"), "new": c.get("new_signature"), "targets": mapped,
                        "reason": f"{cls} on a public export" + ("" if mapped else " (no doc-map entry)")})
        elif c["type"] == "body_changed":
            out.append({"id": f"d{len(out)+1}", "candidate": key, "class": "body", "confidence": "unknown", "old": None, "new": None,
                        "targets": mapped, "reason": "body changed; whether docs describe the old behaviour is not derivable from the graph"})
    return out


def _mapped_targets(path: str, symbol: str, dmap: dict) -> list[dict]:
    """Return only structurally usable doc-map targets.

    A doc-map is local, editable state.  Its values must never be allowed to
    crash candidate rendering or turn a non-target (a string, number, or
    partial object) into a write target.  Invalid rows are intentionally
    dropped, which leaves the candidate guessed/manual rather than applying a
    docs edit automatically.
    """
    if not isinstance(dmap, dict):
        return []
    mapped = dmap.get(f"{path}#{symbol}") or dmap.get(symbol) or []
    if not isinstance(mapped, list):
        return []
    targets: list[dict] = []
    for entry in mapped:
        if not isinstance(entry, dict):
            continue
        filename = entry.get("file")
        section = entry.get("section")
        if not isinstance(filename, str) or not filename.strip() or not isinstance(section, str):
            continue
        targets.append({"file": filename, "section": section})
    return targets


def _renumber(rows: list[dict]) -> list[dict]:
    """Give one docs invocation stable, non-colliding candidate ids."""
    return [{**row, "id": f"d{i}", "fingerprint": candidate_fingerprint(row)} for i, row in enumerate(rows, 1)]


_CANDIDATE_MANIFEST = "docs-candidates.json"


def candidate_fingerprint(candidate: dict) -> str:
    """Fingerprint the exact candidate identity independent of its display id.

    ``d1`` is intentionally a short list position, not a durable identity.
    The fingerprint covers every field that can change what a manual apply
    would write, including its mapped documentation target.
    """
    targets = candidate.get("targets") if isinstance(candidate, dict) else None
    safe_targets = []
    if isinstance(targets, list):
        for target in targets:
            if not isinstance(target, dict):
                continue
            filename = target.get("file")
            section = target.get("section")
            if isinstance(filename, str) and isinstance(section, str):
                safe_targets.append({"file": filename, "section": section})
    return state.stable_hash(
        {
            "candidate": candidate.get("candidate") if isinstance(candidate, dict) else None,
            "class": candidate.get("class") if isinstance(candidate, dict) else None,
            "confidence": candidate.get("confidence") if isinstance(candidate, dict) else None,
            "old": candidate.get("old") if isinstance(candidate, dict) else None,
            "new": candidate.get("new") if isinstance(candidate, dict) else None,
            "targets": safe_targets,
            "reason": candidate.get("reason") if isinstance(candidate, dict) else None,
            "evidence": candidate.get("evidence") if isinstance(candidate, dict) else None,
            "api_change": candidate.get("api_change") if isinstance(candidate, dict) else None,
            "facets_changed": candidate.get("facets_changed") if isinstance(candidate, dict) else None,
            "api_evidence": candidate.get("api_evidence") if isinstance(candidate, dict) else None,
        }
    )


def issue_candidate_manifest(repo: str, rows: list[dict]) -> None:
    """Record the listing that issued ephemeral ``dN`` ids for a later apply."""
    worktree = state.worktree_fingerprint(repo)
    domains = freshness.capture(repo)
    state.write_json(
        _CANDIDATE_MANIFEST,
        {
            "schema": 1,
            "head": state.git_head(repo),
            "worktree": {"digest": worktree.get("digest"), "error": worktree.get("error")},
            "revisions": ({"head": domains["head"], "code": domains["code"]["digest"],
                           "docs": domains["docs"]["digest"], "config_hash": domains["config_hash"],
                           "doc_map_hash": state.stable_hash(doc_map(repo))}
                          if freshness.valid_capture(domains) else None),
            "candidates": [
                {"id": row.get("id"), "fingerprint": candidate_fingerprint(row)}
                for row in rows
                if isinstance(row, dict) and isinstance(row.get("id"), str)
            ],
        },
        repo,
    )


def issued_candidate(repo: str, rows: list[dict], candidate_id: str) -> tuple[dict | None, str | None]:
    """Resolve a human-selected id only when the listing remains exactly current.

    This deliberately refuses a bare stale ``dN`` instead of following its
    new list position.  A user must re-run ``amu docs`` and review the fresh
    list before retrying an apply.
    """
    manifest = state.read_json(_CANDIDATE_MANIFEST, repo, default={})
    if not isinstance(manifest, dict) or manifest.get("schema") != 1:
        return None, "candidate id was not issued by a docs listing; run amu docs and review the current candidates first"
    if manifest.get("head") != state.git_head(repo):
        return None, "candidate listing is stale because HEAD changed; run amu docs and review the current candidates first"
    recorded_worktree = manifest.get("worktree")
    current_worktree = state.worktree_fingerprint(repo)
    if (
        not isinstance(recorded_worktree, dict)
        or recorded_worktree.get("error")
        or current_worktree.get("error")
        or not recorded_worktree.get("digest")
        or recorded_worktree.get("digest") != current_worktree.get("digest")
    ):
        return None, "candidate listing is stale because the worktree changed; run amu docs and review the current candidates first"
    if manifest.get("revisions") is not None:
        domains = freshness.capture(repo)
        current_revisions = ({"head": domains["head"], "code": domains["code"]["digest"],
                              "docs": domains["docs"]["digest"], "config_hash": domains["config_hash"],
                              "doc_map_hash": state.stable_hash(doc_map(repo))}
                             if freshness.valid_capture(domains) else None)
        if current_revisions != manifest["revisions"]:
            return None, "candidate listing is stale because documentation or analysis configuration changed; run amu docs again"
    issued = manifest.get("candidates")
    if not isinstance(issued, list):
        return None, "candidate listing is malformed; run amu docs and review the current candidates first"
    record = next(
        (
            item
            for item in issued
            if isinstance(item, dict) and item.get("id") == candidate_id and isinstance(item.get("fingerprint"), str)
        ),
        None,
    )
    if record is None:
        return None, "candidate id was not issued by the current docs listing; run amu docs and review the current candidates first"
    current = next((row for row in rows if isinstance(row, dict) and row.get("id") == candidate_id), None)
    if current is None or candidate_fingerprint(current) != record["fingerprint"]:
        return None, "candidate listing is stale or reordered; run amu docs and review the current candidates first"
    return current, None


def _git(repo: str, args: list[str]) -> tuple[int, bytes, bytes]:
    """Run a bounded git read without a shell; callers turn failures into unknowns."""
    try:
        result = subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, timeout=15, check=False
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return 1, b"", b"git unavailable or timed out"
    return result.returncode, result.stdout, result.stderr


def _nul_paths(repo: str, args: list[str]) -> tuple[list[str], str | None]:
    code, stdout, stderr = _git(repo, [*args, "-z"])
    if code:
        return [], stderr.decode("utf-8", "replace").strip() or "git could not enumerate changed paths"
    return [part.decode("utf-8", "surrogateescape") for part in stdout.split(b"\0") if part], None


def _changed_worktree_paths(repo: str) -> tuple[list[str], bool, str | None]:
    """Return changed paths, whether HEAD exists, and a query failure if any.

    ``--no-renames`` intentionally represents a rename as a remove plus an
    add.  That is safer than claiming a rename when source comparison cannot
    prove it, and it still produces documentation candidates for both API
    shapes.
    """
    in_repo, _, in_repo_stderr = _git(repo, ["rev-parse", "--is-inside-work-tree"])
    if in_repo:
        detail = in_repo_stderr.decode("utf-8", "replace").strip()
        return [], False, detail or "git could not determine whether this is a worktree"
    has_head, _, _ = _git(repo, ["rev-parse", "--verify", "HEAD"])
    if has_head == 0:
        changed, error = _nul_paths(repo, ["diff", "--name-only", "--no-renames", "HEAD"])
    else:
        staged, staged_error = _nul_paths(repo, ["diff", "--cached", "--name-only", "--no-renames"])
        unstaged, unstaged_error = _nul_paths(repo, ["diff", "--name-only", "--no-renames"])
        changed, error = [*staged, *unstaged], staged_error or unstaged_error
    untracked, untracked_error = _nul_paths(repo, ["ls-files", "--others", "--exclude-standard"])
    error = error or untracked_error
    if error:
        return [], has_head == 0, error
    return sorted(set([*changed, *untracked])), has_head == 0, None


def _is_ignored_worktree_path(path: str, repo: str, *, mapped_config: bool = False) -> bool:
    """Explicit config intent can override prose/test scope, never internal data."""
    raw = Path(path)
    if not path or raw.is_absolute() or ".." in raw.parts:
        return True
    parts = raw.parts
    if not parts or any(part in {".git", ".amu", "__pycache__"} for part in parts):
        return True
    if mapped_config:
        if any(part.lower() in {"node_modules", ".venv", "venv", "__pypackages__", ".cache", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".aws", ".ssh", ".gnupg"} for part in parts):
            return True
        credential_name = raw.name.lower().lstrip(".")
        if credential_name.startswith(("env.", "credentials.", "secrets.", "application_default_credentials.")):
            return True
    if raw.name.startswith(".") and not mapped_config:
        return True
    if any(part in {"test", "tests"} for part in parts[:-1]) and not mapped_config:
        return True
    docs_dir = Path(str(state.config(repo).get("docs_dir", "docs")))
    if not docs_dir.is_absolute() and ".." not in docs_dir.parts and docs_dir.parts:
        return parts[: len(docs_dir.parts)] == docs_dir.parts and not mapped_config
    return False


def _source_kind(path: str) -> str | None:
    raw = Path(path)
    suffix = raw.suffix.lower()
    if suffix in _PYTHON_SUFFIXES:
        return "python"
    if suffix in _UNSUPPORTED_SOURCE_SUFFIXES:
        return "unsupported"
    # Do not keep a finite allow-list of programming languages: a changed
    # Julia, Haskell, or project-specific source file is still an evidence gap
    # that must reach manual review.  Only clearly non-source artifacts are
    # omitted to avoid turning lockfiles and images into docs noise.
    if raw.name.lower() in _IRRELEVANT_FILENAMES or suffix in _IRRELEVANT_SUFFIXES:
        return None
    return "unsupported"


def _current_source(repo: str, path: str) -> tuple[str, str | None]:
    """Return source text with the manifest adapter's bounded, stable reads."""
    raw = Path(path)
    if raw.is_absolute() or ".." in raw.parts:
        return "error", None
    try:
        root = Path(repo).resolve()
        candidate = root / raw
        if candidate.resolve() != candidate:
            return "error", None
        if not candidate.exists() and not candidate.is_symlink():
            return "missing", None
        from amu import api_manifest

        return "present", api_manifest._read(str(root), path)
    except (OSError, RuntimeError, UnicodeDecodeError, ValueError):
        return "error", None


def _head_source(repo: str, path: str) -> tuple[str, str | None]:
    """Return present/missing/error and the exact HEAD version of one path."""
    raw = Path(path)
    if raw.is_absolute() or ".." in raw.parts:
        return "error", None
    # ``ls-tree`` distinguishes a genuinely new path from a failed ``show``.
    code, listed, _ = _git(repo, ["ls-tree", "-r", "-z", "HEAD", "--", path])
    if code:
        return "error", None
    if not listed:
        return "missing", None
    records = [item.partition(b"\t") for item in listed.split(b"\0") if item]
    exact = [metadata for metadata, separator, filename in records if separator and filename == path.encode("utf-8")]
    if len(exact) != 1 or exact[0].split(b" ", 1)[0] not in {b"100644", b"100755"}:
        return "error", None
    code, source, _ = _git(repo, ["show", f"HEAD:{path}"])
    if code or len(source) > _MAX_WORKTREE_SOURCE_BYTES:
        return "error", None
    try:
        return "present", source.decode("utf-8")
    except UnicodeDecodeError:
        return "error", None


def _definition_signature(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> tuple[str, str]:
    """Return a readable signature plus a stable AST fingerprint for comparisons."""
    type_params = getattr(node, "type_params", [])
    type_params_fingerprint = ",".join(ast.dump(item, include_attributes=False) for item in type_params)
    returns_fingerprint = ast.dump(node.returns, include_attributes=False) if getattr(node, "returns", None) else "None"
    try:
        if isinstance(node, ast.ClassDef):
            base_text = [ast.unparse(base) for base in node.bases]
            base_text.extend(ast.unparse(keyword) for keyword in node.keywords)
            display = f"class {node.name}" + (f"({', '.join(base_text)})" if base_text else "")
            fingerprint = "|".join(
                [
                    *(ast.dump(base, include_attributes=False) for base in node.bases),
                    *(ast.dump(keyword, include_attributes=False) for keyword in node.keywords),
                    *(ast.dump(decorator, include_attributes=False) for decorator in node.decorator_list),
                    type_params_fingerprint,
                ]
            )
        else:
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            returns = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
            display = f"{prefix} {node.name}({ast.unparse(node.args)}){returns}"
            fingerprint = "|".join(
                [
                    ast.dump(node.args, include_attributes=False),
                    returns_fingerprint,
                    *(ast.dump(decorator, include_attributes=False) for decorator in node.decorator_list),
                    type_params_fingerprint,
                ]
            )
    except (TypeError, ValueError):
        # AST parsing itself succeeded.  A display failure must not make an
        # API change disappear, so use a small non-secret fallback.
        display = f"{node.__class__.__name__} {node.name}(…)"
        fingerprint = ast.dump(node, include_attributes=False)
    return display[:1000], fingerprint


def _definition_body_fingerprint(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> str:
    """Compare a class's own body without duplicating every changed method."""
    if isinstance(node, ast.ClassDef):
        own_body = [
            child
            for child in node.body
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        return "|".join(ast.dump(child, include_attributes=False) for child in own_body)
    return ast.dump(node, include_attributes=False)


def _literal_export_names(value: ast.AST) -> set[str] | None:
    """Return a statically knowable ``__all__`` collection, never evaluate code."""
    try:
        parsed = ast.literal_eval(value)
    except (ValueError, TypeError, SyntaxError):
        return None
    if not isinstance(parsed, (list, tuple, set, frozenset)) or not all(
        isinstance(name, str) and name for name in parsed
    ):
        return None
    return set(parsed)


def _is_all_target(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id == "__all__"


def _contains_all_target(node: ast.AST) -> bool:
    return any(isinstance(child, ast.Name) and child.id == "__all__" for child in ast.walk(node))


def _contains_module_all_reference(node: ast.AST) -> bool:
    """See a module-scope ``__all__`` use without confusing function locals."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
        return False
    pending = list(ast.iter_child_nodes(node))
    while pending:
        child = pending.pop()
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(child, ast.Name) and child.id == "__all__":
            return True
        pending.extend(ast.iter_child_nodes(child))
    return False


def _static_module_exports(tree: ast.Module) -> tuple[set[str] | None, str | None]:
    """Read conservative module-level ``__all__`` evidence.

    Private definitions may be intentionally public through ``__all__``.  A
    literal assignment (and a literal ``+=`` after an assignment) is enough to
    identify those definitions.  Any other manipulation remains a file-level
    unknown rather than allowing a private-but-exported API change to vanish
    from documentation review.
    """
    exports: set[str] = set()
    initialized = False
    for node in tree.body:
        if isinstance(node, ast.Assign):
            direct_targets = [target for target in node.targets if _is_all_target(target)]
            if direct_targets:
                names = _literal_export_names(node.value)
                if names is None:
                    return None, "module __all__ is not a literal export list"
                exports = names
                initialized = True
            elif any(_contains_all_target(target) for target in node.targets):
                return None, "module __all__ is assigned through an unsupported target shape"
            elif _contains_module_all_reference(node.value):
                return None, "module __all__ is dynamically modified"
            continue
        if isinstance(node, ast.AnnAssign):
            if _is_all_target(node.target):
                names = _literal_export_names(node.value) if node.value is not None else None
                if names is None:
                    return None, "module __all__ is not a literal export list"
                exports = names
                initialized = True
            elif _contains_all_target(node.target):
                return None, "module __all__ is assigned through an unsupported target shape"
            elif node.value is not None and _contains_module_all_reference(node.value):
                return None, "module __all__ is dynamically modified"
            continue
        if isinstance(node, ast.AugAssign):
            if _is_all_target(node.target):
                names = _literal_export_names(node.value) if isinstance(node.op, ast.Add) else None
                if not initialized or names is None:
                    return None, "module __all__ is dynamically modified"
                exports.update(names)
            elif _contains_all_target(node.target):
                return None, "module __all__ is dynamically modified"
            elif _contains_module_all_reference(node.value):
                return None, "module __all__ is dynamically modified"
            continue
        if isinstance(node, ast.Delete) and any(_contains_all_target(target) for target in node.targets):
            return None, "module __all__ is dynamically modified"
        if isinstance(node, ast.Import) and any(alias.asname == "__all__" for alias in node.names):
            return None, "module __all__ is imported dynamically"
        if isinstance(node, ast.ImportFrom) and any(
            alias.name == "__all__" or alias.asname == "__all__" for alias in node.names
        ):
            return None, "module __all__ is imported dynamically"
        if _contains_module_all_reference(node):
            return None, "module __all__ is dynamically modified"
    return exports, None


def _is_user_facing_dunder(name: str) -> bool:
    """Treat language-protocol methods as public surface, even when spelled private."""
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


def _public_definitions(source: str, path: str) -> tuple[dict[str, dict[str, str]] | None, str | None, str | None]:
    """Collect top-level public functions/classes and direct public methods.

    Nested local functions are intentionally not considered API.  Repeated
    public declarations (for example, overload-heavy code) are labelled
    unknown instead of choosing one declaration and potentially missing a
    signature change.
    """
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError, TypeError) as exc:
        return None, None, f"Python AST parse failed: {exc.__class__.__name__}"
    exported_names, exports_error = _static_module_exports(tree)
    if exports_error:
        return None, None, f"{exports_error}; public export impact is unknown"
    assert exported_names is not None
    definitions: dict[str, dict[str, str]] = {}

    def add(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef, symbol: str, kind: str) -> bool:
        if symbol in definitions:
            return False
        signature, signature_fingerprint = _definition_signature(node)
        definitions[symbol] = {
            "kind": kind,
            "signature": signature,
            "signature_fingerprint": signature_fingerprint,
            "body_fingerprint": _definition_body_fingerprint(node),
        }
        return True

    def public(name: str, qualified_name: str | None = None) -> bool:
        # Module/class ``__getattr__`` and ``__dir__`` can define a dynamic
        # public surface even though their spelling looks private.
        return bool(name) and (
            not name.startswith("_")
            or name in exported_names
            or (qualified_name is not None and qualified_name in exported_names)
            or name in {"__getattr__", "__dir__"}
        )

    def visit_class(node: ast.ClassDef, prefix: str) -> bool:
        name = f"{prefix}.{node.name}" if prefix else node.name
        class_is_public = public(node.name, name)
        if class_is_public and not add(node, name, "class"):
            return False
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and class_is_public:
                child_name = f"{name}.{child.name}"
                if (public(child.name, child_name) or _is_user_facing_dunder(child.name)) and not add(
                    child, child_name, "method"
                ):
                    return False
            elif isinstance(child, ast.ClassDef) and class_is_public:
                if not visit_class(child, name):
                    return False
        return True

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and public(node.name):
            if not add(node, node.name, "function"):
                return None, None, "ambiguous repeated public declaration"
        elif isinstance(node, ast.ClassDef) and public(node.name):
            if not visit_class(node, ""):
                return None, None, "ambiguous repeated public declaration"
    # Public module exports frequently arrive through ``__init__.py`` imports,
    # ``__all__``, or constants rather than a local def/class.  The detector
    # cannot reliably name those APIs, so retain the whole module-level AST
    # fingerprint and turn a change into a file-level unknown later.
    module_body = [
        node
        for node in tree.body
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    module_fingerprint = "|".join(ast.dump(node, include_attributes=False) for node in module_body)
    return definitions, module_fingerprint, None


def _unknown_worktree_candidate(path: str, reason: str, dmap: dict) -> dict:
    symbol = "*"
    return {
        "candidate": f"{path}#{symbol}",
        "class": "unknown",
        "confidence": "unknown",
        "old": None,
        "new": None,
        "targets": _mapped_targets(path, symbol, dmap),
        "reason": reason,
        "evidence": "worktree-source",
    }


def _worktree_change_candidate(
    path: str, symbol: str, change_class: str, before: dict[str, str] | None, after: dict[str, str] | None, dmap: dict
) -> dict:
    """Build a manual-only candidate from a precise local AST comparison."""
    old = before.get("signature") if before else None
    new = after.get("signature") if after else None
    change_text = {
        "add_export": "public export added",
        "remove": "public export removed",
        "signature": "public signature changed",
        "body": "public body changed",
    }.get(change_class, "public source changed")
    return {
        "candidate": f"{path}#{symbol}",
        "class": change_class,
        # A local AST can prove the source shape, but not whether behaviour
        # changed from the user's perspective.  Keep it manual-only so this
        # detector never expands docs --mode auto's existing write authority.
        "confidence": "unknown",
        "old": old,
        "new": new,
        "targets": _mapped_targets(path, symbol, dmap),
        "reason": f"uncommitted worktree AST detected {change_text}; confirm documentation impact before applying a draft",
        "evidence": "worktree-python-ast",
    }


def _config_targets(path: str, symbol: str, dmap: dict) -> list[dict]:
    """Resolve exact key, closest parent, then a file-wide config mapping.

    Manifest key names escape literal dots/backslashes. Only unescaped dots
    separate parent keys; a literal ``a.b`` must never match the parent ``a``.
    """
    scoped = {key: value for key, value in dmap.items() if isinstance(key, str) and key.startswith(f"{path}#")}
    boundaries = []
    escaped = False
    for index, char in enumerate(symbol):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ".":
            boundaries.append(index)
    for name in [symbol, *(symbol[:index] for index in reversed(boundaries))]:
        targets = _mapped_targets(path, name, scoped)
        if targets:
            return targets
    return _mapped_targets(path, "*", scoped)


def _config_is_mapped(path: str, dmap: dict) -> bool:
    prefix = f"{path}#"
    return any(
        isinstance(key, str) and key.startswith(prefix) and key[len(prefix):]
        and _config_targets(path, key[len(prefix):], dmap)
        for key in dmap
    )


def _api_entry_evidence(entry: dict | None) -> dict | None:
    if not isinstance(entry, dict):
        return None
    return {
        "kind": entry.get("kind"), "visibility": entry.get("visibility"),
        "signature_fingerprint": entry.get("signature_fingerprint"),
        "body_fingerprint": entry.get("body_fingerprint"),
        "facets_fingerprint": state.stable_hash(entry.get("facets", {})),
    }


def _manifest_candidates(path: str, manifest: dict, dmap: dict, *, config: bool = False) -> list[dict]:
    rows = []
    for change in manifest["changes"]:
        symbol = change["id"].partition("#")[2]
        targets = _config_targets(path, symbol, dmap) if config else _mapped_targets(path, symbol, dmap)
        if config and not targets:
            continue
        class_name = {"added": "add_export", "removed": "remove", "signature": "signature", "surface": "surface"}.get(change["change"], "body")
        item = _worktree_change_candidate(path, symbol, class_name, change["before"], change["after"], dmap)
        facets = change["facets_changed"]
        detail = f"; changed facets: {', '.join(facets[:20])}" if facets else ""
        item.update(
            targets=targets, evidence="worktree-api-manifest", api_change=change["change"],
            facets_changed=facets,
            api_evidence={"before": _api_entry_evidence(change["before"]), "after": _api_entry_evidence(change["after"])},
            reason=f"static API manifest detected {change['change']}{detail}; confirm user-facing documentation impact",
        )
        rows.append(item)
    return rows


def _manifest_unknown(path: str, manifest: dict, dmap: dict, *, config: bool = False) -> dict | None:
    if not manifest.get("unknowns"):
        return None
    reasons = list(dict.fromkeys(str(item.get("reason") or "static API extraction was incomplete") for item in manifest["unknowns"]))
    row = _unknown_worktree_candidate(path, "; ".join(reasons[:5]), dmap)
    row.update(evidence="worktree-api-manifest", api_change="unknown", facets_changed=[],
               api_evidence={"unknowns_fingerprint": state.stable_hash(manifest["unknowns"])})
    if config and not row["targets"]:
        prefix = f"{path}#"
        targets = [_config_targets(path, key[len(prefix):], dmap) for key in dmap if isinstance(key, str) and key.startswith(prefix)]
        row["targets"] = [target for group in targets for target in group]
    return row


def worktree_candidates(repo: str = ".", dmap: dict | None = None) -> list[dict]:
    """Find uncommitted source API changes without pretending they are graph diffs.

    For Python, current source is compared to ``HEAD`` with the local AST and
    API manifest. JSON/TOML keys are reviewed only when explicitly mapped. A
    malformed Python file, an unsafe/oversized file, a source language AMU
    cannot parse, or a missing initial commit yields an *unknown* candidate.
    That makes an evidence gap visible to the agent instead of returning an
    empty candidate list that could be mistaken for "no docs impact."
    """
    dmap = dmap if isinstance(dmap, dict) else doc_map(repo)
    paths, has_head, query_error = _changed_worktree_paths(repo)
    if query_error:
        return _renumber([_unknown_worktree_candidate("<worktree>", query_error, dmap)])
    rows: list[dict] = []
    source_paths = 0
    for path in paths:
        config = Path(path).suffix.lower() in {".json", ".toml"}
        mapped_config = config and _config_is_mapped(path, dmap)
        if _is_ignored_worktree_path(path, repo, mapped_config=mapped_config):
            continue
        kind = _source_kind(path)
        if config:
            if not mapped_config:
                continue
            kind = "config"
        if kind is None:
            continue
        source_paths += 1
        if source_paths > _MAX_WORKTREE_SOURCE_PATHS:
            continue
        if kind == "unsupported":
            rows.append(
                _unknown_worktree_candidate(
                    path,
                    f"uncommitted {Path(path).suffix or 'source'} file is unsupported by the local docs detector; documentation impact is unknown",
                    dmap,
                )
            )
            continue
        if not has_head:
            rows.append(
                _unknown_worktree_candidate(
                    path,
                    "repository has no committed HEAD for a worktree API comparison; documentation impact is unknown",
                    dmap,
                )
            )
            continue
        current_state, current = _current_source(repo, path)
        head_state, head = _head_source(repo, path)
        if current_state == "error" or head_state == "error":
            rows.append(
                _unknown_worktree_candidate(
                    path,
                    "uncommitted source could not be read safely from both worktree and HEAD; documentation impact is unknown",
                    dmap,
                )
            )
            continue
        from amu import api_manifest
        missing_source = "{}" if config and Path(path).suffix.lower() == ".json" else ""
        before_source = head if head_state == "present" else missing_source
        after_source = current if current_state == "present" else missing_source
        manifest = api_manifest.compare_sources(before_source, after_source, path)
        manifest_rows = _manifest_candidates(path, manifest, dmap, config=config)
        manifest_gap = _manifest_unknown(path, manifest, dmap, config=config)
        if config:
            rows.extend(manifest_rows)
            if manifest_gap:
                rows.append(manifest_gap)
            continue
        before, before_module, before_error = (
            _public_definitions(head, path) if head_state == "present" and head is not None else ({}, "", None)
        )
        after, after_module, after_error = (
            _public_definitions(current, path) if current_state == "present" and current is not None else ({}, "", None)
        )
        if before_error or after_error or before is None or after is None:
            rows.append(
                _unknown_worktree_candidate(path, before_error or after_error or "Python AST comparison failed", dmap)
            )
            rows.extend(manifest_rows)
            continue
        legacy_rows = []
        for symbol in sorted(set(before) | set(after)):
            old, new = before.get(symbol), after.get(symbol)
            if old is None:
                legacy_rows.append(_worktree_change_candidate(path, symbol, "add_export", None, new, dmap))
            elif new is None:
                legacy_rows.append(_worktree_change_candidate(path, symbol, "remove", old, None, dmap))
            elif old["kind"] != new["kind"]:
                legacy_rows.append(_worktree_change_candidate(path, symbol, "remove", old, None, dmap))
                legacy_rows.append(_worktree_change_candidate(path, symbol, "add_export", None, new, dmap))
            elif old["signature_fingerprint"] != new["signature_fingerprint"]:
                legacy_rows.append(_worktree_change_candidate(path, symbol, "signature", old, new, dmap))
            elif old["body_fingerprint"] != new["body_fingerprint"]:
                legacy_rows.append(_worktree_change_candidate(path, symbol, "body", old, new, dmap))
        # A manifest assessment supersedes a duplicate legacy declaration row
        # while file-level legacy unknowns retain their conservative scope.
        assessed = {row["candidate"] for row in manifest_rows}
        unchanged_bodies = set()
        if manifest.get("complete"):
            old_entries = {entry["id"]: entry for entry in manifest["before"]["entries"]}
            new_entries = {entry["id"]: entry for entry in manifest["after"]["entries"]}
            unchanged_bodies = {
                identity for identity in old_entries.keys() & new_entries.keys()
                if old_entries[identity]["body_fingerprint"] == new_entries[identity]["body_fingerprint"]
            }
        # Legacy full-definition dumps include docstrings. A complete manifest
        # with the same supported body evidence establishes that those legacy
        # differences add no behavioral review signal. Partial coverage keeps
        # the old fallback and its file-level uncertainty intact.
        rows.extend(
            row for row in legacy_rows if row["candidate"] not in assessed
            and not (row["class"] == "body" and row["candidate"] in unchanged_bodies)
        )
        rows.extend(manifest_rows)
        if manifest_gap:
            rows.append(manifest_gap)
        if before_module != after_module:
            rows.append(
                _unknown_worktree_candidate(
                    path,
                    "uncommitted module-level source changed; exported API impact is unknown until reviewed",
                    dmap,
                )
            )
    if source_paths > _MAX_WORKTREE_SOURCE_PATHS:
        rows.append(
            _unknown_worktree_candidate(
                "<worktree>",
                f"more than {_MAX_WORKTREE_SOURCE_PATHS} changed source paths; remaining documentation impact is unknown",
                dmap,
            )
        )
    return _renumber(rows)


def all_candidates(diff_data: dict | None, repo: str = ".", dmap: dict | None = None) -> list[dict]:
    """Preserve committed graph-diff candidates and append manual worktree evidence."""
    dmap = dmap if isinstance(dmap, dict) else doc_map(repo)
    return _renumber([*candidates(diff_data, dmap), *worktree_candidates(repo, dmap)])


def worktree_auto_block(candidate: dict, rows: list[dict]) -> dict | None:
    """Return why a graph candidate cannot be auto-applied over live evidence.

    A commit-ref graph candidate remains useful for manual review, but it is
    unsafe to write its proposed docs paragraph automatically when an agent
    has since made an uncommitted edit to the same symbol.  A file-level
    ``path#*`` worktree unknown is intentionally broader: it blocks every
    graph candidate in that file until a human resolves the source gap.
    """
    if str(candidate.get("evidence") or "").startswith("worktree"):
        return None
    raw_target = candidate.get("candidate")
    if not isinstance(raw_target, str):
        return None
    path, marker, symbol = raw_target.partition("#")
    if not marker or not path or not symbol:
        return None
    for worktree in rows:
        if not str(worktree.get("evidence") or "").startswith("worktree"):
            continue
        raw_worktree_target = worktree.get("candidate")
        if not isinstance(raw_worktree_target, str):
            continue
        worktree_path, worktree_marker, worktree_symbol = raw_worktree_target.partition("#")
        if not worktree_marker or not worktree_symbol:
            continue
        if worktree_path == "<worktree>" and worktree_symbol == "*":
            return {
                "worktree_candidate": raw_worktree_target,
                "reason": (
                    "auto update withheld: worktree changes could not be enumerated; "
                    "review committed graph candidates manually"
                ),
            }
        if worktree_path != path:
            continue
        if worktree_symbol == "*" or symbol == "*" or worktree_symbol == symbol:
            scope = "file-level" if worktree_symbol == "*" or symbol == "*" else "exact-target"
            return {
                "worktree_candidate": raw_worktree_target,
                "reason": (
                    f"auto update withheld: {scope} uncommitted worktree evidence "
                    f"({raw_worktree_target}) shadows committed graph candidate {raw_target}; review manually"
                ),
            }
    return None


def other_mentions(symbol: str, repo: str = ".", docs_dir: str = "docs") -> list[str]:
    name = symbol.split("#")[-1].split(".")[-1]
    hits = []
    for md in glob.glob(str(Path(repo) / docs_dir / "**" / "*.md"), recursive=True):
        for i, line in enumerate(Path(md).read_text(errors="ignore").splitlines(), 1):
            if name in line:
                hits.append(f"{Path(md).relative_to(repo)}:{i}")
    return hits[:20]


def draft(c: dict) -> str:
    if c.get("confidence") == "unknown":
        reason = c.get("reason") or "the documentation impact is not derivable from this source evidence"
        label = str(c.get("class") or "source change").replace("_", " ")
        return f"SUGGESTED (unknown): `{c['candidate']}` {label} — {reason}."
    old = f"`{c['old']}`" if c.get("old") else "(no previous signature recorded)"
    new = f"`{c['new']}`" if c.get("new") else "(removed)"
    return f"SUGGESTED: `{c['candidate']}` — {c['class']}. Was {old}; now {new}. Update the section to match the new shape."


def packet(c: dict, repo: str = ".", docs_dir: str = "docs") -> dict:
    raw_targets = c.get("targets") if isinstance(c.get("targets"), list) else []
    t = next(
        (target for target in raw_targets if isinstance(target, dict) and isinstance(target.get("file"), str) and isinstance(target.get("section"), str)),
        {"file": None, "section": None},
    )
    return {"id": c.get("id"), "fingerprint": c.get("fingerprint"), "candidate": c.get("candidate"), "doc_file": t["file"], "section": t["section"], "old": c.get("old"), "new": c.get("new"),
            "consistent_with_diff": c["confidence"] != "unknown", "concerns": [] if c["confidence"] == "sound" else [c["reason"]],
            "evidence": c.get("evidence", "entire-graph-diff"), "api_change": c.get("api_change"),
            "facets_changed": c.get("facets_changed", []), "other_mentions": other_mentions(c["candidate"], repo, docs_dir),
            "next": f"amu docs --apply {c['id']}", "draft": draft(c)}


def _safe_mapped_doc_path(target: dict, repo: str) -> Path | None:
    """Return an existing mapped document only when it stays in docs_dir."""
    filename = target.get("file") if isinstance(target, dict) else None
    if not isinstance(filename, str) or not filename:
        return None

    raw_path = Path(filename)
    if raw_path.is_absolute() or ".." in raw_path.parts:
        return None

    try:
        repo_root = Path(repo).resolve()
        doc_path = (repo_root / raw_path).resolve()
        doc_path.relative_to(repo_root)
        docs_dir = Path(state.config(repo).get("docs_dir", "docs"))
        if docs_dir.is_absolute() or ".." in docs_dir.parts:
            return None
        docs_root = (repo_root / docs_dir).resolve()
        docs_root.relative_to(repo_root)
        doc_path.relative_to(docs_root)
        if doc_path.suffix.lower() not in {".md", ".mdx", ".rst"} or not doc_path.is_file():
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    return doc_path


def apply(c: dict, repo: str = ".", *, detailed: bool = False) -> bool | dict:
    """Append the draft under the mapped section. Only called for a human-confirmed or sound+mapped candidate."""
    targets = c.get("targets")
    if not isinstance(targets, list) or not targets:
        return False
    t = targets[0]
    section = t.get("section") if isinstance(t, dict) else None
    if not isinstance(section, str):
        return False
    p = _safe_mapped_doc_path(t, repo)
    if p is None:
        return False
    from amu import docreview, docsave

    try:
        p, text, start, end = docreview._region(c, repo)
    except (OSError, ValueError):
        return False
    addition = draft(c).replace("SUGGESTED: ", "")
    if addition in text.splitlines():
        return False
    position = start if section.strip() else end
    newline = "\r\n" if "\r\n" in text else "\n"
    insertion = newline + addition + newline
    result = docsave.save(repo, p, text, position, position, insertion)
    return result if detailed else result["saved"]
