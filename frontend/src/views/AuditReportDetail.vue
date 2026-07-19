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

      <!-- PDF / DOCX 预览 -->
      <el-card shadow="hover" class="pdf-card">
        <template #header>
          <div class="pdf-header">
            <span>合同原文预览</span>
            <el-tag v-if="!isPdf" size="small" type="info">DOCX</el-tag>
            <el-tag v-else-if="!pdfError && totalPages > 0" size="small">第 {{ currentPage }} / {{ totalPages }} 页</el-tag>
          </div>
        </template>

        <!-- DOCX -->
        <template v-if="!isPdf">
          <div v-show="docxLoading" class="docx-status"><el-icon class="is-loading"><Loading /></el-icon> 正在解析文档...</div>
          <div v-show="docxError" class="docx-status docx-status--err"><el-icon><Warning /></el-icon> {{ docxError }}</div>
          <div class="zoom-bar">
            <el-button size="small" :icon="ZoomOut" :disabled="docxZoom <= 0.3" @click="zoomDocx(-0.1)" />
            <span class="zoom-label">{{ Math.round(docxZoom * 100) }}%</span>
            <el-button size="small" :icon="ZoomIn" :disabled="docxZoom >= 2" @click="zoomDocx(0.1)" />
            <el-button size="small" @click="resetDocxZoom">适配窗口</el-button>
          </div>
          <div class="docx-viewer">
            <div ref="docxContainerRef" :style="{ width: DOCX_W + 'px', zoom: docxZoom }"></div>
          </div>
        </template>

        <!-- PDF -->
        <template v-else>
          <div v-if="pdfLoading" class="pdf-loading">
            <el-icon class="is-loading" :size="32"><Loading /></el-icon>
            <p>正在加载合同原文...</p>
          </div>
          <div v-if="!pdfError && totalPages > 0" class="pdf-controls">
            <el-button-group>
              <el-button size="small" :disabled="currentPage <= 1" @click="goToPage(currentPage - 1)">
                <el-icon><ArrowLeft /></el-icon>
              </el-button>
              <el-button size="small" disabled class="page-indicator">{{ currentPage }} / {{ totalPages }}</el-button>
              <el-button size="small" :disabled="currentPage >= totalPages" @click="goToPage(currentPage + 1)">
                <el-icon><ArrowRight /></el-icon>
              </el-button>
            </el-button-group>
            <el-input-number v-model="jumpPage" :min="1" :max="totalPages" size="small" controls-position="right" style="width:120px;margin-left:8px" @change="goToPage(jumpPage)" />
          </div>
          <canvas v-show="!pdfError && !pdfLoading" ref="pdfCanvasRef" class="pdf-canvas"></canvas>
          <div v-if="pdfError" class="pdf-error"><el-icon :size="40"><Warning /></el-icon><p>PDF 加载失败：{{ pdfError }}</p></div>
        </template>
      </el-card>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { Warning, ArrowLeft, ArrowRight, SuccessFilled, Loading, ZoomIn, ZoomOut } from '@element-plus/icons-vue'
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
    try { await fetchAndDetect(contractId.value) } catch { isPdf.value = true }
    if (isPdf.value) loadPdf(); else loadDocx()
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

// ── DOCX ──
const docxContainerRef = ref(null)
const docxLoading = ref(false)
const docxError = ref('')
const docxZoom = ref(1)

function applyZoom(z) { docxZoom.value = z; if (docxContainerRef.value) docxContainerRef.value.style.zoom = z }

async function loadDocx() {
  if (isPdf.value !== false) return
  docxLoading.value = true; docxError.value = ''
  try {
    await renderAsync(new Blob([cachedFileData],{type:'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}), docxContainerRef.value, undefined, {className:'docx-page'})
    const vw = (docxContainerRef.value?.parentElement?.clientWidth || 0) - 16
    applyZoom(vw > 0 ? Math.round(Math.max(0.3, vw / DOCX_W) * 10) / 10 : 0.8)
  } catch (e) { docxError.value = 'DOCX 解析失败：' + (e.message || '未知错误') }
  finally { docxLoading.value = false }
}
function zoomDocx(d) { applyZoom(Math.round(Math.max(0.3, Math.min(2, docxZoom.value + d)) * 100) / 100) }
function resetDocxZoom() { const vw = (docxContainerRef.value?.parentElement?.clientWidth || 0) - 16; applyZoom(vw > 0 ? Math.round(Math.max(0.3, vw / DOCX_W) * 10) / 10 : 0.8) }

// ── PDF ──
const pdfCanvasRef = ref(null)
const currentPage = ref(1)
const totalPages = ref(0)
const jumpPage = ref(1)
const pdfError = ref('')
const pdfLoading = ref(false)
let pdfLoadingTask = null
let pdfDoc = null

async function loadPdf() {
  if (isPdf.value !== true || !cachedFileData) return
  pdfjsLib.GlobalWorkerOptions.workerSrc = pdfjsWorker
  pdfLoading.value = true; pdfError.value = ''; currentPage.value = 1; jumpPage.value = 1
  try {
    pdfLoadingTask = pdfjsLib.getDocument({data: cachedFileData.slice()})
    pdfDoc = await pdfLoadingTask.promise
    totalPages.value = pdfDoc.numPages
    await renderPage(1)
  } catch (e) { pdfError.value = e.message || 'PDF 加载失败'; console.warn(e) }
  finally { pdfLoading.value = false }
}

async function renderPage(pageNum) {
  if (!pdfDoc) return
  const canvas = pdfCanvasRef.value; if (!canvas) return
  try {
    const page = await pdfDoc.getPage(pageNum)
    currentPage.value = pageNum; jumpPage.value = pageNum
    const vp = page.getViewport({scale:1})
    const s = Math.min((canvas.parentElement.clientWidth - 2) / vp.width, 1.5)
    const sv = page.getViewport({scale:s})
    canvas.width = sv.width; canvas.height = sv.height
    await page.render({canvasContext:canvas.getContext('2d'),viewport:sv}).promise
  } catch (e) { console.warn('PDF 页面渲染失败:', e) }
}
function goToPage(pageNum) { if (pageNum < 1 || pageNum > totalPages.value) return; renderPage(pageNum) }

onMounted(async () => {
  await fetchReport()
  await nextTick(); initCharts()
  try { await fetchAndDetect(contractId.value) } catch { isPdf.value = true }
  if (isPdf.value) loadPdf(); else loadDocx()
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

.docx-status { padding:20px; text-align:center; color:#909399; font-size:13px }
.docx-status--err { color:#f56c6c }
.zoom-bar { display:flex; align-items:center; gap:6px; margin-bottom:8px }
.zoom-label { font-size:13px; color:#606266; min-width:40px; text-align:center }
.docx-viewer { max-height:calc(100vh - 200px); min-height:400px; overflow:auto; border:1px solid #e4e7ed; border-radius:4px; background:#fff; padding:4px; display:flex; justify-content:center }
.docx-viewer > div { flex-shrink:0 }
.docx-viewer :where(.docx-page-wrapper) { background:#fff }
.docx-viewer :where(section) { padding:24px 32px; background:#fff }
</style>
