"""Prepare the 32 workspaces for desktop-app sessions (no CLI sign-in was available for headless runs).

Writes ws/<id>/.claude/settings.json (the AMU arm's PostToolUse hook, or an empty hooks block, plus the same
permission rules in both arms so that ./rt and read-only shell commands run without a prompt) and
ws/<id>/PROMPT.md (the prompt from TRIAL-RECORD.md). Both are excluded from git so `git status` stays clean.
Usage: desktop_prepare.py ROOT
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from run_trial import PROMPT, RULES  # noqa: E402

ALLOW = ['Bash(./rt:*)', 'Bash(./rt)', 'Bash(git diff:*)', 'Bash(git status:*)', 'Bash(git log:*)', 'Bash(git show:*)', 'Bash(ls:*)',
         'Bash(grep:*)', 'Bash(rg:*)', 'Bash(cat:*)', 'Bash(head:*)', 'Bash(tail:*)', 'Bash(wc:*)', 'Bash(find:*)', 'Bash(sed -n:*)',
         'Read', 'Grep', 'Glob', 'Edit', 'Write']
DENY = ['WebFetch', 'WebSearch', 'Bash(curl:*)', 'Bash(wget:*)', 'Bash(git fetch:*)', 'Bash(git pull:*)', 'Bash(git clone:*)',
        'Bash(git commit:*)', 'Bash(pip:*)', 'Bash(pip3:*)']

if __name__ == '__main__':
    root = Path(sys.argv[1])
    for ident, slot in json.loads((root/'runs'/'map.json').read_text()).items():
        work, run = root/'ws'/ident, root/'runs'/ident
        hooks = json.loads((run/'settings.json').read_text()).get('hooks', {}) if slot['arm'] == 'amu' else {}
        (work/'.claude').mkdir(exist_ok=True)
        (work/'.claude'/'settings.json').write_text(json.dumps(dict(hooks=hooks, permissions=dict(allow=ALLOW, deny=DENY)), indent=1))
        prompt = PROMPT.format(workspace=work, suite=RULES['suite_minutes_told'], budget=RULES['budget_seconds'])
        (work/'PROMPT.md').write_text(prompt + '\n')
        (run/'prompt.txt').write_text(prompt)
        exclude = work/'.git/info/exclude'
        if '.claude/' not in exclude.read_text():
            exclude.write_text(exclude.read_text() + '.claude/\nPROMPT.md\n')
    print('prepared', len(list((root/'ws').iterdir())))
