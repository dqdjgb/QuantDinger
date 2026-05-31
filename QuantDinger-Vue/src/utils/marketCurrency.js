export const MARKET_CATEGORY_ENUM = Object.freeze({
  CRYPTO: 'Crypto',
  CN_STOCK: 'CNStock',
  HK_STOCK: 'HKStock',
  US_STOCK: 'USStock',
  FOREX: 'Forex',
  FUTURES: 'Futures'
})

export const MARKET_CURRENCY_ENUM = Object.freeze({
  CNY: { code: 'CNY', symbol: '\u00A5', locale: 'zh-CN', suffix: false },
  HKD: { code: 'HKD', symbol: 'HK$', locale: 'zh-HK', suffix: false },
  USD: { code: 'USD', symbol: '$', locale: 'en-US', suffix: false },
  USDT: { code: 'USDT', symbol: 'USDT', locale: 'en-US', suffix: true }
})

export const MARKET_CURRENCY_BY_CATEGORY = Object.freeze({
  [MARKET_CATEGORY_ENUM.CN_STOCK]: MARKET_CURRENCY_ENUM.CNY,
  [MARKET_CATEGORY_ENUM.FUTURES]: MARKET_CURRENCY_ENUM.CNY,
  [MARKET_CATEGORY_ENUM.HK_STOCK]: MARKET_CURRENCY_ENUM.HKD,
  [MARKET_CATEGORY_ENUM.US_STOCK]: MARKET_CURRENCY_ENUM.USD,
  [MARKET_CATEGORY_ENUM.FOREX]: MARKET_CURRENCY_ENUM.USD,
  [MARKET_CATEGORY_ENUM.CRYPTO]: MARKET_CURRENCY_ENUM.USDT
})

export const ACCOUNT_CURRENCY_BY_MARKET_TYPE = Object.freeze({
  spot: MARKET_CURRENCY_ENUM.USDT,
  swap: MARKET_CURRENCY_ENUM.USDT,
  futures: MARKET_CURRENCY_ENUM.USDT,
  future: MARKET_CURRENCY_ENUM.USDT,
  perp: MARKET_CURRENCY_ENUM.USDT,
  perpetual: MARKET_CURRENCY_ENUM.USDT
})

export function normalizeMarketCategory (marketCategory) {
  const key = String(marketCategory || '').trim()
  return Object.values(MARKET_CATEGORY_ENUM).includes(key) ? key : MARKET_CATEGORY_ENUM.CRYPTO
}

export function getMarketCurrency (marketCategory) {
  return MARKET_CURRENCY_BY_CATEGORY[normalizeMarketCategory(marketCategory)] || MARKET_CURRENCY_ENUM.USD
}

export function getAccountCurrency (marketCategory, marketType) {
  const mt = String(marketType || '').trim().toLowerCase()
  return ACCOUNT_CURRENCY_BY_MARKET_TYPE[mt] || getMarketCurrency(marketCategory)
}

export function getMarketCurrencyLabel (marketCategory, marketType) {
  return getAccountCurrency(marketCategory, marketType).code
}

export function formatMarketMoney (value, marketCategory, options = {}) {
  if (value === null || value === undefined || value === '') return options.fallback || '--'

  const num = typeof value === 'number' ? value : parseFloat(value)
  if (!Number.isFinite(num)) return options.fallback || '--'

  const currency = options.currency || (options.accountCurrency
    ? getAccountCurrency(marketCategory, options.marketType)
    : getMarketCurrency(marketCategory))
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
