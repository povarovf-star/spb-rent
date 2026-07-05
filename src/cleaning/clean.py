"""Чистка данных: фильтры мусора + дедупликация агентских клонов.

Каждый фильтр логируется: сколько строк убито и почему. Итог — отчёт
data/processed/cleaning_report.md (таблица потерь — прямо в README).

Запуск:
    python -m src.cleaning.clean                # по умолчанию data/cian.sqlite
    python -m src.cleaning.clean --db path.sqlite --out data/processed
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pandas as pd

# Жёсткие границы валидности (долгосрочная аренда, СПб + ближний пригород)
PRICE_MIN, PRICE_MAX = 8_000, 350_000
AREA_MIN, AREA_MAX = 10.0, 200.0
PPM2_MIN, PPM2_MAX = 300.0, 6_000.0  # ₽ за м² в месяц

COLUMNS = """offer_id snapshot_date price deposit client_fee_pct agent_fee_pct
utilities_included rooms flat_type is_apartments total_area living_area
kitchen_area floor floors_total build_year material_type lat lon district
okrug address metro_name metro_time_min metro_transport is_by_homeowner
published_ts photos_count region url description""".split()


def load(db_path: str | Path, snapshot: str | None = None) -> pd.DataFrame:
    """Читает последний (или указанный) снимок. raw_json не тянем — тяжёлый."""
    conn = sqlite3.connect(db_path)
    if snapshot is None:
        snapshot = conn.execute(
            "SELECT MAX(snapshot_date) FROM offers"
        ).fetchone()[0]
    df = pd.read_sql(
        f"SELECT {', '.join(COLUMNS)} FROM offers WHERE snapshot_date = ?",
        conn, params=(snapshot,),
    )
    conn.close()
    return df


class FilterLog:
    def __init__(self, n_start: int) -> None:
        self.rows: list[tuple[str, int, int]] = []
        self.n = n_start

    def apply(self, df: pd.DataFrame, mask_keep: pd.Series, name: str) -> pd.DataFrame:
        dropped = int((~mask_keep).sum())
        df = df[mask_keep].copy()
        self.rows.append((name, dropped, len(df)))
        self.n = len(df)
        return df

    def to_markdown(self, n_start: int) -> str:
        lines = [
            "| Фильтр | Убрано строк | Осталось |",
            "|---|---:|---:|",
            f"| исходно | — | {n_start} |",
        ]
        for name, dropped, left in self.rows:
            lines.append(f"| {name} | {dropped} | {left} |")
        return "\n".join(lines)


def add_derived(df: pd.DataFrame) -> pd.DataFrame:
    """Производные колонки, нужные фильтрам и модели."""
    df = df.copy()
    # rooms_n: 0 = студия/свободная планировка, иначе число комнат
    df["rooms_n"] = df["rooms"].where(df["flat_type"] == "rooms", 0)
    df["is_studio"] = (df["flat_type"] == "studio").astype(int)
    df["is_lenobl"] = (df["region"] == 4588).astype(int)
    df["price_per_m2"] = df["price"] / df["total_area"]
    if "source" not in df.columns:       # одиночный источник — по умолчанию ЦИАН
        df["source"] = "cian"
    return df


def clean(df: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    n_start = len(df)
    log = FilterLog(n_start)
    df = add_derived(df)

    df = log.apply(df, df["price"].between(PRICE_MIN, PRICE_MAX),
                   f"цена вне [{PRICE_MIN}, {PRICE_MAX}] ₽/мес")
    df = log.apply(df, df["total_area"].between(AREA_MIN, AREA_MAX),
                   f"площадь вне [{AREA_MIN}, {AREA_MAX}] м²")
    df = log.apply(df, df["lat"].notna() & df["lon"].notna(), "нет координат")
    df = log.apply(df, df["price_per_m2"].between(PPM2_MIN, PPM2_MAX),
                   f"цена/м² вне [{PPM2_MIN}, {PPM2_MAX}] ₽")
    df = log.apply(df, df["rooms_n"].notna(), "не определить комнатность")
    # грубая ошибка геокода: далеко за пределами агломерации
    df = log.apply(df, df["lat"].between(59.5, 60.5) & df["lon"].between(29.5, 31.0),
                   "координаты вне агломерации СПб")

    # --- флаг подозрительно дёшево (скам): < 50% медианы цены/м² своего района ---
    seg = df["district"].fillna("ЛО")
    seg_median = df.groupby(seg)["price_per_m2"].transform("median")
    df["is_suspicious_cheap"] = (df["price_per_m2"] < 0.5 * seg_median).astype(int)

    # --- дедупликация агентских клонов ---
    # Одна квартира у нескольких агентств: совпадают координаты (~10 м),
    # площадь (±0.25 м²), этаж и комнатность. Оставляем собственника,
    # при равенстве — минимальную цену.
    df["dup_key"] = (
        df["lat"].round(4).astype(str) + "_" + df["lon"].round(4).astype(str)
        + "_" + (df["total_area"] * 2).round().astype(int).astype(str)
        + "_" + df["floor"].astype(str) + "_" + df["rooms_n"].astype(int).astype(str)
    )
    df["n_clones"] = df.groupby("dup_key")["offer_id"].transform("count")
    # число площадок в группе-дубле: 2 = квартира есть и на ЦИАН, и на Яндексе
    df["n_platforms"] = df.groupby("dup_key")["source"].transform("nunique")
    df["cross_platform"] = (df["n_platforms"] >= 2).astype(int)
    # приоритет при склейке: ЦИАН (богаче полями) > собственник > меньшая цена
    df["_cian_first"] = (df["source"] != "cian").astype(int)
    df = df.sort_values(
        ["dup_key", "_cian_first", "is_by_homeowner", "price"],
        ascending=[True, True, False, True],
    )
    before = len(df)
    df = df.drop_duplicates("dup_key", keep="first").drop(columns="_cian_first")
    log.rows.append(("дубли (агентства + кросс-платформенные)", before - len(df), len(df)))

    report = log.to_markdown(n_start)
    return df.drop(columns=["dup_key"]), report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/cian.sqlite")
    parser.add_argument("--parquet", default=None,
                        help="читать объединённый датасет (ЦИАН+Яндекс) вместо sqlite")
    parser.add_argument("--out", default="data/processed")
    parser.add_argument("--snapshot", default=None)
    args = parser.parse_args()

    if args.parquet:
        df = pd.read_parquet(args.parquet)
    else:
        df = load(args.db, args.snapshot)
    snapshot = df["snapshot_date"].iloc[0]
    cleaned, report = clean(df)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cleaned.to_parquet(out / "listings.parquet", index=False)

    header = (
        f"# Отчёт чистки данных\n\nСнимок: {snapshot}. "
        f"Вход: {len(df)} объявлений, выход: {len(cleaned)}.\n\n"
    )
    by_source = dict(cleaned["source"].value_counts()) if "source" in cleaned else {}
    extras = (
        f"\n\nПомечено флагом (не удалено):\n"
        f"- подозрительно дёшево (< 50% медианы района): "
        f"{int(cleaned['is_suspicious_cheap'].sum())}\n"
        f"- апартаменты: {int(cleaned['is_apartments'].fillna(0).sum())}\n"
        f"- Ленобласть: {int(cleaned['is_lenobl'].sum())}\n"
        f"- по источникам: {by_source}\n"
        f"- кросс-платформенных (квартира и на ЦИАН, и на Яндексе): "
        f"{int(cleaned['cross_platform'].sum()) if 'cross_platform' in cleaned else 0}\n"
    )
    (out / "cleaning_report.md").write_text(header + report + extras, encoding="utf-8")
    print(header + report + extras)


if __name__ == "__main__":
    main()
