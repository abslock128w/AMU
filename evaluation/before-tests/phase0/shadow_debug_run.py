import json, sys, time
from pathlib import Path
sys.path.insert(0,'.')
from amu.core import analyze
truth=json.load(open('/private/tmp/claude-502/-Users-Abslock128-Documents-ChatGPT-AMU/f7b55dfe-b587-4e07-bace-49a9194bc191/scratchpad/p0-truth.json')); rows={r['case_id']:r for r in json.load(open('evaluation/before-tests/phase0/django.json'))}
for k,v in truth.items():
    if len(sys.argv)>1 and k not in sys.argv[1:]: continue
    r=rows[v['case']]; t=time.perf_counter()
    out=analyze(r['tree'],Path(r['patch']).read_text(),full_evidence=True,coverage_evidence='none',index_dir='/Users/Abslock128/Documents/ChatGPT/AMU/phase0-django-2026-10-13/index/'+r['case_id'],run=True,python='/Users/Abslock128/Documents/ChatGPT/AMU/phase0-django-2026-10-13/env/bin/python',budget=120)
    pf={x['id'] for x in out['predicted_failures']}
    print(k,f'{time.perf_counter()-t:.1f}s |',out['summary'][:88],'| predicted',len(pf),'| frozen breaking found',len(pf&set(v['breaking'])),'of',len(v['breaking']),'| indirect found',bool(pf&set(v['indirect'])),'|',[g[:110] for g in out['gaps'] if 'shadow' in g], flush=True)
