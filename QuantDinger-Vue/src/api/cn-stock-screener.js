import request from '@/utils/request'

const BASE_URL = '/api/cn-stock-screener'

export function runCNStockScreener (data) {
  return request({
    url: `${BASE_URL}/run`,
    method: 'post',
    data,
    timeout: 30000
  })
}

export function getCNStockScreenerJob (jobId) {
  return request({
    url: `${BASE_URL}/jobs/${jobId}`,
    method: 'get',
    timeout: 30000
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
