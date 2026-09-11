# -*- coding: utf-8 -*-
"""Бэктест 2015-2024: для постов с обученной моделью — точность прогноза уровня
(RMSE/MAE/bias медианы) и превышений НЯ/ОЯ (Brier) на исторических датах.
Прогноз от исторической base_date (в БД есть строка → фичи реальные, без zero-fill).
Пороги — реальные (None, если не заданы)."""
import sys, os, sqlite3
import numpy as np
import pandas as pd
PC = r"c:\Users\pc24\Downloads\Code\my_portal\flood_app\Dataset_water\python_code"
sys.path.insert(0, PC)
import hydro_service as hs
DB = r"c:\Users\pc24\Downloads\Code\my_portal\flood_app\Dataset_water\data\ml_features.db"
MODELS = r"c:\Users\pc24\Downloads\Code\my_portal\flood_app\Dataset_water\models"
OUT = r"c:\Users\pc24\Downloads\Code\my_portal\_backtest.txt"
out = open(OUT, "w", encoding="utf-8")
def w(*a): print(*a, file=out)

posts = []
if os.path.isdir(MODELS):
    for riv in os.listdir(MODELS):
        rp = os.path.join(MODELS, riv)
        if not os.path.isdir(rp):
            continue
        for po in os.listdir(rp):
            if os.path.exists(os.path.join(rp, po, "manifest.json")):
                posts.append((riv, po))
w(f"ML posts with manifest: {len(posts)} -> {posts}")


def backtest(river, post, horizon=7, y0=2015, y1=2024):
    st = hs.get_station_row(river, post) or {}
    low = st.get("low_oya"); crit = st.get("critical_oya")
    low_f = float(low) if low is not None else None
    crit_f = float(crit) if crit is not None else None
    p = hs.load_predictor(river, post)
    if not p:
        return {"river": river, "post": post, "error": "no model"}
    conn = sqlite3.connect(DB)
    df = pd.read_sql_query(
        "SELECT date, water_level_cm FROM daily_features WHERE river=? AND post=? "
        "AND water_level_cm IS NOT NULL ORDER BY date", conn, params=(river, post))
    conn.close()
    df["date"] = pd.to_datetime(df["date"])
    df = df[(df["date"].dt.year >= y0) & (df["date"].dt.year <= y1)].reset_index(drop=True)
    med, act, pl, pc = [], [], [], []
    for i in range(len(df) - horizon):
        base = df.iloc[i]["date"].date()
        try:
            res = p.predict(base, horizon=horizon, warning_level=low_f, danger_level=crit_f)
        except Exception:
            continue
        if not res:
            continue
        m = res.get("median")
        if m is None:
            continue
        med.append(float(m)); act.append(float(df.iloc[i + horizon]["water_level_cm"]))
        pl.append(res.get("prob_warning")); pc.append(res.get("prob_danger"))
    if not med:
        return {"river": river, "post": post, "error": "no predictions", "n": 0}
    med = np.array(med); act = np.array(act)
    rmse = float(np.sqrt(np.mean((med - act) ** 2))); mae = float(np.mean(np.abs(med - act)))
    bias = float(np.mean(med - act))

    def brier(probs, thr):
        if thr is None:
            return None
        pp, yy = [], []
        for pr, a in zip(probs, act):
            if pr is None:
                continue
            pp.append(float(pr)); yy.append(1.0 if a >= thr else 0.0)
        if len(pp) < 20:
            return None
        pp = np.array(pp); yy = np.array(yy)
        return round(float(np.mean((pp - yy) ** 2)), 4)
    return {"river": river, "post": post, "horizon": horizon, "n": len(med),
            "rmse": round(rmse, 1), "mae": round(mae, 1), "bias": round(bias, 1),
            "brier_low": brier(pl, low_f), "brier_crit": brier(pc, crit_f),
            "low_oya": low_f, "critical_oya": crit_f}


w("\n=== BACKTEST 2015-2024 (horizon=7d: median level RMSE/MAE + exceedance Brier) ===")
for riv, po in posts:
    w(riv, "/", po, "->", backtest(riv, po))
out.close()
print("done")
