<template>
  <div class="page-container">
    <!-- 加载状态 -->
    <div v-if="loading" class="loading-state">
      <el-skeleton :rows="6" animated />
    </div>

    <!-- 错误状态 -->
    <div v-else-if="error" class="error-state">
      <el-result icon="error" :title="error">
        <template #extra>
          <el-button @click="$router.push('/audit/report')">返回审核报告列表</el-button>
        </template>
      </el-result>
    </div>

    <template v-else>
      <!-- 页面导航 -->
      <div class="page-nav-bar">
        <el-button @click="$router.push('/audit/report')">
          <el-icon><ArrowLeft /></el-icon>返回审核报告列表
        </el-button>
        <el-button
          type="primary"
          size="small"
          @click="$router.push(`/audit/result/${contractId}`)"
        >
          查看审核结果
        </el-button>
      </div>

      <!-- 报告总览 -->
      <el-descriptions v-if="report" :column="4" border class="report-summary">
        <el-descriptions-item label="综合评分">
          <el-tag
            :type="report.risk_score >= 60 ? 'danger' : report.risk_score >= 30 ? 'warning' : 'success'"
            size="large"
          >{{ report.risk_score }} 分</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="高风险">{{ report.high_risk_count }} 条</el-descriptions-item>
        <el-descriptions-item label="中风险">{{ report.mid_risk_count }} 条</el-descriptions-item>
        <el-descriptions-item label="低风险">{{ report.low_risk_count }} 条</el-descriptions-item>
      </el-descriptions>

      <!-- ECharts -->
      <el-row :gutter="20">
        <el-col :span="12">
          <el-card shadow="hover">
            <template #header><span>风险等级分布</span></template>
            <div ref="pieChartRef" class="chart-box"></div>
          </el-card>
        </el-col>
        <el-col :span="12">
          <el-card shadow="hover">
            <template #header><span>各类型风险数量</span></template>
            <div ref="barChartRef" class="chart-box"></div>
          </el-card>
        </el-col>
      </el-row>

      <!-- 风险明细表 -->
      <el-card shadow="hover" class="risk-table-card">
        <template #header><span>风险条款明细</span></template>
        <el-table
          v-if="riskItems.length > 0"
          :data="riskItems"
          stripe
          style="width: 100%"
          max-height="500"
        >
          <el-table-column prop="risk_type" label="风险编号" width="90" />
          <el-table-column label="风险等级" width="90">
            <template #default="{ row }">
              <el-tag :type="riskLevelTag(row.risk_level)" size="small">
                {{ levelLabel(row.risk_level) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="clause_text" label="涉及条款" min-width="250" show-overflow-tooltip />
          <el-table-column prop="reason" label="风险分析" min-width="200" show-overflow-tooltip />
          <el-table-column prop="suggestion" label="修改建议" min-width="200" show-overflow-tooltip>
            <template #default="{ row }">
              <span v-if="row.suggestion">{{ row.suggestion }}</span>
              <span v-else class="text-muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="置信度" width="100">
            <template #default="{ row }">
              <el-progress
                :percentage="Math.round((row.confidence || 0) * 100)"
                :status="row.confidence >= 0.7 ? 'success' : 'warning'"
                :stroke-width="8"
                :show-text="true"
              />
            </template>
          </el-table-column>
        </el-table>
        <div v-else class="no-risks">
          <el-icon :size="32"><SuccessFilled /></el-icon>
          <p>本次审核未发现风险条款</p>
          <span class="text-muted">AI 审核结果仅供参考，建议进行人工复核</span>
        </div>
      </el-card>

      <!-- PDF 预览 -->
      <el-card shadow="hover" class="pdf-card">
        <template #header>
          <div class="pdf-header">
            <span>合同原文预览</span>
            <div v-if="!pdfError && totalPages > 0" class="pdf-controls">
              <el-button-group>
                <el-button size="small" :disabled="currentPage <= 1" @click="goToPage(currentPage - 1)">
                  <el-icon><ArrowLeft /></el-icon>
                </el-button>
                <el-button size="small" disabled class="page-indicator">
                  {{ currentPage }} / {{ totalPages }}
                </el-button>
                <el-button size="small" :disabled="currentPage >= totalPages" @click="goToPage(currentPage + 1)">
                  <el-icon><ArrowRight /></el-icon>
                </el-button>
              </el-button-group>
              <el-input-number
                v-model="jumpPage"
                :min="1"
                :max="totalPages"
                size="small"
                controls-position="right"
                style="width: 120px; margin-left: 8px;"
                @change="goToPage(jumpPage)"
              />
            </div>
          </div>
        </template>
        <div class="pdf-preview">
          <div v-if="pdfLoading" class="pdf-loading">
            <el-icon class="is-loading" :size="32"><Loading /></el-icon>
            <p>正在加载合同原文...</p>
          </div>
          <canvas v-show="!pdfError && !pdfLoading" ref="pdfCanvasRef" class="pdf-canvas"></canvas>
          <div v-if="pdfError" class="pdf-error">
            <el-icon :size="40"><Warning /></el-icon>
            <p>PDF 加载失败：{{ pdfError }}</p>
          </div>
        </div>
      </el-card>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { Warning, ArrowLeft, ArrowRight, SuccessFilled, Loading } from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import * as pdfjsLib from 'pdfjs-dist'
import pdfjsWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { getAuditReport, getAuditResult, getContractFile } from '../api/contract.js'

const route = useRoute()
const contractId = computed(() => route.params.contractId || '')

const report = ref(null)
const riskItems = ref([])
const riskTypes = ref([])
const loading = ref(true)
const error = ref('')

// ── 风险等级映射 ──
const LEVEL_MAP = { high: '高风险', medium: '中风险', low: '低风险' }
function levelLabel(level) { return LEVEL_MAP[level] || level || '未知' }
function riskLevelTag(level) {
  if (level === 'high' || level === '高风险') return 'danger'
  if (level === 'medium' || level === '中风险') return 'warning'
  return 'success'
}

async function fetchReport() {
  const id = contractId.value
  if (!id) {
    error.value = '缺少合同 ID 参数'
    loading.value = false
    return
  }
  try {
    const [reportRes, resultRes] = await Promise.all([
      getAuditReport(id),
      getAuditResult(id),
    ])
    report.value = reportRes.data
    riskItems.value = resultRes.data?.items || []

    const countByType = {}
    riskItems.value.forEach(r => {
      countByType[r.risk_type] = (countByType[r.risk_type] || 0) + 1
    })
    riskTypes.value = Object.entries(countByType).map(([type, count]) => ({ type, count }))
  } catch (e) {
    error.value = '加载审核报告失败'
    console.warn('报告加载失败:', e)
  } finally {
    loading.value = false
  }
}

watch(contractId, async (newId, oldId) => {
  if (newId && newId !== oldId) {
    disposeCharts()
    pdfLoadingTask?.destroy()
    pdfLoadingTask = null
    pdfDoc = null
    loading.value = true
    error.value = ''
    report.value = null
    riskItems.value = []
    riskTypes.value = []
    await fetchReport()
    await nextTick()
    initCharts()
    renderPdf()
  }
})

// ── ECharts ──
const pieChartRef = ref(null)
const barChartRef = ref(null)
let pieChartInstance = null
let barChartInstance = null

function initCharts() {
  disposeCharts()
  if (!report.value) return

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
          { value: report.value.high_risk_count || 0, name: '高风险', itemStyle: { color: '#F56C6C' } },
          { value: report.value.mid_risk_count || 0, name: '中风险', itemStyle: { color: '#E6A23C' } },
          { value: report.value.low_risk_count || 0, name: '低风险', itemStyle: { color: '#67C23A' } },
        ],
      }],
    })
  }

  if (barChartRef.value) {
    barChartInstance = echarts.init(barChartRef.value)
    barChartInstance.setOption({
      tooltip: { trigger: 'axis' },
      xAxis: {
        type: 'category',
        data: riskTypes.value.map(t => t.type),
        axisLabel: { rotate: 45 },
      },
      yAxis: { type: 'value', name: '数量', minInterval: 1 },
      series: [{
        name: '风险数量',
        type: 'bar',
        data: riskTypes.value.map(t => t.count),
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

// ── PDF ──
const pdfCanvasRef = ref(null)
const currentPage = ref(1)
const totalPages = ref(0)
const jumpPage = ref(1)
const pdfError = ref('')
const pdfLoading = ref(false)
let pdfLoadingTask = null
let pdfDoc = null

async function renderPdf() {
  const id = contractId.value
  if (!id) return

  pdfjsLib.GlobalWorkerOptions.workerSrc = pdfjsWorker
  pdfLoading.value = true
  pdfError.value = ''
  currentPage.value = 1
  jumpPage.value = 1

  try {
    // Fetch the contract file as binary via auth'd API
    const arrayBuffer = await getContractFile(id)
    pdfLoadingTask = pdfjsLib.getDocument({ data: arrayBuffer.slice() })
    pdfDoc = await pdfLoadingTask.promise
    totalPages.value = pdfDoc.numPages
    await renderPage(1)
  } catch (e) {
    pdfError.value = e.message || 'PDF 加载失败'
    console.warn('PDF 加载失败:', e)
  } finally {
    pdfLoading.value = false
  }
}

async function renderPage(pageNum) {
  if (!pdfDoc) return
  const canvas = pdfCanvasRef.value
  if (!canvas) return

  try {
    const page = await pdfDoc.getPage(pageNum)
    currentPage.value = pageNum
    jumpPage.value = pageNum

    const viewport = page.getViewport({ scale: 1.0 })
    const maxWidth = 600
    const containerWidth = Math.min(canvas.parentElement.clientWidth - 2, maxWidth)
    const scale = containerWidth / viewport.width
    const scaledViewport = page.getViewport({ scale })

    canvas.width = scaledViewport.width
    canvas.height = scaledViewport.height

    const ctx = canvas.getContext('2d')
    await page.render({ canvasContext: ctx, viewport: scaledViewport }).promise
  } catch (e) {
    console.warn('PDF 页面渲染失败:', e)
  }
}

function goToPage(pageNum) {
  if (pageNum < 1 || pageNum > totalPages.value) return
  renderPage(pageNum)
}

onMounted(async () => {
  await fetchReport()
  await nextTick()
  initCharts()
  renderPdf()
})

onUnmounted(() => {
  pdfLoadingTask?.destroy()
  disposeCharts()
})
</script>

<style scoped>
.page-container {
  padding: 24px;
  max-width: 1200px;
  margin: 0 auto;
}

.loading-state, .error-state {
  padding: 60px 0;
}

.page-nav-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.report-summary {
  margin-bottom: 20px;
}

.chart-box {
  width: 100%;
  height: 350px;
}

.risk-table-card {
  margin-top: 20px;
}

.no-risks {
  text-align: center;
  padding: 40px 20px;
  color: #67c23a;
}

.text-muted {
  color: #909399;
  font-size: 13px;
}

.pdf-card {
  margin-top: 20px;
}

.pdf-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.pdf-controls {
  display: flex;
  align-items: center;
}

.page-indicator {
  min-width: 80px;
  text-align: center;
}

.pdf-preview {
  text-align: center;
}

.pdf-loading {
  padding: 50px;
  color: #909399;
}

.pdf-canvas {
  border: 1px solid #ddd;
  max-width: 100%;
}

.pdf-error {
  padding: 50px;
  color: #999;
}
</style>
