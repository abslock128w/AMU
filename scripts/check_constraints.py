"""Reject dependency drift; run alongside pip check in a clean environment.

One exact version per name is supported. Markers govern installation, not which
version is accepted when a distribution is installed.
"""

import re
from importlib.metadata import distributions
from pathlib import Path


def normalize(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def read_pins(path):
    pins = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([^\s;]+)(?:\s*;.*)?', line)
        if not match:
            raise ValueError(f'Not an exact constraint: {line}')
        name, version = normalize(match[1]), match[2]
        if name in pins:
            raise ValueError(f'Duplicate constraint: {name}')
        pins[name] = version
    if not pins:
        raise ValueError('Empty constraints')
    return pins


def check(pins, installed):
    errors = []
    for name, version in installed:
        name = normalize(name)
        # The app has a separate version smoke check; pip bootstraps the environment.
        if name in {'amu', 'anti-messup', 'pip'}:
            continue
        if pins.get(name) != version:
            errors.append(f'{name}: installed {version}, expected {pins.get(name, "a reviewed pin")}')
    return errors


if __name__ == '__main__':
    pins = read_pins(Path(__file__).resolve().parents[1] / 'constraints.txt')
    errors = check(pins, [(d.metadata['Name'], d.version) for d in distributions()])
    if errors:
        raise SystemExit('\n'.join(errors))
    print('Installed dependencies match constraints.')
