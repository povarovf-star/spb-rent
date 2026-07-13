#!/usr/bin/env bash
# Full Yandex.Realty collection on the Mac via a headless browser.
# Run ON THE MAC (not on the server, which has little RAM and a trading bot next to it).
# Recommended in tmux, since it takes several hours:
#   tmux new -s yandex 'bash deploy/collect_yandex.sh'
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv_mac/bin/python

# Phase 1: segmented id collection (rooms × price bands with splitting).
# Gets past the search depth cap (~600/query), up to ~4-5k unique ids.
echo "== Phase 1: id collection (segments) =="
$PY -m src.scraping.yandex serp

# Phase 2: fill in fields + coordinates for all uncollected ids (one card at a time).
# Slow (~1 page/4 sec). Restartable: only fetched=0 are collected.
echo "== Phase 2: offer cards =="
$PY -m src.scraping.yandex offers --limit 100000

echo "Done. Data in data/yandex.sqlite (source='yandex')."
