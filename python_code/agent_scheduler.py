# -*- coding: utf-8 -*-
"""Планировщик ночного автопрогона агента + snapshot-кеш.

Простое решение без внешних зависимостей: фоновый поток с циклом sleep.
Ежедневно в заданное время (по умолчанию 03:00 Asia/Yakutsk = UTC+09)
последовательно вызывает assess_flood_risk для всех (river, post) из БД
и сохраняет результат в таблицу agent_snapshots.
"""
from __future__ import annotations

import datetime as _dt
import gc
import json
import logging
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import hydro_service as hs

logger = logging.getLogger("hydropredict.scheduler")

_YAKUTSK_TZ = _dt.timezone(_dt.timedelta(hours=9))
_DEFAULT_HOUR = 3
_DEFAULT_MINUTE = 0
_DEFAULT_HORIZON = 14
_DEFAULT_WILL_FLOOD_THR = 0.7
_DEFAULT_VERDICT_RED_THR = 0.7
_CONFIG_PATH = hs.PROJECT_ROOT / "data" / "agent_config.json"

_state = {
    "enabled": True,
    "hour": _DEFAULT_HOUR,
    "minute": _DEFAULT_MINUTE,
    "horizon": _DEFAULT_HORIZON,
    "will_flood_threshold": _DEFAULT_WILL_FLOOD_THR,
    "verdict_red_threshold": _DEFAULT_VERDICT_RED_THR,
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


def _load_persisted_config() -> None:
    try:
        if not _CONFIG_PATH.exists():
            return
        data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        with _state_lock:
            for k in ("hour", "minute", "horizon", "enabled", "will_flood_threshold", "verdict_red_threshold"):
                if k in data:
                    _state[k] = data[k]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось загрузить agent_config: %s", exc)


def _save_persisted_config() -> None:
    try:
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _state_lock:
            payload = {k: _state[k] for k in ("hour", "minute", "horizon", "enabled", "will_flood_threshold", "verdict_red_threshold")}
        _CONFIG_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось сохранить agent_config: %s", exc)


def get_thresholds() -> Dict[str, float]:
    with _state_lock:
        return {
            "will_flood_threshold": float(_state.get("will_flood_threshold", _DEFAULT_WILL_FLOOD_THR)),
            "verdict_red_threshold": float(_state.get("verdict_red_threshold", _DEFAULT_VERDICT_RED_THR)),
        }


def get_agent_config() -> Dict[str, Any]:
    with _state_lock:
        s = dict(_state)
    s["thresholds"] = {
        "will_flood_threshold": float(s.get("will_flood_threshold", _DEFAULT_WILL_FLOOD_THR)),
        "verdict_red_threshold": float(s.get("verdict_red_threshold", _DEFAULT_VERDICT_RED_THR)),
    }
    return s


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
    stations = _list_stations()
    logger.info("Планировщик агента: старт прогона по %d станциям", len(stations))
    for i, (river, post) in enumerate(stations, 1):
        try:
            res = assess_flood_risk(river, post, horizon=horizon, persist=True)
            save_snapshot(river, post, res, horizon)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            err.append({"river": river, "post": post, "error": str(exc)[:200]})
        if i % 5 == 0:
            gc.collect()
    gc.collect()
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
    target = now_local.replace(hour=_state["hour"], minute=_state["minute"],
                               second=0, microsecond=0)
    if target <= now_local:
        target = target + _dt.timedelta(days=1)
    with _state_lock:
        _state["next_run"] = target.astimezone(_dt.timezone.utc).isoformat(timespec="seconds")
    return (target - now_local).total_seconds()


def _loop() -> None:
    logger.info("Планировщик агента запущен (target %02d:%02d Asia/Yakutsk)",
                _state["hour"], _state["minute"])
    while not _stop_event.is_set():
        wait_s = _seconds_until_next_run()
        while wait_s > 0 and not _stop_event.is_set():
            step = min(60.0, wait_s)
            time.sleep(step)
            wait_s -= step
        if _stop_event.is_set():
            break
        if not _state["enabled"]:
            time.sleep(60)
            continue
        try:
            _run_all(_state["horizon"])
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
    h = int(horizon or _state["horizon"])
    if not background:
        return _run_all(h)
    if _state["running"]:
        return {"queued": False, "reason": "already running"}
    threading.Thread(target=lambda: _run_all(h), daemon=True).start()
    return {"queued": True, "horizon": h}


def get_status() -> Dict[str, Any]:
    with _state_lock:
        s = dict(_state)
    s["snapshot_stats"] = snapshot_stats()
    s["timezone"] = "Asia/Yakutsk (UTC+9)"
    return s


def update_config(hour: Optional[int] = None, minute: Optional[int] = None,
                  enabled: Optional[bool] = None, horizon: Optional[int] = None,
                  will_flood_threshold: Optional[float] = None,
                  verdict_red_threshold: Optional[float] = None) -> Dict[str, Any]:
    with _state_lock:
        if hour is not None:
            if not (0 <= hour <= 23):
                raise ValueError("hour должен быть 0..23")
            _state["hour"] = int(hour)
        if minute is not None:
            if not (0 <= minute <= 59):
                raise ValueError("minute должен быть 0..59")
            _state["minute"] = int(minute)
        if enabled is not None:
            _state["enabled"] = bool(enabled)
        if horizon is not None:
            if not (1 <= horizon <= 60):
                raise ValueError("horizon должен быть 1..60")
            _state["horizon"] = int(horizon)
        if will_flood_threshold is not None:
            v = float(will_flood_threshold)
            if not (0.1 <= v <= 0.99):
                raise ValueError("will_flood_threshold должен быть 0.1..0.99")
            _state["will_flood_threshold"] = v
        if verdict_red_threshold is not None:
            v = float(verdict_red_threshold)
            if not (0.1 <= v <= 0.99):
                raise ValueError("verdict_red_threshold должен быть 0.1..0.99")
            _state["verdict_red_threshold"] = v
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

