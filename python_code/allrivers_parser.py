# -*- coding: utf-8 -*-
"""
Парсер данных с https://allrivers.info для региона ДВФО-Север.

Собирает:
  - список гидропостов региона (река, пост, дата последнего измерения, slug)
  - для каждого поста: координаты (lat, lon), текущий уровень воды, критические уровни паводка

Использование:
    python allrivers_parser.py                     # собрать всё в allrivers_dvfo_sever.json
    python allrivers_parser.py --limit 5            # только первые 5 постов (для теста)
    python allrivers_parser.py --workers 4          # параллельная загрузка (по умолчанию 3)

Зависимости: только стандартная библиотека Python 3.9+.
"""

import argparse
import json
import re
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple


BASE_URL = "https://allrivers.info"
USER_AGENT = "hydropredict/allrivers-parser (+flood-portal)"
REQUEST_TIMEOUT = 30
RETRY_COUNT = 2
RETRY_DELAY = 2.0


@dataclass
class GaugeInfo:
    """Данные одного гидропоста."""
    river: str = ""
    post: str = ""
    slug: str = ""
    url: str = ""
    last_date: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    water_level_cm: Optional[int] = None
    water_level_delta_cm: Optional[int] = None
    critical_level_cm: Optional[float] = None
    adverse_level_cm: Optional[float] = None   # НЯ
    dangerous_level_cm: Optional[float] = None  # ОЯ
    source: str = ""
    error: Optional[str] = None


def _fetch_html(url: str, retries: int = RETRY_COUNT) -> str:
    """Загрузить HTML с повторами при ошибках."""
    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(RETRY_DELAY * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url}: {last_exc}")


def parse_region_page(region_slug: str = "russia/dvfo-sever") -> List[GaugeInfo]:
    """Парсит страницу региона и возвращает список постов со slug-ами и датами."""
    url = f"{BASE_URL}/region/{region_slug}"
    html = _fetch_html(url)

    gauge_links = re.findall(r'href="/gauge/([^"]+)"', html)
    seen_slugs: set = set()
    gauges: List[GaugeInfo] = []

    for slug in gauge_links:
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)

        date_match = re.search(
            rf'/gauge/{re.escape(slug)}"</a>\s*(\d{{2}}\.\d{{2}}\.\d{{4}})',
            html
        )
        last_date = date_match.group(1) if date_match else ""

        parts = slug.split("-", 1)
        river_slug = parts[0] if parts else slug
        post_slug = parts[1] if len(parts) > 1 else ""

        gauges.append(GaugeInfo(
            slug=slug,
            url=f"/gauge/{slug}",
            river=river_slug,
            post=post_slug,
            last_date=last_date,
        ))

    return gauges


def parse_gauge_page(gauge: GaugeInfo) -> GaugeInfo:
    """Парсит детальную страницу поста: координаты, уровень воды, критические уровни."""
    url = f"{BASE_URL}{gauge.url}"
    try:
        html = _fetch_html(url)
    except RuntimeError as exc:
        gauge.error = str(exc)
        return gauge

    # Координаты — ищем пару lat,lon в любом формате на странице
    # Вариант 1: текстовая пара "53.1844,158.3970" (для буфера обмена)
    coord_text = re.search(r'([\d]{1,3}\.[\d]{3,8})\s*,\s*([\d]{1,3}\.[\d]{3,8})', html)
    if coord_text:
        try:
            lat_val = float(coord_text.group(1))
            lon_val = float(coord_text.group(2))
            # Sanity check: lat in [40..80], lon in [20..180] for Russia
            if 40 <= lat_val <= 80 and 20 <= lon_val <= 180:
                gauge.lat = lat_val
                gauge.lon = lon_val
        except ValueError:
            pass

    # Вариант 2: JS/Vue атрибуты :lat="..." :lng="..."
    if gauge.lat is None:
        lat_js = re.search(r':lat=["\']([\d.]+)["\']', html)
        lng_js = re.search(r':lng=["\']([\d.]+)["\']', html)
        if lat_js and lng_js:
            try:
                gauge.lat = float(lat_js.group(1))
                gauge.lon = float(lng_js.group(1))
            except ValueError:
                pass

    # Текущий уровень воды
    level_match = re.search(
        r'Уровень\s+воды.*?(\d+)\s*[сc]м\s*\(([+-]?\d+)\)', html, re.S | re.I
    )
    if level_match:
        try:
            gauge.water_level_cm = int(level_match.group(1))
            gauge.water_level_delta_cm = int(level_match.group(2))
        except ValueError:
            pass
    else:
        level_simple = re.search(r'(?:уровень|составляет)\s+(\d+)\s*[сc]м', html, re.I)
        if level_simple:
            try:
                gauge.water_level_cm = int(level_simple.group(1))
            except ValueError:
                pass

    # Критические уровни паводка
    crit_match = re.search(r'([\d.]+)\s*[сc]м\s+уровень\s+критический', html, re.I)
    if crit_match:
        try:
            gauge.critical_level_cm = float(crit_match.group(1))
        except ValueError:
            pass

    adverse_match = re.search(r'([\d.]+)\s*[сc]м\s+уровень\s+неблагоприятного', html, re.I)
    if adverse_match:
        try:
            gauge.adverse_level_cm = float(adverse_match.group(1))
        except ValueError:
            pass

    danger_match = re.search(r'([\d.]+)\s*[сc]м\s+уровень\s+опасного', html, re.I)
    if danger_match:
        try:
            gauge.dangerous_level_cm = float(danger_match.group(1))
        except ValueError:
            pass

    source_match = re.search(r'Источник\s+данных\s*[-–]\s*(.+?)(?:\.|$)', html, re.I)
    if source_match:
        gauge.source = source_match.group(1).strip()[:200]

    return gauge


def collect_allrivers_data(
    region_slug: str = "russia/dvfo-sever",
    limit: Optional[int] = None,
    workers: int = 3,
) -> Dict:
    """Основной сборщик: парсит регион, затем каждый пост параллельно."""
    print(f"[AllRivers] Загрузка списка постов региона {region_slug}...")
    gauges = parse_region_page(region_slug)
    print(f"[AllRivers] Найдено {len(gauges)} постов")

    if limit and limit > 0:
        gauges = gauges[:limit]
        print(f"[AllRivers] Ограничено до {limit} постов")

    results: List[GaugeInfo] = []
    total = len(gauges)

    def _process(idx_and_gauge: Tuple[int, GaugeInfo]) -> GaugeInfo:
        idx, g = idx_and_gauge
        result = parse_gauge_page(g)
        status = "OK" if not result.error else f"ERR: {result.error[:60]}"
        coords = f"({result.lat}, {result.lon})" if result.lat else "(no coords)"
        level = f"{result.water_level_cm} см" if result.water_level_cm is not None else "—"
        print(f"  [{idx+1}/{total}] {g.slug}: {level} {coords} {status}")
        return result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_process, (i, g)): i
            for i, g in enumerate(gauges)
        }
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                idx = futures[future]
                g = gauges[idx]
                g.error = str(exc)
                results.append(g)

    results.sort(key=lambda g: (g.river, g.post))

    ok_count = sum(1 for g in results if not g.error)
    err_count = sum(1 for g in results if g.error)
    with_coords = sum(1 for g in results if g.lat is not None)
    with_level = sum(1 for g in results if g.water_level_cm is not None)

    return {
        "summary": {
            "region": region_slug,
            "total": len(results),
            "ok": ok_count,
            "errors": err_count,
            "with_coords": with_coords,
            "with_water_level": with_level,
        },
        "gauges": [asdict(g) for g in results],
    }


def main():
    parser = argparse.ArgumentParser(description="Парсер allrivers.info для гидропостов")
    parser.add_argument("--region", default="russia/dvfo-sever", help="Slug региона")
    parser.add_argument("--limit", type=int, default=None, help="Максимум постов")
    parser.add_argument("--workers", type=int, default=3, help="Параллельных запросов")
    parser.add_argument("--output", default=None, help="Выходной файл JSON")
    args = parser.parse_args()

    start = time.time()
    data = collect_allrivers_data(
        region_slug=args.region,
        limit=args.limit,
        workers=args.workers,
    )
    elapsed = time.time() - start

    output_path = args.output or f"allrivers_{args.region.replace('/', '_')}.json"
    out = Path(output_path)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    s = data["summary"]
    print(f"\n[AllRivers] Готово за {elapsed:.1f}с")
    print(f"  Всего: {s['total']}, OK: {s['ok']}, Ошибок: {s['errors']}")
    print(f"  С координатами: {s['with_coords']}, С уровнем: {s['with_water_level']}")
    print(f"  Файл: {out.resolve()}")


if __name__ == "__main__":
    main()
