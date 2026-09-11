# -*- coding: utf-8 -*-
"""Фоновый батч обучения топ-25 постов (быстрый режим Optuna 5x60с).
Запускается отдельно (Start-Process), пишет прогресс в _train_batch.log.
 climatology-fallback в flood_agent._forecast_daily покрывает посты без ML-модели,
 поэтому приложение работает и во время/без этого батча.
"""
import sys, os, time, traceback
PC = r"c:\Users\pc24\Downloads\Code\my_portal\flood_app\Dataset_water\python_code"
sys.path.insert(0, PC)
from train_all import train_station, DB_PATH, MODELS_DIR, FAST_N_TRIALS, FAST_TIMEOUT

POSTS = [
    ("Лена", "Олёкминск"), ("Лена", "Якутск"), ("Лена", "Табага"),
    ("Лена", "Ленск"), ("Лена", "Покровск"), ("Лена", "Мача"),
    ("Лена", "Пеледуй"), ("Лена", "Витим"), ("Алдан", "Крест-Хальджай"),
    ("Алдан", "Томмот"), ("Лена", "Нюя"), ("Алдан", "Батамай"),
    ("Лена", "Сангары"), ("Лена", "Солянка"), ("Алдан", "Верх.Перевоз"),
    ("Алдан", "Усть-Миль"), ("Лена", "Кангалассы"), ("Алдан", "Охот.Перевоз"),
    ("Олёкма", "Куду-Кюель"), ("Вилюй", "Нюрба"), ("Вилюй", "Сунтар"),
    ("Вилюй", "Вилюйск"), ("Лена", "Кюсюр"), ("Колыма", "Зырянка"),
    ("Лена", "Жиганск"),
]

LOG = open(r"c:\Users\pc24\Downloads\Code\my_portal\_train_batch.log", "a", encoding="utf-8")
def w(*a): print(*a, file=LOG, flush=True)

w(f"\n=== BATCH START {time.strftime('%Y-%m-%d %H:%M:%S')} posts={len(POSTS)} trials={FAST_N_TRIALS} timeout={FAST_TIMEOUT} ===")
ok_n = fail_n = skip_n = 0
for i, (r, p) in enumerate(POSTS, 1):
    # Возобновляемость: пропускаем посты с уже готовым manifest (обучение можно прерывать
    # и перезапускать — продолжится с места остановки).
    mf = os.path.join(MODELS_DIR, r, p, "manifest.json")
    if os.path.exists(mf):
        w(f"--- [{i}/{len(POSTS)}] {r} / {p} --- SKIP (manifest already exists)")
        skip_n += 1
        continue
    w(f"\n--- [{i}/{len(POSTS)}] {r} / {p} ---")
    t0 = time.time()
    try:
        ok = train_station(r, p, db_path=DB_PATH, models_dir=MODELS_DIR,
                           n_trials=FAST_N_TRIALS, timeout=FAST_TIMEOUT, backend="catboost")
        w(f"RESULT {'OK' if ok else 'FAIL'} {r}/{p} {time.time()-t0:.1f}s")
        ok_n += 1 if ok else 0
        fail_n += 0 if ok else 1
    except Exception as e:
        w(f"RESULT EXC {r}/{p} {time.time()-t0:.1f}s {e!r}")
        traceback.print_exc(file=LOG)
        fail_n += 1
w(f"=== BATCH DONE {time.strftime('%Y-%m-%d %H:%M:%S')} ok={ok_n} fail={fail_n} skip={skip_n} ===")
LOG.close()
