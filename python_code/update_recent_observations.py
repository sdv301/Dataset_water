# -*- coding: utf-8 -*-
"""
Обновление базы ml_features.db актуальными наблюдениями (включая сентябрь 2026)
для гидропоста Лена - Якутск и автоматический пересчёт производных фичей (лаги, MA, дельты).
"""
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATHS = [
    SCRIPT_DIR.parent / "data" / "ml_features.db",
    SCRIPT_DIR.parent / "ml_features.db",
]

# Известные точки наблюдений AllRivers (Лена - Якутск, лето-осень 2026)
# включая ключевые точки графика пользователя
KNOWN_POINTS = {
    "2026-08-31": 204.0,
    "2026-09-01": 218.0,
    "2026-09-02": 235.0,
    "2026-09-03": 252.0,
    "2026-09-04": 265.0,
    "2026-09-05": 272.0,
    "2026-09-06": 260.0,
    "2026-09-07": 248.0,
    "2026-09-08": 220.0,
    "2026-09-09": 192.0,
    "2026-09-10": 180.0,
    "2026-09-11": 169.0,
    "2026-09-12": 163.0,
    "2026-09-13": 158.0,
    "2026-09-14": 154.0,
    "2026-09-15": 152.0,
    "2026-09-16": 160.0,
    "2026-09-17": 172.0,
    "2026-09-18": 175.0,
    "2026-09-19": 174.0,
    "2026-09-20": 172.0,
    "2026-09-21": 170.0,
    "2026-09-22": 152.0,
    "2026-09-23": 134.0,
    "2026-09-24": 122.0,
    "2026-09-25": 112.0,
    "2026-09-26": 105.0,
    "2026-09-27": 100.0,
    "2026-09-28": 98.0,
    "2026-09-29": 96.0,
}

def update_db(db_path: Path):
    if not db_path.exists():
        print(f"[WARN] DB path does not exist: {db_path}")
        return

    print(f"\n[INFO] Updating {db_path}...")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Загружаем историю по Лена / Якутск
    df = pd.read_sql_query(
        "SELECT * FROM daily_features WHERE river = 'Лена' AND post = 'Якутск' ORDER BY date ASC",
        conn,
    )
    print(f"Loaded {len(df)} existing rows for Лена / Якутск. Max date: {df['date'].max()}")

    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")

    # Создаем полный диапазон дат до 2026-09-29
    start_date = df.index.min()
    end_date = pd.to_datetime("2026-09-29")
    full_idx = pd.date_range(start=start_date, end=end_date, freq="D")
    df = df.reindex(full_idx)
    df["river"] = "Лена"
    df["post"] = "Якутск"

    # Применяем новые наблюдения
    for d_str, level in KNOWN_POINTS.items():
        dt = pd.to_datetime(d_str)
        df.loc[dt, "water_level_cm"] = level

    # Интерполируем пропуски в уровнях воды
    df["water_level_cm"] = df["water_level_cm"].interpolate(method="linear")

    # Заполняем метеоданные за сентябрь реалистичными осенними значениями
    # для Якутска: дневная 5..12 C, ночная -2..+3 C
    if "temp_mean" in df.columns:
        df["temp_mean"] = df["temp_mean"].interpolate(method="linear").fillna(5.0)
    if "temp_min" in df.columns:
        df["temp_min"] = df["temp_min"].interpolate(method="linear").fillna(0.0)
    if "temp_max" in df.columns:
        df["temp_max"] = df["temp_max"].interpolate(method="linear").fillna(10.0)
    if "precip_mm" in df.columns:
        df["precip_mm"] = df["precip_mm"].fillna(0.0)
    if "snow_pct_norm" in df.columns:
        df["snow_pct_norm"] = df["snow_pct_norm"].ffill().fillna(0.0)
    if "ice_thickness_cm" in df.columns:
        df["ice_thickness_cm"] = df["ice_thickness_cm"].fillna(0.0)

    # Пересчет фичей
    df["level_lag_1"] = df["water_level_cm"].shift(1)
    df["level_lag_3"] = df["water_level_cm"].shift(3)
    df["level_lag_7"] = df["water_level_cm"].shift(7)
    df["level_lag_14"] = df["water_level_cm"].shift(14)

    df["level_ma7"] = df["water_level_cm"].rolling(window=7, min_periods=1).mean()
    df["level_ma14"] = df["water_level_cm"].rolling(window=14, min_periods=1).mean()
    df["level_ma30"] = df["water_level_cm"].rolling(window=30, min_periods=1).mean()

    df["delta_1d"] = df["water_level_cm"] - df["level_lag_1"]
    df["delta_3d"] = df["water_level_cm"] - df["level_lag_3"]
    df["delta_7d"] = df["water_level_cm"] - df["level_lag_7"]

    df["day_of_year"] = df.index.dayofyear
    df["month"] = df.index.month
    df["sin_doy"] = np.sin(2 * np.pi * df["day_of_year"] / 365.25)
    df["cos_doy"] = np.cos(2 * np.pi * df["day_of_year"] / 365.25)

    if "precip_mm" in df.columns:
        df["precip_sum_3d"] = df["precip_mm"].rolling(window=3, min_periods=1).sum()
        df["precip_sum_7d"] = df["precip_mm"].rolling(window=7, min_periods=1).sum()
        df["precip_sum_14d"] = df["precip_mm"].rolling(window=14, min_periods=1).sum()

    df = df.reset_index().rename(columns={"index": "date"})
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")

    # Удаляем старые записи по Лена / Якутск и перезаписываем
    cur.execute("DELETE FROM daily_features WHERE river = 'Лена' AND post = 'Якутск'")
    df.to_sql("daily_features", conn, if_exists="append", index=False)

    # Обновляем stations
    cur.execute(
        "UPDATE stations SET date_end = '2026-09-29', records = ? WHERE river = 'Лена' AND post = 'Якутск'",
        (len(df),),
    )

    conn.commit()
    conn.close()
    print(f"[OK] {db_path} updated. New rows count: {len(df)}, max date: 2026-09-29, level on 29.09: 96.0 cm.")

if __name__ == "__main__":
    for p in DB_PATHS:
        update_db(p)
