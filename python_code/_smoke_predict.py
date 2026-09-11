# -*- coding: utf-8 -*-
"""Smoke-тест фикса predict(): клиппинг нижнего хвоста + монотонизация квантилей
на реальных обученных моделях. Проверяем, что q10 >= наблюдённый минимум и
q10 <= q50 <= q90 <= q95 (нет 'quantile crossing')."""
import sys
import os
import sqlite3
import datetime

PC = r"c:\Users\pc24\Downloads\Code\my_portal\flood_app\Dataset_water\python_code"
sys.path.insert(0, PC)
import hydro_service as hs

DB = r"c:\Users\pc24\Downloads\Code\my_portal\flood_app\Dataset_water\data\ml_features.db"


def latest_date(river, post):
    conn = sqlite3.connect(DB)
    row = conn.execute(
        "SELECT MAX(date) FROM daily_features WHERE river=? AND post=? "
        "AND water_level_cm IS NOT NULL",
        (river, post)).fetchone()
    conn.close()
    return row[0]


def test_post(river, post):
    print(f"\n=== {river} / {post} ===")
    p = hs.load_predictor(river, post)
    if not p:
        print("  no model -> skip")
        return
    st = hs.get_station_row(river, post) or {}
    low = st.get("low_oya")
    crit = st.get("critical_oya")
    low_f = float(low) if low is not None else None
    crit_f = float(crit) if crit is not None else None
    d = latest_date(river, post)
    base = datetime.date.fromisoformat(d) if d else datetime.date(2024, 9, 1)
    print(f"  base_date={base}  НЯ={low_f}  ОЯ={crit_f}  backend={p.backend}")
    lo, hi = p._level_bounds()
    print(f"  _level_bounds -> lo={lo} hi={hi}  (_river={p._river!r} _post={p._post!r})")
    ok_all = True
    for h in p.horizons:
        if h not in p.models:
            continue
        res = p.predict(base, horizon=h, warning_level=low_f, danger_level=crit_f)
        if isinstance(res, list):
            res = res[0] if res else None
        if not res:
            print(f"   h={h:3d}: no result")
            continue
        q10 = res.get("q10")
        q50 = res.get("median")
        q90 = res.get("q90")
        q95 = res.get("q95")
        pw = res.get("prob_warning")
        pd_ = res.get("prob_danger")
        mono = (
            (q10 is None or q50 is None or q10 <= q50 + 1e-6)
            and (q50 is None or q90 is None or q50 <= q90 + 1e-6)
            and (q90 is None or q95 is None or q90 <= q95 + 1e-6)
        )
        clip_ok = lo is None or q10 is None or q10 >= lo - 1e-6
        flag = "" if (mono and clip_ok) else "  <<<< VIOLATION"
        if not (mono and clip_ok):
            ok_all = False
        print(f"   h={h:3d}: q10={q10} q50={q50} q90={q90} q95={q95}  "
              f"pw={pw} pd={pd_}{flag}")
    print(f"  => {'ALL OK (monotone + clipped)' if ok_all else 'VIOLATIONS PRESENT'}")


for rp in [("Лена", "Табага"), ("Лена", "Олёкминск"),
           ("Лена", "Якутск"), ("Колыма", "Зырянка")]:
    try:
        test_post(*rp)
    except Exception as e:
        import traceback
        print(f"  EXC {rp}: {e!r}")
        traceback.print_exc()
