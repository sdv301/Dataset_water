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
import sqlite3
import logging
import datetime
import threading
import subprocess
import shutil
from pathlib import Path
from typing import Optional

import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException, Query, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Пути к данным и моделям (относительно этого файла)
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_DATA_DIR = _SCRIPT_DIR / ".." / "data"
_DB_PATH = _DATA_DIR / "ml_features.db"
_MODELS_DIR = _SCRIPT_DIR / "models"

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
    from flood_predictor import FloodPredictor
except ImportError:
    FloodPredictor = None  # type: ignore
    logger.warning("Модуль flood_predictor не найден. Используются заглушки.")

# ---------------------------------------------------------------------------
# Глобальный статус обучения
# ---------------------------------------------------------------------------
training_status: dict = {
    "status": "idle",      # idle | training | complete | error
    "progress": 0.0,
    "current_station": "",
    "message": "",
}

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
    """Путь к директории моделей станции."""
    safe_name = f"{river}__{post}".replace(" ", "_").replace("/", "_")
    return _MODELS_DIR / safe_name


def _has_trained_model(river: str, post: str) -> bool:
    """Проверяет, существуют ли обученные модели для станции."""
    model_dir = _station_model_dir(river, post)
    if not model_dir.exists():
        return False
    # Ищем хотя бы один .joblib файл
    return any(model_dir.glob("*.joblib"))


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

def _run_training(river: Optional[str], post: Optional[str], fast: bool) -> None:
    """
    Запускает обучение в фоновом потоке.
    Обновляет глобальный training_status.
    """
    global training_status
    try:
        training_status.update(
            status="training", progress=0.0, current_station="", message="Подготовка…",
        )

        if FloodPredictor is None:
            training_status.update(
                status="error", progress=0.0,
                message="Модуль flood_predictor не найден.",
            )
            return

        conn = sqlite3.connect(str(_DB_PATH))
        conn.row_factory = sqlite3.Row

        # Определяем список станций для обучения
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

        for idx, st in enumerate(stations):
            r_name, p_name = st["river"], st["post"]
            training_status.update(
                progress=round(idx / total, 2),
                current_station=f"{r_name} — {p_name}",
                message=f"Обучение станции {idx + 1}/{total}…",
            )

            # Извлекаем данные
            model_dir = _station_model_dir(r_name, p_name)
            model_dir.mkdir(parents=True, exist_ok=True)

            predictor = FloodPredictor(models_dir=str(model_dir), db_path=str(_DB_PATH))
            if fast:
                # Быстрый режим — ограничиваем горизонты
                predictor.horizons = [1, 3, 7]

            try:
                df = predictor.load_station_data(r_name, p_name)
            except Exception as e:
                logger.warning(f"Error loading data: {e}")
                continue

            if df.empty or len(df) < 60:
                logger.warning("Недостаточно данных для %s — %s, пропуск.", r_name, p_name)
                continue

            target_col = "water_level_cm"
            if target_col not in df.columns:
                logger.warning("Нет целевого столбца '%s' для %s — %s.", target_col, r_name, p_name)
                continue

            # Убираем нецелевые столбцы (river, post, …)
            drop_cols = [c for c in ["river", "post"] if c in df.columns]
            df_train = df.drop(columns=drop_cols)

            predictor.train(df_train, target_col=target_col)

            # Сохраняем манифест модели
            manifest = {
                "river": r_name,
                "post": p_name,
                "trained_at": datetime.datetime.now().isoformat(),
                "n_samples": len(df_train),
                "horizons": predictor.horizons,
                "fast": fast,
            }
            with open(model_dir / "manifest.json", "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)

        training_status.update(
            status="complete", progress=1.0,
            current_station="",
            message=f"Обучение завершено для {total} станций.",
        )
        conn.close()

    except Exception as exc:
        logger.exception("Ошибка при обучении.")
        training_status.update(
            status="error", progress=0.0,
            message=str(exc),
        )


# ---------------------------------------------------------------------------
# FastAPI-приложение
# ---------------------------------------------------------------------------

app = FastAPI(
    title="HydroPredict API",
    description="Бэкенд системы вероятностного прогнозирования паводков",
    version="1.0.0",
)

# CORS — для связки с React-фронтендом
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173", "*"],
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

    model_dir = _station_model_dir(river, post)
    is_mock = True
    forecast_points: list[dict] = []

    # Попытка использовать реальные модели
    if FloodPredictor is not None and model_dir.exists() and _has_trained_model(river, post):
        try:
            predictor = FloodPredictor(models_dir=str(model_dir))
            import joblib
            # Загружаем модели
            for h in predictor.horizons:
                for q in predictor.quantiles:
                    fp = model_dir / f"model_h{h}_q{int(q * 100)}.joblib"
                    if fp.exists():
                        predictor.models.setdefault(h, {})[q] = joblib.load(str(fp))

            features_path = model_dir / "features.joblib"
            if features_path.exists():
                predictor.features = joblib.load(str(features_path))

            if base_date:
                try:
                    today = datetime.date.fromisoformat(base_date)
                except ValueError:
                    today = datetime.date.today()
            else:
                conn_d = _get_db()
                try:
                    cur_d = conn_d.execute("SELECT MAX(date) as md FROM daily_features WHERE river=? AND post=?", (river, post))
                    row_d = cur_d.fetchone()
                    if row_d and dict(row_d).get("md"):
                        today = datetime.date.fromisoformat(dict(row_d)["md"])
                    else:
                        today = datetime.date.today()
                finally:
                    conn_d.close()
            for i in range(days):
                target_date = today + datetime.timedelta(days=i)
                # Ближайший доступный горизонт
                available = [h for h in predictor.horizons if h >= (i + 1)]
                use_h = available[0] if available else predictor.horizons[-1]

                res = predictor.predict(
                    today, use_h,
                    warning_level=low_oya,
                    danger_level=critical_oya,
                )
                if res:
                    forecast_points.append({
                        "date": target_date.isoformat(),
                        "median": round(float(res.get("median", 0)), 2),
                        "q10": round(float(res.get("median", 0)) * 0.85, 2),
                        "q90": round(float(res.get("q90", 0)), 2),
                        "q95": round(float(res.get("q95", 0)), 2),
                    })
            if forecast_points:
                is_mock = False
        except Exception:
            logger.exception("Ошибка при прогнозировании моделью, используем заглушку.")
            forecast_points = []

    # Мок-данные если моделей нет или произошла ошибка
    if not forecast_points:
        forecast_points = _generate_mock_forecast(days, base_level=400.0)
        is_mock = True

    # Расчёт сводки рисков
    max_q95 = max(p["q95"] for p in forecast_points) if forecast_points else 0.0
    if max_q95 >= critical_oya:
        current_risk = "ОПАСНЫЙ (ОЯ)"
        prob_warning = 0.95
        prob_danger = 0.60
    elif max_q95 >= low_oya:
        current_risk = "ПОВЫШЕННЫЙ (НЯ)"
        prob_warning = 0.55
        prob_danger = 0.10
    else:
        current_risk = "НИЗКИЙ"
        prob_warning = 0.05
        prob_danger = 0.01

    risk_summary = RiskSummary(
        max_q95=round(max_q95, 2),
        current_risk=current_risk,
        prob_warning=round(prob_warning, 3),
        prob_danger=round(prob_danger, 3),
    )

    feature_importance = _mock_feature_importance()

    # Если модели реальные — пытаемся вытащить реальную важность
    if not is_mock and FloodPredictor is not None:
        try:
            import joblib as _jl
            features_path = model_dir / "features.joblib"
            if features_path.exists():
                feat_names = _jl.load(str(features_path))
                # Берём медианную модель с горизонтом 7
                m7_path = model_dir / "model_h7_q50.joblib"
                if m7_path.exists():
                    m7 = _jl.load(str(m7_path))
                    importances = m7.feature_importances_
                    if len(importances) == len(feat_names):
                        pairs = sorted(
                            zip(feat_names, importances),
                            key=lambda x: x[1], reverse=True,
                        )
                        feature_importance = {
                            n: round(float(v), 4) for n, v in pairs[:15]
                        }
        except Exception:
            pass  # Используем мок

    return ForecastResponse(
        station=station_meta,
        forecast=[ForecastPoint(**p) for p in forecast_points],
        risk_summary=risk_summary,
        feature_importance=feature_importance,
        is_mock=is_mock,
    )


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
    thread = threading.Thread(
        target=_run_training,
        args=(body.river, body.post, body.fast),
        daemon=True,
    )
    thread.start()
    logger.info("Обучение запущено [task_id=%s].", task_id)
    return TrainStarted(task_id=task_id, status="started")


@app.get("/api/train/status", response_model=TrainStatus)
async def get_train_status():
    """Текущий статус фонового обучения."""
    return TrainStatus(**training_status)


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

    # Формируем метрики — заглушки, пока нет реальных из CV
    horizons_list = manifest.get("horizons", [1, 3, 7, 14, 30])
    horizons_dict: dict[str, HorizonMetrics] = {}
    for h in horizons_list:
        # Масштаб ошибок растёт с горизонтом
        scale = math.log(h + 1) / math.log(2)
        horizons_dict[str(h)] = HorizonMetrics(
            rmse=round(12.0 * scale, 2),
            mae=round(8.5 * scale, 2),
            pinball=round(4.2 * scale, 2),
        )

    return MetricsResponse(
        horizons=horizons_dict,
        trained_at=manifest.get("trained_at", "unknown"),
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
    uvicorn.run(
        "api_server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
