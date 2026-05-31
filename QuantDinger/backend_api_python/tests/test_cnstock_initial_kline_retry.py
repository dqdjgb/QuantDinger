from app.services import trading_executor as trading_executor_module
from app.services.trading_executor import TradingExecutor


def _executor_without_init():
    return object.__new__(TradingExecutor)


def test_cnstock_initial_kline_retries_until_success(monkeypatch):
    executor = _executor_without_init()
    calls = []
    recovered = [
        {"time": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 10},
        {"time": 2, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 20},
    ]

    monkeypatch.setenv("CNSTOCK_INITIAL_KLINE_RETRIES", "3")
    monkeypatch.setenv("CNSTOCK_INITIAL_KLINE_RETRY_DELAY_SEC", "0")
    monkeypatch.setattr(trading_executor_module, "append_strategy_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        executor,
        "_format_kline_fetch_diagnostics",
        lambda market_category: "TwelveData=0, Eastmoney=0, yfinance=0, AkShare=0",
    )

    def fake_fetch_latest_kline(*args, **kwargs):
        calls.append(kwargs)
        return recovered if len(calls) == 3 else []

    monkeypatch.setattr(executor, "_fetch_latest_kline", fake_fetch_latest_kline)

    rows, attempts, diagnostics = executor._fetch_initial_kline_with_retries(
        42,
        "603618",
        "15m",
        limit=500,
        market_category="CNStock",
    )

    assert rows == recovered
    assert attempts == 3
    assert len(calls) == 3
    assert "Eastmoney=0" in diagnostics


def test_non_cnstock_initial_kline_does_not_retry(monkeypatch):
    executor = _executor_without_init()
    calls = []

    monkeypatch.setenv("CNSTOCK_INITIAL_KLINE_RETRIES", "3")
    monkeypatch.setattr(executor, "_format_kline_fetch_diagnostics", lambda market_category: "")

    def fake_fetch_latest_kline(*args, **kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(executor, "_fetch_latest_kline", fake_fetch_latest_kline)

    rows, attempts, diagnostics = executor._fetch_initial_kline_with_retries(
        42,
        "BTC/USDT",
        "15m",
        limit=500,
        market_category="Crypto",
    )

    assert rows == []
    assert attempts == 1
    assert diagnostics == ""
    assert len(calls) == 1


def test_kline_fetch_failure_reason_includes_attempts_and_diagnostics():
    reason = TradingExecutor._kline_fetch_failure_reason(
        "CNStock",
        "603618",
        "15m",
        500,
        3,
        "TwelveData=0, Eastmoney=0, yfinance=0, AkShare=0",
    )

    assert "CNStock:603618" in reason
    assert "timeframe=15m" in reason
    assert "limit=500" in reason
    assert "attempts=3" in reason
    assert "Eastmoney=0" in reason


def test_cnstock_initial_kline_uses_fetch_error_when_diagnostics_empty(monkeypatch):
    executor = _executor_without_init()

    monkeypatch.setenv("CNSTOCK_INITIAL_KLINE_RETRIES", "1")
    monkeypatch.setattr(executor, "_format_kline_fetch_diagnostics", lambda market_category: "")

    def fake_fetch_latest_kline(*args, **kwargs):
        executor._last_kline_fetch_error = "boom"
        return []

    monkeypatch.setattr(executor, "_fetch_latest_kline", fake_fetch_latest_kline)

    rows, attempts, diagnostics = executor._fetch_initial_kline_with_retries(
        42,
        "603618",
        "1H",
        limit=500,
        market_category="CNStock",
    )

    assert rows == []
    assert attempts == 1
    assert diagnostics == "fetch_error=boom"


def test_cnstock_initial_kline_records_missing_diagnostics(monkeypatch):
    executor = _executor_without_init()

    monkeypatch.setenv("CNSTOCK_INITIAL_KLINE_RETRIES", "1")
    monkeypatch.setattr(executor, "_format_kline_fetch_diagnostics", lambda market_category: "")
    monkeypatch.setattr(executor, "_fetch_latest_kline", lambda *args, **kwargs: [])

    rows, attempts, diagnostics = executor._fetch_initial_kline_with_retries(
        42,
        "603618",
        "1H",
        limit=500,
        market_category="CNStock",
    )

    assert rows == []
    assert attempts == 1
    assert diagnostics == "no CNStock data source diagnostics were recorded"
