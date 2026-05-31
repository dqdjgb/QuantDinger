"""Structured strategy execution events for analysis and AI review."""

from __future__ import annotations

import json
import threading
from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

_ensure_lock = threading.Lock()
_ensure_done = False


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _json_dumps(value: Any) -> str:
    if value is None:
        value = {}
    if not isinstance(value, (dict, list)):
        value = {"value": value}
    return json.dumps(value, ensure_ascii=False, default=_json_default)


def _get_user_id(strategy_id: int) -> int:
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute("SELECT user_id FROM qd_strategies_trading WHERE id = %s", (int(strategy_id),))
            row = cur.fetchone() or {}
            cur.close()
        return int((row or {}).get("user_id") or 1)
    except Exception:
        return 1


def ensure_strategy_execution_events_table() -> None:
    """Create the structured event table if init.sql has not run yet."""
    global _ensure_done
    if _ensure_done:
        return
    with _ensure_lock:
        if _ensure_done:
            return
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS qd_strategy_execution_events (
                    id BIGSERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES qd_users(id) ON DELETE CASCADE,
                    strategy_id INTEGER NOT NULL REFERENCES qd_strategies_trading(id) ON DELETE CASCADE,
                    event_type VARCHAR(40) NOT NULL,
                    status VARCHAR(30) DEFAULT '',
                    execution_mode VARCHAR(20) DEFAULT '',
                    symbol VARCHAR(50) DEFAULT '',
                    signal_type VARCHAR(40) DEFAULT '',
                    decision_source VARCHAR(40) DEFAULT '',
                    reason TEXT DEFAULT '',
                    confidence DOUBLE PRECISION,
                    price DECIMAL(24, 10),
                    amount DECIMAL(24, 10),
                    position_state VARCHAR(20) DEFAULT '',
                    pending_order_id BIGINT,
                    trade_id BIGINT,
                    exchange_id VARCHAR(40) DEFAULT '',
                    exchange_order_id VARCHAR(120) DEFAULT '',
                    error TEXT DEFAULT '',
                    context_json JSONB DEFAULT '{}'::jsonb,
                    execution_json JSONB DEFAULT '{}'::jsonb,
                    result_json JSONB DEFAULT '{}'::jsonb,
                    created_at TIMESTAMP DEFAULT NOW()
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_strategy_execution_events_strategy_time "
                "ON qd_strategy_execution_events(strategy_id, created_at DESC)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_strategy_execution_events_user_time "
                "ON qd_strategy_execution_events(user_id, created_at DESC)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_strategy_execution_events_order "
                "ON qd_strategy_execution_events(pending_order_id)"
            )
            db.commit()
            cur.close()
        _ensure_done = True


def append_strategy_execution_event(
    *,
    strategy_id: int,
    event_type: str,
    status: str = "",
    execution_mode: str = "",
    symbol: str = "",
    signal_type: str = "",
    decision_source: str = "",
    reason: str = "",
    confidence: Optional[float] = None,
    price: Optional[float] = None,
    amount: Optional[float] = None,
    position_state: str = "",
    pending_order_id: Optional[int] = None,
    trade_id: Optional[int] = None,
    exchange_id: str = "",
    exchange_order_id: str = "",
    error: str = "",
    context: Optional[Dict[str, Any]] = None,
    execution: Optional[Dict[str, Any]] = None,
    result: Optional[Dict[str, Any]] = None,
    user_id: Optional[int] = None,
) -> Optional[int]:
    """Best-effort insert; never raises to trading code."""
    try:
        sid = int(strategy_id or 0)
        if sid <= 0:
            return None
        ensure_strategy_execution_events_table()
        uid = int(user_id or _get_user_id(sid))
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                INSERT INTO qd_strategy_execution_events (
                    user_id, strategy_id, event_type, status, execution_mode, symbol, signal_type,
                    decision_source, reason, confidence, price, amount, position_state,
                    pending_order_id, trade_id, exchange_id, exchange_order_id, error,
                    context_json, execution_json, result_json, created_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, NOW()
                )
                """,
                (
                    uid,
                    sid,
                    str(event_type or "")[:40],
                    str(status or "")[:30],
                    str(execution_mode or "")[:20],
                    str(symbol or "")[:50],
                    str(signal_type or "")[:40],
                    str(decision_source or "")[:40],
                    str(reason or "")[:4000],
                    confidence,
                    price,
                    amount,
                    str(position_state or "")[:20],
                    int(pending_order_id) if pending_order_id else None,
                    int(trade_id) if trade_id else None,
                    str(exchange_id or "")[:40],
                    str(exchange_order_id or "")[:120],
                    str(error or "")[:4000],
                    _json_dumps(context),
                    _json_dumps(execution),
                    _json_dumps(result),
                ),
            )
            event_id = cur.lastrowid
            db.commit()
            cur.close()
        return int(event_id) if event_id is not None else None
    except Exception as exc:
        logger.debug("append_strategy_execution_event skip: %s", exc)
        return None


def _parse_jsonish(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ""):
        return {}
    try:
        return json.loads(value)
    except Exception:
        return value


def list_strategy_execution_events(
    *,
    user_id: int,
    strategy_id: int,
    limit: int = 200,
    event_type: str = "",
    symbol: str = "",
) -> List[Dict[str, Any]]:
    ensure_strategy_execution_events_table()
    params: List[Any] = [int(strategy_id), int(user_id)]
    filters = ["strategy_id = %s", "user_id = %s"]
    if event_type:
        filters.append("event_type = %s")
        params.append(str(event_type)[:40])
    if symbol:
        filters.append("symbol = %s")
        params.append(str(symbol)[:50])
    params.append(max(1, min(int(limit or 200), 1000)))

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            f"""
            SELECT *
            FROM qd_strategy_execution_events
            WHERE {' AND '.join(filters)}
            ORDER BY id DESC
            LIMIT %s
            """,
            tuple(params),
        )
        rows = cur.fetchall() or []
        cur.close()

    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        for key in ("price", "amount", "confidence"):
            if isinstance(item.get(key), Decimal):
                item[key] = float(item[key])
        for key in ("context_json", "execution_json", "result_json"):
            item[key] = _parse_jsonish(item.get(key))
        out.append(item)
    return out
