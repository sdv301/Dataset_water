# -*- coding: utf-8 -*-
"""
Точечный backfill daily_features.snow_pct_norm из CSV снегозапасов.

БЕЗ DROP / пересборки БД: не трогает stations, training_*, agent_*, прочие колонки.
Только UPDATE snow_pct_norm.

CSV dannie_снегозапасы.csv хранит диапазоны («110-130», «<70», «>130»);
старый safe_float() давал NaN → в БД ~0.2% non-null snow.

Запуск:
    python backfill_snow.py
    python backfill_snow.py --dry-run
    python backfill_snow.py --self-test
"""
from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
_DEFAULT_DB = _PROJECT_ROOT / "data" / "ml_features.db"
_DEFAULT_CSV = (
    _PROJECT_ROOT / "Реки" / "данные январь" / "export" / "dannie_снегозапасы.csv"
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("backfill_snow")

# Притоки → бассейн CSV (если у реки нет собственной строки)
RIVER_TO_SNOW_BASIN: Dict[str, str] = {
    "Витим": "Лена",
    "Олёкма": "Лена",
    "Олекма": "Лена",
    "Пеледуй": "Лена",
    "Нюя": "Лена",
    "Суола": "Лена",
    "Б.Патома": "Лена",
    "Чара": "Лена",
    "Мая": "Алдан",
    "Учур": "Алдан",
    "Тимптон": "Алдан",
    "Чульман": "Алдан",
    "Аллах-Юнь": "Алдан",
    "Б.Нимныр": "Алдан",
    "Б.Хатами": "Алдан",
    "Б.Хатыма": "Алдан",
    "Б.Ыллымах": "Алдан",
    "В.Нерюнга": "Алдан",
    "Иенгра": "Алдан",
    "Томпо": "Алдан",
    "Якокит": "Алдан",
    "Марха": "Вилюй",
    "Моркока": "Вилюй",
    "Чона": "Вилюй",
    "Ахтаранда": "Вилюй",
    "Улахан-Ботуобуйа": "Вилюй",
    "Улахан-Эдьек": "Вилюй",
    "Чуркуо": "Вилюй",
    "Шестаковка": "Вилюй",
    "Тээнэ": "Вилюй",
    "Адыча": "Яна",
    "Бытантай": "Яна",
    "Дулгалах": "Яна",
    "Сартанг": "Яна",
    "Бролог": "Яна",
    "Агаякан": "Индигирка",
    "Эльги": "Индигирка",
    "Б.Артык-Юрях": "Индигирка",
    "Радио-Уруйэтэ": "Индигирка",
    "Дьэкиндэ": "Индигирка",
    "Ичода": "Индигирка",
    "Ясачная": "Колыма",
    "Берёзовка": "Колыма",
    "Алазея": "Колыма",
    "Оленек": "Оленёк",
    "М.Куонапка": "Оленёк",
}


def parse_snow_pct(val) -> float:
    """Парсер % от нормы: диапазоны/операторы → число (как prepare_ml_data)."""
    if val is None:
        return float("nan")
    try:
        if pd.isna(val):
            return float("nan")
    except Exception:
        pass
    s = str(val).strip()
    if not s:
        return float("nan")
    s = (
        s.replace(",", ".")
        .replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace(" ", "")
    )
    low = s.lower()
    if low in ("-", "*", "#н/д", "#н/д.", "н/д", "nan", "none", "null", "."):
        return float("nan")
    try:
        return float(s)
    except ValueError:
        pass
    m = re.match(r"^(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)$", s)
    if m:
        return (float(m.group(1)) + float(m.group(2))) / 2.0
    m = re.match(r"^<(\d+(?:\.\d+)?)$", s)
    if m:
        bound = float(m.group(1))
        if abs(bound - 70.0) < 1e-9:
            return 60.0
        return round(0.85 * bound, 2)
    m = re.match(r"^>(\d+(?:\.\d+)?)$", s)
    if m:
        bound = float(m.group(1))
        if abs(bound - 130.0) < 1e-9:
            return 145.0
        if abs(bound - 200.0) < 1e-9:
            return 220.0
        return round(bound * 1.1, 2)
    return float("nan")



def normalize_basin(name: str) -> str:
    s = str(name or "").strip()
    if s.lower().startswith("р."):
        s = s[2:].strip()
    key = s.lower().replace("ё", "е")
    canon = {
        "лена": "Лена",
        "алдан": "Алдан",
        "амга": "Амга",
        "вилюй": "Вилюй",
        "индигирка": "Индигирка",
        "колыма": "Колыма",
        "яна": "Яна",
        "анабар": "Анабар",
        "оленек": "Оленёк",
    }
    return canon.get(key, s)


def self_test() -> int:
    cases = [
        ("110-130", 120.0),
        ("70-90", 80.0),
        ("90-110", 100.0),
        ("130-200", 165.0),
        ("<70", 60.0),
        (">130", 145.0),
        (">200", 220.0),
        ("100", 100.0),
        ("80", 80.0),
        ("-", float("nan")),
        ("", float("nan")),
        ("#н/д", float("nan")),
        (None, float("nan")),
        ("165", 165.0),
        ("<200", 170.0),
    ]
    failed = 0
    for raw, exp in cases:
        got = parse_snow_pct(raw)
        if exp != exp:
            ok = got != got
        else:
            ok = abs(got - exp) < 1e-6
        status = "OK" if ok else "FAIL"
        if not ok:
            failed += 1
        print(f"  {status}: parse_snow_pct({raw!r}) -> {got!r} (expected {exp!r})")
    print(f"self-test: {len(cases) - failed}/{len(cases)} passed")
    return 1 if failed else 0


def load_snow_daily(csv_path: Path) -> pd.DataFrame:
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV не найден: {csv_path}")
    df = pd.read_csv(csv_path, encoding="utf-8")
    col_map = {
        "Бассейн рек": "river_basin",
        "Год": "year",
        "Месяц": "month",
        "День": "day",
        "в % от нормы": "snow_pct_norm",
    }
    missing = [c for c in col_map if c not in df.columns]
    if missing:
        raise ValueError(f"В CSV нет колонок: {missing}; есть {list(df.columns)}")
    df = df.rename(columns=col_map)
    df["date"] = pd.to_datetime(df[["year", "month", "day"]], errors="coerce")
    df = df.dropna(subset=["date"])
    df["snow_pct_norm"] = df["snow_pct_norm"].apply(parse_snow_pct)
    df["basin"] = df["river_basin"].apply(normalize_basin)
    daily = (
        df.dropna(subset=["snow_pct_norm"])
        .groupby(["basin", "date"], as_index=False)["snow_pct_norm"]
        .mean()
    )
    log.info(
        "CSV: %s строк → parsed non-null daily basin-dates: %s (basins=%s)",
        len(df),
        len(daily),
        sorted(daily["basin"].unique().tolist()),
    )
    return daily


def resolve_basin_for_river(river: str, known_basins: set) -> Optional[str]:
    if not river or river == "Метеостанции":
        return None
    n = normalize_basin(river)
    if n in known_basins:
        return n
    mapped = RIVER_TO_SNOW_BASIN.get(river) or RIVER_TO_SNOW_BASIN.get(n)
    if mapped and mapped in known_basins:
        return mapped
    rl = river.lower().replace("ё", "е")
    for b in known_basins:
        bl = b.lower().replace("ё", "е")
        if bl in rl or rl in bl:
            return b
    return None


def snow_series_for_river(
    snow_daily: pd.DataFrame, basin: str, dates: pd.DatetimeIndex
) -> pd.Series:
    """Снег по датам поста.

    CSV — редкие съёмки (10/20/28/30), daily_features — дырявый календарь.
    Алгоритм:
      1) непрерывный range (с запасом по обеим сторонам)
      2) join съёмок
      3) ffill/bfill с большим лимитом (между съёмками до ~2 мес)
      4) вне ноя–апр → NaN (не тащим через лето)
      5) reindex на даты поста
    """
    if dates is None or len(dates) == 0:
        return pd.Series(dtype=float)
    sub = snow_daily[snow_daily["basin"] == basin][["date", "snow_pct_norm"]].copy()
    if sub.empty:
        return pd.Series(
            index=pd.DatetimeIndex(dates), data=float("nan"), name="snow_pct_norm"
        )
    sub["date"] = pd.to_datetime(sub["date"]).dt.normalize()
    dmin = pd.Timestamp(dates.min()).normalize()
    dmax = pd.Timestamp(dates.max()).normalize()
    cal_start = min(dmin, sub["date"].min()) - pd.Timedelta(days=5)
    cal_end = max(dmax, sub["date"].max()) + pd.Timedelta(days=5)
    full = pd.DataFrame({"date": pd.date_range(cal_start, cal_end, freq="D")})
    full = full.merge(sub.drop_duplicates("date"), on="date", how="left")
    full = full.sort_values("date")
    # между зимними съёмками до ~2 мес; через лето режем сезоном ниже
    full["snow_pct_norm"] = full["snow_pct_norm"].ffill(limit=100).bfill(limit=60)
    m = full["date"].dt.month
    full.loc[~m.isin([11, 12, 1, 2, 3, 4]), "snow_pct_norm"] = float("nan")
    target = pd.DatetimeIndex(pd.to_datetime(dates)).normalize()
    out = full.set_index("date")["snow_pct_norm"].reindex(target)
    out.index = pd.DatetimeIndex(dates)
    return out



def count_snow(conn: sqlite3.Connection) -> Tuple[int, int]:
    total = conn.execute("SELECT COUNT(*) FROM daily_features").fetchone()[0]
    nn = conn.execute(
        "SELECT COUNT(*) FROM daily_features WHERE snow_pct_norm IS NOT NULL"
    ).fetchone()[0]
    return int(total), int(nn)



def backfill(
    db_path: Path,
    csv_path: Path,
    dry_run: bool = False,
    only_null: bool = False,
) -> dict:
    snow_daily = load_snow_daily(csv_path)
    known = set(snow_daily["basin"].unique())
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        before_total, before_nn = count_snow(conn)
        log.info(
            "DB before: rows=%s snow_non_null=%s (%.2f%%)",
            before_total,
            before_nn,
            100.0 * before_nn / max(before_total, 1),
        )
        rivers = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT river FROM daily_features ORDER BY 1"
            ).fetchall()
        ]
        mapping = {riv: resolve_basin_for_river(riv, known) for riv in rivers}
        mapped_n = sum(1 for v in mapping.values() if v)
        log.info("Реки в БД: %s, с basin-map: %s", len(rivers), mapped_n)
        for riv, b in sorted(mapping.items()):
            log.info("  map %s -> %s", riv, b or "-")

        updates: List[Tuple[float, str, str]] = []
        rivers_updated = 0
        for riv, basin in mapping.items():
            if not basin:
                continue
            rows = conn.execute(
                "SELECT DISTINCT date FROM daily_features WHERE river=? ORDER BY date",
                (riv,),
            ).fetchall()
            if not rows:
                continue
            dates = pd.to_datetime([r[0] for r in rows], errors="coerce")
            dates = pd.DatetimeIndex(sorted(dates.dropna().unique()))
            series = snow_series_for_river(snow_daily, basin, dates)
            n_local = 0
            for dt, val in series.items():
                if val is None or (isinstance(val, float) and np.isnan(val)):
                    continue
                date_str = pd.Timestamp(dt).strftime("%Y-%m-%d")
                updates.append((float(val), riv, date_str))
                n_local += 1
            if n_local:
                rivers_updated += 1
                log.info("  %s (%s): %s date-values", riv, basin, n_local)
        log.info("Всего UPDATE-ключей (river,date): %s", len(updates))
        if dry_run:
            log.info("DRY-RUN: запись пропущена")
            return {
                "before_total": before_total,
                "before_nn": before_nn,
                "updates": len(updates),
                "rivers_updated": rivers_updated,
                "dry_run": True,
            }
        # continued below
        return _apply_updates(conn, updates, before_total, before_nn, rivers_updated, only_null)
    finally:
        conn.close()



def _apply_updates(conn, updates, before_total, before_nn, rivers_updated, only_null):
    conn.execute("DROP TABLE IF EXISTS _snow_backfill_tmp")
    conn.execute(
        "CREATE TEMP TABLE _snow_backfill_tmp ("
        "river TEXT NOT NULL, date TEXT NOT NULL, snow REAL NOT NULL, "
        "PRIMARY KEY(river, date))"
    )
    conn.executemany(
        "INSERT OR REPLACE INTO _snow_backfill_tmp(river, date, snow) VALUES (?,?,?)",
        [(r, d, s) for s, r, d in updates],
    )
    if only_null:
        sql = (
            "UPDATE daily_features AS df "
            "SET snow_pct_norm = ("
            "  SELECT t.snow FROM _snow_backfill_tmp t "
            "  WHERE t.river = df.river AND t.date = df.date"
            ") "
            "WHERE snow_pct_norm IS NULL "
            "AND EXISTS ("
            "  SELECT 1 FROM _snow_backfill_tmp t "
            "  WHERE t.river = df.river AND t.date = df.date"
            ")"
        )
    else:
        sql = (
            "UPDATE daily_features AS df "
            "SET snow_pct_norm = ("
            "  SELECT t.snow FROM _snow_backfill_tmp t "
            "  WHERE t.river = df.river AND t.date = df.date"
            ") "
            "WHERE EXISTS ("
            "  SELECT 1 FROM _snow_backfill_tmp t "
            "  WHERE t.river = df.river AND t.date = df.date"
            ")"
        )
    cur = conn.execute(sql)
    changed = cur.rowcount
    conn.commit()
    after_total, after_nn = count_snow(conn)
    log.info(
        "DB after: rows=%s snow_non_null=%s (%.2f%%); rows touched~%s",
        after_total,
        after_nn,
        100.0 * after_nn / max(after_total, 1),
        changed,
    )
    for riv in ("Лена", "Алдан", "Вилюй"):
        sample = conn.execute(
            "SELECT date, ROUND(AVG(snow_pct_norm),1) AS s, COUNT(*) AS n "
            "FROM daily_features "
            "WHERE river=? AND snow_pct_norm IS NOT NULL "
            "AND substr(date,6,2) IN ('03','04','05','11') "
            "GROUP BY date ORDER BY date DESC LIMIT 5",
            (riv,),
        ).fetchall()
        log.info("sample %s spring/nov: %s", riv, [dict(x) for x in sample])
    return {
        "before_total": before_total,
        "before_nn": before_nn,
        "after_total": after_total,
        "after_nn": after_nn,
        "updates": len(updates),
        "rows_touched": changed,
        "rivers_updated": rivers_updated,
        "dry_run": False,
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Backfill snow_pct_norm without DROP DB")
    p.add_argument("--db", type=Path, default=_DEFAULT_DB)
    p.add_argument("--csv", type=Path, default=_DEFAULT_CSV)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--only-null",
        action="store_true",
        help="Обновлять только строки где snow_pct_norm IS NULL",
    )
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.db.exists():
        log.error("БД не найдена: %s", args.db)
        return 2
    rc = self_test()
    if rc != 0:
        return rc
    stats = backfill(args.db, args.csv, dry_run=args.dry_run, only_null=args.only_null)
    log.info("DONE %s", stats)
    if not args.dry_run and stats.get("after_nn", 0) <= stats.get("before_nn", 0):
        log.warning("non-null snow не вырос — проверьте CSV/mapping")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())

