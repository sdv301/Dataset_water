# -*- coding: utf-8 -*-
"""
TimesFM Challenger: оффлайн-челленджер на базе Foundation Time Series Models.

Модуль предназначен для оффлайн-сравнения с CatBoost и наивными бейзлайнами:
1. Принимает одномерный исторический ряд уровня воды станции (контекст до 512 дней).
2. Выполняет прогноз на горизонты 1..30 дней.
3. Оценивает квантили [0.1, 0.5, 0.9, 0.95].
4. Не имеет утечек из будущего (строго контекст <= base_date).
5. При отсутствии пакета timesfm предоставляет робастный вероятностный бейзлайн.
"""

from __future__ import annotations

import datetime
import math
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "ml_features.db"


class TimesFMChallenger:
    """Оффлайн-предиктор на базе TimesFM / Time Series Foundation Models."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        context_len: int = 512,
        model_name: str = "google/timesfm-1.0-200m",
    ):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.context_len = context_len
        self.model_name = model_name
        self._model = None
        self._is_native_timesfm = False
        self._init_backend()

    def _init_backend(self) -> None:
        try:
            import timesfm  # type: ignore[import-untyped]
            tfm = timesfm.TimesFm(
                context_len=self.context_len,
                horizon_len=32,
                input_patch_len=32,
                output_patch_len=128,
                num_layers=20,
                model_dims=1280,
                backend="cpu",
            )
            self._model = tfm
            self._is_native_timesfm = True
        except Exception:
            self._model = None
            self._is_native_timesfm = False

    def load_series(
        self,
        river: str,
        post: str,
        up_to_date: Optional[datetime.date] = None,
    ) -> pd.Series:
        if not self.db_path.exists():
            raise FileNotFoundError(f"БД не найдена: {self.db_path}")

        conn = sqlite3.connect(str(self.db_path))
        try:
            if up_to_date is not None:
                query = """
                    SELECT date, water_level_cm
                    FROM daily_features
                    WHERE river = ? AND post = ? AND date <= ? AND water_level_cm IS NOT NULL
                    ORDER BY date ASC
                """
                df = pd.read_sql_query(
                    query, conn, params=(river, post, up_to_date.isoformat())
                )
            else:
                query = """
                    SELECT date, water_level_cm
                    FROM daily_features
                    WHERE river = ? AND post = ? AND water_level_cm IS NOT NULL
                    ORDER BY date ASC
                """
                df = pd.read_sql_query(query, conn, params=(river, post))
        finally:
            conn.close()

        if df.empty:
            return pd.Series(dtype=float)

        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")["water_level_cm"].sort_index()
        df = df[~df.index.duplicated(keep="last")]
        return df


    def forecast(
        self,
        river: str,
        post: str,
        base_date: datetime.date,
        horizons: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        horizons = horizons or [1, 3, 7, 14, 30]
        series = self.load_series(river, post, up_to_date=base_date)
        if series.empty or len(series) < 14:
            return []

        recent = series.iloc[-self.context_len:].values.astype(float)
        last_val = float(recent[-1])
        max_h = max(horizons)

        if self._is_native_timesfm and self._model is not None:
            try:
                inp = np.expand_dims(recent, axis=0)
                point_forecast, _ = self._model.forecast(inp, [0])
                forecast_vals = point_forecast[0][:max_h]
                results = []
                for h in horizons:
                    idx = min(h - 1, len(forecast_vals) - 1)
                    med = float(forecast_vals[idx])
                    target_d = base_date + datetime.timedelta(days=h)
                    results.append({
                        "date": target_d.isoformat(),
                        "horizon": h,
                        "median": round(med, 2),
                        "q10": round(med - 1.28 * 15.0, 2),
                        "q90": round(med + 1.28 * 15.0, 2),
                        "q95": round(med + 1.64 * 18.0, 2),
                        "source": "timesfm_native",
                    })
                return results
            except Exception:
                pass

        trend_7d = (recent[-1] - recent[-min(7, len(recent))]) / min(7, len(recent))
        trend_damped = float(np.clip(trend_7d, -30.0, 30.0))
        ma30 = float(np.mean(recent[-min(30, len(recent)):]))
        std_resid = float(np.std(np.diff(recent[-min(60, len(recent)):]) if len(recent) > 1 else [10.0]))
        std_resid = max(std_resid, 8.0)

        results = []
        for h in horizons:
            target_d = base_date + datetime.timedelta(days=h)
            decay = math.exp(-0.08 * h)
            mean_revert_weight = 1.0 - decay
            pred_level = (last_val + trend_damped * (1.0 - decay) * 5.0) * decay + ma30 * mean_revert_weight
            h_std = std_resid * math.sqrt(h)
            q10 = pred_level - 1.28 * h_std
            q90 = pred_level + 1.28 * h_std
            q95 = pred_level + 1.64 * h_std

            results.append({
                "date": target_d.isoformat(),
                "horizon": h,
                "median": round(pred_level, 2),
                "q10": round(q10, 2),
                "q90": round(q90, 2),
                "q95": round(q95, 2),
                "source": "timesfm_challenger",
                "is_native": self._is_native_timesfm,
            })

        return results

        df = df.set_index("date")["water_level_cm"].sort_index()
        df = df[~df.index.duplicated(keep="last")]
        return df
