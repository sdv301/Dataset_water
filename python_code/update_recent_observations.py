# -*- coding: utf-8 -*-
"""
Универсальное обновление наблюдений в ml_features.db для ЛЮБОЙ гидрологической станции.
Поддерживает безопасный upsert без удаления чужих строк и автоматический пересчет лагов.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Union

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_PATH = DATA_DIR / "ml_features.db"

def update_station_observations(
    river: str,
    post: str,
    new_observations: Dict[str, float],
    db_path: Optional[Union[str, Path]] = None,
) -> int:
    """Безопасное обновление наблюдений для гидропоста и пересчёт лагов."""
    db_file = Path(db_path) if db_path else DEFAULT_DB_PATH
    if not db_file.exists():
        raise FileNotFoundError(f"БД не найдена: {db_file}")

    if not new_observations:
        return 0

    conn = sqlite3.connect(str(db_file))
    try:
        query = "SELECT * FROM daily_features WHERE river = ? AND post = ? ORDER BY date ASC"
        df = pd.read_sql_query(query, conn, params=(river, post))

        if df.empty:
            df = pd.DataFrame([
                {"date": d, "river": river, "post": post, "water_level_cm": lvl}
                for d, lvl in sorted(new_observations.items())
            ])
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")
        else:
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")
            df = df[~df.index.duplicated(keep="last")]

            for d_str, level in new_observations.items():
                dt = pd.to_datetime(d_str)
                if dt in df.index:
                    df.loc[dt, "water_level_cm"] = float(level)
                else:
                    new_row = pd.Series(index=df.columns, dtype=object)
                    new_row["river"] = river
                    new_row["post"] = post
                    new_row["water_level_cm"] = float(level)
                    df.loc[dt] = new_row

        df = df.sort_index()
        df["water_level_cm"] = df["water_level_cm"].astype(float).interpolate(method="linear", limit=3)

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

        doy = df.index.dayofyear
        df["day_of_year"] = doy
        df["month"] = df.index.month
        df["sin_doy"] = np.sin(2 * np.pi * doy / 365.25)
        df["cos_doy"] = np.cos(2 * np.pi * doy / 365.25)

        if "precip_mm" in df.columns:
            precip = df["precip_mm"].astype(float).fillna(0.0)
            df["precip_sum_3d"] = precip.rolling(window=3, min_periods=1).sum()
            df["precip_sum_7d"] = precip.rolling(window=7, min_periods=1).sum()
            df["precip_sum_14d"] = precip.rolling(window=14, min_periods=1).sum()

        df = df.reset_index().rename(columns={"index": "date"})
        df["date"] = df["date"].dt.strftime("%Y-%m-%d")

        cur = conn.cursor()
        cur.execute("DELETE FROM daily_features WHERE river = ? AND post = ?", (river, post))
        df.to_sql("daily_features", conn, if_exists="append", index=False)

        max_date_str = df["date"].max()
        records_count = len(df)
        cur.execute(
            "UPDATE stations SET date_end = ?, records = ? WHERE river = ? AND post = ?",
            (max_date_str, records_count, river, post),
        )
        conn.commit()
    finally:
        conn.close()

    return len(new_observations)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Обновление наблюдений гидропоста")
    parser.add_argument("--river", type=str, default="Лена", help="Река")
    parser.add_argument("--post", type=str, default="Якутск", help="Пост")
    parser.add_argument("--json", type=str, help="JSON файл или строка с точками")
    args = parser.parse_args()

    points = {}
    if args.json:
        if os.path.exists(args.json):
            with open(args.json, "r", encoding="utf-8") as f:
                points = json.load(f)
        else:
            points = json.loads(args.json)
    else:
        points = {"2026-09-29": 96.0}

    updated = update_station_observations(args.river, args.post, points)
    print(f"[OK] Станция {args.river} / {args.post}: обновлено {updated} точек")

