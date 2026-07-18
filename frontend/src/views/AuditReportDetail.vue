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

      <!-- PDF 预览 -->
      <el-card shadow="hover" class="pdf-card">
        <template #header><span>合同原文预览</span></template>
        <div class="pdf-preview">
          <canvas v-if="!pdfError" ref="pdfCanvasRef" class="pdf-canvas"></canvas>
          <div v-if="pdfError" class="pdf-error">
            <el-icon :size="40"><Warning /></el-icon>
            <p>PDF 加载失败：{{ pdfError }}</p>
          </div>
          <div class="pdf-footer">第 1 页 / 共 {{ totalPages || '...' }} 页</div>
        </div>
      </el-card>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { Warning, ArrowLeft } from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import * as pdfjsLib from 'pdfjs-dist'
import pdfjsWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { getAuditReport, getAuditResult } from '../api/contract.js'

const route = useRoute()
const contractId = computed(() => route.params.contractId || '')

const report = ref(null)
const riskTypes = ref([])
const loading = ref(true)
const error = ref('')

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
    const countByType = {}
    ;(resultRes.data?.items || []).forEach(r => {
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
    loading.value = true
    error.value = ''
    report.value = null
    riskTypes.value = []
    await fetchReport()
    await nextTick()
    initCharts()
    await renderPdf()
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
const totalPages = ref(0)
const pdfError = ref('')
let pdfLoadingTask = null

async function renderPdf() {
  pdfjsLib.GlobalWorkerOptions.workerSrc = pdfjsWorker
  try {
    pdfLoadingTask = pdfjsLib.getDocument({ url: '/test.pdf' })
    const pdf = await pdfLoadingTask.promise
    totalPages.value = pdf.numPages

    const page = await pdf.getPage(1)
    const canvas = pdfCanvasRef.value
    if (!canvas) return
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
    pdfError.value = e.message
    console.warn('PDF 加载失败:', e.message)
  }
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

.pdf-card {
  margin-top: 20px;
}

.pdf-preview {
  text-align: center;
}

.pdf-canvas {
  border: 1px solid #ddd;
  max-width: 100%;
}

.pdf-error {
  padding: 50px;
  color: #999;
}

.pdf-footer {
  margin-top: 10px;
  color: #999;
  font-size: 13px;
}
</style>
