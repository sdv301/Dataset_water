# -*- coding: utf-8 -*-
"""Фоновый батч обучения топ-25 постов (быстрый режим Optuna 5x60с).
Запускается отдельно (Start-Process), пишет прогресс в _train_batch.log.
climatology-fallback в flood_agent._forecast_daily покрывает посты без ML-модели,
поэтому приложение работает и во время/без этого батча.
"""
import sys, os, time, traceback, argparse
from pathlib import Path

PC = r"c:\Users\pc24\Downloads\Code\my_portal\flood_app\Dataset_water\python_code"
if PC not in sys.path:
    sys.path.insert(0, PC)

from train_all import train_station, DB_PATH, MODELS_DIR, FAST_N_TRIALS, FAST_TIMEOUT

ALL_POSTS = [
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

def main():
    parser = argparse.ArgumentParser(description="Обучение батча моделей HydroPredict")
    parser.add_argument("--force", action="store_true", help="Принудительно переобучить даже при наличии manifest.json")
    parser.add_argument("--river", type=str, default=None, help="Фильтр по реке")
    parser.add_argument("--post", type=str, default=None, help="Фильтр по посту")
    parser.add_argument("--trials", type=int, default=FAST_N_TRIALS, help="Число итераций Optuna")
    parser.add_argument("--timeout", type=int, default=FAST_TIMEOUT, help="Таймаут Optuna в секундах")
    parser.add_argument("--backend", type=str, default="catboost", help="Бэкенд (catboost/xgboost)")
    args = parser.parse_args()

    posts = ALL_POSTS
    if args.river and args.post:
        posts = [(args.river, args.post)]
    elif args.river:
        posts = [p for p in ALL_POSTS if p[0] == args.river]

    log_path = Path(r"c:\Users\pc24\Downloads\Code\my_portal\_train_batch.log")
    log_file = open(log_path, "a", encoding="utf-8")
    def w(*a):
        print(*a, flush=True)
        print(*a, file=log_file, flush=True)

    w(f"\n=== BATCH START {time.strftime('%Y-%m-%d %H:%M:%S')} posts={len(posts)} trials={args.trials} timeout={args.timeout} force={args.force} ===")
    ok_n = fail_n = skip_n = 0

    for i, (r, p) in enumerate(posts, 1):
        mf = os.path.join(MODELS_DIR, r, p, "manifest.json")
        if os.path.exists(mf) and not args.force:
            w(f"--- [{i}/{len(posts)}] {r} / {p} --- SKIP (manifest already exists, use --force to overwrite)")
            skip_n += 1
            continue

        w(f"\n--- [{i}/{len(posts)}] {r} / {p} ---")
        t0 = time.time()
        try:
            ok = train_station(r, p, db_path=DB_PATH, models_dir=MODELS_DIR,
                               n_trials=args.trials, timeout=args.timeout, backend=args.backend)
            w(f"RESULT {'OK' if ok else 'FAIL'} {r}/{p} {time.time()-t0:.1f}s")
            ok_n += 1 if ok else 0
            fail_n += 0 if ok else 1
        except Exception as e:
            w(f"RESULT EXC {r}/{p} {time.time()-t0:.1f}s {e!r}")
            traceback.print_exc(file=log_file)
            fail_n += 1

    w(f"=== BATCH DONE {time.strftime('%Y-%m-%d %H:%M:%S')} ok={ok_n} fail={fail_n} skip={skip_n} ===")
    log_file.close()

if __name__ == "__main__":
    main()

