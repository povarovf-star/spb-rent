#!/usr/bin/env bash
# Builds the static version of the site for Vercel into web/:
# the frontend from src/app/static + precomputed JSON data.
# Requires a running local server: uvicorn src.app.site:app --port 8501
# Run from the project root:  bash deploy/build_web.sh
set -euo pipefail

SITE_URL="${SITE_URL:-http://127.0.0.1:8501}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WEB="$ROOT/web"

echo "==> Copying the frontend..."
mkdir -p "$WEB/static" "$WEB/data"
cp "$ROOT/src/app/static/index.html" "$WEB/index.html"
cp "$ROOT"/src/app/static/*.css "$ROOT"/src/app/static/*.js "$WEB/static/"
cp "$ROOT"/src/app/static/*.png "$ROOT"/src/app/static/*.svg "$WEB/static/"

echo "==> Downloading data from $SITE_URL..."
curl -sf "$SITE_URL/api/assets" -o "$WEB/data/assets.json"
curl -sf "$SITE_URL/api/map" -o "$WEB/data/map.json"
for verdict in overpriced suspicious_cheap fair; do
    curl -sf "$SITE_URL/api/scan?verdict=$verdict&limit=60" -o "$WEB/data/scan-$verdict.json"
done

echo "==> Compressing map.json (coordinates to ~1 m, only client-needed fields)..."
python3 - "$WEB/data/map.json" <<'PYEOF'
import json, sys

path = sys.argv[1]
data = json.load(open(path))
for feature in data["features"]:
    geometry = feature["geometry"]
    geometry["coordinates"] = [
        [[round(x, 5), round(y, 5)] for x, y in ring]
        for ring in geometry["coordinates"]
    ]
    props = feature["properties"]
    feature["properties"] = {
        "ppm2": round(props["ppm2"]) if props.get("ppm2") is not None else None,
        "price_median": round(props["price_median"]) if props.get("price_median") is not None else None,
        "n": props.get("n"),
    }
json.dump(data, open(path, "w"), ensure_ascii=False, separators=(",", ":"))
PYEOF

echo "==> Done:"
du -sh "$WEB"
echo "Deploy:  cd web && vercel deploy --prod"
