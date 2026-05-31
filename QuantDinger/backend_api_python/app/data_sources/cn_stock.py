"""
中国A股数据源 — 多层 fallback

有 TWELVE_DATA_API_KEY:
  所有周期 → Twelve Data（主） → 腾讯日/周线 → yfinance → AkShare

无 API Key:
  分钟/小时 → yfinance → AkShare
  日/周线 → 腾讯 fqkline → yfinance → AkShare
"""

from __future__ import annotations

from typing import Dict, List, Any, Optional

from app.data_sources.base import BaseDataSource
from app.data_sources.tencent import normalize_cn_code, fetch_quote, parse_quote_to_ticker, fetch_kline, tencent_kline_rows_to_dicts
from app.data_sources.asia_stock_kline import (
    normalize_chart_timeframe,
    fetch_twelvedata_klines,
    fetch_tencent_minute_klines,
    fetch_eastmoney_minute_klines,
    fetch_yahoo_chart_klines,
    fetch_yfinance_klines,
    fetch_akshare_minute_klines,
    fetch_akshare_weekly_klines,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


class CNStockDataSource(BaseDataSource):
    """A股数据源（TwelveData + Tencent + yfinance + AkShare）"""

    name = "CNStock/multi-source"

    def __init__(self):
        self._last_kline_diagnostics: List[Dict[str, Any]] = []

    def _reset_kline_diagnostics(self) -> None:
        self._last_kline_diagnostics = []

    def _record_kline_source(
        self,
        source: str,
        code: str,
        timeframe: str,
        limit: int,
        rows: Optional[List[Dict[str, Any]]] = None,
        error: Optional[BaseException] = None,
    ) -> None:
        count = len(rows or [])
        entry: Dict[str, Any] = {"source": source, "count": count}
        if error is not None:
            entry["error"] = str(error)
        self._last_kline_diagnostics.append(entry)
        if error is not None:
            logger.warning(
                "CNStock K-line source %s failed for %s tf=%s limit=%s: %s",
                source, code, timeframe, limit, error,
            )
        else:
            logger.info(
                "CNStock K-line source %s returned %d bars for %s tf=%s limit=%s",
                source, count, code, timeframe, limit,
            )

    def _fetch_kline_source(
        self,
        source: str,
        code: str,
        diag_timeframe: str,
        diag_limit: int,
        fetcher: Any,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        try:
            rows = fetcher(**kwargs) or []
            self._record_kline_source(source, code, diag_timeframe, diag_limit, rows=rows)
            return rows
        except Exception as e:
            self._record_kline_source(source, code, diag_timeframe, diag_limit, rows=[], error=e)
            return []

    def get_last_kline_diagnostics(self) -> List[Dict[str, Any]]:
        return list(self._last_kline_diagnostics)

    def format_last_kline_diagnostics(self) -> str:
        parts = []
        for item in self._last_kline_diagnostics:
            source = item.get("source", "unknown")
            count = item.get("count", 0)
            err = item.get("error")
            if err:
                parts.append(f"{source}=error:{err}")
            else:
                parts.append(f"{source}={count}")
        return ", ".join(parts)

    @staticmethod
    def _has_enough_kline_rows(rows: List[Dict[str, Any]], limit: int) -> bool:
        if not rows:
            return False
        if int(limit or 0) <= 1:
            return True
        return len(rows) >= 2

    def get_ticker(self, symbol: str) -> Dict[str, Any]:
        code = normalize_cn_code(symbol)
        parts = fetch_quote(code)
        if not parts:
            return {"last": 0, "symbol": code}
        t = parse_quote_to_ticker(parts)
        return {
            "last": t.get("last", 0),
            "change": t.get("change", 0),
            "changePercent": t.get("changePercent", 0),
            "high": t.get("high", 0),
            "low": t.get("low", 0),
            "open": t.get("open", 0),
            "previousClose": t.get("previousClose", 0),
            "name": t.get("name", ""),
            "symbol": code,
        }

    def get_kline(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        before_time: Optional[int] = None,
        after_time: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        code = normalize_cn_code(symbol)
        tf = normalize_chart_timeframe(timeframe)
        lim = max(int(limit or 300), 1)
        self._reset_kline_diagnostics()

        # Tier 1: Twelve Data (paid, most reliable)
        rows = self._fetch_kline_source(
            "TwelveData",
            code,
            tf,
            lim,
            fetch_twelvedata_klines,
            is_hk=False, tencent_code=code, timeframe=tf, limit=lim, before_time=before_time
        )
        if self._has_enough_kline_rows(rows, lim):
            return self.filter_and_limit(
                rows,
                limit=lim,
                before_time=before_time,
                after_time=after_time,
                truncate=(after_time is None),
            )

        # Tier 2: Tencent for daily/weekly (fast, free)
        if tf in ("1D", "1W"):
            tf_map = {"1D": "day", "1W": "week"}
            period = tf_map.get(tf, "day")
            raw_rows = self._fetch_kline_source(
                "Tencent",
                code,
                tf,
                lim,
                fetch_kline,
                code=code, period=period, count=lim, adj="qfq",
            )
            out = tencent_kline_rows_to_dicts(raw_rows)
            self._record_kline_source("TencentParsed", code, tf, lim, rows=out)
            if self._has_enough_kline_rows(out, lim):
                return self.filter_and_limit(
                    out,
                    limit=lim,
                    before_time=before_time,
                    after_time=after_time,
                    truncate=(after_time is None),
                )

        # Tier 3: Tencent minute/hour K-lines for A-shares.
        if tf in ("1m", "3m", "5m", "15m", "30m", "1H", "4H"):
            rows = self._fetch_kline_source(
                "TencentMinute",
                code,
                tf,
                lim,
                fetch_tencent_minute_klines,
                is_hk=False, tencent_code=code, timeframe=tf, limit=lim, before_time=before_time
            )
            if self._has_enough_kline_rows(rows, lim):
                return self.filter_and_limit(
                    rows,
                    limit=lim,
                    before_time=before_time,
                    after_time=after_time,
                    truncate=(after_time is None),
                )

        # Tier 4: Eastmoney direct minute/hour K-lines for A-shares.
        if tf in ("1m", "3m", "5m", "15m", "30m", "1H", "4H"):
            rows = self._fetch_kline_source(
                "Eastmoney",
                code,
                tf,
                lim,
                fetch_eastmoney_minute_klines,
                is_hk=False, tencent_code=code, timeframe=tf, limit=lim, before_time=before_time
            )
            if self._has_enough_kline_rows(rows, lim):
                return self.filter_and_limit(
                    rows,
                    limit=lim,
                    before_time=before_time,
                    after_time=after_time,
                    truncate=(after_time is None),
                )

        # Tier 5: Yahoo chart HTTP fallback (no yfinance package required)
        rows = self._fetch_kline_source(
            "YahooChart",
            code,
            tf,
            lim,
            fetch_yahoo_chart_klines,
            is_hk=False, tencent_code=code, timeframe=tf, limit=lim, before_time=before_time
        )
        if self._has_enough_kline_rows(rows, lim):
            return self.filter_and_limit(
                rows,
                limit=lim,
                before_time=before_time,
                after_time=after_time,
                truncate=(after_time is None),
            )

        # Tier 6: yfinance package fallback (works when Yahoo not rate-limited)
        rows = self._fetch_kline_source(
            "yfinance",
            code,
            tf,
            lim,
            fetch_yfinance_klines,
            is_hk=False, tencent_code=code, timeframe=tf, limit=lim, before_time=before_time
        )
        if self._has_enough_kline_rows(rows, lim):
            return self.filter_and_limit(
                rows,
                limit=lim,
                before_time=before_time,
                after_time=after_time,
                truncate=(after_time is None),
            )

        # Tier 7: AkShare (fragile overseas, last resort)
        if tf in ("1m", "3m", "5m", "15m", "30m", "1H", "4H"):
            rows = self._fetch_kline_source(
                "AkShare",
                code,
                tf,
                lim,
                fetch_akshare_minute_klines,
                is_hk=False, tencent_code=code, timeframe=tf, limit=lim, before_time=before_time
            )
        elif tf == "1W":
            rows = self._fetch_kline_source(
                "AkShareWeekly",
                code,
                tf,
                lim,
                fetch_akshare_weekly_klines,
                is_hk=False, tencent_code=code, limit=lim, before_time=before_time
            )
        else:
            rows = []
            self._record_kline_source("AkShareSkipped", code, tf, lim, rows=rows)

        return self.filter_and_limit(
            rows,
            limit=lim,
            before_time=before_time,
            after_time=after_time,
            truncate=(after_time is None),
        )
