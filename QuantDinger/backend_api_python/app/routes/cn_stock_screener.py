"""CNStock one-click screener API routes."""

from __future__ import annotations

import traceback

from flask import Blueprint, g, jsonify, request

from app import get_trading_executor
from app.services.cn_stock_screener import get_cn_stock_screener_service
from app.services.strategy import StrategyService
from app.utils.agent_jobs import get_job, submit_job
from app.utils.auth import login_required
from app.utils.logger import get_logger

logger = get_logger(__name__)

cn_stock_screener_bp = Blueprint("cn_stock_screener", __name__)
ASYNC_CANDIDATE_MAX = 80
ASYNC_AI_MAX_TOP_N = 10
ASYNC_ENRICHMENT_MAX_TOP_N = 5
JOB_KIND = "cn_stock_screener"


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


def _prepare_screener_payload(data):
    payload = data if isinstance(data, dict) else {}
    factors = payload.get("factors") if isinstance(payload.get("factors"), dict) else {}
    factors = {**factors}
    include_enrichment = bool(payload.get("include_enrichment", False))
    factors["include_enrichment"] = include_enrichment
    if include_enrichment:
        factors["enrichment_top_n"] = min(
            max(0, _int_payload(payload, "enrichment_top_n", ASYNC_ENRICHMENT_MAX_TOP_N)),
            ASYNC_ENRICHMENT_MAX_TOP_N,
        )

    requested_ai_top_n = max(0, _int_payload(payload, "ai_top_n", 0))
    sync_ai = bool(payload.get("sync_ai", True))
    ai_top_n = min(requested_ai_top_n, ASYNC_AI_MAX_TOP_N) if sync_ai else 0
    requested_candidate_limit = max(1, _int_payload(payload, "candidate_limit", 80))
    candidate_limit = min(requested_candidate_limit, ASYNC_CANDIDATE_MAX)

    return {
        "timeframe": (payload.get("timeframe") or "1D").strip(),
        "candidate_limit": candidate_limit,
        "top_n": _int_payload(payload, "top_n", 10),
        "ai_top_n": ai_top_n,
        "strategy_feedback_days": _int_payload(payload, "strategy_feedback_days", 30),
        "factors": factors,
        "async_limits": {
            "requested_candidate_limit": requested_candidate_limit,
            "effective_candidate_limit": candidate_limit,
            "max_candidate_limit": ASYNC_CANDIDATE_MAX,
            "requested_ai_top_n": requested_ai_top_n,
            "effective_ai_top_n": ai_top_n,
            "include_enrichment": include_enrichment,
            "max_enrichment_top_n": ASYNC_ENRICHMENT_MAX_TOP_N,
            "reason": "The screener now runs as an asynchronous job; clients poll the job endpoint for progress and results.",
        },
    }


def _run_screener_job(payload, on_progress):
    service = get_cn_stock_screener_service()
    result = service.run(
        user_id=int(payload["user_id"]),
        timeframe=payload["timeframe"],
        candidate_limit=payload["candidate_limit"],
        top_n=payload["top_n"],
        ai_top_n=payload["ai_top_n"],
        strategy_feedback_days=payload["strategy_feedback_days"],
        factors=payload["factors"],
        on_progress=on_progress,
    )
    result["async_limits"] = payload["async_limits"]
    return result


@cn_stock_screener_bp.route("/run", methods=["POST"])
@login_required
def run_screener():
    """Submit rule-first CNStock screening as a background job."""
    try:
        data = request.get_json() or {}
        payload = _prepare_screener_payload(data)
        payload["user_id"] = int(g.user_id)
        job = submit_job(
            user_id=int(g.user_id),
            agent_token_id=None,
            kind=JOB_KIND,
            request_payload=payload,
            runner=_run_screener_job,
        )
        job["poll_url"] = f"/api/cn-stock-screener/jobs/{job['job_id']}"
        job["async_limits"] = payload["async_limits"]
        return jsonify({"code": 1, "msg": "submitted", "data": job})
    except Exception as exc:
        logger.error("CNStock screener submit failed: %s", exc)
        logger.error(traceback.format_exc())
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 500


@cn_stock_screener_bp.route("/jobs/<job_id>", methods=["GET"])
@login_required
def get_screener_job(job_id):
    """Poll a CNStock screener job for status, progress, and result."""
    try:
        row = get_job(job_id, user_id=int(g.user_id))
        if not row or row.get("kind") != JOB_KIND:
            return jsonify({"code": 0, "msg": "Job not found", "data": None}), 404
        data = {
            "job_id": row.get("job_id"),
            "status": row.get("status"),
            "progress": row.get("progress") or {},
            "result": row.get("result"),
            "error": row.get("error"),
            "created_at": row.get("created_at"),
            "started_at": row.get("started_at"),
            "finished_at": row.get("finished_at"),
        }
        return jsonify({"code": 1, "msg": "success", "data": data})
    except Exception as exc:
        logger.error("CNStock screener job lookup failed: %s", exc)
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
            strategy_type=(data.get("strategy_type") or "IndicatorStrategy").strip(),
            strategy_template=(data.get("strategy_template") or "ma_momentum").strip(),
            initial_capital=_float_payload(data, "initial_capital", 10000),
            decide_interval=_int_payload(data, "decide_interval", 300),
            timeframe=(data.get("timeframe") or "1D").strip(),
            trading_config=data.get("trading_config") if isinstance(data.get("trading_config"), dict) else {},
            indicator_params=data.get("indicator_params") if isinstance(data.get("indicator_params"), dict) else {},
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
