#!/bin/bash
# One fork's live results, in GitHub Actions (artifacts can't be downloaded where the reports are written): collect.py,
# then the raise measure (evaluation/generalize/raise_missed.py of the engine) on each pull request's head with the
# fork's install line. Usage: pilot-collect.sh FORK OPENED INSTALL OUT
set -uo pipefail
fork=$1; opened=$2; install=$3; out=$4
W=$AMU_EVAL_WORK; mkdir -p "$W" "$out"
git clone -q https://github.com/abslock128w/AMU "$W/engine" && git -C "$W/engine" checkout -q "$ENGINE_REF"
git clone -q "https://github.com/$fork" "$W/clone"
python evaluation/v071-tools/collect.py --fork "$fork" --opened "$opened" --out "$out"
for d in "$out"/*/; do
  n=$(basename "$d")
  [ -f "$d/report.json" ] && [ -f "$d/patch.diff" ] || { echo "no report or patch for $n"; continue; }
  head=$(python -c "import json,sys; m=json.load(open(sys.argv[1])); print(m.get('head') or m.get('head_sha'))" "$d/meta.json")
  git -C "$W/clone" fetch -q origin "$head" 2>/dev/null
  tree="$W/tree-$n"; git -C "$W/clone" worktree add -q --detach "$tree" "$head"
  python -m venv "$W/venv-$n"
  (cd "$tree" && source "$W/venv-$n/bin/activate" && bash -c "$install") > "$d/raise-install.log" 2>&1
  timeout 2400 python "$W/engine/evaluation/generalize/raise_missed.py" --head "$tree" --patch "$d/patch.diff" \
    --report "$d/report.json" --python "$W/venv-$n/bin/python" --runner pytest --workers 3 > "$d/raise.json" 2> "$d/raise.err"
  echo "$n raise: $(head -c 300 "$d/raise.json")"
  git -C "$W/clone" worktree remove --force "$tree"
done
