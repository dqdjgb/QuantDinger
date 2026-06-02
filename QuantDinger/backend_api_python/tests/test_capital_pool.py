from app.services import capital_pool


class FakeCursor:
    def __init__(self, state):
        self.state = state
        self.row = {}

    def execute(self, sql, params=()):
        if "strategy_total_capital" in sql and "FROM qd_users" in sql:
            self.row = {"strategy_total_capital": self.state["total"]}
        elif "SUM(COALESCE(capital_allocation_pct" in sql:
            self.row = {"allocated_pct": self.state["allocated"]}
        else:
            self.row = {}

    def fetchone(self):
        return self.row

    def close(self):
        pass


class FakeConn:
    def __init__(self, state):
        self.state = state

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return FakeCursor(self.state)

    def commit(self):
        pass


def patch_db(monkeypatch, *, total=10000, allocated=0.25):
    state = {"total": total, "allocated": allocated}
    monkeypatch.setattr(capital_pool, "get_db_connection", lambda: FakeConn(state))
    return state


def test_normalize_allocation_accepts_percent_input():
    assert capital_pool.normalize_allocation_pct(25) == 0.25
    assert capital_pool.normalize_allocation_pct(0.25) == 0.25


def test_resolve_strategy_allocation_derives_capital(monkeypatch):
    patch_db(monkeypatch, total=10000, allocated=0.25)

    total, pct, allocated_capital = capital_pool.resolve_strategy_allocation(
        1,
        {"capital_allocation_pct": 0.1},
    )

    assert total == 10000
    assert pct == 0.1
    assert allocated_capital == 1000


def test_resolve_strategy_allocation_rejects_over_allocation(monkeypatch):
    patch_db(monkeypatch, total=10000, allocated=0.95)

    try:
        capital_pool.resolve_strategy_allocation(1, {"capital_allocation_pct": 0.1})
    except ValueError as exc:
        assert "exceeds" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_resolve_strategy_allocation_requires_total_capital(monkeypatch):
    patch_db(monkeypatch, total=0, allocated=0)

    try:
        capital_pool.resolve_strategy_allocation(1, {"capital_allocation_pct": 0.1})
    except ValueError as exc:
        assert "total capital" in str(exc)
    else:
        raise AssertionError("expected ValueError")
