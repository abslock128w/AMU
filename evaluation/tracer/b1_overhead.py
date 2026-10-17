"""B1 check: the edge tracer's overhead on Django's suite, beside no hook and beside coverage contexts.

Usage: b1_overhead.py --tree DIR --python PY --scratch DIR --out OUT.json [--modes plain edges coverage] [--labels ...]

Each mode runs Django's whole suite once (``runtests.py --parallel 1``) on its own copy of the same tree, one after
another on an otherwise idle machine:
- plain: no hook;
- edges: the sys.monitoring edge tracer (amu/core/edges.py), stored as AMU stores it;
- coverage: coverage contexts per test with the child-process hook, as ``amu before-tests --observe`` records.
Suite seconds are the runner's wall time only; the store step's time is reported apart.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from amu.core import edges, observe  # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--tree', required=True)
    p.add_argument('--python', required=True)
    p.add_argument('--scratch', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--modes', nargs='+', default=['plain', 'edges', 'coverage'])
    p.add_argument('--labels', nargs='*', default=[])
    a = p.parse_args(argv)
    out = Path(a.out)
    results = json.loads(out.read_text()) if out.exists() else []
    for mode in a.modes:
        work = Path(a.scratch)/mode
        shutil.rmtree(work, ignore_errors=True)
        shutil.copytree(a.tree, work/'tree', symlinks=True, ignore=shutil.ignore_patterns('.amu', '__pycache__', '.git'))
        began = time.perf_counter()
        if mode == 'coverage':
            info = observe.record(work/'tree', a.python, 'django-runtests', index_dir=work/'index', labels=a.labels)
        else:
            info = edges.record(work/'tree', a.python, 'django-runtests', index_dir=work/'index', labels=a.labels, plain=mode == 'plain')
        info['total_seconds'] = round(time.perf_counter() - began, 1)
        index = work/'index'
        info['stored_bytes'] = sum(f.stat().st_size for f in index.rglob('*') if f.is_file()
                                   and {'edges', 'observed'} & set(f.relative_to(index).parts)) if index.exists() else 0
        if mode == 'edges' and (work/'index').exists():
            for stored in (work/'index').rglob('edges.json.gz'):
                shutil.copy(stored, Path(a.scratch)/'edges-recording.json.gz')
        results.append(dict(info, mode=mode, labels=a.labels))
        out.write_text(json.dumps(results, indent=1) + '\n')
        print(json.dumps({k: info.get(k) for k in ('seconds', 'total_seconds', 'suite', 'tests', 'edges', 'stored_bytes', 'gaps')}), mode, flush=True)
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
