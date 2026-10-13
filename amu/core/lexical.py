"""What a careful reader would search the tests for: text and names the patch takes away.

A test that asserts on a message the patch removed, or that uses a definition the patch
deleted, is likely to fail whether or not a call path to it was resolved.
"""
from __future__ import annotations

import re

STRING = re.compile(r'''(?:[rbfuRBFU]{0,2})("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')''')
WORD = re.compile(r"[A-Za-z0-9_']+")
SHINGLE = 3
ENOUGH = 3


def phrases(text):
    """Word triples of the string literals on these lines; f-string fields split a literal."""
    found = set()
    for literal in STRING.findall(text):
        for piece in re.split(r'\{[^}]*\}', literal[1:-1]):
            words = [w.lower() for w in WORD.findall(piece)]
            for i in range(len(words) - SHINGLE + 1):
                found.add(' '.join(words[i:i + SHINGLE]))
    return found


def removed_phrases(edit):
    """Literal wording present on removed lines and absent from added ones."""
    gone = phrases('\n'.join(text for _, text in edit['removed']))
    return gone - phrases('\n'.join(text for _, text in edit['added'])) if gone else gone


def removed_names(before, after, elsewhere=()):
    """Functions and classes the patch deletes from the package, not merely moves.

    ``elsewhere`` holds the leaf names declared in files the patch does not touch.
    A file that keeps a module ``__getattr__``, or still binds or imports the name, still serves it.
    """
    kept = {r['qual'] for r in after.values()} if after else set()
    modules = {r['path']: r for r in after.values() if r['kind'] == 'module'}
    names = set()
    for record in before.values():
        # Functions, classes and module-level constants can be imported by name; class attributes are not.
        top_level = '.' not in record['qual']
        if record['qual'] in kept or not (record['kind'] in ('function', 'class') or record['kind'] == 'variable' and top_level):
            continue
        leaf = record['qual'].rsplit('.', 1)[-1]
        module = modules.get(record['path'], {})
        short = len(leaf) < 6 and not (leaf.isupper() and len(leaf) >= 4)
        if (short or leaf.startswith('__') or leaf in elsewhere
                or any(q == leaf or q.endswith('.' + leaf) for q in kept)
                or leaf in module.get('imports', {}) or leaf in module.get('bindings', {})
                or (record['path'] + '#__getattr__') in after):
            continue
        names.add(leaf)
    return names


def removed_signals(before, after):
    """{function identity: exception and warning categories it raised before and no longer does}."""
    gone = {}
    for ident, record in before.items():
        if record['kind'] != 'function':
            continue
        now = {name for name, _ in after.get(ident, {}).get('raises', [])} if ident in after else set()
        lost = {name for name, _ in record.get('raises', []) if name} - now
        if lost and ident in after:
            gone[ident] = {name.split(':')[-1] for name in lost}
    return gone


def matches(tests, gone_phrases, gone_names):
    """{test identity: reason} for tests that depend on removed wording or removed definitions."""
    found = {}
    for ident, record in tests.items():
        used = gone_names & set(record.get('names', ()))
        if used:
            found[ident] = 'uses removed ' + ', '.join(sorted(used)[:2])
            continue
        if not gone_phrases:
            continue
        shared = set()
        for literal in record.get('literals', ()):
            words = [w.lower() for w in WORD.findall(literal)]
            shared |= {' '.join(words[i:i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)} & gone_phrases
        # One or two shared triples are ordinary English; a run of them is the removed message itself.
        if len(shared) >= ENOUGH:
            found[ident] = f"asserts removed text '{max(shared, key=len)}'"
    return found


def expecting(tests, reached, categories):
    """Tests that reach a function and expect a warning or exception it no longer raises."""
    found = {}
    for ident in reached:
        names = set(tests[ident].get('names', ())) if ident in tests else set()
        wanted = categories & names
        if wanted and names & {'warns', 'raises', 'deprecated_call', 'assertRaises', 'assertWarns'}:
            found[ident] = 'expects ' + ', '.join(sorted(wanted)) + ' the change no longer raises'
    return found
