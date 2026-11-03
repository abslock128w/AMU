#!/bin/bash
# Setup for one eval47.yml job: the engine at $ENGINE_REF in its own venv, a blobless clone of the project, and the
# project environments run47.py and record47.py expect (Python 3.14, as in the v0.7.0 runs).
set -euo pipefail
repo=$1
W=$AMU_EVAL_WORK
mkdir -p "$W/repos" "$W/env"
git clone -q --filter=blob:none https://github.com/abslock128w/AMU "$W/engine"
git -C "$W/engine" checkout -q "$ENGINE_REF"
python -m venv "$W/amuvenv"
"$W/amuvenv/bin/python" -m pip install -q --disable-pip-version-check "$W/engine"
case $repo in
  django) url=https://github.com/django/django ;;
  sympy) url=https://github.com/sympy/sympy ;;
  flask) url=https://github.com/pallets/flask ;;
esac
git clone -q --filter=blob:none "$url" "$W/repos/$repo"
case $repo in
  django)
    python -m venv "$W/env/django"; "$W/env/django/bin/pip" install -q asgiref sqlparse ;;
  sympy)
    python -m venv "$W/env/sympy"; "$W/env/sympy/bin/pip" install -q mpmath pytest hypothesis ;;
  flask)
    for e in flask flask-p90; do
      python -m venv "$W/env/$e"
      pin=$([ $e = flask-p90 ] && echo 'pytest==9.0.2' || echo 'pytest>=9.2,<9.3')
      "$W/env/$e/bin/pip" install -q blinker click itsdangerous jinja2 markupsafe werkzeug asgiref python-dotenv "$pin"
    done ;;
esac
for e in "$W"/env/*; do echo "$(basename "$e"): $("$e/bin/python" -m pip freeze | tr '\n' ' ')"; done
