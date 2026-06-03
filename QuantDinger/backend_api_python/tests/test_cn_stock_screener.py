from app.services import cn_stock_screener as mod
from app.services.cn_stock_screener import CNStockScreenerService
from app.services.strategy import StrategyService


class _FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, *args, **kwargs):
        return None

    def fetchall(self):
        return self.rows

    def close(self):
        return None


class _FakeDb:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return _FakeCursor(self.rows)


class _FakeKline:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def get_kline(self, market, symbol, timeframe, limit):
        self.calls.append((market, symbol, timeframe, limit))
        return self.rows.get(symbol, [])


class _FakeFastAnalysis:
    def __init__(self):
        self.calls = []

    def analyze(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "decision": "BUY",
            "confidence": 72,
            "objective_score": {"overall_score": 38},
            "summary": "AI likes the setup",
            "reasons": ["trend confirmation"],
            "memory_id": 11,
        }


class _FakeStrategyService:
    def __init__(self):
        self.payload = None

    def batch_create_strategies(self, payload):
        self.payload = payload
        return {
            "success": True,
            "strategy_group_id": "abc123",
            "created_ids": [1, 2],
            "failed_symbols": [],
        }


class _FakeScreenerRouteService:
    def __init__(self):
        self.kwargs = None

    def run(self, **kwargs):
        self.kwargs = kwargs
        return {
            "market": "CNStock",
            "candidate_count": 0,
            "scored_count": 0,
            "ai_analyzed_count": kwargs.get("ai_top_n"),
            "items": [],
        }


def _klines(count=90, start=10.0, step=0.2, volume=1000.0):
    rows = []
    for idx in range(count):
        close = start + idx * step
        rows.append({
            "open": close - 0.1,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": volume + idx * 10,
        })
    return rows


def test_get_candidates_prioritizes_watchlist_and_limits(monkeypatch):
    monkeypatch.setattr(mod, "get_db_connection", lambda: _FakeDb([
        {"symbol": "600519", "name": "贵州茅台"},
        {"symbol": "000001", "name": "平安银行"},
    ]))
    monkeypatch.setattr(mod, "get_hot_symbols", lambda market, limit: [
        {"symbol": "600519", "name": "贵州茅台"},
        {"symbol": "600036", "name": "招商银行"},
    ])
    monkeypatch.setattr(mod, "get_all_symbols", lambda market: [
        {"symbol": "300750", "name": "宁德时代", "sort_order": 92},
        {"symbol": "002594", "name": "比亚迪", "sort_order": 91},
    ])

    service = CNStockScreenerService(kline_service=_FakeKline({}))
    result = service.get_candidates(user_id=7, limit=3)

    assert [r["symbol"] for r in result] == ["600519", "000001", "600036"]
    assert result[0]["source"] == "watchlist"


def test_run_skips_insufficient_kline_and_only_ai_analyzes_top_n(monkeypatch):
    rows = {
        "600519": _klines(start=10, step=0.3),
        "600036": _klines(start=10, step=0.1),
        "000001": _klines(count=10),
    }
    fake_ai = _FakeFastAnalysis()
    service = CNStockScreenerService(kline_service=_FakeKline(rows), fast_analysis_service=fake_ai)
    monkeypatch.setattr(service, "get_candidates", lambda user_id, limit: [
        {"market": "CNStock", "symbol": "600519", "name": "贵州茅台"},
        {"market": "CNStock", "symbol": "600036", "name": "招商银行"},
        {"market": "CNStock", "symbol": "000001", "name": "平安银行"},
    ])
    monkeypatch.setattr(service, "get_strategy_feedback", lambda user_id, days: {})

    result = service.run(user_id=7, top_n=2, ai_top_n=1, candidate_limit=3)

    assert result["scored_count"] == 2
    assert result["skipped_count"] == 1
    assert result["ai_analyzed_count"] == 1
    assert len(fake_ai.calls) == 1
    assert result["items"][0]["ai_decision"] == "BUY"
    assert result["items"][1]["ai_decision"] is None


def test_run_respects_zero_ai_top_n(monkeypatch):
    rows = {
        "600519": _klines(start=10, step=0.3),
        "600036": _klines(start=10, step=0.1),
    }
    fake_ai = _FakeFastAnalysis()
    service = CNStockScreenerService(kline_service=_FakeKline(rows), fast_analysis_service=fake_ai)
    monkeypatch.setattr(service, "get_candidates", lambda user_id, limit: [
        {"market": "CNStock", "symbol": "600519", "name": "璐靛窞鑼呭彴"},
        {"market": "CNStock", "symbol": "600036", "name": "鎷涘晢閾惰"},
    ])
    monkeypatch.setattr(service, "get_strategy_feedback", lambda user_id, days: {})

    result = service.run(user_id=7, top_n=2, ai_top_n=0, candidate_limit=2)

    assert result["ai_analyzed_count"] == 0
    assert len(fake_ai.calls) == 0
    assert all(item["ai_decision"] is None for item in result["items"])


def test_route_allows_requested_sync_ai_top_n_by_default(monkeypatch):
    from flask import Flask, g
    from app.routes import cn_stock_screener as route_mod

    fake_service = _FakeScreenerRouteService()
    monkeypatch.setattr(route_mod, "get_cn_stock_screener_service", lambda: fake_service)

    app = Flask(__name__)
    with app.test_request_context(
        "/api/cn-stock-screener/run",
        method="POST",
        json={"ai_top_n": 5, "candidate_limit": 80, "top_n": 10},
    ):
        g.user_id = 7
        response = route_mod.run_screener.__wrapped__()

    payload = response.get_json()
    assert payload["code"] == 1
    assert fake_service.kwargs["candidate_limit"] == route_mod.SYNC_CANDIDATE_MAX
    assert fake_service.kwargs["ai_top_n"] == 5
    assert payload["data"]["sync_limits"]["effective_ai_top_n"] == 5


def test_route_can_disable_sync_ai(monkeypatch):
    from flask import Flask, g
    from app.routes import cn_stock_screener as route_mod

    fake_service = _FakeScreenerRouteService()
    monkeypatch.setattr(route_mod, "get_cn_stock_screener_service", lambda: fake_service)

    app = Flask(__name__)
    with app.test_request_context(
        "/api/cn-stock-screener/run",
        method="POST",
        json={"ai_top_n": 5, "sync_ai": False},
    ):
        g.user_id = 7
        response = route_mod.run_screener.__wrapped__()

    payload = response.get_json()
    assert payload["code"] == 1
    assert fake_service.kwargs["ai_top_n"] == 0
    assert payload["data"]["sync_limits"]["effective_ai_top_n"] == 0


def test_strategy_feedback_is_neutral_when_absent():
    service = CNStockScreenerService(kline_service=_FakeKline({}))
    item = service.score_candidate(
        candidate={"symbol": "600519", "name": "贵州茅台"},
        klines=_klines(),
        strategy_feedback=service.empty_feedback(),
        factors={},
    )

    assert item["feedback_score"] == 0
    assert item["strategy_feedback"]["trade_count"] == 0


def test_create_paper_strategies_forces_cnstock_paper_payload(monkeypatch):
    monkeypatch.setattr(mod, "get_strategy_total_capital", lambda user_id: 100000)
    strategy_service = _FakeStrategyService()
    service = CNStockScreenerService(
        kline_service=_FakeKline({}),
        strategy_service=strategy_service,
    )

    result = service.create_paper_strategies(
        user_id=7,
        items=[{"symbol": "600519"}, {"symbol": "000001"}],
        strategy_name="筛选策略",
        initial_capital=20000,
        decide_interval=180,
    )

    payload = strategy_service.payload
    assert result["created_ids"] == [1, 2]
    assert payload["market_category"] == "CNStock"
    assert payload["execution_mode"] == "paper"
    assert payload["symbols"] == ["CNStock:600519", "CNStock:000001"]
    assert payload["trading_config"]["market_type"] == "spot"
    assert payload["trading_config"]["trade_direction"] == "long"
    assert payload["trading_config"]["initial_capital"] == 20000


def test_create_paper_strategies_derives_capital_allocation_from_pool(monkeypatch):
    monkeypatch.setattr(mod, "get_strategy_total_capital", lambda user_id: 100000)
    strategy_service = _FakeStrategyService()
    service = CNStockScreenerService(
        kline_service=_FakeKline({}),
        strategy_service=strategy_service,
    )

    service.create_paper_strategies(
        user_id=7,
        items=[{"symbol": "600519"}, {"symbol": "000001"}],
        strategy_name="筛选策略",
        initial_capital=20000,
    )

    payload = strategy_service.payload
    assert payload["trading_config"]["initial_capital"] == 20000
    assert payload["trading_config"]["capital_allocation_pct"] == 0.2


def test_create_paper_strategies_defaults_to_auto_position_sizing(monkeypatch):
    monkeypatch.setattr(mod, "get_strategy_total_capital", lambda user_id: 100000)
    strategy_service = _FakeStrategyService()
    service = CNStockScreenerService(
        kline_service=_FakeKline({}),
        strategy_service=strategy_service,
    )

    service.create_paper_strategies(
        user_id=7,
        items=[{"symbol": "600519"}],
        strategy_name="auto size",
        initial_capital=20000,
        trading_config={},
    )

    payload = strategy_service.payload
    assert payload["trading_config"]["position_sizing_mode"] == "auto"
    assert "entry_pct" not in payload["trading_config"]
    assert "position_pct" not in payload["trading_config"]


def test_create_paper_strategies_does_not_force_minimum_position_pct(monkeypatch):
    monkeypatch.setattr(mod, "get_strategy_total_capital", lambda user_id: 100000)
    strategy_service = _FakeStrategyService()
    service = CNStockScreenerService(
        kline_service=_FakeKline({}),
        strategy_service=strategy_service,
    )

    service.create_paper_strategies(
        user_id=7,
        items=[{"symbol": "600519"}],
        strategy_name="low size",
        initial_capital=20000,
        trading_config={
            "position_pct": 0.25,
            "max_position_pct": 8,
        },
    )

    payload = strategy_service.payload
    assert payload["trading_config"]["entry_pct"] == 0.25
    assert payload["trading_config"]["position_pct"] == 0.25
    assert payload["trading_config"]["max_position_pct"] == 8


def test_create_paper_strategies_accepts_symbol_position_overrides(monkeypatch):
    monkeypatch.setattr(mod, "get_strategy_total_capital", lambda user_id: 100000)
    strategy_service = _FakeStrategyService()
    service = CNStockScreenerService(
        kline_service=_FakeKline({}),
        strategy_service=strategy_service,
    )

    service.create_paper_strategies(
        user_id=7,
        items=[
            {"symbol": "600519", "position_pct": 5},
            {"symbol": "000001", "trading_config": {"entry_pct": 12, "max_position_pct": 30}},
        ],
        strategy_name="symbol override",
        initial_capital=20000,
        trading_config={},
    )

    symbol_configs = strategy_service.payload["trading_config"]["symbol_trading_configs"]
    assert symbol_configs["600519"]["entry_pct"] == 5
    assert symbol_configs["600519"]["position_pct"] == 5
    assert symbol_configs["000001"]["entry_pct"] == 12
    assert symbol_configs["000001"]["max_position_pct"] == 30


def test_strategy_batch_create_applies_symbol_trading_config_overrides(monkeypatch):
    service = StrategyService()
    created_payloads = []

    def fake_create_strategy(payload):
        created_payloads.append(payload)
        return len(created_payloads)

    monkeypatch.setattr(service, "create_strategy", fake_create_strategy)

    result = service.batch_create_strategies({
        "user_id": 7,
        "strategy_name": "batch",
        "strategy_type": "IndicatorStrategy",
        "market_category": "CNStock",
        "execution_mode": "paper",
        "symbols": ["CNStock:600519", "CNStock:000001"],
        "trading_config": {
            "market_type": "spot",
            "trade_direction": "long",
            "position_sizing_mode": "auto",
            "symbol_trading_configs": {
                "600519": {"position_sizing_mode": "fixed_pct", "entry_pct": 5, "position_pct": 5},
                "000001": {"position_sizing_mode": "fixed_pct", "entry_pct": 12, "position_pct": 12},
            },
        },
    })

    assert result["created_ids"] == [1, 2]
    assert created_payloads[0]["trading_config"]["symbol"] == "600519"
    assert created_payloads[0]["trading_config"]["entry_pct"] == 5
    assert created_payloads[1]["trading_config"]["symbol"] == "000001"
    assert created_payloads[1]["trading_config"]["entry_pct"] == 12
    assert "symbol_trading_configs" not in created_payloads[0]["trading_config"]
