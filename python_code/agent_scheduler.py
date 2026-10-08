# -*- coding: utf-8 -*-
"""Планировщик ночного автопрогона агента + snapshot-кеш.

Простое решение без внешних зависимостей: фоновый поток с циклом sleep.
Ежедневно в заданное время (по умолчанию 03:00 Asia/Yakutsk = UTC+09)
последовательно вызывает assess_flood_risk для всех (river, post) из БД
и сохраняет результат в таблицу agent_snapshots.
"""
from __future__ import annotations

import copy
import datetime as _dt
import gc
import json
import logging
import os
import sqlite3
import threading
import time
import concurrent.futures
from typing import Any, Dict, List, Optional, Tuple

import hydro_service as hs

logger = logging.getLogger("hydropredict.scheduler")

_YAKUTSK_TZ = _dt.timezone(_dt.timedelta(hours=9))
_CONFIG_PATH = hs.PROJECT_ROOT / "data" / "agent_config.json"

PRESETS: Dict[str, Dict[str, float]] = {
    "sensitive": {"will_flood_threshold": 0.60, "verdict_red_threshold": 0.60},
    "balanced": {"will_flood_threshold": 0.75, "verdict_red_threshold": 0.75},
    "conservative": {"will_flood_threshold": 0.85, "verdict_red_threshold": 0.85},
}

_DEFAULT_CONFIG_V2: Dict[str, Any] = {
    "version": 2,
    "enabled": True,
    "schedule": {
        "hour": 3,
        "minute": 0,
        "timezone": "Asia/Yakutsk",
    },
    "horizon_days": 14,
    "thresholds": {
        "preset": "balanced",
        "will_flood_threshold": 0.75,
        "verdict_red_threshold": 0.75,
    },
    "safety": {
        "station_timeout_seconds": 15.0,
        "station_delay_seconds": 0.05,
        "max_workers": 1,
        "gc_frequency": 5,
    },
    "challenger": {
        "enabled": False,
    },
}

def _detect_preset(will_flood_threshold: float, verdict_red_threshold: float) -> str:
    for name, p in PRESETS.items():
        if abs(p["will_flood_threshold"] - will_flood_threshold) < 1e-4 and \
           abs(p["verdict_red_threshold"] - verdict_red_threshold) < 1e-4:
            return name
    return "custom"

def _apply_env_overrides(cfg: Dict[str, Any]) -> Dict[str, Any]:
    v_en = os.getenv("AGENT_ENABLED")
    if v_en is not None:
        cfg["enabled"] = v_en.strip().lower() in ("1", "true", "yes", "on")

    v_h = os.getenv("AGENT_SCHEDULE_HOUR")
    if v_h is not None:
        try:
            h = int(v_h.strip())
            if 0 <= h <= 23:
                cfg["schedule"]["hour"] = h
        except ValueError:
            pass

    v_m = os.getenv("AGENT_SCHEDULE_MINUTE")
    if v_m is not None:
        try:
            m = int(v_m.strip())
            if 0 <= m <= 59:
                cfg["schedule"]["minute"] = m
        except ValueError:
            pass

    v_hz = os.getenv("AGENT_HORIZON")
    if v_hz is not None:
        try:
            hz = int(v_hz.strip())
            if 1 <= hz <= 60:
                cfg["horizon_days"] = hz
        except ValueError:
            pass

    v_pre = os.getenv("AGENT_THRESHOLD_PRESET")
    if v_pre and v_pre.strip().lower() in PRESETS:
        p_name = v_pre.strip().lower()
        cfg["thresholds"]["preset"] = p_name
        cfg["thresholds"]["will_flood_threshold"] = PRESETS[p_name]["will_flood_threshold"]
        cfg["thresholds"]["verdict_red_threshold"] = PRESETS[p_name]["verdict_red_threshold"]

    v_wf = os.getenv("AGENT_WILL_FLOOD_THRESHOLD")
    if v_wf is not None:
        try:
            wf = float(v_wf.strip())
            if 0.1 <= wf <= 0.99:
                cfg["thresholds"]["will_flood_threshold"] = wf
                cfg["thresholds"]["preset"] = _detect_preset(wf, cfg["thresholds"]["verdict_red_threshold"])
        except ValueError:
            pass

    v_rd = os.getenv("AGENT_VERDICT_RED_THRESHOLD")
    if v_rd is not None:
        try:
            rd = float(v_rd.strip())
            if 0.1 <= rd <= 0.99:
                cfg["thresholds"]["verdict_red_threshold"] = rd
                cfg["thresholds"]["preset"] = _detect_preset(cfg["thresholds"]["will_flood_threshold"], rd)
        except ValueError:
            pass

    v_to = os.getenv("AGENT_STATION_TIMEOUT_S")
    if v_to is not None:
        try:
            to = float(v_to.strip())
            if to > 0:
                cfg["safety"]["station_timeout_seconds"] = to
        except ValueError:
            pass

    v_dl = os.getenv("AGENT_STATION_DELAY_S")
    if v_dl is not None:
        try:
            dl = float(v_dl.strip())
            if dl >= 0:
                cfg["safety"]["station_delay_seconds"] = dl
        except ValueError:
            pass

    v_tfm = os.getenv("ENABLE_TIMESFM_CHALLENGER")
    if v_tfm is not None:
        cfg["challenger"]["enabled"] = v_tfm.strip().lower() in ("1", "true", "yes", "on")

    return cfg


_config: Dict[str, Any] = copy.deepcopy(_DEFAULT_CONFIG_V2)
_state = {
    "last_run_started": None,
    "last_run_finished": None,
    "last_run_stats": None,
    "running": False,
    "next_run": None,
    "thread_started": False,
}
_state_lock = threading.Lock()
_stop_event = threading.Event()
_SNAPSHOT_SCHEMA_READY = False


def _upgrade_config_to_v2(raw: Dict[str, Any]) -> Dict[str, Any]:
    cfg = copy.deepcopy(_DEFAULT_CONFIG_V2)
    if "enabled" in raw:
        cfg["enabled"] = bool(raw["enabled"])

    if "schedule" in raw and isinstance(raw["schedule"], dict):
        if "hour" in raw["schedule"]:
            cfg["schedule"]["hour"] = int(raw["schedule"]["hour"])
        if "minute" in raw["schedule"]:
            cfg["schedule"]["minute"] = int(raw["schedule"]["minute"])
    else:
        if "hour" in raw:
            cfg["schedule"]["hour"] = int(raw["hour"])
        if "minute" in raw:
            cfg["schedule"]["minute"] = int(raw["minute"])

    if "horizon_days" in raw:
        cfg["horizon_days"] = int(raw["horizon_days"])
    elif "horizon" in raw:
        cfg["horizon_days"] = int(raw["horizon"])

    if "thresholds" in raw and isinstance(raw["thresholds"], dict):
        th = raw["thresholds"]
        if "will_flood_threshold" in th:
            cfg["thresholds"]["will_flood_threshold"] = float(th["will_flood_threshold"])
        if "verdict_red_threshold" in th:
            cfg["thresholds"]["verdict_red_threshold"] = float(th["verdict_red_threshold"])
        if "preset" in th:
            cfg["thresholds"]["preset"] = str(th["preset"])
        else:
            cfg["thresholds"]["preset"] = _detect_preset(
                cfg["thresholds"]["will_flood_threshold"],
                cfg["thresholds"]["verdict_red_threshold"],
            )
    else:
        if "will_flood_threshold" in raw:
            cfg["thresholds"]["will_flood_threshold"] = float(raw["will_flood_threshold"])
        if "verdict_red_threshold" in raw:
            cfg["thresholds"]["verdict_red_threshold"] = float(raw["verdict_red_threshold"])
        cfg["thresholds"]["preset"] = _detect_preset(
            cfg["thresholds"]["will_flood_threshold"],
            cfg["thresholds"]["verdict_red_threshold"],
        )

    if "safety" in raw and isinstance(raw["safety"], dict):
        cfg["safety"].update(raw["safety"])
    if "challenger" in raw and isinstance(raw["challenger"], dict):
        cfg["challenger"].update(raw["challenger"])

    return cfg


def _load_persisted_config() -> None:
    global _config
    cfg = copy.deepcopy(_DEFAULT_CONFIG_V2)
    try:
        if _CONFIG_PATH.exists():
            data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg = _upgrade_config_to_v2(data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось загрузить agent_config: %s", exc)

    cfg = _apply_env_overrides(cfg)
    with _state_lock:
        _config = cfg


def _save_persisted_config() -> None:
    try:
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _state_lock:
            payload = copy.deepcopy(_config)
        _CONFIG_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось сохранить agent_config: %s", exc)


def get_thresholds() -> Dict[str, Any]:
    with _state_lock:
        th = _config["thresholds"]
        return {
            "will_flood_threshold": float(th.get("will_flood_threshold", 0.75)),
            "verdict_red_threshold": float(th.get("verdict_red_threshold", 0.75)),
            "preset": str(th.get("preset", "balanced")),
        }


def get_agent_config() -> Dict[str, Any]:
    return get_status()


def self_check() -> Dict[str, Any]:
    """Coverage + health отчёт для UI /api/agent/self-check."""
    gaps: List[Dict[str, Any]] = []
    try:
        conn = hs.get_db()
        try:
            total = conn.execute("SELECT COUNT(*) FROM daily_features").fetchone()[0]
            # coverage за последние 90 дней
            for col, label in [
                ("snow_pct_norm", "Снегозапас"),
                ("ice_thickness_cm", "Толщина льда"),
                ("temp_anomaly", "Аномалия температуры"),
                ("precip_sum_30d", "Осадки 30д"),
                ("level_vs_oya_pct", "Уровень vs ОЯ"),
            ]:
                try:
                    nn = conn.execute(f"SELECT COUNT(*) FROM daily_features WHERE {col} IS NOT NULL").fetchone()[0]
                except sqlite3.OperationalError:
                    nn = 0
                pct = (nn / total * 100) if total else 0
                if pct < 5:
                    gaps.append({"feature": col, "label": label, "non_null": nn, "pct": round(pct, 2), "severity": "high" if col == "snow_pct_norm" else "medium"})
            # stale stations: нет данных за 60 дней
            try:
                stale = conn.execute("SELECT river, post, MAX(date) as last_date FROM daily_features GROUP BY river, post HAVING last_date < date('now','-60 days')").fetchall()
                stale_list = [{"river": r[0], "post": r[1], "last_date": r[2]} for r in stale[:20]]
            except Exception:
                stale_list = []
            # 5 постов без ОЯ
            try:
                missing_oya = conn.execute("SELECT river, post FROM stations WHERE critical_oya IS NULL OR low_oya IS NULL").fetchall()
                missing_oya_n = len(missing_oya)
            except Exception:
                missing_oya_n = 0
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "gaps": gaps}
    # model age
    model_age_days: Optional[int] = None
    try:
        # ищем любой manifest
        import glob
        manifests = list((hs.MODELS_DIR).rglob("manifest.json"))
        if manifests:
            mtimes = [p.stat().st_mtime for p in manifests]
            newest = max(mtimes)
            model_age_days = int((time.time() - newest) / 86400)
    except Exception:
        pass
    with _state_lock:
        last_stats = _state.get("last_run_stats")
        challenger_info = dict(_config.get("challenger", {}))
        safety_info = dict(_config.get("safety", {}))
    return {
        "ok": True,
        "total_rows": total,
        "gaps": gaps,
        "stale_stations": stale_list,
        "stale_count": len(stale_list),
        "missing_oya_count": missing_oya_n,
        "model_age_days": model_age_days,
        "last_run_stats": last_stats,
        "snapshot_stats": snapshot_stats(),
        "safety": safety_info,
        "challenger": challenger_info,
    }


def _ensure_snapshot_schema(conn: sqlite3.Connection) -> None:
    global _SNAPSHOT_SCHEMA_READY
    if _SNAPSHOT_SCHEMA_READY:
        return
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS agent_snapshots (
            river         TEXT NOT NULL,
            post          TEXT NOT NULL,
            computed_at   TEXT NOT NULL,
            horizon_days  INTEGER NOT NULL,
            risk_class    TEXT,
            will_flood    INTEGER,
            confidence    REAL,
            payload_json  TEXT NOT NULL,
            PRIMARY KEY (river, post)
        );
        CREATE INDEX IF NOT EXISTS ix_snapshots_class ON agent_snapshots(risk_class);
        """
    )
    conn.commit()
    _SNAPSHOT_SCHEMA_READY = True


def _snapshot_db() -> sqlite3.Connection:
    conn = hs.get_db()
    _ensure_snapshot_schema(conn)
    return conn


def save_snapshot(river: str, post: str, result: Dict[str, Any], horizon: int) -> None:
    conn = _snapshot_db()
    try:
        verdict = result.get("verdict") or {}
        conn.execute(
            """
            INSERT INTO agent_snapshots (river, post, computed_at, horizon_days,
                                         risk_class, will_flood, confidence, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(river, post) DO UPDATE SET
                computed_at=excluded.computed_at,
                horizon_days=excluded.horizon_days,
                risk_class=excluded.risk_class,
                will_flood=excluded.will_flood,
                confidence=excluded.confidence,
                payload_json=excluded.payload_json
            """,
            (
                river, post,
                _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
                horizon,
                result.get("risk_class"),
                1 if verdict.get("will_flood") else 0,
                float(verdict.get("confidence") or 0.0),
                json.dumps(result, ensure_ascii=False),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_snapshot(river: str, post: str) -> Optional[Dict[str, Any]]:
    conn = _snapshot_db()
    try:
        row = conn.execute(
            "SELECT computed_at, payload_json FROM agent_snapshots WHERE river=? AND post=? LIMIT 1",
            (river, post),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        payload = json.loads(row["payload_json"])
    except Exception:
        return None
    payload["_snapshot_computed_at"] = row["computed_at"]
    try:
        ts = _dt.datetime.fromisoformat(row["computed_at"].replace("Z", ""))
        payload["_snapshot_age_s"] = int((_dt.datetime.utcnow() - ts).total_seconds())
    except Exception:
        payload["_snapshot_age_s"] = None
    return payload


def snapshot_stats() -> Dict[str, Any]:
    conn = _snapshot_db()
    try:
        rows = conn.execute(
            "SELECT risk_class, will_flood, COUNT(*) AS n FROM agent_snapshots "
            "GROUP BY risk_class, will_flood"
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) AS n FROM agent_snapshots").fetchone()["n"]
        latest = conn.execute("SELECT MAX(computed_at) AS t FROM agent_snapshots").fetchone()["t"]
    finally:
        conn.close()
    by_class: Dict[str, int] = {}
    will_flood_total = 0
    for r in rows:
        c = r["risk_class"] or "unknown"
        by_class[c] = by_class.get(c, 0) + r["n"]
        if r["will_flood"]:
            will_flood_total += r["n"]
    return {"total": total, "by_class": by_class, "will_flood": will_flood_total, "latest": latest}


def _list_stations() -> List[Tuple[str, str]]:
    conn = hs.get_db()
    try:
        cur = conn.execute("SELECT DISTINCT river, post FROM stations ORDER BY river, post")
        return [(r["river"], r["post"]) for r in cur.fetchall()]
    finally:
        conn.close()


def _run_all(horizon: int) -> Dict[str, Any]:
    from flood_agent import assess_flood_risk

    started = _dt.datetime.utcnow()
    ok = 0
    err: List[Dict[str, str]] = []
    with _state_lock:
        _state["running"] = True
        _state["last_run_started"] = started.isoformat(timespec="seconds") + "Z"
        safety_cfg = dict(_config.get("safety", {}))
    
    timeout_s = float(safety_cfg.get("station_timeout_seconds", 15.0))
    delay_s = float(safety_cfg.get("station_delay_seconds", 0.05))
    gc_freq = int(safety_cfg.get("gc_frequency", 5))

    try:
        stations = _list_stations()
        logger.info("Планировщик агента: старт прогона по %d станциям (timeout=%.1fs, delay=%.2fs)",
                    len(stations), timeout_s, delay_s)
        for i, (river, post) in enumerate(stations, 1):
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(assess_flood_risk, river, post, horizon=horizon, persist=True)
                    res = future.result(timeout=timeout_s)
                save_snapshot(river, post, res, horizon)
                ok += 1
            except concurrent.futures.TimeoutError:
                err_msg = f"Station assessment timed out after {timeout_s}s"
                logger.warning("Планировщик: таймаут на станции %s / %s", river, post)
                err.append({"river": river, "post": post, "error": err_msg})
            except Exception as exc:  # noqa: BLE001
                err.append({"river": river, "post": post, "error": str(exc)[:200]})

            if delay_s > 0:
                time.sleep(delay_s)
            if i % gc_freq == 0:
                gc.collect()
        gc.collect()
    finally:
        finished = _dt.datetime.utcnow()
        stats = {"ok": ok, "err": len(err),
                 "took_s": int((finished - started).total_seconds()),
                 "errors": err[:20]}
        with _state_lock:
            _state["running"] = False
            _state["last_run_finished"] = finished.isoformat(timespec="seconds") + "Z"
            _state["last_run_stats"] = stats
        logger.info("Планировщик агента: готово ok=%d err=%d за %ds",
                    ok, len(err), stats["took_s"])
    return stats


def _seconds_until_next_run() -> float:
    now_utc = _dt.datetime.now(_dt.timezone.utc)
    now_local = now_utc.astimezone(_YAKUTSK_TZ)
    with _state_lock:
        h = int(_config["schedule"].get("hour", 3))
        m = int(_config["schedule"].get("minute", 0))
    target = now_local.replace(hour=h, minute=m, second=0, microsecond=0)
    if target <= now_local:
        target = target + _dt.timedelta(days=1)
    with _state_lock:
        _state["next_run"] = target.astimezone(_dt.timezone.utc).isoformat(timespec="seconds")
    return (target - now_local).total_seconds()


def _loop() -> None:
    with _state_lock:
        h = _config["schedule"]["hour"]
        m = _config["schedule"]["minute"]
    logger.info("Планировщик агента запущен (target %02d:%02d Asia/Yakutsk)", h, m)
    while not _stop_event.is_set():
        wait_s = _seconds_until_next_run()
        while wait_s > 0 and not _stop_event.is_set():
            step = min(60.0, wait_s)
            time.sleep(step)
            wait_s -= step
        if _stop_event.is_set():
            break
        with _state_lock:
            en = bool(_config.get("enabled", True))
            hz = int(_config.get("horizon_days", 14))
        if not en:
            time.sleep(60)
            continue
        try:
            _run_all(hz)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ошибка планировщика: %s", exc)


def init_scheduler() -> None:
    _load_persisted_config()
    with _state_lock:
        if _state["thread_started"]:
            return
        _state["thread_started"] = True
    t = threading.Thread(target=_loop, name="agent-scheduler", daemon=True)
    t.start()
    _seconds_until_next_run()


def run_now(background: bool = True, horizon: Optional[int] = None) -> Dict[str, Any]:
    with _state_lock:
        h = int(horizon or _config.get("horizon_days", 14))
        if _state["running"]:
            return {"queued": False, "reason": "already running"}
    if not background:
        return _run_all(h)
    threading.Thread(target=lambda: _run_all(h), daemon=True).start()
    return {"queued": True, "horizon": h}


def get_status() -> Dict[str, Any]:
    with _state_lock:
        res = copy.deepcopy(_config)
        res["last_run_started"] = _state["last_run_started"]
        res["last_run_finished"] = _state["last_run_finished"]
        res["last_run_stats"] = _state["last_run_stats"]
        res["running"] = _state["running"]
        res["next_run"] = _state["next_run"]
        # Совместимость со старыми плоскими полями
        res["hour"] = _config["schedule"]["hour"]
        res["minute"] = _config["schedule"]["minute"]
        res["timezone"] = _config["schedule"]["timezone"]
        res["horizon"] = _config["horizon_days"]
        res["will_flood_threshold"] = _config["thresholds"]["will_flood_threshold"]
        res["verdict_red_threshold"] = _config["thresholds"]["verdict_red_threshold"]
        res["preset"] = _config["thresholds"].get("preset", "balanced")
    res["snapshot_stats"] = snapshot_stats()
    return res


def update_config(
    hour: Optional[int] = None,
    minute: Optional[int] = None,
    enabled: Optional[bool] = None,
    horizon: Optional[int] = None,
    will_flood_threshold: Optional[float] = None,
    verdict_red_threshold: Optional[float] = None,
    preset: Optional[str] = None,
    station_timeout_seconds: Optional[float] = None,
    station_delay_seconds: Optional[float] = None,
    challenger_enabled: Optional[bool] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    with _state_lock:
        if hour is not None:
            if not (0 <= hour <= 23):
                raise ValueError("hour должен быть 0..23")
            _config["schedule"]["hour"] = int(hour)
        if minute is not None:
            if not (0 <= minute <= 59):
                raise ValueError("minute должен быть 0..59")
            _config["schedule"]["minute"] = int(minute)
        if enabled is not None:
            _config["enabled"] = bool(enabled)
        if horizon is not None:
            if not (1 <= horizon <= 60):
                raise ValueError("horizon должен быть 1..60")
            _config["horizon_days"] = int(horizon)

        # Пресеты и пороги
        if preset is not None:
            p_clean = str(preset).lower().strip()
            if p_clean in PRESETS:
                _config["thresholds"]["preset"] = p_clean
                _config["thresholds"]["will_flood_threshold"] = PRESETS[p_clean]["will_flood_threshold"]
                _config["thresholds"]["verdict_red_threshold"] = PRESETS[p_clean]["verdict_red_threshold"]
            elif p_clean == "custom":
                _config["thresholds"]["preset"] = "custom"
            else:
                raise ValueError(f"Неизвестный пресет '{preset}'. Доступны: {list(PRESETS.keys())} или 'custom'")

        if will_flood_threshold is not None:
            v = float(will_flood_threshold)
            if not (0.1 <= v <= 0.99):
                raise ValueError("will_flood_threshold должен быть 0.1..0.99")
            _config["thresholds"]["will_flood_threshold"] = v
            if preset is None:
                _config["thresholds"]["preset"] = _detect_preset(v, _config["thresholds"]["verdict_red_threshold"])

        if verdict_red_threshold is not None:
            v = float(verdict_red_threshold)
            if not (0.1 <= v <= 0.99):
                raise ValueError("verdict_red_threshold должен быть 0.1..0.99")
            _config["thresholds"]["verdict_red_threshold"] = v
            if preset is None:
                _config["thresholds"]["preset"] = _detect_preset(_config["thresholds"]["will_flood_threshold"], v)

        if station_timeout_seconds is not None:
            to = float(station_timeout_seconds)
            if to <= 0:
                raise ValueError("station_timeout_seconds должен быть > 0")
            _config["safety"]["station_timeout_seconds"] = to

        if station_delay_seconds is not None:
            dl = float(station_delay_seconds)
            if dl < 0:
                raise ValueError("station_delay_seconds должен быть >= 0")
            _config["safety"]["station_delay_seconds"] = dl

        if challenger_enabled is not None:
            _config["challenger"]["enabled"] = bool(challenger_enabled)

        # Поддержка вложенных словарей из kwargs
        if "schedule" in kwargs and isinstance(kwargs["schedule"], dict):
            if "hour" in kwargs["schedule"]:
                _config["schedule"]["hour"] = int(kwargs["schedule"]["hour"])
            if "minute" in kwargs["schedule"]:
                _config["schedule"]["minute"] = int(kwargs["schedule"]["minute"])
        if "thresholds" in kwargs and isinstance(kwargs["thresholds"], dict):
            t_kw = kwargs["thresholds"]
            if "will_flood_threshold" in t_kw:
                _config["thresholds"]["will_flood_threshold"] = float(t_kw["will_flood_threshold"])
            if "verdict_red_threshold" in t_kw:
                _config["thresholds"]["verdict_red_threshold"] = float(t_kw["verdict_red_threshold"])
            if "preset" in t_kw:
                _config["thresholds"]["preset"] = str(t_kw["preset"])

    _save_persisted_config()
    _seconds_until_next_run()
    return get_status()


def main() -> int:
    """CLI: однократный прогон агента по всем станциям (--once) или постоянный цикл (--loop)."""
    import argparse
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(description="Планировщик агента HydroPredict")
    ap.add_argument("--once", action="store_true",
                    help="Однократный прогон агента по всем станциям + snapshot, затем выход")
    ap.add_argument("--loop", action="store_true",
                    help="Запустить постоянный цикл (как в api_server)")
    ap.add_argument("--horizon", type=int, default=_DEFAULT_HORIZON,
                    help=f"Горизонт прогноза в днях (по умолчанию {_DEFAULT_HORIZON})")
    args = ap.parse_args()

    if args.loop:
        init_scheduler()
        _loop()  # бесконечный
        return 0
    # по умолчанию и с --once — однократный прогон
    stats = _run_all(int(args.horizon))
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0 if not stats.get("errors") else 1


if __name__ == "__main__":
    raise SystemExit(main())

