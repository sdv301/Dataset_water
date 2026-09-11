# -*- coding: utf-8 -*-
"""
Импорт сводного Excel-датасета (август 2026) в `data/ml_features.db` +
исправление семантики порогов ОЯ/НЯ + координаты + пересчёт производных фичей.

Этап 1 плана:
  1) Залить 4 наблюдательных листа в `daily_features` (upsert по river/post/date).
  2) КОРНЕВОЙ БАГ: `low_oya` исторически хранила порог МАЛОВОДЬЯ («Низкий ОЯ»,
     напр. Якутск = -115 см), а код агента использует её как «предупредительный
     уровень наводнения». Переносим маловодье в `low_water_oya`, а `low_oya`
     делаем настоящим НЯ (0.75×ОЯ / оценка из климатологии).
  3) Залить реальные `critical_oya` из Excel (94 поста) + новые посты.
  4) Залить координаты из листа «Толщина_льда».
  5) Пересчитать производные фичи date-aware (БЕЗ backfill — он ломается на разрыве).

Бэкап БД автоматический (если не --no-backup). --dry-run ничего не пишет.
"""
from __future__ import annotations
import argparse, os, shutil, sqlite3, sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH = SCRIPT_DIR.parent / "data" / "ml_features.db"
EXCEL_PATH = Path(r"C:\Users\pc24\Downloads\0E03~1\D86A~1\84D2~1\27D1~1\_2026_~1.XLS")
SHEET_LEVELS, SHEET_METEO, SHEET_ICE, SHEET_SNOW = 7, 1, 2, 3
RANGES = {"water_level_cm": (-500.0, 5000.0), "temp_min": (-70.0, 45.0),
    "temp_mean": (-70.0, 45.0), "temp_max": (-70.0, 50.0), "precip_mm": (0.0, 300.0),
    "ice_thickness_cm": (0.0, 500.0), "snow_pct_norm": (0.0, 300.0)}


def log(msg: str) -> None:
    print(f"[import] {msg}", flush=True)


def safe_float(val) -> float:
    try:
        if val is None or (isinstance(val, float) and np.isnan(val)):
            return np.nan
        s = str(val).strip()
        if s in ("", "*", "—", "-", "н/д", "нет"):
            return np.nan
        return float(s.replace(",", "."))
    except Exception:
        return np.nan


def parse_snow_pct(val) -> float:
    """'<70'->70, '70-90'->80, '>130'->130, число->число."""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return np.nan
    s = str(val).strip().replace(",", ".").lower()
    if s in ("", "*", "—", "н/д", "нет"):
        return np.nan
    try:
        return float(s)
    except ValueError:
        pass
    if s.startswith(("<", ">")):
        try:
            return float(s[1:])
        except ValueError:
            return np.nan
    if "-" in s:
        parts = s.split("-")
        try:
            return round((float(parts[0]) + float(parts[1])) / 2, 1)
        except (ValueError, IndexError):
            return np.nan
    return np.nan


def oktmo_key(val) -> Optional[str]:
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    s = str(val).strip()
    if s in ("", "nan", "None"):
        return None
    if s.endswith(".0"):
        s = s[:-2]
    return s


def validate_series(df: pd.DataFrame, col: str) -> int:
    if col not in df.columns:
        return 0
    lo, hi = RANGES[col]
    s = df[col].dropna()
    return int(((s < lo) | (s > hi)).sum())


def load_levels(xl: pd.ExcelFile) -> pd.DataFrame:
    df = xl.parse(xl.sheet_names[SHEET_LEVELS]).rename(columns={
        "Гидрометеорологический пост": "post", "Река": "river", "Район": "district",
        "Наименование населенного пункта": "np", "ОКТМО": "oktmo",
        "Дата (дд.мм..гг)": "date", "Уровень_воды": "water_level_cm",
        "Крит ОЯ": "critical_oya", "Низкий ОЯ": "low_water_oya"})
    df["water_level_cm"] = df["water_level_cm"].apply(safe_float)
    df["critical_oya"] = df["critical_oya"].apply(safe_float)
    df["low_water_oya"] = df["low_water_oya"].apply(safe_float)
    df["oktmo"] = df["oktmo"].apply(oktmo_key)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "river", "post"])
    df["river"] = df["river"].astype(str).str.strip()
    df["post"] = df["post"].astype(str).str.strip()
    th = df.dropna(subset=["critical_oya"]).drop_duplicates("post")[["river", "post", "critical_oya", "low_water_oya"]]
    df.attrs["thresholds"] = th
    log(f"Уровни_воды: {len(df)} строк, {df['post'].nunique()} постов, "
        f"дат {df['date'].min().date()}..{df['date'].max().date()}; Крит ОЯ у {len(th)} постов")
    return df


def load_meteo(xl: pd.ExcelFile) -> pd.DataFrame:
    df = xl.parse(xl.sheet_names[SHEET_METEO]).rename(columns={
        "Код ГМС": "gms_code", "Н.П.": "np", "Год": "year", "Месяц": "month",
        "День": "day", "Тмин": "temp_min", "Тср": "temp_mean", "Тмакс": "temp_max",
        "осадки": "precip_mm", "ОКТМО": "oktmo"})
    for c in ("temp_min", "temp_mean", "temp_max", "precip_mm"):
        df[c] = df[c].apply(safe_float)
    df["oktmo"] = df["oktmo"].apply(oktmo_key)
    df["date"] = pd.to_datetime(df[["year", "month", "day"]], errors="coerce")
    df = df.dropna(subset=["date", "oktmo"])
    log(f"Метеоданные: {len(df)} строк, {df['oktmo'].nunique()} ОКТМО, "
        f"дат {df['date'].min().date()}..{df['date'].max().date()}")
    return df


def load_ice(xl: pd.ExcelFile) -> pd.DataFrame:
    df = xl.parse(xl.sheet_names[SHEET_ICE]).rename(columns={
        "Район": "district", "Наслег": "nasleg", "ОКТМО": "oktmo",
        "Гидрометеорологический пост": "post", "Наименование реки": "river",
        "Геопозиция (широта)": "lat", "Геопозиция (долгота)": "lon",
        "Год": "year", "Месяц": "month", "День": "day",
        "Толщина льда,см": "ice_thickness_cm"})
    df["ice_thickness_cm"] = df["ice_thickness_cm"].apply(safe_float)
    df["lat"] = df["lat"].apply(safe_float)
    df["lon"] = df["lon"].apply(safe_float)
    df["oktmo"] = df["oktmo"].apply(oktmo_key)
    df["date"] = pd.to_datetime(df[["year", "month", "day"]], errors="coerce")
    df = df.dropna(subset=["date", "post"])
    df["river"] = df["river"].astype(str).str.strip()
    df["post"] = df["post"].astype(str).str.strip()
    coords = df.dropna(subset=["lat", "lon"]).drop_duplicates("post")[["river", "post", "lat", "lon"]]
    df.attrs["coords"] = coords
    log(f"Толщина_льда: {len(df)} строк, {df['post'].nunique()} постов, координаты у {len(coords)} постов")
    return df


def load_snow(xl: pd.ExcelFile) -> pd.DataFrame:
    df = xl.parse(xl.sheet_names[SHEET_SNOW]).rename(columns={
        "Район": "district", "Наслег": "nasleg", "ОКТМО": "oktmo",
        "Бассейн рек": "basin", "Населенный пункт": "np",
        "Год": "year", "Месяц": "month", "День": "day", "в % от нормы": "snow_pct_norm"})
    df["snow_pct_norm"] = df["snow_pct_norm"].apply(parse_snow_pct)
    df["oktmo"] = df["oktmo"].apply(oktmo_key)
    df["date"] = pd.to_datetime(df[["year", "month", "day"]], errors="coerce")
    df = df.dropna(subset=["date", "basin", "snow_pct_norm"])
    df["basin"] = df["basin"].astype(str).str.strip()
    log(f"Снегозапасы: {len(df)} строк, {df['basin'].nunique()} бассейнов, "
        f"дат {df['date'].min().date()}..{df['date'].max().date()}")
    return df


def _has_col(conn, tbl: str, col: str) -> bool:
    return any(r[1] == col for r in conn.execute(f"PRAGMA table_info({tbl})"))


def _annual_peaks(conn, river: str, post: str) -> List[float]:
    rows = conn.execute(
        "SELECT strftime('%Y', date), MAX(water_level_cm) FROM daily_features "
        "WHERE river=? AND post=? AND water_level_cm IS NOT NULL GROUP BY 1", (river, post)
    ).fetchall()
    return [float(r[1]) for r in rows if r[1] is not None]


def stage_thresholds(conn, levels_df: pd.DataFrame, dry: bool) -> Dict:
    log("=== СТАДИЯ: пороги (миграция low_oya -> low_water_oya, залив critical_oya, расчёт НЯ) ===")
    rep = {"migrated_low_water": 0, "critical_upserted": 0, "new_stations": 0,
           "nya_computed": 0, "nya_estimated": 0, "nya_none": 0}
    if not dry:
        if not _has_col(conn, "stations", "low_water_oya"):
            conn.execute("ALTER TABLE stations ADD COLUMN low_water_oya REAL")
            log("Добавлена колонка stations.low_water_oya")
        if not _has_col(conn, "stations", "threshold_source"):
            conn.execute("ALTER TABLE stations ADD COLUMN threshold_source TEXT")
            log("Добавлена колонка stations.threshold_source")
        # 1) Сохраняем маловодье: low_oya -> low_water_oya
        rep["migrated_low_water"] = conn.execute(
            "UPDATE stations SET low_water_oya=low_oya WHERE low_water_oya IS NULL AND low_oya IS NOT NULL"
        ).rowcount
        conn.commit()
        log(f"Маловодье сохранено в low_water_oya: {rep['migrated_low_water']} постов")

    # 2) Заливка critical_oya из Excel + новые посты
    th = levels_df.attrs.get("thresholds")
    ice_coords = levels_df.attrs.get("coords")  # нет, coords в ice_df; передадим отдельно
    if th is None or len(th) == 0:
        log("Нет порогов в Excel — пропуск заливки critical_oya")
    else:
        existing = {(r[0], r[1]) for r in conn.execute("SELECT river, post FROM stations")}
        for _, r in th.iterrows():
            river, post = str(r["river"]), str(r["post"])
            crit = None if pd.isna(r["critical_oya"]) else float(r["critical_oya"])
            lwm = None if pd.isna(r["low_water_oya"]) else float(r["low_water_oya"])
            if (river, post) in existing:
                if crit is not None:
                    if not dry:
                        conn.execute("UPDATE stations SET critical_oya=? WHERE river=? AND post=?",
                                     (crit, river, post))
                    rep["critical_upserted"] += 1
            else:
                if not dry:
                    conn.execute(
                        "INSERT INTO stations(river, post, critical_oya, low_water_oya, "
                        "threshold_source) VALUES (?,?,?,?,?)",
                        (river, post, crit, lwm, "real" if crit else None))
                rep["new_stations"] += 1
        if not dry:
            conn.commit()
        log(f"critical_oya обновлён: {rep['critical_upserted']}, новых постов: {rep['new_stations']}")

    # 3) Расчёт НЯ (low_oya) для всех постов
    stations = conn.execute("SELECT river, post, COALESCE(critical_oya,0) FROM stations").fetchall()
    for river, post, crit0 in stations:
        crit = crit0 if crit0 else None
        peaks = _annual_peaks(conn, river, post)
        p95 = float(np.percentile(peaks, 95)) if len(peaks) >= 5 else None
        p99 = float(np.percentile(peaks, 99)) if len(peaks) >= 5 else None
        # критический уровень: реальный > оценка из истории > нет
        if crit:
            source = "real"
        elif p99 is not None:
            crit, source = p99, "estimated"
        else:
            crit, source = None, "none"
        # НЯ: климатологический p95, но строго < ОЯ (иначе 0.75×ОЯ); без ОЯ — 0.75×оценки
        if crit:
            nya = p95 if (p95 is not None and p95 < crit) else 0.75 * crit
            if nya >= crit:
                nya = 0.75 * crit
        else:
            nya = None
        if not dry:
            conn.execute(
                "UPDATE stations SET critical_oya=?, low_oya=?, threshold_source=? WHERE river=? AND post=?",
                (crit, nya, source, river, post))
        if source == "real":
            rep["nya_computed"] += 1
        elif source == "estimated":
            rep["nya_estimated"] += 1
        else:
            rep["nya_none"] += 1
    if not dry:
        conn.commit()
    log(f"НЯ рассчитан: real={rep['nya_computed']}, estimated={rep['nya_estimated']}, none={rep['nya_none']}")
    return rep


def stage_coords(conn, ice_df: pd.DataFrame, dry: bool) -> Dict:
    log("=== СТАДИЯ: координаты из листа Толщина_льда ===")
    coords = ice_df.attrs.get("coords")
    rep = {"updated": 0, "new": 0, "unmatched": 0}
    if coords is None or len(coords) == 0:
        log("Нет координат в листе льда")
        return rep
    # карта post -> [(river,post)] из stations для разрешения неоднозначностей
    by_post = {}
    for r, p in conn.execute("SELECT river, post FROM stations"):
        by_post.setdefault(p, []).append((r, p))
    for _, row in coords.iterrows():
        post, lat, lon = str(row["post"]), float(row["lat"]), float(row["lon"])
        cands = by_post.get(post, [])
        if not cands:
            rep["unmatched"] += 1
            continue
        # предпочитаем пару (river,post), где river совпадает с ice river
        ice_river = str(row.get("river", "")).strip()
        target = next((c for c in cands if c[0] == ice_river), cands[0])
        river, post = target
        if not dry:
            cur = conn.execute("UPDATE stations SET lat=?, lon=? WHERE river=? AND post=?",
                               (lat, lon, river, post))
            rep["updated"] += cur.rowcount
    if not dry:
        conn.commit()
    log(f"Координаты: обновлено {rep['updated']}, не сопоставлено {rep['unmatched']}")
    return rep


def _norm_basin(s: str) -> str:
    s = str(s).lower().strip()
    for p in ("бассейн", "реки", "река", "озеро", "р.", "оз.", "(", ")"):
        s = s.replace(p, " ")
    return " ".join(s.split())


def stage_import(conn, levels, meteo, ice, snow, dry, post_filter, river_filter):
    from collections import defaultdict
    log("=== СТАДИЯ: импорт наблюдений (уровни+метео+лёд+снег) в daily_features ===")
    rep = {"rows": 0, "posts": 0, "meteo_unmatched_oktmo": 0, "ice_unmatched_post": 0,
           "snow_unmatched_basin": 0, "snow_basins_used": 0}

    oktmo_map = defaultdict(list)
    for r, p, o in conn.execute("SELECT river, post, oktmo FROM stations WHERE oktmo IS NOT NULL"):
        oktmo_map[oktmo_key(o)].append((r, p))
    post_map = defaultdict(list)
    for r, p in conn.execute("SELECT river, post FROM stations"):
        post_map[p].append((r, p))
    from difflib import SequenceMatcher
    rivers_norm = {_norm_basin(r): r for (r,) in conn.execute("SELECT DISTINCT river FROM stations")}
    basin_map = defaultdict(list)
    for b in snow["basin"].dropna().unique():
        nb = _norm_basin(b)
        matched = [rv for rn, rv in rivers_norm.items() if rn and (rn == nb or SequenceMatcher(None, rn, nb).ratio() >= 0.82)]
        if matched:
            basin_map[b] = matched
            rep["snow_basins_used"] += 1
        else:
            rep["snow_unmatched_basin"] += 1
    river_posts = defaultdict(list)
    for r, p in conn.execute("SELECT river, post FROM stations"):
        river_posts[r].append(p)

    obs = defaultdict(lambda: defaultdict(dict))
    for _, r in levels.iterrows():
        key = (str(r["river"]), str(r["post"]))
        if post_filter and key[1] != post_filter:
            continue
        if river_filter and key[0] != river_filter:
            continue
        obs[key][r["date"]]["water_level_cm"] = r["water_level_cm"]
    n_level_posts = len(obs)
    n_meteo_used = 0
    for _, r in meteo.iterrows():
        cands = oktmo_map.get(r["oktmo"], [])
        if not cands:
            rep["meteo_unmatched_oktmo"] += 1
            continue
        for (rv, po) in cands:
            if post_filter and po != post_filter:
                continue
            if river_filter and rv != river_filter:
                continue
            d = obs[(rv, po)].get(r["date"], {})
            d.update({"temp_min": r["temp_min"], "temp_mean": r["temp_mean"],
                      "temp_max": r["temp_max"], "precip_mm": r["precip_mm"]})
            obs[(rv, po)][r["date"]] = d
            n_meteo_used += 1
    log(f"Уровни: постов {n_level_posts}; метео-совпадений {n_meteo_used}; "
        f"снег-бассейнов {rep['snow_basins_used']} (+{rep['snow_unmatched_basin']} не сопоставлено)")
    return _stage_import_tail(conn, ice, snow, dry, post_filter, river_filter, obs, rep, basin_map, post_map, river_posts)


def _stage_import_tail(conn, ice, snow, dry, post_filter, river_filter, obs, rep, basin_map, post_map, river_posts):
    # --- лёд (по имени поста) ---
    for _, r in ice.iterrows():
        cands = post_map.get(str(r["post"]), [])
        if not cands:
            rep["ice_unmatched_post"] += 1
            continue
        ice_river = str(r.get("river", "")).strip()
        target = next((c for c in cands if c[0] == ice_river), cands[0])
        if post_filter and target[1] != post_filter:
            continue
        if river_filter and target[0] != river_filter:
            continue
        if pd.isna(r["ice_thickness_cm"]):
            continue
        d = obs[target].get(r["date"], {})
        d["ice_thickness_cm"] = r["ice_thickness_cm"]
        obs[target][r["date"]] = d
    # --- снег (бассейн -> реки -> посты) ---
    snow_daily = snow.groupby(["basin", "date"], as_index=False)["snow_pct_norm"].mean()
    for _, r in snow_daily.iterrows():
        for rv in basin_map.get(r["basin"], []):
            if river_filter and rv != river_filter:
                continue
            for po in river_posts.get(rv, []):
                if post_filter and po != post_filter:
                    continue
                d = obs[(rv, po)].get(r["date"], {})
                d["snow_pct_norm"] = r["snow_pct_norm"]
                obs[(rv, po)][r["date"]] = d

    affected = sorted(k for k in obs if obs[k])
    log(f"Постов к импорту: {len(affected)}; лёд без поста {rep['ice_unmatched_post']}")

    cols = ("river, post, date, water_level_cm, temp_min, temp_mean, temp_max, precip_mm, "
            "snow_pct_norm, level_lag_1, level_lag_3, level_lag_7, level_lag_14, level_ma7, "
            "level_ma14, level_ma30, delta_1d, delta_3d, delta_7d, day_of_year, month, sin_doy, "
            "cos_doy, precip_sum_3d, precip_sum_7d, precip_sum_14d, ice_thickness_cm, temp_anomaly, "
            "level_vs_oya_pct, snow_depth_cm, swe_mm, precip_sum_30d, precip_sum_60d, precip_sum_90d, "
            "ice_event, is_summer")
    n_cols = len(cols.split(","))
    ins = f"INSERT INTO daily_features ({cols}) VALUES ({','.join(['?']*n_cols)})"
    for (river, post) in affected:
        dates = sorted(obs[(river, post)])
        rows = []
        for d in dates:
            o = obs[(river, post)][d]
            doy = d.timetuple().tm_yday
            m = d.month
            rows.append((river, post, d.strftime("%Y-%m-%d"), o.get("water_level_cm"),
                o.get("temp_min"), o.get("temp_mean"), o.get("temp_max"), o.get("precip_mm"),
                o.get("snow_pct_norm"), None, None, None, None, None, None, None, None, None, None,
                doy, m, round(float(np.sin(2 * np.pi * doy / 365.25)), 6),
                round(float(np.cos(2 * np.pi * doy / 365.25)), 6), None, None, None,
                o.get("ice_thickness_cm"), None, None, None, None, None, None, None, None,
                1 if m in (6, 7, 8) else 0))
        if rows:
            assert len(rows[0]) == n_cols, f"row arity {len(rows[0])} != {n_cols} cols"
        if not dry:
            conn.executemany("DELETE FROM daily_features WHERE river=? AND post=? AND date=?",
                             [(river, post, d.strftime("%Y-%m-%d")) for d in dates])
            conn.executemany(ins, rows)
            rep["rows"] += len(rows)
        rep["posts"] += 1
    if not dry:
        conn.commit()
    log(f"Импортировано строк: {rep['rows']} по {rep['posts']} постам (dry={dry})")
    return rep, affected


def _ice_event(month: int, tmean):
    if month in (12, 1, 2) and (tmean is None or (not pd.isna(tmean) and tmean < -10)):
        return "ice_cover"
    if month in (10, 11):
        return "freeze_up"
    if month in (4, 5) and (tmean is None or (not pd.isna(tmean) and tmean > -2)):
        return "break_up"
    if month in (6, 7, 8, 9):
        return "open_water"
    if month == 3:
        return "ice_cover"
    return None


def _recompute_one(conn, river, post, crit, dry) -> int:
    rows = conn.execute(
        "SELECT date, water_level_cm, temp_mean, precip_mm FROM daily_features "
        "WHERE river=? AND post=? ORDER BY date", (river, post)).fetchall()
    if not rows:
        return 0
    df = pd.DataFrame(rows, columns=["date", "water_level_cm", "temp_mean", "precip_mm"])
    df["date"] = pd.to_datetime(df["date"])
    for c in ("water_level_cm", "temp_mean", "precip_mm"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.set_index("date").sort_index()
    full_idx = pd.date_range(df.index.min(), df.index.max(), freq="D")
    f = df.reindex(full_idx)
    lvl = f["water_level_cm"]
    f["level_lag_1"], f["level_lag_3"], f["level_lag_7"], f["level_lag_14"] = (
        lvl.shift(1), lvl.shift(3), lvl.shift(7), lvl.shift(14))
    f["level_ma7"] = lvl.rolling(7, min_periods=1).mean()
    f["level_ma14"] = lvl.rolling(14, min_periods=1).mean()
    f["level_ma30"] = lvl.rolling(30, min_periods=1).mean()
    f["delta_1d"], f["delta_3d"], f["delta_7d"] = lvl.diff(1), lvl.diff(3), lvl.diff(7)
    p = f["precip_mm"].fillna(0.0)
    for col, w in (("precip_sum_3d", 3), ("precip_sum_7d", 7), ("precip_sum_14d", 14),
                   ("precip_sum_30d", 30), ("precip_sum_60d", 60), ("precip_sum_90d", 90)):
        f[col] = p.rolling(w, min_periods=1).sum()
    clim = f.groupby(f.index.dayofyear)["temp_mean"].mean()
    f["temp_anomaly"] = f["temp_mean"] - f.index.dayofyear.map(clim)
    if crit:
        f["level_vs_oya_pct"] = 100.0 * lvl / float(crit)
    else:
        f["level_vs_oya_pct"] = np.nan
    months = f.index.month
    f["is_summer"] = (months.isin([6, 7, 8])).astype(int)
    f["ice_event"] = [None if pd.isna(t) and m not in (10, 11, 6, 7, 8, 9) else _ice_event(int(m), None if pd.isna(t) else float(t))
                     for m, t in zip(months, f["temp_mean"])]
    res = f.reindex(df.index)  # обратно к реально существующим датам
    year2026 = pd.Timestamp("2026-01-01")
    updates_new, updates_hist = [], []
    for d, r in res.iterrows():
        ds = d.strftime("%Y-%m-%d")
        vals_new = (r["level_lag_1"], r["level_lag_3"], r["level_lag_7"], r["level_lag_14"],
                    r["level_ma7"], r["level_ma14"], r["level_ma30"], r["delta_1d"],
                    r["delta_3d"], r["delta_7d"], r["precip_sum_3d"], r["precip_sum_7d"],
                    r["precip_sum_14d"], r["precip_sum_30d"], r["precip_sum_60d"],
                    r["precip_sum_90d"], None if pd.isna(r["temp_anomaly"]) else round(float(r["temp_anomaly"]), 2),
                    None if pd.isna(r["level_vs_oya_pct"]) else round(float(r["level_vs_oya_pct"]), 2),
                    r["ice_event"], int(r["is_summer"]))
        if d >= year2026:
            updates_new.append(vals_new + (river, post, ds))
        else:
            lvp = None if pd.isna(r["level_vs_oya_pct"]) else round(float(r["level_vs_oya_pct"]), 2)
            updates_hist.append((lvp, river, post, ds))
    if not dry:
        if updates_new:
            conn.executemany(
                "UPDATE daily_features SET level_lag_1=?, level_lag_3=?, level_lag_7=?, level_lag_14=?, "
                "level_ma7=?, level_ma14=?, level_ma30=?, delta_1d=?, delta_3d=?, delta_7d=?, "
                "precip_sum_3d=?, precip_sum_7d=?, precip_sum_14d=?, precip_sum_30d=?, precip_sum_60d=?, "
                "precip_sum_90d=?, temp_anomaly=?, level_vs_oya_pct=?, ice_event=?, is_summer=? "
                "WHERE river=? AND post=? AND date=?", updates_new)
        if updates_hist:
            conn.executemany("UPDATE daily_features SET level_vs_oya_pct=? WHERE river=? AND post=? AND date=?",
                             updates_hist)
        conn.commit()
    return len(updates_new) + len(updates_hist)


def stage_features(conn, affected, dry) -> Dict:
    log(f"=== СТАДИЯ: пересчёт производных фичей (date-aware) для {len(affected)} постов ===")
    rep = {"posts": 0, "rows_updated": 0}
    sth = {(rp[0], rp[1]): rp[2] for rp in conn.execute(
        "SELECT river, post, COALESCE(critical_oya,0) FROM stations")}
    for i, (river, post) in enumerate(affected, 1):
        crit = sth.get((river, post), 0)
        n = _recompute_one(conn, river, post, crit, dry)
        rep["posts"] += 1
        rep["rows_updated"] += n
        if i % 25 == 0 or i == len(affected):
            log(f"  фичи: [{i}/{len(affected)}] {river}/{post} (+{n})")
    log(f"Пересчитано фичей: {rep['rows_updated']} строк по {rep['posts']} постам")
    return rep


def _report_validation(levels, meteo, ice, snow):
    log("=== ВАЛИДАЦИЯ диапазонов ===")
    sheets = (("Уровни", levels), ("Метео", meteo), ("Лёд", ice), ("Снег", snow))
    for name, df in sheets:
        if df is None:
            continue
        bad = {}
        for col in RANGES:
            n = validate_series(df, col)
            if n:
                bad[col] = n
        dup = ""
        if name == "Уровни":
            d = levels.duplicated(subset=["river", "post", "date"]).sum()
            dup = f", дубликатов(river/post/date)={d}"
        log(f"  {name}: вне диапазона {bad or 'нет'}{dup}")


def _db_stats(conn):
    log("=== ИТОГовые статистики БД ===")
    n_st = conn.execute("SELECT COUNT(*) FROM stations").fetchone()[0]
    n_df = conn.execute("SELECT COUNT(*) FROM daily_features").fetchone()[0]
    rng = conn.execute("SELECT MIN(date), MAX(date) FROM daily_features").fetchone()
    crit = conn.execute("SELECT COUNT(*) FROM stations WHERE critical_oya IS NOT NULL").fetchone()[0]
    nya = conn.execute("SELECT COUNT(*) FROM stations WHERE low_oya IS NOT NULL").fetchone()[0]
    noc = conn.execute("SELECT COUNT(*) FROM stations WHERE lat IS NOT NULL AND lon IS NOT NULL").fetchone()[0]
    log(f"  stations={n_st}, daily_features={n_df:,}, даты {rng[0]}..{rng[1]}")
    log(f"  critical_oya заполнен у {crit}/{n_st}; low_oya(НЯ) у {nya}/{n_st}; координаты у {noc}/{n_st}")
    if _has_col(conn, "stations", "low_water_oya"):
        lwm = conn.execute("SELECT COUNT(*) FROM stations WHERE low_water_oya IS NOT NULL").fetchone()[0]
        log(f"  low_water_oya(маловодье) у {lwm}/{n_st}")
    if _has_col(conn, "stations", "threshold_source"):
        ts = conn.execute("SELECT COALESCE(threshold_source,'(null)'), COUNT(*) FROM stations GROUP BY 1").fetchall()
        log(f"  threshold_source: {dict(ts)}")


def main():
    ap = argparse.ArgumentParser(description="Импорт Excel 2026 + миграция порогов HydroPredict")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--excel", default=str(EXCEL_PATH))
    ap.add_argument("--no-backup", action="store_true")
    ap.add_argument("--stage", default="all", choices=["all", "thresholds", "coords", "import", "features"])
    ap.add_argument("--river")
    ap.add_argument("--post")
    args = ap.parse_args()

    if not Path(args.db).exists():
        log(f"ERROR БД не найдена: {args.db}"); return 2
    if not Path(args.excel).exists():
        log(f"ERROR Excel не найден: {args.excel}"); return 2
    dry = args.dry_run

    if not dry and not args.no_backup:
        bak = Path(args.db).with_suffix(f".db.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        shutil.copy2(args.db, bak)
        log(f"Бэкап БД: {bak}")

    log(f"Открытие Excel: {args.excel}")
    xl = pd.ExcelFile(args.excel, engine="openpyxl")
    log(f"Листы: {xl.sheet_names}")

    need_levels = args.stage in ("all", "thresholds", "import")
    need_meteo = args.stage in ("all", "import")
    need_ice = args.stage in ("all", "coords", "import")
    need_snow = args.stage in ("all", "import")
    levels = load_levels(xl) if need_levels else None
    meteo = load_meteo(xl) if need_meteo else None
    ice = load_ice(xl) if need_ice else None
    snow = load_snow(xl) if need_snow else None
    if levels is not None:
        _report_validation(levels, meteo, ice, snow)

    conn = sqlite3.connect(args.db, timeout=120)
    conn.execute("PRAGMA busy_timeout=120000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        affected = []
        if args.stage in ("all", "thresholds"):
            stage_thresholds(conn, levels, dry)
        if args.stage in ("all", "coords"):
            stage_coords(conn, ice, dry)
        if args.stage in ("all", "import"):
            rep_imp, affected = stage_import(conn, levels, meteo, ice, snow, dry,
                                             args.post, args.river)
        if args.stage in ("all", "features"):
            if not affected and args.stage == "features":
                # пересчёт для всех постов с данными за 2026+
                affected = [tuple(r) for r in conn.execute(
                    "SELECT DISTINCT river, post FROM daily_features WHERE date >= '2026-01-01'")]
            stage_features(conn, affected, dry)
        _db_stats(conn)
        log("=== ГОТОВО ===")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())






