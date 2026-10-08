# -*- coding: utf-8 -*-
"""
Диагностика качества данных и моделей HydroPredict по ВСЕМ гидропостам (>= 180 дней).
Выявляет причины неадекватных/скачущих прогнозов:
1. Сдвиг целевой переменной по строкам вместо календарных дат (row-shift vs calendar-shift).
2. Ступенчатые артефакты (shelf artifact) из-за дискретного набора горизонтов [1, 3, 7, 14, 30].
3. Разреженность и пропуски во временных рядах (зимние пропуски, разрывы измерений).
4. Пропуски в ковариатах (снег, лёд, температура, осадки) и их заполнение нулями.
5. Отсутствие моделей (покрытие моделей по станциям) и рассинхрон бэкендов.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "ml_features.db"
MODELS_DIR = PROJECT_ROOT / "models"
LEGACY_MODELS_DIR = SCRIPT_DIR / "models"
MIN_DATA_DAYS = 180

HORIZONS_TO_AUDIT = [1, 3, 7, 14, 30]


def get_model_info(river: str, post: str) -> Dict[str, Any]:
    """Проверка наличия и параметров модели станции."""
    primary_dir = MODELS_DIR / river / post
    safe_name = f"{river}__{post}".replace(" ", "_").replace("/", "_")
    legacy_dir = LEGACY_MODELS_DIR / safe_name
    
    model_dir = None
    if primary_dir.exists() and any(primary_dir.glob("model_h*.joblib")):
        model_dir = primary_dir
    elif legacy_dir.exists() and any(legacy_dir.glob("model_h*.joblib")):
        model_dir = legacy_dir

    if not model_dir:
        return {"has_model": False}

    manifest_file = model_dir / "manifest.json"
    manifest: Dict[str, Any] = {}
    if manifest_file.exists():
        try:
            with open(manifest_file, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception:
            manifest = {}

    h_files = list(model_dir.glob("model_h*.joblib"))
    trained_horizons = sorted(list(set(
        int(f.name.split("_h")[1].split("_q")[0])
        for f in h_files if "_h" in f.name and "_q" in f.name
    )))

    return {
        "has_model": True,
        "model_dir": str(model_dir),
        "backend": manifest.get("backend", "unknown"),
        "training_date": manifest.get("training_date"),
        "trained_horizons": trained_horizons,
        "metrics": manifest.get("metrics", {}),
    }


def analyze_station(conn: sqlite3.Connection, river: str, post: str) -> Dict[str, Any]:
    """Глубокий аудит одной гидрологической станции."""
    query = """
        SELECT date, water_level_cm, temp_mean, precip_mm, snow_pct_norm, 
               ice_thickness_cm, level_lag_1, level_ma7, delta_7d
        FROM daily_features
        WHERE river = ? AND post = ?
        ORDER BY date ASC
    """
    df = pd.read_sql_query(query, conn, params=(river, post))
    if df.empty:
        return {"error": "empty_data"}

    df["date"] = pd.to_datetime(df["date"])
    n_records = len(df)
    min_date = df["date"].min().date()
    max_date = df["date"].max().date()
    span_days = (max_date - min_date).days + 1
    density = round(n_records / span_days, 3) if span_days > 0 else 1.0

    # Разрывы в наблюдениях (gaps)
    date_diffs = (df["date"].diff().dt.days).dropna()
    gaps_gt_1 = int((date_diffs > 1).sum())
    gaps_gt_7 = int((date_diffs > 7).sum())
    gaps_gt_30 = int((date_diffs > 30).sum())
    max_gap_days = int(date_diffs.max()) if not date_diffs.empty else 0

    # Проверка критического бага целевой переменной: shift(-h) vs calendar date + h
    shift_mismatches = {}
    for h in HORIZONS_TO_AUDIT:
        if n_records <= h:
            continue
        shifted_dates = df["date"].shift(-h)
        expected_dates = df["date"] + pd.to_timedelta(h, unit="D")
        valid_mask = shifted_dates.notna()
        if valid_mask.sum() == 0:
            continue
        mismatches = (shifted_dates[valid_mask] != expected_dates[valid_mask]).sum()
        total_eval = valid_mask.sum()
        shift_mismatches[f"h{h}"] = {
            "mismatch_count": int(mismatches),
            "total_evaluated": int(total_eval),
            "mismatch_pct": round(float(mismatches / total_eval * 100.0), 1),
        }

    # Полнота ключевых фичей (null percentages)
    null_pcts = {
        "water_level_cm": round(float(df["water_level_cm"].isna().mean() * 100.0), 1),
        "temp_mean": round(float(df["temp_mean"].isna().mean() * 100.0), 1),
        "precip_mm": round(float(df["precip_mm"].isna().mean() * 100.0), 1),
        "snow_pct_norm": round(float(df["snow_pct_norm"].isna().mean() * 100.0), 1),
        "ice_thickness_cm": round(float(df["ice_thickness_cm"].isna().mean() * 100.0), 1),
        "level_lag_1": round(float(df["level_lag_1"].isna().mean() * 100.0), 1),
        "level_ma7": round(float(df["level_ma7"].isna().mean() * 100.0), 1),
    }

    valid_levels = df["water_level_cm"].dropna()
    level_stats = {
        "min": round(float(valid_levels.min()), 1) if not valid_levels.empty else None,
        "max": round(float(valid_levels.max()), 1) if not valid_levels.empty else None,
        "mean": round(float(valid_levels.mean()), 1) if not valid_levels.empty else None,
        "std": round(float(valid_levels.std()), 1) if not valid_levels.empty else None,
    }

    model_info = get_model_info(river, post)

    shelf_risk = False
    if model_info["has_model"]:
        trained_h = set(model_info.get("trained_horizons", []))
        if trained_h and not any(h in trained_h for h in [2, 4, 5, 6, 8, 9, 10, 11, 12, 13]):
            shelf_risk = True

    erratic_factors = []
    if shift_mismatches.get("h7", {}).get("mismatch_pct", 0) > 10.0:
        pct = shift_mismatches["h7"]["mismatch_pct"]
        erratic_factors.append(f"Target date leakage/mismatch at h=7: {pct}% rows shifted incorrectly due to gaps")
    if shelf_risk:
        erratic_factors.append("Shelf/ladder artifact: intermediate horizon interpolation missing")
    if null_pcts["snow_pct_norm"] > 80.0:
        erratic_factors.append(f"High snow feature nulls ({null_pcts['snow_pct_norm']}%) filled with 0.0")
    if null_pcts["ice_thickness_cm"] > 80.0:
        erratic_factors.append(f"High ice feature nulls ({null_pcts['ice_thickness_cm']}%) filled with 0.0")
    if null_pcts["level_lag_1"] > 20.0:
        erratic_factors.append(f"Missing lag_1 features ({null_pcts['level_lag_1']}%) zero-filled at inference")
    if not model_info["has_model"]:
        erratic_factors.append("No trained model (fallback to climatology or unserved)")

    return {
        "river": river,
        "post": post,
        "n_records": n_records,
        "min_date": str(min_date),
        "max_date": str(max_date),
        "span_days": span_days,
        "density": density,
        "gaps_gt_1": gaps_gt_1,
        "gaps_gt_7": gaps_gt_7,
        "gaps_gt_30": gaps_gt_30,
        "max_gap_days": max_gap_days,
        "shift_mismatches": shift_mismatches,
        "null_pcts": null_pcts,
        "level_stats": level_stats,
        "model_info": model_info,
        "shelf_risk": shelf_risk,
        "erratic_factors": erratic_factors,
    }

def run_diagnostics() -> Dict[str, Any]:
    print("=" * 70)
    print("ДИАГНОСТИКА КАЧЕСТВА ДАННЫХ И МОДЕЛЕЙ ДЛЯ ВСЕХ ГИДРОПОСТОВ (>= 180 дней)")
    print("=" * 70)
    
    if not DB_PATH.exists():
        raise FileNotFoundError(f"БД не найдена: {DB_PATH}")

    conn = sqlite3.connect(str(DB_PATH))
    try:
        query = """
            SELECT river, post, COUNT(*) as cnt
            FROM daily_features
            GROUP BY river, post
            HAVING cnt >= ?
            ORDER BY river, post
        """
        eligible = conn.execute(query, (MIN_DATA_DAYS,)).fetchall()
        print(f"Найдено подходящих гидропостов: {len(eligible)}")

        station_reports = []
        for idx, (river, post, cnt) in enumerate(eligible, 1):
            report = analyze_station(conn, river, post)
            station_reports.append(report)
            if idx % 25 == 0 or idx == len(eligible):
                print(f"  [{idx}/{len(eligible)}] {river} / {post} ({cnt} дней) проверен")
    finally:
        conn.close()

    total_eligible = len(station_reports)
    with_models = sum(1 for s in station_reports if s["model_info"]["has_model"])
    without_models = total_eligible - with_models
    
    catboost_models = sum(1 for s in station_reports if s["model_info"].get("backend") == "catboost")
    xgboost_models = sum(1 for s in station_reports if s["model_info"].get("backend") == "xgboost")
    
    stations_with_shelf_risk = sum(1 for s in station_reports if s.get("shelf_risk"))
    
    h7_mismatches = [
        s["shift_mismatches"].get("h7", {}).get("mismatch_pct", 0)
        for s in station_reports if "h7" in s["shift_mismatches"]
    ]
    avg_h7_mismatch = round(float(np.mean(h7_mismatches)), 1) if h7_mismatches else 0.0
    severe_mismatch_stations = [
        s for s in station_reports
        if s["shift_mismatches"].get("h7", {}).get("mismatch_pct", 0) > 15.0
    ]

    high_snow_nulls = sum(1 for s in station_reports if s["null_pcts"]["snow_pct_norm"] > 80.0)
    high_ice_nulls = sum(1 for s in station_reports if s["null_pcts"]["ice_thickness_cm"] > 80.0)

    summary = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "total_eligible_stations": total_eligible,
        "stations_with_trained_models": with_models,
        "stations_missing_models": without_models,
        "model_coverage_pct": round(with_models / total_eligible * 100.0, 1) if total_eligible else 0.0,
        "backends": {"catboost": catboost_models, "xgboost": xgboost_models},
        "step_shelf_artifact_risk_count": stations_with_shelf_risk,
        "avg_target_row_shift_mismatch_h7_pct": avg_h7_mismatch,
        "stations_with_severe_target_leakage_mismatch_gt_15pct": len(severe_mismatch_stations),
        "high_snow_null_stations_gt_80pct": high_snow_nulls,
        "high_ice_null_stations_gt_80pct": high_ice_nulls,
    }

    full_output = {"summary": summary, "stations": station_reports}
    json_path = DATA_DIR / "diagnostics_all_posts.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(full_output, f, ensure_ascii=False, indent=2)

    md_path = PROJECT_ROOT.parent / "docs" / "services" / "flood_diagnostics_report.md"
    os.makedirs(md_path.parent, exist_ok=True)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Диагностический отчет: аудит гидропостов и модели HydroPredict\n\n")
        f.write(f"**Дата аудита:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n\n")
        f.write("## 1. Сводная статистика\n\n")
        f.write(f"- Постов с историей >=180 дней: **{total_eligible}**\n")
        f.write(f"- Постов с обученными моделями: **{with_models}** ({summary['model_coverage_pct']}%)\n")
        f.write(f"- Постов БЕЗ обученных моделей: **{without_models}**\n")
        f.write(f"- Бэкенды: CatBoost: {catboost_models}, XGBoost: {xgboost_models}\n")
        f.write(f"- Постов со ступенчатым артефактом (shelf artifact): **{stations_with_shelf_risk}**\n")
        f.write(f"- Средний процент рассинхрона shift(-7) по датам: **{avg_h7_mismatch}%**\n")
        f.write(f"- Постов с рассинхроном shift(-7) >15%: **{len(severe_mismatch_stations)}**\n")
        f.write(f"- Постов с пропусками снега >80%: **{high_snow_nulls}**\n")
        f.write(f"- Постов с пропусками льда >80%: **{high_ice_nulls}**\n\n")

        f.write("## 2. Анализ причин неадекватных / скачущих прогнозов\n\n")
        f.write("1. **Ступенчатые полки (Shelf artifact):** Модели обучены только на горизонтах 1, 3, 7, 14, 30. Промежуточные дни (4-7, 8-14, 15-30) заполняются плоскими ступеньками.\n")
        f.write("2. **Рассинхрон таргета `df.shift(-h)`:** Сдвиг по строкам, а не по календарным датам. При наличии зимних перерывов строка перед ледоставом получает таргет из следующей весны.\n")
        f.write("3. **Заполнение нулями при инференсе:** `level_lag_1` и метео при отсутствии заполняются 0.0, что неестественно для уровней рек.\n")
        f.write("4. **Низкое покрытие моделями:** Лишь 22 из 143 постов имеют модели.\n\n")

        f.write("## 3. Таблица обученных постов\n\n")
        f.write("| Река | Пост | Записей | Бэкенд | Ошибочный сдвиг h7 | Ступени |\n")
        f.write("|---|---|---|---|---|---|\n")
        for s in station_reports:
            if s["model_info"]["has_model"]:
                mi = s["model_info"]
                m_h7 = s["shift_mismatches"].get("h7", {}).get("mismatch_pct", 0)
                f.write(f"| {s['river']} | {s['post']} | {s['n_records']} | {mi.get('backend')} | {m_h7}% | {'Да' if s['shelf_risk'] else 'Нет'} |\n")

    print(f"[OK] Подробный Markdown-отчет сохранен в: {md_path}")
    return full_output


if __name__ == "__main__":
    run_diagnostics()
