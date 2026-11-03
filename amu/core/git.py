"""Read the uncommitted change of a working tree, and files as a commit holds them. Git output is data; nothing here
writes."""
from __future__ import annotations

import os
import subprocess


def _git(root, *args, raw=False):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0')
    done = subprocess.run(['git', '-C', str(root), *args], capture_output=True, timeout=30, env=env)
    if done.returncode:
        raise OSError(done.stderr.decode(errors='replace').strip()[:200] or 'git failed')
    return done.stdout if raw else done.stdout.decode(errors='replace')


def file_at(root, commit, path):
    """``path`` as ``commit`` holds it, newlines as read_text gives them: '' when the commit has no such file, None
    when the commit or the text cannot be read. The blob as stored: no textconv, no filters."""
    try:
        data = _git(root, 'cat-file', 'blob', f'{commit}:{path}', raw=True)
    except (OSError, subprocess.TimeoutExpired):
        try:
            _git(root, 'rev-parse', '--verify', '--quiet', f'{commit}^{{commit}}')
        except (OSError, subprocess.TimeoutExpired):
            return None
        return ''
    try:
        return data.decode().replace('\r\n', '\n').replace('\r', '\n')
    except UnicodeDecodeError:
        return None


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


def since_diff(root, ref):
    """(diff from where the working tree's branch left ``ref`` to the working tree, that commit, gaps): the commits since
    ``ref`` and what is not committed yet. Never raises; an unknown ref is a gap and an empty diff."""
    try:
        base = _git(root, 'merge-base', ref, 'HEAD').strip()
        diff = _git(root, 'diff', base, '--no-ext-diff', '--no-textconv', '--no-color', '--no-renames')
    except (OSError, subprocess.TimeoutExpired) as exc:
        return '', None, [f'--since {ref}: no common commit with HEAD or git unavailable ({exc})']
    return diff, base, [] if diff.strip() else [f'No change since {ref} ({base[:12]})']
