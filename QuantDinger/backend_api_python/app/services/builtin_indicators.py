"""
新用户注册时写入内置示例指标（可自由修改、删除）。

通过首条示例名称做幂等：已存在则跳过，避免重复调用 create_user 等边界情况重复插入。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from app.utils.logger import get_logger

logger = get_logger(__name__)


# Used for idempotency on registration. Keep in sync with _builtin_specs()[0]["name"].
_BUILTIN_PACK_ANCHOR_NAME = "[Sample] SuperTrend Trend-Following"


# QuantDinger Indicator IDE contract (the sandbox injects df / pd / np / params):
#   * top of file declares my_indicator_name / my_indicator_description
#   * df = df.copy()  -> work on a private copy
#   * df['buy'] / df['sell'] are boolean Series with length == len(df)
#   * output dict contains plots / signals; every data list MUST have length == len(df)
#   * # @strategy ...  default risk controls; can be overridden in the backtest panel
#   * # @param ... range=a:b:s  auto-detected by the structured parameter tuner
_SUPERTREND_CODE = r'''# ============================================================
# [Sample] SuperTrend Trend-Following -- classic ATR channel flip
# ------------------------------------------------------------
# Idea: build an adaptive band pair (HL2 +/- mult * ATR). The bands
# can only tighten in the prevailing trend direction; price crossing
# the opposite band flips the direction and fires a buy / sell signal.
#
# Design notes:
#   1) ATR uses Wilder smoothing (ewm alpha=1/N) so values match
#      TradingView, MT5 and most pro charting tools.
#   2) Final upper / lower bands are path-dependent: they cannot
#      drift in the unfavourable direction, so we recurse bar by bar
#      in a Python loop instead of pure vectorisation.
#   3) Signals fire on the bar where direction flips -- naturally
#      edge-triggered, no repeat entries while the trend persists.
#   4) Direction is compared against the PREVIOUS final band only
#      (cl[i] vs final_*[i-1]) -> strictly no look-ahead bias.
# ============================================================

my_indicator_name = "[Sample] SuperTrend Trend-Following"
my_indicator_description = (
    "Classic SuperTrend: ATR-channel direction flip. Open on trend flip, "
    "close on reverse flip. Tweak leverage / SL / TP / symbol / timeframe "
    "in the backtest panel, or sweep params in the Smart Tuner."
)

# ===== Default risk controls (overridable in the backtest panel) =====
# @strategy stopLossPct 0.04
# @strategy takeProfitPct 0.10
# @strategy entryPct 1
# @strategy tradeDirection both

# ===== Tunable params (auto-detected by the structured tuner via range=...) =====
# @param atr_period int 10 ATR Wilder smoothing period range=7:21:1
# @param multiplier float 3.0 ATR band multiplier range=1.5:5.0:0.5

atr_period = int(params.get('atr_period', 10))
multiplier = float(params.get('multiplier', 3.0))

df = df.copy()
high = df['high']
low = df['low']
close = df['close']
prev_close = close.shift(1)

# --- 1) True Range = max(H-L, |H-prevC|, |L-prevC|)
tr = pd.concat([
    high - low,
    (high - prev_close).abs(),
    (low - prev_close).abs(),
], axis=1).max(axis=1)

# --- 2) ATR via Wilder smoothing (RMA); first atr_period-1 bars are NaN
atr = tr.ewm(alpha=1.0 / atr_period, adjust=False, min_periods=atr_period).mean()

# --- 3) Basic upper / lower bands
hl2 = (high + low) / 2.0
upper_basic = hl2 + multiplier * atr
lower_basic = hl2 - multiplier * atr

# --- 4) Final bands + direction (path-dependent loop)
n = len(df)
ub = upper_basic.to_numpy()
lb = lower_basic.to_numpy()
cl = close.to_numpy()

final_upper = np.full(n, np.nan)
final_lower = np.full(n, np.nan)
direction = np.zeros(n, dtype=np.int8)   # 1=long, -1=short, 0=warmup
supertrend = np.full(n, np.nan)

# Wait for Wilder ATR to stabilise before emitting any direction
start_idx = int(atr_period)

for i in range(n):
    if i < start_idx or np.isnan(ub[i]) or np.isnan(lb[i]):
        # Warmup bar: no signal, direction stays 0
        continue

    if i == start_idx or direction[i - 1] == 0:
        # First valid bar: seed direction from close vs band midline
        final_upper[i] = ub[i]
        final_lower[i] = lb[i]
        direction[i] = 1 if cl[i] >= (ub[i] + lb[i]) / 2.0 else -1
        supertrend[i] = final_lower[i] if direction[i] == 1 else final_upper[i]
        continue

    # Upper band may only tighten downward, unless price already broke above it
    if (ub[i] < final_upper[i - 1]) or (cl[i - 1] > final_upper[i - 1]):
        final_upper[i] = ub[i]
    else:
        final_upper[i] = final_upper[i - 1]

    # Lower band may only tighten upward, unless price already broke below it
    if (lb[i] > final_lower[i - 1]) or (cl[i - 1] < final_lower[i - 1]):
        final_lower[i] = lb[i]
    else:
        final_lower[i] = final_lower[i - 1]

    # Direction flip when close breaks the previous final band
    if cl[i] > final_upper[i - 1]:
        direction[i] = 1
    elif cl[i] < final_lower[i - 1]:
        direction[i] = -1
    else:
        direction[i] = direction[i - 1]

    supertrend[i] = final_lower[i] if direction[i] == 1 else final_upper[i]

# --- 5) Edge-triggered signals: direction -1 -> 1 = buy, 1 -> -1 = sell
prev_direction = np.concatenate([[0], direction[:-1]])
buy_mask = (direction == 1) & (prev_direction == -1)
sell_mask = (direction == -1) & (prev_direction == 1)

df['buy'] = pd.Series(buy_mask, index=df.index).astype(bool)
df['sell'] = pd.Series(sell_mask, index=df.index).astype(bool)

# --- 6) Two-colour SuperTrend line: green while long, red while short
supertrend_up = [float(v) if (d == 1 and not np.isnan(v)) else None
                 for v, d in zip(supertrend, direction)]
supertrend_dn = [float(v) if (d == -1 and not np.isnan(v)) else None
                 for v, d in zip(supertrend, direction)]

buy_marks = [df['low'].iloc[i] * 0.995 if bool(df['buy'].iloc[i]) else None
             for i in range(n)]
sell_marks = [df['high'].iloc[i] * 1.005 if bool(df['sell'].iloc[i]) else None
              for i in range(n)]

output = {
    'name': my_indicator_name,
    'plots': [
        {'name': 'SuperTrend Up', 'data': supertrend_up, 'color': '#00E676', 'overlay': True},
        {'name': 'SuperTrend Down', 'data': supertrend_dn, 'color': '#FF5252', 'overlay': True},
    ],
    'signals': [
        {'type': 'buy', 'text': 'B', 'data': buy_marks, 'color': '#00E676'},
        {'type': 'sell', 'text': 'S', 'data': sell_marks, 'color': '#FF5252'},
    ],
}
'''


_MACD_CODE = r'''# ============================================================
# [Sample] MACD Trend-Following -- classic momentum confirmation
# ------------------------------------------------------------
# Idea: MACD captures trend momentum by comparing fast and slow EMAs.
# A bullish cross opens a long signal, and a bearish cross closes or
# reverses depending on the backtest panel's trade direction settings.
#
# Design notes:
#   1) Signals are edge-triggered on MACD / signal-line crosses.
#   2) The histogram is plotted for momentum inspection.
#   3) All periods are declared as @param values for Smart Tuner sweeps.
# ============================================================

my_indicator_name = "[Sample] MACD Trend-Following"
my_indicator_description = (
    "Classic MACD trend-following strategy: buy when MACD crosses above "
    "the signal line, sell when it crosses below. Best used to confirm "
    "directional momentum after a trend has started."
)

# ===== Default risk controls (overridable in the backtest panel) =====
# @strategy stopLossPct 0.03
# @strategy takeProfitPct 0.08
# @strategy entryPct 0.5
# @strategy trailingEnabled true
# @strategy trailingStopPct 0.025
# @strategy trailingActivationPct 0.04
# @strategy tradeDirection both

# ===== Tunable params =====
# @param fast_period int 12 Fast EMA period range=8:16:1
# @param slow_period int 26 Slow EMA period range=20:34:2
# @param signal_period int 9 MACD signal EMA period range=5:13:1

fast_period = int(params.get('fast_period', 12))
slow_period = int(params.get('slow_period', 26))
signal_period = int(params.get('signal_period', 9))

df = df.copy()
close = df['close']

fast_ema = close.ewm(span=fast_period, adjust=False, min_periods=fast_period).mean()
slow_ema = close.ewm(span=slow_period, adjust=False, min_periods=slow_period).mean()
macd = fast_ema - slow_ema
signal = macd.ewm(span=signal_period, adjust=False, min_periods=signal_period).mean()
histogram = macd - signal

raw_buy = (macd > signal) & (macd.shift(1) <= signal.shift(1))
raw_sell = (macd < signal) & (macd.shift(1) >= signal.shift(1))

df['buy'] = raw_buy.fillna(False).astype(bool)
df['sell'] = raw_sell.fillna(False).astype(bool)

n = len(df)
buy_marks = [df['low'].iloc[i] * 0.995 if bool(df['buy'].iloc[i]) else None
             for i in range(n)]
sell_marks = [df['high'].iloc[i] * 1.005 if bool(df['sell'].iloc[i]) else None
              for i in range(n)]

output = {
    'name': my_indicator_name,
    'plots': [
        {'name': 'MACD', 'data': macd.fillna(0).tolist(), 'color': '#1677FF', 'overlay': False},
        {'name': 'Signal', 'data': signal.fillna(0).tolist(), 'color': '#FA8C16', 'overlay': False},
        {'name': 'Histogram', 'data': histogram.fillna(0).tolist(), 'color': '#52C41A', 'overlay': False},
    ],
    'signals': [
        {'type': 'buy', 'text': 'B', 'data': buy_marks, 'color': '#00E676'},
        {'type': 'sell', 'text': 'S', 'data': sell_marks, 'color': '#FF5252'},
    ],
}
'''


_BOLLINGER_CODE = r'''# ============================================================
# [Sample] Bollinger Mean Reversion -- classic volatility bands
# ------------------------------------------------------------
# Idea: Bollinger Bands estimate a rolling fair-value zone. This sample
# buys when price recovers back above the lower band after an oversold
# excursion, and sells when price fades back below the upper band after
# an overbought excursion.
#
# Design notes:
#   1) Signals require a re-entry into the bands, not just touching them.
#   2) The middle band is a simple moving average.
#   3) This is a mean-reversion template; it can struggle in strong trends.
# ============================================================

my_indicator_name = "[Sample] Bollinger Mean Reversion"
my_indicator_description = (
    "Classic Bollinger Band mean-reversion strategy: buy when price moves "
    "back above the lower band after an oversold move, sell when price "
    "moves back below the upper band after an overbought move."
)

# ===== Default risk controls (overridable in the backtest panel) =====
# @strategy stopLossPct 0.025
# @strategy takeProfitPct 0.05
# @strategy entryPct 0.35
# @strategy trailingEnabled false
# @strategy tradeDirection both

# ===== Tunable params =====
# @param window int 20 Rolling mean and standard deviation window range=14:40:2
# @param num_std float 2.0 Band width in standard deviations range=1.5:3.0:0.25

window = int(params.get('window', 20))
num_std = float(params.get('num_std', 2.0))

df = df.copy()
close = df['close']

middle = close.rolling(window=window, min_periods=window).mean()
std = close.rolling(window=window, min_periods=window).std()
upper = middle + num_std * std
lower = middle - num_std * std

raw_buy = (close > lower) & (close.shift(1) <= lower.shift(1))
raw_sell = (close < upper) & (close.shift(1) >= upper.shift(1))

df['buy'] = raw_buy.fillna(False).astype(bool)
df['sell'] = raw_sell.fillna(False).astype(bool)

n = len(df)
buy_marks = [df['low'].iloc[i] * 0.995 if bool(df['buy'].iloc[i]) else None
             for i in range(n)]
sell_marks = [df['high'].iloc[i] * 1.005 if bool(df['sell'].iloc[i]) else None
              for i in range(n)]

output = {
    'name': my_indicator_name,
    'plots': [
        {'name': 'Upper Band', 'data': upper.fillna(0).tolist(), 'color': '#722ED1', 'overlay': True},
        {'name': 'Middle Band', 'data': middle.fillna(0).tolist(), 'color': '#8C8C8C', 'overlay': True},
        {'name': 'Lower Band', 'data': lower.fillna(0).tolist(), 'color': '#13C2C2', 'overlay': True},
    ],
    'signals': [
        {'type': 'buy', 'text': 'B', 'data': buy_marks, 'color': '#00E676'},
        {'type': 'sell', 'text': 'S', 'data': sell_marks, 'color': '#FF5252'},
    ],
}
'''


def _builtin_specs() -> List[Dict[str, str]]:
    """内置指标：name / description / code（与指标 IDE、回测引擎约定一致）。

    现在只保留一个高质量示例 —— 经典 SuperTrend，作为「新手第一份指标」
    的标杆样本：注释充分、可调参数化、严格无未来数据、信号边缘触发。
    """
    return [
        {
            "name": _BUILTIN_PACK_ANCHOR_NAME,
            "description": (
                "Classic SuperTrend (ATR-channel direction flip): Wilder-smoothed ATR "
                "drives adaptive upper / lower bands; opens on trend flip and closes "
                "on the reverse flip. Tunable params are declared via @param so the "
                "Smart Tuner can sweep them out-of-the-box."
            ),
            "code": _SUPERTREND_CODE,
        },
        {
            "name": "[Sample] MACD Trend-Following",
            "description": (
                "Classic MACD trend-following strategy: buys on MACD signal-line "
                "bullish crosses and sells on bearish crosses. Useful as a simple "
                "momentum-confirmation template with tunable EMA periods."
            ),
            "code": _MACD_CODE,
        },
        {
            "name": "[Sample] Bollinger Mean Reversion",
            "description": (
                "Classic Bollinger Band mean-reversion strategy: buys after price "
                "recovers from the lower band and sells after it fades from the "
                "upper band. Useful for range-bound market research."
            ),
            "code": _BOLLINGER_CODE,
        },
    ]


def seed_builtin_indicators_for_new_user(db: Any, user_id: int) -> int:
    """
    注册成功后写入示例指标包。若该用户已有锚点名称指标则跳过（幂等）。
    返回本次插入条数。
    """
    if not user_id:
        return 0
    now = int(time.time())
    cur = db.cursor()
    try:
        cur.execute(
            """
            SELECT 1 AS x
            FROM qd_indicator_codes
            WHERE user_id = ? AND name = ?
            LIMIT 1
            """,
            (user_id, _BUILTIN_PACK_ANCHOR_NAME),
        )
        if cur.fetchone():
            return 0

        inserted = 0
        for spec in _builtin_specs():
            cur.execute(
                """
                INSERT INTO qd_indicator_codes
                  (user_id, is_buy, end_time, name, code, description,
                   publish_to_community, pricing_type, price, preview_image, vip_free, review_status,
                   createtime, updatetime, created_at, updated_at)
                VALUES (?, 0, 1, ?, ?, ?, 0, 'free', 0, '', FALSE, NULL, ?, ?, NOW(), NOW())
                """,
                (
                    user_id,
                    spec["name"],
                    spec["code"],
                    spec["description"],
                    now,
                    now,
                ),
            )
            inserted += 1
        db.commit()
        if inserted:
            logger.info("Seeded %s builtin indicator(s) for new user_id=%s", inserted, user_id)
        return inserted
    except Exception as e:
        logger.warning("seed_builtin_indicators_for_new_user failed user_id=%s: %s", user_id, e)
        try:
            db.rollback()
        except Exception:
            pass
        return 0
    finally:
        try:
            cur.close()
        except Exception:
            pass
