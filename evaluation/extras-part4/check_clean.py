"""Both mutation samples, checked cleanly: no bytecode written (PYTHONDONTWRITEBYTECODE=1) and none left from earlier
runs, each mutant rebuilt with the part 4 generator (mutate.mutants), baseline then mutant, file restored. A mutant is
killed when the mutant run has failing tests the baseline run does not."""
import json, re, shutil, subprocess
from pathlib import Path
from amu.core.mutate import mutants

R = Path('/Users/Shared/amu-retro-2026-10-18')
LABELS = {  # (pr, line): test labels most likely to catch it; None = reasoned, not run (#21523's middleware flags)
    ('21482', 741): ['model_formsets'],
    ('22025', 665): ['async.test_async_queryset', 'prefetch_related'],
    ('22041', 1632): ['null_queries', 'lookup', 'queries.tests'],
    ('21736', 298): ['validators'],
    ('21357', 410): ['template_tests.syntax_tests.test_now'],
    ('30601', 46): ['sympy/tensor/array/expressions/tests'],
    ('30589', 1945): ['sympy/core/tests/test_relational.py'],
    ('21805', 85): ['mail'],
    ('30563', 928): ['sympy/algebras/tests/test_quaternion.py'],
    ('30575', 980): ['sympy/core/tests/test_arit.py', 'sympy/series/tests/test_order.py', 'sympy/series/tests/test_series.py'],
    ('21886', 83): ['version'],
}
FAILED = re.compile(r'^(?:FAILED|ERROR) (\S+)|^(\S+ \([\w.]+\)).* \.\.\. (?:FAIL|ERROR)$', re.M)


def run(repo, head, labels):
    py = str(R/'env'/repo/'bin'/'python')
    for cache in head.rglob('__pycache__'):
        shutil.rmtree(cache, ignore_errors=True)
    if repo == 'django':
        cmd, cwd = [py, 'runtests.py', *labels, '--parallel', '1', '--noinput', '-v', '2'], head/'tests'
    else:
        cmd, cwd = [py, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-rfE', *labels], head
    done = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=1800,
                          env={'PATH': '/usr/bin:/bin', 'PYTHONHASHSEED': '0', 'PYTHONPATH': str(head),
                               'PYTHONDONTWRITEBYTECODE': '1'})
    out = done.stderr + done.stdout
    return done.returncode, {a or b for a, b in FAILED.findall(out)}


samples = [dict(s, sample='django-10', repo='django') for s in json.load(open(R/'q4'/'mutation-sample-django.json'))]
samples += [dict(s, sample='pooled-10') for s in json.load(open(R/'q4'/'mutation-sample-pooled.json'))]
results = []
for s in samples:
    key = (s['pr'], s['line'])
    row = dict(sample=s['sample'], repo=s['repo'], pr=s['pr'], file=s['file'], line=s['line'], mutant=s['mutant'])
    if s['pr'] == '21523':
        row.update(verdict='true', how='reasoned: the fix itself; no test pins one middleware\'s async_capable')
        results.append(row); print(json.dumps(row), flush=True); continue
    head = R/'work-q4'/s['repo']/s['pr']/'head'
    target = head/s['file']
    text = target.read_text()
    built = [t for n, what, t in mutants(text, {s['line']}) if n == s['line'] and what == s['mutant']]
    if not built:
        row.update(verdict='?', how='mutant not rebuilt'); results.append(row); print(json.dumps(row), flush=True); continue
    base_rc, base_bad = run(s['repo'], head, LABELS[key])
    target.write_text(built[0])
    try:
        mut_rc, mut_bad = run(s['repo'], head, LABELS[key])
    finally:
        target.write_text(text)
    new = sorted(mut_bad - base_bad)
    killed = bool(new) or (mut_rc != 0 and base_rc == 0)
    row.update(labels=LABELS[key], base_rc=base_rc, base_failing=len(base_bad), mutant_rc=mut_rc, newly_failing=len(new),
               example=new[:1], verdict='false' if killed else 'true',
               how='an existing test fails on the mutant' if killed else 'no test in those labels fails on the mutant')
    results.append(row); print(json.dumps(row), flush=True)
json.dump(results, open(R/'q4'/'mutation-labels.json', 'w'), indent=1)
