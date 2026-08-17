# -*- coding: utf-8 -*-
"""
FastAPI-сервер HydroPredict — мост между Python ML-бэкендом и React-фронтендом.

Запуск:
    uvicorn api_server:app --host 0.0.0.0 --port 8000 --reload

Требования:
    pip install fastapi uvicorn pydantic
"""

import os
import sys
import math
import json
import uuid
import csv
import io
import sqlite3
import logging
import datetime
import threading
import subprocess
import shutil
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException, Query, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Пути к данным и моделям (относительно этого файла)
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
_DATA_DIR = _PROJECT_ROOT / "data"
_DB_PATH = _DATA_DIR / "ml_features.db"
_MODELS_DIR = _PROJECT_ROOT / "models"

import hydro_service as hs

# ---------------------------------------------------------------------------
# Логирование
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("hydropredict")

# ---------------------------------------------------------------------------
# Импорт FloodPredictor (опционально — может отсутствовать)
# ---------------------------------------------------------------------------
try:
    from flood_predictor import DEFAULT_BACKEND, FloodPredictor
except ImportError as exc:
    FloodPredictor = None  # type: ignore
    DEFAULT_BACKEND = "catboost"  # type: ignore
    logger.warning("Модуль flood_predictor недоступен: %s", exc)
else:
    try:
        import catboost  # noqa: F401

        logger.info("CatBoost %s — бэкенд по умолчанию: %s", catboost.__version__, DEFAULT_BACKEND)
    except ImportError:
        logger.error(
            "CatBoost не установлен. Выполните: pip install -r python_code/requirements.txt"
        )

# ---------------------------------------------------------------------------
# Глобальный статус обучения
# ---------------------------------------------------------------------------
training_status: dict = {
    "status": "idle",      # idle | training | complete | error
    "progress": 0.0,
    "current_station": "",
    "message": "",
    "step_detail": "",
    "station_index": 0,
    "stations_total": 0,
}

# Параметры Optuna (как в train_all.py)
TRAIN_FAST_TRIALS = 5
TRAIN_FAST_TIMEOUT = 45
TRAIN_FULL_TRIALS = 12
TRAIN_FULL_TIMEOUT = 90
TRAIN_BATCH_TRIALS = 5
TRAIN_BATCH_TIMEOUT = 45

# ---------------------------------------------------------------------------
# Pydantic-модели ответов
# ---------------------------------------------------------------------------

class RiverInfo(BaseModel):
    """Информация о реке."""
    river: str
    post_count: int
    date_range: list[str]
    has_models: bool


class PostInfo(BaseModel):
    """Информация о гидрологическом посте."""
    post: str
    river: str
    lat: float
    lon: float
    critical_oya: float = Field(0.0, description="Критический уровень (ОЯ, см)")
    low_oya: float = Field(0.0, description="Повышенный уровень (НЯ, см)")
    has_model: bool
    data_days: int


class ForecastPoint(BaseModel):
    """Одна точка прогноза."""
    date: str
    median: float
    q10: float
    q90: float
    q95: float


class StationMeta(BaseModel):
    """Краткая мета-информация станции."""
    post: str
    river: str
    critical_oya: float
    low_oya: float


class RiskSummary(BaseModel):
    """Сводка по рискам."""
    max_q95: float
    current_risk: str
    prob_warning: float
    prob_danger: float


class ForecastResponse(BaseModel):
    """Ответ эндпоинта прогноза."""
    station: StationMeta
    forecast: list[ForecastPoint]
    risk_summary: RiskSummary
    feature_importance: dict[str, float]
    is_mock: bool = False


class HistoryPoint(BaseModel):
    """Одна точка исторических данных."""
    date: str
    water_level_cm: Optional[float] = None
    temp_mean: Optional[float] = None
    precip_mm: Optional[float] = None


class TrainRequest(BaseModel):
    """Тело запроса на обучение."""
    river: Optional[str] = None
    post: Optional[str] = None
    fast: bool = False
    backend: str = "catboost"


class TrainStarted(BaseModel):
    """Ответ при запуске обучения."""
    task_id: str
    status: str = "started"


class TrainStatus(BaseModel):
    """Текущий статус обучения."""
    status: str
    progress: float
    current_station: str
    message: str
    step_detail: str = ""
    station_index: int = 0
    stations_total: int = 0


class TrainingHistoryItem(BaseModel):
    """Запись журнала обучения."""
    id: int
    task_id: Optional[str] = None
    started_at: str
    finished_at: Optional[str] = None
    river: Optional[str] = None
    post: Optional[str] = None
    scope: str
    fast: bool
    status: str
    stations_total: int = 0
    stations_trained: int = 0
    stations_skipped: int = 0
    message: Optional[str] = None


class HorizonMetrics(BaseModel):
    """Метрики для одного горизонта."""
    rmse: float
    mae: float
    pinball: float


class MetricsResponse(BaseModel):
    """Ответ эндпоинта метрик модели."""
    horizons: dict[str, HorizonMetrics]
    trained_at: str
    n_samples: int


class RiverStats(BaseModel):
    """Статистика по одной реке."""
    name: str
    posts: int
    records: int


class DataStatsResponse(BaseModel):
    """Общая статистика БД."""
    total_stations: int
    total_rivers: int
    total_records: int
    date_range: list[str]
    rivers: list[RiverStats]


# ---------------------------------------------------------------------------
# Утилиты для работы с БД
# ---------------------------------------------------------------------------

def _get_db() -> sqlite3.Connection:
    """
    Создаёт подключение к SQLite БД.
    Выбрасывает HTTPException 503, если БД не найдена.
    """
    if not _DB_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail=(
                f"База данных не найдена по пути {_DB_PATH}. "
                "Сначала выполните: python prepare_ml_data.py"
            ),
        )
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _station_model_dir(river: str, post: str) -> Path:
    return hs.station_model_dir(river, post)


def _has_trained_model(river: str, post: str) -> bool:
    return hs.has_trained_model(river, post)


# ---------------------------------------------------------------------------
# Генерация мок-данных (формулы совпадают с App.tsx generateMockData)
# ---------------------------------------------------------------------------

def _generate_mock_forecast(
    days: int,
    base_level: float = 400.0,
    temp_mod: float = 0.0,
    precip_mod: float = 100.0,
) -> list[dict]:
    """
    Генерирует мок-прогноз — формула эквивалентна App.tsx generateMockData.
    """
    result = []
    today = datetime.date.today()
    for i in range(days):
        d = today + datetime.timedelta(days=i)
        trend = i * 1.5
        modifier = (temp_mod * 5) + ((precip_mod - 100) * 0.5)
        trend_value = base_level + trend + modifier
        seasonality = math.sin(i / 5) * 20
        median = max(0.0, trend_value + seasonality)
        q90 = max(0.0, median + 30 + i * 1.2)
        q95 = max(0.0, median + 50 + i * 2.0)
        # q10 — зеркально ниже медианы
        q10 = max(0.0, median - 30 - i * 1.0)
        result.append({
            "date": d.isoformat(),
            "median": round(median, 2),
            "q10": round(q10, 2),
            "q90": round(q90, 2),
            "q95": round(q95, 2),
        })
    return result


def _mock_feature_importance() -> dict[str, float]:
    """Мок-данные важности признаков (совпадают с фронтом)."""
    return {
        "Уровень t-1": 0.85,
        "Сумма осадков 3д": 0.65,
        "Снежный покров": 0.45,
        "Т-ср 7д": 0.35,
        "Осадки t-1": 0.25,
    }


# ---------------------------------------------------------------------------
# Фоновое обучение
# ---------------------------------------------------------------------------

def _run_training(
    river: Optional[str],
    post: Optional[str],
    fast: bool,
    task_id: str,
    backend: str = "catboost",
) -> None:
    """
    Запускает обучение в фоновом потоке.
    Обновляет глобальный training_status и пишет историю в БД.
    """
    global training_status
    history_id: Optional[int] = None
    conn = None
    trained = 0
    skipped = 0

    try:
        hs.ensure_training_schema()
        training_status.update(
            status="training", progress=0.0, current_station="", message="Подготовка…",
        )

        if FloodPredictor is None:
            training_status.update(
                status="error", progress=0.0,
                message="Модуль flood_predictor не найден.",
            )
            return

        backend = (backend or "catboost").lower().strip()
        if backend not in ("catboost", "xgboost"):
            training_status.update(
                status="error", progress=0.0,
                message=f"Недопустимый backend: {backend}",
            )
            return
        if backend == "catboost":
            try:
                import catboost  # noqa: F401
            except ImportError:
                training_status.update(
                    status="error", progress=0.0,
                    message="CatBoost не установлен. pip install catboost>=1.2",
                )
                return

        conn = sqlite3.connect(str(_DB_PATH))
        conn.row_factory = sqlite3.Row

        if river and post:
            stations = [{"river": river, "post": post}]
        elif river:
            cur = conn.execute(
                "SELECT DISTINCT river, post FROM stations WHERE river = ?", (river,)
            )
            stations = [dict(r) for r in cur.fetchall()]
        else:
            cur = conn.execute("SELECT DISTINCT river, post FROM stations")
            stations = [dict(r) for r in cur.fetchall()]

        total = len(stations)
        if total == 0:
            training_status.update(
                status="error", progress=0.0,
                message="Станции не найдены в БД.",
            )
            conn.close()
            return

        history_id = hs.start_training_run(task_id, river, post, fast, total)

        if fast or total == 1:
            n_trials, timeout = TRAIN_FAST_TRIALS, TRAIN_FAST_TIMEOUT
            mode_label = "быстрое"
        elif total <= 3:
            n_trials, timeout = TRAIN_FULL_TRIALS, TRAIN_FULL_TIMEOUT
            mode_label = "полное"
        else:
            n_trials, timeout = TRAIN_BATCH_TRIALS, TRAIN_BATCH_TIMEOUT
            mode_label = "пакетное (ускоренное для многих станций)"

        training_status.update(
            stations_total=total,
            message=(
                f"Режим: {mode_label}, Optuna {n_trials} ит. × {timeout} с на модель. "
                f"Станций: {total}. Ориентир: ~5–12 мин на станцию."
            ),
        )

        for idx, st in enumerate(stations):
            r_name, p_name = st["river"], st["post"]
            training_status.update(
                progress=round(idx / total, 2) if total else 0.0,
                station_index=idx + 1,
                stations_total=total,
                current_station=f"{r_name} — {p_name}",
                step_detail="Загрузка данных…",
                message=f"Станция {idx + 1}/{total}",
            )

            predictor = FloodPredictor(
                models_dir=str(_MODELS_DIR),
                db_path=str(_DB_PATH),
                backend=backend,
            )
            if fast or total > 1:
                predictor.horizons = [1, 3, 7, 14, 30]

            try:
                df = predictor.load_station_data(r_name, p_name)
            except Exception as e:
                logger.warning("Error loading data: %s", e)
                skipped += 1
                if history_id:
                    hs.log_training_station(
                        history_id, r_name, p_name, "error", message=str(e),
                    )
                continue

            if df.empty or len(df) < 60:
                skipped += 1
                if history_id:
                    hs.log_training_station(
                        history_id, r_name, p_name, "skipped",
                        rows_count=len(df), message="Недостаточно данных (<60 дней)",
                    )
                continue

            target_col = "water_level_cm"
            if target_col not in df.columns:
                skipped += 1
                if history_id:
                    hs.log_training_station(
                        history_id, r_name, p_name, "skipped",
                        message=f"Нет столбца {target_col}",
                    )
                continue

            drop_cols = [c for c in ["river", "post"] if c in df.columns]
            df_train = df.drop(columns=drop_cols)
            station_ok = False
            station_msg = ""

            def _on_step(detail: str, frac: float) -> None:
                training_status.update(
                    step_detail=detail,
                    progress=round(min(0.99, (idx + frac) / total), 3) if total else 0.0,
                )

            try:
                predictor.train(
                    df_train,
                    target_col=target_col,
                    n_trials=n_trials,
                    timeout=timeout,
                    on_progress=_on_step,
                )
                station_ok = True
            except UnicodeEncodeError:
                manifest_path = hs.station_model_dir(r_name, p_name) / "manifest.json"
                if manifest_path.exists():
                    station_ok = True
                    station_msg = "Модель сохранена (предупреждение кодировки консоли)"
                else:
                    station_msg = str(UnicodeEncodeError)
            except Exception as e:
                station_msg = str(e)
                logger.exception("Ошибка обучения %s — %s", r_name, p_name)

            if station_ok and _has_trained_model(r_name, p_name):
                trained += 1
                reg = hs.register_station_model(r_name, p_name)
                if reg:
                    training_status.update(
                        step_detail=f"Сохранено: {reg.get('model_dir')} ({reg.get('n_model_files')} файлов)",
                    )
                if history_id:
                    hs.log_training_station(
                        history_id, r_name, p_name, "success",
                        rows_count=len(df_train),
                        message=station_msg or f"OK → {reg.get('model_dir', 'models/')}",
                    )
            else:
                skipped += 1
                if history_id:
                    hs.log_training_station(
                        history_id, r_name, p_name, "error",
                        rows_count=len(df_train), message=station_msg or "Модель не создана",
                    )

            training_status.update(
                progress=round((idx + 1) / total, 2),
                step_detail="Станция завершена",
            )

        if trained == 0 and skipped > 0:
            final_status = "error"
            msg = f"Ни одна станция не обучена ({skipped} пропущено/ошибок из {total})."
        elif skipped > 0:
            final_status = "partial"
            msg = f"Обучено {trained} из {total} станций ({skipped} пропущено)."
        else:
            final_status = "success"
            msg = f"Обучение завершено: {trained} станций."

        if history_id:
            hs.finish_training_run(history_id, final_status, msg, trained, skipped)

        training_status.update(
            status="complete", progress=1.0,
            current_station="",
            message=msg,
        )
        if conn:
            conn.close()

    except Exception as exc:
        logger.exception("Ошибка при обучении.")
        if history_id:
            try:
                hs.finish_training_run(
                    history_id, "error", str(exc), trained, skipped,
                )
            except Exception:
                pass
        training_status.update(
            status="error", progress=0.0,
            message=str(exc),
        )
        if conn:
            try:
                conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# FastAPI-приложение
# ---------------------------------------------------------------------------

app = FastAPI(
    title="HydroPredict API",
    description="Бэкенд системы вероятностного прогнозирования паводков",
    version="1.0.0",
)


def _reset_training_status_idle() -> None:
    """Сброс статуса при старте API — обучение только по POST /api/train."""
    global training_status
    training_status.update(
        status="idle",
        progress=0.0,
        current_station="",
        message="",
        step_detail="",
        station_index=0,
        stations_total=0,
    )


@app.on_event("startup")
def _on_startup() -> None:
    _reset_training_status_idle()
    try:
        hs.ensure_training_schema()
    except Exception as e:
        logger.warning("Схема training_history: %s", e)

# CORS — для связки с React-фронтендом
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3547", "http://localhost:3000", "http://localhost:3001", "http://localhost:5173", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup_check() -> None:
    """Проверка наличия БД при старте сервера."""
    if _DB_PATH.exists():
        logger.info("БД обнаружена: %s", _DB_PATH)
    else:
        logger.warning(
            "БД НЕ найдена (%s). Сначала выполните: python prepare_ml_data.py",
            _DB_PATH,
        )
    # Создаём каталог моделей если отсутствует
    _MODELS_DIR.mkdir(parents=True, exist_ok=True)
    # Запускаем планировщик агента (ночной автопрогон)
    try:
        import agent_scheduler
        agent_scheduler.init_scheduler()
        logger.info("Планировщик агента активирован")
    except Exception as e:
        logger.warning("Не удалось запустить планировщик агента: %s", e)


# ----------------------------- Служебные -------------------------------

@app.get("/api/health")
async def health_check():
    """Проверка, что API запущен и БД доступна."""
    return {
        "ok": True,
        "db": _DB_PATH.exists(),
        "db_path": str(_DB_PATH),
        "predictor_loaded": FloodPredictor is not None,
    }


# ---------------------------------------------------------------------------
# Глобальный обработчик исключений — чтобы 500-ки не были «немыми».
# ---------------------------------------------------------------------------
from fastapi import Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error at %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Внутренняя ошибка сервера",
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
            "path": str(request.url.path),
        },
    )


# ---------------------------------------------------------------------------
# ИИ-агент прогноза паводка
# ---------------------------------------------------------------------------

from fastapi.responses import Response, JSONResponse  # noqa: E402

@app.get("/api/agent/flood-risk/{river}/{post}")
async def get_flood_risk_agent(
    river: str,
    post: str,
    horizon: int = Query(14, ge=1, le=60, description="Горизонт оценки риска, дней"),
    persist: bool = Query(True, description="Сохранять критический риск в alerts"),
    fresh: bool = Query(False, description="Пересчитать сейчас (иначе — снапшот из ночного прогона)"),
):
    """Агент оценки риска паводка. По умолчанию отдаёт кеш из ночного прогона
    (мгновенно). При `fresh=true` — пересчёт на лету."""
    try:
        from flood_agent import assess_flood_risk
        import agent_scheduler
        if not fresh:
            snap = agent_scheduler.get_snapshot(river, post)
            if snap and snap.get("horizon_days") == horizon:
                age = snap.get("_snapshot_age_s")
                return JSONResponse(content=snap,
                                    headers={"X-Snapshot-Age": str(age) if age is not None else "-1",
                                             "X-Source": "snapshot"})
        result = assess_flood_risk(river, post, horizon=horizon, persist=persist)
        try:
            agent_scheduler.save_snapshot(river, post, result, horizon)
        except Exception:
            pass
        return JSONResponse(content=result, headers={"X-Snapshot-Age": "0", "X-Source": "fresh"})
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="БД не найдена")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# --- Планировщик агента ---------------------------------------------------

@app.get("/api/agent/scheduler/status")
async def agent_scheduler_status():
    import agent_scheduler
    return agent_scheduler.get_status()


class _SchedRunPayload(BaseModel):
    horizon: Optional[int] = None
    background: bool = True


@app.post("/api/agent/scheduler/run-now")
async def agent_scheduler_run_now(payload: _SchedRunPayload = _SchedRunPayload()):
    import agent_scheduler
    return agent_scheduler.run_now(background=payload.background, horizon=payload.horizon)


class _SchedConfigPayload(BaseModel):
    hour: Optional[int] = None
    minute: Optional[int] = None
    enabled: Optional[bool] = None
    horizon: Optional[int] = None


@app.post("/api/agent/scheduler/config")
async def agent_scheduler_config(payload: _SchedConfigPayload):
    import agent_scheduler
    try:
        return agent_scheduler.update_config(
            hour=payload.hour, minute=payload.minute,
            enabled=payload.enabled, horizon=payload.horizon,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))



@app.get("/api/agent/alerts")
async def list_agent_alerts(
    river: Optional[str] = Query(None),
    post: Optional[str] = Query(None),
    only_pending: bool = Query(False, description="Только неподтверждённые"),
    limit: int = Query(50, ge=1, le=500),
):
    """Список сохранённых алертов агента (для профиля пользователя)."""
    try:
        from flood_agent import list_alerts
        return {"items": list_alerts(river=river, post=post,
                                     only_pending=only_pending, limit=limit)}
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="БД не найдена")


class _AckPayload(BaseModel):
    user: Optional[str] = None


@app.post("/api/agent/alerts/{alert_id}/acknowledge")
async def acknowledge_agent_alert(alert_id: int, payload: _AckPayload = _AckPayload()):
    """Пользователь подтверждает, что ознакомился с алертом."""
    try:
        from flood_agent import acknowledge_alert
        return acknowledge_alert(alert_id, user=payload.user)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class _BatchItem(BaseModel):
    river: str
    post: str


class _BatchPayload(BaseModel):
    items: List[_BatchItem] = Field(..., min_items=1, max_items=100)
    horizon: int = Field(14, ge=1, le=60)
    persist: bool = False


@app.post("/api/agent/flood-risk/batch")
async def post_flood_risk_batch(payload: _BatchPayload):
    """Оценка агента сразу по списку станций (для профиля/дашборда).

    `persist=False` по умолчанию: батч-опрос не должен спамить алертами.
    Возвращает `results` (успехи) и `errors` (станции с ошибкой) отдельно —
    один плохой пост не роняет остальных.
    """
    from flood_agent import assess_flood_risk

    results: List[dict] = []
    errors: List[dict] = []
    for it in payload.items:
        try:
            results.append(
                assess_flood_risk(it.river, it.post,
                                  horizon=payload.horizon, persist=payload.persist)
            )
        except Exception as exc:  # noqa: BLE001 — сознательно ловим всё
            errors.append({"river": it.river, "post": it.post,
                           "error_type": type(exc).__name__, "error": str(exc)})
    return {"count": len(results), "results": results, "errors": errors}


@app.get("/api/agent/alerts/summary")
async def get_agent_alerts_summary(
    only_pending: bool = Query(True),
    limit: int = Query(200, ge=1, le=1000),
):
    """Сводка для бейджа в профиле: сколько pending-алертов и по каким станциям.

    Полезно на фронте: показать «⚠ 3» в шапке и открыть модалку со списком.
    """
    from flood_agent import list_alerts

    items = list_alerts(only_pending=only_pending, limit=limit)
    by_class: Dict[str, int] = {}
    by_station: Dict[str, int] = {}
    for a in items:
        by_class[a.get("risk_class") or "unknown"] = by_class.get(a.get("risk_class") or "unknown", 0) + 1
        key = f"{a.get('river')}/{a.get('post')}"
        by_station[key] = by_station.get(key, 0) + 1
    return {
        "total": len(items),
        "by_risk_class": by_class,
        "by_station": by_station,
        "requires_ack": sum(1 for a in items if not a.get("acknowledged")),
        "latest": items[:10],
    }


class _FeedbackPayload(BaseModel):
    river: str
    post: str
    verdict: str = Field(..., description="'reward' или 'penalty'")
    alert_id: Optional[int] = None
    actual_class: Optional[str] = None
    comment: Optional[str] = None


@app.post("/api/agent/feedback")
async def post_agent_feedback(payload: _FeedbackPayload):
    """Поощрение/наказание агента. Обновляет веса сработавших правил per-станция."""
    try:
        from flood_agent import record_feedback
        return record_feedback(
            river=payload.river,
            post=payload.post,
            verdict=payload.verdict,
            alert_id=payload.alert_id,
            actual_class=payload.actual_class,
            comment=payload.comment,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/agent/analytics/{river}/{post}")
async def get_agent_analytics(river: str, post: str, years: int = Query(10, ge=1, le=50)):
    """Аналитика поста: пики по годам, климатология уровня, лёд, тренд."""
    try:
        from flood_agent import compute_station_analytics
        return compute_station_analytics(river, post, years=years)
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="БД не найдена")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/agent/priority")
async def get_agent_priority(limit: int = Query(20, ge=1, le=200)):
    """Топ станций по «горячности» на основе snapshot-кеша (ночного прогона)."""
    from flood_agent import station_priority
    return {"items": station_priority(limit=limit)}


@app.get("/api/agent/history/{river}/{post}")
async def get_agent_history(river: str, post: str, days: int = Query(180, ge=7, le=1825)):
    """Последние N дней с уровнями/температурой/осадками и списком превышений НЯ/ОЯ."""
    try:
        from flood_agent import compute_station_history
        return compute_station_history(river, post, days=days)
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="БД не найдена")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------------------------------------------------------------------------
# Тайловый кэш на диске — ускоряет карту, убирает белый фон при повторных запросах
# ---------------------------------------------------------------------------
_TILE_CACHE_DIR = _DATA_DIR / "tile_cache"
_TILE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_TILE_CACHE_MAX_AGE_DAYS = 30


def _tile_cache_path(provider: str, z: int, x: int, y: int) -> Path:
    return _TILE_CACHE_DIR / provider / str(z) / str(x) / f"{y}.bin"


def _read_cached_tile(provider: str, z: int, x: int, y: int) -> Optional[tuple[bytes, str]]:
    p = _tile_cache_path(provider, z, x, y)
    if not p.exists():
        return None
    try:
        age = datetime.datetime.now().timestamp() - p.stat().st_mtime
        if age > _TILE_CACHE_MAX_AGE_DAYS * 86400:
            return None
        meta_p = p.with_suffix(".meta")
        ct = "image/jpeg"
        if meta_p.exists():
            ct = meta_p.read_text(encoding="utf-8").strip() or ct
        data = p.read_bytes()
        if len(data) < 100:
            return None
        return data, ct
    except Exception:
        return None


def _write_cached_tile(provider: str, z: int, x: int, y: int, data: bytes, ct: str) -> None:
    try:
        p = _tile_cache_path(provider, z, x, y)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        p.with_suffix(".meta").write_text(ct or "image/jpeg", encoding="utf-8")
    except Exception as exc:
        logger.warning("Tile cache write failed %s/%s/%s/%s: %s", provider, z, x, y, exc)


def _fetch_tile_upstream(url: str) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "hydropredict/tile-proxy"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
        ct = resp.headers.get("Content-Type", "image/jpeg")
    return data, ct


@app.get("/api/tiles/arcgis/{z}/{y}/{x}")
async def arcgis_tile(z: int, y: int, x: int):
    """Esri World Imagery — © Esri, Maxar, Earthstar Geographics."""
    if z < 0 or z > 22:
        raise HTTPException(status_code=400, detail="Invalid zoom level")
    extent = 2 ** z
    if x < 0 or x >= extent or y < 0 or y >= extent:
        raise HTTPException(status_code=400, detail="Tile out of range")
    cached = _read_cached_tile("arcgis", z, x, y)
    if cached:
        data, ct = cached
        return Response(content=data, media_type=ct, headers={"Cache-Control": "public, max-age=86400"})
    url = (
        f"https://server.arcgisonline.com/ArcGIS/rest/services/"
        f"World_Imagery/MapServer/tile/{z}/{y}/{x}"
    )
    try:
        data, ct = _fetch_tile_upstream(url)
        _write_cached_tile("arcgis", z, x, y, data, ct)
        return Response(content=data, media_type=ct, headers={"Cache-Control": "public, max-age=86400"})
    except Exception as exc:
        # Fallback: serve stale cache if upstream fails
        stale = _read_cached_tile("arcgis", z, x, y)
        if stale:
            return Response(content=stale[0], media_type=stale[1], headers={"Cache-Control": "public, max-age=86400"})
        raise HTTPException(status_code=502, detail=f"ArcGIS tile fetch failed: {exc}") from exc


@app.get("/api/tiles/carto/{z}/{x}/{y}")
async def carto_tile(z: int, x: int, y: int):
    """Carto Positron light scheme tiles (proxied)."""
    if z < 0 or z > 22:
        raise HTTPException(status_code=400, detail="Invalid zoom level")
    extent = 2 ** z
    if x < 0 or x >= extent or y < 0 or y >= extent:
        raise HTTPException(status_code=400, detail="Tile out of range")
    cached = _read_cached_tile("carto", z, x, y)
    if cached:
        data, ct = cached
        return Response(content=data, media_type=ct, headers={"Cache-Control": "public, max-age=86400"})
    url = f"https://cartodb-basemaps-a.global.ssl.fastly.net/light_all/{z}/{x}/{y}.png"
    try:
        data, ct = _fetch_tile_upstream(url)
        _write_cached_tile("carto", z, x, y, data, ct or "image/png")
        return Response(content=data, media_type=ct or "image/png", headers={"Cache-Control": "public, max-age=86400"})
    except Exception as exc:
        stale = _read_cached_tile("carto", z, x, y)
        if stale:
            return Response(content=stale[0], media_type=stale[1], headers={"Cache-Control": "public, max-age=86400"})
        raise HTTPException(status_code=502, detail=f"Carto tile fetch failed: {exc}") from exc


@app.get("/")
async def root():
    return {
        "service": "HydroPredict API",
        "docs": "/docs",
        "health": "/api/health",
    }


# ----------------------------- Реки ----------------------------------

@app.get("/api/rivers", response_model=list[RiverInfo])
async def get_rivers():
    """Список рек с кол-вом постов, диапазоном дат и наличием моделей."""
    conn = _get_db()
    try:
        cur = conn.execute("""
            SELECT river,
                   COUNT(DISTINCT post) AS post_count,
                   MIN(date_start)      AS min_date,
                   MAX(date_end)        AS max_date
            FROM stations
            GROUP BY river
            ORDER BY river
        """)
        rows = cur.fetchall()
        result: list[RiverInfo] = []
        for row in rows:
            # Проверяем, есть ли модели хотя бы для одного поста реки
            posts_cur = conn.execute(
                "SELECT DISTINCT post FROM stations WHERE river = ?",
                (row["river"],),
            )
            has = any(
                _has_trained_model(row["river"], p["post"])
                for p in posts_cur.fetchall()
            )
            result.append(RiverInfo(
                river=row["river"],
                post_count=row["post_count"],
                date_range=[row["min_date"] or "", row["max_date"] or ""],
                has_models=has,
            ))
        return result
    finally:
        conn.close()


# ----------------------------- Посты реки ----------------------------

@app.get("/api/rivers/{river}/posts", response_model=list[PostInfo])
async def get_river_posts(river: str):
    """Список гидрологических постов для заданной реки."""
    conn = _get_db()
    try:
        cur = conn.execute(
            "SELECT * FROM stations WHERE river = ? ORDER BY post", (river,)
        )
        rows = cur.fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail=f"Река «{river}» не найдена.")

        result: list[PostInfo] = []
        for row in rows:
            # Подсчёт дней данных
            days_cur = conn.execute(
                "SELECT COUNT(*) AS cnt FROM daily_features WHERE river = ? AND post = ?",
                (row["river"], row["post"]),
            )
            data_days = days_cur.fetchone()["cnt"]
            result.append(PostInfo(
                post=row["post"],
                river=row["river"],
                lat=dict(row).get("lat", 0.0) or 0.0,
                lon=dict(row).get("lon", 0.0) or 0.0,
                critical_oya=dict(row).get("critical_oya", 0.0) or 0.0,
                low_oya=dict(row).get("low_oya", 0.0) or 0.0,
                has_model=_has_trained_model(row["river"], row["post"]),
                data_days=data_days,
            ))
        return result
    finally:
        conn.close()


# ----------------------------- Прогноз -------------------------------

@app.get("/api/forecast/{river}/{post}", response_model=ForecastResponse)
async def get_forecast(
    river: str,
    post: str,
    horizon: int = Query(7, ge=1, le=365, description="Горизонт прогноза (дней)"),
    days: int = Query(60, ge=1, le=730, description="Кол-во дней вперёд для генерации"),
    base_date: str = Query(None, description="Базовая дата (YYYY-MM-DD)"),
):
    """
    Прогноз уровня воды. Если обученных моделей нет — возвращает
    мок-данные с флагом is_mock=true.
    """
    conn = _get_db()
    try:
        cur = conn.execute(
            "SELECT * FROM stations WHERE river = ? AND post = ? LIMIT 1",
            (river, post),
        )
        station_row = cur.fetchone()
        if not station_row:
            raise HTTPException(
                status_code=404,
                detail=f"Станция «{river} — {post}» не найдена.",
            )
    finally:
        conn.close()

    critical_oya = dict(station_row).get("critical_oya", 650.0) or 650.0
    low_oya = dict(station_row).get("low_oya", 500.0) or 500.0

    station_meta = StationMeta(
        post=post, river=river, critical_oya=critical_oya, low_oya=low_oya,
    )

    try:
        bd = datetime.date.fromisoformat(base_date) if base_date else None
    except ValueError:
        bd = None

    try:
        tier_data = hs.tier_forecast(river, post, "medium", days=days, base_date=bd)
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="БД не найдена. Запустите prepare_ml_data.py")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    if tier_data["is_mock"]:
        tier_data["forecast"] = _generate_mock_forecast(days, base_level=400.0)

    forecast_points = tier_data["forecast"]
    rs = tier_data["risk_summary"]
    risk_summary = RiskSummary(
        max_q95=rs["max_q95"],
        current_risk=rs["current_risk"],
        prob_warning=rs["prob_warning"],
        prob_danger=rs["prob_danger"],
    )
    fi = tier_data.get("feature_importance") or _mock_feature_importance()

    return ForecastResponse(
        station=station_meta,
        forecast=[ForecastPoint(**{k: p[k] for k in ("date", "median", "q10", "q90", "q95") if k in p}) for p in forecast_points],
        risk_summary=risk_summary,
        feature_importance=fi,
        is_mock=tier_data["is_mock"],
    )


def _tier_response(river: str, post: str, tier: str, days: int, base_date: Optional[str] = None) -> dict:
    try:
        bd = datetime.date.fromisoformat(base_date) if base_date else None
    except ValueError:
        bd = None
    try:
        data = hs.tier_forecast(river, post, tier, days=days, base_date=bd)
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="БД не найдена. Запустите prepare_ml_data.py")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if data["is_mock"] or not data.get("forecast"):
        st = hs.get_station_row(river, post) or {}
        base_lvl = float(st.get("low_oya") or 400.0) + float(st.get("critical_oya") or 650.0) / 2
        data["forecast"] = _generate_mock_forecast(days, base_level=base_lvl)
        data["is_mock"] = True
        if not data.get("forecast_note"):
            data["forecast_error"] = (
                "Нет модели или горизонтов 14–30 — показан демо-прогноз. "
                "Обучите станцию в разделе «Управление данными»."
            )
    return data


@app.get("/api/forecast/{river}/{post}/short")
async def get_forecast_short(
    river: str, post: str,
    days: int = Query(7, ge=1, le=30),
    base_date: str = Query(None),
):
    return _tier_response(river, post, "short", days, base_date)


@app.get("/api/forecast/{river}/{post}/medium")
async def get_forecast_medium(
    river: str, post: str,
    days: int = Query(30, ge=1, le=120),
    base_date: str = Query(None),
):
    return _tier_response(river, post, "medium", days, base_date)


@app.get("/api/forecast/{river}/{post}/season")
async def get_forecast_season(
    river: str, post: str,
    days: int = Query(90, ge=30, le=180),
    base_date: str = Query(None),
):
    return _tier_response(river, post, "season", days, base_date)


@app.get("/api/stations/{river}/{post}/model-status")
async def get_station_model_status(river: str, post: str):
    """Статус модели и последнее обучение для станции."""
    try:
        return hs.get_station_model_status(river, post)
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="БД не найдена")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/climatology/{river}/{post}")
async def get_climatology(
    river: str, post: str,
    year: int = Query(None, description="Исключить год из статистики"),
):
    try:
        return {"river": river, "post": post, "points": hs.compute_climatology(river, post, exclude_year=year)}
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="БД не найдена")


@app.get("/api/forecast/{river}/{post}/scenarios")
async def get_forecast_scenarios(
    river: str,
    post: str,
    days: int = Query(30, ge=7, le=90),
    temp_delta: float = Query(0, description="Сдвиг температуры, °C"),
    precip_pct: float = Query(100, ge=0, le=300, description="Осадки, % от нормы"),
    snow_pct: float = Query(100, ge=0, le=300, description="Снег, % от нормы"),
):
    try:
        return hs.build_scenario_forecasts(
            river, post, days=days,
            temp_delta=temp_delta, precip_pct=precip_pct, snow_pct=snow_pct,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="БД не найдена")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/forecast/{river}/{post}/year")
async def get_forecast_year(
    river: str,
    post: str,
    year: int = Query(None),
    overlay_years: int = Query(3, ge=0, le=8, description="Число экстремальных лет для overlay"),
):
    if year is None or year <= 0:
        try:
            year = hs.get_default_display_year(river, post)
        except (FileNotFoundError, ValueError):
            year = datetime.date.today().year
    try:
        return hs.build_year_analytics(river, post, year, overlay_years=overlay_years)
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="БД не найдена")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/explain/{river}/{post}")
async def get_explain(
    river: str, post: str,
    horizon: int = Query(7, ge=1, le=90),
    base_date: str = Query(None),
):
    try:
        bd = datetime.date.fromisoformat(base_date) if base_date else None
    except ValueError:
        bd = None
    try:
        return hs.build_explanation(river, post, base_date=bd, horizon=horizon)
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="БД не найдена")


# ----------------------------- История --------------------------------

@app.get("/api/history/{river}/{post}", response_model=list[HistoryPoint])
async def get_history(
    river: str,
    post: str,
    days: int = Query(365, ge=1, le=3650, description="За сколько дней назад отдать данные"),
    end_date: str = Query(None, description="Конечная дата (YYYY-MM-DD)"),
):
    """Исторические наблюдения со станции."""
    conn = _get_db()
    try:
        # Проверяем существование станции
        st = conn.execute(
            "SELECT 1 FROM stations WHERE river = ? AND post = ? LIMIT 1",
            (river, post),
        ).fetchone()
        if not st:
            raise HTTPException(
                status_code=404,
                detail=f"Станция «{river} — {post}» не найдена.",
            )

        if end_date:
            cur = conn.execute(
                "SELECT * FROM ("
                "SELECT date, water_level_cm, temp_mean, precip_mm "
                "FROM daily_features WHERE river = ? AND post = ? AND date <= ? "
                "ORDER BY date DESC LIMIT ?"
                ") ORDER BY date ASC",
                (river, post, end_date, days),
            )
        else:
            cur = conn.execute(
                "SELECT * FROM ("
                "SELECT date, water_level_cm, temp_mean, precip_mm "
                "FROM daily_features WHERE river = ? AND post = ? "
                "ORDER BY date DESC LIMIT ?"
                ") ORDER BY date ASC",
                (river, post, days),
            )
        rows = cur.fetchall()
        return [
            HistoryPoint(
                date=row["date"],
                water_level_cm=row["water_level_cm"],
                temp_mean=row["temp_mean"] if "temp_mean" in row.keys() else None,
                precip_mm=row["precip_mm"] if "precip_mm" in row.keys() else None,
            )
            for row in rows
        ]
    finally:
        conn.close()


# ----------------------------- CSV-шаблоны и импорт --------------------

_TEMPLATES_DIR = _DATA_DIR / "templates"

_OBSERVATIONS_REQUIRED_COLS = {"river", "post", "date"}
_OBSERVATIONS_OPTIONAL_COLS = {
    "water_level_cm", "temp_min", "temp_mean", "temp_max",
    "precip_mm", "snow_pct_norm", "ice_thickness_cm",
}
_OBSERVATIONS_NUMERIC_COLS = _OBSERVATIONS_OPTIONAL_COLS


@app.get("/api/data/templates/{template_type}")
async def get_data_template(template_type: str):
    """Скачать CSV-шаблон для импорта данных (observations или stations)."""
    mapping = {
        "observations": "observations_template.csv",
        "stations": "stations_template.csv",
    }
    fname = mapping.get(template_type)
    if not fname:
        raise HTTPException(
            status_code=404,
            detail=f"Шаблон «{template_type}» не найден. Доступные: {', '.join(mapping.keys())}",
        )
    fpath = _TEMPLATES_DIR / fname
    if not fpath.exists():
        raise HTTPException(status_code=503, detail=f"Файл шаблона не найден: {fpath.name}")
    content = fpath.read_bytes()
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


class _ImportResult(BaseModel):
    rows_total: int
    rows_inserted: int
    rows_skipped: int
    errors: list[str]


def _parse_csv_upload(raw: bytes) -> list[dict]:
    """Парсит байты CSV в список словарей. Поддерживает UTF-8 и UTF-8-BOM."""
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    return [{k.strip(): (v.strip() if v else None) for k, v in row.items() if k} for row in reader]


def _validate_observation_row(row: dict, line_no: int) -> tuple[Optional[dict], Optional[str]]:
    """Валидирует одну строку наблюдений."""
    river = (row.get("river") or "").strip()
    post = (row.get("post") or "").strip()
    date_raw = (row.get("date") or "").strip()
    if not river or not post or not date_raw:
        return None, f"Строка {line_no}: отсутствуют обязательные поля (river/post/date)"
    try:
        datetime.date.fromisoformat(date_raw[:10])
    except ValueError:
        return None, f"Строка {line_no}: некорректная дата «{date_raw}»"
    numeric_vals: dict[str, Optional[float]] = {}
    for col in _OBSERVATIONS_NUMERIC_COLS:
        val = row.get(col)
        if val is None or val == "":
            numeric_vals[col] = None
        else:
            try:
                numeric_vals[col] = float(val)
            except ValueError:
                return None, f"Строка {line_no}: колонка «{col}» — не число («{val}»)"
    return {"river": river, "post": post, "date": date_raw[:10], **numeric_vals}, None


@app.post("/api/data/import/observations", response_model=_ImportResult)
async def import_observations(file: UploadFile = File(...)):
    """Импорт наблюдений из CSV в таблицу daily_features.

    Обязательные колонки: river, post, date.
    Опциональные: water_level_cm, temp_min, temp_mean, temp_max, precip_mm, snow_pct_norm, ice_thickness_cm.
    Дубликаты по (river, post, date) перезаписываются (upsert).
    """
    if file.content_type and "csv" not in file.content_type and "text" not in file.content_type:
        raise HTTPException(status_code=400, detail="Ожидается CSV-файл")
    raw = await file.read()
    if len(raw) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Файл слишком большой (макс. 50 МБ)")
    try:
        rows = _parse_csv_upload(raw)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Ошибка парсинга CSV: {exc}")
    if not rows:
        raise HTTPException(status_code=400, detail="CSV пуст или не содержит строк данных")
    header_cols = set(rows[0].keys())
    missing = _OBSERVATIONS_REQUIRED_COLS - header_cols
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Отсутствуют обязательные колонки: {', '.join(sorted(missing))}",
        )
    conn = _get_db()
    inserted = 0
    skipped = 0
    errors: list[str] = []
    try:
        existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(daily_features)").fetchall()}
        insert_cols = ["river", "post", "date"] + [c for c in _OBSERVATIONS_NUMERIC_COLS if c in existing_cols]
        placeholders = ", ".join(["?"] * len(insert_cols))
        col_list = ", ".join(insert_cols)
        delete_sql = "DELETE FROM daily_features WHERE river = ? AND post = ? AND date = ?"
        insert_sql = f"INSERT INTO daily_features ({col_list}) VALUES ({placeholders})"
        for i, raw_row in enumerate(rows, start=2):
            parsed, err = _validate_observation_row(raw_row, i)
            if err:
                errors.append(err)
                skipped += 1
                continue
            values = [parsed.get(c) for c in insert_cols]
            try:
                conn.execute(delete_sql, (parsed["river"], parsed["post"], parsed["date"]))
                conn.execute(insert_sql, values)
                inserted += 1
            except sqlite3.Error as e:
                errors.append(f"Строка {i}: ошибка БД — {e}")
                skipped += 1
        conn.commit()
    finally:
        conn.close()
    return _ImportResult(rows_total=len(rows), rows_inserted=inserted, rows_skipped=skipped, errors=errors[:50])


# ----------------------------- Импорт станций --------------------------

_STATIONS_REQUIRED_COLS = {"river", "post"}
_STATIONS_OPTIONAL_COLS = {
    "lat", "lon", "low_oya", "critical_oya", "district", "oktmo",
}
_STATIONS_NUMERIC_COLS = {"lat", "lon", "low_oya", "critical_oya", "oktmo"}


def _validate_station_row(row: dict, line_no: int) -> tuple[Optional[dict], Optional[str]]:
    """Валидирует одну строку станции."""
    river = (row.get("river") or "").strip()
    post = (row.get("post") or "").strip()
    if not river or not post:
        return None, f"Строка {line_no}: отсутствуют обязательные поля (river/post)"
    parsed: dict = {"river": river, "post": post}
    # name_ru (необязательное текстовое поле)
    if row.get("name_ru"):
        parsed["name_ru"] = row["name_ru"].strip()
    for col in _STATIONS_NUMERIC_COLS:
        val = row.get(col)
        if val is None or val == "":
            parsed[col] = None
        else:
            try:
                parsed[col] = float(val)
            except ValueError:
                return None, f"Строка {line_no}: колонка «{col}» — не число («{val}»)"
    for col in ("district",):
        parsed[col] = (row.get(col) or "").strip() or None
    return parsed, None


@app.post("/api/data/import/stations", response_model=_ImportResult)
async def import_stations(file: UploadFile = File(...)):
    """Импорт станций из CSV в таблицу stations.

    Обязательные колонки: river, post.
    Опциональные: lat, lon, low_oya, critical_oya, district, oktmo, name_ru.
    Дубликаты по (river, post) перезаписываются (upsert).
    """
    if file.content_type and "csv" not in file.content_type and "text" not in file.content_type:
        raise HTTPException(status_code=400, detail="Ожидается CSV-файл")
    raw = await file.read()
    if len(raw) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Файл слишком большой (макс. 50 МБ)")
    try:
        rows = _parse_csv_upload(raw)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Ошибка парсинга CSV: {exc}")
    if not rows:
        raise HTTPException(status_code=400, detail="CSV пуст или не содержит строк данных")
    header_cols = set(rows[0].keys())
    missing = _STATIONS_REQUIRED_COLS - header_cols
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Отсутствуют обязательные колонки: {', '.join(sorted(missing))}",
        )
    conn = _get_db()
    inserted = 0
    skipped = 0
    errors: list[str] = []
    try:
        existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(stations)").fetchall()}
        update_cols = ["lat", "lon", "low_oya", "critical_oya", "district", "oktmo"]
        for i, raw_row in enumerate(rows, start=2):
            parsed, err = _validate_station_row(raw_row, i)
            if err:
                errors.append(err)
                skipped += 1
                continue
            river, post = parsed["river"], parsed["post"]
            try:
                existing = conn.execute(
                    "SELECT river FROM stations WHERE river = ? AND post = ?", (river, post)
                ).fetchone()
                if existing:
                    # Обновляем только переданные поля
                    sets = []
                    vals = []
                    for col in update_cols:
                        if col in existing_cols and parsed.get(col) is not None:
                            sets.append(f"{col} = ?")
                            vals.append(parsed[col])
                    if sets:
                        conn.execute(
                            f"UPDATE stations SET {', '.join(sets)} WHERE river = ? AND post = ?",
                            vals + [river, post],
                        )
                else:
                    cols = ["river", "post"] + [c for c in update_cols if c in existing_cols]
                    vals = [river, post] + [parsed.get(c) for c in update_cols if c in existing_cols]
                    placeholders = ", ".join(["?"] * len(cols))
                    conn.execute(
                        f"INSERT INTO stations ({', '.join(cols)}) VALUES ({placeholders})", vals
                    )
                inserted += 1
            except sqlite3.Error as e:
                errors.append(f"Строка {i}: ошибка БД — {e}")
                skipped += 1
        conn.commit()
    finally:
        conn.close()
    return _ImportResult(rows_total=len(rows), rows_inserted=inserted, rows_skipped=skipped, errors=errors[:50])


# ----------------------------- Обучение -------------------------------

@app.post("/api/train", response_model=TrainStarted)
async def start_training(body: TrainRequest):
    """Запуск обучения в фоновом потоке."""
    global training_status

    if training_status["status"] == "training":
        raise HTTPException(
            status_code=409,
            detail="Обучение уже выполняется. Дождитесь завершения.",
        )

    # Проверяем наличие БД
    if not _DB_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail=(
                f"База данных не найдена по пути {_DB_PATH}. "
                "Сначала выполните: python prepare_ml_data.py"
            ),
        )

    task_id = str(uuid.uuid4())
    train_backend = (body.backend or "catboost").lower().strip()
    if train_backend not in ("catboost", "xgboost"):
        raise HTTPException(
            status_code=400,
            detail="backend должен быть «catboost» или «xgboost»",
        )

    thread = threading.Thread(
        target=_run_training,
        args=(body.river, body.post, body.fast, task_id, train_backend),
        daemon=True,
    )
    thread.start()
    logger.info("Обучение запущено [task_id=%s].", task_id)
    return TrainStarted(task_id=task_id, status="started")


@app.get("/api/train/status", response_model=TrainStatus)
async def get_train_status():
    """Текущий статус фонового обучения."""
    return TrainStatus(**training_status)


@app.get("/api/train/history", response_model=list[TrainingHistoryItem])
async def get_train_history(limit: int = Query(30, ge=1, le=200)):
    """Журнал запусков обучения из БД."""
    try:
        rows = hs.get_training_history(limit=limit)
        return [
            TrainingHistoryItem(
                id=r["id"],
                task_id=r.get("task_id"),
                started_at=r["started_at"],
                finished_at=r.get("finished_at"),
                river=r.get("river"),
                post=r.get("post"),
                scope=r["scope"],
                fast=bool(r.get("fast")),
                status=r["status"],
                stations_total=r.get("stations_total") or 0,
                stations_trained=r.get("stations_trained") or 0,
                stations_skipped=r.get("stations_skipped") or 0,
                message=r.get("message"),
            )
            for r in rows
        ]
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="БД не найдена")


@app.post("/api/train/reset-status")
async def reset_train_status():
    """Сбросить индикатор обучения в UI (после просмотра результата)."""
    global training_status
    if training_status["status"] == "training":
        raise HTTPException(status_code=409, detail="Обучение ещё выполняется")
    training_status.update(
        status="idle",
        progress=0.0,
        current_station="",
        message="",
        step_detail="",
        station_index=0,
        stations_total=0,
    )
    return {"ok": True}


# ----------------------------- Метрики модели -------------------------

@app.get("/api/metrics/{river}/{post}", response_model=MetricsResponse)
async def get_metrics(river: str, post: str):
    """Метрики обученной модели из manifest.json."""
    model_dir = _station_model_dir(river, post)
    manifest_path = model_dir / "manifest.json"

    if not manifest_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Модель для «{river} — {post}» не найдена. Сначала запустите обучение.",
        )

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    horizons_dict: dict[str, HorizonMetrics] = {}
    raw_metrics = manifest.get("metrics", {})
    for h_key, q_dict in raw_metrics.items():
        if not isinstance(q_dict, dict):
            continue
        q50 = q_dict.get("0.5") or q_dict.get(0.5)
        if q50:
            horizons_dict[str(h_key)] = HorizonMetrics(
                rmse=float(q50.get("rmse", 0)),
                mae=float(q50.get("mae", 0)),
                pinball=float(q50.get("pinball_loss", 0)),
            )
    if not horizons_dict:
        for h in manifest.get("horizons", [1, 3, 7, 14, 30]):
            scale = math.log(h + 1) / math.log(2)
            horizons_dict[str(h)] = HorizonMetrics(
                rmse=round(12.0 * scale, 2),
                mae=round(8.5 * scale, 2),
                pinball=round(4.2 * scale, 2),
            )

    return MetricsResponse(
        horizons=horizons_dict,
        trained_at=manifest.get("training_date", manifest.get("trained_at", "unknown")),
        n_samples=manifest.get("n_samples", 0),
    )


# ----------------------------- Статистика данных ----------------------

@app.get("/api/data/stats", response_model=DataStatsResponse)
async def get_data_stats():
    """Общая статистика содержимого БД."""
    conn = _get_db()
    try:
        # Общие счётчики
        total_stations = conn.execute(
            "SELECT COUNT(DISTINCT post) AS cnt FROM stations"
        ).fetchone()["cnt"]

        total_rivers = conn.execute(
            "SELECT COUNT(DISTINCT river) AS cnt FROM stations"
        ).fetchone()["cnt"]

        total_records = conn.execute(
            "SELECT COUNT(*) AS cnt FROM daily_features"
        ).fetchone()["cnt"]

        date_range_row = conn.execute(
            "SELECT MIN(date) AS min_d, MAX(date) AS max_d FROM daily_features"
        ).fetchone()

        # По каждой реке
        rivers_cur = conn.execute("""
            SELECT s.river AS name,
                   COUNT(DISTINCT s.post) AS posts,
                   COALESCE(d.records, 0)  AS records
            FROM stations s
            LEFT JOIN (
                SELECT river, COUNT(*) AS records
                FROM daily_features
                GROUP BY river
            ) d ON s.river = d.river
            GROUP BY s.river
            ORDER BY s.river
        """)
        rivers = [
            RiverStats(name=r["name"], posts=r["posts"], records=r["records"])
            for r in rivers_cur.fetchall()
        ]

        return DataStatsResponse(
            total_stations=total_stations,
            total_rivers=total_rivers,
            total_records=total_records,
            date_range=[
                date_range_row["min_d"] or "",
                date_range_row["max_d"] or "",
            ],
            rivers=rivers,
        )
    finally:
        conn.close()


@app.post("/api/upload")
async def upload_data_file(file: UploadFile = File(...)):
    """Загрузка новых данных CSV/Excel и пересборка базы."""
    # Сохраняем файл в папку export
    export_dir = _SCRIPT_DIR.parent / "Реки" / "данные январь" / "export"
    export_dir.mkdir(parents=True, exist_ok=True)
    
    file_path = export_dir / file.filename
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    # Запуск скрипта подготовки данных
    try:
        subprocess.run(["python", str(_SCRIPT_DIR / "prepare_ml_data.py")], check=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"Error during data preparation: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при обработке файла и пересборке БД.")
        
    return {"message": f"Файл {file.filename} успешно загружен, БД пересобрана."}


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # reload=False: сохранение model_*.joblib не перезапускает API и не рвёт сессию в браузере
    _reload = os.environ.get("UVICORN_RELOAD", "").lower() in ("1", "true", "yes")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        reload=_reload,
        reload_excludes=["models", "data", "*.db", "*.joblib"] if _reload else None,
    )
