"""Price explanation and overpricing detector.

Explainer.explain(row) -> a JSON-compatible dict:
- fair_price, the interval [low, high] (conformally calibrated);
- contributions of human-readable factors in RUB (aggregated SHAP);
- a verdict for the actual price: overpriced / fair / suspicious_cheap.

SHAP is taken natively from CatBoost (type="ShapValues"), in log space.
A group's contribution in RUB is computed counterfactually: "what the flat would
cost if this group's contribution were removed", pred - pred/exp(phi_g). The sums
of contributions are approximate (log space), which the UI states honestly.

CLI:
    python -m src.models.explain demo          # explain a random flat
    python -m src.models.explain scan          # market scan -> market_scan.parquet
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool

from src.features.build import CAT_FEATURES, NUM_FEATURES

MODELS_DIR = Path("models")

# human-readable feature groups (names shown to the user, kept in Russian)
FACTOR_GROUPS: dict[str, list[str]] = {
    "Локация": ["district", "okrug", "metro_name", "h3_08", "dist_center_km",
                 "dist_moscow_st_km", "metro_walk_min", "has_metro_nearby",
                 "is_lenobl"],
    "Площадь и планировка": ["total_area", "living_area", "kitchen_area",
                              "rooms_n", "is_studio", "flat_type"],
    "Дом": ["building_age", "material_type", "floors_total", "floor",
            "floor_first", "floor_last", "floor_ratio", "new_building",
            "concierge", "parking", "is_apartments"],
    "Состояние и удобства": ["renov_euro", "renov_cosmetic", "renov_needed",
                              "furnished", "unfurnished", "dishwasher", "washer",
                              "aircon", "fridge", "balcony", "nice_view"],
    "Условия сделки": ["no_deposit", "utilities_included", "no_client_fee",
                        "is_by_homeowner", "pets_ok", "pets_no", "kids_no"],
    "Прочее": ["photos_count", "desc_len", "has_description"],
}

VERDICTS = {
    "overpriced": "цена выше типичной для таких квартир",
    "fair": "цена в рынке",
    "suspicious_cheap": "подозрительно дёшево — проверьте объявление",
}


class Explainer:
    def __init__(self, models_dir: str | Path = MODELS_DIR) -> None:
        d = Path(models_dir)
        self.model = CatBoostRegressor()
        self.model.load_model(str(d / "catboost.cbm"))
        self.qmodel = CatBoostRegressor()
        self.qmodel.load_model(str(d / "quantiles.cbm"))
        self.cqr_delta = json.loads(
            (d / "quantile_calibration.json").read_text())["cqr_delta_log"]
        self.features = CAT_FEATURES + NUM_FEATURES
        # column -> group
        self.col2group = {c: g for g, cols in FACTOR_GROUPS.items() for c in cols}

    def _pool(self, df: pd.DataFrame) -> Pool:
        return Pool(df[self.features], cat_features=CAT_FEATURES)

    def predict_interval(self, df: pd.DataFrame) -> pd.DataFrame:
        q = self.qmodel.predict(self._pool(df))  # log, (n, 3)
        point = np.exp(self.model.predict(self._pool(df)))
        return pd.DataFrame({
            "fair_price": point,
            "price_low": np.exp(q[:, 0] - self.cqr_delta),
            "price_high": np.exp(q[:, 2] + self.cqr_delta),
        }, index=df.index)

    def factor_contributions(self, df: pd.DataFrame) -> pd.DataFrame:
        """SHAP by group, in RUB (counterfactual, see the module docstring)."""
        shap = self.model.get_feature_importance(self._pool(df), type="ShapValues")
        phi = pd.DataFrame(shap[:, :-1], columns=self.features, index=df.index)
        pred = np.exp(self.model.predict(self._pool(df)))
        out = {}
        for g in FACTOR_GROUPS:
            cols = [c for c in FACTOR_GROUPS[g] if c in phi.columns]
            phi_g = phi[cols].sum(axis=1)
            out[g] = pred - pred / np.exp(phi_g)
        return pd.DataFrame(out, index=df.index).round(0)

    def verdict(self, actual_price: float, low: float, high: float) -> str:
        if actual_price > high:
            return "overpriced"
        if actual_price < low:
            return "suspicious_cheap"
        return "fair"

    def explain(self, row: pd.Series | pd.DataFrame) -> dict:
        df = row.to_frame().T if isinstance(row, pd.Series) else row
        df = df.reset_index(drop=True)
        iv = self.predict_interval(df).iloc[0]
        contrib = self.factor_contributions(df).iloc[0]
        result = {
            "fair_price": round(float(iv.fair_price), -2),
            "price_low": round(float(iv.price_low), -2),
            "price_high": round(float(iv.price_high), -2),
            "factors": {k: float(v) for k, v in
                        contrib.sort_values(key=abs, ascending=False).items()},
        }
        if "price" in df.columns and pd.notna(df["price"].iloc[0]):
            actual = float(df["price"].iloc[0])
            v = self.verdict(actual, iv.price_low, iv.price_high)
            result.update({
                "actual_price": actual,
                "delta_vs_fair": round(actual - result["fair_price"]),
                "verdict": v,
                "verdict_text": VERDICTS[v],
            })
        return result


def cmd_demo() -> None:
    df = pd.read_parquet("data/processed/features.parquet")
    ex = Explainer()
    row = df.sample(1, random_state=7).iloc[0]
    print(f"Flat: {row.rooms_n:.0f} rooms (type {row.flat_type}), "
          f"{row.total_area} m2, {row.district}, metro {row.metro_name} "
          f"({row.metro_walk_min:.0f} min on foot)")
    print(json.dumps(ex.explain(row), ensure_ascii=False, indent=2))


def cmd_scan() -> None:
    """Market scan: verdicts for all listings -> market_scan.parquet."""
    df = pd.read_parquet("data/processed/features.parquet")
    ex = Explainer()
    iv = ex.predict_interval(df)
    scan = pd.concat([
        df[["offer_id", "url", "price", "district", "metro_name", "rooms_n",
            "total_area", "is_suspicious_cheap", "lat", "lon"]], iv], axis=1)
    scan["delta"] = scan["price"] - scan["fair_price"]
    scan["delta_pct"] = (scan["delta"] / scan["fair_price"] * 100).round(1)
    scan["verdict"] = np.select(
        [scan.price > scan.price_high, scan.price < scan.price_low],
        ["overpriced", "suspicious_cheap"], default="fair")
    scan.to_parquet("data/processed/market_scan.parquet", index=False)

    print("verdicts:", dict(scan.verdict.value_counts()))
    print("\n--- top 10 overpriced (RUB/mo above fair) ---")
    cols = ["district", "metro_name", "rooms_n", "total_area", "price",
            "fair_price", "delta", "delta_pct"]
    top = scan[scan.verdict == "overpriced"].nlargest(10, "delta")[cols]
    print(top.to_string(index=False))
    print("\n--- top 10 suspiciously cheap ---")
    bot = scan[scan.verdict == "suspicious_cheap"].nsmallest(10, "delta")[cols]
    print(bot.to_string(index=False))


if __name__ == "__main__":
    {"demo": cmd_demo, "scan": cmd_scan}[sys.argv[1] if len(sys.argv) > 1 else "demo"]()
