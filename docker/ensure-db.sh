#!/bin/sh
# Build ml_features.db on first start when data volume is empty.
set -e
DB="/app/data/ml_features.db"
EXPORT="/app/Реки/данные январь/export/levels_wide_report.csv"
PREPARE="/app/python_code/prepare_ml_data.py"

if [ -f "$DB" ]; then
  echo "[ensure-db] OK: $DB exists ($(du -h "$DB" | cut -f1))"
  exit 0
fi

mkdir -p /app/data

if [ ! -f "$EXPORT" ]; then
  echo "[ensure-db] WARN: $DB missing and export CSV not found at:"
  echo "[ensure-db]   $EXPORT"
  echo "[ensure-db] Mount host dir: flood_app/Dataset_water/Реки -> /app/Реки"
  echo "[ensure-db] Or run: python python_code/prepare_ml_data.py"
  exit 0
fi

if [ ! -f "$PREPARE" ]; then
  echo "[ensure-db] ERROR: $PREPARE not found in image"
  exit 1
fi

echo "[ensure-db] Building $DB from export (first start, may take a few minutes)..."
cd /app
python python_code/prepare_ml_data.py
if [ -f "$DB" ]; then
  echo "[ensure-db] Done: $DB created ($(du -h "$DB" | cut -f1))"
else
  echo "[ensure-db] ERROR: prepare_ml_data.py finished but $DB not found"
  exit 1
fi
