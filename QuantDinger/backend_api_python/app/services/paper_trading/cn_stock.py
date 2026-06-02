"""China A-share paper-trading rules."""

from __future__ import annotations

import logging
import math
import os
import time as time_module
from dataclasses import dataclass
from datetime import date, datetime, time, timezone, timedelta
from typing import Any, Dict, Set

logger = logging.getLogger(__name__)


LOT_SIZE = 100
DEFAULT_COMMISSION_RATE = 0.0003
DEFAULT_STAMP_TAX_RATE = 0.0005
SHANGHAI_TZ = timezone(timedelta(hours=8))
TRADING_SESSIONS = (
    (time(9, 30), time(11, 30)),
    (time(13, 0), time(15, 0)),
)
DEFAULT_TRADING_WINDOW_BUFFER_MINUTES = 10
_TRADE_DATES_CACHE: Dict[str, Any] = {"ts": 0.0, "dates": set()}


@dataclass(frozen=True)
class PaperFill:
    amount: float
    price: float
    commission: float
    rejection: str = ""

    @property
    def accepted(self) -> bool:
        return not self.rejection


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _rate(config: Dict[str, Any], *keys: str, default: float) -> float:
    for key in keys:
        if key in config and config.get(key) is not None:
            raw = _to_float(config.get(key), default)
            return raw / 100.0 if raw > 0.01 else raw
    return default


def normalize_signal(signal_type: str) -> str:
    return (signal_type or "").strip().lower()


def is_supported_signal(signal_type: str) -> bool:
    return normalize_signal(signal_type) in {
        "open_long",
        "add_long",
        "reduce_long",
        "close_long",
    }


def is_buy_signal(signal_type: str) -> bool:
    return normalize_signal(signal_type) in {"open_long", "add_long"}


def is_sell_signal(signal_type: str) -> bool:
    return normalize_signal(signal_type) in {"reduce_long", "close_long"}


def _to_shanghai_datetime(value: datetime | None = None) -> datetime:
    dt = value or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SHANGHAI_TZ)
    return dt.astimezone(SHANGHAI_TZ)


def _normalize_trade_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raw = str(value or "").strip()
    if not raw:
        return ""
    return raw[:10].replace("/", "-")


def _fetch_trade_dates_from_akshare() -> Set[str]:
    import akshare as ak

    df = ak.tool_trade_date_hist_sina()
    if df is None or "trade_date" not in df:
        return set()
    return {d for d in (_normalize_trade_date(v) for v in df["trade_date"].tolist()) if d}


def _trade_calendar_cache_ttl_sec() -> int:
    try:
        return max(3600, int(os.getenv("CNSTOCK_TRADE_CALENDAR_CACHE_TTL_SEC", "43200")))
    except Exception:
        return 43200


def _get_trade_dates() -> Set[str]:
    now = time_module.time()
    cached = _TRADE_DATES_CACHE.get("dates")
    if cached and now - float(_TRADE_DATES_CACHE.get("ts") or 0.0) < _trade_calendar_cache_ttl_sec():
        return set(cached)

    try:
        fresh = _fetch_trade_dates_from_akshare()
        if fresh:
            _TRADE_DATES_CACHE["dates"] = fresh
            _TRADE_DATES_CACHE["ts"] = now
            return set(fresh)
        raise RuntimeError("empty A-share trade calendar")
    except Exception as exc:
        if cached:
            logger.warning("Using stale CNStock trade calendar after refresh failure: %s", exc)
            return set(cached)
        if str(os.getenv("CNSTOCK_TRADE_CALENDAR_FALLBACK_WEEKDAY", "")).strip().lower() in {"1", "true", "yes", "on"}:
            logger.warning("CNStock trade calendar unavailable; falling back to weekday rule: %s", exc)
            return set()
        logger.error("CNStock trade calendar unavailable; fail-closed for trading-day checks: %s", exc)
        return set()


def is_trading_day(value: datetime | date | None = None) -> bool:
    dt = _to_shanghai_datetime(value if isinstance(value, datetime) else None)
    day = value if isinstance(value, date) and not isinstance(value, datetime) else dt.date()
    key = day.isoformat()
    trade_dates = _get_trade_dates()
    if trade_dates:
        return key in trade_dates
    if str(os.getenv("CNSTOCK_TRADE_CALENDAR_FALLBACK_WEEKDAY", "")).strip().lower() in {"1", "true", "yes", "on"}:
        return day.weekday() < 5
    return False


def is_trading_time(value: datetime | None = None) -> bool:
    """Return True during mainland China A-share continuous trading sessions."""
    dt = _to_shanghai_datetime(value)
    if not is_trading_day(dt):
        return False
    t = dt.time()
    return any(start <= t <= end for start, end in TRADING_SESSIONS)


def is_trading_window(value: datetime | None = None, buffer_minutes: int = DEFAULT_TRADING_WINDOW_BUFFER_MINUTES) -> bool:
    """Return True on A-share trading days around each trading session."""
    dt = _to_shanghai_datetime(value)
    if not is_trading_day(dt):
        return False

    try:
        buffer = max(0, int(buffer_minutes))
    except Exception:
        buffer = DEFAULT_TRADING_WINDOW_BUFFER_MINUTES

    day = dt.date()
    for start, end in TRADING_SESSIONS:
        window_start = datetime.combine(day, start, tzinfo=SHANGHAI_TZ) - timedelta(minutes=buffer)
        window_end = datetime.combine(day, end, tzinfo=SHANGHAI_TZ) + timedelta(minutes=buffer)
        if window_start <= dt <= window_end:
            return True
    return False


def apply_slippage(price: float, signal_type: str, trading_config: Dict[str, Any]) -> float:
    px = max(_to_float(price), 0.0)
    if px <= 0:
        return 0.0
    slippage = _rate(trading_config or {}, "slippage", "paper_slippage", "paperSlippage", default=0.0)
    if slippage <= 0:
        return px
    if is_buy_signal(signal_type):
        return px * (1.0 + slippage)
    if is_sell_signal(signal_type):
        return px * (1.0 - slippage)
    return px


def estimate_commission(value: float, signal_type: str, trading_config: Dict[str, Any]) -> float:
    cfg = trading_config or {}
    commission_rate = _rate(
        cfg,
        "paper_commission",
        "paperCommission",
        "commission",
        default=DEFAULT_COMMISSION_RATE,
    )
    stamp_rate = _rate(
        cfg,
        "paper_stamp_tax",
        "paperStampTax",
        "stamp_tax",
        "stampTax",
        default=DEFAULT_STAMP_TAX_RATE,
    )
    fee = max(_to_float(value), 0.0) * max(commission_rate, 0.0)
    if is_sell_signal(signal_type):
        fee += max(_to_float(value), 0.0) * max(stamp_rate, 0.0)
    return round(fee, 8)


def build_fill(
    *,
    signal_type: str,
    requested_amount: float,
    ref_price: float,
    trading_config: Dict[str, Any],
    sellable_amount: float | None = None,
) -> PaperFill:
    sig = normalize_signal(signal_type)
    if not is_supported_signal(sig):
        return PaperFill(0.0, 0.0, 0.0, f"cnstock_paper_unsupported_signal:{signal_type}")

    fill_price = apply_slippage(ref_price, sig, trading_config or {})
    if fill_price <= 0:
        return PaperFill(0.0, 0.0, 0.0, "cnstock_paper_invalid_price")

    amount = max(_to_float(requested_amount), 0.0)
    if is_buy_signal(sig):
        shares = math.floor(amount)
        board_lots = shares // LOT_SIZE
        if board_lots <= 0:
            return PaperFill(0.0, fill_price, 0.0, "cnstock_paper_min_buy_lot_100")
        amount = float(board_lots * LOT_SIZE)

    if is_sell_signal(sig):
        amount = float(math.floor(amount))
        if amount <= 0:
            return PaperFill(0.0, fill_price, 0.0, "cnstock_paper_invalid_sell_amount")
        if sellable_amount is not None and amount > float(sellable_amount) + 1e-9:
            return PaperFill(0.0, fill_price, 0.0, "cnstock_paper_t_plus_1_sellable_insufficient")

    value = amount * fill_price
    return PaperFill(amount, fill_price, estimate_commission(value, sig, trading_config or {}))
