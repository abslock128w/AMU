"""Record which test executes which line, once per repository state.

The suite runs once under Coverage.py with a context per test. Code a test runs in a child
process is attributed to that test as well. The result is stored per source file beside the
index, keyed by the file's content hash, so an edit to one file leaves the rest exact.

This executes the repository's whole test suite. It runs only when asked for.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

from . import runners
from .index import build

FORMAT = 'amu-observed-2'

HOOK = r'''
import os
_rc = os.environ.get("AMU_OBSERVE_RC")
if _rc:
    try:
        import coverage
        from coverage.control import process_startup
        _other = coverage.Coverage.current()
        if _other is not None:
            # The project's own subprocess configuration got there first; one tracer, ours, for this run.
            _other._auto_save = False
            _other.stop()
        # A child started by a test carries that test's name; everything it runs belongs to that test.
        _ctx = os.environ.get("AMU_TEST_CONTEXT")
        _cov = coverage.Coverage(config_file=_rc, context=("child:" + _ctx) if _ctx else None)
        process_startup.coverage = _cov
        _cov._warn_no_data = _cov._warn_unimported_source = _cov._warn_preimported_source = False
        _cov._auto_save = True
        _cov.start()
    except Exception:
        pass
    try:
        import unittest
        _run = unittest.TestCase.run
        def run(self, result=None):
            os.environ["AMU_TEST_CONTEXT"] = self.id()
            try:
                return _run(self, result)
            finally:
                os.environ.pop("AMU_TEST_CONTEXT", None)
        unittest.TestCase.run = run
    except Exception:
        pass


def pytest_runtest_setup(item):
    path, _, rest = item.nodeid.partition("::")
    os.environ["AMU_TEST_CONTEXT"] = ".".join([path[:-3].replace("/", "."), *rest.split("[")[0].split("::")])


def pytest_runtest_teardown(item):
    os.environ.pop("AMU_TEST_CONTEXT", None)
'''

EXPORT = r'''
import json, sys, coverage
rc, out = sys.argv[1], sys.argv[2]
cov = coverage.Coverage(config_file=rc)
cov.combine()
cov.save()
data = cov.get_data()
table, files = {}, 0
for name in sorted(data.measured_files()):
    lines = {}
    for line, contexts in data.contexts_by_lineno(name).items():
        found = sorted({table.setdefault(c, len(table)) for c in contexts if c})
        if found:
            lines[line] = found
    if lines:
        with open(f"{out}/{files}.json", "w") as handle:
            json.dump({"file": name, "lines": lines}, handle)
        files += 1
with open(f"{out}/contexts.json", "w") as handle:
    json.dump(sorted(table, key=table.get), handle)
print(files)
'''

SUITE = {'pytest': lambda py: [py, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-p', 'amu_observe_hook'],
         'unittest': lambda py: [py, '-m', 'unittest', 'discover'],
         'django-runtests': lambda py: [py, 'runtests.py', '--parallel', '1', '--noinput'],
         'django-manage': lambda py: [py, 'manage.py', 'test', '--noinput', '--parallel', '1']}


def normalize(context):
    """The test a context names. A child process records ``child:<test>`` or ``child:<test>|<function>``."""
    context = context.split('|', 1)[0]
    return context[len('child:'):] if context.startswith('child:') else context


def directory(cache):
    return Path(cache)/'observed'


def record(root, python=None, runner=None, index_dir=None, timeout=7200, labels=()):
    """Run the suite under coverage and store the recording. Returns an info dict; problems are in ``gaps``."""
    root = Path(root).resolve()
    python = python or runners.interpreter(root)
    chosen = runners.get(root, runner)
    info = dict(runner=chosen.name, python=python, gaps=[])
    try:
        purelib = Path(subprocess.check_output([python, '-c', 'import coverage, sysconfig; print(sysconfig.get_paths()["purelib"])'],
                                               stderr=subprocess.DEVNULL, timeout=60).decode().strip())
    except (OSError, subprocess.SubprocessError):
        info['gaps'].append(f'{python} cannot import coverage; install it there to record')
        return info
    with tempfile.TemporaryDirectory(prefix='amu-observe-') as scratch:
        scratch = Path(scratch)
        (scratch/'data').mkdir()
        (scratch/'export').mkdir()
        rc = scratch/'coveragerc'
        rc.write_text(f'[run]\nparallel = True\ndynamic_context = test_function\nsource = {root}\n'
                      f'data_file = {scratch}/data/.coverage\n')
        installed = []
        try:
            # The hook must load in every interpreter the tests start, whatever they do to PYTHONPATH.
            (purelib/'amu_observe_hook.py').write_text(HOOK)
            installed.append(purelib/'amu_observe_hook.py')
            (purelib/'zzz_amu_observe.pth').write_text('import amu_observe_hook\n')
            installed.append(purelib/'zzz_amu_observe.pth')
        except OSError:
            info['gaps'].append('could not install the recording hook in the interpreter; nothing recorded')
            for path in installed:
                path.unlink(missing_ok=True)
            return info
        env = dict(os.environ, AMU_OBSERVE_RC=str(rc), PYTHONDONTWRITEBYTECODE='1',
                   PYTHONPATH=os.pathsep.join(filter(None, [str(root/'src'), str(root), os.environ.get('PYTHONPATH')])))
        command = [*SUITE[chosen.name](python), *labels]
        started = time.perf_counter()
        try:
            done = subprocess.run(command, cwd=chosen.cwd(root), env=env, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
            tail = (done.stderr + done.stdout).decode(errors='replace').strip().splitlines()[-1:] or ['']
            info.update(exit=done.returncode, suite=tail[0][:160])
        except subprocess.TimeoutExpired:
            info['gaps'].append(f'the suite did not finish in {timeout} s; what ran is recorded')
        finally:
            for path in installed:
                path.unlink(missing_ok=True)
        info.update(command=' '.join(command), seconds=round(time.perf_counter() - started, 1))
        children = len(list((scratch/'data').glob('.coverage.*'))) - 1
        exported = subprocess.run([python, '-c', EXPORT, str(rc), str(scratch/'export')], capture_output=True, timeout=3600)
        if exported.returncode:
            info['gaps'].append('recording could not be read back: ' + exported.stderr.decode(errors='replace').strip()[-160:])
            return info
        info.update(store(root, scratch/'export', index_dir, info), child_processes=max(children, 0))
    return info


def store(root, exported, index_dir, info):
    """Turn the raw export into per-file shards with line-level and function-level attribution."""
    facts, hashes, _, _, cache = build(root, index_dir)
    out = directory(cache)
    shutil.rmtree(out, ignore_errors=True)
    (out/'files').mkdir(parents=True)
    contexts = json.loads((exported/'contexts.json').read_text())
    labels, index = [], {}
    remap = [index.setdefault(normalize(c), len(index)) for c in contexts]
    labels = sorted(index, key=index.get)
    recorded, added, via_child = {}, 0, set()
    child = {i for i, c in enumerate(contexts) if c.startswith('child:')}
    for raw in sorted(exported.glob('[0-9]*.json')):
        entry = json.loads(raw.read_text())
        try:
            path = Path(entry['file']).resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        if path not in hashes:
            continue
        lines = {int(line): sorted({remap[i] for i in found}) for line, found in entry['lines'].items()}
        direct = {int(line): {remap[i] for i in found if i not in child} for line, found in entry['lines'].items()}
        functions = {}
        for record in facts.get(path, {}).get('records', {}).values():
            if record['kind'] == 'function':
                ran, in_process = set(), set()
                for start, end in record['spans']:
                    # The def line runs at import; only the body says a test executed the function.
                    for line in range(max(start, record.get('body', start)), end + 1):
                        ran.update(lines.get(line, ()))
                        in_process.update(direct.get(line, ()))
                functions[record['qual']] = sorted(ran)
                # What recording child processes adds: test-to-function links seen nowhere in the parent process.
                added += len(ran - in_process)
                via_child.update(ran - in_process)
        shard = out/'files'/(hashlib.sha256((path + '\0' + hashes[path]).encode()).hexdigest() + '.json')
        try:
            # The recorded text lets a later run map these line numbers through whatever changed since.
            text = base64.b64encode(zlib.compress((root/path).read_bytes(), 6)).decode()
        except OSError:
            text = None
        shard.write_text(json.dumps(dict(path=path, sha=hashes[path], lines=lines, functions=functions, source=text), separators=(',', ':')))
        recorded[path] = hashes[path]
    (out/'meta.json').write_text(json.dumps(dict(
        format=FORMAT, recorded_at=time.time(), runner=info.get('runner'), command=info.get('command'),
        seconds=info.get('seconds'), suite=info.get('suite'), labels=labels, files=recorded,
        links_only_through_child_processes=added, tests_with_such_links=len(via_child)), separators=(',', ':')))
    return dict(tests=len(labels), files=len(recorded), stored=str(out),
                links_only_through_child_processes=added, tests_with_such_links=len(via_child))


if __name__ == '__main__':
    print(json.dumps(record(sys.argv[1], *sys.argv[2:4])))
