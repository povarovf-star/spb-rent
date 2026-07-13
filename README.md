# SPb Rent: what it really costs to rent an apartment in St. Petersburg

**Enter the flat's parameters and get a fair market rent with a range, a breakdown of what drives it, and a verdict: are you overpaying or is the price honest?**

🔗 **Live demo:** https://arenda-spb.vercel.app

An end-to-end ML project: my own data collection from two listing sites, cleaning and
deduplication, gradient boosting with interpretation, an overpricing detector, and a
web dashboard with a price map, deployed in Docker on a production server.

---

## Key numbers

| Metric | Value |
|---|---|
| Dataset after cleaning and dedup | **15,415** listings (CIAN 11,525 + Yandex 3,890) |
| Cross-platform duplicates caught (same flat on both sites) | 736 |
| **Model MAE** (mean error in rubles, holdout) | **8,606 ₽/mo** |
| Median error in % (MdAPE) | **10.4%** |
| Gain over the naive baseline | **-27%** (11,770 to 8,606 ₽) |
| Prediction interval coverage (CQR calibration) | **77%** |
| Overpriced listings (price above the upper bound of the interval) | 1,247 of 15,415 (**8.1%**) |

Data snapshot: **July 2026** (a cron job refreshes snapshots twice a week).

The metric is MAE in **rubles**, not an abstract RMSE. "The model is off by about 8.6k ₽
on average" is clear both to a client and to a user.

---

## How it works

```
CIAN (JSON API)   ┐
                  ├─► collect ─► clean + dedup ─► features ─► CatBoost ─► SHAP + intervals ─► FastAPI + web map
Yandex (headless) ┘           (filters,          (geo + text)  (log target)  (overpricing        (Docker on a VPS)
                               agency clones)                                 detector)
```

1. **Collection.** CIAN is fetched through the internal JSON search API (`curl_cffi` with a Chrome TLS fingerprint, adaptive segmentation by price and rooms to get past the ~700 depth limit, checkpoints). Yandex is fetched with a headless browser (Playwright): there is no clean HTTP access and offers are rendered by JS. A cron job takes snapshots twice a week, so a price history builds up.
2. **Cleaning.** Junk filters with a loss log (the report is written to `data/processed/cleaning_report.md`), removal of daily rentals, and the main part: **deduplication of a single flat** posted by several agencies and on both sites at once (736 cross-platform duplicates), by coordinates plus area plus floor.
3. **Features.** 6 categorical and 42 numeric. Geo: distance to the center and to the train station, minutes to the metro, H3 hexagons. Listing text: renovation, furniture, appliances.
4. **Model.** A `log(price)` target, CatBoost. Validation includes a spatial split (GroupKFold over hexagons, so the model scores districts it has not seen).
5. **Interpretation.** SHAP grouped into 6 human-readable factors with contributions in rubles. Prediction intervals via conformal calibration (CQR). An overpricing detector based on the interval.
6. **Product.** A FastAPI `/predict` service plus my own web front with a form, a price map over H3 hexagons, and a table of the most overpriced listings. Docker Compose on a VPS.

---

## Model ladder

| Model | Validation | MAE, ₽ | MdAPE |
|---|---|---:|---:|
| Naive baseline (median ₽/m² per district × room count) | holdout (3,054) | 11,770 | 15.8% |
| Ridge on numeric features | CV | 9,828 | 13.0% |
| **CatBoost + geo + text** | holdout (3,054) | **8,606** | **10.4%** |
| CatBoost | spatial GroupKFold over H3 | 8,830 | 11.5% |

The gap between random CV (7,976 ₽) and the spatial split (8,830 ₽) is moderate, which
means the model **generalizes over geography** instead of memorizing specific hexagons.

**Error by segment** is honestly uneven: studios sit at MAE 4,612 ₽, the outskirts at
5,069 ₽; the center and the expensive segment run 18k to 20k ₽ (little data there and huge
variance, since neighboring flats on Nevsky can cost twice as much as each other).

---

## What the data showed

- **Price is log-normal**, so training on `log(price)` is a must.
- **Geography is signal number one:** the median price per m² differs by **2.5x** between districts; the top of the feature importance list is area (31%), distance to the center (9.6%), district, and okrug.
- **Metro:** the premium for "5 minutes on foot or less" is about **15%** of the price per m².
- **Owner vs agent:** the median price is the same, but owners have a higher price per m².
- **Leningrad Oblast** (Murino, Kudrovo) is about **35%** cheaper than the city.
- **A dishwasher** correlates noticeably with price and lands in the top 15 features.

The full exploratory analysis with charts is in [`notebooks/01_eda.ipynb`](notebooks/01_eda.ipynb).

---

## Technical decisions and trade-offs

- **CatBoost, not a neural net.** Tabular data, a moderate volume, many categories (district, metro, building type); boosting is optimal and interpretable here.
- **Metro time is taken from the CIAN listing**, not computed from station coordinates, because it reflects real accessibility (rivers, railways) rather than a straight line.
- **Against leakage:** deduplication happens before the train/test split (otherwise one flat lands in both sets); target encoding and per-hexagon medians are computed only inside CV folds; `deposit_ratio` is excluded (the deposit is roughly one month's rent, so the price ends up in the denominator).
- **Intervals:** raw CatBoost quantiles turned out to be overconfident; conformal calibration (CQR) on validation raises coverage. On the combined data (CIAN + Yandex) holdout coverage is 77% against a target of 80: the heterogeneity of the sources makes the intervals slightly overconfident, and this is reported honestly rather than tuned away.
- **Yandex via a headless browser, not HTTP.** A necessary compromise: the gate API returns nothing without JS execution. It runs locally so it does not interfere with other services on the server.

---

## Limitations (honestly)

- The model learns the **typical market price** from listings. If the market is systematically inflated, it does not see that skew. This is not an estimate of an "absolutely fair" price, it is the "typical price for flats like this".
- The residual "actual minus predicted" is model error **plus** real overpayment; the two cannot be separated exactly. That is why the verdict is worded carefully ("above typical by N ₽") rather than "you are being ripped off". Prediction intervals partly solve this.
- The center and the premium segment are predicted worse: little data, high variance.
- This estimates the **current** price, not a forecast of the future. Prices drift, so re-scraping and retraining are needed (the cron job is already accumulating fresh snapshots).
- Apart-hotels and daily rentals are excluded or flagged.

---

## Example API request

```bash
curl -X POST https://arenda-spb.vercel.app/api/predict -H "Content-Type: application/json" \
  -d '{"district": "Калининский", "rooms": 1, "total_area": 35,
       "floor": 5, "floors_total": 12, "metro_walk_min": 7,
       "dishwasher": true, "actual_price": 52000}'
```
```json
{
  "fair_price": 44300,
  "price_low": 38900,
  "price_high": 50100,
  "factors": {"Локация": 4900, "Площадь и планировка": 3200, "…": 0},
  "actual_price": 52000,
  "delta_vs_fair": 7700,
  "verdict": "overpriced",
  "verdict_text": "цена выше типичной для таких квартир"
}
```

---

## Running it

> Raw data is not published in the repo, so steps 2 and 3 are reproducible only after
> collecting your own data (step 1).

```bash
pip install -r requirements.txt

# 1) collect CIAN data (restartable, checkpoints per segment)
python -m src.scraping.run probe         # check the API
python -m src.scraping.run collect        # full collection

# 2) clean -> features -> train
python -m src.cleaning.clean
python -m src.features.build
python -m src.models.train --stage all    # baseline -> ridge -> catboost
python -m src.models.quantiles            # intervals (CQR)
python -m src.models.explain scan         # market scan + overpricing detector

# 3) the app
python -m src.app.prepare_assets
docker compose up --build                 # API :8000 + dashboard :8501
```

The second source (Yandex, headless browser, local):
```bash
bash deploy/collect_yandex.sh             # Playwright, segmented collection
```

### Production

The public version at **https://arenda-spb.vercel.app** is a static front with
precomputed data on the Vercel CDN; `/api/predict` is proxied by a serverless
function (`web/api/predict.js`) to a VPS that holds the model.

```bash
# rebuild web/ (needs a local server on :8501) and deploy
bash deploy/build_web.sh
cd web && npx vercel deploy --prod
```

SEO: title/description, Open Graph (`static/og.png`), JSON-LD
(WebApplication + FAQPage), robots.txt, sitemap.xml, and a visible FAQ block.

---

## Stack

Python · `curl_cffi` · Playwright · pandas · **CatBoost** · SHAP · scikit-learn (CQR) ·
H3 · FastAPI · MapLibre GL + H3 · Docker Compose · SQLite/Parquet

## Structure

```
src/scraping/   collection: CIAN (API) and Yandex (headless), storage, CLI
src/cleaning/   junk filters + deduplication with a loss log
src/features/   geo and text features, build_features()
src/models/     train, quantiles (CQR), explain (SHAP + overpricing detector)
src/app/        FastAPI /predict + web front (form, map, market scan)
notebooks/      01_eda.ipynb, exploratory analysis with charts
tests/          unit tests for the parser and features
```

---

*All metrics come from real data; raw data is not published, and collection is for
educational purposes.*
