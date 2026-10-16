import json, sys, time
from pathlib import Path
sys.path.insert(0,'.')
from amu.core import analyze
if __name__ == '__main__':
    truth=json.load(open('/private/tmp/claude-502/-Users-Abslock128-Documents-ChatGPT-AMU/f7b55dfe-b587-4e07-bace-49a9194bc191/scratchpad/p0-truth.json')); rows={r['case_id']:r for r in json.load(open('evaluation/before-tests/phase0/django.json'))}
    for k,v in truth.items():
        r=rows[v['case']]; t=time.perf_counter()
        out=analyze(r['tree'],Path(r['patch']).read_text(),full_evidence=True,index_dir='/Users/Abslock128/Documents/ChatGPT/AMU/phase0-django-2026-10-13/record-index',run=True,python='/Users/Abslock128/Documents/ChatGPT/AMU/phase0-django-2026-10-13/env/bin/python',budget=60)
        pf={x['id'] for x in out['predicted_failures']}
        obs=sum(t_['confidence']=='observed' for t_ in out['tests'])
        ids=[t_['id'] for t_ in out['tests']]
        print(k,f'{time.perf_counter()-t:.1f}s |',out['summary'][:74],'| cov',out.get('coverage'),'| predicted',len(pf),'| frozen breaking found',len(pf&set(v['breaking'])),'of',len(v['breaking']),'| indirect found',bool(pf&set(v['indirect'])),'|',[g[:90] for g in out['gaps'] if 'shadow' in g], flush=True)
