"""A-share one-click screener and paper-strategy creation service."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.data.market_symbols_seed import get_all_symbols, get_hot_symbols, get_symbol_name
from app.services.kline import KlineService
from app.services.strategy import StrategyService
from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)


DEFAULT_TIMEFRAME = "1D"
DEFAULT_CANDIDATE_LIMIT = 80
DEFAULT_TOP_N = 10
DEFAULT_AI_TOP_N = 5
DEFAULT_FEEDBACK_DAYS = 30
MAX_CANDIDATE_LIMIT = 80
MAX_TOP_N = 30
MAX_AI_TOP_N = 10
DEFAULT_ENRICHMENT_TOP_N = 20


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _avg(values: Sequence[float]) -> float:
    vals = [float(v) for v in values if v is not None]
    return sum(vals) / len(vals) if vals else 0.0


def _pct(current: float, base: float) -> float:
    if base <= 0:
        return 0.0
    return (current / base - 1.0) * 100.0


def _normalize_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if "." in s and s.split(".", 1)[0].isdigit():
        s = s.split(".", 1)[0]
    if s.isdigit() and len(s) < 6:
        s = s.zfill(6)
    return s


class CNStockScreenerService:
    """Batch score CNStock candidates and create paper strategies for picks."""

    def __init__(
        self,
        *,
        kline_service: Optional[KlineService] = None,
        strategy_service: Optional[StrategyService] = None,
        fast_analysis_service: Any = None,
    ):
        self.kline_service = kline_service or KlineService()
        self.strategy_service = strategy_service or StrategyService()
        self.fast_analysis_service = fast_analysis_service

    def run(
        self,
        *,
        user_id: int,
        timeframe: str = DEFAULT_TIMEFRAME,
        candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
        top_n: int = DEFAULT_TOP_N,
        ai_top_n: int = DEFAULT_AI_TOP_N,
        strategy_feedback_days: int = DEFAULT_FEEDBACK_DAYS,
        factors: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run rule-first screening and optional AI enrichment."""
        tf = (timeframe or DEFAULT_TIMEFRAME).strip() or DEFAULT_TIMEFRAME
        candidate_limit = max(1, min(int(candidate_limit or DEFAULT_CANDIDATE_LIMIT), MAX_CANDIDATE_LIMIT))
        top_n = max(1, min(int(top_n or DEFAULT_TOP_N), MAX_TOP_N))
        ai_top_n = max(0, min(int(ai_top_n or DEFAULT_AI_TOP_N), MAX_AI_TOP_N, top_n))
        strategy_feedback_days = max(1, min(int(strategy_feedback_days or DEFAULT_FEEDBACK_DAYS), 365))
        factors = factors or {}

        candidates = self.get_candidates(user_id=user_id, limit=candidate_limit)
        feedback_by_symbol = self.get_strategy_feedback(
            user_id=user_id,
            days=strategy_feedback_days,
        )

        items: List[Dict[str, Any]] = []
        skipped: List[Dict[str, Any]] = []
        for candidate in candidates:
            symbol = candidate.get("symbol") or ""
            try:
                klines = self.kline_service.get_kline("CNStock", symbol, tf, 90) or []
                scored = self.score_candidate(
                    candidate=candidate,
                    klines=klines,
                    strategy_feedback=feedback_by_symbol.get(symbol) or self.empty_feedback(),
                    factors=factors,
                )
                items.append(scored)
            except Exception as exc:
                logger.warning("CNStock screener skipped %s: %s", symbol, exc)
                skipped.append({"symbol": symbol, "name": candidate.get("name") or "", "reason": str(exc)})

        items.sort(key=lambda row: float(row.get("score") or 0), reverse=True)
        if bool(factors.get("include_enrichment")):
            self.apply_market_enrichment(items[:max(top_n, ai_top_n, DEFAULT_ENRICHMENT_TOP_N)])
            items.sort(key=lambda row: float(row.get("score") or 0), reverse=True)

        ai_targets = items[:ai_top_n]
        for item in ai_targets:
            self.apply_ai_analysis(item=item, timeframe=tf, user_id=user_id)
            self.finalize_decision(item)

        for item in items[ai_top_n:]:
            self.finalize_decision(item)

        result_items = items[:top_n]
        executable = [x for x in result_items if x.get("decision") == "paper_trade"]

        return {
            "market": "CNStock",
            "timeframe": tf,
            "candidate_count": len(candidates),
            "scored_count": len(items),
            "skipped_count": len(skipped),
            "ai_analyzed_count": len(ai_targets),
            "items": result_items,
            "executable_items": executable,
            "skipped": skipped[:20],
            "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "defaults": {
                "candidate_limit": candidate_limit,
                "top_n": top_n,
                "ai_top_n": ai_top_n,
                "strategy_feedback_days": strategy_feedback_days,
            },
        }

    def get_candidates(self, *, user_id: int, limit: int) -> List[Dict[str, Any]]:
        """Merge user's CNStock watchlist with active/hot seed symbols."""
        merged: Dict[str, Dict[str, Any]] = {}

        def add(row: Dict[str, Any], source: str, priority: int) -> None:
            symbol = _normalize_symbol(row.get("symbol") or "")
            if not symbol:
                return
            existing = merged.get(symbol)
            if existing and int(existing.get("_priority") or 0) >= priority:
                return
            merged[symbol] = {
                "market": "CNStock",
                "symbol": symbol,
                "name": row.get("name") or get_symbol_name("CNStock", symbol) or symbol,
                "source": source,
                "_priority": priority,
            }

        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    SELECT symbol, name
                    FROM qd_watchlist
                    WHERE user_id = ? AND market = 'CNStock'
                    ORDER BY id DESC
                    """,
                    (int(user_id),),
                )
                for row in cur.fetchall() or []:
                    add(dict(row), "watchlist", 10_000)
                cur.close()
        except Exception as exc:
            logger.debug("CNStock watchlist candidate load failed: %s", exc)

        for row in get_hot_symbols("CNStock", limit=max(limit, 10)):
            add(row, "hot", 5_000)
        for row in get_all_symbols("CNStock"):
            add(row, "seed", int(row.get("sort_order") or 0))
            if len(merged) >= max(limit * 2, limit + 20):
                break

        out = sorted(merged.values(), key=lambda row: int(row.get("_priority") or 0), reverse=True)
        for row in out:
            row.pop("_priority", None)
        return out[:limit]

    def score_candidate(
        self,
        *,
        candidate: Dict[str, Any],
        klines: List[Dict[str, Any]],
        strategy_feedback: Dict[str, Any],
        factors: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Compute deterministic factor scores for one symbol."""
        if len(klines) < 30:
            raise ValueError("insufficient_kline_data")

        closes = [_to_float(k.get("close")) for k in klines if _to_float(k.get("close")) > 0]
        highs = [_to_float(k.get("high")) for k in klines if _to_float(k.get("high")) > 0]
        volumes = [_to_float(k.get("volume") or k.get("vol")) for k in klines]
        if len(closes) < 30:
            raise ValueError("insufficient_close_data")

        current = closes[-1]
        ret20 = _pct(current, closes[-21]) if len(closes) >= 21 else 0.0
        ret60 = _pct(current, closes[-61]) if len(closes) >= 61 else ret20
        ma20 = _avg(closes[-20:])
        ma60 = _avg(closes[-60:]) if len(closes) >= 60 else _avg(closes)

        momentum_score = _clamp(ret20 * 1.8 + ret60 * 0.8, -25, 30)
        trend_score = 0.0
        if current > ma20:
            trend_score += 8
        else:
            trend_score -= 8
        if ma20 > ma60:
            trend_score += 10
        else:
            trend_score -= 6
        if current > ma20 > ma60:
            trend_score += 6
        trend_score = _clamp(trend_score, -20, 24)

        daily_returns = []
        for idx in range(max(1, len(closes) - 20), len(closes)):
            prev = closes[idx - 1]
            if prev > 0:
                daily_returns.append((closes[idx] / prev - 1.0) * 100.0)
        volatility = math.sqrt(_avg([(x - _avg(daily_returns)) ** 2 for x in daily_returns])) if daily_returns else 0.0
        volatility_score = _clamp(12 - volatility * 4.0, -18, 12)

        avg_vol20 = _avg([v for v in volumes[-20:] if v > 0])
        avg_vol60 = _avg([v for v in volumes[-60:] if v > 0]) if len(volumes) >= 60 else avg_vol20
        volume_ratio = avg_vol20 / avg_vol60 if avg_vol60 > 0 else 1.0
        if 1.05 <= volume_ratio <= 2.5:
            volume_score = min(14.0, (volume_ratio - 1.0) * 12.0)
        elif volume_ratio > 2.5:
            volume_score = -6.0
        else:
            volume_score = -3.0

        recent_high = max(highs[-60:] or closes[-60:])
        drawdown = (current / recent_high - 1.0) * 100.0 if recent_high > 0 else 0.0
        drawdown_score = _clamp(12 + drawdown * 0.8, -18, 12)

        feedback_score = _clamp(_to_float(strategy_feedback.get("score"), 0.0), -20, 20)
        breakdown = [
            {"key": "momentum", "score": round(momentum_score, 2), "reason": f"20d {ret20:.2f}%, 60d {ret60:.2f}%"},
            {"key": "trend", "score": round(trend_score, 2), "reason": f"price {current:.2f}, MA20 {ma20:.2f}, MA60 {ma60:.2f}"},
            {"key": "volatility", "score": round(volatility_score, 2), "reason": f"20d volatility {volatility:.2f}%"},
            {"key": "volume", "score": round(volume_score, 2), "reason": f"20d/60d volume ratio {volume_ratio:.2f}"},
            {"key": "drawdown", "score": round(drawdown_score, 2), "reason": f"60d drawdown {drawdown:.2f}%"},
            {"key": "strategy_feedback", "score": round(feedback_score, 2), "reason": strategy_feedback.get("summary") or "no recent paper-trading feedback"},
        ]
        raw_rule_score = 50 + momentum_score + trend_score + volatility_score + volume_score + drawdown_score + feedback_score
        rule_score = _clamp(raw_rule_score, 0, 100)

        risk_tags = []
        if volatility > 4:
            risk_tags.append("high_volatility")
        if drawdown < -20:
            risk_tags.append("deep_drawdown")
        if volume_ratio > 2.5:
            risk_tags.append("volume_overheated")
        if strategy_feedback.get("recent_errors"):
            risk_tags.append("strategy_error_feedback")

        return {
            "market": "CNStock",
            "symbol": candidate.get("symbol"),
            "name": candidate.get("name") or candidate.get("symbol"),
            "source": candidate.get("source") or "seed",
            "score": round(rule_score, 2),
            "rule_score": round(rule_score, 2),
            "ai_score": None,
            "feedback_score": round(feedback_score, 2),
            "decision": "skip",
            "ai_decision": None,
            "confidence": None,
            "factor_breakdown": breakdown,
            "risk_tags": risk_tags,
            "strategy_feedback": strategy_feedback,
            "kline_summary": {
                "current_price": round(current, 4),
                "return_20d_pct": round(ret20, 2),
                "return_60d_pct": round(ret60, 2),
                "ma20": round(ma20, 4),
                "ma60": round(ma60, 4),
                "volatility_20d_pct": round(volatility, 2),
                "volume_ratio_20_60": round(volume_ratio, 2),
                "drawdown_60d_pct": round(drawdown, 2),
            },
            "ai_summary": "",
            "ai_reasons": [],
        }

    def apply_market_enrichment(self, items: List[Dict[str, Any]]) -> None:
        """Add A-share direct data factors to already promising candidates."""
        try:
            from app.data_sources.cn_stock_enrichment import fetch_enrichment_bundle, score_enrichment
        except Exception as exc:
            logger.debug("CNStock enrichment module unavailable: %s", exc)
            return

        for item in items:
            symbol = item.get("symbol")
            if not symbol:
                continue
            try:
                enrichment = fetch_enrichment_bundle(symbol, name=item.get("name") or "", include_news=False)
                score_info = score_enrichment(enrichment)
                delta = _to_float(score_info.get("score"), 0.0)
                if delta:
                    item["score"] = round(_clamp(_to_float(item.get("score"), 0) + delta, 0, 100), 2)
                    item["rule_score"] = round(_clamp(_to_float(item.get("rule_score"), 0) + delta, 0, 100), 2)
                item["market_enrichment"] = enrichment
                item["enrichment_score"] = delta
                item.setdefault("factor_breakdown", []).append({
                    "key": "cnstock_enrichment",
                    "score": round(delta, 2),
                    "reason": "；".join(score_info.get("reasons") or []) or "A股资金面/估值增强数据",
                })
            except Exception as exc:
                logger.debug("CNStock market enrichment failed for %s: %s", symbol, exc)
                item.setdefault("risk_tags", []).append("market_enrichment_failed")

    def apply_ai_analysis(self, *, item: Dict[str, Any], timeframe: str, user_id: int) -> None:
        """Enrich one item with existing FastAnalysisService output."""
        try:
            service = self.fast_analysis_service
            if service is None:
                from app.services.fast_analysis import get_fast_analysis_service

                service = get_fast_analysis_service()
            result = service.analyze(
                market="CNStock",
                symbol=item.get("symbol"),
                language="zh-CN",
                timeframe=timeframe,
                user_id=user_id,
            )
            if result.get("error"):
                item.setdefault("risk_tags", []).append("ai_analysis_failed")
                item["ai_summary"] = result.get("error") or ""
                return

            decision = str(result.get("decision") or "HOLD").upper()
            confidence = int(_to_float(result.get("confidence"), 50))
            objective = result.get("objective_score") or {}
            overall = _to_float(objective.get("overall_score"), 0.0)
            ai_score = _clamp(50 + overall * 0.5, 0, 100)
            if decision == "BUY":
                ai_score += min(15, confidence / 8)
            elif decision == "SELL":
                ai_score -= min(20, confidence / 5)
            item["ai_decision"] = decision if decision in ("BUY", "HOLD", "SELL") else "HOLD"
            item["confidence"] = confidence
            item["ai_score"] = round(_clamp(ai_score, 0, 100), 2)
            item["ai_summary"] = result.get("summary") or ""
            item["ai_reasons"] = result.get("reasons") or result.get("key_reasons") or []
            item["fast_analysis_memory_id"] = result.get("memory_id")
            item["score"] = round(_clamp(item["rule_score"] * 0.68 + item["ai_score"] * 0.32, 0, 100), 2)
        except Exception as exc:
            logger.warning("CNStock AI enrichment failed for %s: %s", item.get("symbol"), exc)
            item.setdefault("risk_tags", []).append("ai_analysis_failed")
            item["ai_summary"] = str(exc)

    def finalize_decision(self, item: Dict[str, Any]) -> None:
        score = _to_float(item.get("score"), 0)
        ai_decision = str(item.get("ai_decision") or "").upper()
        confidence = _to_float(item.get("confidence"), 0)
        if ai_decision == "SELL":
            item["decision"] = "skip"
            return
        if ai_decision == "BUY" and confidence >= 60 and score >= 62:
            item["decision"] = "paper_trade"
            return
        if score >= 72 and ai_decision in ("", "HOLD", "BUY"):
            item["decision"] = "paper_trade"
            return
        if score >= 58:
            item["decision"] = "watch"
            return
        item["decision"] = "skip"

    def get_strategy_feedback(self, *, user_id: int, days: int) -> Dict[str, Dict[str, Any]]:
        since = datetime.utcnow() - timedelta(days=days)
        feedback: Dict[str, Dict[str, Any]] = {}
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    SELECT s.symbol,
                           COALESCE(SUM(t.profit), 0) AS realized_pnl,
                           COUNT(t.id) AS trade_count,
                           SUM(CASE WHEN COALESCE(t.profit, 0) > 0 THEN 1 ELSE 0 END) AS win_count
                    FROM qd_strategies_trading s
                    LEFT JOIN qd_strategy_trades t
                      ON t.strategy_id = s.id AND t.created_at >= ?
                    WHERE s.user_id = ?
                      AND s.market_category = 'CNStock'
                      AND s.execution_mode = 'paper'
                    GROUP BY s.symbol
                    """,
                    (since, int(user_id)),
                )
                trade_rows = cur.fetchall() or []

                cur.execute(
                    """
                    SELECT s.symbol,
                           COALESCE(SUM(p.unrealized_pnl), 0) AS unrealized_pnl,
                           COUNT(p.id) AS open_position
                    FROM qd_strategies_trading s
                    LEFT JOIN qd_strategy_positions p
                      ON p.strategy_id = s.id
                    WHERE s.user_id = ?
                      AND s.market_category = 'CNStock'
                      AND s.execution_mode = 'paper'
                    GROUP BY s.symbol
                    """,
                    (int(user_id),),
                )
                position_rows = cur.fetchall() or []

                cur.execute(
                    """
                    SELECT s.symbol, COUNT(l.id) AS error_count
                    FROM qd_strategies_trading s
                    LEFT JOIN qd_strategy_logs l
                      ON l.strategy_id = s.id
                     AND l.timestamp >= ?
                     AND (LOWER(COALESCE(l.level, '')) IN ('error', 'warning')
                          OR LOWER(COALESCE(l.message, '')) LIKE '%error%'
                          OR LOWER(COALESCE(l.message, '')) LIKE '%failed%')
                    WHERE s.user_id = ?
                      AND s.market_category = 'CNStock'
                      AND s.execution_mode = 'paper'
                    GROUP BY s.symbol
                    """,
                    (since, int(user_id)),
                )
                error_rows = cur.fetchall() or []
                cur.close()
        except Exception as exc:
            logger.debug("CNStock strategy feedback load failed: %s", exc)
            return feedback

        for row in trade_rows:
            symbol = _normalize_symbol(row.get("symbol") or "")
            if not symbol:
                continue
            realized = _to_float(row.get("realized_pnl"))
            trade_count = int(_to_float(row.get("trade_count")))
            win_count = int(_to_float(row.get("win_count")))
            win_rate = round(win_count / trade_count * 100, 2) if trade_count else 0.0
            feedback[symbol] = {
                "pnl": realized,
                "realized_pnl": realized,
                "unrealized_pnl": 0.0,
                "win_rate": win_rate,
                "trade_count": trade_count,
                "open_position": 0,
                "recent_errors": 0,
            }

        for row in position_rows:
            symbol = _normalize_symbol(row.get("symbol") or "")
            if not symbol:
                continue
            data = feedback.setdefault(symbol, self.empty_feedback())
            data["unrealized_pnl"] = _to_float(row.get("unrealized_pnl"))
            data["open_position"] = int(_to_float(row.get("open_position")))
            data["pnl"] = _to_float(data.get("realized_pnl")) + _to_float(data.get("unrealized_pnl"))

        for row in error_rows:
            symbol = _normalize_symbol(row.get("symbol") or "")
            if not symbol:
                continue
            data = feedback.setdefault(symbol, self.empty_feedback())
            data["recent_errors"] = int(_to_float(row.get("error_count")))

        for data in feedback.values():
            data["score"] = self._feedback_score(data)
            data["summary"] = self._feedback_summary(data)
        return feedback

    @staticmethod
    def empty_feedback() -> Dict[str, Any]:
        return {
            "pnl": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "win_rate": 0.0,
            "trade_count": 0,
            "open_position": 0,
            "recent_errors": 0,
            "score": 0.0,
            "summary": "no recent paper-trading feedback",
        }

    def _feedback_score(self, data: Dict[str, Any]) -> float:
        pnl = _to_float(data.get("pnl"))
        trade_count = int(_to_float(data.get("trade_count")))
        win_rate = _to_float(data.get("win_rate"))
        errors = int(_to_float(data.get("recent_errors")))
        score = 0.0
        if pnl > 0:
            score += min(10.0, math.log1p(pnl) * 1.5)
        elif pnl < 0:
            score -= min(12.0, math.log1p(abs(pnl)) * 1.8)
        if trade_count >= 3:
            score += _clamp((win_rate - 50.0) / 5.0, -8, 8)
        if errors:
            score -= min(10.0, errors * 2.0)
        return _clamp(score, -20, 20)

    @staticmethod
    def _feedback_summary(data: Dict[str, Any]) -> str:
        if not data.get("trade_count") and not data.get("open_position") and not data.get("recent_errors"):
            return "no recent paper-trading feedback"
        return (
            f"pnl {float(data.get('pnl') or 0):.2f}, "
            f"win_rate {float(data.get('win_rate') or 0):.1f}%, "
            f"trades {int(data.get('trade_count') or 0)}, "
            f"open_positions {int(data.get('open_position') or 0)}, "
            f"errors {int(data.get('recent_errors') or 0)}"
        )

    def create_paper_strategies(
        self,
        *,
        user_id: int,
        items: List[Dict[str, Any]],
        strategy_name: str,
        initial_capital: float,
        decide_interval: int,
    ) -> Dict[str, Any]:
        symbols = []
        for item in items or []:
            symbol = _normalize_symbol(item.get("symbol") if isinstance(item, dict) else str(item))
            if symbol and symbol not in symbols:
                symbols.append(symbol)
        if not symbols:
            raise ValueError("items must include at least one CNStock symbol")

        base_name = (strategy_name or "").strip() or "A股选股模拟策略"
        payload = {
            "user_id": int(user_id),
            "strategy_name": base_name,
            "strategy_type": "IndicatorStrategy",
            "market_category": "CNStock",
            "execution_mode": "paper",
            "strategy_mode": "signal",
            "symbols": [f"CNStock:{s}" for s in symbols],
            "decide_interval": max(60, int(decide_interval or 300)),
            "trading_config": {
                "timeframe": DEFAULT_TIMEFRAME,
                "initial_capital": float(initial_capital or 10000),
                "leverage": 1,
                "market_type": "spot",
                "trade_direction": "long",
                "paper_source": "cn_stock_screener",
            },
            "exchange_config": {},
            "notification_config": {},
            "indicator_config": {
                "source": "cn_stock_screener",
                "description": "One-click A-share screener paper strategy",
            },
        }
        return self.strategy_service.batch_create_strategies(payload)


_screener_service: Optional[CNStockScreenerService] = None


def get_cn_stock_screener_service() -> CNStockScreenerService:
    global _screener_service
    if _screener_service is None:
        _screener_service = CNStockScreenerService()
    return _screener_service
