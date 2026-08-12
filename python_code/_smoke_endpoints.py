# -*- coding: utf-8 -*-
"""Смоук всех эндпоинтов api_server.app через TestClient.

Печатает: METHOD PATH -> code (ok|FAIL). Возвращает non-zero, если критичный
эндпоинт упал. 4xx для явно проверяемых негативных сценариев считаются ok.
"""
from __future__ import annotations
import json
import sys
from typing import Any

from fastapi.testclient import TestClient

import api_server as a

client = TestClient(a.app)

RIVER = "Лена"
POST = "Якутск"
BAD_RIVER = "НетТакойРеки"

# Список: (label, method, path, kwargs, expected_set)
CASES: list[tuple[str, str, str, dict, set[int]]] = [
    # Служебные
    ("health",              "GET",  "/api/health", {}, {200}),
    ("root",                "GET",  "/", {}, {200, 404}),

    # Списки
    ("rivers",              "GET",  "/api/rivers", {}, {200}),
    ("posts",               "GET",  f"/api/rivers/{RIVER}/posts", {}, {200}),
    ("posts_bad_river",     "GET",  f"/api/rivers/{BAD_RIVER}/posts", {}, {200, 404}),

    # Прогноз
    ("forecast_default",    "GET",  f"/api/forecast/{RIVER}/{POST}", {}, {200}),
    ("forecast_short",      "GET",  f"/api/forecast/{RIVER}/{POST}/short", {}, {200}),
    ("forecast_medium",     "GET",  f"/api/forecast/{RIVER}/{POST}/medium", {}, {200}),
    ("forecast_season",     "GET",  f"/api/forecast/{RIVER}/{POST}/season", {}, {200}),
    ("forecast_year",       "GET",  f"/api/forecast/{RIVER}/{POST}/year", {}, {200}),
    ("forecast_scenarios",  "GET",  f"/api/forecast/{RIVER}/{POST}/scenarios", {}, {200}),

    # Модели и климатология
    ("model_status",        "GET",  f"/api/stations/{RIVER}/{POST}/model-status", {}, {200}),
    ("climatology",         "GET",  f"/api/climatology/{RIVER}/{POST}", {}, {200}),
    ("explain",             "GET",  f"/api/explain/{RIVER}/{POST}", {}, {200}),
    ("metrics",             "GET",  f"/api/metrics/{RIVER}/{POST}", {}, {200, 404}),

    # История
    ("history",             "GET",  f"/api/history/{RIVER}/{POST}", {"params": {"limit": 50}}, {200}),

    # Обучение
    ("train_status",        "GET",  "/api/train/status", {}, {200}),
    ("train_history",       "GET",  "/api/train/history?limit=5", {}, {200}),
    ("train_reset",         "POST", "/api/train/reset-status", {"json": {}}, {200}),

    # Данные
    ("data_stats",          "GET",  "/api/data/stats", {}, {200}),

    # Агент
    ("agent_flood_risk",    "GET",  f"/api/agent/flood-risk/{RIVER}/{POST}?horizon=14", {}, {200}),
    ("agent_alerts",        "GET",  "/api/agent/alerts?limit=10", {}, {200}),
    ("agent_alerts_summary","GET",  "/api/agent/alerts/summary", {}, {200}),
    ("agent_analytics",     "GET",  f"/api/agent/analytics/{RIVER}/{POST}?years=10", {}, {200}),
    ("agent_history",       "GET",  f"/api/agent/history/{RIVER}/{POST}?days=90", {}, {200}),
    ("agent_batch",         "POST", "/api/agent/flood-risk/batch",
        {"json": {"items": [{"river": RIVER, "post": POST}], "horizon": 14, "persist": False}}, {200}),
    ("agent_ack_missing",   "POST", "/api/agent/alerts/999999/acknowledge",
        {"json": {"user": "smoke"}}, {200, 404}),
    ("agent_feedback_bad",  "POST", "/api/agent/feedback",
        {"json": {"river": RIVER, "post": POST, "verdict": "invalid"}}, {200, 400}),

    # Планировщик и приоритет
    ("agent_sched_status",  "GET",  "/api/agent/scheduler/status", {}, {200}),
    ("agent_sched_run_now", "POST", "/api/agent/scheduler/run-now",
        {"json": {"background": True}}, {200}),
    ("agent_sched_config",  "POST", "/api/agent/scheduler/config",
        {"json": {"hour": 3, "minute": 0}}, {200}),
    ("agent_priority",      "GET",  "/api/agent/priority?limit=5", {}, {200}),
    ("agent_flood_fresh",   "GET",  f"/api/agent/flood-risk/{RIVER}/{POST}?horizon=14&fresh=true", {}, {200}),

    # Тайлы (external network) — не критичны, помечены как opt
    ("tile_carto",          "GET",  "/api/tiles/carto/5/20/10", {}, {200, 502, 504}),
    ("tile_arcgis",         "GET",  "/api/tiles/arcgis/5/10/20", {}, {200, 502, 504}),
]


def run() -> int:
    ok = 0
    fail: list[str] = []
    for label, method, path, kwargs, expected in CASES:
        try:
            if method == "GET":
                r = client.get(path, **kwargs)
            elif method == "POST":
                r = client.post(path, **kwargs)
            else:
                raise ValueError(method)
            code = r.status_code
            status = "ok" if code in expected else "FAIL"
            if status == "ok":
                ok += 1
            else:
                fail.append(f"{label} {method} {path} -> {code} (ожидали {sorted(expected)}) "
                            f"body={_short(r.text)}")
            print(f"{status:4} {code:>3} {method:4} {path}    [{label}]")
        except Exception as exc:  # noqa: BLE001
            fail.append(f"{label} EXC {type(exc).__name__}: {exc}")
            print(f"FAIL EXC {method:4} {path}    [{label}] {type(exc).__name__}: {exc}")

    print("\n" + "=" * 72)
    print(f"OK: {ok}/{len(CASES)}  FAIL: {len(fail)}")
    for f in fail:
        print("  -", f)
    return 0 if not fail else 1


def _short(t: str, n: int = 160) -> str:
    t = t.replace("\n", " ")
    return t if len(t) <= n else t[:n] + "…"


if __name__ == "__main__":
    sys.exit(run())
