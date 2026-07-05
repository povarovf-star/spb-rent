#!/usr/bin/env bash
# Полная пересборка датасета и модели на ОБЪЕДИНЁННЫХ данных (ЦИАН + Яндекс).
# Запускать НА МАКЕ после того, как сбор Яндекса завершился
# (offers ≈ ids в data/yandex.sqlite).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv_mac/bin/python

echo "== 1. Слияние источников (ЦИАН + Яндекс, KNN-деривация гео) =="
$PY -m src.cleaning.merge_sources

echo "== 2. Чистка объединённого + кросс-платформенный дедуп =="
$PY -m src.cleaning.clean --parquet data/processed/all_sources_raw.parquet

echo "== 3. Признаки =="
$PY -m src.features.build

echo "== 4. Переобучение модели (сравни MAE с прежним 7 310 ₽) =="
$PY -m src.models.train --stage baselines
$PY -m src.models.train --stage final
$PY -m src.models.quantiles
$PY -m src.models.explain scan
$PY -m src.app.prepare_assets

echo "Готово. Проверь models/metrics.json, затем передеплой: bash deploy/deploy_app.sh"
