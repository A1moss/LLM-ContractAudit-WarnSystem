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
        <el-descriptions-item label="页数">{{ pdfTotalPages || '—' }} 页</el-descriptions-item>
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
                  <el-button type="primary" size="small" class="audit-full-link" @click="goToAuditResult">全屏查看结果</el-button>
                  <FeedbackPanel ref="feedbackRef" :risk-items="riskItems" :contract-id="contract?.id" :loaded-feedbacks="loadedFeedbacks" @feedback-change="onFeedback" @feedback-undo="onFeedbackUndo" />
                </template>
                <el-empty v-else description="审核完成，未检测到风险" />
              </div>
            </el-tab-pane>
            <el-tab-pane label="条款比对" name="compare"><div class="tab-content"><el-empty description="标准条款比对结果将在审核完成后生成" /></div></el-tab-pane>
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

        <!-- 右侧预览 -->
        <el-col :span="10" class="detail-right">
          <el-card shadow="hover">
            <template #header>
              <div class="pdf-header">
                <span>合同原文</span>
                <el-tag v-if="!isPdf" size="small" type="info">DOCX</el-tag>
                <el-tag v-else-if="!pdfError && pdfTotalPages > 0" size="small">第 {{ pdfCurrentPage }} / {{ pdfTotalPages }} 页</el-tag>
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
                <div ref="docxContainerRef" :style="{ width: CONTENT_W + 'px', zoom: docxZoom }"></div>
              </div>
            </template>

            <!-- PDF -->
            <template v-else>
              <div v-if="pdfError" class="pdf-error"><el-icon :size="32"><Warning /></el-icon><p>{{ pdfError }}</p></div>
              <template v-else>
                <div class="pdf-viewer"><canvas ref="pdfCanvasRef" class="pdf-canvas"></canvas></div>
                <div class="page-nav">
                  <el-button size="small" :disabled="pdfCurrentPage <= 1" @click="goToPdfPage(pdfCurrentPage - 1)"><el-icon><ArrowLeft /></el-icon></el-button>
                  <span class="page-buttons">
                    <el-button v-for="p in pdfTotalPages" :key="p" :type="p === pdfCurrentPage ? 'primary' : 'default'" size="small" @click="goToPdfPage(p)">{{ p }}</el-button>
                  </span>
                  <el-button size="small" :disabled="pdfCurrentPage >= pdfTotalPages" @click="goToPdfPage(pdfCurrentPage + 1)"><el-icon><ArrowRight /></el-icon></el-button>
                </div>
              </template>
            </template>
          </el-card>
        </el-col>
      </el-row>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Warning, ArrowLeft, ArrowRight, Loading, ZoomIn, ZoomOut } from '@element-plus/icons-vue'
import FeedbackPanel from '../components/FeedbackPanel.vue'
import { ElMessage } from 'element-plus'
import { getContractDetail, getAuditResult, triggerAudit, submitFeedback, getFeedback } from '../api/contract.js'
import { formatTime } from '../utils/format.js'
import * as pdfjsLib from 'pdfjs-dist'
import pdfjsWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { renderAsync } from 'docx-preview'

const route = useRoute()
const router = useRouter()

// ── 合同 ID ──
const contractId = computed(() => route.params.id)

// ── 反馈标注 ──
const feedbackRef = ref(null)

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
  if (status === 'auditing' || status === 'parsing') return 'warning'
  return 'info'
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
const isPdf = ref(null)
let cachedFileData = null

async function fetchAndDetect(id) {
  const token = localStorage.getItem('token')
  const res = await fetch(`/api/contracts/${id}/file`,{headers:token?{Authorization:`Bearer ${token}`}:{}})
  if (!res.ok) throw new Error(`服务器返回 ${res.status}`)
  const buf = await res.arrayBuffer()
  const head = new Uint8Array(buf.slice(0, 4))
  if (head[0]===0x25 && head[1]===0x50 && head[2]===0x44 && head[3]===0x46) { isPdf.value=true }
  else if (head[0]===0x50 && head[1]===0x4B) { isPdf.value=false }
  else { isPdf.value = (contract.value?.file_name||'').toLowerCase().endsWith('.pdf') }
  cachedFileData = buf
}
const { feedbackRef, loadFeedback } = useFeedback(computed(() => route.params.id))
const loadedFeedbacks = ref([])
const auditing = ref(false)

// ── PDF ──
const pdfCanvasRef = ref(null)
const pdfCurrentPage = ref(1)
const pdfTotalPages = ref(0)
const pdfError = ref('')
let pdfDoc = null
let pdfLoadingTask = null

// ── 风险 ──
const riskItems = ref([])
const levelMap = { high:'高风险', medium:'中风险', low:'低风险' }
const riskSummary = computed(() => {
  const h=riskItems.value.filter(r=>r.level==='高风险').length
  const m=riskItems.value.filter(r=>r.level==='中风险').length
  const l=riskItems.value.filter(r=>r.level==='低风险').length
  return {total:riskItems.value.length,high:h,mid:m,low:l}
})

const typeMap = {purchase:'采购合同',sales:'销售合同',nda:'保密协议 (NDA)',outsourcing:'服务外包合同',employment:'劳动合同',other:'其他合同'}
function typeLabel(t){return typeMap[t]||t||'未分类'}
function statusLabel(s){const m={uploaded:'已上传',parsed:'已解析',auditing:'审核中',completed:'审核完成'};return m[s]||s||'未知'}
function statusTag(s){return s==='completed'?'success':s==='auditing'?'warning':'info'}
function levelTag(l){return l==='高风险'?'danger':l==='中风险'?'warning':'success'}
function escapeHtml(t){return t.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c])}
const renderedText = computed(()=>(contract.value?.parsed_text||'').split('\n').map(p=>p.trim()?`<p>${escapeHtml(p)}</p>`:'<p><br></p>').join(''))

// ── API ──
async function fetchDetail(){loading.value=true;error.value='';try{contract.value=(await getContractDetail(route.params.id)).data}catch(e){error.value='无法加载合同详情';console.warn(e)}finally{loading.value=false}}
async function fetchAuditResult(){try{const r=await getAuditResult(route.params.id);riskItems.value=(r.data?.items||[]).map(x=>({...x,level:levelMap[x.risk_level]||x.risk_level,category:x.risk_type,clause:x.clause_text}))}catch{riskItems.value=[]}}
async function fetchFeedback(){const cid=contract.value?.id;if(!cid)return;try{loadedFeedbacks.value=(await getFeedback(cid)).data?.items||[]}catch{loadedFeedbacks.value=[]}}
async function handleTriggerAudit(){auditing.value=true;try{await triggerAudit(route.params.id);contract.value.status='auditing';await fetchAuditResult();await fetchDetail();ElMessage.success('审核完成')}catch{ElMessage.error('审核触发失败')}finally{auditing.value=false}}
async function onFeedback(p){try{await submitFeedback(p);ElMessage.success('反馈已保存');fetchFeedback()}catch(e){ElMessage.error('反馈提交失败：'+(e.response?.data?.detail||e.message))}}
function onFeedbackUndo(p){console.log('[FeedbackPanel] 撤销:',p);ElMessage.info('已撤销（本地状态）')}

// ── DOCX ──
const CONTENT_W = 602
const docxContainerRef = ref(null)
const docxLoading = ref(false)
const docxError = ref('')
const docxZoom = ref(1)

function applyZoom(z) {
  docxZoom.value = z
  if (docxContainerRef.value) docxContainerRef.value.style.zoom = z
}

async function loadDocx() {
  if (isPdf.value !== false) return
  docxLoading.value = true; docxError.value = ''
  try {
    await renderAsync(
      new Blob([cachedFileData],{type:'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}),
      docxContainerRef.value,
      undefined,
      {className:'docx-page'}
    )
    // auto-fit — leave 16px for scrollbar
    const vw = (docxContainerRef.value?.parentElement?.clientWidth || 0) - 16
    applyZoom(vw > 0 ? Math.round(Math.max(0.3, vw / CONTENT_W) * 10) / 10 : 0.8)
  } catch (e) {
    docxError.value = 'DOCX 解析失败：' + (e.message || '未知错误')
  } finally { docxLoading.value = false }
}

function zoomDocx(delta) {
  applyZoom(Math.round(Math.max(0.3, Math.min(2, docxZoom.value + delta)) * 100) / 100)
}

function resetDocxZoom() {
  const vw = (docxContainerRef.value?.parentElement?.clientWidth || 0) - 16
  applyZoom(vw > 0 ? Math.round(Math.max(0.3, vw / CONTENT_W) * 10) / 10 : 0.8)
}

// ── PDF ──
async function loadPdf() {
  if (isPdf.value !== true || !cachedFileData) return
  pdfjsLib.GlobalWorkerOptions.workerSrc = pdfjsWorker
  pdfError.value = ''
  try {
    pdfLoadingTask = pdfjsLib.getDocument({data: cachedFileData.slice()})
    pdfDoc = await pdfLoadingTask.promise
    pdfTotalPages.value = pdfDoc.numPages
    await nextTick()
    renderPdfPage(1)
  } catch(e) { pdfError.value = 'PDF 加载失败：' + (e.message || '文件格式异常'); console.warn(e) }
}
async function renderPdfPage(n) {
  if (!pdfDoc) return; pdfCurrentPage.value = n
  const c = pdfCanvasRef.value; if (!c) return
  const p = await pdfDoc.getPage(n)
  const v = p.getViewport({scale:1})
  const s = Math.min((c.parentElement.clientWidth - 2) / v.width, 1.5)
  const sv = p.getViewport({scale:s})
  c.width = sv.width; c.height = sv.height
  await p.render({canvasContext:c.getContext('2d'),viewport:sv}).promise
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
watch(() => contract.value?.id, cid => { if (cid) fetchFeedback() })
watch(() => route.params.id, () => { pdfLoadingTask?.destroy(); pdfLoadingTask = null; pdfDoc = null; cachedFileData = null; fetchDetail(); fetchAuditResult() })
onUnmounted(() => { pdfLoadingTask?.destroy(); pdfDoc = null; pdfLoadingTask = null })
</script>

<style scoped>
.page-container { padding:24px; max-width:1200px; margin:0 auto }
.loading-state { padding:40px 0 }
.error-state { padding:60px 0 }
.meta-descriptions { margin-bottom:20px }
.detail-row { --row-height:calc(100vh - 200px); height:var(--row-height); min-height:600px; overflow:hidden }
.detail-right { height:100%; overflow-y:auto }

.tab-content { height:calc(var(--row-height) - 55px); min-height:540px; overflow-y:auto; padding:8px 0; line-height:1.8; color:#303133 }
.tab-content :deep(p) { margin:0 0 4px }

.pdf-header { display:flex; justify-content:space-between; align-items:center }
.pdf-viewer { text-align:center; min-height:200px; background:#f5f7fa; border-radius:4px; padding:4px }
.pdf-canvas { border:1px solid #e4e7ed; width:100% }
.pdf-error { padding:40px; text-align:center; color:#999 }
.pdf-error p { margin-top:8px }

.docx-status { padding:20px; text-align:center; color:#909399; font-size:13px }
.docx-status--err { color:#f56c6c }
.zoom-bar { display:flex; align-items:center; gap:6px; margin-bottom:8px }
.zoom-label { font-size:13px; color:#606266; min-width:40px; text-align:center }
.docx-viewer { height:calc(var(--row-height) - 90px); min-height:400px; overflow:auto; border:1px solid #e4e7ed; border-radius:4px; background:#fff; padding:4px; display:flex; justify-content:center }
.docx-viewer > div { flex-shrink:0 }
.docx-viewer :where(.docx-page-wrapper) { background:#fff }
.docx-viewer :where(section) { padding:24px 32px; background:#fff }

.page-nav { display:flex; align-items:center; justify-content:center; gap:8px; margin-top:12px; flex-wrap:wrap }
.page-buttons { display:flex; gap:4px; max-width:260px; overflow-x:auto; padding:2px 0 }

.audit-alert { margin-bottom:16px }
.audit-placeholder { padding:60px 0 }
.audit-full-link { margin-top:12px }
.report-desc { margin-bottom:16px }
.report-btn { margin-top:8px }
</style>
