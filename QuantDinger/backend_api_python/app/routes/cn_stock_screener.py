"""CNStock one-click screener API routes."""

from __future__ import annotations

import traceback

from flask import Blueprint, g, jsonify, request

from app import get_trading_executor
from app.services.cn_stock_screener import get_cn_stock_screener_service
from app.services.strategy import StrategyService
from app.utils.auth import login_required
from app.utils.logger import get_logger

logger = get_logger(__name__)

cn_stock_screener_bp = Blueprint("cn_stock_screener", __name__)


def _int_payload(data, key, default):
    try:
        return int(data.get(key, default))
    except Exception:
        return default


def _float_payload(data, key, default):
    try:
        return float(data.get(key, default))
    except Exception:
        return default


@cn_stock_screener_bp.route("/run", methods=["POST"])
@login_required
def run_screener():
    """Run rule-first CNStock screening with optional AI enrichment."""
    try:
        data = request.get_json() or {}
        factors = data.get("factors") if isinstance(data.get("factors"), dict) else {}
        factors.setdefault("include_enrichment", bool(data.get("include_enrichment", True)))
        service = get_cn_stock_screener_service()
        result = service.run(
            user_id=int(g.user_id),
            timeframe=(data.get("timeframe") or "1D").strip(),
            candidate_limit=_int_payload(data, "candidate_limit", 80),
            top_n=_int_payload(data, "top_n", 10),
            ai_top_n=_int_payload(data, "ai_top_n", 5),
            strategy_feedback_days=_int_payload(data, "strategy_feedback_days", 30),
            factors=factors,
        )
        return jsonify({"code": 1, "msg": "success", "data": result})
    except Exception as exc:
        logger.error("CNStock screener run failed: %s", exc)
        logger.error(traceback.format_exc())
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 500


@cn_stock_screener_bp.route("/create-paper-strategies", methods=["POST"])
@login_required
def create_paper_strategies():
    """Create CNStock paper strategies from screener output."""
    try:
        data = request.get_json() or {}
        items = data.get("items") or []
        if not isinstance(items, list) or not items:
            return jsonify({"code": 0, "msg": "items is required", "data": None}), 400

        service = get_cn_stock_screener_service()
        create_result = service.create_paper_strategies(
            user_id=int(g.user_id),
            items=items,
            strategy_name=(data.get("strategy_name") or "A股选股模拟策略").strip(),
            initial_capital=_float_payload(data, "initial_capital", 10000),
            decide_interval=_int_payload(data, "decide_interval", 300),
        )

        started_ids = []
        start_failed = []
        if bool(data.get("start_immediately")) and create_result.get("created_ids"):
            strategy_ids = create_result.get("created_ids") or []
            start_result = StrategyService().batch_start_strategies(strategy_ids, user_id=int(g.user_id))
            started_ids = start_result.get("success_ids") or []
            start_failed = start_result.get("failed_ids") or []
            executor = get_trading_executor()
            for sid in started_ids:
                try:
                    executor.start_strategy(int(sid))
                except Exception as exc:
                    logger.error("Failed to start CNStock screener strategy %s: %s", sid, exc)
                    start_failed.append({"id": sid, "error": str(exc)})

        data_out = {
            **create_result,
            "started_ids": started_ids,
            "start_failed": start_failed,
        }
        return jsonify({"code": 1 if create_result.get("success") else 0, "msg": "success", "data": data_out})
    except Exception as exc:
        logger.error("CNStock screener create strategies failed: %s", exc)
        logger.error(traceback.format_exc())
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 500
