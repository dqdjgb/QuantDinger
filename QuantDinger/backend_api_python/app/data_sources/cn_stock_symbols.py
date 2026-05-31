"""
CN A-share symbol universe lookup.

AkShare exposes a lightweight code/name table for the full A-share universe.
The watchlist search endpoint uses this as a dynamic fallback after the local
seed table, so users can add any listed A-share without pre-seeding it in DB.
"""

from __future__ import annotations

import os
import time
from typing import Dict, List, Set

from app.utils.logger import get_logger

logger = get_logger(__name__)

_CACHE: Dict[str, object] = {"ts": 0.0, "rows": []}


def _cache_ttl_sec() -> int:
    try:
        return max(60, int(os.getenv("CN_STOCK_SYMBOLS_CACHE_TTL", "21600")))
    except Exception:
        return 21600


def _exchange_for_code(code: str) -> str:
    s = (code or "").strip()
    if s.startswith("6"):
        return "SSE"
    if s.startswith(("0", "3")):
        return "SZSE"
    if s.startswith(("4", "8", "9")):
        return "BSE"
    return ""


def _pick_col(columns: List[str], candidates: List[str]) -> str:
    lowered = {str(c).strip().lower(): c for c in columns}
    for cand in candidates:
        key = cand.strip().lower()
        if key in lowered:
            return lowered[key]
    return ""


def _load_akshare_a_share_symbols() -> List[Dict[str, str]]:
    try:
        import akshare as ak  # type: ignore
    except Exception as e:
        logger.debug("akshare not installed; CNStock universe search disabled: %s", e)
        return []

    df = None
    try:
        if hasattr(ak, "stock_info_a_code_name"):
            df = ak.stock_info_a_code_name()
    except Exception as e:
        logger.debug("ak.stock_info_a_code_name failed: %s", e)

    if df is None or getattr(df, "empty", True):
        try:
            if hasattr(ak, "stock_zh_a_spot_em"):
                df = ak.stock_zh_a_spot_em()
        except Exception as e:
            logger.debug("ak.stock_zh_a_spot_em failed: %s", e)

    if df is None or getattr(df, "empty", True):
        return []

    columns = [str(c) for c in list(df.columns)]
    code_col = _pick_col(columns, ["code", "代码", "证券代码"])
    name_col = _pick_col(columns, ["name", "名称", "股票简称", "证券简称"])
    if not code_col or not name_col:
        logger.debug("AkShare A-share columns not recognized: %s", columns)
        return []

    out: List[Dict[str, str]] = []
    for _, row in df.iterrows():
        code = str(row.get(code_col, "")).strip().upper()
        name = str(row.get(name_col, "")).strip()
        if "." in code:
            code = code.split(".", 1)[0]
        code = code.zfill(6) if code.isdigit() and len(code) < 6 else code
        if len(code) != 6 or not code.isdigit() or not name or name.lower() == "nan":
            continue
        out.append({
            "market": "CNStock",
            "symbol": code,
            "name": name,
            "exchange": _exchange_for_code(code),
            "currency": "CNY",
        })
    return out


def get_a_share_symbols(force_refresh: bool = False) -> List[Dict[str, str]]:
    now = time.time()
    rows = _CACHE.get("rows")
    if (
        not force_refresh
        and isinstance(rows, list)
        and rows
        and now - float(_CACHE.get("ts") or 0) < _cache_ttl_sec()
    ):
        return rows

    fresh = _load_akshare_a_share_symbols()
    if fresh:
        _CACHE["rows"] = fresh
        _CACHE["ts"] = now
        logger.info("Loaded %d CNStock symbols from AkShare", len(fresh))
        return fresh

    return rows if isinstance(rows, list) else []


def search_a_share_symbols(keyword: str, limit: int = 20, exclude: Set[str] | None = None) -> List[Dict[str, str]]:
    kw = (keyword or "").strip().upper()
    if not kw:
        return []

    excluded = {s.upper() for s in (exclude or set())}
    results: List[Dict[str, str]] = []
    for row in get_a_share_symbols():
        symbol = (row.get("symbol") or "").upper()
        name = row.get("name") or ""
        if not symbol or symbol in excluded:
            continue
        if kw in symbol or kw in name.upper():
            results.append(row)
            if len(results) >= max(limit, 0):
                break
    return results
