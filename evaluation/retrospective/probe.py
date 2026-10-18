"""The generated-test tier, with this session as the model (brief 5, piece 2; PROTOCOL.md).

Usage:
  python -P evaluation/retrospective/probe.py targets --root ROOT --repo NAME --pr N
      prints the targets the automated tier would get: comparable changed functions side-by-side saw no call of, each
      with its source before and after the change, and writes them to ROOT/out/NAME/N/probe-targets.json
  python -P evaluation/retrospective/probe.py run --root ROOT --repo NAME --pr N --probes PROBES.json [--minutes M]
      runs the probes through AMU's own filter chain (amu.core.generate.run of the frozen engine), with a client that
      returns PROBES.json in place of a model reply, and appends the outcome to the pull request's comment

Run it with the frozen engine's interpreter (ROOT/amu-venv/bin/python -P), so ``amu`` is the v0.2.6 install.
PROBES.json: {"probes": [{"function": "<target id>", "setup": "...", "call": "...", "why": "..."}], "attempt": 1}.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from run import environment, render, suite_lock  # noqa: E402

from amu.core import generate, runners, sidebyside, trust  # noqa: E402
from amu.core.patch import changes  # noqa: E402
from amu.core.shadow import _after_lines  # noqa: E402
from amu.core.replay import is_test_file  # noqa: E402


def paths(root, name, number):
    root = Path(root)
    work = root/'work'/name/str(number)
    return root, work/'head', work/'base', root/'out'/name/str(number)


def load(out):
    return json.loads((out/'report.json').read_text()), json.loads((out/'meta.json').read_text())


def targets(root, name, number):
    root, head, base, out = paths(root, name, number)
    report, _ = load(out)
    functions = report.get('changed_functions') or []
    files = {row['file'] for row in functions}

    def read(tree, path):
        try:
            return (tree/path).read_text()
        except OSError:
            return None
    before = {p: read(base, p) for p in files}
    after = {p: read(head, p) for p in files}
    found, _ = sidebyside.targets(functions, before, after)
    side = report.get('side_by_side') or {}
    called = set(side.get('called') or ())
    uncalled = [t for t in found if t['id'] not in called]
    (out/'probe-targets.json').write_text(json.dumps(dict(all=len(found), called=len(called & {t['id'] for t in found}),
                                                          targets=uncalled), indent=1))
    return uncalled, found


class Reply:
    """What generate.ask reads from a model response, holding probes written by this session."""

    def __init__(self, probes):
        self.probes = probes
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))
        self.messages = self.beta.messages

    def create(self, **params):
        text = params['messages'][0]['content']
        wanted = [p for p in self.probes if f"## {p['function']}\n" in text]
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=0, output_tokens=0, cache_creation_input_tokens=0),
                               stop_reason='end_turn', content=[SimpleNamespace(type='text', text=json.dumps(dict(probes=wanted)))])


def run(root, name, number, probes_file, minutes, dollars=0.10):
    root, head, base, out = paths(root, name, number)
    report, meta = load(out)
    uncalled, _ = targets(root, name, number)
    wanted = json.loads(Path(probes_file).read_text())
    probes = wanted['probes']
    patch = (out/'patch.diff').read_text(errors='replace')
    edits, _ = changes(head, patch)
    changed = {path: _after_lines(path, edit) for path, edit in edits.items() if path.endswith('.py') and not is_test_file(path)}
    runner = runners.get(head, (report.get('executed') or {}).get('runner'))
    python = str(root/'env'/name/'bin'/'python')
    example = (head/wanted['example']).read_text() if wanted.get('example') else ''
    began = time.monotonic()
    with suite_lock(root, f'probe {name} {number}'):
        waited = time.monotonic() - began
        with tempfile.TemporaryDirectory(prefix='amu-probe-', dir=environment(root)['TMPDIR']) as scratch:
            info, differences, gaps = generate.run(runner, python, head, base, uncalled, changed, example, scratch, dollars, minutes,
                                                   generate.MODEL, api=Reply(probes), copy_before=True)
    seconds = round(time.monotonic() - began, 1)
    record = dict(attempt=wanted.get('attempt', 1), dollars_cap=dollars, probes=probes, info=info, differences=differences, gaps=gaps,
                  filter_seconds=round(seconds - waited, 1), waited_seconds=round(waited, 1), targets=[t['id'] for t in uncalled])
    log = out/'probes.json'
    history = json.loads(log.read_text()) if log.exists() else []
    history.append(record)
    log.write_text(json.dumps(history, indent=1))
    clock = out/'probe-minutes.json'
    stamps = json.loads(clock.read_text()) if clock.exists() else dict(start=time.time() - seconds)
    stamps.update(end=time.time(), waited=stamps.get('waited', 0) + waited)
    # Time queued behind a pull request's job is not the probe writer's time.
    stamps['minutes'] = round((stamps['end'] - stamps['start'] - stamps['waited']) / 60, 1)
    clock.write_text(json.dumps(stamps))
    print(json.dumps(dict(info=info, gaps=gaps, kept=[d['target'] for d in differences]), indent=1))
    return record


def append_to_comment(root, name, number):
    """The comment as the automated tier would have left it: every attempt's probes and kept differences merged into
    the report's ``generated`` fields, rendered again."""
    root, head, base, out = paths(root, name, number)
    history = json.loads((out/'probes.json').read_text())
    report, meta = load(out)
    keys = ('functions', 'requests', 'proposed', 'compiled', 'ran', 'stable', 'reached', 'differed', 'accepted')
    made = dict(model=generate.MODEL, dollars_cap=0.10, minutes_cap=20, cost=0.0, **{k: 0 for k in keys})
    produced = []
    for record in history:
        for k in keys:
            if k != 'functions':
                made[k] += record['info'].get(k, 0)
        produced += record['differences']
    made['functions'] = len(history[0]['targets'])
    report['generated'], report['generated_differences'] = made, produced
    staged = out/'with-probes'
    staged.mkdir(exist_ok=True)
    (staged/'report.json').write_text(json.dumps(report, indent=1))
    (staged/'meta.json').write_text(json.dumps(meta, indent=1))
    if (out/'verify').is_dir():
        (staged/'verify').mkdir(exist_ok=True)
        (staged/'verify'/'verify.json').write_text((out/'verify'/'verify.json').read_text())
    render(root, staged)
    return (staged/'comment.md').read_text()


def brief(root, name, number):
    """What the probe writer sees: the pull request's title and description, its test files, the targets."""
    import subprocess
    from freeze import REPOS, check
    root, head, base, out = paths(root, name, number)
    pr = next(r for r in check()['prs'][name] if r['number'] == number)
    cached = out/'pr-body.txt'
    if not cached.exists():
        body = subprocess.run(['gh', 'pr', 'view', str(number), '--repo', REPOS[name]['slug'], '--json', 'body', '--jq', '.body'],
                              capture_output=True, text=True).stdout
        cached.write_text(body)
    uncalled, found = targets(root, name, number)
    clock = out/'probe-minutes.json'
    if not clock.exists():
        clock.write_text(json.dumps(dict(start=time.time())))
    lines = [f"# {name} #{number}: {pr['title']}", '', cached.read_text().strip()[:3000], '',
             f"Production files: {', '.join(pr['production_files'])}", f"Test files: {', '.join(pr['test_files']) or 'none'}",
             f'{len(uncalled)} of {len(found)} comparable changed functions had no captured call']
    for t in uncalled:
        lines += ['', f"## {t['id']}", '### before', t['before'], '### after', t['after']]
    return '\n'.join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('action', choices=['targets', 'brief', 'run', 'comment'])
    p.add_argument('--root', required=True)
    p.add_argument('--repo', required=True)
    p.add_argument('--pr', type=int, required=True)
    p.add_argument('--probes')
    p.add_argument('--minutes', type=float, default=20)
    # The Action's default cap is 0.10; a larger one only when that cap blocked the request before any probe ran.
    p.add_argument('--dollars', type=float, default=0.10)
    a = p.parse_args(argv)
    if a.action == 'targets':
        uncalled, found = targets(a.root, a.repo, a.pr)
        print(f'{len(uncalled)} of {len(found)} comparable changed functions had no captured call')
        for t in uncalled:
            print(f"\n## {t['id']}\n### before\n{t['before']}\n### after\n{t['after']}")
    elif a.action == 'brief':
        print(brief(a.root, a.repo, a.pr))
    elif a.action == 'run':
        run(a.root, a.repo, a.pr, a.probes, a.minutes, a.dollars)
    else:
        print(append_to_comment(a.root, a.repo, a.pr))


if __name__ == '__main__':
    main()
