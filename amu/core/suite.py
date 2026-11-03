"""Whole-suite recordings: which tests ran each line of the project's code, at one commit, made outside the pull request
job (a scheduled workflow on the default branch: ``amu.ci.record_suite``).

A recording is one gzipped JSON file named after its commit, ``<sha>.json.gz``:

- ``tests``: every test id the recording saw, in order; ``planned``/``ran``/``seconds``: how much of the suite ran;
- ``files``: for each production file, the hash of its text and ``{line: set number}``;
- ``sets``: the distinct sets of tests, each a zlib-compressed list of delta-encoded test numbers (base64). Lines run by
  the same tests share one set, so a recording is far smaller than one entry per test and line.

A pull request job takes the recording of the nearest ancestor of its merge base (``nearest``) and reads it through the
diff since that commit (``Recording.tests``): a line unchanged since then has its recorded tests; a line that changed
since then has every test that ran its file; a test file changed since then has all its tests added
(``changed_tests``). It never records the suite itself.
"""
from __future__ import annotations

import base64
import difflib
import gzip
import hashlib
import json
import os
import subprocess
import time
import zlib
from array import array
from pathlib import Path

FORMAT = 1
IMPORT = '<import>'


def _sha(text):
    return hashlib.sha256((text or '').encode()).hexdigest()[:16]


def _encode(numbers):
    out, last = bytearray(), -1
    for n in numbers:
        step = n - last
        last = n
        while step >= 0x80:
            out.append((step & 0x7F) | 0x80)
            step >>= 7
        out.append(step)
    return base64.b64encode(zlib.compress(bytes(out), 9)).decode()


def _decode(text):
    data, out, value, shift, last = zlib.decompress(base64.b64decode(text)), [], 0, 0, -1
    for byte in data:
        value |= (byte & 0x7F) << shift
        if byte & 0x80:
            shift += 7
            continue
        last += value
        out.append(last)
        value, shift = 0, 0
    return out


def _git(root, *args):
    done = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, timeout=120)
    return done.stdout.strip() if done.returncode == 0 else None


def production_files(root):
    """Every Python file of the project that is not a test file, relative to ``root``."""
    from .replay import is_test_file
    from .shadow import IGNORED, _ignored
    root, found = Path(root), []
    for folder, names, files in os.walk(root):
        names[:] = sorted(n for n in names if not _ignored(n) and n not in IGNORED)
        for name in sorted(files):
            if name.endswith('.py'):
                path = (Path(folder)/name).relative_to(root).as_posix()
                if not is_test_file(path):
                    found.append(path)
    return found


def compact(out_folder, root, files, planned, cwd=None):
    """A recording from the hook's output in ``out_folder`` (record.prepare with ``all_files``), read a line at a time:
    the whole suite's coverage does not fit in memory as sets."""
    from .record import identity
    tests, index, names, lines = [], {}, {}, {}
    for part in sorted(Path(out_folder).glob('*.jsonl')):
        with open(part) as handle:
            for text in handle:
                try:
                    event = json.loads(text)
                except ValueError:
                    continue
                if event.get('k') != 'cov':
                    continue
                raw = event.get('t', IMPORT)
                test = names.get(raw)
                if test is None:
                    test = names[raw] = identity(raw, root, cwd)
                number = index.get(test)
                if number is None:
                    number = index[test] = len(tests)
                    tests.append(test)
                for path, found in event.get('f', {}).items():
                    into = lines.setdefault(path, {})
                    for line in found:
                        seen = into.get(line)
                        if seen is None:
                            seen = into[line] = array('I')
                        if not seen or seen[-1] != number:
                            seen.append(number)
    return _build(tests, lines, root, files, planned)


def _build(tests, lines, root, files, planned):
    sets, numbered, out = [], {}, {}
    for path in sorted(set(files) | set(lines)):
        try:
            text = (Path(root)/path).read_text()
        except (OSError, UnicodeDecodeError):
            text = ''
        entry = dict(sha=_sha(text), lines={})
        for line, seen in sorted((lines.get(path) or {}).items()):
            members = tuple(sorted(set(seen)))
            number = numbered.get(members)
            if number is None:
                number = numbered[members] = len(sets)
                sets.append(_encode(members))
            entry['lines'][str(line)] = number
        out[path] = entry
    return dict(format=FORMAT, tests=tests, planned=planned, files=out, sets=sets)


def merge(parts):
    """One recording from the recordings of several shards of the same commit."""
    tests, index, lines, files, planned, seconds, ran = [], {}, {}, {}, 0, 0.0, 0
    for data in parts:
        remap = []
        for test in data['tests']:
            if test not in index:
                index[test] = len(tests)
                tests.append(test)
            remap.append(index[test])
        decoded = {}
        for path, entry in data['files'].items():
            files.setdefault(path, entry['sha'])
            into = lines.setdefault(path, {})
            for line, number in entry['lines'].items():
                if number not in decoded:
                    decoded[number] = [remap[n] for n in _decode(data['sets'][number])]
                into.setdefault(int(line), []).extend(decoded[number])
        planned += data.get('planned') or 0
        seconds += data.get('seconds') or 0.0  # one job running the whole suite takes about the shards' total
        ran += data.get('ran') or 0
    sets, numbered, out = [], {}, {}
    for path, sha in sorted(files.items()):
        entry = dict(sha=sha, lines={})
        for line, seen in sorted(lines.get(path, {}).items()):
            members = tuple(sorted(set(seen)))
            number = numbered.get(members)
            if number is None:
                number = numbered[members] = len(sets)
                sets.append(_encode(members))
            entry['lines'][str(line)] = number
        out[path] = entry
    first = parts[0] if parts else {}
    return dict(format=FORMAT, commit=first.get('commit'), tests=tests, planned=planned, ran=ran, seconds=seconds,
                shards=len(parts), complete=all(p.get('complete') for p in parts), files=out, sets=sets)


def write(data, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, 'wt') as handle:
        json.dump(data, handle, separators=(',', ':'))


def read(path):
    with gzip.open(path, 'rt') as handle:
        return json.load(handle)


def folder(root):
    """Where a job keeps the recordings it has: ``AMU_SUITE_DIR``, else ``.amu/suite`` in the tree."""
    return Path(os.environ.get('AMU_SUITE_DIR') or Path(root)/'.amu'/'suite')


def nearest(root, where, commit):
    """(commit, path) of the recording in ``where`` whose commit is the nearest ancestor of ``commit`` (itself
    included), or None."""
    best = None
    for path in sorted(Path(where).glob('*.json.gz')) if Path(where).is_dir() else []:
        sha = path.name[:-len('.json.gz')]
        if _git(root, 'merge-base', '--is-ancestor', sha, commit) is None:
            continue
        distance = _git(root, 'rev-list', '--count', f'{sha}..{commit}')
        if distance is not None and (best is None or int(distance) < best[0]):
            best = (int(distance), sha, path)
    return (best[1], best[2]) if best else None


class Recording:
    """A whole-suite recording read for one commit (``base``), through the diff between the recording's commit and it."""

    def __init__(self, root, sha, data, base):
        self.root, self.sha, self.data, self.base = Path(root), sha, data, base
        self.seconds = data.get('seconds')
        self.complete = bool(data.get('complete'))
        self._sets = {}
        listed = _git(root, 'diff', '--name-only', sha, base) if sha != base else ''
        self.changed = set((listed or '').split()) if listed is not None else None

    def _members(self, number):
        if number not in self._sets:
            self._sets[number] = [self.data['tests'][n] for n in _decode(self.data['sets'][number])]
        return self._sets[number]

    def tests(self, path, text, lines):
        """Tests that ran any of ``lines`` of ``path`` as the base commit holds it (``text``), or None when the recording
        has nothing for that file."""
        entry = (self.data.get('files') or {}).get(path)
        if entry is None or self.changed is None:
            return None
        recorded = entry['lines']
        found = set()
        if entry['sha'] == _sha(text):
            for line in lines:
                if str(line) in recorded:
                    found.update(self._members(recorded[str(line)]))
            return found
        # The file changed since the recording: a line unchanged since then has its recorded tests; a line that changed
        # has every test that ran the file.
        old = _git(self.root, 'show', f'{self.sha}:{path}')
        mapping = {}
        if old is not None:
            matcher = difflib.SequenceMatcher(None, old.splitlines(), (text or '').splitlines(), autojunk=False)
            for block in matcher.get_matching_blocks():
                for k in range(block.size):
                    mapping[block.b + k + 1] = block.a + k + 1
        whole = None
        for line in lines:
            if line in mapping:
                number = recorded.get(str(mapping[line]))
                if number is not None:
                    found.update(self._members(number))
            else:
                if whole is None:
                    whole = {t for number in set(recorded.values()) for t in self._members(number)}
                found |= whole
        return found

    def changed_tests(self, everything):
        """Tests in test files that changed since the recording (the recording does not know what they run now)."""
        from .replay import is_test_file
        files = {p for p in (self.changed or ()) if is_test_file(p)}
        return [i for i in everything if i.split('::')[0] in files]


def load(root, base):
    """The Recording of the nearest ancestor of ``base`` this job has, or None."""
    found = nearest(root, folder(root), base) if base else None
    if found is None:
        return None
    try:
        return Recording(root, found[0], read(found[1]), base)
    except (OSError, ValueError, KeyError, EOFError):
        return None


def record(root, out, python=None, runner=None, shard=None, budget=None, index_dir=None, ids=None):
    """Run the suite (or one shard ``(i, n)`` of it) at the tree's commit with every production line watched, and write
    the recording to ``out``. Returns it."""
    import tempfile

    from . import evidence, record as hook, runners, shadow, suite_ids
    root = Path(root).resolve()
    python = python or runners.interpreter(root)
    chosen = runners.get(root, runner)
    ids = list(ids) if ids is not None else suite_ids(root, runner=runner, index_dir=index_dir, python=python)
    if shard:
        number, count = shard
        ids = ids[number::count]
    files = production_files(root)
    parts = shadow.PARTS if chosen.parallel else 1
    seeded, _ = shadow.trust.ordering(chosen, python)
    chosen.extra = (*runners.plugin_args(chosen, python), *seeded)
    with tempfile.TemporaryDirectory(prefix='amu-suite-') as scratch:
        env = hook.prepare(Path(scratch)/'record', root, {}, [], all_files=files)
        began = time.perf_counter()
        found, problems, seconds, tried = evidence._chunks(shadow.execute, chosen, python, root, ids, scratch, 'suite',
                                                       budget or 7 * 24 * 3600.0, None, parts, env, evidence.WHOLE)
        data = compact(Path(scratch)/'record'/'out', root, files, len(ids), chosen.cwd(root))
    data.update(commit=_git(root, 'rev-parse', 'HEAD'), seconds=round(time.perf_counter() - began, 1),
                ran=sum(state in ('passed', 'failed', 'skipped') for state in found.values()),
                complete=all(i in tried for i in ids) and not {'timed out', 'wall cap'} & set(problems), problems=problems, shard=list(shard) if shard else None,
                runner=chosen.name)
    write(data, out)
    return data
