FROM python:3.12-slim

WORKDIR /app

# зависимости отдельным слоем — кэшируются между сборками
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# код, модели и подготовленные данные (сырые данные в образ не попадают)
COPY src/ src/
COPY configs/ configs/
COPY models/ models/
COPY data/processed/app_assets.json data/processed/
COPY data/processed/map_hex.parquet data/processed/
COPY data/processed/market_scan.parquet data/processed/

EXPOSE 8000 8501

# по умолчанию — API; dashboard переопределяет команду в compose
CMD ["python", "-m", "uvicorn", "src.app.api:app", "--host", "0.0.0.0", "--port", "8000"]
