from app.data_sources import cn_stock_enrichment as mod


class _NoWaitLimiter:
    def wait(self):
        return 0


class _Resp:
    def __init__(self, payload, text=None):
        self.payload = payload
        self.text = text if text is not None else "x"

    def json(self):
        return self.payload


def setup_function():
    mod._CACHE.clear()


def test_fetch_stock_profile_parses_eastmoney_quote(monkeypatch):
    monkeypatch.setattr(mod, "get_eastmoney_limiter", lambda: _NoWaitLimiter())

    def fake_get(url, params=None, headers=None, timeout=None):
        assert params["secid"] == "1.600519"
        return _Resp({
            "data": {
                "f58": "贵州茅台",
                "f84": 1256197800,
                "f85": 1256197800,
                "f116": 1880000000000,
                "f117": 1880000000000,
                "f127": "酿酒行业",
                "f162": 2534,
                "f167": 921,
                "f168": 74,
                "f173": 210,
                "f189": 182001,
                "f190": 130000,
            }
        })

    monkeypatch.setattr(mod.requests, "get", fake_get)

    profile = mod.fetch_stock_profile("600519")

    assert profile["symbol"] == "600519"
    assert profile["name"] == "贵州茅台"
    assert profile["exchange"] == "SSE"
    assert profile["industry"] == "酿酒行业"
    assert profile["market_cap"] == 18800
    assert profile["pe_ratio"] == 25.34
    assert profile["pb_ratio"] == 9.21
    assert profile["turnover_rate"] == 0.74


def test_fetch_money_flow_summarizes_recent_rows(monkeypatch):
    monkeypatch.setattr(mod, "get_eastmoney_limiter", lambda: _NoWaitLimiter())
    rows = [
        "2026-05-25,100,1.5",
        "2026-05-26,200,2.5",
        "2026-05-27,-50,-0.5",
    ]
    monkeypatch.setattr(mod.requests, "get", lambda *args, **kwargs: _Resp({"data": {"klines": rows}}))

    flow = mod.fetch_money_flow("000001", limit=3)

    assert flow["latest"]["date"] == "2026-05-27"
    assert flow["main_net_inflow_5d"] == 250
    assert flow["main_net_ratio_5d_avg"] == 1.17


def test_score_enrichment_rewards_positive_money_flow():
    result = mod.score_enrichment({
        "money_flow": {"main_net_ratio_5d_avg": 5.2},
        "profile": {"turnover_rate": 3.0, "pe_ratio": 20.0},
    })

    assert result["score"] == 12
    assert "5日主力净流入占比较高" in result["reasons"]
