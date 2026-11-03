"""Release 0.7.0: repin the Action, templates and workflows (the recording workflow too), and give the install note
the measured size of a recording."""
import sys

ROOT = sys.argv[1]
OLD, NEW = '0.6.0', '0.7.0'
for path in ('.github/workflows/amu-pr-comment.yml', '.github/workflows/amu-pr.yml', 'action.yml', 'amu/__init__.py',
             'ci/amu-pr-comment.yml', 'ci/amu-pr.yml', 'ci/amu-record.yml', 'evaluation/ci/INSTALL-NOTE.md'):
    full = f'{ROOT}/{path}'
    text = open(full).read()
    changed = text.replace(f'v{OLD}', f'v{NEW}').replace(f'"{OLD}"', f'"{NEW}"')
    assert changed != text, path
    open(full, 'w').write(changed)
note = f'{ROOT}/evaluation/ci/INSTALL-NOTE.md'
text = open(note).read()
old = 'megabytes: SIZES.'
new = ('megabytes: 2.8 MB for Django (19,322 tests; 129 s on 10 local cores) and 4.9 MB for sympy (13,205 tests; 43 '
       'minutes in one process, so about 11 per shard over four shards).')
assert old in text
open(note, 'w').write(text.replace(old, new))
print('release edits applied')
