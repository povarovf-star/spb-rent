"""Обучение: лестница моделей baseline → Ridge → CatBoost.

Дизайн:
- target = log(price), метрики в рублях после exp;
- скам-флаги (is_suspicious_cheap) исключены из train и eval;
- holdout 20% — финальные цифры; 5-fold CV — устойчивость;
- GroupKFold по h3_08 — проверка пространственного обобщения
  (модель оценивает гексагоны, которых не видела);
- сегментный разбор ошибки: комнатность, удалённость, ценовой терцил.

Запуск: python -m src.models.train  (артефакты в models/)
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool
from sklearn.linear_model import Ridge
from sklearn.model_selection import GroupKFold, KFold, train_test_split

from src.features.build import CAT_FEATURES, NUM_FEATURES

SEED = 42
MODELS_DIR = Path("models")

CB_PARAMS = dict(
    loss_function="MAE",
    iterations=1000,
    learning_rate=0.08,
    depth=6,
    early_stopping_rounds=100,
    random_seed=SEED,
    verbose=0,
)


def metrics_rub(y_true_log: np.ndarray, y_pred_log: np.ndarray) -> dict:
    y_true, y_pred = np.exp(y_true_log), np.exp(y_pred_log)
    ape = np.abs(y_true - y_pred) / y_true
    return {
        "MAE": float(np.mean(np.abs(y_true - y_pred))),
        "MdAE": float(np.median(np.abs(y_true - y_pred))),
        "MdAPE_%": float(np.median(ape) * 100),
    }


# ---------- baseline: медиана ₽/м² по (район × комнатность) ----------

class MedianBaseline:
    def fit(self, df: pd.DataFrame, y_log: pd.Series):
        d = df.assign(ppm2=np.exp(y_log) / df["total_area"])
        self.by_seg = d.groupby(["district", "rooms_n"])["ppm2"].median()
        self.by_district = d.groupby("district")["ppm2"].median()
        self.global_ = d["ppm2"].median()
        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        keys = pd.MultiIndex.from_frame(df[["district", "rooms_n"]])
        ppm2 = pd.Series(self.by_seg.reindex(keys).values, index=df.index)
        ppm2 = ppm2.fillna(df["district"].map(self.by_district)).fillna(self.global_)
        return np.log(ppm2.values * df["total_area"].values)


# ---------- Ridge на числовых ----------

def ridge_cv_mae(df: pd.DataFrame, y_log: pd.Series) -> dict:
    X = df[NUM_FEATURES].fillna(df[NUM_FEATURES].median())
    X = pd.concat([X, pd.get_dummies(df["district"], prefix="d")], axis=1)
    kf = KFold(5, shuffle=True, random_state=SEED)
    m = []
    for tr, te in kf.split(X):
        model = Ridge(alpha=1.0).fit(X.iloc[tr], y_log.iloc[tr])
        m.append(metrics_rub(y_log.iloc[te].values, model.predict(X.iloc[te])))
    return {k: float(np.mean([x[k] for x in m])) for k in m[0]}


# ---------- CatBoost ----------

def make_pool(df: pd.DataFrame, y_log: pd.Series | None = None) -> Pool:
    return Pool(
        df[CAT_FEATURES + NUM_FEATURES],
        label=y_log,
        cat_features=CAT_FEATURES,
    )


def catboost_cv(df: pd.DataFrame, y_log: pd.Series, splitter, groups=None) -> dict:
    m = []
    for tr, te in splitter.split(df, groups=groups):
        model = CatBoostRegressor(**CB_PARAMS)
        model.fit(make_pool(df.iloc[tr], y_log.iloc[tr]),
                  eval_set=make_pool(df.iloc[te], y_log.iloc[te]))
        m.append(metrics_rub(y_log.iloc[te].values,
                             model.predict(make_pool(df.iloc[te]))))
    return {k: float(np.mean([x[k] for x in m])) for k in m[0]}


def segment_report(df: pd.DataFrame, y_log, y_pred_log) -> pd.DataFrame:
    d = df.copy()
    d["abs_err"] = np.abs(np.exp(y_log) - np.exp(y_pred_log))
    d["ape"] = d["abs_err"] / np.exp(y_log)
    d["rooms_cat"] = np.select(
        [d.is_studio == 1, d.rooms_n == 1, d.rooms_n == 2, d.rooms_n == 3],
        ["студия", "1", "2", "3"], default="4+")
    d["zone"] = np.select(
        [d.dist_center_km <= 5, d.dist_center_km <= 12],
        ["центр (≤5 км)", "середина (5–12)"], default="окраина (12+)")
    d["price_tier"] = pd.qcut(np.exp(y_log), 3, labels=["дёшево", "средне", "дорого"])
    rows = []
    for col in ["rooms_cat", "zone", "price_tier"]:
        g = d.groupby(col, observed=True).agg(
            n=("abs_err", "size"), MAE=("abs_err", "mean"), MdAPE=("ape", "median"))
        g["MdAPE"] = (g["MdAPE"] * 100).round(1)
        g["MAE"] = g["MAE"].round(0)
        g.index = [f"{col}={i}" for i in g.index]
        rows.append(g)
    return pd.concat(rows)


def _load_split():
    df = pd.read_parquet("data/processed/features.parquet")
    df = df[df["is_suspicious_cheap"] == 0].reset_index(drop=True)
    y_log = np.log(df["price"])
    train_df, hold_df, y_tr, y_hold = train_test_split(
        df, y_log, test_size=0.2, random_state=SEED)
    return (train_df.reset_index(drop=True), hold_df.reset_index(drop=True),
            y_tr.reset_index(drop=True), y_hold.reset_index(drop=True))


def _merge_metrics(update: dict) -> dict:
    """Стадии запускаются отдельными процессами — метрики копятся в json."""
    MODELS_DIR.mkdir(exist_ok=True)
    path = MODELS_DIR / "metrics.json"
    results = json.loads(path.read_text()) if path.exists() else {}
    results.update(update)
    path.write_text(json.dumps(results, indent=2))
    return results


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", default="all",
                        choices=["baselines", "cv", "spatial", "final", "all"])
    args = parser.parse_args()
    stages = (["baselines", "cv", "spatial", "final"]
              if args.stage == "all" else [args.stage])

    train_df, hold_df, y_tr, y_hold = _load_split()

    if "baselines" in stages:
        bl = MedianBaseline().fit(train_df, y_tr)
        r = {"baseline_median_ppm2": metrics_rub(y_hold.values, bl.predict(hold_df)),
             "ridge_cv": ridge_cv_mae(train_df, y_tr),
             "n_train": len(train_df), "n_holdout": len(hold_df)}
        print(json.dumps(_merge_metrics(r), indent=2, ensure_ascii=False))

    if "cv" in stages:
        r = {"catboost_cv_random": catboost_cv(
            train_df, y_tr, KFold(5, shuffle=True, random_state=SEED))}
        print(json.dumps(_merge_metrics(r), indent=2, ensure_ascii=False))

    if "spatial" in stages:
        r = {"catboost_cv_spatial_h3": catboost_cv(
            train_df, y_tr, GroupKFold(5), groups=train_df["h3_08"])}
        print(json.dumps(_merge_metrics(r), indent=2, ensure_ascii=False))

    if "final" in stages:
        final = CatBoostRegressor(**CB_PARAMS)
        tr2, va2, ytr2, yva2 = train_test_split(train_df, y_tr, test_size=0.1,
                                                random_state=SEED)
        final.fit(make_pool(tr2, ytr2), eval_set=make_pool(va2, yva2))
        hold_pred = final.predict(make_pool(hold_df))
        _merge_metrics({"catboost_holdout": metrics_rub(y_hold.values, hold_pred)})

        final.save_model(str(MODELS_DIR / "catboost.cbm"))
        (MODELS_DIR / "feature_spec.json").write_text(json.dumps(
            {"cat": CAT_FEATURES, "num": NUM_FEATURES}, ensure_ascii=False, indent=2))
        seg = segment_report(hold_df, y_hold.values, hold_pred)
        seg.to_csv(MODELS_DIR / "segment_report.csv")
        imp = pd.Series(
            final.get_feature_importance(make_pool(hold_df, y_hold)),
            index=CAT_FEATURES + NUM_FEATURES).sort_values(ascending=False)
        imp.to_csv(MODELS_DIR / "feature_importance.csv")

        print(json.dumps(json.loads((MODELS_DIR / "metrics.json").read_text()),
                         indent=2, ensure_ascii=False))
        print("\n--- сегменты (holdout) ---")
        print(seg.to_string())
        print("\n--- топ-15 признаков ---")
        print(imp.head(15).round(2).to_string())


if __name__ == "__main__":
    main()
