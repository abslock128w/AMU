"""Content-addressed cache of passing outcomes on the code before the change.

A test file's entry is keyed on the bytes of that file, of every first-party file it imports directly or through
others, of the conftest.py files above it, and on the interpreter, the project's lock and configuration files, and the
runner's arguments. Only passes are kept: a failure is always run again, as Bazel does with cached test results.
The granularity is the file: an entry lists which of the file's tests passed under that key.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path, PurePosixPath

LOCKS = ('poetry.lock', 'uv.lock', 'pdm.lock', 'Pipfile.lock', 'constraints.txt', 'requirements.txt', 'requirements-dev.txt',
         'requirements-test.txt', 'pyproject.toml', 'setup.cfg', 'setup.py', 'tox.ini', 'pytest.ini')


def interpreter(python):
    try:
        out = subprocess.run([python, '-c', 'import sys; print(sys.version); print(sys.prefix)'], capture_output=True, text=True,
                             timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        out = str(python)
    return hashlib.sha256(out.encode()).hexdigest()


def locks(root):
    digest = hashlib.sha256()
    for name in LOCKS:
        try:
            digest.update(name.encode() + b'\0' + (Path(root)/name).read_bytes() + b'\0')
        except OSError:
            pass
    return digest.hexdigest()


class BaseCache:
    def __init__(self, folder, imports, root, python, args):
        """``imports``: {file: [first-party files it imports]}. ``args``: the runner's name and arguments."""
        self.folder = Path(folder)
        self.imports = imports
        self.context = hashlib.sha256(json.dumps([interpreter(python), locks(root), list(args), os.environ.get('PYTHONHASHSEED', '0')]).encode()).hexdigest()
        self.hits = self.misses = self.stored = 0

    def closure(self, path):
        seen, pending = set(), [path]
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            pending.extend(self.imports.get(current, ()))
        parts = PurePosixPath(path).parts
        seen.update('/'.join([*parts[:n], 'conftest.py']) for n in range(len(parts)))
        return sorted(seen)

    def key(self, tree, path):
        digest = hashlib.sha256(self.context.encode())
        for name in self.closure(path):
            try:
                content = (Path(tree)/name).read_bytes()
            except OSError:
                continue
            digest.update(name.encode() + b'\0' + hashlib.sha256(content).digest())
        return digest.hexdigest()

    def _entry(self, key):
        try:
            return set(json.loads((self.folder/f'{key}.json').read_text()))
        except (OSError, ValueError, TypeError):
            return set()

    def lookup(self, tree, ids):
        """{id: 'passed'} for the ids whose file's entry under the current key holds them."""
        found, keys = {}, {}
        for ident in ids:
            path = ident.split('::')[0]
            if path not in keys:
                keys[path] = self._entry(self.key(tree, path))
            if ident in keys[path]:
                found[ident] = 'passed'
        self.hits += len(found)
        self.misses += len(ids) - len(found)
        return found

    def store(self, tree, outcomes):
        by_file = {}
        for ident, state in outcomes.items():
            if state == 'passed':
                by_file.setdefault(ident.split('::')[0], set()).add(ident)
        for path, passed in by_file.items():
            key = self.key(tree, path)
            entry = self._entry(key) | passed
            try:
                self.folder.mkdir(parents=True, exist_ok=True)
                temporary = self.folder/f'{key}.{os.getpid()}.tmp'
                temporary.write_text(json.dumps(sorted(entry)))
                temporary.replace(self.folder/f'{key}.json')
                self.stored += len(passed)
            except OSError:
                return


def execute(cache, runner_execute, tree, ids, *args, **kwargs):
    """``runner_execute(..., tree, ids, ...)`` for the ids the cache does not answer. Returns ({id: state}, problem)."""
    if cache is None:
        return runner_execute(tree, ids, *args, **kwargs)
    known = cache.lookup(tree, ids)
    rest = [i for i in ids if i not in known]
    found, problem = runner_execute(tree, rest, *args, **kwargs) if rest else ({}, None)
    cache.store(tree, found)
    return {**known, **found}, problem
