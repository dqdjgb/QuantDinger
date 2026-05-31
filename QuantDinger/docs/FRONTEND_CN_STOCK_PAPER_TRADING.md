# QuantDinger-Vue: CNStock Paper Trading UI

The backend for v3.1.0 exposes CNStock paper-trading rules through:

```http
GET /api/policy/broker-market
```

Use these response fields instead of hard-coding the UI matrix:

```json
{
  "paper_market_categories": ["CNStock", "Crypto", "Forex", "USStock"],
  "paper_market_rules": {
    "CNStock": {
      "execution_mode": "paper",
      "market_type": "spot",
      "trade_direction": "long",
      "lot_size": 100,
      "t_plus_1": true,
      "live_supported": false
    }
  }
}
```

## Required UI Behavior

- In strategy create/edit forms, add `Paper` / `模拟盘` as an execution-mode option.
- When `market_category === 'CNStock'`:
  - default `execution_mode` to `paper`
  - disable or hide `live`
  - hide broker credential selection
  - force `trading_config.market_type = 'spot'`
  - force `trading_config.trade_direction = 'long'`
  - show a small rule hint: long-only, 100-share board lots, T+1 sells, no real broker execution
- Strategy detail pages can keep using:
  - `GET /api/strategies/positions?id=<strategy_id>`
  - `GET /api/strategies/trades?id=<strategy_id>`
  - strategy runtime logs
- Label CNStock paper positions/trades as simulated, especially where the UI currently says live/executed.

## Suggested Patch Shape

In `QuantDinger-Vue-src/src/views/trading-assistant/index.vue` and any shared strategy wizard component:

```js
const policy = await getBrokerMarketPolicy()
const paperRules = policy.paper_market_rules || {}

function applyMarketExecutionDefaults(form) {
  if (form.market_category === 'CNStock') {
    const rule = paperRules.CNStock || {}
    form.execution_mode = rule.execution_mode || 'paper'
    form.trading_config = {
      ...(form.trading_config || {}),
      market_type: rule.market_type || 'spot',
      trade_direction: rule.trade_direction || 'long'
    }
  }
}

function isExecutionModeDisabled(mode, form) {
  if (form.market_category === 'CNStock' && mode === 'live') return true
  return false
}
```

Payload sent to the backend:

```json
{
  "market_category": "CNStock",
  "execution_mode": "paper",
  "trading_config": {
    "symbol": "600519",
    "timeframe": "1D",
    "market_type": "spot",
    "trade_direction": "long"
  }
}
```

## Version Management

- Frontend `package.json`, visible footer/version labels, and release tag should be bumped to `3.1.0`.
- Build and publish the frontend image as `ghcr.io/brokermr810/quantdinger-frontend:v3.1.0`.
- Backend and frontend can then be pinned together with `IMAGE_TAG=v3.1.0`.
