# -*- coding: utf-8 -*-
"""
Бэкфилл производных фич в `daily_features`.

Заполняет колонки, которые исторически лежали как NULL/0:
  - is_summer          (месяц ∈ {6,7,8})
  - temp_anomaly       (temp_mean − климатология по дню года на посту)
  - level_vs_oya_pct   (water_level_cm / critical_oya * 100)
  - precip_sum_30d/60d/90d (rolling суммы precip_mm)
  - ice_event          (freeze_up/ice_cover/break_up/open_water — эвристика)

Идемпотентно: обновляет только NULL/0. Можно запускать повторно.

Запуск:
    python backfill_features.py
    python backfill_features.py --river "Лена" --post "Якутск"
    python backfill_features.py --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_SCRIPT_DIR = Path(__file__).resolve().parent
_DB_PATH = _SCRIPT_DIR.parent / "data" / "ml_features.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("backfill")


def _stations(conn: sqlite3.Connection, river: Optional[str], post: Optional[str]) -> List[Tuple[str, str, float, float]]:
    q = "SELECT river, post, COALESCE(low_oya,0), COALESCE(critical_oya,0) FROM stations"
    args: List[str] = []
    where: List[str] = []
    if river:
        where.append("river = ?"); args.append(river)
    if post:
        where.append("post = ?"); args.append(post)
    if where:
        q += " WHERE " + " AND ".join(where)
    return [(r[0], r[1], float(r[2] or 0), float(r[3] or 0)) for r in conn.execute(q, args).fetchall()]


def _climatology_temp(conn: sqlite3.Connection, river: str, post: str) -> Dict[int, float]:
    """Средняя temp_mean по day_of_year для конкретного поста."""
    rows = conn.execute(
        "SELECT day_of_year, AVG(temp_mean) FROM daily_features "
        "WHERE river=? AND post=? AND temp_mean IS NOT NULL "
        "GROUP BY day_of_year",
        (river, post),
    ).fetchall()
    return {int(r[0]): float(r[1]) for r in rows if r[0] is not None and r[1] is not None}


def _ice_event(month: int, tmean: Optional[float]) -> Optional[str]:
    """Эвристика ледового режима для Якутии.
       dec/jan/feb + t<-10 → ice_cover
       oct/nov            → freeze_up
       apr/may + t>-2     → break_up
       jun-sep            → open_water
       mar                → ice_cover
    """
    if month in (12, 1, 2):
        if tmean is None or tmean < -10:
            return "ice_cover"
    if month in (10, 11):
        return "freeze_up"
    if month in (4, 5) and (tmean is None or tmean > -2):
        return "break_up"
    if month in (6, 7, 8, 9):
        return "open_water"
    if month == 3:
        return "ice_cover"
    return None


def _backfill_one(conn: sqlite3.Connection, river: str, post: str,
                  low: float, crit: float, dry: bool) -> Dict[str, int]:
    """Обновляет фичи одного поста. Возвращает счётчики обновлений по колонкам."""
    clim = _climatology_temp(conn, river, post)

    rows = conn.execute(
        """
        SELECT rowid, date, month, day_of_year, water_level_cm, temp_mean, precip_mm,
               is_summer, temp_anomaly, level_vs_oya_pct, ice_event,
               precip_sum_30d, precip_sum_60d, precip_sum_90d
        FROM daily_features
        WHERE river=? AND post=?
        ORDER BY date
        """,
        (river, post),
    ).fetchall()

    if not rows:
        return {}

    precip = [float(r["precip_mm"]) if r["precip_mm"] is not None else 0.0 for r in rows]

    def _roll(idx: int, window: int) -> float:
        lo = max(0, idx - window + 1)
        return round(sum(precip[lo: idx + 1]), 2)

    stats: Dict[str, int] = {
        "is_summer": 0, "temp_anomaly": 0, "level_vs_oya_pct": 0,
        "ice_event": 0, "precip_sum_30d": 0, "precip_sum_60d": 0, "precip_sum_90d": 0,
    }
    updates: List[tuple] = []

    for i, r in enumerate(rows):
        month = r["month"]
        doy = r["day_of_year"]
        tmean = r["temp_mean"]
        level = r["water_level_cm"]

        set_parts: List[str] = []
        values: List = []

        if r["is_summer"] is None and month is not None:
            set_parts.append("is_summer=?")
            values.append(1 if int(month) in (6, 7, 8) else 0)
            stats["is_summer"] += 1

        if (r["temp_anomaly"] is None or r["temp_anomaly"] == 0) and tmean is not None and doy is not None:
            base = clim.get(int(doy))
            if base is not None:
                set_parts.append("temp_anomaly=?")
                values.append(round(float(tmean) - base, 2))
                stats["temp_anomaly"] += 1

        if (r["level_vs_oya_pct"] is None or r["level_vs_oya_pct"] == 0) and level is not None and crit > 0:
            set_parts.append("level_vs_oya_pct=?")
            values.append(round(float(level) / crit * 100.0, 2))
            stats["level_vs_oya_pct"] += 1

        if r["ice_event"] is None and month is not None:
            ev = _ice_event(int(month), tmean)
            if ev is not None:
                set_parts.append("ice_event=?")
                values.append(ev)
                stats["ice_event"] += 1

        for col, win in (("precip_sum_30d", 30), ("precip_sum_60d", 60), ("precip_sum_90d", 90)):
            if r[col] is None or r[col] == 0:
                set_parts.append(f"{col}=?")
                values.append(_roll(i, win))
                stats[col] += 1

        if set_parts:
            values.append(r["rowid"])
            updates.append((f"UPDATE daily_features SET {', '.join(set_parts)} WHERE rowid=?", values))

    if not dry and updates:
        conn.execute("BEGIN")
        try:
            for sql, args in updates:
                conn.execute(sql, args)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return stats


def main() -> int:
    p = argparse.ArgumentParser(description="Бэкфилл производных фич в daily_features.")
    p.add_argument("--river", help="Ограничить одной рекой")
    p.add_argument("--post", help="Ограничить одним постом (нужен --river)")
    p.add_argument("--dry-run", action="store_true", help="Не писать, только отчёт")
    p.add_argument("--db", default=str(_DB_PATH), help="Путь к ml_features.db")
    args = p.parse_args()

    if not Path(args.db).exists():
        log.error("БД не найдена: %s", args.db)
        return 2

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    stations = _stations(conn, args.river, args.post)
    log.info("Постов к обработке: %d (dry_run=%s)", len(stations), args.dry_run)

    grand: Dict[str, int] = {}
    for i, (r, po, low, crit) in enumerate(stations, 1):
        try:
            s = _backfill_one(conn, r, po, low, crit, dry=args.dry_run)
        except Exception as e:
            log.error("[%d/%d] %s / %s — FAIL: %s", i, len(stations), r, po, e)
            continue
        if not s:
            continue
        for k, v in s.items():
            grand[k] = grand.get(k, 0) + v
        if i % 20 == 0 or i == len(stations):
            log.info("[%d/%d] %s / %s: %s", i, len(stations), r, po,
                     ", ".join(f"{k}=+{v}" for k, v in s.items() if v))

    conn.close()
    log.info("Итого обновлено: %s", ", ".join(f"{k}=+{v}" for k, v in grand.items()) or "(ничего)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
