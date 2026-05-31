"""
Policy / capability discovery routes.

Read-only endpoints that expose backend policy matrices to the frontend so
the UI does not have to hard-code its own copy of broker x market rules.
The frontend fetches these once at app boot and caches them in
sessionStorage; nothing here is per-user, so caching is safe.
"""
from flask import Blueprint, jsonify

from app.services.broker_market_policy import to_dict as broker_market_policy_dict


policy_bp = Blueprint('policy', __name__)


@policy_bp.route('/broker-market', methods=['GET'])
def get_broker_market_policy():
    """Return the full broker x market x market_type compatibility matrix.

    Response shape (kept stable for the frontend):
      {
        "code": 1,
        "data": {
          "broker_markets": {
              "ibkr":   {"USStock": ["spot"]},
              "mt5":    {"Forex":   ["spot"]},
              "alpaca": {"USStock": ["spot"], "Crypto": ["spot"]},
              "binance": {"Crypto":  ["spot", "swap"]},
              ...
          },
          "long_only_brokers": ["alpaca", "ibkr"],
          "bot_type_markets": {
              "grid":       ["Crypto", "Forex"],
              "martingale": ["Crypto"],
              "dca":        ["Crypto", "Forex", "USStock"],
              "trend":      ["Crypto", "Forex", "USStock"]
          },
          "live_market_categories": ["Crypto", "Forex", "USStock"],
          "paper_market_categories": ["CNStock", "Crypto", "Forex", "USStock"],
          "paper_market_rules": {
              "CNStock": {
                  "execution_mode": "paper",
                  "market_type": "spot",
                  "trade_direction": "long",
                  "lot_size": 100,
                  "t_plus_1": true
              }
          }
        }
      }
    """
    return jsonify({"code": 1, "data": broker_market_policy_dict()})
