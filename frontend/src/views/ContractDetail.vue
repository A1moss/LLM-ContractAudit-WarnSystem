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
      <el-row :gutter="20">
        <el-col :span="14">
          <el-tabs v-model="activeTab" type="border-card">
            <el-tab-pane label="原始文本" name="text">
              <div class="tab-content">
                <div v-if="contract.parsed_text" v-html="renderedText"></div>
                <el-empty v-else description="暂无解析文本" />
              </div>
            </el-tab-pane>

            <el-tab-pane label="审核结果" name="audit">
              <div class="tab-content">
                <el-alert title="共检测到 15 条风险，其中高风险 3 条、中风险 7 条、低风险 5 条" type="warning" show-icon :closable="false" class="audit-alert" />
                <el-table :data="riskItems" stripe size="small" max-height="400">
                  <el-table-column prop="level" label="等级" width="80">
                    <template #default="{ row }">
                      <el-tag :type="levelTag(row.level)" size="small">{{ row.level }}</el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column prop="category" label="风险类别" width="130" />
                  <el-table-column prop="clause" label="涉及条款" min-width="180" show-overflow-tooltip />
                  <el-table-column prop="suggestion" label="建议" min-width="200" show-overflow-tooltip />
                </el-table>
              </div>
            </el-tab-pane>

            <el-tab-pane label="条款比对" name="compare">
              <div class="tab-content">
                <el-empty description="标准条款比对结果将在审核完成后生成" />
              </div>
            </el-tab-pane>

            <el-tab-pane label="审核报告" name="report">
              <div class="tab-content">
                <el-empty description="完整审核报告请前往 审核报告 页面查看">
                  <el-button type="primary" @click="$router.push('/audit/report')">前往审核报告</el-button>
                </el-empty>
              </div>
            </el-tab-pane>
          </el-tabs>
        </el-col>

        <!-- 右侧 PDF 预览面板 -->
        <el-col :span="10">
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
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Warning, ArrowLeft, ArrowRight } from '@element-plus/icons-vue'
import { getContractDetail } from '../api/contract.js'
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

// ── 渲染文本（简单段落换行） ──
const renderedText = computed(() => {
  const text = contract.value?.parsed_text || ''
  return text
    .split('\n')
    .filter(p => p.trim())
    .map(p => `<p>${p}</p>`)
    .join('')
})

// ── 获取合同详情 ──
async function fetchDetail() {
  loading.value = true
  error.value = ''
  try {
    const res = await getContractDetail(route.params.id)
    contract.value = res.data
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
let pdfDoc = null

const pageSummary = ref('点击页码浏览各页合同条款')
const pageSummaries = {
  1: '保密协议定义条款：明确保密信息的范围，包括技术资料、商业计划、客户信息、财务数据等，采用概括+列举的定义方式。',
  2: '保密义务条款：乙方不得向第三方披露、仅可用于约定目的、需采取不低于保护自身同类信息的注意程度。',
  3: '保密期限条款：保密义务有效期 5 年，自保密信息披露之日起计算。',
  4: '违约责任与争议解决：违约责任条款约定赔偿计算方式，争议提交甲方所在地法院管辖。',
}

// ── 模拟审核结果（审核 API 上线后替换） ──
const riskItems = [
  { level: '高风险', category: 'R01 保密期限缺失', clause: '第三条 保密期限', suggestion: '建议明确保密期限的起算时间和终止条件' },
  { level: '高风险', category: 'R02 违约责任不明确', clause: '第四条 违约责任', suggestion: '建议约定违约金计算方式或赔偿上限' },
  { level: '高风险', category: 'R03 管辖条款不利', clause: '第五条 争议解决', suggestion: '争议管辖地对接收方不利，建议协商变更' },
  { level: '中风险', category: 'R04 保密范围过宽', clause: '第一条 定义', suggestion: '保密信息定义过于宽泛，建议限定具体范围' },
  { level: '中风险', category: 'R05 例外情形缺失', clause: '第一条 定义', suggestion: '建议增加保密信息的例外情形' },
  { level: '中风险', category: 'R06 返还义务缺失', clause: '第二条 义务', suggestion: '建议增加协议终止后保密信息返还/销毁条款' },
  { level: '中风险', category: 'R07 转许可限制', clause: '第二条 义务', suggestion: '建议明确禁止向关联方转许可' },
  { level: '低风险', category: 'R08 标题格式', clause: '全文', suggestion: '建议统一条款标题格式' },
  { level: '低风险', category: 'R09 签署信息', clause: '末页', suggestion: '签署栏缺少日期填写提示' },
]

function levelTag(level) {
  if (level === '高风险') return 'danger'
  if (level === '中风险') return 'warning'
  return 'success'
}

// ── pdf.js ──
async function loadPdf() {
  pdfjsLib.GlobalWorkerOptions.workerSrc = pdfjsWorker
  try {
    const task = pdfjsLib.getDocument({ url: '/test.pdf' })
    pdfDoc = await task.promise
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
  loadPdf()
})

onUnmounted(() => {
  pdfDoc?.destroy()
  pdfDoc = null
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

.audit-alert {
  margin-bottom: 16px;
}

.tab-content {
  min-height: 400px;
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
