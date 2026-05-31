import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.data_sources import asia_stock_kline
from app.data_sources import cn_stock
from app.data_sources.cn_stock import CNStockDataSource


def test_eastmoney_kline_rows_parse_to_standard_bars():
    rows = [
        "2026-05-29 09:45,15.10,15.25,15.30,15.05,12345,18800000,1.1,0.3,0.05,0.2",
        "2026-05-29 09:30,15.00,15.10,15.20,14.95,10000,15000000,1.0,0.2,0.03,0.1",
    ]

    bars = asia_stock_kline._bars_from_eastmoney_klines(rows)

    assert [b["close"] for b in bars] == [15.10, 15.25]
    assert bars[0]["open"] == 15.00
    assert bars[0]["high"] == 15.20
    assert bars[0]["low"] == 14.95
    assert bars[0]["volume"] == 10000
    assert bars[0]["time"] < bars[1]["time"]


def test_cnstock_15m_uses_eastmoney_before_yfinance_or_akshare(monkeypatch):
    calls = []
    expected = [{"time": 100, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10}]

    monkeypatch.setattr(cn_stock, "fetch_twelvedata_klines", lambda **kwargs: [])

    def eastmoney(**kwargs):
        calls.append(("eastmoney", kwargs["timeframe"], kwargs["tencent_code"]))
        return expected

    def forbidden_yfinance(**kwargs):
        raise AssertionError("yfinance should not be called when Eastmoney has data")

    def forbidden_akshare(**kwargs):
        raise AssertionError("AkShare should not be called when Eastmoney has data")

    monkeypatch.setattr(cn_stock, "fetch_eastmoney_minute_klines", eastmoney)
    monkeypatch.setattr(cn_stock, "fetch_yfinance_klines", forbidden_yfinance)
    monkeypatch.setattr(cn_stock, "fetch_akshare_minute_klines", forbidden_akshare)

    rows = CNStockDataSource().get_kline("603618", "15m", 20)

    assert rows == expected
    assert calls == [("eastmoney", "15m", "SH603618")]


def test_cnstock_minute_falls_back_to_yfinance_then_akshare(monkeypatch):
    calls = []
    fallback = [{"time": 200, "open": 3, "high": 4, "low": 2, "close": 3.5, "volume": 20}]

    monkeypatch.setattr(cn_stock, "fetch_twelvedata_klines", lambda **kwargs: [])
    monkeypatch.setattr(cn_stock, "fetch_eastmoney_minute_klines", lambda **kwargs: calls.append("eastmoney") or [])
    monkeypatch.setattr(cn_stock, "fetch_yfinance_klines", lambda **kwargs: calls.append("yfinance") or [])
    monkeypatch.setattr(cn_stock, "fetch_akshare_minute_klines", lambda **kwargs: calls.append("akshare") or fallback)

    rows = CNStockDataSource().get_kline("603618", "15m", 20)

    assert rows == fallback
    assert calls == ["eastmoney", "yfinance", "akshare"]


def test_eastmoney_three_minute_bars_merge_one_minute_response(monkeypatch):
    class DummyLimiter:
        def wait(self):
            return 0

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "klines": [
                        "2026-05-29 09:30,10,10.5,11,9,100,0,0,0,0,0",
                        "2026-05-29 09:31,10.5,11,12,10,150,0,0,0,0,0",
                        "2026-05-29 09:32,11,11.2,11.5,10.8,200,0,0,0,0,0",
                        "2026-05-29 09:33,11.2,11.1,11.4,11,50,0,0,0,0,0",
                    ]
                }
            }

    monkeypatch.setattr(asia_stock_kline, "get_eastmoney_limiter", lambda: DummyLimiter())
    monkeypatch.setattr(asia_stock_kline.requests, "get", lambda *args, **kwargs: DummyResponse())

    rows = asia_stock_kline.fetch_eastmoney_minute_klines(
        is_hk=False,
        tencent_code="SH600010",
        timeframe="3m",
        limit=10,
        before_time=None,
    )

    assert len(rows) == 1
    assert rows[0]["open"] == 10
    assert rows[0]["high"] == 12
    assert rows[0]["low"] == 9
    assert rows[0]["close"] == 11.2
    assert rows[0]["volume"] == 450
