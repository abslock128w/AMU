"""Recorded per-test execution as a ranking boost. Never a gate, never a reason to skip a test.

Staleness is judged per file. A recording stays usable for every file whose bytes
still match it; a file edited since is used as approximate evidence.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

DEFAULTS = ('.amu/observed-tests.json', 'coverage.json', '.coverage')
MAX_BYTES = 64 * 1024 * 1024


def _test_identity(context):
    nodeid, sep, phase = context.rpartition('|')
    if not sep:
        nodeid = context
    if '::' not in nodeid:
        return None
    path, name = nodeid.split('::', 1)
    return path + '#' + re.sub(r'\[.*\]$', '', name).replace('::', '.')


def _relative(root, name):
    path = Path(name)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(root).as_posix()
        except ValueError:
            return None
    return path.as_posix()


def _artifact(root, raw, hashes):
    table = raw['context_table']
    recorded = raw['snapshot']['source_hashes']
    files = {path: {int(line): {table[i] for i in indexes} for line, indexes in rows.items()}
             for path, rows in raw['observations'].items()}
    return files, {path for path in files if recorded.get(path) != hashes.get(path)}


def _json_report(root, raw, hashes, recorded_at):
    files = {}
    for name, entry in raw['files'].items():
        path = _relative(root, name)
        if path:
            files[path] = {int(line): set(contexts) for line, contexts in entry.get('contexts', {}).items()}
    return files, _newer(root, files, recorded_at)


def _data_file(root, source, hashes, recorded_at):
    import coverage  # optional; absence is reported as a gap by the caller
    data = coverage.CoverageData(basename=str(source))
    data.read()
    files = {}
    try:
        for name in data.measured_files():
            path = _relative(root, name)
            if path and not data.file_tracer(name):
                files[path] = {int(line): set(contexts) for line, contexts in data.contexts_by_lineno(name).items()}
    finally:
        data.close()
    return files, _newer(root, files, recorded_at)


def _newer(root, files, recorded_at):
    """Without recorded hashes, a file modified after the recording is treated as edited."""
    stale = set()
    for path in files:
        try:
            if (root/path).stat().st_mtime > recorded_at:
                stale.add(path)
        except OSError:
            stale.add(path)
    return stale


def load(root, evidence, hashes, gaps):
    """Return ({'files', 'stale'} or None, status or None). Problems become gaps, not errors."""
    explicit = evidence is not None
    candidates = [evidence] if explicit else DEFAULTS
    for name in candidates:
        source = Path(name) if Path(name).is_absolute() else root/name
        if not source.is_file():
            if explicit:
                gaps.append(f'coverage evidence {name} not found; static ranking only')
            continue
        try:
            if source.stat().st_size > MAX_BYTES:
                raise ValueError('larger than 64 MiB')
            head = source.read_bytes()[:16]
            if head.startswith(b'SQLite format 3'):
                files, stale = _data_file(root, source, hashes, source.stat().st_mtime)
            else:
                raw = json.loads(source.read_text())
                if raw.get('format') == 'amu-observed-tests':
                    files, stale = _artifact(root, raw, hashes)
                else:
                    files, stale = _json_report(root, raw, hashes, source.stat().st_mtime)
        except ImportError:
            gaps.append(f'{name}: reading Coverage.py data needs the coverage package; static ranking only')
            continue
        except Exception as exc:  # any unreadable recording degrades to static ranking
            gaps.append(f'{name}: unreadable coverage evidence ({type(exc).__name__}); static ranking only')
            continue
        contexts = any(_test_identity(c) for rows in files.values() for found in rows.values() for c in found)
        if not contexts:
            gaps.append(f'{name}: no per-test contexts; record with --cov-context=test')
            continue
        used = dict(evidence=str(name), files=len(files), edited_since=len(stale))
        return dict(files=files, stale=stale), used
    return None, None


def tests_for(observed, record, tests, lines=()):
    """{test identity: (exactness, rarity)} for tests recorded executing this symbol.

    ``lines`` are the changed lines inside the symbol. ``rarity`` is 0 for a test that only
    entered the declaration, and otherwise grows as fewer tests share the changed line it ran:
    a line one test executes says more than a line every test passes through. It is None for
    every test when no recorded test executed any changed line.
    """
    if not observed or record['kind'] not in ('function', 'class', 'variable'):
        return {}
    rows = observed['files'].get(record['path'])
    if not rows:
        return {}
    exactness = 0.5 if record['path'] in observed['stale'] else 1.0
    found, rarity = {}, {}
    for start, end in record['spans']:
        for line in range(start, end + 1):
            ran = {ident for ident in map(_test_identity, rows.get(line, ())) if ident in tests}
            # Entering a function is evidence about that function. A class or module span holds
            # unrelated members, so there only the changed lines themselves count.
            if record['kind'] == 'function' or line in lines:
                for ident in ran:
                    found[ident] = exactness
            if line in lines and ran:
                share = 1 / math.log2(1 + len(ran))
                for ident in ran:
                    rarity[ident] = max(rarity.get(ident, 0.0), share)
    return {ident: (value, rarity.get(ident, 0.0) if rarity else None) for ident, value in found.items()}
