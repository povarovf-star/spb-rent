"""Квантильная модель: интервалы предсказания q10/q50/q90.

Отдельная CatBoost-модель с MultiQuantile-лоссом. Интервал нужен:
- пользователю: «справедливая цена 42 000 ₽ (37 000–48 000)»;
- детектору переплаты: цена выше q90 — статистически «дорого для таких
  квартир», а не просто «выше точечного предсказания».

Запуск: python -m src.models.quantiles
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor
from sklearn.model_selection import train_test_split

from src.features.build import CAT_FEATURES, NUM_FEATURES
from src.models.train import SEED, MODELS_DIR, make_pool, _load_split

QUANTILES = [0.1, 0.5, 0.9]

QCB_PARAMS = dict(
    loss_function=f"MultiQuantile:alpha={','.join(map(str, QUANTILES))}",
    iterations=1000,
    learning_rate=0.08,
    depth=6,
    early_stopping_rounds=100,
    random_seed=SEED,
    verbose=0,
)


def main() -> None:
    train_df, hold_df, y_tr, y_hold = _load_split()
    tr2, va2, ytr2, yva2 = train_test_split(
        train_df, y_tr, test_size=0.1, random_state=SEED)

    model = CatBoostRegressor(**QCB_PARAMS)
    model.fit(make_pool(tr2, ytr2), eval_set=make_pool(va2, yva2))
    MODELS_DIR.mkdir(exist_ok=True)
    model.save_model(str(MODELS_DIR / "quantiles.cbm"))

    # --- конформная калибровка (CQR, Romano et al. 2019) ---
    # Квантильная регрессия самоуверенна: сырое покрытие [q10, q90] < 80%.
    # На валидации считаем conformity score s = max(q10 - y, y - q90)
    # и расширяем обе границы на его 80-й процентиль (в log-пространстве).
    va_pred = model.predict(make_pool(va2))  # log, (n, 3)
    scores = np.maximum(va_pred[:, 0] - yva2.values, yva2.values - va_pred[:, 2])
    delta = float(np.quantile(scores, 0.8))
    (MODELS_DIR / "quantile_calibration.json").write_text(
        json.dumps({"cqr_delta_log": delta, "target_coverage": 0.8}))

    # покрытие на holdout ПОСЛЕ калибровки (калибровка — на валидации, честно)
    preds_log = model.predict(make_pool(hold_df))
    lo = np.exp(preds_log[:, 0] - delta)
    mid = np.exp(preds_log[:, 1])
    hi = np.exp(preds_log[:, 2] + delta)
    y = np.exp(y_hold.values)
    stats = {
        "cqr_delta_log": round(delta, 4),
        "coverage_q10_q90_calibrated": round(float(np.mean((y >= lo) & (y <= hi))), 3),
        "share_below_q10": round(float(np.mean(y < lo)), 3),
        "share_above_q90": round(float(np.mean(y > hi)), 3),
        "median_interval_width_rel": round(float(np.median((hi - lo) / mid)), 3),
    }
    path = MODELS_DIR / "metrics.json"
    m = json.loads(path.read_text()) if path.exists() else {}
    m["quantile_model"] = stats
    path.write_text(json.dumps(m, indent=2))
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
