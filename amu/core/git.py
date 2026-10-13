"""Read the uncommitted change of a working tree. Git output is data; nothing here writes."""
from __future__ import annotations

import os
import subprocess


def _git(root, *args):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0')
    done = subprocess.run(['git', '-C', str(root), *args], capture_output=True, timeout=30, env=env)
    if done.returncode:
        raise OSError(done.stderr.decode(errors='replace').strip()[:200] or 'git failed')
    return done.stdout.decode(errors='replace')


def working_diff(root):
    """(diff of tracked changes against HEAD, gaps). Never raises; a missing repository is a gap."""
    gaps = []
    try:
        # External diff drivers and textconv filters are repository-controlled programs; keep them off.
        diff = _git(root, 'diff', 'HEAD', '--no-ext-diff', '--no-textconv', '--no-color', '--no-renames')
    except (OSError, subprocess.TimeoutExpired) as exc:
        return '', [f'No patch given and git diff unavailable ({exc}); pass --patch-file']
    try:
        untracked = [line[3:] for line in _git(root, 'status', '--porcelain', '--untracked-files=all').splitlines()
                     if line.startswith('?? ') and line.endswith(('.py', '.pyi'))]
        if untracked:
            gaps.append(f'{len(untracked)} untracked Python file(s) not in the diff; new files have no existing callers')
    except (OSError, subprocess.TimeoutExpired):
        pass
    if not diff.strip():
        gaps.append('No uncommitted change to tracked files')
    return diff, gaps
