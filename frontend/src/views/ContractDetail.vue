<template>
  <div class="page-container">
    <!-- 加载状态 -->
    <div v-if="loading" class="loading-state">
      <el-skeleton :rows="8" animated />
    </div>

    <!-- 错误状态 -->
    <div v-else-if="error" class="error-state">
      <el-result icon="error" title="加载失败" :sub-title="error">
        <template #extra>
          <el-button type="primary" @click="fetchDetail">重新加载</el-button>
          <el-button @click="$router.push('/contracts')">返回列表</el-button>
        </template>
      </el-result>
    </div>

    <!-- 正常内容 -->
    <template v-else-if="contract">
      <!-- 顶部：合同元信息 -->
      <el-descriptions title="合同详情" :column="5" border class="meta-descriptions">
        <el-descriptions-item label="文件名">
          <el-tag type="primary" size="small">{{ contract.file_name }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="合同类型">{{ typeLabel(contract.contract_type) }}</el-descriptions-item>
        <el-descriptions-item label="上传时间">{{ formatTime(contract.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="页数">{{ totalPages }} 页</el-descriptions-item>
        <el-descriptions-item label="审核状态">
          <el-tag :type="statusTag(contract.status)">{{ statusLabel(contract.status) }}</el-tag>
        </el-descriptions-item>
      </el-descriptions>

      <!-- 主体：左侧 tabs + 右侧 PDF -->
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
                <!-- 审核未触发 -->
                <div v-if="contract.status === 'parsed' && riskItems.length === 0" class="audit-placeholder">
                  <el-empty description="尚未审核此合同">
                    <el-button type="primary" :loading="auditing" @click="handleTriggerAudit">
                      开始审核
                    </el-button>
                  </el-empty>
                </div>

                <!-- 审核中 -->
                <div v-else-if="contract.status === 'auditing'" class="audit-placeholder">
                  <el-result icon="info" title="审核中" sub-title="AI 正在分析合同条款，请稍候...">
                    <template #extra>
                      <el-button :loading="true" type="primary">审核进行中</el-button>
                    </template>
                  </el-result>
                </div>

                <!-- 审核完成：展示风险列表 -->
                <template v-else-if="riskItems.length > 0">
                  <el-alert
                    :title="`共检测到 ${riskSummary.total} 条风险，其中高风险 ${riskSummary.high} 条、中风险 ${riskSummary.mid} 条、低风险 ${riskSummary.low} 条`"
                    type="warning" show-icon :closable="false" class="audit-alert"
                  />
                  <el-table :data="riskItems" stripe size="small" max-height="400">
                    <el-table-column prop="level" label="等级" width="80">
                      <template #default="{ row }">
                        <el-tag :type="levelTag(row.level)" size="small">{{ row.level }}</el-tag>
                      </template>
                    </el-table-column>
                    <el-table-column prop="category" label="风险类别" width="130" />
                    <el-table-column prop="clause" label="涉及条款" min-width="180" show-overflow-tooltip />
                    <el-table-column prop="suggestion" label="建议" min-width="200" show-overflow-tooltip />
                    <el-table-column prop="confidence" label="置信度" width="100">
                      <template #default="{ row }">
                        <el-progress
                          :percentage="Math.round((row.confidence || 0) * 100)"
                          :color="row.confidence >= 0.7 ? '#67C23A' : row.confidence >= 0.5 ? '#E6A23C' : '#F56C6C'"
                          :stroke-width="6"
                        />
                      </template>
                    </el-table-column>
                  </el-table>
                  <el-button type="primary" size="small" class="audit-full-link" @click="goToAuditResult">
                    全屏查看结果
                  </el-button>

                  <!-- 反馈标注面板 -->
                  <FeedbackPanel
                    ref="feedbackRef"
                    :risk-items="riskItems"
                    :contract-id="contract?.id"
                    :loaded-feedbacks="loadedFeedbacks"
                    @feedback-change="onFeedback"
                    @feedback-undo="onFeedbackUndo"
                  />
                </template>

                <!-- 审核完成但无风险 -->
                <el-empty v-else description="审核完成，未检测到风险" />
              </div>
            </el-tab-pane>

            <el-tab-pane label="条款比对" name="compare">
              <div class="tab-content">
                <el-empty description="标准条款比对结果将在审核完成后生成" />
              </div>
            </el-tab-pane>

            <el-tab-pane label="审核报告" name="report">
              <div class="tab-content">
                <el-empty v-if="contract.status !== 'completed'" description="审核完成后将自动生成报告">
                  <el-button v-if="contract.status === 'parsed'" type="primary" :loading="auditing" @click="handleTriggerAudit">
                    开始审核
                  </el-button>
                </el-empty>
                <template v-else-if="riskItems.length > 0">
                  <el-descriptions :column="2" border size="small" class="report-desc">
                    <el-descriptions-item label="风险总数">{{ riskSummary.total }} 条</el-descriptions-item>
                    <el-descriptions-item label="高风险">{{ riskSummary.high }} 条</el-descriptions-item>
                    <el-descriptions-item label="中风险">{{ riskSummary.mid }} 条</el-descriptions-item>
                    <el-descriptions-item label="低风险">{{ riskSummary.low }} 条</el-descriptions-item>
                  </el-descriptions>
                  <el-button type="primary" class="report-btn" @click="goToAuditReport">
                    查看完整审核报告
                  </el-button>
                </template>
                <el-empty v-else description="审核完成，未检测到风险" />
              </div>
            </el-tab-pane>
          </el-tabs>
        </el-col>

        <!-- 右侧 PDF 预览面板 -->
        <el-col :span="10" class="detail-right">
          <el-card shadow="hover">
            <template #header>
              <div class="pdf-header">
                <span>合同原文</span>
                <el-tag size="small">第 {{ currentPage }} / {{ totalPages }} 页</el-tag>
              </div>
            </template>

            <!-- PDF 渲染区 -->
            <div v-if="!pdfError" class="pdf-viewer">
              <canvas ref="pdfCanvasRef" class="pdf-canvas"></canvas>
            </div>
            <div v-if="pdfError" class="pdf-error">
              <el-icon :size="32"><Warning /></el-icon>
              <p>{{ pdfError }}</p>
            </div>

            <!-- 页码跳转按钮组 -->
            <div v-if="!pdfError" class="page-nav">
              <el-button size="small" :disabled="currentPage <= 1" @click="goToPage(currentPage - 1)">
                <el-icon><ArrowLeft /></el-icon>
              </el-button>
              <span class="page-buttons">
                <el-button
                  v-for="p in totalPages"
                  :key="p"
                  :type="p === currentPage ? 'primary' : 'default'"
                  size="small"
                  @click="goToPage(p)"
                >
                  {{ p }}
                </el-button>
              </span>
              <el-button size="small" :disabled="currentPage >= totalPages" @click="goToPage(currentPage + 1)">
                <el-icon><ArrowRight /></el-icon>
              </el-button>
            </div>

            <!-- 侧栏文本展示（当前页条款摘要） -->
            <div v-if="!pdfError" class="text-sidebar">
              <el-divider class="sidebar-divider" />
              <span class="sidebar-label">当前页条款摘要</span>
              <p class="sidebar-text">
                {{ pageSummary }}
              </p>
            </div>
          </el-card>
        </el-col>
      </el-row>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Warning, ArrowLeft, ArrowRight } from '@element-plus/icons-vue'
import FeedbackPanel from '../components/FeedbackPanel.vue'
import { ElMessage } from 'element-plus'
import { getContractDetail, getAuditResult, triggerAudit, submitFeedback, getFeedback } from '../api/contract.js'
import * as pdfjsLib from 'pdfjs-dist'
import pdfjsWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

const route = useRoute()
const router = useRouter()

// ── 数据状态 ──
const loading = ref(true)
const error = ref('')
const contract = ref(null)

// ── 合同类型映射 ──
const typeMap = {
  purchase: '采购合同',
  sales: '销售合同',
  nda: '保密协议 (NDA)',
  outsourcing: '服务外包合同',
  employment: '劳动合同',
  other: '其他合同',
}

function typeLabel(type) {
  return typeMap[type] || type || '未分类'
}

// ── 状态映射 ──
function statusLabel(status) {
  const map = { uploaded: '已上传', parsed: '已解析', auditing: '审核中', completed: '审核完成' }
  return map[status] || status || '未知'
}

function statusTag(status) {
  if (status === 'completed') return 'success'
  if (status === 'auditing') return 'warning'
  return 'info'
}

function formatTime(iso) {
  if (!iso) return '—'
  return iso.replace('T', ' ').slice(0, 19)
}

// ── HTML 转义（防 XSS） ──
function escapeHtml(text) {
  const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }
  return text.replace(/[&<>"']/g, ch => map[ch])
}

// ── 渲染文本（段落换行，保留空行） ──
const renderedText = computed(() => {
  const text = contract.value?.parsed_text || ''
  return text
    .split('\n')
    .map(p => (p.trim() ? `<p>${escapeHtml(p)}</p>` : '<p><br></p>'))
    .join('')
})

// ── 获取合同详情 ──
async function fetchDetail() {
  loading.value = true
  error.value = ''
  try {
    const res = await getContractDetail(route.params.id)
    contract.value = res.data
    fetchFeedback()
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
const pdfError = ref('')
const pdfCanvasRef = ref(null)
const feedbackRef = ref(null)
const loadedFeedbacks = ref([])
let pdfDoc = null
let pdfLoadingTask = null

const pageSummary = ref('点击页码浏览各页合同条款')
const pageSummaries = {
  1: '保密协议定义条款：明确保密信息的范围，包括技术资料、商业计划、客户信息、财务数据等，采用概括+列举的定义方式。',
  2: '保密义务条款：乙方不得向第三方披露、仅可用于约定目的、需采取不低于保护自身同类信息的注意程度。',
  3: '保密期限条款：保密义务有效期 5 年，自保密信息披露之日起计算。',
  4: '违约责任与争议解决：违约责任条款约定赔偿计算方式，争议提交甲方所在地法院管辖。',
}

// ── 风险详情（从 API 获取）──
const riskItems = ref([])

// 风险等级映射：API 英文 → 前端中文
const levelMap = { high: '高风险', medium: '中风险', low: '低风险' }

async function fetchAuditResult() {
  const id = route.params.id
  if (!id) return
  try {
    const res = await getAuditResult(id)
    riskItems.value = (res.data?.items || []).map(r => ({
      id: r.id,
      risk_level: r.risk_level,
      risk_type: r.risk_type,
      clause_text: r.clause_text,
      level: levelMap[r.risk_level] || r.risk_level,
      category: r.risk_type,
      clause: r.clause_text,
      suggestion: r.suggestion,
      reason: r.reason,
      confidence: r.confidence,
      detection_method: r.detection_method,
    }))
  } catch {
    // 无风险详情时静默，保持空列表
    riskItems.value = []
  }
}

// ── 风险详情汇总 ──
const riskSummary = computed(() => {
  const high = riskItems.value.filter(r => r.level === '高风险').length
  const mid = riskItems.value.filter(r => r.level === '中风险').length
  const low = riskItems.value.filter(r => r.level === '低风险').length
  return { total: riskItems.value.length, high, mid, low }
})

function levelTag(level) {
  if (level === '高风险') return 'danger'
  if (level === '中风险') return 'warning'
  return 'success'
}

// ── 触发审核 ──
const auditing = ref(false)
async function handleTriggerAudit() {
  auditing.value = true
  try {
    await triggerAudit(route.params.id)
    contract.value.status = 'auditing'
    // 审核完成后拉结果
    await fetchAuditResult()
    await fetchDetail()
    ElMessage.success('审核完成')
  } catch (e) {
    ElMessage.error('审核触发失败')
  } finally {
    auditing.value = false
  }
}

// ── 加载已有反馈 ──
async function fetchFeedback() {
  const cid = contract.value?.id
  if (!cid) return
  try {
    const res = await getFeedback(cid)
    loadedFeedbacks.value = res.data?.items || []
  } catch {
    loadedFeedbacks.value = []
  }
}

// ── 反馈标注回调 ──
async function onFeedback(payload) {
  try {
    await submitFeedback(payload)
    ElMessage.success('反馈已保存')
    // 刷新反馈列表确保状态同步
    fetchFeedback()
  } catch (e) {
    ElMessage.error('反馈提交失败：' + (e.response?.data?.detail || e.message))
  }
}

// ── 撤销反馈回调 ──
function onFeedbackUndo(payload) {
  // TODO: 等 C 角色实现 DELETE /api/feedback/{id} 后对接
  console.log('[FeedbackPanel] 撤销反馈:', payload)
  ElMessage.info('已撤销（本地状态）')
}

// ── pdf.js ──
async function loadPdf() {
  pdfjsLib.GlobalWorkerOptions.workerSrc = pdfjsWorker
  try {
    pdfLoadingTask = pdfjsLib.getDocument({ url: '/test.pdf' })
    pdfDoc = await pdfLoadingTask.promise
    totalPages.value = pdfDoc.numPages
    renderPage(currentPage.value)
  } catch (e) {
    pdfError.value = 'PDF 加载失败：' + e.message + '（请确保 frontend/public/test.pdf 存在）'
    console.warn('PDF 加载失败:', e.message)
  }
}

async function renderPage(pageNum) {
  if (!pdfDoc) return
  currentPage.value = pageNum

  pageSummary.value = pageSummaries[pageNum] || `第 ${pageNum} 页（暂无条款摘要）`

  const page = await pdfDoc.getPage(pageNum)
  const canvas = pdfCanvasRef.value
  if (!canvas) return

  const viewport = page.getViewport({ scale: 1.0 })
  const containerWidth = canvas.parentElement.clientWidth - 2
  const scale = Math.min(containerWidth / viewport.width, 1.5)
  const scaledViewport = page.getViewport({ scale })

  canvas.width = scaledViewport.width
  canvas.height = scaledViewport.height

  const ctx = canvas.getContext('2d')
  await page.render({ canvasContext: ctx, viewport: scaledViewport }).promise
}

function goToPage(p) {
  if (p < 1 || p > totalPages.value) return
  renderPage(p)
}

// ── 生命周期 ──
onMounted(() => {
  fetchDetail()
  fetchAuditResult()
  loadPdf()
})

// ── 导航到风险详情/报告页（列表页）──
function goToAuditResult() {
  router.push(`/audit/result/${route.params.id}`)
}

function goToAuditReport() {
  router.push(`/audit/report/${route.params.id}`)
}

// 路由参数变化时重新拉数据（组件复用场景）
watch(() => route.params.id, () => {
  fetchDetail()
  fetchAuditResult()
})

// 合同数据就绪后加载反馈
watch(() => contract.value?.id, (cid) => {
  if (cid) fetchFeedback()
})

onUnmounted(() => {
  // pdfjs-dist v6: destroy() 在 PDFDocumentLoadingTask 上，不在 PDFDocumentProxy
  pdfLoadingTask?.destroy()
  pdfDoc = null
  pdfLoadingTask = null
})
</script>

<style scoped>
.page-container {
  padding: 24px;
  max-width: 1200px;
  margin: 0 auto;
}

.loading-state {
  padding: 40px 0;
}

.error-state {
  padding: 60px 0;
}

.meta-descriptions {
  margin-bottom: 20px;
}

/* 左右两列固定等高：CSS 变量统一基准，避免 calc 漂移 */
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

.audit-alert {
  margin-bottom: 16px;
}

.audit-placeholder {
  padding: 60px 0;
}

.audit-full-link {
  margin-top: 12px;
}

.report-desc {
  margin-bottom: 16px;
}

.report-btn {
  margin-top: 8px;
}

.tab-content {
  height: calc(var(--row-height) - 55px);
  min-height: 540px;
  overflow-y: auto;
  padding: 8px 0;
  line-height: 1.8;
  color: #303133;
}

.tab-content h4 {
  margin: 0 0 12px 0;
}

.tab-content h5 {
  margin: 16px 0 8px 0;
}

.pdf-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.pdf-viewer {
  text-align: center;
  min-height: 200px;
  background: #f5f7fa;
  border-radius: 4px;
  padding: 4px;
}

.pdf-canvas {
  border: 1px solid #e4e7ed;
  width: 100%;
}

.pdf-error {
  padding: 40px;
  text-align: center;
  color: #999;
}

.pdf-error p {
  margin-top: 8px;
}

.page-nav {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin-top: 12px;
  flex-wrap: wrap;
}

.page-buttons {
  display: flex;
  gap: 4px;
  max-width: 260px;
  overflow-x: auto;
  padding: 2px 0;
}

.text-sidebar {
  padding: 0 4px;
}

.sidebar-divider {
  margin: 12px 0;
}

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
