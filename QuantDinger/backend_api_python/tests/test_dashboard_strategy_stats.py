from datetime import datetime, timedelta, timezone

from app.routes.dashboard import _compute_strategy_stats
from app.services import trading_executor as trading_executor_module
from app.services.trading_executor import TradingExecutor


def test_strategy_stats_include_unrealized_pnl_and_count_open_events():
    strategies = [
        {
            "id": 1,
            "strategy_name": "A股选股模拟策略-000333",
            "initial_capital": 100000,
            "market_category": "CNStock",
        }
    ]
    trades = [
        {"strategy_id": 1, "type": "open_long", "profit": None, "commission": 2.0},
        {"strategy_id": 1, "type": "open_long", "profit": None, "commission": 3.0},
        {"strategy_id": 1, "type": "open_long", "profit": None, "commission": 4.0},
    ]
    positions = [
        {"strategy_id": 1, "unrealized_pnl": 270.0},
        {"strategy_id": 1, "unrealized_pnl": 160.0},
        {"strategy_id": 1, "unrealized_pnl": 80.0},
    ]

    stats = _compute_strategy_stats(trades, strategies, positions)

    assert len(stats) == 1
    row = stats[0]
    assert row["total_trades"] == 3
    assert row["win_rate"] == 0.0
    assert row["realized_pnl"] == 0.0
    assert row["unrealized_pnl"] == 510.0
    assert row["total_pnl"] == 510.0


def test_cnstock_signal_time_epoch_is_market_session_time():
    market_dt = datetime(2026, 6, 2, 14, 57, tzinfo=timezone(timedelta(hours=8)))

    assert int(market_dt.timestamp()) == 1780383420


def test_record_trade_uses_signal_timestamp_for_local_simulation(monkeypatch):
    calls = []

    class FakeCursor:
        def execute(self, query, params=None):
            calls.append((query, params))

        def fetchone(self):
            return {"user_id": 7}

        def close(self):
            pass

    class FakeDb:
        def cursor(self):
            return FakeCursor()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(trading_executor_module, "get_db_connection", lambda: FakeDb())
    executor = TradingExecutor.__new__(TradingExecutor)
    signal_ts = int(datetime(2026, 6, 2, 14, 57, tzinfo=timezone(timedelta(hours=8))).timestamp())

    executor._record_trade(
        strategy_id=1,
        symbol="000333",
        type="open_long",
        price=81.7,
        amount=300,
        value=24510,
        commission=1.2,
        signal_ts=signal_ts,
    )

    insert_query, insert_params = calls[-1]
    assert "to_timestamp" in insert_query
    assert insert_params[-2:] == (signal_ts, signal_ts)
