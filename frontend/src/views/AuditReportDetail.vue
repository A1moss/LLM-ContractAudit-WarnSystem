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

      <!-- 合同原文预览 -->
      <el-card shadow="hover" class="pdf-card">
        <template #header>
          <div class="pdf-header">
            <span>合同原文预览</span>
            <div class="pdf-toolbar">
              <el-tag v-if="convertingDocx" size="small" type="warning">加载中...</el-tag>
            </div>
          </div>
        </template>

        <!-- 加载中 -->
        <div v-if="convertingDocx" class="converting-state">
          <el-icon class="is-loading" :size="24"><Loading /></el-icon>
          <p>正在加载预览</p>
          <p class="converting-hint">首次加载需要 5-10 秒</p>
        </div>

        <!-- PDF 模式：双页并排 -->
        <template v-else-if="pdfReady">
          <div class="spread-viewer">
            <div class="spread-container">
              <!-- 左页 -->
              <div class="page-slot">
                <canvas ref="leftCanvasRef" class="pdf-canvas"></canvas>
                <span class="page-num">{{ leftPageNum }}</span>
              </div>
              <!-- 右页 -->
              <div class="page-slot">
                <canvas ref="rightCanvasRef" class="pdf-canvas"></canvas>
                <span class="page-num">{{ rightPageNum }}</span>
              </div>
            </div>
          </div>

          <div class="page-nav">
            <el-button size="small" :disabled="currentSpread <= 1" @click="goToSpread(1)">
              <el-icon><ArrowLeft /></el-icon><el-icon><ArrowLeft /></el-icon>
            </el-button>
            <el-button size="small" :disabled="currentSpread <= 1" @click="prevSpread">
              <el-icon><ArrowLeft /></el-icon>
            </el-button>

            <span class="spread-label">{{ leftPageNum }}/{{ rightPageNum }}</span>

            <el-button size="small" :disabled="currentSpread >= totalSpreads" @click="nextSpread">
              <el-icon><ArrowRight /></el-icon>
            </el-button>
            <el-button size="small" :disabled="currentSpread >= totalSpreads" @click="goToSpread(totalSpreads)">
              <el-icon><ArrowRight /></el-icon><el-icon><ArrowRight /></el-icon>
            </el-button>

            <span class="page-jump">
              <span class="jump-label">跳至</span>
              <el-input v-model="jumpPage" size="small" class="jump-input" @keyup.enter="handleJump" />
              <span class="jump-label">页</span>
              <el-button size="small" @click="handleJump">GO</el-button>
            </span>
          </div>
        </template>

        <!-- 错误 -->
        <div v-else class="pdf-error-box">
          <el-icon :size="32"><Warning /></el-icon>
          <p>{{ pdfError || '无法加载合同内容' }}</p>
        </div>
      </el-card>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { Warning, ArrowLeft, ArrowRight, SuccessFilled, Loading } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import * as echarts from 'echarts'
import * as pdfjsLib from 'pdfjs-dist'
import pdfjsWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { renderAsync } from 'docx-preview'
import { getAuditReport, getAuditResult } from '../api/contract.js'

const route = useRoute()
const contractId = computed(() => route.params.contractId || '')

const report = ref(null)
const riskItems = ref([])
const riskTypes = ref([])
const loading = ref(true)
const error = ref('')

// ── 文件格式检测 ──
const isPdf = ref(null)
const DOCX_W = 602
let cachedFileData = null

async function fetchAndDetect(id) {
  const token = localStorage.getItem('token')
  const res = await fetch(`/api/contracts/${id}/file`,{headers:token?{Authorization:`Bearer ${token}`}:{}})
  if (!res.ok) throw new Error(`服务器返回 ${res.status}`)
  const buf = await res.arrayBuffer()
  const head = new Uint8Array(buf.slice(0, 4))
  if (head[0]===0x25 && head[1]===0x50 && head[2]===0x44 && head[3]===0x46) { isPdf.value=true }
  else if (head[0]===0x50 && head[1]===0x4B) { isPdf.value=false }
  else { isPdf.value = true }
  cachedFileData = buf
}

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
    loadPdf()
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

// ── PDF 双页并排 ──
const leftCanvasRef = ref(null)
const rightCanvasRef = ref(null)
const currentSpread = ref(1)
const totalPages = ref(0)
const pdfReady = ref(false)
const pdfError = ref('')
const convertingDocx = ref(false)
const zoomFit = ref(1.0)
const jumpPage = ref('')
let pdfLoadingTask = null
let pdfDoc = null

const totalSpreads = computed(() => Math.ceil(totalPages.value / 2))
const leftPageNum = computed(() => (currentSpread.value - 1) * 2 + 1)
const rightPageNum = computed(() => Math.min(leftPageNum.value + 1, totalPages.value))

async function loadPdf() {
  pdfjsLib.GlobalWorkerOptions.workerSrc = pdfjsWorker
  const id = contractId.value
  if (!id) return

  let fileData = null
  convertingDocx.value = true
  try {
    fileData = await getContractFile(id)
  } catch {
    console.warn('合同文件 API 加载失败')
  }

  if (fileData) {
    try {
      pdfLoadingTask = pdfjsLib.getDocument({ data: new Uint8Array(fileData) })
      pdfDoc = await pdfLoadingTask.promise
      totalPages.value = pdfDoc.numPages
      // 每个 page-slot 可用宽度 = 卡片宽度 / 2 - 间隙
      convertingDocx.value = false
      pdfReady.value = true
      await nextTick()
      renderSpread(currentSpread.value)
      return
    } catch (e) {
      convertingDocx.value = false
      pdfError.value = 'PDF 加载失败，请确认文件格式正确'
      console.warn('PDF 渲染失败:', e.message)
      return
    }
  }
  convertingDocx.value = false
  pdfError.value = '后端服务未启动，无法加载文件'
}

async function renderPageToCanvas(pageNum, canvas) {
  if (!pdfDoc || !canvas || pageNum > totalPages.value) return
  const page = await pdfDoc.getPage(pageNum)
  const vp = page.getViewport({ scale: 1.0 })
  // 按 canvas 父容器宽度适配
  const slotW = canvas.parentElement?.clientWidth || 400
  const scale = (slotW - 16) / vp.width
  const svp = page.getViewport({ scale })
  canvas.width = svp.width
  canvas.height = svp.height
  const ctx = canvas.getContext('2d')
  await page.render({ canvasContext: ctx, viewport: svp }).promise
}

async function renderSpread(spreadNum) {
  if (!pdfDoc) return
  const leftPage = (spreadNum - 1) * 2 + 1
  const rightPage = leftPage + 1
  currentSpread.value = spreadNum
  await Promise.all([
    renderPageToCanvas(leftPage, leftCanvasRef.value),
    rightPage <= totalPages.value ? renderPageToCanvas(rightPage, rightCanvasRef.value) : clearCanvas(rightCanvasRef.value),
  ])
}

function clearCanvas(canvas) {
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  ctx?.clearRect(0, 0, canvas.width, canvas.height)
  canvas.width = 0
  canvas.height = 0
}

function goToSpread(num) {
  if (num >= 1 && num <= totalSpreads.value) renderSpread(num)
}

function prevSpread() { goToSpread(currentSpread.value - 1) }
function nextSpread() { goToSpread(currentSpread.value + 1) }

function handleJump() {
  const n = parseInt(jumpPage.value, 10)
  if (isNaN(n) || n < 1 || n > totalPages.value) {
    ElMessage.warning(`请输入 1-${totalPages.value} 之间的页码`)
    return
  }
  // 输入任意页码 → 跳到包含该页的 spread
  const spread = Math.ceil(n / 2)
  goToSpread(spread)
  jumpPage.value = ''
}
function goToPage(pageNum) { if (pageNum < 1 || pageNum > totalPages.value) return; renderPage(pageNum) }

onMounted(async () => {
  await fetchReport()
  await nextTick()
  initCharts()
  loadPdf()
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

.pdf-card { margin-top:20px }
.pdf-header { display:flex; justify-content:space-between; align-items:center }
.pdf-controls { display:flex; align-items:center; margin-bottom:8px }
.page-indicator { min-width:80px; text-align:center }
.pdf-preview { text-align:center }
.pdf-loading { padding:50px; color:#909399 }
.pdf-canvas { border:1px solid #ddd; max-width:100% }
.pdf-error { padding:50px; color:#999 }

.pdf-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  min-height: 36px;
}

.pdf-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

/* 双页并排 */
.spread-viewer {
  background: #f5f7fa;
  border-radius: 4px;
  padding: 16px 8px;
}

.spread-container {
  display: flex;
  gap: 12px;
  justify-content: center;
}

.page-slot {
  flex: 1;
  min-width: 0;
  text-align: center;
  position: relative;
}

.page-slot .pdf-canvas {
  border: 1px solid #dcdfe6;
  box-shadow: 0 1px 4px rgba(0,0,0,0.08);
}

.page-num {
  display: block;
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}

.converting-state {
  padding: 60px 20px;
  text-align: center;
  color: #606266;
  min-height: 400px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  background: #fafafa;
  border-radius: 4px;
  border: 1px solid #ebeef5;
}

.converting-state .is-loading { animation: rotating 2s linear infinite; }

.converting-hint { font-size: 12px; color: #909399; }

@keyframes rotating {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.pdf-error-box {
  padding: 40px;
  text-align: center;
  color: #999;
}

.pdf-error-box p { margin-top: 8px; }

.page-nav {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  margin-top: 12px;
  flex-wrap: wrap;
}

.spread-label {
  font-size: 14px;
  color: #303133;
  font-weight: 500;
  min-width: 60px;
  text-align: center;
}

.page-ellipsis {
  color: #909399;
  padding: 0 4px;
  user-select: none;
}

.page-jump {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-left: 4px;
}

.jump-label { font-size: 13px; color: #909399; white-space: nowrap; }
.jump-input { width: 56px; }
</style>
