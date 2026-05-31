<template>
  <div class="cn-stock-screener">
    <div class="screener-header">
      <div>
        <h1>A股一键选股</h1>
        <p>从自选股和热门A股中筛选机会，规则因子先打分，Top股票再做AI深度分析。</p>
      </div>
      <a-space>
        <a-button icon="reload" :disabled="loading" @click="resetParams">重置</a-button>
        <a-button type="primary" icon="thunderbolt" :loading="loading" @click="runScreener">一键分析</a-button>
      </a-space>
    </div>

    <div class="toolbar-band">
      <a-form layout="inline" class="screener-form">
        <a-form-item label="周期">
          <a-select v-model="form.timeframe" class="w-96">
            <a-select-option value="1D">1D</a-select-option>
            <a-select-option value="1W">1W</a-select-option>
          </a-select>
        </a-form-item>
        <a-form-item label="候选数">
          <a-input-number v-model="form.candidate_limit" :min="5" :max="80" />
        </a-form-item>
        <a-form-item label="输出Top">
          <a-input-number v-model="form.top_n" :min="3" :max="30" />
        </a-form-item>
        <a-form-item label="AI分析Top">
          <a-input-number v-model="form.ai_top_n" :min="0" :max="10" />
        </a-form-item>
        <a-form-item label="反馈天数">
          <a-input-number v-model="form.strategy_feedback_days" :min="1" :max="365" />
        </a-form-item>
      </a-form>
    </div>

    <div class="summary-strip">
      <div class="metric">
        <span>候选</span>
        <strong>{{ summary.candidate_count || 0 }}</strong>
      </div>
      <div class="metric">
        <span>已评分</span>
        <strong>{{ summary.scored_count || 0 }}</strong>
      </div>
      <div class="metric">
        <span>AI分析</span>
        <strong>{{ summary.ai_analyzed_count || 0 }}</strong>
      </div>
      <div class="metric">
        <span>可模拟</span>
        <strong>{{ executableItems.length }}</strong>
      </div>
      <a-alert
        v-if="summary.skipped_count"
        class="summary-alert"
        type="warning"
        show-icon
        :message="`${summary.skipped_count} 只股票因数据不足或源异常被跳过`"
      />
    </div>

    <div class="action-bar">
      <a-space>
        <a-button
          type="primary"
          icon="deployment-unit"
          :disabled="selectedExecutable.length === 0"
          :loading="creating"
          @click="createStrategies(false)"
        >
          创建模拟策略
        </a-button>
        <a-button
          icon="play-circle"
          :disabled="selectedExecutable.length === 0"
          :loading="creating"
          @click="createStrategies(true)"
        >
          创建并启动
        </a-button>
      </a-space>
      <span class="selection-note">已选 {{ selectedExecutable.length }} / {{ executableItems.length }} 只可模拟股票</span>
    </div>

    <a-table
      row-key="symbol"
      class="screener-table"
      :columns="columns"
      :data-source="items"
      :loading="loading"
      :pagination="{ pageSize: 10, showSizeChanger: true }"
      :row-selection="rowSelection"
      :scroll="{ x: 1180 }"
    >
      <template slot="stock" slot-scope="text, record">
        <div class="stock-cell">
          <strong>{{ record.symbol }}</strong>
          <span>{{ record.name }}</span>
        </div>
      </template>
      <template slot="score" slot-scope="score">
        <a-progress :percent="Math.round(score || 0)" size="small" :status="score >= 72 ? 'success' : 'normal'" />
      </template>
      <template slot="aiDecision" slot-scope="text, record">
        <a-tag :color="decisionColor(record.ai_decision)">{{ record.ai_decision || '未分析' }}</a-tag>
        <span v-if="record.confidence !== null && record.confidence !== undefined" class="confidence">{{ record.confidence }}%</span>
      </template>
      <template slot="decision" slot-scope="text">
        <a-tag :color="actionColor(text)">{{ actionText(text) }}</a-tag>
      </template>
      <template slot="riskTags" slot-scope="tags">
        <template v-if="tags && tags.length">
          <a-tag v-for="tag in tags" :key="tag" color="orange">{{ riskText(tag) }}</a-tag>
        </template>
        <span v-else class="muted">无</span>
      </template>
      <template slot="actions" slot-scope="text, record">
        <a-button type="link" size="small" @click="openDetail(record)">详情</a-button>
      </template>
    </a-table>

    <a-empty v-if="!loading && items.length === 0" class="empty-state" description="暂无分析结果，点击一键分析开始筛选" />

    <a-drawer
      width="520"
      :visible="detailVisible"
      :title="detailTitle"
      @close="detailVisible = false"
    >
      <template v-if="activeItem">
        <a-descriptions size="small" bordered :column="1">
          <a-descriptions-item label="综合评分">{{ activeItem.score }}</a-descriptions-item>
          <a-descriptions-item label="规则评分">{{ activeItem.rule_score }}</a-descriptions-item>
          <a-descriptions-item label="AI评分">{{ activeItem.ai_score || '--' }}</a-descriptions-item>
          <a-descriptions-item label="建议">{{ actionText(activeItem.decision) }}</a-descriptions-item>
        </a-descriptions>

        <h3 class="drawer-section">因子拆分</h3>
        <a-list size="small" :data-source="activeItem.factor_breakdown || []">
          <a-list-item slot="renderItem" slot-scope="factor">
            <div class="factor-row">
              <span>{{ factorName(factor.key) }}</span>
              <strong :class="factor.score >= 0 ? 'positive' : 'negative'">{{ factor.score }}</strong>
            </div>
            <div class="factor-reason">{{ factor.reason }}</div>
          </a-list-item>
        </a-list>

        <h3 class="drawer-section">K线摘要</h3>
        <div class="kv-grid">
          <span>当前价</span><strong>{{ klineValue('current_price') }}</strong>
          <span>20日收益</span><strong>{{ klineValue('return_20d_pct') }}%</strong>
          <span>60日收益</span><strong>{{ klineValue('return_60d_pct') }}%</strong>
          <span>20日波动</span><strong>{{ klineValue('volatility_20d_pct') }}%</strong>
          <span>60日回撤</span><strong>{{ klineValue('drawdown_60d_pct') }}%</strong>
        </div>

        <h3 class="drawer-section">AI结论</h3>
        <p class="ai-summary">{{ activeItem.ai_summary || '未对该股票执行AI深度分析。' }}</p>
        <ul v-if="activeItem.ai_reasons && activeItem.ai_reasons.length" class="reason-list">
          <li v-for="reason in activeItem.ai_reasons" :key="reason">{{ reason }}</li>
        </ul>

        <h3 class="drawer-section">策略反馈</h3>
        <div class="kv-grid">
          <span>盈亏</span><strong>{{ feedbackValue('pnl') }}</strong>
          <span>胜率</span><strong>{{ feedbackValue('win_rate') }}%</strong>
          <span>成交</span><strong>{{ feedbackValue('trade_count') }}</strong>
          <span>持仓</span><strong>{{ feedbackValue('open_position') }}</strong>
          <span>异常</span><strong>{{ feedbackValue('recent_errors') }}</strong>
        </div>
      </template>
    </a-drawer>
  </div>
</template>

<script>
import { runCNStockScreener, createCNStockPaperStrategies } from '@/api/cn-stock-screener'

export default {
  name: 'CNStockScreener',
  data () {
    return {
      loading: false,
      creating: false,
      items: [],
      summary: {},
      selectedRowKeys: [],
      activeItem: null,
      detailVisible: false,
      form: {
        timeframe: '1D',
        candidate_limit: 80,
        top_n: 10,
        ai_top_n: 5,
        strategy_feedback_days: 30
      }
    }
  },
  computed: {
    columns () {
      return [
        { title: '股票', key: 'stock', scopedSlots: { customRender: 'stock' }, width: 170, fixed: 'left' },
        { title: '综合分', dataIndex: 'score', key: 'score', scopedSlots: { customRender: 'score' }, width: 150, sorter: (a, b) => (a.score || 0) - (b.score || 0) },
        { title: '规则分', dataIndex: 'rule_score', key: 'rule_score', width: 90, sorter: (a, b) => (a.rule_score || 0) - (b.rule_score || 0) },
        { title: 'AI结论', key: 'aiDecision', scopedSlots: { customRender: 'aiDecision' }, width: 140 },
        { title: '反馈分', dataIndex: 'feedback_score', key: 'feedback_score', width: 90 },
        { title: '建议', dataIndex: 'decision', key: 'decision', scopedSlots: { customRender: 'decision' }, width: 110 },
        { title: '风险', dataIndex: 'risk_tags', key: 'risk_tags', scopedSlots: { customRender: 'riskTags' }, width: 220 },
        { title: '来源', dataIndex: 'source', key: 'source', width: 90 },
        { title: '操作', key: 'actions', scopedSlots: { customRender: 'actions' }, width: 90, fixed: 'right' }
      ]
    },
    executableItems () {
      return this.items.filter(item => item.decision === 'paper_trade')
    },
    selectedExecutable () {
      const keys = new Set(this.selectedRowKeys)
      return this.executableItems.filter(item => keys.has(item.symbol))
    },
    rowSelection () {
      return {
        selectedRowKeys: this.selectedRowKeys,
        getCheckboxProps: record => ({
          props: { disabled: record.decision !== 'paper_trade' }
        }),
        onChange: keys => {
          this.selectedRowKeys = keys
        }
      }
    },
    detailTitle () {
      if (!this.activeItem) return '详情'
      return `${this.activeItem.symbol} ${this.activeItem.name || ''}`
    }
  },
  methods: {
    resetParams () {
      this.form = {
        timeframe: '1D',
        candidate_limit: 80,
        top_n: 10,
        ai_top_n: 5,
        strategy_feedback_days: 30
      }
    },
    async runScreener () {
      this.loading = true
      this.selectedRowKeys = []
      try {
        const res = await runCNStockScreener({ ...this.form })
        if (res.code === 1) {
          const data = res.data || {}
          this.items = data.items || []
          this.summary = data
          this.selectedRowKeys = this.items.filter(item => item.decision === 'paper_trade').map(item => item.symbol)
          if (!this.items.length) this.$message.warning('没有筛选出可展示结果')
        } else {
          this.$message.error(res.msg || '分析失败')
        }
      } catch (e) {
        this.$message.error(e.message || '分析失败')
      } finally {
        this.loading = false
      }
    },
    async createStrategies (startImmediately) {
      if (!this.selectedExecutable.length) return
      this.creating = true
      try {
        const res = await createCNStockPaperStrategies({
          items: this.selectedExecutable,
          strategy_name: 'A股选股模拟策略',
          initial_capital: 10000,
          decide_interval: 300,
          start_immediately: startImmediately
        })
        if (res.code === 1) {
          const data = res.data || {}
          const count = (data.created_ids || []).length
          const started = (data.started_ids || []).length
          this.$notification.success({
            message: '模拟策略已创建',
            description: startImmediately ? `创建 ${count} 个，启动 ${started} 个。` : `创建 ${count} 个。`
          })
        } else {
          this.$message.error(res.msg || '创建失败')
        }
      } catch (e) {
        this.$message.error(e.message || '创建失败')
      } finally {
        this.creating = false
      }
    },
    openDetail (record) {
      this.activeItem = record
      this.detailVisible = true
    },
    decisionColor (decision) {
      return { BUY: 'green', HOLD: 'blue', SELL: 'red' }[decision] || 'default'
    },
    actionColor (decision) {
      return { paper_trade: 'green', watch: 'blue', skip: 'default' }[decision] || 'default'
    },
    actionText (decision) {
      return { paper_trade: '模拟执盘', watch: '关注', skip: '跳过' }[decision] || decision || '--'
    },
    riskText (tag) {
      const map = {
        high_volatility: '高波动',
        deep_drawdown: '回撤较深',
        volume_overheated: '放量过热',
        strategy_error_feedback: '策略异常',
        ai_analysis_failed: 'AI失败'
      }
      return map[tag] || tag
    },
    factorName (key) {
      const map = {
        momentum: '动量',
        trend: '趋势',
        volatility: '波动',
        volume: '成交',
        drawdown: '回撤',
        strategy_feedback: '策略反馈'
      }
      return map[key] || key
    },
    klineValue (key) {
      const v = ((this.activeItem || {}).kline_summary || {})[key]
      return v === null || v === undefined ? '--' : v
    },
    feedbackValue (key) {
      const v = ((this.activeItem || {}).strategy_feedback || {})[key]
      return v === null || v === undefined ? '--' : v
    }
  }
}
</script>

<style lang="less" scoped>
.cn-stock-screener {
  padding: 20px;
  min-height: 100%;
  background: #f5f7fb;
}

.screener-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;

  h1 {
    margin: 0 0 6px;
    font-size: 24px;
    line-height: 32px;
    font-weight: 650;
    color: #172033;
  }

  p {
    margin: 0;
    color: #6b7280;
  }
}

.toolbar-band,
.summary-strip,
.action-bar,
.screener-table {
  background: #fff;
  border: 1px solid #e6ebf2;
}

.toolbar-band {
  padding: 14px 16px 2px;
  margin-bottom: 12px;
}

.screener-form {
  display: flex;
  flex-wrap: wrap;
  gap: 0 8px;
}

.w-96 {
  width: 96px;
}

.summary-strip {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  margin-bottom: 12px;
}

.metric {
  min-width: 96px;
  display: flex;
  flex-direction: column;

  span {
    font-size: 12px;
    color: #6b7280;
  }

  strong {
    font-size: 22px;
    line-height: 28px;
    color: #172033;
  }
}

.summary-alert {
  flex: 1;
  min-width: 240px;
}

.action-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 0;
}

.selection-note,
.muted,
.confidence {
  color: #6b7280;
}

.stock-cell {
  display: flex;
  flex-direction: column;
  min-width: 0;

  strong {
    color: #172033;
  }

  span {
    color: #6b7280;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
}

.empty-state {
  margin-top: 24px;
  padding: 32px;
  background: #fff;
  border: 1px solid #e6ebf2;
}

.drawer-section {
  margin: 22px 0 10px;
  font-size: 15px;
  font-weight: 650;
}

.factor-row {
  display: flex;
  justify-content: space-between;
  width: 100%;
}

.factor-reason {
  width: 100%;
  color: #6b7280;
  font-size: 12px;
}

.positive {
  color: #0f9f6e;
}

.negative {
  color: #d93025;
}

.kv-grid {
  display: grid;
  grid-template-columns: minmax(90px, 1fr) minmax(120px, 1.4fr);
  gap: 8px 12px;

  span {
    color: #6b7280;
  }
}

.ai-summary {
  color: #374151;
  line-height: 1.7;
}

.reason-list {
  padding-left: 18px;
  color: #374151;
}

@media (max-width: 768px) {
  .cn-stock-screener {
    padding: 12px;
  }

  .screener-header,
  .action-bar,
  .summary-strip {
    align-items: stretch;
    flex-direction: column;
  }

  .metric {
    min-width: 0;
  }
}
</style>
