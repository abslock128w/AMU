"""Post hoc paired re-analysis of the 32 scored sympy runs. No new runs; reads results/ and the saved transcripts.

Usage: posthoc.py ROOT            (ROOT is the trial folder holding runs/<id>/session.jsonl)
Writes results/posthoc.json and prints the per-task table.

Excluded: w26 and w30, both blocked on a permission prompt past the hard stop. Both are task 2, trial 1, so task 2 is
compared on its remaining pair (w05 against w10).

Per run:
- wall: session start to the transcript's last timestamp (result.json's wall_seconds), as scored.
- waiting: time the agent sat on a permission prompt. For every tool call, the gap from tool_use to tool_result, less
  the test and hook seconds the ledger logged inside that gap, less 5 s of slack, floored at 0. Only gaps over 20 s count.
- active: wall minus waiting. This is "wall time to declared done" with the owner's prompt-answering time taken out.
- test_seconds: test time charged (agent ./rt runs plus hook runs).
- red: tests that passed on the parent and fail in the agent's own tree as shipped.
- breaking_run: frozen breaking tests the agent ran before declaring, of all frozen breaking tests.
- clean_active: active time if the agent's own tree has no red test, else None (did not reach a clean tree).

Per task, each arm is the mean over its runs; an arm reaches a clean tree only if every one of its runs does.
AMU wins a measure on a task if its mean is strictly better (lower time, test time and red; higher share of frozen
breaking tests run).
"""
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXCLUDED = {'w26', 'w30'}
SLACK, LONG = 5.0, 20.0


def stamp(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')).timestamp()


def waiting(transcript, ledger):
    pending, total, waits = {}, 0.0, []
    for line in transcript.read_text().splitlines():
        row = json.loads(line)
        content = (row.get('message') or {}).get('content')
        if not row.get('timestamp') or not isinstance(content, list):
            continue
        at = stamp(row['timestamp'])
        for part in content:
            if part.get('type') == 'tool_use':
                pending[part['id']] = at
            elif part.get('type') == 'tool_result' and part.get('tool_use_id') in pending:
                start = pending.pop(part['tool_use_id'])
                gap = at - start
                if gap <= LONG:
                    continue
                ran = sum(r['seconds'] for r in ledger if start - 1 <= r['at'] <= at + 1)
                wait = max(0.0, gap - ran - SLACK)
                if wait:
                    waits.append(round(wait))
                    total += wait
    return total, waits


def mean(values):
    return sum(values) / len(values)


if __name__ == '__main__':
    root = Path(sys.argv[1])
    scores = json.loads((HERE/'results'/'scores.json').read_text())
    runs = []
    for s in scores:
        ledger = json.loads((HERE/'results'/'runs'/s['id']/'ledger.json').read_text())
        wait, waits = waiting(root/'runs'/s['id']/'session.jsonl', ledger)
        active = s['wall_seconds'] - wait
        runs.append(dict(id=s['id'], task=s['task'], arm=s['arm'], trial=s['trial'], excluded=s['id'] in EXCLUDED,
                         wall=s['wall_seconds'], waiting=round(wait, 1), waits=waits, active=round(active, 1),
                         test_seconds=s['test_seconds'], red=len(s['red_in_own_tree']),
                         breaking_run=len(s['breaking_run_by_agent']), breaking=len(s['breaking']),
                         clean_active=round(active, 1) if not s['red_in_own_tree'] else None,
                         regressions=len(s['regressions'])))
    tasks = sorted({r['task'] for r in runs})
    table, wins = [], {}
    for task in tasks:
        arms = {}
        for arm in ('amu', 'dumb'):
            mine = [r for r in runs if r['task'] == task and r['arm'] == arm and not r['excluded']]
            arms[arm] = dict(runs=[r['id'] for r in mine], active=mean([r['active'] for r in mine]),
                             wall=mean([r['wall'] for r in mine]), test_seconds=mean([r['test_seconds'] for r in mine]),
                             red=mean([r['red'] for r in mine]),
                             breaking_share=mean([r['breaking_run'] / r['breaking'] for r in mine]),
                             clean_active=None if any(r['clean_active'] is None for r in mine)
                             else mean([r['clean_active'] for r in mine]))
        a, d = arms['amu'], arms['dumb']
        won = dict(active=a['active'] < d['active'], wall=a['wall'] < d['wall'],
                   test_seconds=a['test_seconds'] < d['test_seconds'], red=a['red'] < d['red'],
                   breaking_share=a['breaking_share'] > d['breaking_share'],
                   clean_active=a['clean_active'] is not None and (d['clean_active'] is None or a['clean_active'] < d['clean_active']))
        tied = dict(red=a['red'] == d['red'], breaking_share=a['breaking_share'] == d['breaking_share'])
        table.append(dict(task=task, amu=a, dumb=d, amu_won=won, tied=tied))
    for measure in ('clean_active', 'active', 'wall', 'test_seconds', 'red', 'breaking_share'):
        wins[measure] = sum(row['amu_won'][measure] for row in table)
    (HERE/'results'/'posthoc.json').write_text(json.dumps(dict(runs=runs, tasks=table, amu_task_wins=wins), indent=1))
    print('run  task  arm   wall  wait  active  test  red  brk')
    for r in sorted(runs, key=lambda r: (r['task'], r['arm'], r['trial'])):
        print(f"{r['id']} {r['task']} {r['arm']:4} {r['wall']:6.0f} {r['waiting']:5.0f} {r['active']:6.0f} {r['test_seconds']:5.0f}"
              f" {r['red']:3} {r['breaking_run']}/{r['breaking']}{'  EXCLUDED' if r['excluded'] else ''} {r['waits']}")
    for row in table:
        a, d = row['amu'], row['dumb']
        fmt = lambda x: '-' if x is None else f'{x:.0f}'
        print(row['task'], 'clean', fmt(a['clean_active']), fmt(d['clean_active']), 'active', fmt(a['active']), fmt(d['active']),
              'test', fmt(a['test_seconds']), fmt(d['test_seconds']), 'red', a['red'], d['red'],
              'brk', round(a['breaking_share'], 2), round(d['breaking_share'], 2), row['amu_won'])
    print('AMU task wins of', len(table), wins)
