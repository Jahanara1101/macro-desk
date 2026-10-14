#!/usr/bin/env bash
# update.sh — refresh the macro data and publish it to GitHub Pages.
# Safe to run every 15 min: it commits ONLY when the data actually changed.
set -uo pipefail
REPO="/root/macro-desk"
cd "$REPO" || exit 1

python3 scripts/fetch_macro.py >/tmp/macro_fetch.log 2>&1 || { echo "fetch failed"; exit 1; }

git add data >/dev/null 2>&1
if git diff --cached --quiet; then
  exit 0                      # nothing changed — stay silent
fi

STAMP=$(python3 -c "import json;print(json.load(open('data/meta.json'))['built_utc'])" 2>/dev/null)
git -c user.name="Noman" -c user.email="Jahanara1101@users.noreply.github.com" \
    commit -q -m "data: refresh $STAMP" && git push -q origin main
echo "pushed: $(git rev-parse --short HEAD)"
