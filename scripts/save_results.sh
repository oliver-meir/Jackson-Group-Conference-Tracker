#!/usr/bin/env bash
# Commit data/ changes made by the checker and push them to main.
# If someone else saved at the same moment (e.g. a Confirm/Remove click on the website),
# start again from the latest data and re-run the check, up to 3 times.
# Usage: scripts/save_results.sh "<commit message>" <checker args...>
set -u
MSG="$1"; shift
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
for attempt in 1 2 3; do
  git add data
  if git diff --cached --quiet; then
    echo "Nothing new to save."
    exit 0
  fi
  git commit -q -m "$MSG"
  if git push -q origin HEAD:main; then
    echo "Saved."
    exit 0
  fi
  echo "Someone else saved at the same time. Retrying with the latest data ($attempt/3)."
  git fetch -q origin main
  git reset -q --hard origin/main
  python scripts/check_watchlist.py "$@" || exit 1
done
echo "Could not save after 3 attempts."
exit 1
