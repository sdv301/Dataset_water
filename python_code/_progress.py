# -*- coding: utf-8 -*-
"""Разовая диагностика: прогресс батча + ОЯ/НЯ-дни 2015-2024 (п.4)."""
import sqlite3
import os
import pathlib
import time

db = "flood_app/Dataset_water/data/ml_features.db"
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row

print("=== station_models (прогресс обучения) ===")
rows = list(conn.execute(
    "SELECT river,post,trained_at,backend,horizons,model_dir,n_model_files,updated_at "
    "FROM station_models ORDER BY updated_at DESC"))
for r in rows:
    print(dict(r))
print("station_models count:", len(rows))

# manifest.json по предполагаемым корням моделей
roots = set()
for r in rows:
    md = r["model_dir"] or ""
    if md:
        p = pathlib.Path(md)
        if not p.is_absolute():
            cand1 = pathlib.Path("flood_app/Dataset_water/python_code") / md
            cand2 = pathlib.Path("flood_app/Dataset_water") / md
            p = cand1 if cand1.exists() else cand2
        for parent in [p, *p.parents]:
            if parent.name == "models":
                roots.add(parent)
                break
        else:
            roots.add(p)
roots.update({
    pathlib.Path("flood_app/Dataset_water/python_code/models"),
    pathlib.Path("flood_app/Dataset_water/models"),
})
seen = set()
manifests = []
for root in roots:
    if root.exists():
        for m in root.rglob("manifest.json"):
            if m not in seen:
                seen.add(m)
                manifests.append(m)
print("manifest.json total:", len(manifests))
for m in sorted(manifests, key=lambda x: x.stat().st_mtime, reverse=True)[:25]:
    print("  ", time.strftime('%Y-%m-%d %H:%M', time.localtime(m.stat().st_mtime)), m)

print("\n=== ОЯ/НЯ days 2015-2024 per post (п.4) ===")
q = """
SELECT s.river AS river, s.post AS post, s.critical_oya AS crit, s.low_oya AS low,
       SUM(CASE WHEN d.water_level_cm >= s.critical_oya THEN 1 ELSE 0 END) AS oya_days,
       SUM(CASE WHEN d.water_level_cm >= s.low_oya THEN 1 ELSE 0 END) AS nya_days,
       COUNT(*) AS n_days,
       MAX(d.water_level_cm) AS max_level
FROM daily_features d
JOIN stations s ON s.river = d.river AND s.post = d.post
WHERE d.date BETWEEN '2015-01-01' AND '2024-12-31'
GROUP BY s.river, s.post
ORDER BY oya_days DESC
"""
rows = list(conn.execute(q))
print("posts with data 2015-2024:", len(rows))
oya_zero = sum(1 for r in rows if (r["oya_days"] or 0) == 0)
nya_zero = sum(1 for r in rows if (r["nya_days"] or 0) == 0)
print("posts with 0 ОЯ-days:", oya_zero)
print("posts with 0 НЯ-days:", nya_zero)
print(f"{'river':<10}{'post':<24}{'crit':>7}{'low':>7}{'oya_d':>7}{'nya_d':>7}{'n':>6}{'max':>7}")
for r in rows:
    print(f"{r['river']:<10}{r['post']:<24}{str(r['crit']):>7}{str(r['low']):>7}"
          f"{str(r['oya_days'] or 0):>7}{str(r['nya_days'] or 0):>7}{r['n_days']:>6}{str(r['max_level']):>7}")
conn.close()
