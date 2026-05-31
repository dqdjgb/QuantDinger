# Built-in Classic Strategy Pack

This document describes the indicator strategies seeded for new users at registration time.
They are different from the standalone example scripts in `docs/examples/`: the built-in
pack is inserted into each new user's Indicator IDE workspace, while `docs/examples/`
contains repository-level reference files.

These strategies are educational templates for research, backtesting, and customization.
They are not investment advice and should not be used with real capital without independent
review, parameter validation, and risk controls.

## Current Built-in Pack

| Strategy | Style | Best fit | Main failure mode |
| --- | --- | --- | --- |
| `[Sample] SuperTrend Trend-Following` | ATR trend flip | Directional markets with persistent moves | Choppy markets can create repeated whipsaws |
| `[Sample] MACD Trend-Following` | Momentum confirmation | Trends that build after EMA momentum crosses | Late entries and exits during sharp reversals |
| `[Sample] Bollinger Mean Reversion` | Volatility-band reversion | Range-bound markets with repeated overextension | Strong breakouts can keep moving against the signal |

## SuperTrend Trend-Following

SuperTrend builds adaptive upper and lower bands from HL2 plus or minus an ATR multiple.
The active band follows the trend and flips direction when price crosses the opposite band.
This makes it a compact trend-following template with clear visual overlays.

Default parameters:

| Parameter | Default | Meaning |
| --- | ---: | --- |
| `atr_period` | `10` | Wilder-smoothed ATR lookback |
| `multiplier` | `3.0` | ATR band width |

Default risk settings:

| Setting | Default |
| --- | ---: |
| `stopLossPct` | `0.04` |
| `takeProfitPct` | `0.10` |
| `entryPct` | `1` |
| `tradeDirection` | `both` |

## MACD Trend-Following

MACD compares a fast EMA with a slow EMA, then smooths the difference with a signal line.
The built-in strategy buys on bullish MACD/signal crosses and sells on bearish crosses.
It is useful when the user wants a familiar momentum template with a small number of knobs.

Default parameters:

| Parameter | Default | Meaning |
| --- | ---: | --- |
| `fast_period` | `12` | Fast EMA period |
| `slow_period` | `26` | Slow EMA period |
| `signal_period` | `9` | Signal-line EMA period |

Default risk settings:

| Setting | Default |
| --- | ---: |
| `stopLossPct` | `0.03` |
| `takeProfitPct` | `0.08` |
| `entryPct` | `0.5` |
| `trailingEnabled` | `true` |
| `trailingStopPct` | `0.025` |
| `trailingActivationPct` | `0.04` |
| `tradeDirection` | `both` |

## Bollinger Mean Reversion

Bollinger Bands estimate a rolling fair-value zone with a moving average and standard
deviation envelope. The built-in strategy waits for price to move back inside the band
after an excursion: recovery above the lower band is a buy signal, and fade below the upper
band is a sell signal.

Default parameters:

| Parameter | Default | Meaning |
| --- | ---: | --- |
| `window` | `20` | Rolling mean and standard deviation window |
| `num_std` | `2.0` | Band width in standard deviations |

Default risk settings:

| Setting | Default |
| --- | ---: |
| `stopLossPct` | `0.025` |
| `takeProfitPct` | `0.05` |
| `entryPct` | `0.35` |
| `trailingEnabled` | `false` |
| `tradeDirection` | `both` |

## Implementation Notes

All built-in strategies follow the QuantDinger `IndicatorStrategy` contract:

- declare `my_indicator_name` and `my_indicator_description`;
- copy the injected dataframe with `df = df.copy()`;
- write boolean `df['buy']` and `df['sell']` series with the same length as `df`;
- return an `output` dictionary with plot and signal data lists aligned to `df`;
- declare tunable values with `# @param`;
- declare default risk behavior with `# @strategy`.

