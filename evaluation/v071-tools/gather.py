import json,sys
from pathlib import Path
repo=sys.argv[1]; base=Path(sys.argv[2])
rows=[]
for d in sorted(base.glob('*')):
    if not (d/'report.json').exists(): print('MISSING',d.name); continue
    r=json.loads((d/'report.json').read_text()); m=json.loads((d/'meta.json').read_text())
    rz=json.loads((d/'raise.json').read_text()) if (d/'raise.json').exists() else {}
    t=json.loads((d/'timing.json').read_text()) if (d/'timing.json').exists() else {}
    se=r.get('selection_evidence') or {}; sb=r.get('side_by_side') or {}
    bd=r.get('behaviour_differences') or []
    first=(d/'comment.md').read_text().splitlines()[0]
    rows.append(dict(repo=repo,number=int(d.name),mode=se.get('mode'),base_complete=se.get('base_complete'),
      static_fallback=se.get('static_fallback'),suite=se.get('suite'),sources=se.get('sources'),
      flips=len(r.get('flips') or []),rows=len(bd),
      excluded_fields=sb.get('excluded_fields'),paired={k:sb.get(k) for k in ('excluded','not_rerun','rerun_tests','unstable','recovered')},
      raise_catchers=rz.get('catchers'),raise_missed=rz.get('missed'),raise_injected=rz.get('injected'),
      unrunnable=(r.get('executed') or {}).get('unrunnable'),first=first,seconds=t.get('seconds') or m.get('seconds')))
json.dump(rows,sys.stdout,indent=1)
