# -*- coding: utf-8 -*-
"""
Скрипт-оркестратор обучения моделей HydroPredict.

Использование:
    python train_all.py                                 — обучить все станции
    python train_all.py --river "Лена" --post "Якутск"  — обучить одну станцию
    python train_all.py --fast                          — ускоренный режим (5 итераций Optuna)
    python train_all.py --list                          — показать доступные станции
    python train_all.py --backend catboost              — использовать CatBoost
"""

import argparse
import os
import sys
import time
import sqlite3
from datetime import datetime
from typing import List, Tuple

import pandas as pd

# Добавляем текущую директорию в путь для импорта
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flood_predictor import FloodPredictor
from model_registry import ModelRegistry
import hydro_service as hs


# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "ml_features.db"
)
MODELS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "models"
)

# Минимальное число дней данных для обучения
MIN_DATA_DAYS = 180

# Параметры Optuna по умолчанию и в быстром режиме
DEFAULT_N_TRIALS = 15
DEFAULT_TIMEOUT = 180
FAST_N_TRIALS = 5
FAST_TIMEOUT = 60


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def get_available_stations(db_path: str) -> List[Tuple[str, str, int]]:
    """
    Получение списка доступных станций из БД.

    Args:
        db_path: Путь к SQLite-базе.

    Returns:
        Список кортежей (река, пост, количество_записей).
    """
    if not os.path.exists(db_path):
        print(f"[ОШИБКА] База данных не найдена: {db_path}")
        return []

    conn = sqlite3.connect(db_path)
    try:
        query = (
            "SELECT river, post, COUNT(*) as cnt "
            "FROM daily_features "
            "GROUP BY river, post "
            "ORDER BY river, post"
        )
        df = pd.read_sql_query(query, conn)
    except Exception as e:
        print(f"[ОШИБКА] Ошибка чтения БД: {e}")
        return []
    finally:
        conn.close()

    stations = []
    for _, row in df.iterrows():
        stations.append((row["river"], row["post"], int(row["cnt"])))

    return stations


def print_stations_table(stations: List[Tuple[str, str, int]]) -> None:
    """Вывод таблицы станций в консоль."""
    if not stations:
        print("Станции не найдены.")
        return

    print(f"\n{'='*60}")
    print(f"{'Река':<20} {'Пост':<20} {'Записей':>8} {'Статус':>10}")
    print(f"{'-'*60}")

    for river, post, count in stations:
        status = "[OK]" if count >= MIN_DATA_DAYS else "[LOW]"
        print(f"{river:<20} {post:<20} {count:>8} {status:>10}")

    total = len(stations)
    eligible = sum(1 for _, _, c in stations if c >= MIN_DATA_DAYS)
    print(f"{'-'*60}")
    print(f"Всего станций: {total}, пригодных для обучения: {eligible}")
    print(f"{'='*60}\n")


def format_elapsed(seconds: float) -> str:
    """Форматирование прошедшего времени."""
    if seconds < 60:
        return f"{seconds:.1f} сек"
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes} мин {secs:.0f} сек"


# ---------------------------------------------------------------------------
# Обучение одной станции
# ---------------------------------------------------------------------------

def train_station(
    river: str,
    post: str,
    db_path: str,
    models_dir: str,
    n_trials: int,
    timeout: int,
    backend: str = "catboost",
) -> bool:
    """
    Обучение моделей для одной станции.

    Args:
        river: Название реки.
        post: Название поста.
        db_path: Путь к SQLite-базе с фичами.
        models_dir: Корневая директория моделей.
        n_trials: Число итераций Optuna.
        timeout: Таймаут Optuna (секунды).
        backend: Бэкенд ML («xgboost» или «catboost»).

    Returns:
        True при успешном обучении, False при ошибке.
    """
    start_time = time.time()
    print(f"\n{'='*60}")
    print(f"[STATION] {river} / {post}")
    print(f"{'='*60}")

    # Инициализация предиктора
    predictor = FloodPredictor(
        models_dir=models_dir,
        db_path=db_path,
        backend=backend,
    )

    # Загрузка данных
    try:
        data = predictor.load_station_data(river, post)
        print(f"  Загружено записей: {len(data)}")
    except (FileNotFoundError, ValueError) as e:
        print(f"  [ОШИБКА] {e}")
        return False

    # Проверка достаточности данных
    if len(data) < MIN_DATA_DAYS:
        print(
            f"  [WARN] Недостаточно данных ({len(data)} < {MIN_DATA_DAYS}). Пропуск."
        )
        return False

    # Обучение
    print(f"  Бэкенд: {backend}, Optuna: {n_trials} итераций, таймаут {timeout} сек")
    try:
        predictor.train(data, n_trials=n_trials, timeout=timeout)
    except Exception as e:
        print(f"  [ОШИБКА] Ошибка обучения: {e}")
        return False

    # Оценка на hold-out
    print("  Оценка на hold-out (20%):")
    try:
        eval_metrics = predictor.evaluate(data)
        for h, q_dict in eval_metrics.items():
            for q, m in q_dict.items():
                print(
                    f"    h={h:>2d} q={q:.2f}: "
                    f"RMSE={m['rmse']:.1f}, MAE={m['mae']:.1f}, "
                    f"PBL={m['pinball_loss']:.4f} "
                    f"(n={m['n_test_samples']})"
                )
    except Exception as e:
        print(f"  [!] Ошибка оценки: {e}")

    # Регистрация в реестре
    registry = ModelRegistry(models_dir=models_dir)
    train_metrics = predictor.get_metrics()

    for h, q_dict in train_metrics.items():
        for q, m in q_dict.items():
            if h in predictor.models and q in predictor.models[h]:
                registry.register_model(
                    river=river,
                    post=post,
                    horizon=h,
                    quantile=q,
                    model=predictor.models[h][q],
                    metrics={
                        "rmse": m["rmse"],
                        "mae": m["mae"],
                        "pinball_loss": m["pinball_loss"],
                    },
                    params=m.get("params", {}),
                )

    reg = hs.register_station_model(river, post)
    if reg:
        print(f"  [DB] Запись station_models: {reg.get('model_dir')}")
        print(f"  [Git] {reg.get('git_add')}")

    elapsed = time.time() - start_time
    print(f"\n  [DONE] Завершено за {format_elapsed(elapsed)}")

    return True


# ---------------------------------------------------------------------------
# Главная функция
# ---------------------------------------------------------------------------

def main():
    """Точка входа CLI."""
    parser = argparse.ArgumentParser(
        description="Обучение моделей прогнозирования паводков HydroPredict",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            '  python train_all.py\n'
            '  python train_all.py --river "Лена" --post "Якутск"\n'
            '  python train_all.py --fast\n'
            '  python train_all.py --list\n'
        ),
    )

    parser.add_argument(
        "--river",
        type=str,
        default=None,
        help="Название реки (для обучения одной станции)",
    )
    parser.add_argument(
        "--post",
        type=str,
        default=None,
        help="Название поста (для обучения одной станции)",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Быстрый режим (5 итераций Optuna, таймаут 60 сек)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_stations",
        help="Показать доступные станции и выйти",
    )
    parser.add_argument(
        "--backend",
        type=str,
        default="catboost",
        choices=["xgboost", "catboost"],
        help="ML-бэкенд (по умолчанию: catboost)",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help=f"Путь к БД (по умолчанию: {DB_PATH})",
    )
    parser.add_argument(
        "--models-dir",
        type=str,
        default=None,
        help=f"Директория моделей (по умолчанию: {MODELS_DIR})",
    )

    args = parser.parse_args()

    db_path = args.db or DB_PATH
    models_dir = args.models_dir or MODELS_DIR
    n_trials = FAST_N_TRIALS if args.fast else DEFAULT_N_TRIALS
    timeout = FAST_TIMEOUT if args.fast else DEFAULT_TIMEOUT

    print("=" * 60)
    print("  HydroPredict — Обучение моделей прогнозирования паводков")
    print(f"  Дата запуска: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  БД: {db_path}")
    print(f"  Модели: {models_dir}")
    print(f"  Бэкенд: {args.backend}")
    if args.fast:
        print("  [FAST] Быстрый режим")
    print("=" * 60)

    # Получаем список станций
    stations = get_available_stations(db_path)

    if args.list_stations:
        print_stations_table(stations)
        return

    if not stations:
        print("[ОШИБКА] Станции не найдены. Сначала запустите prepare_ml_data.py.")
        sys.exit(1)

    # Фильтрация по конкретной станции
    if args.river and args.post:
        stations = [
            (r, p, c) for r, p, c in stations
            if r == args.river and p == args.post
        ]
        if not stations:
            print(
                f"[ОШИБКА] Станция '{args.river} / {args.post}' "
                f"не найдена в БД."
            )
            sys.exit(1)
    elif args.river:
        stations = [(r, p, c) for r, p, c in stations if r == args.river]
        if not stations:
            print(f"[ОШИБКА] Река '{args.river}' не найдена в БД.")
            sys.exit(1)

    # Фильтрация по минимальному объёму данных
    eligible = [(r, p, c) for r, p, c in stations if c >= MIN_DATA_DAYS]
    skipped = len(stations) - len(eligible)

    if skipped > 0:
        print(
            f"\n[WARN] Пропущено станций с недостаточным объёмом данных "
            f"(< {MIN_DATA_DAYS} дней): {skipped}"
        )

    if not eligible:
        print("[ОШИБКА] Нет станций с достаточным объёмом данных.")
        sys.exit(1)

    print(f"\nК обучению: {len(eligible)} станций")

    # Обучение
    total_start = time.time()
    success_count = 0
    fail_count = 0

    for i, (river, post, count) in enumerate(eligible, 1):
        print(f"\n[{i}/{len(eligible)}]", end="")
        ok = train_station(
            river=river,
            post=post,
            db_path=db_path,
            models_dir=models_dir,
            n_trials=n_trials,
            timeout=timeout,
            backend=args.backend,
        )
        if ok:
            success_count += 1
        else:
            fail_count += 1

    # Итоги
    total_elapsed = time.time() - total_start
    print(f"\n{'='*60}")
    print("  ИТОГИ ОБУЧЕНИЯ")
    print(f"{'='*60}")
    print(f"  Успешно обучено: {success_count}")
    print(f"  Ошибки:          {fail_count}")
    print(f"  Общее время:     {format_elapsed(total_elapsed)}")

    # Сводка по реестру
    registry = ModelRegistry(models_dir=models_dir)
    all_metrics = registry.get_all_metrics()
    if all_metrics:
        print(f"\n  Всего моделей в реестре: {len(all_metrics)}")
        print(f"  Реки: {', '.join(registry.list_rivers())}")

    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
