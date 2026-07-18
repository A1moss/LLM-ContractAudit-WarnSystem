<template>
  <div class="page-container">
    <h3>审核报告</h3>
    <el-divider />

    <!-- 加载状态 -->
    <div v-if="loading" class="loading-state">
      <el-skeleton :rows="6" animated />
    </div>

    <!-- 错误状态 -->
    <div v-else-if="error" class="error-state">
      <el-result icon="error" :title="error">
        <template #extra>
          <el-button @click="fetchList">重试</el-button>
        </template>
      </el-result>
    </div>

    <!-- 空状态 -->
    <div v-else-if="contracts.length === 0" class="empty-state">
      <el-empty description="暂无已完成审核的合同">
        <el-button type="primary" @click="$router.push('/contracts/upload')">上传合同</el-button>
      </el-empty>
    </div>

    <!-- 合同报告列表 + 右侧报告详情 -->
    <template v-else>
      <el-row :gutter="20">
        <!-- 左侧：合同列表 -->
        <el-col :span="selectedContract ? 8 : 24">
          <el-table
            :data="contracts"
            stripe
            border
            highlight-current-row
            @current-change="handleSelect"
          >
            <el-table-column prop="file_name" label="合同名称" min-width="180">
              <template #default="{ row }">
                <el-link type="primary" :underline="false" @click="$router.push(`/audit/report/${row.id}`)">{{ row.file_name }}</el-link>
              </template>
            </el-table-column>
            <el-table-column prop="contract_type" label="类型" width="120">
              <template #default="{ row }">{{ typeLabel(row.contract_type) }}</template>
            </el-table-column>
            <el-table-column label="审核时间" width="160">
              <template #default="{ row }">{{ formatTime(row.updated_at) }}</template>
            </el-table-column>
            <el-table-column label="风险评分" width="100" align="center">
              <template #default="{ row }">
                <el-tag
                  :type="row._score >= 60 ? 'danger' : row._score >= 30 ? 'warning' : 'success'"
                  size="small"
                >{{ row._score ?? '—' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="100">
              <template #default="{ row }">
                <el-button size="small" @click="$router.push(`/audit/report/${row.id}`)">
                  查看报告
                </el-button>
              </template>
            </el-table-column>
          </el-table>

          <div class="pagination-wrapper">
            <el-pagination
              v-model:current-page="pagination.page"
              v-model:page-size="pagination.page_size"
              :page-sizes="[10, 20, 50]"
              :total="pagination.total"
              layout="total, sizes, prev, pager, next, jumper"
              @current-change="(p) => { clearReport(); fetchList(p); }"
              @size-change="() => { clearReport(); fetchList(1); }"
            />
          </div>
        </el-col>

        <!-- 右侧：报告详情 -->
        <el-col v-if="selectedContract" :span="16">
          <el-card shadow="hover">
            <template #header>
              <div class="report-detail-header">
                <span>{{ selectedContract.file_name }} — 审核报告</span>
                <div class="report-detail-actions">
                  <el-button size="small" @click="$router.push(`/audit/report/${selectedContract.id}`)">
                    全屏查看
                  </el-button>
                  <el-button size="small" type="primary" @click="$router.push(`/contracts/${selectedContract.id}`)">
                    合同详情
                  </el-button>
                </div>
              </div>
            </template>

            <div v-if="reportLoading" class="report-detail-loading">
              <el-skeleton :rows="6" animated />
            </div>

            <template v-else-if="reportData">
              <!-- 综合评分 -->
              <el-descriptions :column="4" border size="small" class="report-summary">
                <el-descriptions-item label="综合评分">
                  <el-tag
                    :type="reportData.risk_score >= 60 ? 'danger' : reportData.risk_score >= 30 ? 'warning' : 'success'"
                    size="large"
                  >{{ reportData.risk_score }} 分</el-tag>
                </el-descriptions-item>
                <el-descriptions-item label="高风险">{{ reportData.high_risk_count }} 条</el-descriptions-item>
                <el-descriptions-item label="中风险">{{ reportData.mid_risk_count }} 条</el-descriptions-item>
                <el-descriptions-item label="低风险">{{ reportData.low_risk_count }} 条</el-descriptions-item>
              </el-descriptions>

              <!-- 图表区 -->
              <el-row :gutter="20" class="chart-row">
                <el-col :span="12">
                  <div ref="pieChartRef" class="chart-box"></div>
                </el-col>
                <el-col :span="12">
                  <div ref="barChartRef" class="chart-box"></div>
                </el-col>
              </el-row>

              <!-- 风险明细列表 -->
              <h4 class="detail-subtitle">风险明细</h4>
              <el-table :data="riskItems" size="small" border max-height="300">
                <el-table-column prop="level" label="等级" width="80">
                  <template #default="{ row: r }">
                    <el-tag
                      :type="r.level === '高风险' ? 'danger' : r.level === '中风险' ? 'warning' : 'success'"
                      size="small"
                    >{{ r.level }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="type" label="风险类型" width="120" />
                <el-table-column prop="clause" label="涉及条款" min-width="180" show-overflow-tooltip />
                <el-table-column prop="suggestion" label="建议" min-width="180" show-overflow-tooltip />
              </el-table>
            </template>
          </el-card>
        </el-col>
      </el-row>
    </template>
  </div>
</template>

<script setup>
import { ref, reactive, nextTick, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import * as echarts from 'echarts'
import { getContractList, getAuditResult, getAuditReport } from '../api/contract.js'

// ── 列表状态 ──
const contracts = ref([])
const loading = ref(true)
const error = ref('')
const pagination = reactive({
  page: 1,
  page_size: 10,
  total: 0,
})

// ── 选中合同 & 报告详情 ──
const selectedContract = ref(null)
const reportData = ref(null)
const riskItems = ref([])
const reportLoading = ref(false)

// ── 图表 ──
const pieChartRef = ref(null)
const barChartRef = ref(null)
let pieChartInstance = null
let barChartInstance = null
let selectSeq = 0  // 防止 handleSelect 竞态

// ── 工具函数 ──
const typeMap = {
  purchase: '采购合同', sales: '销售合同', nda: '保密协议 (NDA)',
  outsourcing: '服务外包合同', employment: '劳动合同', other: '其他合同',
}
function typeLabel(type) { return typeMap[type] || type || '未分类' }
function formatTime(iso) {
  if (!iso) return '—'
  return iso.replace('T', ' ').slice(0, 19)
}

// ── 获取列表 ──
async function fetchList(page = pagination.page) {
  loading.value = true
  error.value = ''
  pagination.page = page
  try {
    const res = await getContractList({
      page,
      page_size: pagination.page_size,
      status: 'completed',
    })
    const items = res.data?.items || []
    pagination.total = res.data?.total || 0

    // 为每个合同预取评分
    const enriched = await Promise.all(
      items.map(async (c) => {
        try {
          const reportRes = await getAuditReport(c.id)
          return { ...c, _score: reportRes.data?.risk_score ?? 0 }
        } catch {
          return { ...c, _score: 0 }
        }
      })
    )
    contracts.value = enriched
  } catch (e) {
    error.value = '加载审核报告列表失败'
    console.warn('审核报告列表加载失败:', e)
  } finally {
    loading.value = false
  }
}

// ── 清除报告详情 ──
function clearReport() {
  selectedContract.value = null
  reportData.value = null
  riskItems.value = []
  disposeCharts()
}

// ── 选择合同 → 加载报告详情 ──
const levelMap = { high: '高风险', medium: '中风险', low: '低风险' }

async function handleSelect(row) {
  if (!row) return
  const seq = ++selectSeq
  selectedContract.value = row
  reportLoading.value = true
  reportData.value = null
  riskItems.value = []

  try {
    const [reportRes, resultRes] = await Promise.all([
      getAuditReport(row.id),
      getAuditResult(row.id),
    ])
    // 只保留最后一次点击的结果
    if (seq !== selectSeq) return
    reportData.value = reportRes.data
    riskItems.value = (resultRes.data?.items || []).map(r => ({
      level: levelMap[r.risk_level] || r.risk_level,
      type: r.risk_type,
      clause: r.clause_text,
      suggestion: r.suggestion,
    }))

    await nextTick()
    initCharts()
  } catch (e) {
    if (seq !== selectSeq) return
    console.warn('报告详情加载失败:', e)
    ElMessage.error('加载报告详情失败')
  } finally {
    if (seq === selectSeq) {
      reportLoading.value = false
    }
  }
}

// ── ECharts ──
function initCharts() {
  disposeCharts()
  if (!reportData.value) return

  // 饼图
  if (pieChartRef.value) {
    pieChartInstance = echarts.init(pieChartRef.value)
    pieChartInstance.setOption({
      tooltip: { trigger: 'item' },
      legend: { bottom: '0%' },
      series: [{
        name: '风险等级',
        type: 'pie',
        radius: ['40%', '70%'],
        label: { show: true, formatter: '{b}: {c} 条' },
        data: [
          { value: reportData.value.high_risk_count || 0, name: '高风险', itemStyle: { color: '#F56C6C' } },
          { value: reportData.value.mid_risk_count || 0, name: '中风险', itemStyle: { color: '#E6A23C' } },
          { value: reportData.value.low_risk_count || 0, name: '低风险', itemStyle: { color: '#67C23A' } },
        ],
      }],
    })
  }

  // 柱状图
  if (barChartRef.value) {
    const countByType = {}
    riskItems.value.forEach(r => {
      countByType[r.type] = (countByType[r.type] || 0) + 1
    })
    const types = Object.entries(countByType)

    barChartInstance = echarts.init(barChartRef.value)
    barChartInstance.setOption({
      tooltip: { trigger: 'axis' },
      xAxis: {
        type: 'category',
        data: types.map(t => t[0]),
        axisLabel: { rotate: 45 },
      },
      yAxis: { type: 'value', name: '数量', minInterval: 1 },
      series: [{
        name: '风险数量',
        type: 'bar',
        data: types.map(t => t[1]),
        itemStyle: { color: '#409EFF' },
        barWidth: '50%',
      }],
    })
  }
}

function disposeCharts() {
  pieChartInstance?.dispose()
  pieChartInstance = null
  barChartInstance?.dispose()
  barChartInstance = null
}

onMounted(() => fetchList())

onUnmounted(() => disposeCharts())
</script>

<style scoped>
.page-container {
  padding: 24px;
  max-width: 1200px;
  margin: 0 auto;
}

.loading-state, .error-state, .empty-state {
  padding: 60px 0;
}

.pagination-wrapper {
  margin-top: 20px;
  display: flex;
  justify-content: flex-end;
}

.report-detail-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.report-detail-actions {
  display: flex;
  gap: 8px;
}

.report-detail-loading {
  padding: 20px 0;
}

.report-summary {
  margin-bottom: 16px;
}

.chart-row {
  margin-bottom: 20px;
}

.chart-box {
  width: 100%;
  height: 300px;
}

.detail-subtitle {
  margin: 16px 0 8px;
  font-size: 15px;
  color: #303133;
}
</style>
