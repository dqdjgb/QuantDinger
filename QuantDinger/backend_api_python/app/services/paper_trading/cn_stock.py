"""China A-share paper-trading rules."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, time, timezone, timedelta
from typing import Any, Dict


LOT_SIZE = 100
DEFAULT_COMMISSION_RATE = 0.0003
DEFAULT_STAMP_TAX_RATE = 0.0005
SHANGHAI_TZ = timezone(timedelta(hours=8))
TRADING_SESSIONS = (
    (time(9, 30), time(11, 30)),
    (time(13, 0), time(15, 0)),
)


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


def is_trading_time(value: datetime | None = None) -> bool:
    """Return True during mainland China A-share continuous trading sessions."""
    dt = _to_shanghai_datetime(value)
    if dt.weekday() >= 5:
        return False
    t = dt.time()
    return any(start <= t <= end for start, end in TRADING_SESSIONS)


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
