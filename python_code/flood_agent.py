# -*- coding: utf-8 -*-
"""
Flood-risk агент HydroPredict.

Гибрид «правила + ML» на уже установленных зависимостях (pandas/numpy/
sqlite3 + FloodPredictor). Без LLM/внешних сервисов.
"""

from __future__ import annotations

import datetime
import json
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

import hydro_service as hs

# ---------------------------------------------------------------------------
# Слой персистентности агента: алерты, обратная связь, веса правил.
# Всё живёт в той же ml_features.db, что и остальные данные API.
# ---------------------------------------------------------------------------

_AGENT_SCHEMA_READY = False


def _ensure_agent_schema(conn: sqlite3.Connection) -> None:
    """Идемпотентно создаёт служебные таблицы агента."""
    global _AGENT_SCHEMA_READY
    if _AGENT_SCHEMA_READY:
        return
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS agent_alerts (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            river         TEXT NOT NULL,
            post          TEXT NOT NULL,
            created_at    TEXT NOT NULL,
            observation_date TEXT,
            horizon_days  INTEGER,
            risk_score    REAL,
            risk_class    TEXT,
            peak_level_cm REAL,
            peak_date     TEXT,
            critical_days_json TEXT,
            drivers_json  TEXT,
            narrative     TEXT,
            acknowledged  INTEGER NOT NULL DEFAULT 0,
            acknowledged_at TEXT,
            acknowledged_by TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_alerts_station
            ON agent_alerts(river, post, created_at);
        CREATE INDEX IF NOT EXISTS ix_alerts_pending
            ON agent_alerts(acknowledged, created_at);
        CREATE INDEX IF NOT EXISTS idx_alerts_river_post_created
            ON agent_alerts(river, post, created_at DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS ux_alerts_river_post_obs_horizon
            ON agent_alerts(river, post, observation_date, horizon_days);

        CREATE TABLE IF NOT EXISTS agent_feedback (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            river         TEXT NOT NULL,
            post          TEXT NOT NULL,
            alert_id      INTEGER,
            created_at    TEXT NOT NULL,
            verdict       TEXT NOT NULL,          -- 'reward' | 'penalty'
            actual_class  TEXT,                    -- реальный исход, если знаем
            comment       TEXT,
            payload_json  TEXT,
            FOREIGN KEY (alert_id) REFERENCES agent_alerts(id)
        );
        CREATE INDEX IF NOT EXISTS idx_feedback_alert
            ON agent_feedback(alert_id);

        CREATE TABLE IF NOT EXISTS agent_rule_weights (
            river         TEXT NOT NULL,
            post          TEXT NOT NULL,
            rule_id       TEXT NOT NULL,
            weight        REAL NOT NULL DEFAULT 1.0,
            updated_at    TEXT NOT NULL,
            n_reward      INTEGER NOT NULL DEFAULT 0,
            n_penalty     INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (river, post, rule_id)
        );
        """
    )
    conn.commit()
    _AGENT_SCHEMA_READY = True


def _agent_db() -> sqlite3.Connection:
    conn = hs.get_db()  # уже гарантирует наличие daily_features-схемы
    _ensure_agent_schema(conn)
    return conn


def _load_rule_weights(river: str, post: str) -> Dict[str, float]:
    """Веса правил per-station (после feedback). По умолчанию 1.0."""
    conn = _agent_db()
    try:
        rows = conn.execute(
            "SELECT rule_id, weight FROM agent_rule_weights WHERE river=? AND post=?",
            (river, post),
        ).fetchall()
    finally:
        conn.close()
    return {r["rule_id"]: float(r["weight"]) for r in rows}


def _bump_rule_weight(
    conn: sqlite3.Connection,
    river: str,
    post: str,
    rule_id: str,
    delta: float,
    verdict: str,
) -> None:
    """Мягкое обновление веса правила. delta = +0.1 при reward, -0.15 при penalty.
    Вес зажимается в [0.1 .. 2.5], чтобы не «взорвать» и не «занулить» правило."""
    now = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    row = conn.execute(
        "SELECT weight, n_reward, n_penalty FROM agent_rule_weights "
        "WHERE river=? AND post=? AND rule_id=?",
        (river, post, rule_id),
    ).fetchone()
    if row is None:
        w = max(0.1, min(2.5, 1.0 + delta))
        conn.execute(
            "INSERT INTO agent_rule_weights (river, post, rule_id, weight, updated_at, "
            "n_reward, n_penalty) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (river, post, rule_id, w, now,
             1 if verdict == "reward" else 0,
             1 if verdict == "penalty" else 0),
        )
    else:
        w = max(0.1, min(2.5, float(row["weight"]) + delta))
        nr = int(row["n_reward"]) + (1 if verdict == "reward" else 0)
        np_ = int(row["n_penalty"]) + (1 if verdict == "penalty" else 0)
        conn.execute(
            "UPDATE agent_rule_weights SET weight=?, updated_at=?, n_reward=?, n_penalty=? "
            "WHERE river=? AND post=? AND rule_id=?",
            (w, now, nr, np_, river, post, rule_id),
        )


# Пороговые правила (можно тюнить per-станция потом).
_RULES: List[Dict[str, Any]] = [
    {"id": "snow_high", "feature": "snow_pct_norm", "op": ">=", "threshold": 130.0,
     "score": 25.0, "label": "Аномально большой снегозапас",
     "note": "Снегозапас ≥130% нормы — риск резкого весеннего подъёма.",
     "season": {3, 4, 5}},
    {"id": "snow_extreme", "feature": "snow_pct_norm", "op": ">=", "threshold": 160.0,
     "score": 15.0, "label": "Экстремальный снегозапас",
     "note": "Снегозапас ≥160% нормы — исторически коррелирует с крупными паводками.",
     "season": {3, 4, 5}},
    {"id": "ice_thick", "feature": "ice_thickness_cm", "op": ">=", "threshold": 90.0,
     "score": 15.0, "label": "Толстый лёд",
     "note": "Толщина льда ≥90 см — при быстрой оттепели повышенный риск заторов.",
     "season": {3, 4, 5}},
    {"id": "summer_rain_30d", "feature": "precip_sum_30d", "op": ">=", "threshold": 120.0,
     "score": 20.0, "label": "Обильные летние осадки (30 дней)",
     "note": "Сумма осадков за месяц ≥120 мм — риск дождевого паводка.",
     "season": {6, 7, 8, 9}},
    {"id": "summer_rain_7d", "feature": "precip_sum_7d", "op": ">=", "threshold": 50.0,
     "score": 15.0, "label": "Ливневый эпизод (7 дней)",
     "note": "Сумма осадков за неделю ≥50 мм — быстрый рост притока.",
     "season": {6, 7, 8, 9}},
    {"id": "temp_anom_warm", "feature": "temp_anomaly", "op": ">=", "threshold": 4.0,
     "score": 10.0, "label": "Положительная аномалия температуры",
     "note": "Аномалия ≥+4 °C — ускоряет снеготаяние / вскрытие льда.",
     "season": {3, 4, 5}},
    {"id": "level_rise_7d", "feature": "delta_7d", "op": ">=", "threshold": 30.0,
     "score": 15.0, "label": "Быстрый рост уровня",
     "note": "+30 см за неделю — активная фаза подъёма воды.",
     "season": None},
    {"id": "level_near_oya", "feature": "level_vs_oya_pct", "op": ">=", "threshold": 85.0,
     "score": 20.0, "label": "Уровень близко к ОЯ",
     "note": "Текущий уровень ≥85% от критического ОЯ.",
     "season": None},
]

_RISK_CLASSES: List[Tuple[float, str, str]] = [
    (0.0, "low", "низкий"),
    (30.0, "moderate", "средний"),
    (60.0, "high", "высокий"),
    (85.0, "critical", "критический"),
]


def _classify(score: float) -> Tuple[str, str]:
    en, ru = "low", "низкий"
    for thr, e, r in _RISK_CLASSES:
        if score >= thr:
            en, ru = e, r
    return en, ru


def _data_gaps(features: Dict[str, Any]) -> List[Dict[str, str]]:
    """Список отсутствующих/подозрительных признаков с человеческой рекомендацией."""
    gaps: List[Dict[str, str]] = []
    checks = [
        ("snow_pct_norm",     "Снегозапас (% от нормы)",   "Загрузите снегомерные наблюдения за март–апрель."),
        ("ice_thickness_cm",  "Толщина льда",              "Добавьте наблюдения толщины льда (см)."),
        ("temp_anomaly",      "Аномалия температуры",      "Проверьте климатологию температуры на посту."),
        ("precip_sum_30d",    "Осадки за 30 дней",         "Проверьте, что precip_mm заполнен без пропусков."),
        ("precip_sum_7d",     "Осадки за 7 дней",          "Проверьте precip_mm за последние 7 дней."),
        ("delta_7d",          "Дельта уровня за 7 дней",   "Нужны water_level_cm минимум за 8 предыдущих дней."),
        ("level_vs_oya_pct",  "Уровень относительно ОЯ",   "Заполните low_oya/critical_oya в таблице stations."),
    ]
    for key, label, hint in checks:
        v = features.get(key)
        if v is None:
            gaps.append({"feature": key, "label": label, "hint": hint, "severity": "missing"})
    return gaps


def station_priority(limit: int = 20) -> List[Dict[str, Any]]:
    """Рейтинг станций по «горячности» на основе snapshot-кеша:
    hot_score = 0.5*confidence + 0.3*P(≥ОЯ) + 0.2*P(≥НЯ) + бонус за will_flood/high|critical.
    Возвращает топ-N.
    """
    import json as _json
    conn = _agent_db()
    try:
        # snapshot-таблица создаётся планировщиком; безопасно проверяем существование
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_snapshots'"
        ).fetchone()
        if not exists:
            return []
        rows = conn.execute(
            "SELECT river, post, risk_class, will_flood, confidence, computed_at, payload_json "
            "FROM agent_snapshots"
        ).fetchall()
    finally:
        conn.close()

    items: List[Dict[str, Any]] = []
    for r in rows:
        try:
            payload = _json.loads(r["payload_json"] or "{}")
        except Exception:
            payload = {}
        v = payload.get("verdict") or {}
        p_low = float(v.get("p_exceed_low") or 0)
        p_crit = float(v.get("p_exceed_crit") or 0)
        conf = float(r["confidence"] or 0)
        cls_bonus = {"critical": 0.3, "high": 0.2, "moderate": 0.05}.get(r["risk_class"] or "", 0.0)
        wf_bonus = 0.15 if r["will_flood"] else 0.0
        hot = 0.5 * conf + 0.3 * p_crit + 0.2 * p_low + cls_bonus + wf_bonus
        # Тренд: сравниваем последние 7 дней уровня
        trend = None
        try:
            daily = payload.get("forecast_daily") or []
            if len(daily) >= 2:
                first = daily[0].get("median")
                last = daily[-1].get("median")
                if first is not None and last is not None:
                    trend = round(float(last) - float(first), 1)
        except Exception:
            pass
        items.append({
            "river": r["river"],
            "post": r["post"],
            "risk_class": r["risk_class"],
            "will_flood": bool(r["will_flood"]),
            "confidence": round(conf, 3),
            "p_exceed_low": round(p_low, 3),
            "p_exceed_crit": round(p_crit, 3),
            "hot_score": round(hot, 3),
            "trend_cm": trend,
            "computed_at": r["computed_at"],
        })
    items.sort(key=lambda x: -x["hot_score"])
    return items[:limit]


def _cmp(v: float, op: str, t: float) -> bool:
    return (v >= t) if op == ">=" else (v <= t) if op == "<=" else \
           (v > t) if op == ">" else (v < t) if op == "<" else False



def _load_recent_features(river: str, post: str) -> Dict[str, Any]:
    """Возвращает признаки для «сегодня» + досчитывает то, чего нет в БД."""
    conn = hs.get_db()
    try:
        cur = conn.execute(
            """
            SELECT date, water_level_cm, temp_mean, precip_mm,
                   snow_pct_norm, ice_thickness_cm,
                   level_lag_1, level_lag_7,
                   delta_1d, delta_7d,
                   precip_sum_7d, precip_sum_14d,
                   precip_sum_30d, precip_sum_60d, precip_sum_90d,
                   temp_anomaly, level_vs_oya_pct,
                   day_of_year, month
            FROM daily_features
            WHERE river = ? AND post = ?
            ORDER BY date DESC
            LIMIT 120
            """,
            (river, post),
        )
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    if not rows:
        raise ValueError(f"Нет данных по станции: {river} / {post}")

    latest = rows[0]

    def _sum_precip(n: int) -> Optional[float]:
        vals = [r.get("precip_mm") for r in rows[:n] if r.get("precip_mm") is not None]
        return round(sum(vals), 2) if vals else None

    for key, n in (("precip_sum_30d", 30), ("precip_sum_60d", 60), ("precip_sum_90d", 90)):
        if latest.get(key) in (None, 0):
            calc = _sum_precip(n)
            if calc is not None:
                latest[key] = calc

    if latest.get("temp_anomaly") is None and latest.get("temp_mean") is not None:
        try:
            clim = hs.compute_climatology(river, post)
            doy = latest.get("day_of_year") or datetime.date.fromisoformat(
                str(latest["date"])[:10]
            ).timetuple().tm_yday
            clim_row = next((c for c in clim if c.get("day_of_year") == doy), None)
            if clim_row and clim_row.get("temp_mean") is not None:
                latest["temp_anomaly"] = round(
                    float(latest["temp_mean"]) - float(clim_row["temp_mean"]), 2
                )
        except Exception:
            pass

    if latest.get("level_vs_oya_pct") is None and latest.get("water_level_cm") is not None:
        st = hs.get_station_row(river, post) or {}
        crit = float(st.get("critical_oya") or 0)
        if crit > 0:
            latest["level_vs_oya_pct"] = round(
                float(latest["water_level_cm"]) / crit * 100.0, 2
            )

    return latest


def _last_year_ice_breakup(river: str, post: str, current_year: int) -> Optional[str]:
    """Дата вскрытия льда прошлого года: первый день после 1 марта,
    когда ice_thickness_cm падает до 0/NULL после ненулевых значений."""
    conn = hs.get_db()
    try:
        cur = conn.execute(
            """
            SELECT date, ice_thickness_cm
            FROM daily_features
            WHERE river = ? AND post = ?
              AND strftime('%Y', date) = ?
            ORDER BY date
            """,
            (river, post, str(current_year - 1)),
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    prev = None
    for r in rows:
        d = str(r["date"])[:10]
        thick = r["ice_thickness_cm"]
        try:
            month = int(d[5:7])
        except ValueError:
            continue
        if month < 3:
            prev = thick
            continue
        if prev is not None and prev > 0 and (thick is None or thick <= 0):
            return d
        prev = thick
    return None


def _forecast_daily(
    river: str, post: str, horizon: int
) -> Tuple[List[Dict[str, Any]], bool]:
    """Полный ряд прогноза (по дням) вместо одной точки-пика.

    Возвращает (points, has_model). points — список {date, median, q10, q90,
    q95, prob_warning, prob_danger}. Если модель не удалось загрузить, вернёт
    ([], False), а вызывающий код доиграет по климатологии.
    """
    try:
        st = hs.get_station_row(river, post) or {}
        low = float(st.get("low_oya") or 500)
        crit = float(st.get("critical_oya") or 650)
        latest = hs.get_latest_data_date(river, post)
        base = max(latest, datetime.date.today())  # прогноз всегда от сегодня
        predictor = hs.load_predictor(river, post)
        if not predictor:
            return [], False
        pts = hs.forecast_points_from_predictor(predictor, base, horizon, low, crit) or []
    except Exception:
        return [], False
    return pts, True


def _peak_of(points: List[Dict[str, Any]]) -> Tuple[Optional[float], Optional[str]]:
    if not points:
        return None, None
    peak = max(points, key=lambda p: p.get("median") or 0)
    return float(peak.get("median") or 0), peak.get("date")


def _critical_days(points: List[Dict[str, Any]], low: float, crit: float) -> List[Dict[str, Any]]:
    """Дни, где прогнозная медиана/верхний квантиль превышает ОЯ."""
    out: List[Dict[str, Any]] = []
    for p in points:
        med = p.get("median")
        q95 = p.get("q95") or p.get("q90") or med
        if med is None:
            continue
        level = "watch"
        reached = None
        if med >= crit or (q95 is not None and q95 >= crit):
            level = "critical"
            reached = "critical_oya"
        elif med >= low or (q95 is not None and q95 >= low):
            level = "warning"
            reached = "low_oya"
        else:
            continue
        out.append({
            "date": p.get("date"),
            "median": med,
            "q95": q95,
            "level": level,
            "reached": reached,
            "prob_warning": p.get("prob_warning"),
            "prob_danger": p.get("prob_danger"),
        })
    return out


def _oya_pressure_score(peak: Optional[float], low: float, crit: float) -> float:
    if peak is None or crit <= 0:
        return 0.0
    ratio = peak / crit
    if ratio >= 1.0:
        return 40.0
    if ratio >= 0.9:
        return 30.0
    if ratio >= 0.8:
        return 20.0
    if peak >= low:
        return 10.0
    return 0.0


def assess_flood_risk(
    river: str,
    post: str,
    horizon: int = 14,
    persist: bool = True,
) -> Dict[str, Any]:
    """Оценка риска паводка. Возвращает JSON-совместимый dict.

    Если persist=True и риск ≥ high или есть критические дни — сохраняет
    запись в agent_alerts и просит оператора ознакомиться.
    """
    st = hs.get_station_row(river, post)
    if not st:
        raise ValueError(f"Станция не найдена: {river} / {post}")

    low = float(st.get("low_oya") or 500)
    crit = float(st.get("critical_oya") or 650)

    features = _load_recent_features(river, post)
    obs_date = str(features.get("date"))[:10]
    try:
        month = int(obs_date[5:7])
        year = int(obs_date[:4])
    except ValueError:
        month = datetime.date.today().month
        year = datetime.date.today().year

    weights = _load_rule_weights(river, post)  # feedback-обучение

    drivers: List[Dict[str, Any]] = []
    rule_score = 0.0
    for rule in _RULES:
        val = features.get(rule["feature"])
        if val is None:
            continue
        try:
            fv = float(val)
        except (TypeError, ValueError):
            continue
        if rule["season"] and month not in rule["season"]:
            continue
        if not _cmp(fv, rule["op"], rule["threshold"]):
            continue
        w = weights.get(rule["id"], 1.0)
        weighted = rule["score"] * w
        drivers.append({
            "id": rule["id"],
            "label": rule["label"],
            "note": rule["note"],
            "feature": rule["feature"],
            "value": round(fv, 2),
            "threshold": rule["threshold"],
            "base_score": rule["score"],
            "weight": round(w, 2),
            "score": round(weighted, 2),
        })
        rule_score += weighted

    forecast_points, has_model = _forecast_daily(river, post, horizon)
    peak_median, peak_date = _peak_of(forecast_points)
    critical_days = _critical_days(forecast_points, low, crit)

    # --- Свежесть данных (data_through + штраф уверенности) ---
    data_through = hs.get_latest_data_date(river, post)
    today = datetime.date.today()
    data_lag_days = (today - data_through).days if data_through else None
    stale_penalty = 0.0
    stale_warning: Optional[str] = None
    if data_lag_days is not None and data_lag_days > 7:
        # Штраф растёт линейно: -0.05 за каждый день сверх 7, до -0.4 максимум
        stale_penalty = min(0.4, 0.05 * (data_lag_days - 7))
        stale_warning = (
            f"Данные устарели на {data_lag_days} дн. (последние: {data_through}). "
            f"Уверенность снижена на {int(round(stale_penalty * 100))}%."
        )

    prob_warn = max((p.get("prob_warning") or 0) for p in forecast_points) if forecast_points else None
    prob_dang = max((p.get("prob_danger") or 0) for p in forecast_points) if forecast_points else None

    # --- Квантильные сценарии (пессимистичный/медианный/оптимистичный) ---
    def _peak_by(key: str) -> Tuple[Optional[float], Optional[str]]:
        if not forecast_points:
            return None, None
        best = None
        for p in forecast_points:
            v = p.get(key) if p.get(key) is not None else p.get("median")
            if v is None:
                continue
            if best is None or v > best[0]:
                best = (float(v), p.get("date"))
        return best if best else (None, None)

    peak_q90, peak_q90_date = _peak_by("q90")
    peak_q10, peak_q10_date = _peak_by("q10")

    # --- Вердикт «будет паводок» — СТРОГИЙ порог ---
    # will_flood = P(q90 ≥ НЯ по горизонту) ≥ 0.7  (строго, без доп. условий по драйверам/prob)
    days_q90_over_low = sum(
        1 for p in forecast_points
        if (p.get("q90") is not None and p.get("q90") >= low)
    )
    days_q90_over_crit = sum(
        1 for p in forecast_points
        if (p.get("q90") is not None and p.get("q90") >= crit)
    )
    horizon_len = max(1, len(forecast_points))
    p_exceed_low = days_q90_over_low / horizon_len
    p_exceed_crit = days_q90_over_crit / horizon_len

    # Строгое правило для will_flood (ТЗ): только доля дней где q90 ≥ НЯ
    will_flood = p_exceed_low >= 0.7
    # Уровень для UI: red если и ОЯ выполнен на ≥0.7, yellow если только НЯ, иначе green
    verdict_red = p_exceed_crit >= 0.7
    verdict_yellow = will_flood and not verdict_red
    verdict_level = "red" if verdict_red else ("yellow" if verdict_yellow else "green")
    verdict_confidence = round(max(0.0, min(1.0, max(
        p_exceed_crit,
        p_exceed_low * 0.9,
        (prob_dang or 0),
        (prob_warn or 0) * 0.9,
    ) - stale_penalty)), 3)
    verdict_reason_parts: List[str] = []
    if p_exceed_crit >= 0.5:
        verdict_reason_parts.append(f"q90 ≥ ОЯ в {days_q90_over_crit} из {horizon_len} дней")
    if p_exceed_low >= 0.5:
        verdict_reason_parts.append(f"q90 ≥ НЯ в {days_q90_over_low} из {horizon_len} дней")
    if drivers:
        verdict_reason_parts.append(f"сработало правил: {len(drivers)}")
    if not verdict_reason_parts:
        verdict_reason_parts.append("сигналы ниже порогов")
    verdict = {
        "will_flood": bool(will_flood),
        "level": verdict_level,  # green | yellow | red
        "level_ru": {"green": "не ожидается", "yellow": "возможен", "red": "ожидается"}[verdict_level],
        "confidence": verdict_confidence,
        "p_exceed_low": round(p_exceed_low, 3),
        "p_exceed_crit": round(p_exceed_crit, 3),
        "reason": "; ".join(verdict_reason_parts),
    }

    scenarios = {
        "optimistic":  {"peak_cm": peak_q10,     "date": peak_q10_date,     "quantile": "q10"},
        "median":      {"peak_cm": peak_median,  "date": peak_date,         "quantile": "q50"},
        "pessimistic": {"peak_cm": peak_q90,     "date": peak_q90_date,     "quantile": "q90"},
    }

    oya_score = _oya_pressure_score(peak_median, low, crit)

    prob_score = 0.0
    if prob_dang is not None:
        prob_score = prob_dang * 30.0
    elif prob_warn is not None:
        prob_score = prob_warn * 15.0

    total = min(100.0, rule_score + oya_score + prob_score)
    risk_class_en, risk_class_ru = _classify(total)

    last_year_breakup = _last_year_ice_breakup(river, post, year)

    parts: List[str] = []
    parts.append(
        f"Вердикт: паводок {verdict['level_ru']} "
        f"(уверенность {int(round(verdict_confidence*100))}%). "
        f"Оценка риска на {horizon} дн. вперёд от {obs_date}: "
        f"{risk_class_ru} ({int(round(total))}/100)."
    )
    if peak_median is not None and peak_date:
        parts.append(
            f"Прогнозный пик уровня — {peak_median:.0f} см {peak_date} "
            f"(ОЯ низкий {low:.0f}, критический {crit:.0f})."
        )
    if critical_days:
        first_crit = next((c for c in critical_days if c["level"] == "critical"), None)
        first_warn = next((c for c in critical_days if c["level"] == "warning"), None)
        if first_crit:
            parts.append(
                f"⚠️ Достижение критического ОЯ ожидается {first_crit['date']} "
                f"(≈{first_crit['median']:.0f} см). Всего критических дней: "
                f"{sum(1 for c in critical_days if c['level'] == 'critical')}."
            )
        elif first_warn:
            parts.append(
                f"Достижение низкого ОЯ ожидается {first_warn['date']} "
                f"(≈{first_warn['median']:.0f} см)."
            )
    if drivers:
        top = ", ".join(f"{d['label']} ({d['feature']}={d['value']})" for d in drivers[:3])
        parts.append(f"Основные драйверы: {top}.")
    else:
        parts.append("Триггерные пороги правил не сработали.")
    if last_year_breakup:
        parts.append(f"Для контекста: в {year - 1} г. лёд вскрывался ~{last_year_breakup}.")
    if not has_model:
        parts.append("Обученной ML-модели нет — оценка основана на правилах и климатологии.")
    if stale_warning:
        parts.append(stale_warning)

    result: Dict[str, Any] = {
        "river": river,
        "post": post,
        "assessed_at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "observation_date": obs_date,
        "horizon_days": horizon,
        "risk_score": round(total, 1),
        "risk_class": risk_class_en,
        "risk_class_ru": risk_class_ru,
        "has_model": has_model,
        "data_through": data_through.isoformat() if data_through else None,
        "data_lag_days": data_lag_days,
        "stale_warning": stale_warning,
        "thresholds": {"low_oya": low, "critical_oya": crit},
        "forecast_peak": {
            "level_cm": peak_median,
            "date": peak_date,
            "prob_warning": prob_warn,
            "prob_danger": prob_dang,
        },
        "forecast_daily": [
            {k: p.get(k) for k in ("date", "median", "q10", "q90", "q95", "prob_warning", "prob_danger")}
            for p in forecast_points
        ],
        "critical_days": critical_days,
        "features": {
            "water_level_cm": features.get("water_level_cm"),
            "snow_pct_norm": features.get("snow_pct_norm"),
            "ice_thickness_cm": features.get("ice_thickness_cm"),
            "temp_mean": features.get("temp_mean"),
            "temp_anomaly": features.get("temp_anomaly"),
            "precip_sum_7d": features.get("precip_sum_7d"),
            "precip_sum_30d": features.get("precip_sum_30d"),
            "precip_sum_60d": features.get("precip_sum_60d"),
            "precip_sum_90d": features.get("precip_sum_90d"),
            "delta_7d": features.get("delta_7d"),
            "level_vs_oya_pct": features.get("level_vs_oya_pct"),
        },
        "drivers": sorted(drivers, key=lambda d: -d["score"]),
        "verdict": verdict,
        "scenarios": scenarios,
        "data_gaps": _data_gaps(features),
        "context": {
            "last_year_ice_breakup": last_year_breakup,
            "season_month": month,
            "rule_weights_applied": weights,
        },
        "narrative": " ".join(parts),
        "requires_ack": False,
        "alert_id": None,
    }

    if persist and (risk_class_en in ("high", "critical") or any(
        c["level"] == "critical" for c in critical_days
    )):
        alert_id = _save_alert(river, post, result)
        result["alert_id"] = alert_id
        result["requires_ack"] = True
        result["narrative"] += (
            f" Создан алерт №{alert_id} — просьба ознакомиться в профиле станции."
        )

    return result


# ---------------------------------------------------------------------------
# Публичный API работы с алертами и обратной связью
# ---------------------------------------------------------------------------

def _save_alert(river: str, post: str, result: Dict[str, Any]) -> int:
    conn = _agent_db()
    try:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO agent_alerts (
                river, post, created_at, observation_date, horizon_days,
                risk_score, risk_class, peak_level_cm, peak_date,
                critical_days_json, drivers_json, narrative
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                river, post,
                datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
                result.get("observation_date"),
                result.get("horizon_days"),
                result.get("risk_score"),
                result.get("risk_class"),
                (result.get("forecast_peak") or {}).get("level_cm"),
                (result.get("forecast_peak") or {}).get("date"),
                json.dumps(result.get("critical_days") or [], ensure_ascii=False),
                json.dumps(result.get("drivers") or [], ensure_ascii=False),
                result.get("narrative"),
            ),
        )
        conn.commit()
        new_id = int(cur.lastrowid or 0)
        if new_id == 0:
            # запись уже существовала (UNIQUE конфликт) — вернуть существующий id
            row = conn.execute(
                "SELECT id FROM agent_alerts WHERE river=? AND post=? "
                "AND observation_date IS ? AND horizon_days IS ? "
                "ORDER BY id DESC LIMIT 1",
                (river, post, result.get("observation_date"), result.get("horizon_days")),
            ).fetchone()
            new_id = int(row["id"]) if row else 0
        return new_id
    finally:
        conn.close()


def list_alerts(
    river: Optional[str] = None,
    post: Optional[str] = None,
    only_pending: bool = False,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    where: List[str] = []
    args: List[Any] = []
    if river:
        where.append("river = ?"); args.append(river)
    if post:
        where.append("post = ?"); args.append(post)
    if only_pending:
        where.append("acknowledged = 0")
    sql = "SELECT * FROM agent_alerts"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    conn = _agent_db()
    try:
        rows = conn.execute(sql, args).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("critical_days_json", "drivers_json"):
            if d.get(k):
                try:
                    d[k[:-5]] = json.loads(d[k])
                except Exception:
                    d[k[:-5]] = []
            d.pop(k, None)
        out.append(d)
    return out


def acknowledge_alert(alert_id: int, user: Optional[str] = None) -> Dict[str, Any]:
    conn = _agent_db()
    try:
        now = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
        cur = conn.execute(
            "UPDATE agent_alerts SET acknowledged=1, acknowledged_at=?, acknowledged_by=? "
            "WHERE id=?",
            (now, user, int(alert_id)),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise ValueError(f"Алерт #{alert_id} не найден")
        row = conn.execute(
            "SELECT * FROM agent_alerts WHERE id=?", (int(alert_id),)
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else {"id": alert_id, "acknowledged": True}


def record_feedback(
    river: str,
    post: str,
    verdict: str,
    alert_id: Optional[int] = None,
    actual_class: Optional[str] = None,
    comment: Optional[str] = None,
) -> Dict[str, Any]:
    """Поощрение/наказание модели.

    - reward  → веса драйверов связанного алерта +0.1;
    - penalty → −0.15 (правила стреляли зря).
    Вес зажат в [0.1..2.5].
    """
    verdict = (verdict or "").lower().strip()
    if verdict not in ("reward", "penalty"):
        raise ValueError("verdict должен быть 'reward' или 'penalty'")
    river = (river or "").strip()
    post = (post or "").strip()
    if not river or not post:
        raise ValueError("river/post не могут быть пустыми")

    conn = _agent_db()
    try:
        rule_ids: List[str] = []
        if alert_id is not None:
            row = conn.execute(
                "SELECT drivers_json FROM agent_alerts WHERE id=?", (int(alert_id),)
            ).fetchone()
            if row and row["drivers_json"]:
                try:
                    for d in json.loads(row["drivers_json"]):
                        rid = d.get("id")
                        if rid:
                            rule_ids.append(rid)
                except Exception:
                    pass

        delta = 0.1 if verdict == "reward" else -0.15
        for rid in rule_ids:
            _bump_rule_weight(conn, river, post, rid, delta, verdict)

        payload = {
            "alert_id": alert_id,
            "actual_class": actual_class,
            "rules_updated": rule_ids,
            "delta_per_rule": delta,
        }
        now = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
        cur = conn.execute(
            "INSERT INTO agent_feedback (river, post, alert_id, created_at, verdict, "
            "actual_class, comment, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (river, post, alert_id, now, verdict, actual_class, comment,
             json.dumps(payload, ensure_ascii=False)),
        )
        conn.commit()
        fb_id = int(cur.lastrowid)

        new_weights = {
            r["rule_id"]: {
                "weight": float(r["weight"]),
                "n_reward": int(r["n_reward"]),
                "n_penalty": int(r["n_penalty"]),
            }
            for r in conn.execute(
                "SELECT rule_id, weight, n_reward, n_penalty FROM agent_rule_weights "
                "WHERE river=? AND post=?",
                (river, post),
            ).fetchall()
        }
    finally:
        conn.close()

    return {
        "feedback_id": fb_id,
        "verdict": verdict,
        "rules_updated": rule_ids,
        "delta_per_rule": delta,
        "current_weights": new_weights,
    }



# ---------------------------------------------------------------------------
# Аналитика по посту (для вкладки «Аналитика» и /api/agent/history)
# ---------------------------------------------------------------------------

def _linreg_slope(xs: List[float], ys: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 2:
        return None
    sx = sum(xs); sy = sum(ys)
    sxx = sum(x * x for x in xs); sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if denom == 0:
        return None
    return (n * sxy - sx * sy) / denom


def _quantile_sorted(arr: List[float], p: float) -> float:
    n = len(arr)
    if n == 1:
        return arr[0]
    k = (n - 1) * p
    f = int(k); c = min(f + 1, n - 1)
    return arr[f] + (arr[c] - arr[f]) * (k - f)


def compute_station_analytics(river: str, post: str, years: int = 10) -> Dict[str, Any]:
    """Аналитика поста: пики по годам, климатология уровня, лёд, тренд, дни превышений."""
    conn = _agent_db()
    try:
        st = conn.execute(
            "SELECT low_oya, critical_oya FROM stations WHERE river=? AND post=? LIMIT 1",
            (river, post),
        ).fetchone()
        low = float(st["low_oya"]) if st and st["low_oya"] is not None else None
        crit = float(st["critical_oya"]) if st and st["critical_oya"] is not None else None

        rows = conn.execute(
            "SELECT date, water_level_cm, ice_event, day_of_year "
            "FROM daily_features WHERE river=? AND post=? AND water_level_cm IS NOT NULL "
            "ORDER BY date",
            (river, post),
        ).fetchall()
        if not rows:
            raise ValueError(f"Нет данных для {river}/{post}")

        peaks: Dict[int, Dict[str, Any]] = {}
        year_days_low: Dict[int, int] = {}
        year_days_crit: Dict[int, int] = {}
        by_doy: Dict[int, List[float]] = {}
        fu: Dict[int, int] = {}
        bu: Dict[int, int] = {}

        for r in rows:
            d = r["date"]
            if not d or len(d) < 4:
                continue
            try:
                y = int(d[:4])
            except Exception:
                continue
            lvl = float(r["water_level_cm"])
            p = peaks.get(y)
            if p is None or lvl > p["peak_cm"]:
                peaks[y] = {"peak_cm": lvl, "peak_date": d}
            if low is not None and lvl >= low:
                year_days_low[y] = year_days_low.get(y, 0) + 1
            if crit is not None and lvl >= crit:
                year_days_crit[y] = year_days_crit.get(y, 0) + 1
            doy = r["day_of_year"]
            if doy is not None:
                by_doy.setdefault(int(doy), []).append(lvl)
            ev = r["ice_event"]
            if ev == "freeze_up" and y not in fu and doy is not None:
                fu[y] = int(doy)
            if ev == "break_up" and y not in bu and doy is not None:
                bu[y] = int(doy)

        all_years = sorted(peaks.keys())
        selected_years = all_years[-years:] if years > 0 else all_years
        peaks_by_year = [
            {
                "year": y,
                "peak_cm": round(peaks[y]["peak_cm"], 1),
                "peak_date": peaks[y]["peak_date"],
                "exceeded_low": low is not None and peaks[y]["peak_cm"] >= low,
                "exceeded_crit": crit is not None and peaks[y]["peak_cm"] >= crit,
                "days_over_low": year_days_low.get(y, 0),
                "days_over_crit": year_days_crit.get(y, 0),
            }
            for y in selected_years
        ]

        trend = _linreg_slope(
            [float(y) for y in all_years],
            [peaks[y]["peak_cm"] for y in all_years],
        )

        climatology_level = []
        for doy in sorted(by_doy):
            arr = sorted(by_doy[doy])
            if not arr:
                continue
            climatology_level.append({
                "doy": doy,
                "p10": round(_quantile_sorted(arr, 0.10), 1),
                "p50": round(_quantile_sorted(arr, 0.50), 1),
                "p90": round(_quantile_sorted(arr, 0.90), 1),
                "n": len(arr),
            })

        avg_fu = sum(fu.values()) / len(fu) if fu else None
        avg_bu = sum(bu.values()) / len(bu) if bu else None
        ice_regime = {
            "avg_freeze_up_doy": round(avg_fu, 1) if avg_fu is not None else None,
            "avg_break_up_doy": round(avg_bu, 1) if avg_bu is not None else None,
            "n_freeze_up_years": len(fu),
            "n_break_up_years": len(bu),
        }

        exceedance_days_per_year = [
            {
                "year": y,
                "days_over_low": year_days_low.get(y, 0),
                "days_over_crit": year_days_crit.get(y, 0),
            }
            for y in selected_years
        ]

        return {
            "river": river,
            "post": post,
            "low_oya": low,
            "critical_oya": crit,
            "peaks_by_year": peaks_by_year,
            "climatology_level": climatology_level,
            "ice_regime": ice_regime,
            "exceedance_days_per_year": exceedance_days_per_year,
            "trend_peak_cm_per_year": round(trend, 3) if trend is not None else None,
            "n_years_total": len(all_years),
        }
    finally:
        conn.close()



def compute_station_history(river: str, post: str, days: int = 180) -> Dict[str, Any]:
    """Последние N дней: daily-серия + отметки превышений НЯ/ОЯ."""
    conn = _agent_db()
    try:
        st = conn.execute(
            "SELECT low_oya, critical_oya FROM stations WHERE river=? AND post=? LIMIT 1",
            (river, post),
        ).fetchone()
        low = float(st["low_oya"]) if st and st["low_oya"] is not None else None
        crit = float(st["critical_oya"]) if st and st["critical_oya"] is not None else None

        rows = conn.execute(
            "SELECT date, water_level_cm, temp_mean, precip_mm, ice_event "
            "FROM daily_features WHERE river=? AND post=? "
            "ORDER BY date DESC LIMIT ?",
            (river, post, int(days)),
        ).fetchall()
        if not rows:
            raise ValueError(f"Нет данных для {river}/{post}")

        rows = list(reversed(rows))
        daily = []
        warnings: List[Dict[str, Any]] = []
        critical: List[Dict[str, Any]] = []
        for r in rows:
            lvl = float(r["water_level_cm"]) if r["water_level_cm"] is not None else None
            daily.append({
                "date": r["date"],
                "level_cm": lvl,
                "temp_mean": float(r["temp_mean"]) if r["temp_mean"] is not None else None,
                "precip_mm": float(r["precip_mm"]) if r["precip_mm"] is not None else None,
                "ice_event": r["ice_event"],
            })
            if lvl is not None:
                if crit is not None and lvl >= crit:
                    critical.append({"date": r["date"], "level_cm": lvl,
                                     "over_crit_cm": round(lvl - crit, 1)})
                elif low is not None and lvl >= low:
                    warnings.append({"date": r["date"], "level_cm": lvl,
                                     "over_low_cm": round(lvl - low, 1)})

        return {
            "river": river,
            "post": post,
            "low_oya": low,
            "critical_oya": crit,
            "days": len(daily),
            "daily": daily,
            "warnings": warnings,
            "critical": critical,
        }
    finally:
        conn.close()

