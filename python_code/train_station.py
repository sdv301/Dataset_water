# -*- coding: utf-8 -*-
"""
Обучение одной станции: файлы в models/{река}/{пост}/ + запись в station_models (SQLite).

Пример (из python_code, с venv):
    .venv\\Scripts\\python train_station.py --river "Лена" --post "Якутск"
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from train_all import (
    DB_PATH,
    DEFAULT_N_TRIALS,
    DEFAULT_TIMEOUT,
    FAST_N_TRIALS,
    FAST_TIMEOUT,
    MODELS_DIR,
    train_station,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Обучить одну гидростанцию")
    parser.add_argument("--river", required=True, help='Например: "Лена"')
    parser.add_argument("--post", required=True, help='Например: "Якутск"')
    parser.add_argument(
        "--full",
        action="store_true",
        help="Полный Optuna (12 ит. × 90 с), по умолчанию — быстрый (5 × 45 с)",
    )
    parser.add_argument("--backend", default="catboost", choices=["catboost", "xgboost"])
    args = parser.parse_args()

    if args.full:
        n_trials, timeout = DEFAULT_N_TRIALS, DEFAULT_TIMEOUT
    else:
        n_trials, timeout = FAST_N_TRIALS, FAST_TIMEOUT

    ok = train_station(
        args.river,
        args.post,
        db_path=DB_PATH,
        models_dir=MODELS_DIR,
        n_trials=n_trials,
        timeout=timeout,
        backend=args.backend,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
