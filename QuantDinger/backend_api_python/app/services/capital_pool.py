"""User-level strategy capital pool helpers."""

from __future__ import annotations

from typing import Optional

from app.utils.db import get_db_connection


DEFAULT_TOTAL_CAPITAL = 0.0


def to_float(value, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def normalize_allocation_pct(value, default: Optional[float] = None) -> float:
    raw = default if value is None or value == "" else value
    pct = to_float(raw, 0.0)
    if pct > 1.0:
        pct = pct / 100.0
    return pct


def get_strategy_total_capital(user_id: int) -> float:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            "SELECT COALESCE(strategy_total_capital, 0) AS strategy_total_capital FROM qd_users WHERE id = ?",
            (int(user_id),),
        )
        row = cur.fetchone() or {}
        cur.close()
    return max(0.0, to_float(row.get("strategy_total_capital"), DEFAULT_TOTAL_CAPITAL))


def set_strategy_total_capital(user_id: int, total_capital: float) -> float:
    total = max(0.0, to_float(total_capital, DEFAULT_TOTAL_CAPITAL))
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            "UPDATE qd_users SET strategy_total_capital = ?, updated_at = NOW() WHERE id = ?",
            (total, int(user_id)),
        )
        db.commit()
        cur.close()
    return total


def get_allocated_pct(user_id: int, exclude_strategy_id: Optional[int] = None) -> float:
    with get_db_connection() as db:
        cur = db.cursor()
        params = [int(user_id)]
        where = "user_id = ?"
        if exclude_strategy_id is not None:
            where += " AND id <> ?"
            params.append(int(exclude_strategy_id))
        cur.execute(
            f"""
            SELECT COALESCE(SUM(COALESCE(capital_allocation_pct, 0)), 0) AS allocated_pct
            FROM qd_strategies_trading
            WHERE {where}
            """,
            tuple(params),
        )
        row = cur.fetchone() or {}
        cur.close()
    return max(0.0, to_float(row.get("allocated_pct"), 0.0))


def get_capital_pool_summary(user_id: int, exclude_strategy_id: Optional[int] = None) -> dict:
    total = get_strategy_total_capital(user_id)
    allocated = get_allocated_pct(user_id, exclude_strategy_id=exclude_strategy_id)
    remaining = max(0.0, 1.0 - allocated)
    return {
        "strategy_total_capital": total,
        "allocated_pct": allocated,
        "remaining_pct": remaining,
        "allocated_capital": total * allocated,
        "remaining_capital": total * remaining,
    }


def resolve_strategy_allocation(
    user_id: int,
    trading_config: dict,
    *,
    existing_strategy_id: Optional[int] = None,
    existing_allocation_pct: Optional[float] = None,
) -> tuple[float, float, float]:
    """Return (total_capital, allocation_pct, allocated_capital)."""
    total_capital = get_strategy_total_capital(user_id)
    if total_capital <= 0:
        raise ValueError("Please set the strategy total capital before creating strategies")

    pct = normalize_allocation_pct(
        (trading_config or {}).get("capital_allocation_pct"),
        default=existing_allocation_pct,
    )
    if pct <= 0 or pct > 1:
        raise ValueError("capital_allocation_pct must be greater than 0 and no more than 1")

    allocated_without_current = get_allocated_pct(user_id, exclude_strategy_id=existing_strategy_id)
    if allocated_without_current + pct > 1.000001:
        remaining = max(0.0, 1.0 - allocated_without_current)
        raise ValueError(
            f"Capital allocation exceeds total capital pool. Remaining allocation is {remaining * 100:.2f}%"
        )

    allocated_capital = total_capital * pct
    return total_capital, pct, allocated_capital
