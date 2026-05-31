"""CNStock enrichment data via direct HTTP sources.

This module intentionally keeps the surface small and defensive. It borrows the
direct-HTTP style used by a-stock-data: prefer Eastmoney / cninfo endpoints for
A-share-only enrichment, keep AkShare out of the hot path, and normalize every
response into optional fields that callers can safely merge into analysis.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from app.data_sources.rate_limiter import get_eastmoney_limiter, get_request_headers
from app.data_sources.tencent import normalize_cn_code
from app.utils.logger import get_logger

logger = get_logger(__name__)

_EM_QUOTE_URL = "https://push2.eastmoney.com/api/qt/stock/get"
_EM_FLOW_URL = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
_EM_NEWS_URL = "https://search-api-web.eastmoney.com/search/jsonp"
_CNINFO_ANN_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"

_CACHE: Dict[str, Dict[str, Any]] = {}
_DEFAULT_CACHE_TTL = 1800


def _cache_get(key: str, ttl: int = _DEFAULT_CACHE_TTL) -> Optional[Any]:
    row = _CACHE.get(key)
    if not row:
        return None
    if time.time() - float(row.get("ts") or 0) > ttl:
        return None
    return row.get("value")


def _cache_set(key: str, value: Any) -> Any:
    _CACHE[key] = {"ts": time.time(), "value": value}
    return value


def normalize_a_code(symbol: str) -> str:
    """Return a six-digit A-share code."""
    code = normalize_cn_code(symbol or "").upper()
    code = code.replace("SH", "").replace("SZ", "")
    if "." in code:
        code = code.split(".", 1)[0]
    return code.zfill(6) if code.isdigit() and len(code) < 6 else code


def eastmoney_secid(symbol: str) -> str:
    """Convert an A-share code to Eastmoney secid: 1.SSE / 0.SZSE-BSE."""
    code = normalize_a_code(symbol)
    market_id = "1" if code.startswith("6") else "0"
    return f"{market_id}.{code}"


def _to_float(value: Any) -> Optional[float]:
    try:
        if value in (None, "", "-", "--"):
            return None
        v = float(value)
        if v == -1 or v != v:
            return None
        return v
    except Exception:
        return None


def _em_scaled(value: Any, scale: float) -> Optional[float]:
    v = _to_float(value)
    return round(v / scale, 4) if v is not None else None


def _request_json(url: str, *, params: Optional[Dict[str, Any]] = None, timeout: int = 10) -> Dict[str, Any]:
    get_eastmoney_limiter().wait()
    headers = get_request_headers(referer="https://quote.eastmoney.com/")
    resp = requests.get(url, params=params or {}, headers=headers, timeout=timeout)
    text = (resp.text or "").strip()
    if not text:
        return {}
    if text.startswith(("jQuery", "jsonp")):
        start = text.find("(")
        end = text.rfind(")")
        if start >= 0 and end > start:
            text = text[start + 1:end]
    try:
        return resp.json()
    except Exception:
        try:
            return json.loads(text)
        except Exception:
            logger.debug("Non-JSON response from %s: %s", url, text[:160])
            return {}


def fetch_stock_profile(symbol: str) -> Dict[str, Any]:
    """Fetch A-share company profile / valuation basics from Eastmoney quote."""
    code = normalize_a_code(symbol)
    cache_key = f"profile:{code}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    params = {
        "secid": eastmoney_secid(code),
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "fields": ",".join([
            "f57", "f58", "f84", "f85", "f116", "f117", "f127", "f162",
            "f167", "f168", "f173", "f189", "f190", "f191",
        ]),
    }
    data = (_request_json(_EM_QUOTE_URL, params=params).get("data") or {})
    if not data:
        return _cache_set(cache_key, {})

    row = {
        "symbol": code,
        "name": data.get("f58") or code,
        "exchange": "SSE" if code.startswith("6") else ("BSE" if code.startswith(("4", "8", "9")) else "SZSE"),
        "currency": "CNY",
        "total_shares": _em_scaled(data.get("f84"), 1),
        "float_shares": _em_scaled(data.get("f85"), 1),
        "market_cap": _em_scaled(data.get("f116"), 100000000),
        "float_market_cap": _em_scaled(data.get("f117"), 100000000),
        "pe_ratio": _em_scaled(data.get("f162"), 100),
        "pb_ratio": _em_scaled(data.get("f167"), 100),
        "turnover_rate": _em_scaled(data.get("f168"), 100),
        "dividend_yield": _em_scaled(data.get("f173"), 100),
        "52w_high": _em_scaled(data.get("f189"), 100),
        "52w_low": _em_scaled(data.get("f190"), 100),
        "source": "eastmoney_quote",
    }
    industry = data.get("f127")
    if industry not in (None, "-", "--", ""):
        row["industry"] = industry
    return _cache_set(cache_key, {k: v for k, v in row.items() if v is not None})


def fetch_money_flow(symbol: str, limit: int = 20) -> Dict[str, Any]:
    """Fetch recent Eastmoney main-force money flow and summarize it."""
    code = normalize_a_code(symbol)
    cache_key = f"flow:{code}:{int(limit)}"
    cached = _cache_get(cache_key, ttl=900)
    if cached is not None:
        return cached

    params = {
        "secid": eastmoney_secid(code),
        "klt": 101,
        "lmt": max(1, min(int(limit or 20), 120)),
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63",
    }
    rows = (((_request_json(_EM_FLOW_URL, params=params).get("data") or {}).get("klines")) or [])
    parsed: List[Dict[str, Any]] = []
    for raw in rows:
        parts = str(raw or "").split(",")
        if len(parts) < 3:
            continue
        parsed.append({
            "date": parts[0],
            "main_net_inflow": _to_float(parts[1]),
            "main_net_ratio": _to_float(parts[2]),
        })

    latest = parsed[-1] if parsed else {}
    recent = parsed[-5:] if parsed else []
    net_values = [float(x["main_net_inflow"]) for x in recent if x.get("main_net_inflow") is not None]
    ratio_values = [float(x["main_net_ratio"]) for x in recent if x.get("main_net_ratio") is not None]
    result = {
        "source": "eastmoney_fflow",
        "latest": latest,
        "recent": parsed[-10:],
        "main_net_inflow_5d": round(sum(net_values), 2) if net_values else None,
        "main_net_ratio_5d_avg": round(sum(ratio_values) / len(ratio_values), 2) if ratio_values else None,
    }
    return _cache_set(cache_key, {k: v for k, v in result.items() if v is not None})


def fetch_stock_news(symbol: str, name: str = "", limit: int = 5) -> List[Dict[str, Any]]:
    """Fetch lightweight Eastmoney search news for a stock."""
    code = normalize_a_code(symbol)
    keyword = name or code
    cache_key = f"news:{code}:{keyword}:{int(limit)}"
    cached = _cache_get(cache_key, ttl=1800)
    if cached is not None:
        return cached

    params = {
        "keyword": keyword,
        "type": "121",
        "pageindex": 1,
        "pagesize": max(1, min(int(limit or 5), 20)),
        "name": "jsonp",
    }
    data = _request_json(_EM_NEWS_URL, params=params)
    items = ((data.get("result") or {}).get("cmsArticleWebOld") or [])
    out: List[Dict[str, Any]] = []
    for item in items[:limit]:
        title = item.get("title") or item.get("Title") or ""
        if not title:
            continue
        out.append({
            "datetime": item.get("date") or item.get("showTime") or datetime.now().strftime("%Y-%m-%d"),
            "headline": re.sub(r"<[^>]+>", "", title),
            "summary": re.sub(r"<[^>]+>", "", item.get("content") or item.get("summary") or "")[:240],
            "source": item.get("source") or "Eastmoney",
            "url": item.get("url") or item.get("Url") or "",
            "sentiment": "neutral",
        })
    return _cache_set(cache_key, out)


def fetch_announcements(symbol: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Fetch recent cninfo announcements for an A-share symbol."""
    code = normalize_a_code(symbol)
    cache_key = f"ann:{code}:{int(limit)}"
    cached = _cache_get(cache_key, ttl=1800)
    if cached is not None:
        return cached

    get_eastmoney_limiter().wait()
    headers = get_request_headers(referer="https://www.cninfo.com.cn/")
    data = {
        "stock": code,
        "searchkey": "",
        "plate": "szse" if not code.startswith("6") else "sse",
        "category": "",
        "trade": "",
        "column": "szse" if not code.startswith("6") else "sse",
        "pageNum": 1,
        "pageSize": max(1, min(int(limit or 5), 30)),
        "tabName": "fulltext",
        "sortName": "",
        "sortType": "",
        "limit": "",
        "seDate": "",
    }
    try:
        resp = requests.post(_CNINFO_ANN_URL, data=data, headers=headers, timeout=10)
        payload = resp.json() if resp.text else {}
    except Exception as exc:
        logger.debug("cninfo announcement fetch failed for %s: %s", code, exc)
        return _cache_set(cache_key, [])

    out: List[Dict[str, Any]] = []
    for item in (payload.get("announcements") or [])[:limit]:
        title = re.sub(r"<[^>]+>", "", item.get("announcementTitle") or "")
        if not title:
            continue
        ts = item.get("announcementTime")
        dt = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d") if isinstance(ts, (int, float)) else ""
        adjunct = item.get("adjunctUrl") or ""
        out.append({
            "datetime": dt,
            "headline": title,
            "summary": item.get("announcementTypeName") or "",
            "source": "CNInfo",
            "url": f"https://static.cninfo.com.cn/{adjunct}" if adjunct else "",
            "sentiment": "neutral",
        })
    return _cache_set(cache_key, out)


def fetch_enrichment_bundle(symbol: str, name: str = "", *, include_news: bool = False) -> Dict[str, Any]:
    """Fetch profile, money flow and optional text catalysts for one A-share."""
    bundle = {
        "profile": fetch_stock_profile(symbol),
        "money_flow": fetch_money_flow(symbol),
        "source": "eastmoney+cninfo",
    }
    if include_news:
        bundle["news"] = fetch_stock_news(symbol, name=name, limit=5)
        bundle["announcements"] = fetch_announcements(symbol, limit=5)
    return bundle


def score_enrichment(enrichment: Dict[str, Any]) -> Dict[str, Any]:
    """Turn enrichment data into a small additive screener score."""
    flow = enrichment.get("money_flow") or {}
    profile = enrichment.get("profile") or {}
    score = 0.0
    reasons: List[str] = []

    ratio = _to_float(flow.get("main_net_ratio_5d_avg"))
    if ratio is not None:
        if ratio >= 5:
            score += 8
            reasons.append("5日主力净流入占比较高")
        elif ratio >= 1:
            score += 4
            reasons.append("5日主力资金温和流入")
        elif ratio <= -5:
            score -= 8
            reasons.append("5日主力资金明显流出")
        elif ratio <= -1:
            score -= 4
            reasons.append("5日主力资金偏流出")

    turnover = _to_float(profile.get("turnover_rate"))
    if turnover is not None:
        if 1 <= turnover <= 8:
            score += 2
        elif turnover > 18:
            score -= 3
            reasons.append("换手率过高，短线波动风险偏大")

    pe = _to_float(profile.get("pe_ratio"))
    if pe is not None:
        if 0 < pe <= 35:
            score += 2
        elif pe > 90:
            score -= 3
            reasons.append("估值偏高")

    return {
        "score": round(max(-12.0, min(12.0, score)), 2),
        "reasons": reasons,
    }
