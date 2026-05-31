from app.services.paper_trading import cn_stock


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
