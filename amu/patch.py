"""Exact, bounded unified diffs. This module never reads or writes a path."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

MAX_PATCH_BYTES = 48 * 1024
MAX_FILES = 8
MAX_HUNKS = 128
MAX_CANDIDATE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_SOURCE_BYTES = 8 * 1024 * 1024
PYTHON_SUFFIXES = frozenset({".py", ".pyi"})
MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})
_HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: [^\r\n]*)?\n?\Z")


class PatchError(ValueError):
    """An unsupported diff, unsafe path or non-exact application."""


@dataclass(frozen=True)
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class FilePatch:
    path: str
    operation: str
    hunks: tuple[Hunk, ...]


def source_path(value: str) -> str:
    path = PurePosixPath(value)
    if (not value or len(value.encode("utf-8")) > 4096 or path.is_absolute()
            or path.as_posix() != value or path == PurePosixPath(".")
            or any(part in {"..", ".git", ".amu", ".entire"} for part in path.parts)
            or any(char in value for char in "\\\t\r\n\x00#\"")
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise PatchError("diff path must be a normalized repository-relative source path")
    if path.suffix not in PYTHON_SUFFIXES | MARKDOWN_SUFFIXES:
        raise PatchError("concrete patch inspection supports Python .py/.pyi and Markdown .md/.markdown files only")
    return value


def _header(line: str, prefix: str) -> str | None:
    if not line.startswith(prefix):
        raise PatchError("diff requires paired --- and +++ file headers")
    name = line[len(prefix):].removesuffix("\n")
    if name == "/dev/null":
        return None
    expected = "a/" if prefix == "--- " else "b/"
    if not name.startswith(expected):
        raise PatchError("file headers must use a/ and b/ prefixes (or /dev/null)")
    return source_path(name[2:])


def parse_diff(diff: str) -> tuple[FilePatch, ...]:
    if not isinstance(diff, str) or not diff:
        raise PatchError("patch must be nonempty unified-diff text")
    try:
        size = len(diff.encode("utf-8"))
    except UnicodeError as exc:
        raise PatchError("patch must be valid UTF-8") from exc
    if size > MAX_PATCH_BYTES:
        raise PatchError(f"patch exceeds its {MAX_PATCH_BYTES}-byte limit")
    if any((ord(c) < 32 and c not in "\n\t") or ord(c) == 127 for c in diff):
        raise PatchError("patch contains unsupported controls or CRLF; use a UTF-8 LF diff")
    lines, pos, files, seen, hunk_count = diff.splitlines(keepends=True), 0, [], set(), 0
    while pos < len(lines):
        git_paths = None
        mode_metadata = None
        if lines[pos].startswith("diff --git "):
            match = re.fullmatch(r"diff --git a/([^\n]+) b/([^\n]+)\n?", lines[pos])
            if not match:
                raise PatchError("ambiguous or quoted git file header is unsupported")
            git_paths = (source_path(match[1]), source_path(match[2]))
            if git_paths[0] != git_paths[1]:
                raise PatchError("renames and copies are unsupported")
            pos += 1
            # Only ordinary source mode metadata and opaque index hashes are
            # accepted. Application is checked against actual bytes below.
            while pos < len(lines) and not lines[pos].startswith("--- "):
                metadata = lines[pos].rstrip("\n")
                if not (re.fullmatch(r"index [0-9a-f]+\.\.[0-9a-f]+(?: 100644)?", metadata)
                        or metadata in {"new file mode 100644", "deleted file mode 100644"}):
                    raise PatchError("unsupported diff metadata (modes, renames, binary or combined diffs)")
                if metadata.startswith(("new file", "deleted file")):
                    if mode_metadata is not None:
                        raise PatchError("duplicate file mode metadata")
                    mode_metadata = metadata
                pos += 1
        if pos + 1 >= len(lines):
            raise PatchError("diff file headers are incomplete")
        old, new = _header(lines[pos], "--- "), _header(lines[pos + 1], "+++ ")
        pos += 2
        if old is None and new is None or old is not None and new is not None and old != new:
            raise PatchError("ambiguous file replacement or rename is unsupported")
        path = new if new is not None else old
        if git_paths is not None and path != git_paths[0]:
            raise PatchError("git and unified file headers disagree")
        if path in seen:
            raise PatchError("duplicate diff file entries are unsupported")
        seen.add(path)
        hunks = []
        while pos < len(lines) and lines[pos].startswith("@@"):
            match = _HUNK.fullmatch(lines[pos])
            if not match:
                raise PatchError("unsupported or malformed hunk header")
            if any(value and (len(value) > 8 or int(value) > MAX_CANDIDATE_BYTES) for value in match.groups()):
                raise PatchError("hunk coordinates exceed the bounded source limit")
            a, ac, b, bc = int(match[1]), int(match[2] or 1), int(match[3]), int(match[4] or 1)
            if (ac and not a) or (bc and not b) or ac == bc == 0:
                raise PatchError("hunk has invalid start or empty ranges")
            pos += 1
            body, old_used, new_used = [], 0, 0
            while pos < len(lines):
                line = lines[pos]
                if line == "\\ No newline at end of file\n" or line == "\\ No newline at end of file":
                    if not body or not body[-1][1].endswith("\n"):
                        raise PatchError("misplaced no-newline marker")
                    mode, content = body[-1]
                    body[-1] = (mode, content[:-1])
                    pos += 1
                    continue
                if old_used == ac and new_used == bc:
                    break
                if not line or line[0] not in " +-":
                    raise PatchError("hunk body does not match its declared line counts")
                mode, content = line[0], line[1:]
                if not content.endswith("\n"):
                    raise PatchError("hunk lines require LF; use an explicit no-newline marker")
                body.append((mode, content))
                old_used += mode != "+"
                new_used += mode != "-"
                if old_used > ac or new_used > bc:
                    raise PatchError("hunk body exceeds its declared line counts")
                pos += 1
            if old_used != ac or new_used != bc or not any(mode in "+-" for mode, _ in body):
                raise PatchError("hunk is incomplete or has no changes")
            hunks.append(Hunk(a, ac, b, bc, tuple(body)))
            hunk_count += 1
            if hunk_count > MAX_HUNKS:
                raise PatchError("patch exceeds its hunk limit")
        if not hunks:
            raise PatchError("each file requires a nonempty content hunk")
        operation = "add" if old is None else "delete" if new is None else "modify"
        if mode_metadata is not None and mode_metadata != {"add": "new file mode 100644", "delete": "deleted file mode 100644"}.get(operation):
            raise PatchError("file mode metadata disagrees with unified headers")
        if operation == "add" and any(h.old_count for h in hunks):
            raise PatchError("new-file hunks cannot consume source lines")
        if operation == "delete" and any(h.new_count for h in hunks):
            raise PatchError("deleted-file hunks cannot produce source lines")
        files.append(FilePatch(path, operation, tuple(hunks)))
        if len(files) > MAX_FILES:
            raise PatchError("patch exceeds its file limit")
    return tuple(files)


def apply_file(file: FilePatch, before: str | None) -> str | None:
    """Require exact context, offsets, counts and newline state; no fuzz."""
    if (file.operation == "add") != (before is None):
        raise PatchError("addition requires an absent file; modification/deletion requires an existing file")
    original, output, offset = (before or "").splitlines(keepends=True), [], 0
    for hunk in file.hunks:
        start = hunk.old_start - 1 if hunk.old_count else hunk.old_start
        new_start = hunk.new_start - 1 if hunk.new_count else hunk.new_start
        if start < offset or start > len(original):
            raise PatchError("hunks overlap, are out of order or exceed the source")
        output.extend(original[offset:start])
        if new_start != len(output):
            raise PatchError("new hunk position does not match exact application")
        offset = start
        for mode, content in hunk.lines:
            if mode != "+":
                if offset >= len(original) or original[offset] != content:
                    raise PatchError("hunk context does not match the identified source baseline; no fuzzy application")
                offset += 1
            if mode != "-":
                output.append(content)
    output.extend(original[offset:])
    if any(not line.endswith("\n") for line in output[:-1]):
        raise PatchError("no-newline marker is valid only for the final file line")
    result = "".join(output)
    if len(result.encode("utf-8")) > MAX_CANDIDATE_BYTES:
        raise PatchError("candidate source exceeds its byte limit")
    if file.operation == "delete":
        if result:
            raise PatchError("deletion must remove the complete file")
        return None
    return result


def digest(text: str | None) -> str | None:
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text is not None else None
