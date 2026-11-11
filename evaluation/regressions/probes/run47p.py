"""Phase 2: the frozen 47 pull requests with probes on (claude -p), at the settings of the regression probe trial.
Usage: python run47p.py --engine DIR --tag TAG --only repo#n ..."""
import subprocess
import sys

import run47

real = subprocess.run


def run(command, *p, **k):
    if '--generate-dollars' in command:
        command = list(command)
        command[command.index('--budget') + 1] = '120'
        command[command.index('--wall-cap') + 1] = '2400'
        command += ['--generate-cli', '--generate-minutes', '15']
    return real(command, *p, **k)


run47.subprocess.run = run
sys.argv += ['--no-raise']
run47.main()
