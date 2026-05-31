const MARKET_CURRENCIES = {
  CNStock: { symbol: '¥', locale: 'zh-CN', suffix: false },
  Futures: { symbol: '¥', locale: 'zh-CN', suffix: false },
  HKStock: { symbol: 'HK$', locale: 'zh-HK', suffix: false },
  USStock: { symbol: '$', locale: 'en-US', suffix: false },
  Forex: { symbol: '$', locale: 'en-US', suffix: false },
  Crypto: { symbol: 'USDT', locale: 'en-US', suffix: true }
}

export function getMarketCurrency (marketCategory) {
  const key = String(marketCategory || 'Crypto').trim()
  return MARKET_CURRENCIES[key] || MARKET_CURRENCIES.USStock
}

export function formatMarketMoney (value, marketCategory, options = {}) {
  if (value === null || value === undefined || value === '') return options.fallback || '--'

  const num = typeof value === 'number' ? value : parseFloat(value)
  if (!Number.isFinite(num)) return options.fallback || '--'

  const currency = getMarketCurrency(marketCategory)
  const minimumFractionDigits = options.minimumFractionDigits != null ? options.minimumFractionDigits : 2
  const maximumFractionDigits = options.maximumFractionDigits != null ? options.maximumFractionDigits : minimumFractionDigits
  const sign = options.signed && num > 0 ? '+' : (num < 0 ? '-' : '')
  const amount = Math.abs(num).toLocaleString(currency.locale, {
    minimumFractionDigits,
    maximumFractionDigits
  })

  return currency.suffix
    ? `${sign}${amount} ${currency.symbol}`
    : `${sign}${currency.symbol}${amount}`
}
