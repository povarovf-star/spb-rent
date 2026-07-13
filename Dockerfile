FROM python:3.12-slim

WORKDIR /app

# dependencies as a separate layer, cached between builds
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# code, models and prepared data (raw data does not go into the image)
COPY src/ src/
COPY configs/ configs/
COPY models/ models/
COPY data/processed/app_assets.json data/processed/
COPY data/processed/map_hex.parquet data/processed/
COPY data/processed/market_scan.parquet data/processed/

EXPOSE 8000 8501

# default is the API; the dashboard overrides the command in compose
CMD ["python", "-m", "uvicorn", "src.app.api:app", "--host", "0.0.0.0", "--port", "8000"]
