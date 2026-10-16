"""Write the text each slow-suite agent receives: the upstream commit message, never the diff.

sympy's commits carry no ticket text of their own (issues and pull requests live on GitHub, which is not fetched), so
the task is the commit message as committed. Reads sympy.json (the frozen manifest), writes tasks.json, and pins both
and the screening protocol by sha256 in freeze.json.
"""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE/'sympy.json'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


if __name__ == '__main__':
    if (HERE/'freeze.json').exists():
        raise SystemExit('Already frozen; do not overwrite')
    tasks = {}
    for number, row in enumerate(json.loads(MANIFEST.read_text()), 1):
        text = ("# A change to make in sympy\n\n"
                "The maintainers' commit message for this change follows. The code change is yours to write.\n\n"
                f"{row['message'].strip()}\n")
        tasks[f'task{number}'] = dict(case=row['case_id'], commit=row['commit'], parent=row['parent'], text=text)
    (HERE/'tasks.json').write_text(json.dumps(tasks, indent=1) + '\n')
    pins = {name: sha(HERE/name) for name in ('sympy.json', 'sympy-protocol.json', 'tasks.json')}
    (HERE/'freeze.json').write_text(json.dumps(dict(frozen='2026-10-15', sha256=pins), indent=1) + '\n')
    print(json.dumps({k: (v['case'], v['commit'][:10], len(v['text'])) for k, v in tasks.items()}))
