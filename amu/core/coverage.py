"""Recorded per-test execution as a ranking boost. Never a gate, never a reason to skip a test.

Staleness is judged per file. A recording stays usable for every file whose bytes
still match it; a file edited since is used as approximate evidence.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import zlib
from pathlib import Path

DEFAULTS = ('.amu/observed-tests.json', 'coverage.json', '.coverage')
MAX_BYTES = 1024 * 1024 * 1024


def _test_identity(context):
    nodeid, sep, phase = context.rpartition('|')
    if not sep:
        nodeid = context
    if '::' not in nodeid:
        return None
    path, name = nodeid.split('::', 1)
    return path + '#' + re.sub(r'\[.*\]$', '', name).replace('::', '.')


def _repo_path(root, name, known=()):
    path = Path(name)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        pass
    # Recorded elsewhere (CI, another checkout): match the longest path suffix this tree has.
    parts = path.parts
    for start in range(1, len(parts)):
        candidate = '/'.join(parts[start:])
        if candidate in known:
            return candidate
    return None


def _artifact(root, raw, hashes):
    table = raw['context_table']
    recorded = raw['snapshot']['source_hashes']
    files = {path: {int(line): {table[i] for i in indexes} for line, indexes in rows.items()}
             for path, rows in raw['observations'].items()}
    return files, {path for path in files if recorded.get(path) != hashes.get(path)}


def _json_report(root, raw, hashes, recorded_at):
    files = {}
    for name, entry in raw['files'].items():
        path = _repo_path(root, name, hashes)
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
            path = _repo_path(root, name, hashes)
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


class _Shards(dict):
    """Per-file line attribution of a stored recording, read from disk only for the files asked about."""

    def __init__(self, base, recorded, hashes, labels):
        super().__init__()
        self.base, self.recorded, self.hashes, self.labels = base, recorded, hashes, labels
        self.functions, self.sources = {}, {}

    def _read(self, path):
        if path in self.recorded and not dict.__contains__(self, path):
            # The shard of the file's current bytes if one exists, else the one from the recording.
            for sha in (self.hashes.get(path), self.recorded[path]):
                shard = self.base/'files'/(hashlib.sha256((path + '\0' + str(sha)).encode()).hexdigest() + '.json')
                try:
                    raw = json.loads(shard.read_text())
                except (OSError, ValueError):
                    continue
                dict.__setitem__(self, path, {int(line): {self.labels[i] for i in found} for line, found in raw['lines'].items()})
                self.functions[path] = {qual: {self.labels[i] for i in found} for qual, found in raw.get('functions', {}).items()}
                self.sources[path] = raw.get('source') if raw.get('sha') != self.hashes.get(path) else None
                break
            else:
                dict.__setitem__(self, path, {})
                self.functions[path] = {}

    def __contains__(self, path):
        return path in self.recorded

    def get(self, path, default=None):
        self._read(path)
        return dict.get(self, path, default)

    def __getitem__(self, path):
        self._read(path)
        return dict.__getitem__(self, path)

    def by_function(self, path):
        self._read(path)
        return self.functions.get(path)

    def recorded_text(self, path):
        """The file as it was when recorded, when that differs from the tree now; else None."""
        self._read(path)
        packed = self.sources.get(path)
        if not packed:
            return None
        try:
            return zlib.decompress(base64.b64decode(packed)).decode(errors='replace')
        except (ValueError, zlib.error):
            return None


def recording(cache, hashes):
    """The recording stored beside the index by ``--observe``, or None."""
    base = Path(cache)/'observed'
    try:
        meta = json.loads((base/'meta.json').read_text())
        if meta.get('format') != 'amu-observed-2':
            return None
    except (OSError, ValueError):
        return None
    recorded = meta['files']
    stale = {path for path, sha in recorded.items() if hashes.get(path) != sha}
    files = _Shards(base, recorded, hashes, meta['labels'])
    status = dict(evidence='recording', tests=len(meta['labels']), files=len(recorded), edited_since=len(stale))
    return dict(files=files, stale=stale, by_function=files.by_function, recorded=recorded, text=files.recorded_text), status


def identifier(tests, graph=None):
    """A function from a recorded context to the identity of the test it names, or None.

    Contexts are ``path::name|phase`` from pytest-cov, or dotted names from coverage's own
    ``test_function`` context. A dotted name carries the runtime class, so a method inherited
    from a base test class is traced to the class that defines it.
    """
    modules, memo = {}, {}
    for ident, record in tests.items():
        parts = record['path'][:-3].split('/')
        for start in range(len(parts)):
            modules.setdefault('.'.join(parts[start:]), set()).add(record['path'])

    def dotted(label):
        parts = label.split('.')
        for cut in range(len(parts) - 1, 0, -1):
            paths = modules.get('.'.join(parts[:cut]))
            if not paths:
                continue
            for path in sorted(paths):
                ident = path + '#' + '.'.join(parts[cut:])
                if ident in tests:
                    return ident
                owner = path + '#' + '.'.join(parts[cut:-1])
                if graph is not None and len(parts) - cut >= 2 and owner in graph.records:
                    # Inherited: look for the method on an ancestor class.
                    for base, subs in graph.subclasses.items():
                        if owner in graph.descendants(base) and (base + '.' + parts[-1]) in tests:
                            return base + '.' + parts[-1]
            return None
        return None

    def identify(context):
        if context not in memo:
            memo[context] = _test_identity(context) if '::' in context else dotted(context) if context else None
        return memo[context]
    return identify


def load(root, evidence, hashes, gaps, cache=None):
    """Return ({'files', 'stale', ...} or None, status or None). Problems become gaps, not errors."""
    explicit = evidence is not None
    if not explicit and cache is not None:
        own = recording(cache, hashes)
        if own is not None:
            return own
    candidates = [evidence] if explicit else DEFAULTS
    for name in candidates:
        source = Path(name) if Path(name).is_absolute() else root/name
        if not source.is_file():
            if explicit:
                gaps.append(f'coverage evidence {name} not found; static ranking only')
            continue
        try:
            if source.stat().st_size > MAX_BYTES:
                raise ValueError('larger than 1 GiB')
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


def tests_for(observed, record, tests, lines=(), identify=None):
    """{test identity: (exactness, rarity)} for tests recorded executing this symbol.

    ``lines`` are the changed lines inside the symbol. ``rarity`` is 0 for a test that only
    entered the declaration, and otherwise grows as fewer tests share the changed line it ran:
    a line one test executes says more than a line every test passes through. It is None for
    every test when no recorded test executed any changed line.

    When the file was edited after the recording and the line numbers cannot be trusted, the
    recording's own per-function attribution is used, looked up by the function's name.
    """
    if not observed or record['kind'] not in ('function', 'class', 'variable'):
        return {}
    identify = identify or _test_identity
    path = record['path']
    if path in observed['stale'] and path not in observed.get('translated', ()) and observed.get('by_function'):
        recorded = (observed['by_function'](path) or {}).get(record['qual'])
        if record['kind'] != 'function' or recorded is None:
            return {}
        return {ident: (0.5, None) for ident in map(identify, recorded) if ident in tests}
    rows = observed['files'].get(path)
    if not rows:
        return {}
    exactness = 0.5 if path in observed['stale'] else 1.0
    found, rarity = {}, {}
    for start, end in record['spans']:
        for line in range(max(start, record.get('body', start)) if record['kind'] == 'function' else start, end + 1):
            ran = {ident for ident in map(identify, rows.get(line, ())) if ident in tests}
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
