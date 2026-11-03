"""Release 0.7.1: bump the version and repin the Action, templates and workflows (the recording workflow too), as
release070.py did for 0.7.0. The install note's recording sizes are unchanged."""
import sys

ROOT = sys.argv[1]
OLD, NEW = '0.7.0', '0.7.1'
for path in ('.github/workflows/amu-pr-comment.yml', '.github/workflows/amu-pr.yml', 'action.yml', 'amu/__init__.py',
             'ci/amu-pr-comment.yml', 'ci/amu-pr.yml', 'ci/amu-record.yml', 'evaluation/ci/INSTALL-NOTE.md'):
    full = f'{ROOT}/{path}'
    text = open(full).read()
    changed = text.replace(f'v{OLD}', f'v{NEW}').replace(f'"{OLD}"', f'"{NEW}"')
    assert changed != text, path
    open(full, 'w').write(changed)
print('release edits applied')
