from datetime import datetime, timedelta, timezone

from app.services.paper_trading import cn_stock
from app.services import trading_executor as trading_executor_module
from app.services.trading_executor import TradingExecutor


SH_TZ = timezone(timedelta(hours=8))


def _mock_trade_dates(monkeypatch, dates):
    cn_stock._TRADE_DATES_CACHE["ts"] = 0.0
    cn_stock._TRADE_DATES_CACHE["dates"] = set()
    monkeypatch.setattr(cn_stock, "_fetch_trade_dates_from_akshare", lambda: set(dates))


def test_cnstock_buy_rounds_down_to_board_lots():
    fill = cn_stock.build_fill(
        signal_type="open_long",
        requested_amount=250,
        ref_price=10,
        trading_config={},
    )

    assert fill.accepted is True
    assert fill.amount == 200
    assert fill.price == 10
    assert fill.commission == 0.6


def test_cnstock_buy_rejects_less_than_one_lot():
    fill = cn_stock.build_fill(
        signal_type="open_long",
        requested_amount=99,
        ref_price=10,
        trading_config={},
    )

    assert fill.accepted is False
    assert fill.rejection == "cnstock_paper_min_buy_lot_100"


def test_cnstock_t_plus_1_rejects_unsellable_amount():
    fill = cn_stock.build_fill(
        signal_type="close_long",
        requested_amount=200,
        ref_price=10,
        trading_config={},
        sellable_amount=100,
    )

    assert fill.accepted is False
    assert fill.rejection == "cnstock_paper_t_plus_1_sellable_insufficient"


def test_cnstock_sell_fee_includes_stamp_tax():
    fill = cn_stock.build_fill(
        signal_type="close_long",
        requested_amount=100,
        ref_price=10,
        trading_config={},
        sellable_amount=100,
    )

    assert fill.accepted is True
    assert fill.commission == 0.8


def test_cnstock_slippage_moves_against_trade():
    buy = cn_stock.build_fill(
        signal_type="open_long",
        requested_amount=100,
        ref_price=10,
        trading_config={"slippage": 0.001},
    )
    sell = cn_stock.build_fill(
        signal_type="close_long",
        requested_amount=100,
        ref_price=10,
        trading_config={"slippage": 0.001},
        sellable_amount=100,
    )

    assert round(buy.price, 4) == 10.01
    assert round(sell.price, 4) == 9.99


def test_cnstock_trading_time_allows_a_share_sessions(monkeypatch):
    _mock_trade_dates(monkeypatch, {"2026-06-02"})

    assert cn_stock.is_trading_time(datetime(2026, 6, 2, 9, 30, tzinfo=SH_TZ)) is True
    assert cn_stock.is_trading_time(datetime(2026, 6, 2, 11, 30, tzinfo=SH_TZ)) is True
    assert cn_stock.is_trading_time(datetime(2026, 6, 2, 13, 0, tzinfo=SH_TZ)) is True
    assert cn_stock.is_trading_time(datetime(2026, 6, 2, 15, 0, tzinfo=SH_TZ)) is True


def test_cnstock_trading_time_blocks_closed_periods(monkeypatch):
    _mock_trade_dates(monkeypatch, {"2026-06-02"})

    assert cn_stock.is_trading_time(datetime(2026, 6, 2, 9, 29, tzinfo=SH_TZ)) is False
    assert cn_stock.is_trading_time(datetime(2026, 6, 2, 11, 31, tzinfo=SH_TZ)) is False
    assert cn_stock.is_trading_time(datetime(2026, 6, 2, 15, 1, tzinfo=SH_TZ)) is False
    assert cn_stock.is_trading_time(datetime(2026, 6, 6, 10, 0, tzinfo=SH_TZ)) is False


def test_cnstock_trading_window_uses_trade_calendar_and_buffer(monkeypatch):
    _mock_trade_dates(monkeypatch, {"2026-06-02"})

    assert cn_stock.is_trading_window(datetime(2026, 6, 2, 9, 20, tzinfo=SH_TZ)) is True
    assert cn_stock.is_trading_window(datetime(2026, 6, 2, 11, 40, tzinfo=SH_TZ)) is True
    assert cn_stock.is_trading_window(datetime(2026, 6, 2, 11, 40, 1, tzinfo=SH_TZ)) is False
    assert cn_stock.is_trading_window(datetime(2026, 6, 2, 12, 50, tzinfo=SH_TZ)) is True
    assert cn_stock.is_trading_window(datetime(2026, 6, 2, 15, 10, tzinfo=SH_TZ)) is True
    assert cn_stock.is_trading_window(datetime(2026, 6, 2, 9, 19, 59, tzinfo=SH_TZ)) is False
    assert cn_stock.is_trading_window(datetime(2026, 6, 2, 15, 10, 1, tzinfo=SH_TZ)) is False
    assert cn_stock.is_trading_window(datetime(2026, 10, 1, 10, 0, tzinfo=SH_TZ)) is False


def test_cnstock_trading_time_blocks_weekday_not_in_trade_calendar(monkeypatch):
    _mock_trade_dates(monkeypatch, {"2026-06-02"})

    assert cn_stock.is_trading_time(datetime(2026, 10, 1, 10, 0, tzinfo=SH_TZ)) is False


def test_cnstock_trading_calendar_uses_stale_cache_on_refresh_failure(monkeypatch):
    cn_stock._TRADE_DATES_CACHE["ts"] = 1.0
    cn_stock._TRADE_DATES_CACHE["dates"] = {"2026-06-02"}
    monkeypatch.setattr(cn_stock, "_trade_calendar_cache_ttl_sec", lambda: 0)
    monkeypatch.setattr(cn_stock, "_fetch_trade_dates_from_akshare", lambda: (_ for _ in ()).throw(RuntimeError("offline")))

    assert cn_stock.is_trading_day(datetime(2026, 6, 2, 10, 0, tzinfo=SH_TZ)) is True


def test_cnstock_trading_calendar_fails_closed_without_cache(monkeypatch):
    cn_stock._TRADE_DATES_CACHE["ts"] = 0.0
    cn_stock._TRADE_DATES_CACHE["dates"] = set()
    monkeypatch.delenv("CNSTOCK_TRADE_CALENDAR_FALLBACK_WEEKDAY", raising=False)
    monkeypatch.setattr(cn_stock, "_fetch_trade_dates_from_akshare", lambda: (_ for _ in ()).throw(RuntimeError("offline")))

    assert cn_stock.is_trading_day(datetime(2026, 6, 2, 10, 0, tzinfo=SH_TZ)) is False


def test_cnstock_paper_strategy_rejects_when_market_closed(monkeypatch):
    executor = object.__new__(TradingExecutor)
    events = []
    logs = []

    monkeypatch.setattr(trading_executor_module.cn_paper, "is_trading_time", lambda: False)
    monkeypatch.setattr(executor, "_position_state", lambda positions: "flat")
    monkeypatch.setattr(executor, "_is_signal_allowed", lambda state, signal_type: True)
    monkeypatch.setattr(trading_executor_module, "append_strategy_log", lambda *args: logs.append(args))
    monkeypatch.setattr(
        trading_executor_module,
        "append_strategy_execution_event",
        lambda **kwargs: events.append(kwargs),
    )

    accepted = executor._execute_signal(
        strategy_id=1,
        strategy_name="cn-test",
        exchange=None,
        symbol="603618",
        current_price=10.0,
        signal_type="open_long",
        position_size=0.1,
        current_positions=[],
        trade_direction="long",
        leverage=1,
        initial_capital=10000,
        market_type="spot",
        market_category="CNStock",
        execution_mode="paper",
        trading_config={},
    )

    assert accepted is False
    assert events[-1]["reason"] == "cnstock_market_closed"
    assert any("market is closed" in args[2] for args in logs)


def test_cnstock_strategy_tick_skips_outside_trading_window(monkeypatch):
    monkeypatch.setattr(trading_executor_module.cn_paper, "is_trading_window", lambda buffer_minutes=10: False)

    assert TradingExecutor._should_skip_cnstock_strategy_tick("CNStock", buffer_minutes=10) is True
    assert TradingExecutor._should_skip_cnstock_strategy_tick("Crypto", buffer_minutes=10) is False


def test_entry_ai_filter_uses_strategy_market_category(monkeypatch):
    executor = object.__new__(TradingExecutor)
    calls = []

    class FakeBilling:
        def is_billing_enabled(self):
            return False

    class FakeFastAnalysis:
        def analyze(self, market, symbol, language, model=None):
            calls.append((market, symbol, language, model))
            return {"decision": "BUY", "confidence": 82, "summary": "ok"}

    monkeypatch.setattr(
        "app.services.billing_service.get_billing_service",
        lambda: FakeBilling(),
    )
    monkeypatch.setattr(
        "app.services.fast_analysis.get_fast_analysis_service",
        lambda: FakeFastAnalysis(),
    )

    allowed, info = executor._entry_ai_filter_allows(
        strategy_id=5,
        symbol="600010",
        signal_type="open_long",
        ai_model_config={},
        trading_config={},
        market_category="CNStock",
    )

    assert allowed is True
    assert info["analysis_market"] == "CNStock"
    assert calls == [("CNStock", "600010", "zh-CN", None)]


def test_entry_ai_filter_market_config_overrides_strategy_category():
    market = TradingExecutor._resolve_entry_ai_filter_market(
        ai_model_config={"analysis_market": "USStock"},
        trading_config={},
        market_category="CNStock",
    )

    assert market == "USStock"
