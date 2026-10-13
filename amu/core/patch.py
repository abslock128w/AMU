"""Best-effort changed-line extraction; malformed files become gaps, never hide siblings."""
import re
from pathlib import Path, PurePosixPath

MAX_BYTES = 2 * 1024 * 1024
MAX_FILES = 200
HUNK = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')


def changes(root, patch):
    gaps, result, skipped = [], {}, []
    if len(patch.encode()) > MAX_BYTES:
        gaps.append('Patch exceeds 2 MiB; only the first 2 MiB inspected')
        patch = patch.encode()[:MAX_BYTES].decode(errors='ignore')
    lines = patch.splitlines(keepends=True)
    current = None
    before_line = after_line = 0
    removed_in_hunk = False
    for line in lines:
        if line.startswith('--- '):
            current = None
        elif line.startswith('+++ '):
            raw = line[4:].strip().split('\t')[0]
            if raw == '/dev/null':
                # Deleted-file location is in the preceding --- header.
                continue
            path = raw[2:] if raw.startswith('b/') else raw
            pure = PurePosixPath(path)
            if pure.is_absolute() or '..' in pure.parts or any(p in {'.git', '.amu'} for p in pure.parts):
                gaps.append('Unsafe patch path skipped')
                continue
            if pure.suffix not in {'.py', '.pyi'}:
                skipped.append(path)
                continue
            if len(result) >= MAX_FILES and path not in result:
                gaps.append('Patch exceeds 200 Python files; remaining files skipped')
                continue
            current = result.setdefault(path, {'old': set(), 'new': set(), 'added': [], 'removed': [], 'anchor': set(), 'hunks': []})
        elif line.startswith('@@'):
            match = HUNK.match(line)
            if match:
                before_line, after_line = int(match[1]), int(match[3])
                removed_in_hunk = False
                if current is not None:
                    current['hunks'].append([before_line, []])
            else:
                gaps.append('Unrecognized diff hunk')
                current = None
        elif current is not None:
            if current['hunks'] and line[:1] in '-+ ':
                current['hunks'][-1][1].append(line)
            if line.startswith('-'):
                current['old'].add(before_line)
                removed_in_hunk = True
                current['removed'].append((before_line, line[1:]))
                before_line += 1
            elif line.startswith('+'):
                if not removed_in_hunk:
                    # A pure insertion is anchored to the parent line it follows.
                    current['anchor'].add(max(1, before_line - 1))
                current['new'].add(after_line)
                current['added'].append((after_line, line[1:]))
                after_line += 1
            elif line.startswith(' '):
                before_line += 1
                after_line += 1
    if skipped:
        gaps.append(f'Skipped {len(set(skipped))} non-Python file(s)')
    return result, gaps


def applied(root, path, edit):
    """Whether the tree already contains this file's change (an agent's edited worktree)."""
    try:
        lines = (Path(root)/path).read_text(errors='replace').splitlines()
    except OSError:
        return False

    def share(pairs):
        pairs = [(n, text.rstrip('\r\n')) for n, text in pairs if text.strip()]
        return sum(0 < n <= len(lines) and lines[n-1] == text for n, text in pairs) / len(pairs) if pairs else None

    new, old = share(edit['added']), share(edit['removed'])
    if new is None:
        return old == 0 and bool(edit['removed'])
    return new == 1 and (old is None or old < 1)


def candidate(root, path, edit):
    """The file as it would be after the patch, built in memory. None when it cannot be established."""
    try:
        source = (Path(root)/path).read_text()
    except (OSError, UnicodeError):
        source = ''
    if applied(root, path, edit):
        return source
    lines, out, position = source.splitlines(keepends=True), [], 0
    for start, body in edit['hunks']:
        start = max(start - 1, 0)
        if start < position or start > len(lines):
            return None
        out.extend(lines[position:start])
        position = start
        for row in body:
            sign, text = row[:1], row[1:]
            if sign == '+':
                out.append(text)
            else:
                if position >= len(lines) or lines[position].rstrip('\r\n') != text.rstrip('\r\n'):
                    return None
                if sign == ' ':
                    out.append(lines[position])
                position += 1
    out.extend(lines[position:])
    return ''.join(out)


def renumber(edit):
    """Map a parent line number to its place in the patched file.

    A removed line maps to where its replacement landed, so evidence recorded on it
    still counts as evidence about the changed lines.
    """
    table, breaks, offset = {}, [], 0
    for start, body in edit['hunks']:
        old, new = start, start + offset
        if start == 0:
            old, new = 1, 1 + offset
        for row in body:
            sign = row[:1]
            if sign == ' ':
                table[old] = new
                old += 1
                new += 1
            elif sign == '-':
                table[old] = new
                old += 1
            elif sign == '+':
                new += 1
        offset = new - old
        breaks.append((old, offset))

    def place(line):
        if line in table:
            return table[line]
        shift = 0
        for after, value in breaks:
            if line >= after:
                shift = value
        return line + shift
    return place


def original(root, path, edit):
    """The file as it was before the patch, whichever side the tree holds. None when it cannot be established."""
    try:
        source = (Path(root)/path).read_text()
    except (OSError, UnicodeError):
        return None
    if not applied(root, path, edit):
        return source
    flipped = dict(edit, hunks=[[start, [('+' if row[:1] == '-' else '-' if row[:1] == '+' else ' ') + row[1:] for row in body]]
                               for start, body in edit['hunks']])
    # Hunk starts are parent positions; walking the patched file needs patched positions.
    place, moved = renumber(edit), []
    for (start, _), (_, body) in zip(edit['hunks'], flipped['hunks']):
        moved.append([place(start) if start else 0, body])
    lines, out, position = source.splitlines(keepends=True), [], 0
    for start, body in moved:
        start = max(start - 1, 0)
        if start < position or start > len(lines):
            return None
        out.extend(lines[position:start])
        position = start
        for row in body:
            sign, text = row[:1], row[1:]
            if sign == '+':
                out.append(text)
            else:
                if position >= len(lines) or lines[position].rstrip('\r\n') != text.rstrip('\r\n'):
                    return None
                if sign == ' ':
                    out.append(lines[position])
                position += 1
    out.extend(lines[position:])
    return ''.join(out)


def align(old_text, new_text):
    """Map line numbers of ``old_text`` to ``new_text`` for the lines the two versions share."""
    import difflib
    old, new = old_text.splitlines(), new_text.splitlines()
    table = {}
    for a, b, size in difflib.SequenceMatcher(None, old, new, autojunk=False).get_matching_blocks():
        for offset in range(size):
            table[a + offset + 1] = b + offset + 1
    return table
