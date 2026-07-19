<template>
  <div class="page-container">
    <div v-if="loading" class="loading-state"><el-skeleton :rows="8" animated /></div>
    <div v-else-if="error" class="error-state">
      <el-result icon="error" title="加载失败" :sub-title="error">
        <template #extra>
          <el-button type="primary" @click="fetchDetail">重新加载</el-button>
          <el-button @click="$router.push('/contracts')">返回列表</el-button>
        </template>
      </el-result>
    </div>

    <template v-else-if="contract">
      <el-descriptions title="合同详情" :column="5" border class="meta-descriptions">
        <el-descriptions-item label="文件名"><el-tag type="primary" size="small">{{ contract.file_name }}</el-tag></el-descriptions-item>
        <el-descriptions-item label="合同类型">{{ typeLabel(contract.contract_type) }}</el-descriptions-item>
        <el-descriptions-item label="上传时间">{{ formatTime(contract.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="页数">{{ totalPages }} 页</el-descriptions-item>
        <el-descriptions-item label="审核状态">
          <el-tag :type="statusTag(contract.status)">{{ statusLabel(contract.status) }}</el-tag>
        </el-descriptions-item>
      </el-descriptions>

      <el-row :gutter="20" class="detail-row">
        <el-col :span="14">
          <el-tabs v-model="activeTab" type="border-card">
            <el-tab-pane label="原始文本" name="text">
              <div class="tab-content">
                <div v-if="contract.parsed_text" v-html="renderedText"></div>
                <el-empty v-else description="暂无解析文本" />
              </div>
            </el-tab-pane>
            <el-tab-pane label="风险详情" name="audit">
              <div class="tab-content">
                <div v-if="contract.status === 'parsed' && riskItems.length === 0" class="audit-placeholder">
                  <el-empty description="尚未审核此合同">
                    <el-button type="primary" :loading="auditing" @click="handleTriggerAudit">开始审核</el-button>
                  </el-empty>
                </div>

                <div v-else-if="contract.status === 'auditing'" class="audit-placeholder">
                  <el-result icon="info" title="审核中" sub-title="AI 正在分析合同条款，请稍候...">
                    <template #extra><el-button :loading="true" type="primary">审核进行中</el-button></template>
                  </el-result>
                </div>

                <template v-else-if="riskItems.length > 0">
                  <el-alert :title="`共检测到 ${riskSummary.total} 条风险，高风险 ${riskSummary.high}、中风险 ${riskSummary.mid}、低风险 ${riskSummary.low}`" type="warning" show-icon :closable="false" class="audit-alert" />
                  <el-table :data="riskItems" stripe size="small" max-height="400">
                    <el-table-column prop="level" label="等级" width="80"><template #default="{row}"><el-tag :type="levelTag(row.level)" size="small">{{ row.level }}</el-tag></template></el-table-column>
                    <el-table-column prop="category" label="风险类别" width="130" />
                    <el-table-column prop="clause" label="涉及条款" min-width="180" show-overflow-tooltip />
                    <el-table-column prop="suggestion" label="建议" min-width="200" show-overflow-tooltip />
                    <el-table-column prop="confidence" label="置信度" width="100"><template #default="{row}"><el-progress :percentage="Math.round((row.confidence||0)*100)" :color="row.confidence>=0.7?'#67C23A':row.confidence>=0.5?'#E6A23C':'#F56C6C'" :stroke-width="6" /></template></el-table-column>
                  </el-table>
                  <el-button type="primary" size="small" class="audit-full-link" @click="goToAuditResult">
                    全屏查看结果
                  </el-button>

                  <FeedbackPanel
                    ref="feedbackRef"
                    :risk-items="riskItems"
                    :contract-id="contract?.id"
                    :loaded-feedbacks="loadedFeedbacks"
                    @feedback-change="onFeedback"
                    @feedback-undo="onFeedbackUndo"
                  />
                </template>

                <el-empty v-else description="审核完成，未检测到风险" />
              </div>
            </el-tab-pane>
            <el-tab-pane label="条款比对" name="compare">
              <div class="tab-content">
                <el-empty v-if="!clauseComparison" description="暂无条款比对结果" />
                <template v-else>
                  <el-alert :title="`条款覆盖率 ${Math.round(clauseComparison.summary.coverage_rate * 100)}%，缺失 ${clauseComparison.summary.missing} 条关键条款`" :type="clauseComparison.summary.missing > 0 ? 'warning' : 'success'" show-icon :closable="false" class="audit-alert" />
                  <el-tag v-for="c in clauseComparison.missing_critical" :key="c" type="danger" size="small" style="margin:4px">缺失: {{ c }}</el-tag>
                  <el-table :data="clauseComparison.clauses" stripe size="small" max-height="400" style="margin-top:12px">
                    <el-table-column prop="title" label="条款名称" width="140" />
                    <el-table-column label="状态" width="100">
                      <template #default="{row}">
                        <el-tag :type="row.status === 'covered' ? 'success' : row.status === 'partial' ? 'warning' : 'danger'" size="small">
                          {{ row.status === 'covered' ? '已覆盖' : row.status === 'partial' ? '部分偏离' : '缺失' }}
                        </el-tag>
                      </template>
                    </el-table-column>
                    <el-table-column prop="matched_text" label="匹配条款" min-width="180" show-overflow-tooltip />
                    <el-table-column prop="deviation" label="偏离说明" min-width="160" show-overflow-tooltip />
                    <el-table-column prop="completion" label="补全建议" min-width="160" show-overflow-tooltip />
                    <el-table-column prop="risk" label="风险说明" width="120" show-overflow-tooltip />
                  </el-table>
                </template>
              </div>
            </el-tab-pane>
            <el-tab-pane label="审核报告" name="report">
              <div class="tab-content">
                <el-empty v-if="contract.status !== 'completed'" description="审核完成后将自动生成报告">
                  <el-button v-if="contract.status === 'parsed'" type="primary" :loading="auditing" @click="handleTriggerAudit">开始审核</el-button>
                </el-empty>
                <template v-else-if="riskItems.length > 0">
                  <el-descriptions :column="2" border size="small" class="report-desc">
                    <el-descriptions-item label="风险总数">{{ riskSummary.total }} 条</el-descriptions-item>
                    <el-descriptions-item label="高风险">{{ riskSummary.high }} 条</el-descriptions-item>
                    <el-descriptions-item label="中风险">{{ riskSummary.mid }} 条</el-descriptions-item>
                    <el-descriptions-item label="低风险">{{ riskSummary.low }} 条</el-descriptions-item>
                  </el-descriptions>
                  <el-button type="primary" class="report-btn" @click="goToAuditReport">查看完整审核报告</el-button>
                </template>
                <el-empty v-else description="审核完成，未检测到风险" />
              </div>
            </el-tab-pane>
          </el-tabs>
        </el-col>

        <!-- 右侧预览面板 -->
        <el-col :span="10" class="detail-right">
          <el-card shadow="hover">
            <template #header>
              <div class="pdf-header">
                <span>合同原文</span>
                <div class="pdf-toolbar">
                  <template v-if="pdfReady">
                    <el-button size="small" @click="zoomReset">重新适配</el-button>
                    <el-tag size="small">第 {{ currentPage }} / {{ totalPages }} 页</el-tag>
                  </template>
                  <el-tag v-else-if="convertingDocx" size="small" type="warning">转换中...</el-tag>
                </div>
              </div>
            </template>

            <!-- PDF 转换中 -->
            <div v-if="convertingDocx" class="converting-state">
              <el-icon class="is-loading" :size="24"><Loading /></el-icon>
              <p>正在加载预览</p>
              <p class="converting-hint">首次加载需要 5-10 秒</p>
            </div>

            <!-- PDF 模式 -->
            <template v-else-if="pdfReady">
              <div class="pdf-viewer">
                <div class="pdf-scroll-container" ref="pdfScrollRef">
                  <canvas ref="pdfCanvasRef" class="pdf-canvas"></canvas>
                </div>
              </div>

              <!-- 页码导航（省略号风格） -->
              <div class="page-nav">
                <el-button size="small" :disabled="currentPage <= 1" @click="goToPage(1)">首页</el-button>
                <el-button size="small" :disabled="currentPage <= 1" @click="goToPage(currentPage - 1)">
                  <el-icon><ArrowLeft /></el-icon>
                </el-button>

                <template v-for="p in pageEllipsisRange" :key="p">
                  <span v-if="p === '...'" class="page-ellipsis">...</span>
                  <el-button
                    v-else
                    size="small"
                    :type="p === currentPage ? 'primary' : 'default'"
                    @click="goToPage(p)"
                  >
                    {{ p }}
                  </el-button>
                </template>

                <el-button size="small" :disabled="currentPage >= totalPages" @click="goToPage(currentPage + 1)">
                  <el-icon><ArrowRight /></el-icon>
                </el-button>
                <el-button size="small" :disabled="currentPage >= totalPages" @click="goToPage(totalPages)">末页</el-button>

                <span class="page-jump">
                  <span class="jump-label">跳至</span>
                  <el-input
                    v-model="jumpPage"
                    size="small"
                    class="jump-input"
                    @keyup.enter="handleJump"
                  />
                  <span class="jump-label">页</span>
                  <el-button size="small" @click="handleJump">GO</el-button>
                </span>
              </div>

              <!-- 侧栏条款摘要 -->
              <div class="text-sidebar">
                <el-divider class="sidebar-divider" />
                <span class="sidebar-label">当前页条款摘要</span>
                <p class="sidebar-text">{{ pageSummary }}</p>
              </div>
            </template>

            <!-- 加载失败 -->
            <div v-else class="pdf-error">
              <el-icon :size="32"><Warning /></el-icon>
              <p>{{ pdfError || '无法加载合同内容' }}</p>
            </div>
          </el-card>
        </el-col>
      </el-row>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Warning, ArrowLeft, ArrowRight, Loading } from '@element-plus/icons-vue'
import FeedbackPanel from '../components/FeedbackPanel.vue'
import { ElMessage } from 'element-plus'
import { getContractDetail, getAuditResult, triggerAudit, getClauseComparison, submitFeedback, getFeedback, getContractFile } from '../api/contract.js'
import { formatTime } from '../utils/format.js'
import * as pdfjsLib from 'pdfjs-dist'
import pdfjsWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

const route = useRoute()
const router = useRouter()

const feedbackRef = ref(null)

// ── 数据状态 ──
const loading = ref(true)
const error = ref('')
const contract = ref(null)
const contractId = computed(() => route.params.id)

// ── 合同类型映射 ──
const typeMap = {
  purchase: '采购合同', sales: '销售合同', nda: '保密协议 (NDA)',
  outsourcing: '服务外包合同', employment: '劳动合同', other: '其他合同',
}
function typeLabel(type) { return typeMap[type] || type || '未分类' }

// ── 状态映射 ──
function statusLabel(status) {
  const map = { uploaded: '已上传', parsed: '已解析', auditing: '审核中', completed: '审核完成' }
  return map[status] || status || '未知'
}
function statusTag(status) {
  if (status === 'completed') return 'success'
  if (status === 'auditing' || status === 'parsing') return 'warning'
  return 'info'
}

// ── HTML 转义 ──
function escapeHtml(text) {
  const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }
  return text.replace(/[&<>"']/g, ch => map[ch])
}

// ── 渲染文本 ──
const renderedText = computed(() => {
  const text = contract.value?.parsed_text || ''
  return text.split('\n').map(p => (p.trim() ? `<p>${escapeHtml(p)}</p>` : '<p><br></p>')).join('')
})

// ── 获取合同详情 ──
async function fetchDetail() {
  loading.value = true
  error.value = ''
  try {
    const res = await getContractDetail(route.params.id)
    contract.value = res.data
    fetchFeedback()
    loadPdf()
  } catch (e) {
    error.value = '无法加载合同详情，请确认合同 ID 有效且后端已启动'
    console.warn('合同详情加载失败:', e)
  } finally {
    loading.value = false
  }
}

// ── Tab 状态 ──
const activeTab = ref('text')

// ── PDF 状态 ──
const currentPage = ref(1)
const totalPages = ref(0)
const pdfReady = ref(false)
const pdfError = ref('')
const pdfCanvasRef = ref(null)
const pdfScrollRef = ref(null)
const zoomLevel = ref(1.0)
const zoomFit = ref(1.0)
const jumpPage = ref('')
const convertingDocx = ref(false)
const loadedFeedbacks = ref([])

let pdfDoc = null
let pdfLoadingTask = null

// ── loadPdf：自动转换 + 自动适配 ──
async function loadPdf() {
  pdfjsLib.GlobalWorkerOptions.workerSrc = pdfjsWorker
  const cid = route.params.id

  let fileData = null
  if (cid) {
    try {
      convertingDocx.value = true
      fileData = await getContractFile(cid)
    } catch {
      console.warn('合同文件 API 加载失败')
    }
  }

  if (fileData) {
    try {
      pdfLoadingTask = pdfjsLib.getDocument({ data: new Uint8Array(fileData) })
      pdfDoc = await pdfLoadingTask.promise
      totalPages.value = pdfDoc.numPages
      const firstPage = await pdfDoc.getPage(1)
      const vp = firstPage.getViewport({ scale: 1.0 })
      zoomFit.value = +(380 / vp.width).toFixed(2)
      zoomLevel.value = zoomFit.value
      // 必须先关掉 convertingDocx，否则模板 v-if="convertingDocx" 会挡住 canvas
      convertingDocx.value = false
      pdfReady.value = true
      await nextTick()
      renderPage(currentPage.value)
    } catch (e) {
      convertingDocx.value = false
      pdfError.value = 'PDF 加载失败，请确认文件格式正确'
      console.warn('PDF 渲染失败:', e.message)
    }
  } else {
    convertingDocx.value = false
    pdfError.value = '后端服务未启动，无法加载文件'
  }
}

async function renderPage(num) {
  if (!pdfDoc || !pdfCanvasRef.value) return
  try {
    const page = await pdfDoc.getPage(num)
    const vp = page.getViewport({ scale: zoomLevel.value })
    const canvas = pdfCanvasRef.value
    canvas.width = vp.width
    canvas.height = vp.height
    const ctx = canvas.getContext('2d')
    await page.render({ canvasContext: ctx, viewport: vp }).promise
    currentPage.value = num
  } catch (e) {
    console.warn('页面渲染失败:', e.message)
  }
}

function goToPage(num) {
  if (num >= 1 && num <= totalPages.value) {
    renderPage(num)
    pdfScrollRef.value?.scrollTo({ top: 0, behavior: 'smooth' })
  }
}

function handleJump() {
  const n = parseInt(jumpPage.value, 10)
  if (isNaN(n) || n < 1 || n > totalPages.value) {
    ElMessage.warning(`请输入 1-${totalPages.value} 之间的页码`)
    return
  }
  goToPage(n)
  jumpPage.value = ''
}

function zoomReset() {
  zoomLevel.value = zoomFit.value
  renderPage(currentPage.value)
  pdfScrollRef.value?.scrollTo({ top: 0, behavior: 'smooth' })
}

// ── 页码省略号范围 ──
const pageEllipsisRange = computed(() => {
  const total = totalPages.value
  const cur = currentPage.value
  if (total <= 7) {
    return Array.from({ length: total }, (_, i) => i + 1)
  }
  const range = []
  // Always show first page
  range.push(1)
  // Ellipsis if gap > 1
  if (cur > 3) range.push('...')
  // Pages around current
  const start = Math.max(2, cur - 1)
  const end = Math.min(total - 1, cur + 1)
  for (let i = start; i <= end; i++) {
    if (i !== 1 && i !== total) range.push(i)
  }
  // Ellipsis if gap > 1
  if (cur < total - 2) range.push('...')
  // Always show last page
  range.push(total)
  return [...new Set(range)]
})

// ── 页码摘要 ──
const pageSummary = ref('点击页码浏览各页合同条款')
const pageSummaries = {
  1: '保密协议定义条款：明确保密信息的范围，包括技术资料、商业计划、客户信息、财务数据等，采用概括+列举的定义方式。',
  2: '保密义务条款：乙方不得向第三方披露、仅可用于约定目的、需采取不低于保护自身同类信息的注意程度。',
  3: '保密期限条款：保密义务有效期 5 年，自保密信息披露之日起计算。',
  4: '违约责任与争议解决：违约责任条款约定赔偿计算方式，争议提交甲方所在地法院管辖。',
}
watch(currentPage, (p) => {
  pageSummary.value = pageSummaries[p] || '本页无摘要信息'
})

// ── 风险详情 ──
const riskItems = ref([])
const levelMap = { high: '高风险', medium: '中风险', low: '低风险' }

async function fetchAuditResult() {
  const id = route.params.id
  if (!id) return
  try {
    const res = await getAuditResult(id)
    riskItems.value = (res.data?.items || []).map(r => ({
      id: r.id, risk_level: r.risk_level, risk_type: r.risk_type, clause_text: r.clause_text,
      level: levelMap[r.risk_level] || r.risk_level, category: r.risk_type, clause: r.clause_text,
      suggestion: r.suggestion, reason: r.reason, confidence: r.confidence, detection_method: r.detection_method,
    }))
  } catch { riskItems.value = [] }
}

const riskSummary = computed(() => {
  const h=riskItems.value.filter(r=>r.level==='高风险').length
  const m=riskItems.value.filter(r=>r.level==='中风险').length
  const l=riskItems.value.filter(r=>r.level==='低风险').length
  return {total:riskItems.value.length,high:h,mid:m,low:l}
})

function levelTag(level) {
  if (level === '高风险') return 'danger'
  if (level === '中风险') return 'warning'
  return 'success'
}

const clauseComparison = ref(null)
const comparing = ref(false)
async function fetchClauseComparison() {
  comparing.value = true
  try {
    const res = await getClauseComparison(route.params.id)
    clauseComparison.value = res.data || null
  } catch { clauseComparison.value = null }
  finally { comparing.value = false }
}

const auditing = ref(false)
async function handleTriggerAudit() {
  auditing.value = true
  try {
    await triggerAudit(route.params.id)
    contract.value.status = 'auditing'
    await fetchAuditResult()
    await fetchClauseComparison()
    await fetchDetail()
    ElMessage.success('审核完成')
  } catch (e) {
    ElMessage.error('审核触发失败')
  } finally { auditing.value = false }
}

async function fetchFeedback() {
  const cid = contract.value?.id
  if (!cid) return
  try {
    const res = await getFeedback(cid)
    loadedFeedbacks.value = res.data?.items || []
  } catch { loadedFeedbacks.value = [] }
}

async function onFeedback(payload) {
  try {
    await submitFeedback(payload)
    ElMessage.success('反馈已保存')
    fetchFeedback()
  } catch (e) {
    ElMessage.error('反馈提交失败：' + (e.response?.data?.detail || e.message))
  }
}

function onFeedbackUndo(payload) {
  console.log('[FeedbackPanel] 撤销反馈:', payload)
  ElMessage.info('已撤销（本地状态）')
}

// ── 跳转 ──
function goToAuditResult() { router.push(`/audit/result/${route.params.id}`) }
function goToAuditReport() { router.push(`/audit/report/${route.params.id}`) }

// ── 生命周期 ──
onMounted(() => {
  fetchDetail()
  fetchAuditResult()
  fetchClauseComparison()
})
</script>

<style scoped>
.page-container {
  padding: 24px;
  max-width: 1200px;
  margin: 0 auto;
}

.loading-state { padding: 40px 0; }
.error-state { padding: 60px 0; }
.meta-descriptions { margin-bottom: 20px; }

.detail-row {
  --row-height: calc(100vh - 200px);
  height: var(--row-height);
  min-height: 600px;
  overflow: hidden;
}

.detail-right {
  height: 100%;
  overflow-y: auto;
}

.audit-alert { margin-bottom: 16px; }
.audit-placeholder { padding: 60px 0; }
.audit-full-link { margin-top: 12px; }
.report-desc { margin-bottom: 16px; }
.report-btn { margin-top: 8px; }

.tab-content {
  height: calc(var(--row-height) - 55px);
  min-height: 540px;
  overflow-y: auto;
  padding: 8px 0;
  line-height: 1.8;
  color: #303133;
}

.tab-content h4 { margin: 0 0 12px 0; }
.tab-content h5 { margin: 16px 0 8px 0; }

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

.pdf-viewer { text-align: center; }

.pdf-scroll-container {
  height: 520px;
  overflow: auto;
  background: #f5f7fa;
  border-radius: 4px;
  padding: 4px;
  display: flex;
  justify-content: center;
  align-items: flex-start;
}

.pdf-canvas { border: 1px solid #e4e7ed; }

/* 转换中 */
.converting-state {
  padding: 60px 20px;
  text-align: center;
  color: #606266;
  min-height: 520px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  background: #fafafa;
  border-radius: 4px;
  border: 1px solid #ebeef5;
}

.converting-state .is-loading {
  animation: rotating 2s linear infinite;
}

.converting-hint {
  font-size: 12px;
  color: #909399;
}

@keyframes rotating {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.pdf-error {
  padding: 40px;
  text-align: center;
  color: #999;
}

.pdf-error p { margin-top: 8px; }

/* 页码导航 */
.page-nav {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  margin-top: 12px;
  flex-wrap: wrap;
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

.jump-label {
  font-size: 13px;
  color: #909399;
  white-space: nowrap;
}

.jump-input { width: 56px; }

.text-sidebar { padding: 0 4px; }
.sidebar-divider { margin: 12px 0; }

.sidebar-label {
  font-size: 13px;
  color: #909399;
}

.sidebar-text {
  margin-top: 8px;
  font-size: 13px;
  line-height: 1.8;
  color: #606266;
}
</style>
