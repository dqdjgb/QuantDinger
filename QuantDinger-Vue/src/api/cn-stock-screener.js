import request from '@/utils/request'

const BASE_URL = '/api/cn-stock-screener'

export function runCNStockScreener (data) {
  return request({
    url: `${BASE_URL}/run`,
    method: 'post',
    data,
    timeout: 300000
  })
}

export function createCNStockPaperStrategies (data) {
  return request({
    url: `${BASE_URL}/create-paper-strategies`,
    method: 'post',
    data,
    timeout: 120000
  })
}
