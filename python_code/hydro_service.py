# -*- coding: utf-8 -*-
"""Общая логика прогнозов, climatology и explain для HydroPredict API."""

from __future__ import annotations

import math
import sqlite3
import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from pandas import DataFrame

_SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _SCRIPT_DIR.parent
DB_PATH = PROJECT_ROOT / "data" / "ml_features.db"
MODELS_DIR = PROJECT_ROOT / "models"
LEGACY_MODELS_DIR = _SCRIPT_DIR / "models"

TIER_HORIZONS = {
    "short": [1, 3, 7],
    "medium": [14, 30],
    "season": [30],
}

FEATURE_LABELS_RU = {
    "temp_min": "Мин. температура",
    "temp_mean": "Средняя температура",
    "temp_max": "Макс. температура",
    "precip_mm": "Осадки за сутки",
    "snow_pct_norm": "Снегозапасы (% от нормы)",
    "ice_thickness_cm": "Толщина льда",
    "level_lag_1": "Уровень 1 день назад",
    "level_lag_7": "Уровень 7 дней назад",
    "level_lag_14": "Уровень 14 дней назад",
    "level_ma7": "Средний уровень 7 дней",
    "level_ma30": "Средний уровень 30 дней",
    "delta_7d": "Изменение уровня за 7 дней",
    "precip_sum_7d": "Сумма осадков 7 дней",
    "precip_sum_14d": "Сумма осадков 14 дней",
    "temp_anomaly": "Аномалия температуры",
    "level_vs_oya_pct": "Уровень относительно ОЯ (%)",
}


def station_model_dir(river: str, post: str) -> Path:
    primary = MODELS_DIR / river / post
    if primary.exists() and any(primary.glob("model_h*.joblib")):
        return primary
    safe = f"{river}__{post}".replace(" ", "_").replace("/", "_")
    legacy = LEGACY_MODELS_DIR / safe
    if legacy.exists() and any(legacy.glob("model_h*.joblib")):
        return legacy
    return primary


def has_trained_model(river: str, post: str) -> bool:
    d = station_model_dir(river, post)
    return d.exists() and any(d.glob("model_h*.joblib"))


def get_db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(str(DB_PATH))
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def get_station_row(river: str, post: str) -> Optional[dict]:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM stations WHERE river = ? AND post = ? LIMIT 1",
            (river, post),
        ).fetchone()
        if row:
            return dict(row)
        # Есть наблюдения, но нет строки в stations — не отдаём 404 на прогноз
        has_data = conn.execute(
            "SELECT 1 FROM daily_features WHERE river = ? AND post = ? LIMIT 1",
            (river, post),
        ).fetchone()
        if has_data:
            return {
                "river": river,
                "post": post,
                "low_oya": 500.0,
                "critical_oya": 650.0,
                "lat": 0.0,
                "lon": 0.0,
            }
        return None
    finally:
        conn.close()


def get_latest_data_date(river: str, post: str) -> datetime.date:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT MAX(date) AS md FROM daily_features WHERE river = ? AND post = ?",
            (river, post),
        ).fetchone()
        if row and row["md"]:
            return datetime.date.fromisoformat(str(row["md"])[:10])
    finally:
        conn.close()
    return datetime.date.today()


def get_model_backend(river: str, post: str) -> Optional[str]:
    manifest = station_model_dir(river, post) / "manifest.json"
    if not manifest.exists():
        return None
    try:
        import json
        from flood_predictor import DEFAULT_BACKEND, VALID_BACKENDS

        data = json.loads(manifest.read_text(encoding="utf-8"))
        backend = (data.get("backend") or DEFAULT_BACKEND).lower().strip()
        return backend if backend in VALID_BACKENDS else DEFAULT_BACKEND
    except Exception:
        return None


def load_predictor(river: str, post: str):
    from flood_predictor import DEFAULT_BACKEND, FloodPredictor

    if not has_trained_model(river, post):
        return None
    backend = get_model_backend(river, post) or DEFAULT_BACKEND
    predictor = FloodPredictor(
        models_dir=str(MODELS_DIR),
        db_path=str(DB_PATH),
        backend=backend,
    )
    predictor.load_models(river, post)
    return predictor


def estimate_risk_summary(max_q95: float, low_oya: float, critical_oya: float) -> dict:
    if max_q95 >= critical_oya:
        return {
            "max_q95": round(max_q95, 2),
            "current_risk": "ОПАСНЫЙ (ОЯ)",
            "prob_warning": 0.95,
            "prob_danger": 0.60,
        }
    if max_q95 >= low_oya:
        return {
            "max_q95": round(max_q95, 2),
            "current_risk": "ПОВЫШЕННЫЙ (НЯ)",
            "prob_warning": 0.55,
            "prob_danger": 0.10,
        }
    return {
        "max_q95": round(max_q95, 2),
        "current_risk": "НИЗКИЙ",
        "prob_warning": 0.05,
        "prob_danger": 0.01,
    }


def _pick_forecast_horizon(
    predictor,
    need_h: int,
    preferred_horizons: Optional[List[int]] = None,
) -> Optional[int]:
    """Минимальный обученный горизонт >= need_h; иначе максимальный из доступных."""
    trained = sorted(int(h) for h in predictor.models.keys())
    if not trained:
        return None
    pref = list(preferred_horizons or []) + [h for h in trained if h not in (preferred_horizons or [])]
    candidates = [h for h in pref if h in predictor.models and h >= need_h]
    if not candidates:
        candidates = [h for h in trained if h >= need_h]
    if not candidates:
        return max(trained)
    return min(candidates)


def forecast_points_from_predictor(
    predictor,
    base_date: datetime.date,
    days: int,
    low_oya: float,
    critical_oya: float,
    preferred_horizons: Optional[List[int]] = None,
) -> List[dict]:
    points = []
    trained = sorted(int(h) for h in predictor.models.keys())
    for i in range(days):
        target = base_date + datetime.timedelta(days=i + 1)
        need_h = i + 1
        use_h = _pick_forecast_horizon(predictor, need_h, preferred_horizons)
        if use_h is None:
            continue
        res = predictor.predict(
            base_date,
            horizon=use_h,
            warning_level=low_oya,
            danger_level=critical_oya,
        )
        if not res:
            continue
        median = float(res.get("median", 0))
        points.append({
            "date": target.isoformat(),
            "median": round(median, 2),
            "q10": round(float(res.get("q10", median * 0.85)), 2),
            "q90": round(float(res.get("q90", median * 1.15)), 2),
            "q95": round(float(res.get("q95", median * 1.2)), 2),
            "horizon_used": use_h,
            "prob_warning": res.get("prob_warning"),
            "prob_danger": res.get("prob_danger"),
        })
    return points


def compute_climatology(river: str, post: str, exclude_year: Optional[int] = None) -> List[dict]:
    import pandas as pd

    conn = get_db()
    try:
        df = pd.read_sql_query(
            """
            SELECT date, water_level_cm, temp_mean, precip_mm, snow_pct_norm, ice_thickness_cm
            FROM daily_features
            WHERE river = ? AND post = ? AND water_level_cm IS NOT NULL
            """,
            conn,
            params=(river, post),
        )
    finally:
        conn.close()

    if df.empty:
        return []

    df["date"] = pd.to_datetime(df["date"])
    df["doy"] = df["date"].dt.dayofyear
    if exclude_year:
        df = df[df["date"].dt.year != exclude_year]

    agg = df.groupby("doy").agg(
        hist_mean=("water_level_cm", "mean"),
        hist_min=("water_level_cm", "min"),
        hist_max=("water_level_cm", "max"),
        hist_q10=("water_level_cm", lambda s: float(s.quantile(0.1))),
        hist_q90=("water_level_cm", lambda s: float(s.quantile(0.9))),
        temp_mean=("temp_mean", "mean"),
        precip_mm=("precip_mm", "mean"),
        snow_pct_norm=("snow_pct_norm", "mean"),
        ice_thickness_cm=("ice_thickness_cm", "mean"),
    ).reset_index()

    result = []
    for _, row in agg.iterrows():
        doy = int(row["doy"])
        d = datetime.date(2000, 1, 1) + datetime.timedelta(days=doy - 1)
        months_ru = (
            "янв", "фев", "мар", "апр", "май", "июн",
            "июл", "авг", "сен", "окт", "ноя", "дек",
        )
        date_label = d.strftime("%d.%m")
        date_label_long = f"{d.day} {months_ru[d.month - 1]}"
        result.append({
            "day_of_year": doy,
            "date_label": date_label,
            "date_label_long": date_label_long,
            "hist_mean": _rnd(row["hist_mean"]),
            "hist_min": _rnd(row["hist_min"]),
            "hist_max": _rnd(row["hist_max"]),
            "hist_q10": _rnd(row["hist_q10"]),
            "hist_q90": _rnd(row["hist_q90"]),
            "temp_mean": _rnd(row["temp_mean"]),
            "precip_mm": _rnd(row["precip_mm"]),
            "snow_pct_norm": _rnd(row["snow_pct_norm"]),
            "ice_thickness_cm": _rnd(row["ice_thickness_cm"]),
        })
    return result


def _rnd(v) -> Optional[float]:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return round(float(v), 2)


def season_forecast_blend(
    predictor,
    base_date: datetime.date,
    low_oya: float,
    critical_oya: float,
    climatology: List[dict],
    days: int = 90,
) -> List[dict]:
    h30 = forecast_points_from_predictor(
        predictor,
        base_date,
        days=days,
        low_oya=low_oya,
        critical_oya=critical_oya,
        preferred_horizons=[30] if 30 in predictor.models else [14, 7],
    )
    clim_map = {c["day_of_year"]: c for c in climatology}
    for pt in h30:
        d = datetime.date.fromisoformat(pt["date"])
        clim = clim_map.get(d.timetuple().tm_yday, {})
        cm = clim.get("hist_mean")
        w = 0.7 if d.month in (4, 5, 6, 7) else 0.4
        if cm is not None:
            pt["median"] = round(w * pt["median"] + (1 - w) * cm, 2)
            pt["blend"] = True
        else:
            pt["blend"] = False
    return h30


def build_explanation(
    river: str,
    post: str,
    base_date: Optional[datetime.date] = None,
    horizon: int = 7,
) -> dict:
    base_date = base_date or get_latest_data_date(river, post)
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM daily_features WHERE river=? AND post=? AND date=?",
            (river, post, base_date.isoformat()),
        ).fetchone()
        if not row:
            row = conn.execute(
                "SELECT * FROM daily_features WHERE river=? AND post=? ORDER BY date DESC LIMIT 1",
                (river, post),
            ).fetchone()
    finally:
        conn.close()

    if not row:
        return {"factors": [], "narrative": "Нет данных для объяснения.", "horizon": horizon, "date": base_date.isoformat()}

    data = dict(row)
    st = get_station_row(river, post) or {}
    critical = float(st.get("critical_oya") or 650)
    low = float(st.get("low_oya") or 500)
    level = data.get("water_level_cm")

    factors = []
    checks = [
        ("snow_pct_norm", 120, "Большой снегозапас ускоряет весенний подъём уровня."),
        ("ice_thickness_cm", 70, "Толстый лёд — риск резкого паводка при оттепели."),
        ("precip_sum_7d", 15, "Сильные осадки за неделю повышают приток."),
        ("delta_7d", 20, "Быстрый рост уровня за 7 дней."),
        ("temp_anomaly", 3, "Температура выше нормы — ускорение таяния."),
    ]
    for feat, thr, text in checks:
        val = data.get(feat)
        if val is None:
            continue
        try:
            val = float(val)
        except (TypeError, ValueError):
            continue
        if math.isnan(val):
            continue
        if val >= thr:
            factors.append({
                "feature": feat,
                "label": FEATURE_LABELS_RU.get(feat, feat),
                "value": round(val, 2),
                "note": text,
            })

    predictor = load_predictor(river, post)
    importance: Dict[str, float] = {}
    if predictor:
        try:
            importance = predictor.get_feature_importance(horizon=horizon, quantile=0.5)
        except Exception:
            pass

    top_imp = list(importance.items())[:5]
    parts = []
    if level is not None:
        parts.append(f"На {base_date.isoformat()} уровень {round(float(level), 1)} см.")
        if float(level) >= critical * 0.9:
            parts.append("Близко к опасному уровню (ОЯ).")
        elif float(level) >= low:
            parts.append("Выше повышенного уровня (НЯ).")
    if factors:
        parts.append(factors[0]["note"])
    elif top_imp:
        parts.append(f"Главный фактор: {FEATURE_LABELS_RU.get(top_imp[0][0], top_imp[0][0])}.")
    else:
        parts.append("Обстановка в пределах типичной для сезона.")

    return {
        "date": base_date.isoformat(),
        "horizon": horizon,
        "factors": factors[:5],
        "feature_importance": {k: round(float(v), 4) for k, v in top_imp},
        "narrative": " ".join(parts),
        "tier_hint": "short" if horizon <= 7 else ("medium" if horizon <= 30 else "season"),
    }


def get_latest_year_with_data(river: str, post: str) -> int:
    df = _load_station_levels_df(river, post)
    if df.empty:
        return datetime.date.today().year
    sub = df[df["water_level_cm"].notna()]
    if sub.empty:
        return datetime.date.today().year
    return int(sub["date"].dt.year.max())


def get_default_display_year(river: str, post: str) -> int:
    """Год по умолчанию в UI: текущий календарный (2026), не «последний год в файле БД»."""
    return datetime.date.today().year


def _load_station_levels_df(river: str, post: str):
    import pandas as pd

    conn = get_db()
    try:
        df = pd.read_sql_query(
            """
            SELECT date, water_level_cm, temp_mean, precip_mm, snow_pct_norm, ice_thickness_cm
            FROM daily_features
            WHERE river = ? AND post = ?
            ORDER BY date
            """,
            conn,
            params=(river, post),
        )
    finally:
        conn.close()
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df["water_level_cm"] = pd.to_numeric(df["water_level_cm"], errors="coerce")
    return df


def _detect_flood_peaks(
    daily: "DataFrame",
    low_oya: float,
    critical_oya: float,
    min_gap_days: int = 7,
) -> List[dict]:
    import pandas as pd

    if daily.empty or daily["water_level_cm"].notna().sum() < 10:
        return []

    s = daily.dropna(subset=["water_level_cm"]).copy()
    s = s.sort_values("date")
    s["smooth"] = s["water_level_cm"].rolling(3, center=True, min_periods=1).mean()
    peaks = []
    dates = s["date"].tolist()
    levels = s["smooth"].tolist()
    last_peak_idx = -min_gap_days

    for i in range(1, len(levels) - 1):
        if levels[i] < low_oya:
            continue
        if levels[i] <= levels[i - 1] or levels[i] <= levels[i + 1]:
            continue
        if i - last_peak_idx < min_gap_days:
            if peaks and levels[i] > peaks[-1]["level"]:
                peaks[-1] = {
                    "date": dates[i].strftime("%Y-%m-%d"),
                    "level": round(float(levels[i]), 2),
                    "type": "oya" if levels[i] >= critical_oya else "nya",
                }
            continue
        peaks.append({
            "date": dates[i].strftime("%Y-%m-%d"),
            "level": round(float(levels[i]), 2),
            "type": "oya" if levels[i] >= critical_oya else "nya",
        })
        last_peak_idx = i
    return peaks


def _compute_yearly_stats(df, low_oya: float, critical_oya: float) -> List[dict]:
    import pandas as pd

    valid = df.dropna(subset=["water_level_cm"]).copy()
    if valid.empty:
        return []

    valid["calendar_year"] = valid["date"].dt.year
    rows = []
    all_maxes = []

    for yr, grp in valid.groupby("calendar_year"):
        levels = grp["water_level_cm"]
        annual_max = float(levels.max())
        all_maxes.append(annual_max)
        days_nya = int((levels >= low_oya).sum())
        days_oya = int((levels >= critical_oya).sum())
        peaks = _detect_flood_peaks(grp, low_oya, critical_oya)
        spikes = levels.diff().abs()
        bad_spike = bool((spikes > 300).any())

        rows.append({
            "year": int(yr),
            "annual_max": round(annual_max, 2),
            "annual_min": round(float(levels.min()), 2),
            "annual_mean": round(float(levels.mean()), 2),
            "annual_p95": round(float(levels.quantile(0.95)), 2),
            "days_above_nya": days_nya,
            "days_above_oya": days_oya,
            "peak_count": len(peaks),
            "data_quality_flag": bad_spike,
        })

    if not rows:
        return []

    threshold_90 = float(pd.Series(all_maxes).quantile(0.90))
    for r in rows:
        r["is_critical"] = (
            r["annual_max"] >= threshold_90
            and (r["days_above_nya"] >= 5 or r["peak_count"] >= 2)
            and not r["data_quality_flag"]
        )
        r["percentile_rank"] = round(
            100 * sum(1 for m in all_maxes if m <= r["annual_max"]) / len(all_maxes), 1
        )

    rows.sort(key=lambda x: x["annual_max"], reverse=True)
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return rows


def _forecast_map_for_calendar_year(
    display_year: int,
    base: datetime.date,
    predictor,
    low: float,
    crit: float,
    clim_map: Dict[int, dict],
    view_type: str,
) -> Dict[str, dict]:
    """
    Прогноз на каждый день календарного года: rollout модели + климатология для пробелов.
    """
    if not predictor:
        return {}

    year_end = datetime.date(display_year, 12, 31)
    span_days = max(400, (year_end - base).days + 90)
    pts = forecast_points_from_predictor(
        predictor, base, span_days, low, crit,
        preferred_horizons=[30, 14, 7, 3, 1],
    )
    by_doy: Dict[int, dict] = {}
    for p in pts:
        try:
            pdate = datetime.date.fromisoformat(p["date"])
        except ValueError:
            continue
        doy = pdate.timetuple().tm_yday
        by_doy[doy] = p

    medians = [float(x["median"]) for x in by_doy.values() if x.get("median") is not None]
    model_lift = (sum(medians) / len(medians)) if medians else None
    clim_means = [c.get("hist_mean") for c in clim_map.values() if c.get("hist_mean")]
    clim_avg = (sum(clim_means) / len(clim_means)) if clim_means else None
    scale = (model_lift / clim_avg) if model_lift and clim_avg and clim_avg > 0 else 1.0
    scale = max(0.7, min(scale, 1.4))

    forecast_map: Dict[str, dict] = {}
    start = datetime.date(display_year, 1, 1)
    for i in range(366):
        d = start + datetime.timedelta(days=i)
        if d.year != display_year:
            break
        if view_type == "mixed" and d <= base:
            continue
        ds = d.isoformat()
        doy = d.timetuple().tm_yday
        if doy in by_doy:
            p = by_doy[doy]
            forecast_map[ds] = {
                "date": ds,
                "median": p["median"],
                "q10": p.get("q10"),
                "q90": p.get("q90"),
                "q95": p.get("q95"),
                "source": "model_doy",
            }
            continue
        c = clim_map.get(doy, {})
        hm = c.get("hist_mean")
        if hm is None:
            continue
        med = round(float(hm) * scale, 2)
        hq10 = c.get("hist_q10") or hm * 0.9
        hq90 = c.get("hist_q90") or hm * 1.1
        forecast_map[ds] = {
            "date": ds,
            "median": med,
            "q10": round(float(hq10) * scale, 2),
            "q90": round(float(hq90) * scale, 2),
            "q95": round(med * 1.15, 2),
            "source": "climatology_scaled",
        }
    return forecast_map


def apply_scenario_modifiers(
    points: List[dict],
    temp_delta: float = 0.0,
    precip_pct: float = 100.0,
    snow_pct: float = 100.0,
) -> List[dict]:
    """Сдвиг уровней по сценарию температуры / осадков / снега (для «что если»)."""
    level_shift = (float(temp_delta) * 5.0) + ((float(precip_pct) - 100.0) * 0.5) + ((float(snow_pct) - 100.0) * 0.15)
    out = []
    for p in points:
        med = float(p.get("median", 0))
        q10 = float(p.get("q10", med * 0.9))
        q90 = float(p.get("q90", med * 1.1))
        q95 = float(p.get("q95", med * 1.15))
        med2 = max(0.0, med + level_shift)
        out.append({
            **p,
            "median": round(med2, 2),
            "q10": round(max(0.0, q10 + level_shift * 0.85), 2),
            "q90": round(max(0.0, q90 + level_shift * 1.05), 2),
            "q95": round(max(0.0, q95 + level_shift * 1.15), 2),
        })
    return out


def build_scenario_forecasts(
    river: str,
    post: str,
    days: int = 30,
    temp_delta: float = 0.0,
    precip_pct: float = 100.0,
    snow_pct: float = 100.0,
) -> dict:
    """Несколько сценариев прогноза на горизонте days."""
    st = get_station_row(river, post)
    if not st:
        raise ValueError(f"Станция не найдена: {river} / {post}")
    low = float(st.get("low_oya") or 500)
    crit = float(st.get("critical_oya") or 650)
    base = get_latest_data_date(river, post)

    predictor = load_predictor(river, post)
    if predictor:
        baseline_pts = forecast_points_from_predictor(predictor, base, days, low, crit)
    else:
        tier = tier_forecast(river, post, "medium", days=days, base_date=base)
        baseline_pts = tier.get("forecast") or []

    if not baseline_pts:
        raise ValueError("Нет точек прогноза для сценариев")

    presets = [
        ("baseline", "Базовый (модель)", 0, 100, 100),
        ("sliders", "Ваш сценарий (ползунки)", temp_delta, precip_pct, snow_pct),
        ("wet_warm", "Тёплый паводок (+5°C, +50% осадков)", 5, 150, 110),
        ("cold_dry", "Холодный низкий (−5°C, −30% осадков)", -5, 70, 80),
        ("heavy_rain", "Сильные осадки (+80% осадков)", 0, 180, 100),
    ]
    scenarios = []
    for sid, label, t, pr, sn in presets:
        if sid == "baseline":
            pts = baseline_pts
        else:
            pts = apply_scenario_modifiers(baseline_pts, t, pr, sn)
        scenarios.append({
            "id": sid,
            "label": label,
            "temp_delta": t,
            "precip_pct": pr,
            "snow_pct": sn,
            "points": pts,
        })

    return {
        "river": river,
        "post": post,
        "base_date": base.isoformat(),
        "days": days,
        "has_model": predictor is not None,
        "scenarios": scenarios,
    }


def build_year_analytics(
    river: str,
    post: str,
    year: int,
    overlay_years: int = 3,
) -> dict:
    import pandas as pd

    st = get_station_row(river, post)
    if not st:
        raise ValueError(f"Станция не найдена: {river} / {post}")

    requested_year = int(year)
    low = float(st.get("low_oya") or 500)
    crit = float(st.get("critical_oya") or 650)
    df = _load_station_levels_df(river, post)
    if df.empty:
        return {
            "year": requested_year,
            "requested_year": requested_year,
            "series": [],
            "peaks": [],
            "year_min": None,
            "year_max": None,
            "extreme_years": [],
            "critical_years": [],
            "overlays": [],
            "indicators": {},
            "monthly_risk": [],
            "monthly_actual": [],
            "layers": [],
            "has_model": False,
            "thresholds": {"low_oya": low, "critical_oya": crit},
            "message": "Нет данных по станции в БД",
        }

    max_data_year = get_latest_year_with_data(river, post)
    today_year = datetime.date.today().year
    display_year = requested_year
    note = None
    base = get_latest_data_date(river, post)

    if requested_year > today_year:
        view_type = "future"
    elif requested_year == today_year:
        view_type = "mixed"
    elif requested_year > max_data_year:
        view_type = "future"
    elif requested_year == max_data_year and base.year == requested_year:
        view_type = "mixed"
    else:
        view_type = "past"

    clim = compute_climatology(river, post, exclude_year=display_year if view_type == "past" else None)
    clim_map = {c["day_of_year"]: c for c in clim}

    yearly_stats = _compute_yearly_stats(df, low, crit)
    extreme_years = [y for y in yearly_stats if y.get("is_critical")][:15]

    overlay_list = []
    for ys in yearly_stats[: overlay_years + 5]:
        oy = ys["year"]
        if oy == display_year:
            continue
        sub = df[(df["date"].dt.year == oy) & df["water_level_cm"].notna()]
        if sub.empty:
            continue
        overlay_list.append({
            "year": oy,
            "annual_max": ys["annual_max"],
            "is_critical": ys.get("is_critical", False),
            "series": [
                {"date": r["date"].strftime("%Y-%m-%d"), "level": round(float(r["water_level_cm"]), 2)}
                for _, r in sub.iterrows()
            ],
        })
        if len(overlay_list) >= overlay_years:
            break

    year_df = df[(df["date"].dt.year == display_year) & df["water_level_cm"].notna()].copy()
    if view_type == "past" and year_df.empty:
        display_year = max_data_year
        year_df = df[(df["date"].dt.year == display_year) & df["water_level_cm"].notna()].copy()
        note = f"За {requested_year} г. наблюдений нет — показан факт {display_year} г."

    peaks = _detect_flood_peaks(year_df, low, crit) if not year_df.empty else []

    year_min = year_max = None
    if not year_df.empty:
        imin = year_df["water_level_cm"].idxmin()
        imax = year_df["water_level_cm"].idxmax()
        year_min = {
            "date": year_df.loc[imin, "date"].strftime("%Y-%m-%d"),
            "value": round(float(year_df.loc[imin, "water_level_cm"]), 2),
        }
        year_max = {
            "date": year_df.loc[imax, "date"].strftime("%Y-%m-%d"),
            "value": round(float(year_df.loc[imax, "water_level_cm"]), 2),
        }

    predictor = load_predictor(river, post)
    data_through = base.isoformat()
    forecast_map: Dict[str, dict] = {}
    if predictor and view_type in ("future", "mixed"):
        forecast_map = _forecast_map_for_calendar_year(
            display_year, base, predictor, low, crit, clim_map, view_type,
        )
    elif predictor and view_type == "past":
        pts = forecast_points_from_predictor(predictor, base, days=120, low_oya=low, critical_oya=crit)
        for p in pts:
            try:
                if datetime.date.fromisoformat(p["date"]).year == display_year:
                    forecast_map[p["date"]] = p
            except ValueError:
                pass

    actual_by_date = {}
    if not year_df.empty:
        for _, r in year_df.iterrows():
            actual_by_date[r["date"].strftime("%Y-%m-%d")] = round(float(r["water_level_cm"]), 2)

    series = []
    start = datetime.date(display_year, 1, 1)
    for i in range(366):
        d = start + datetime.timedelta(days=i)
        if d.year != display_year:
            break
        doy = d.timetuple().tm_yday
        c = clim_map.get(doy, {})
        ds = d.isoformat()
        fc = forecast_map.get(ds)
        series.append({
            "date": ds,
            "date_label": d.strftime("%d.%m.%Y"),
            "hist_mean": c.get("hist_mean"),
            "hist_q10": c.get("hist_q10"),
            "hist_q90": c.get("hist_q90"),
            "actual": actual_by_date.get(ds),
            "median": fc["median"] if fc else None,
            "q10": fc.get("q10") if fc else None,
            "q90": fc.get("q90") if fc else None,
            "q95": fc.get("q95") if fc else None,
        })

    monthly_actual = []
    names = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]
    if not year_df.empty:
        year_df["month"] = year_df["date"].dt.month
        for m in range(1, 13):
            mg = year_df[year_df["month"] == m]
            if mg.empty:
                monthly_actual.append({
                    "month": m, "month_name": names[m - 1],
                    "max_level": None, "days_above_nya": 0, "days_above_oya": 0,
                })
                continue
            lv = mg["water_level_cm"]
            monthly_actual.append({
                "month": m,
                "month_name": names[m - 1],
                "max_level": round(float(lv.max()), 2),
                "mean_level": round(float(lv.mean()), 2),
                "days_above_nya": int((lv >= low).sum()),
                "days_above_oya": int((lv >= crit).sum()),
            })

    layers = []
    if not year_df.empty:
        ym = year_df.copy()
        ym["month"] = ym["date"].dt.month
        for m in range(1, 13):
            mg = ym[ym["month"] == m]
            if mg.empty:
                continue
            layers.append({
                "month": m,
                "month_name": names[m - 1],
                "temp_mean": _rnd(mg["temp_mean"].mean()) if "temp_mean" in mg else None,
                "precip_mm": _rnd(mg["precip_mm"].sum()) if "precip_mm" in mg else None,
                "snow_pct_norm": _rnd(mg["snow_pct_norm"].mean()) if "snow_pct_norm" in mg else None,
                "ice_thickness_cm": _rnd(mg["ice_thickness_cm"].mean()) if "ice_thickness_cm" in mg else None,
            })

    cur_stats = next((y for y in yearly_stats if y["year"] == display_year), None)
    clim_max = max((c.get("hist_max") or 0 for c in clim), default=0) or 1
    indicators = {
        "year_max": year_max,
        "year_min": year_min,
        "flood_peaks_count": len(peaks),
        "days_above_nya": cur_stats["days_above_nya"] if cur_stats else 0,
        "days_above_oya": cur_stats["days_above_oya"] if cur_stats else 0,
        "vs_climatology_max_pct": round(100 * (year_max["value"] / clim_max), 1) if year_max and clim_max else None,
        "rank_among_years": cur_stats["rank"] if cur_stats else None,
        "is_critical_year": bool(cur_stats and cur_stats.get("is_critical")),
        "nearest_critical_years": [y["year"] for y in extreme_years if y["year"] != display_year][:5],
    }

    monthly_risk = monthly_risk_summary(
        [p for p in forecast_map.values()] if forecast_map else [],
        low,
        crit,
    )

    forecast_max = None
    if forecast_map:
        fp = max(forecast_map.values(), key=lambda x: x.get("median", 0))
        forecast_max = {"date": fp["date"], "value": fp.get("median"), "q95": fp.get("q95")}

    fc_days = sum(1 for s in series if s.get("median") is not None and s.get("actual") is None)
    if view_type == "future":
        note = note or (
            f"Прогноз на весь {display_year} г. (наблюдений за этот год в БД нет). "
            f"Заполнено {fc_days} дней моделью и климатологией. База: {data_through}."
        )
    elif view_type == "mixed":
        note = note or (
            f"Факт до {data_through}, остаток {display_year} г. — прогноз ({fc_days} дн.)."
        )

    summary = {
        "view_type_ru": {
            "past": "Прошлый год (факт)",
            "future": "Будущий год (прогноз)",
            "mixed": "Текущий год (факт + прогноз)",
        }.get(view_type, view_type),
        "year_max_fact": year_max,
        "year_min_fact": year_min,
        "forecast_max": forecast_max,
        "days_above_nya": indicators.get("days_above_nya"),
        "days_above_oya": indicators.get("days_above_oya"),
        "flood_peaks_count": len(peaks),
        "rank_among_years": indicators.get("rank_among_years"),
        "is_critical_year": indicators.get("is_critical_year"),
    }

    return {
        "year": display_year,
        "requested_year": requested_year,
        "view_type": view_type,
        "data_through": data_through,
        "available_years": get_available_years(river, post),
        "note": note,
        "summary": summary,
        "series": series,
        "peaks": peaks,
        "year_min": year_min,
        "year_max": year_max,
        "extreme_years": yearly_stats,
        "critical_years": extreme_years,
        "overlays": overlay_list,
        "indicators": indicators,
        "monthly_risk": monthly_risk,
        "monthly_actual": monthly_actual,
        "layers": layers,
        "has_model": predictor is not None,
        "thresholds": {"low_oya": low, "critical_oya": crit},
    }


def year_chart_data(river: str, post: str, year: int) -> dict:
    """Совместимость: делегирует в build_year_analytics."""
    return build_year_analytics(river, post, year, overlay_years=3)


def monthly_risk_summary(forecast_points: List[dict], low_oya: float, critical_oya: float) -> List[dict]:
    by_month: Dict[int, List[float]] = {}
    for p in forecast_points:
        d = datetime.date.fromisoformat(p["date"])
        by_month.setdefault(d.month, []).append(float(p.get("q95", p.get("median", 0))))
    names = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]
    result = []
    for m in range(1, 13):
        vals = by_month.get(m, [0])
        max_q = max(vals) if vals else 0
        result.append({
            "month": m,
            "month_name": names[m - 1],
            "max_q95": round(max_q, 2),
            "risk": "ОЯ" if max_q >= critical_oya else ("НЯ" if max_q >= low_oya else "низкий"),
        })
    return result


def tier_forecast(
    river: str,
    post: str,
    tier: str,
    days: Optional[int] = None,
    base_date: Optional[datetime.date] = None,
) -> dict:
    st = get_station_row(river, post)
    if not st:
        raise ValueError(f"Станция не найдена: {river} / {post}")

    low = float(st.get("low_oya") or 500)
    crit = float(st.get("critical_oya") or 650)
    base_date = base_date or get_latest_data_date(river, post)

    default_days = {"short": 7, "medium": 30, "season": 90}.get(tier, 60)
    days = days or default_days

    predictor = load_predictor(river, post)
    is_mock = predictor is None
    points: List[dict] = []

    if predictor:
        if tier == "season":
            clim = compute_climatology(river, post)
            points = season_forecast_blend(predictor, base_date, low, crit, clim, days=days)
        else:
            horizons = TIER_HORIZONS.get(tier, [7])
            points = forecast_points_from_predictor(
                predictor, base_date, days, low, crit, preferred_horizons=horizons,
            )

    max_q95 = max((p.get("q95", 0) for p in points), default=0.0)
    risk = estimate_risk_summary(max_q95, low, crit)

    importance = {}
    if predictor:
        try:
            h = TIER_HORIZONS.get(tier, [7])[0]
            importance = {
                FEATURE_LABELS_RU.get(k, k): round(float(v), 4)
                for k, v in list(predictor.get_feature_importance(horizon=h, quantile=0.5).items())[:10]
            }
        except Exception:
            pass

    tier_horizons = TIER_HORIZONS.get(tier, [])
    trained_h = sorted(int(h) for h in predictor.models.keys()) if predictor else []
    forecast_note = None
    if predictor and not points:
        forecast_note = "Не удалось построить прогноз — проверьте модель."
    elif predictor and tier == "medium" and trained_h and not any(h >= 14 for h in trained_h):
        forecast_note = (
            f"Обучены только горизонты {trained_h}. "
            "Для точного прогноза 14–30 дней выполните быстрое или полное обучение."
        )

    return {
        "tier": tier,
        "base_date": base_date.isoformat(),
        "station": {"river": river, "post": post, "low_oya": low, "critical_oya": crit},
        "forecast": points,
        "risk_summary": risk,
        "feature_importance": importance,
        "is_mock": is_mock,
        "horizons": tier_horizons,
        "trained_horizons": trained_h,
        "forecast_note": forecast_note,
    }


# ---------------------------------------------------------------------------
# История обучения (SQLite)
# ---------------------------------------------------------------------------

_TRAINING_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS training_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    river TEXT,
    post TEXT,
    scope TEXT NOT NULL,
    fast INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    stations_total INTEGER DEFAULT 0,
    stations_trained INTEGER DEFAULT 0,
    stations_skipped INTEGER DEFAULT 0,
    message TEXT
);
CREATE TABLE IF NOT EXISTS training_station_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    history_id INTEGER NOT NULL,
    river TEXT NOT NULL,
    post TEXT NOT NULL,
    status TEXT NOT NULL,
    rows_count INTEGER,
    message TEXT,
    FOREIGN KEY (history_id) REFERENCES training_history(id)
);
CREATE INDEX IF NOT EXISTS idx_training_history_started ON training_history(started_at DESC);
"""

_STATION_MODELS_SQL = """
CREATE TABLE IF NOT EXISTS station_models (
    river TEXT NOT NULL,
    post TEXT NOT NULL,
    trained_at TEXT NOT NULL,
    backend TEXT,
    horizons TEXT,
    model_dir TEXT NOT NULL,
    n_model_files INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (river, post)
);
"""


def ensure_training_schema() -> None:
    if not DB_PATH.exists():
        return
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.executescript(_TRAINING_SCHEMA_SQL + _STATION_MODELS_SQL)
        conn.commit()
    finally:
        conn.close()


def register_station_model(river: str, post: str) -> dict:
    """
    Регистрирует обученную модель в SQLite (метаданные).
    Сами файлы CatBoost лежат в models/{river}/{post}/ — их коммитят в Git отдельно.
    """
    ensure_training_schema()
    if not has_trained_model(river, post):
        return {}

    import json

    model_dir = station_model_dir(river, post)
    rel_dir = model_dir.relative_to(PROJECT_ROOT).as_posix()
    files = sorted(model_dir.glob("model_h*.joblib"))
    trained_at = get_model_trained_at(river, post) or datetime.datetime.utcnow().isoformat(
        timespec="seconds"
    ) + "Z"
    backend = get_model_backend(river, post)
    horizons = get_model_horizons(river, post)
    horizons_json = json.dumps(horizons, ensure_ascii=False)
    now = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"

    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO station_models (
                river, post, trained_at, backend, horizons,
                model_dir, n_model_files, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(river, post) DO UPDATE SET
                trained_at = excluded.trained_at,
                backend = excluded.backend,
                horizons = excluded.horizons,
                model_dir = excluded.model_dir,
                n_model_files = excluded.n_model_files,
                updated_at = excluded.updated_at
            """,
            (
                river,
                post,
                trained_at,
                backend,
                horizons_json,
                rel_dir,
                len(files),
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "river": river,
        "post": post,
        "trained_at": trained_at,
        "backend": backend,
        "horizons": horizons,
        "model_dir": rel_dir,
        "n_model_files": len(files),
        "git_add": f"git add {rel_dir}",
    }


def get_station_model_registry(river: str, post: str) -> Optional[dict]:
    ensure_training_schema()
    if not DB_PATH.exists():
        return None
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM station_models WHERE river = ? AND post = ?",
            (river, post),
        ).fetchone()
        if not row:
            return None
        import json

        data = dict(row)
        try:
            data["horizons"] = json.loads(data.get("horizons") or "[]")
        except json.JSONDecodeError:
            data["horizons"] = []
        data["git_add"] = f"git add {data.get('model_dir', '')}"
        return data
    finally:
        conn.close()


def _training_scope(river: Optional[str], post: Optional[str]) -> str:
    if river and post:
        return "station"
    if river:
        return "river"
    return "all"


def start_training_run(
    task_id: str,
    river: Optional[str],
    post: Optional[str],
    fast: bool,
    stations_total: int,
) -> int:
    ensure_training_schema()
    conn = get_db()
    try:
        cur = conn.execute(
            """
            INSERT INTO training_history (
                task_id, started_at, river, post, scope, fast, status,
                stations_total, stations_trained, stations_skipped, message
            ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?, 0, 0, ?)
            """,
            (
                task_id,
                datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
                river,
                post,
                _training_scope(river, post),
                1 if fast else 0,
                stations_total,
                "Обучение запущено",
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def log_training_station(
    history_id: int,
    river: str,
    post: str,
    status: str,
    rows_count: Optional[int] = None,
    message: str = "",
) -> None:
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO training_station_log (history_id, river, post, status, rows_count, message)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (history_id, river, post, status, rows_count, message[:500]),
        )
        conn.commit()
    finally:
        conn.close()


def finish_training_run(
    history_id: int,
    status: str,
    message: str,
    stations_trained: int,
    stations_skipped: int,
) -> None:
    conn = get_db()
    try:
        conn.execute(
            """
            UPDATE training_history
            SET finished_at = ?, status = ?, message = ?,
                stations_trained = ?, stations_skipped = ?
            WHERE id = ?
            """,
            (
                datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
                status,
                message[:1000],
                stations_trained,
                stations_skipped,
                history_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_training_history(limit: int = 30) -> List[dict]:
    ensure_training_schema()
    if not DB_PATH.exists():
        return []
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT id, task_id, started_at, finished_at, river, post, scope, fast,
                   status, stations_total, stations_trained, stations_skipped, message
            FROM training_history
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_model_trained_at(river: str, post: str) -> Optional[str]:
    manifest = station_model_dir(river, post) / "manifest.json"
    if not manifest.exists():
        return None
    try:
        import json
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return data.get("training_date") or data.get("trained_at") or data.get("last_updated")
    except Exception:
        return None


def get_model_horizons(river: str, post: str) -> List[int]:
    manifest = station_model_dir(river, post) / "manifest.json"
    if not manifest.exists():
        return []
    try:
        import json
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return sorted(int(h) for h in data.get("horizons", []))
    except Exception:
        return []


def get_available_years(river: str, post: str) -> List[int]:
    df = _load_station_levels_df(river, post)
    if df.empty:
        return []
    sub = df[df["water_level_cm"].notna()]
    if sub.empty:
        return []
    today = datetime.date.today().year
    years = sorted(int(y) for y in sub["date"].dt.year.unique())
    years = [y for y in years if y <= today + 1]
    for y in (today, today + 1):
        if y not in years:
            years.append(y)
    return sorted(years)


def get_last_training_for_station(river: str, post: str) -> Optional[dict]:
    ensure_training_schema()
    if not DB_PATH.exists():
        return None
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT h.status, h.finished_at, h.fast, h.message, h.stations_trained
            FROM training_history h
            WHERE (h.river = ? AND h.post = ?)
               OR h.id IN (
                   SELECT history_id FROM training_station_log
                   WHERE river = ? AND post = ?
               )
            ORDER BY h.id DESC
            LIMIT 1
            """,
            (river, post, river, post),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_station_model_status(river: str, post: str) -> dict:
    has_model = has_trained_model(river, post)
    horizons = get_model_horizons(river, post) if has_model else []
    trained_at = get_model_trained_at(river, post) if has_model else None
    last_train = get_last_training_for_station(river, post)
    df = _load_station_levels_df(river, post)
    data_through = None
    if not df.empty:
        sub = df[df["water_level_cm"].notna()]
        if not sub.empty:
            data_through = sub["date"].max().strftime("%Y-%m-%d")
    years = get_available_years(river, post)
    backend = get_model_backend(river, post) if has_model else None
    registry = get_station_model_registry(river, post) if has_model else None
    if has_model and registry is None:
        register_station_model(river, post)
        registry = get_station_model_registry(river, post)
    model_dir = registry.get("model_dir") if registry else (
        station_model_dir(river, post).relative_to(PROJECT_ROOT).as_posix() if has_model else None
    )
    return {
        "river": river,
        "post": post,
        "has_model": has_model,
        "trained_at": trained_at,
        "horizons": horizons,
        "backend": backend,
        "model_dir": model_dir,
        "n_model_files": registry.get("n_model_files") if registry else (
            len(list(station_model_dir(river, post).glob("model_h*.joblib"))) if has_model else 0
        ),
        "registry_updated_at": registry.get("updated_at") if registry else None,
        "git_add_command": f"git add {model_dir}" if model_dir else None,
        "persist_note": (
            "Модель на диске: переобучение не нужно после перезапуска API. "
            "Для GitHub: закоммитьте папку models/… (см. models/README.md)."
        ) if has_model else None,
        "data_through": data_through,
        "available_years": years,
        "last_training": last_train,
        "supports_medium": has_model and any(h >= 14 for h in horizons),
    }
