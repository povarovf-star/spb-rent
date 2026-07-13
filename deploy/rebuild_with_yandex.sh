#!/usr/bin/env bash
# Full rebuild of the dataset and model on the COMBINED data (CIAN + Yandex).
# Run ON THE MAC after the Yandex collection has finished
# (offers ~ ids in data/yandex.sqlite).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv_mac/bin/python

echo "== 1. Merging sources (CIAN + Yandex, KNN geo derivation) =="
$PY -m src.cleaning.merge_sources

echo "== 2. Cleaning the combined set + cross-platform dedup =="
$PY -m src.cleaning.clean --parquet data/processed/all_sources_raw.parquet

echo "== 3. Features =="
$PY -m src.features.build

echo "== 4. Retraining the model (compare MAE with the previous 7,310 RUB) =="
$PY -m src.models.train --stage baselines
$PY -m src.models.train --stage final
$PY -m src.models.quantiles
$PY -m src.models.explain scan
$PY -m src.app.prepare_assets

echo "Done. Check models/metrics.json, then redeploy: bash deploy/deploy_app.sh"
