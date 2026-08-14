# -*- coding: utf-8 -*-
"""
Обновление координат гидропостов в ml_features.db на основе данных allrivers.info.

Сопоставляет посты по названию реки + поста (нечёткое сравнение через транслитерацию slug-а).
Обновляет только если разница > порога (по умолчанию 0.005 градуса ~ 500 м).

Использование:
    python update_station_coords.py                              # dry-run (только отчёт)
    python update_station_coords.py --apply                      # применить изменения
    python update_station_coords.py --threshold 0.01             # порог 0.01 градуса
    python update_station_coords.py --allrivers-json path.json   # указать файл с данными
"""

import argparse
import json
import sqlite3
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Tuple, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "ml_features.db"
DEFAULT_JSON = SCRIPT_DIR / "allrivers_russia_dvfo-sever.json"

# Транслитерация для сопоставления slug-ов с кириллическими названиями
_CYR_TO_LAT = str.maketrans({
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
    'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'j', 'к': 'k', 'л': 'l', 'м': 'm',
    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
    'ф': 'f', 'х': 'h', 'ц': 'c', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch',
    'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
})


def _normalize(text: str) -> str:
    """Нормализует строку для нечёткого сравнения."""
    text = text.lower().strip()
    text = unicodedata.normalize('NFKD', text)
    text = text.translate(_CYR_TO_LAT)
    # Убираем всё кроме букв и цифр
    return ''.join(c for c in text if c.isalnum())


def _slug_matches(slug: str, river: str, post: str) -> bool:
    """Проверяет, соответствует ли slug названию реки+поста."""
    norm_river = _normalize(river)
    norm_post = _normalize(post)
    parts = slug.split('-', 1)
    if len(parts) != 2:
        return False
    slug_river, slug_post = parts[0], parts[1]

    # Река должна совпадать с высоким сходством
    river_ratio = SequenceMatcher(None, slug_river, norm_river).ratio()
    if river_ratio < 0.75:
        return False

    # Пост должен совпадать с высоким сходством
    post_ratio = SequenceMatcher(None, slug_post, norm_post).ratio()
    if post_ratio >= 0.7:
        return True

    # Допускаем частичное совпадение по префиксу (для случаев типа ust-mil vs ustmil)
    min_len = min(len(slug_post), len(norm_post))
    if min_len >= 4 and slug_post[:min_len] == norm_post[:min_len]:
        return True

    return False


def load_allrivers_data(json_path: Path) -> List[dict]:
    data = json.loads(json_path.read_text(encoding='utf-8'))
    return [g for g in data.get('gauges', []) if g.get('lat') is not None]


def match_stations(
    db_path: Path,
    gauges: List[dict],
    threshold: float = 0.005,
) -> List[Tuple[str, str, float, float, float, float, float]]:
    """
    Возвращает список (river, post, old_lat, old_lon, new_lat, new_lon, distance_deg).
    Только для пар где разница > threshold.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT river, post, lat, lon FROM stations WHERE lat IS NOT NULL AND lon IS NOT NULL").fetchall()
    conn.close()

    updates = []
    matched_slugs = set()

    for row in rows:
        river = row['river']
        post = row['post']
        old_lat = row['lat']
        old_lon = row['lon']

        best_gauge = None
        for g in gauges:
            if g['slug'] in matched_slugs:
                continue
            if _slug_matches(g['slug'], river, post):
                best_gauge = g
                break

        if best_gauge is None:
            continue

        matched_slugs.add(best_gauge['slug'])
        new_lat = best_gauge['lat']
        new_lon = best_gauge['lon']
        dist = ((old_lat - new_lat) ** 2 + (old_lon - new_lon) ** 2) ** 0.5

        if dist > threshold:
            updates.append((river, post, old_lat, old_lon, new_lat, new_lon, dist))

    return updates


def main():
    parser = argparse.ArgumentParser(description="Обновление координат постов из allrivers")
    parser.add_argument('--db', default=str(DEFAULT_DB), help='Путь к ml_features.db')
    parser.add_argument('--allrivers-json', default=str(DEFAULT_JSON), help='JSON от allrivers_parser')
    parser.add_argument('--threshold', type=float, default=0.005, help='Порог разницы (градусы)')
    parser.add_argument('--apply', action='store_true', help='Применить изменения (по умолчанию dry-run)')
    args = parser.parse_args()

    db_path = Path(args.db)
    json_path = Path(args.allrivers_json)

    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}")
        return
    if not json_path.exists():
        print(f"ERROR: AllRivers JSON not found: {json_path}")
        print("Run allrivers_parser.py first.")
        return

    gauges = load_allrivers_data(json_path)
    print(f"Loaded {len(gauges)} gauges with coordinates from allrivers")

    updates = match_stations(db_path, gauges, threshold=args.threshold)
    print(f"Found {len(updates)} stations needing coordinate update (threshold={args.threshold} deg)")

    if not updates:
        print("Nothing to update.")
        return

    print(f"\n{'River':15s} | {'Post':20s} | {'Old Lat':>10s} {'Old Lon':>10s} | {'New Lat':>10s} {'New Lon':>10s} | {'Dist deg':>8s}")
    print("-" * 100)
    for river, post, olat, olon, nlat, nlon, dist in sorted(updates, key=lambda x: -x[6]):
        print(f"{river:15s} | {post:20s} | {olat:10.5f} {olon:10.5f} | {nlat:10.5f} {nlon:10.5f} | {dist:8.4f}")

    if args.apply:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        updated = 0
        for river, post, _, _, nlat, nlon, _ in updates:
            cur.execute("UPDATE stations SET lat = ?, lon = ? WHERE river = ? AND post = ?",
                        (nlat, nlon, river, post))
            updated += cur.rowcount
        conn.commit()
        conn.close()
        print(f"\nUpdated {updated} stations in DB.")
    else:
        print(f"\nDry run. Use --apply to write changes to DB.")


if __name__ == "__main__":
    main()
