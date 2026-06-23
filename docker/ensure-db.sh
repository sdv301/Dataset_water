#!/bin/sh
# Build ml_features.db on first start when data volume is empty.
set -e
DB="/app/data/ml_features.db"
EXPORT="/app/Реки/данные январь/export/levels_wide_report.csv"

if [ -f "$DB" ]; then
  echo "[ensure-db] OK: $DB exists"
  exit 0
fi

mkdir -p /app/data

if [ ! -f "$EXPORT" ]; then
  echo "[ensure-db] WARN: $DB missing and export CSV not found at $EXPORT"
  echo "[ensure-db] Run on host: cd flood_app/Dataset_water && python python_code/prepare_ml_data.py"
  exit 0
fi

echo "[ensure-db] Building $DB from export data (first start, may take a few minutes)..."
python python_code/prepare_ml_data.py
if [ -f "$DB" ]; then
  echo "[ensure-db] Done: $DB created"
else
  echo "[ensure-db] ERROR: prepare_ml_data.py finished but $DB not found"
  exit 1
fi
