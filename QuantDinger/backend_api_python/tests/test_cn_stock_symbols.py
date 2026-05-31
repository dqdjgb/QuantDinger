import sys
import types

from app.data_sources import cn_stock_symbols


class _FakeRow:
    def __init__(self, data):
        self._data = data

    def get(self, key, default=None):
        return self._data.get(key, default)


class _FakeDataFrame:
    empty = False

    def __init__(self, rows):
        self._rows = rows
        self.columns = list(rows[0].keys()) if rows else []

    def iterrows(self):
        for idx, row in enumerate(self._rows):
            yield idx, _FakeRow(row)


def test_search_a_share_symbols_uses_akshare_code_name(monkeypatch):
    fake_ak = types.SimpleNamespace(
        stock_info_a_code_name=lambda: _FakeDataFrame([
            {"code": "600519", "name": "贵州茅台"},
            {"code": "000001", "name": "平安银行"},
            {"code": "300750", "name": "宁德时代"},
        ])
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)
    monkeypatch.setitem(cn_stock_symbols._CACHE, "rows", [])
    monkeypatch.setitem(cn_stock_symbols._CACHE, "ts", 0.0)

    results = cn_stock_symbols.search_a_share_symbols("茅台", limit=5)

    assert results == [{
        "market": "CNStock",
        "symbol": "600519",
        "name": "贵州茅台",
        "exchange": "SSE",
        "currency": "CNY",
    }]


def test_search_a_share_symbols_filters_code_and_excludes(monkeypatch):
    fake_ak = types.SimpleNamespace(
        stock_info_a_code_name=lambda: _FakeDataFrame([
            {"代码": "600519", "名称": "贵州茅台"},
            {"代码": "600036", "名称": "招商银行"},
        ])
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)
    monkeypatch.setitem(cn_stock_symbols._CACHE, "rows", [])
    monkeypatch.setitem(cn_stock_symbols._CACHE, "ts", 0.0)

    results = cn_stock_symbols.search_a_share_symbols("600", limit=5, exclude={"600519"})

    assert [r["symbol"] for r in results] == ["600036"]
