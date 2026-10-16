"""Write the text each blind-trial agent receives: the upstream ticket as reported, then the commit message.

Never the diff. Reads ../phase0/blind.json and tickets/<number>.csv (code.djangoproject.com/ticket/<number>?format=csv,
fetched once on 2026-10-14). Writes tasks.json and pins it, the manifest and every ticket file by sha256 in freeze.json.
"""
import csv
import hashlib
import io
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE.parent/'phase0/blind.json'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


if __name__ == '__main__':
    if (HERE/'freeze.json').exists():
        raise SystemExit('Already frozen; do not overwrite')
    tasks = {}
    for number, row in enumerate(json.loads(MANIFEST.read_text()), 1):
        ticket = re.match(r'Fixed #(\d+)', row['message'])[1]
        fields = next(csv.DictReader(io.StringIO((HERE/'tickets'/f'{ticket}.csv').read_text(encoding='utf-8-sig'))))
        text = (f"# Django ticket #{ticket}: {fields['summary']}\n\n{fields['description'].strip()}\n\n"
                f"## How the maintainers resolved it (commit message; the code change is yours to write)\n\n{row['message']}\n")
        tasks[f'task{number}'] = dict(case=row['case_id'], commit=row['commit'], parent=row['parent'], ticket=ticket, text=text)
    (HERE/'tasks.json').write_text(json.dumps(tasks, indent=1) + '\n')
    pins = {str(p.relative_to(HERE.parent)): sha(p) for p in [MANIFEST, HERE/'tasks.json', *sorted((HERE/'tickets').glob('*.csv'))]}
    (HERE/'freeze.json').write_text(json.dumps(dict(frozen='2026-10-14', sha256=pins), indent=1) + '\n')
    print(json.dumps({k: (v['case'], v['ticket'], len(v['text'])) for k, v in tasks.items()}))
