#!/bin/bash
# Push a folder as a one-commit branch of this repository, since artifacts can't be downloaded where the reports are
# written. Usage: push-out.sh DIR BRANCH
set -euo pipefail
dir=$1; branch=$2
cd "$dir"
git init -q; git checkout -q -b out
git add -A
git -c user.name=abslock128w -c user.email=rajesh.v@vmrcloudsolutions.com commit -qm "eval output: $branch" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
for i in 1 2 3 4; do
  git push -q -f "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}" "out:refs/heads/$branch" && exit 0
  sleep $((2 ** i))
done
exit 1
