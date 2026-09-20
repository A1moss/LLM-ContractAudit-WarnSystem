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
          <el-button @click="$router.push('/audit/result')">返回审核结果列表</el-button>
        </template>
      </el-result>
    </div>

    <!-- 空状态（无风险） -->
    <div v-else-if="riskItems.length === 0 && !loading" class="empty-state">
      <el-empty description="未检测到风险项">
        <el-button @click="$router.push('/audit/result')">返回列表</el-button>
      </el-empty>
    </div>

    <template v-else>
      <!-- 页头 -->
      <div class="a24-page-header">
        <div>
          <div class="crumb">首页 / 审核中心 / 审核历史 / <b>风险明细</b></div>
          <div class="title-row">
            <span class="icon"><el-icon><Search /></el-icon></span>
            <h3>{{ contractName }}</h3>
          </div>
          <div class="desc">风险明细 · 共 {{ riskSummary.total }} 条风险</div>
        </div>
        <div class="actions">
          <el-button text @click="$router.push('/audit/result')">
            <el-icon><ArrowLeft /></el-icon> 返回列表
          </el-button>
          <el-button-group>
            <el-button type="primary" @click="$router.push(`/contracts/${contractId}`)">查看合同详情</el-button>
            <el-button type="primary" plain @click="$router.push(`/audit/report/${contractId}`)">查看审核报告</el-button>
          </el-button-group>
        </div>
      </div>

      <!-- 统计卡 -->
      <div class="a24-stat-grid">
        <div class="a24-stat-card">
          <span class="ic" style="background:#E8EBF9">📋</span>
          <div><div class="n">{{ riskSummary.total }}</div><div class="l">风险总数</div></div>
        </div>
        <div class="a24-stat-card">
          <span class="ic" style="background:#FDECEC">🔴</span>
          <div><div class="n" style="color:#E60012">{{ riskSummary.high }}</div><div class="l">高风险</div></div>
        </div>
        <div class="a24-stat-card">
          <span class="ic" style="background:#FEF3E2">🟠</span>
          <div><div class="n" style="color:#C77A12">{{ riskSummary.mid }}</div><div class="l">中风险</div></div>
        </div>
        <div class="a24-stat-card">
          <span class="ic" style="background:#E8F7EE">🟢</span>
          <div><div class="n" style="color:#1E9E54">{{ riskSummary.low }}</div><div class="l">低风险</div></div>
        </div>
      </div>

      <!-- 风险列表 -->
      <el-card shadow="hover" class="risk-card">
        <template #header>
          <span>风险明细</span>
          <el-tag v-if="!hasCurrentResult" type="danger" size="small" style="margin-left: 8px">已驳回审核结果</el-tag>
        </template>
        <el-alert v-if="!hasCurrentResult" title="⚠ 上次审核已被驳回，请重新审核（以下为已驳回历史结果）" type="error" show-icon :closable="false" style="margin-bottom: 12px" />
        <el-table ref="riskTableRef" :data="riskItems" stripe border>
          <el-table-column prop="level" label="等级" width="100">
            <template #default="{ row }">
              <el-tag
                :type="row.level === '高风险' ? 'danger' : row.level === '中风险' ? 'warning' : 'success'"
                size="small"
              >{{ row.level }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="type" label="风险类型" width="130" />
          <!-- 「原文片段 / 判定理由」：多行显示 + 默认限高 + 超出可展开。
               不再使用 show-overflow-tooltip（它命中 `.cell.el-tooltip{white-space:nowrap}`
               把整段压成一行省略号）；改为按**最大高度**折叠，不做字符截断：
               内容本身始终完整渲染在 DOM 里（点击展开即可看全文，数据未被裁剪）。 -->
          <el-table-column prop="clause" label="原文片段" min-width="180">
            <template #default="{ row }">
              <div
                class="rp-collapse"
                :class="{ 'is-collapsed': isCollapsed(row, 'clause') }"
                :style="isCollapsed(row, 'clause') ? { maxHeight: DEFAULT_MAX_HEIGHT + 'px' } : null"
              >
                <div class="rp-collapse-body">
                  {{ row.clause_text }}
                </div>
              </div>
              <el-button
                v-if="canCollapse(row, 'clause')"
                link
                type="primary"
                size="small"
                class="rp-toggle"
                @click="toggleCollapse(row, 'clause')"
              >{{ isCollapsed(row, 'clause') ? '展开' : '收起' }}</el-button>
            </template>
          </el-table-column>
          <el-table-column prop="reason" label="判定理由" min-width="180">
            <template #default="{ row }">
              <div
                class="rp-collapse"
                :class="{ 'is-collapsed': isCollapsed(row, 'reason') }"
                :style="isCollapsed(row, 'reason') ? { maxHeight: DEFAULT_MAX_HEIGHT + 'px' } : null"
              >
                <div class="rp-collapse-body">
                  {{ row.reason }}
                </div>
              </div>
              <el-button
                v-if="canCollapse(row, 'reason')"
                link
                type="primary"
                size="small"
                class="rp-toggle"
                @click="toggleCollapse(row, 'reason')"
              >{{ isCollapsed(row, 'reason') ? '展开' : '收起' }}</el-button>
            </template>
          </el-table-column>
          <el-table-column label="建议" min-width="250">
            <template #default="{ row }">
              <div v-if="row.risk_description" class="rec-desc">{{ row.risk_description }}</div>
              <div class="rec-suggestion">{{ row.suggestion }}</div>
              <div v-if="row.example" class="rec-example">{{ row.example }}</div>
              <div v-if="row.legal_basis" class="rec-legal">法律依据：{{ row.legal_basis }}</div>
              <div v-if="row.grounding_warning" class="rec-warning">⚠ 该建议含未经证据核实的数值，请结合合同原文确认。</div>
            </template>
          </el-table-column>
          <el-table-column prop="confidence" label="置信度" min-width="150">
            <template #default="{ row }">
              <!-- 置信度单元格：进度条 + 百分比自成一行（不被压缩、不换行），
                   与右侧 fixed="right" 操作列之间保留余量（见 <style> 的 .rp-conf-* 说明） -->
              <div class="rp-conf-cell">
                <el-progress
                  :percentage="Math.round((row.confidence || 0) * 100)"
                  :color="row.confidence >= 0.7 ? '#67C23A' : row.confidence >= 0.5 ? '#E6A23C' : '#F56C6C'"
                  :stroke-width="6"
                />
              </div>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="130" align="center" fixed="right">
            <template #default="{ row }">
              <el-button type="warning" size="small" @click="goRevise(row)">去修改合同</el-button>
            </template>
          </el-table-column>
        </el-table>

        <!-- 反馈标注（紧凑模式）：只保留 等级 + 风险码/名称 + 一句摘要 + 操作按钮。
             完整风险详情由上方「风险明细」表格负责展示，避免同一批风险完整渲染两遍。 -->
        <FeedbackPanel
          ref="feedbackRef"
          compact
          :risk-items="riskItems"
          :contract-id="contractId"
          :loaded-feedbacks="loadedFeedbacks"
          @feedback-change="onFeedback"
          @feedback-undo="onFeedbackUndo"
        />
      </el-card>
    </template>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Search } from '@element-plus/icons-vue'
import { getAuditResult, getContractDetail, getFeedback, deleteFeedback } from '../api/contract.js'
import { useFeedback } from '../composables/useFeedback.js'
import FeedbackPanel from '../components/FeedbackPanel.vue'

const route = useRoute()
const router = useRouter()
const contractId = computed(() => route.params.contractId || '')

// ============================================================================
// 「原文片段 / 判定理由」折叠展示（纯展示层：只限制**默认显示高度**，不裁剪数据）
// ----------------------------------------------------------------------------
// 设计要点：
//   · 内容始终以完整文本渲染进 DOM（模板里直接输出 row.clause_text / row.reason），
//     JS 不做任何 substring/slice —— 点「展开」看到的就是后端返回的完整原文；
//   · 默认只限制**最大高度**（DEFAULT_MAX_HEIGHT ≈ 4 行），超出部分由 CSS 隐藏；
//   · 是否显示「展开」按**实际渲染高度**判断（scrollHeight 与阈值比较），
//     而不是按字符数猜，短内容因此不会出现多余的「展开」按钮；
//   · 展开状态以 `字段:行id` 为 key 逐个记录 ⇒ 两列独立、每行独立，互不影响。
// ============================================================================
const COLLAPSE_FIELDS = ['clause', 'reason']
// 默认最大展示高度：.cell 的 line-height 为 23px，92px ≈ 4 行（与 .rp-collapse 一致）
const DEFAULT_MAX_HEIGHT = 92

/** 已展开的单元格，key = `${field}:${rowId}`；用普通对象保证 Vue 的变更追踪可靠 */
const expandedCells = reactive({})
/** 内容确实超高的单元格（只有这些才显示「展开/收起」） */
const overflows = reactive({})
/** 风险明细表格根元素（测量时用它定位当前活动的单元格元素） */
const riskTableRef = ref(null)

const cellKey = (field, rowId) => `${field}:${rowId}`
const isCollapsed = (row, field) => !!overflows[cellKey(field, row.id)]
  && !expandedCells[cellKey(field, row.id)]
const canCollapse = (row, field) => !!overflows[cellKey(field, row.id)]

// 列索引：0 等级 / 1 风险类型 / 2 原文片段 / 3 判定理由 / 4 建议 / 5 置信度 / 6 操作
const COLLAPSE_COL_INDEX = { clause: 2, reason: 3 }

/**
 * 测量各单元格内容高度，标记哪些超过默认展示高度（决定是否显示「展开」）。
 *
 * 关键点：**不能**缓存元素引用。el-table 在拿到数据后会重建表格行，
 * 挂载期拿到的元素会变成脱离文档的旧节点（scrollHeight 恒为 0），
 * 因此这里每次测量都从当前表格里重新定位活动元素。
 */
function measureCollapse() {
  // el-table 是组件实例，根 DOM 在其 $el 上
  const comp = riskTableRef.value
  const root = comp && (comp.$el || comp)
  if (!root || typeof root.querySelectorAll !== 'function') return
  const rows = root.querySelectorAll('.el-table__body tbody tr')
  rows.forEach((tr, rowIdx) => {
    const row = riskItems.value[rowIdx]
    if (!row) return
    const tds = tr.querySelectorAll('td')
    COLLAPSE_FIELDS.forEach((field) => {
      const td = tds[COLLAPSE_COL_INDEX[field]]
      const el = td ? td.querySelector('.rp-collapse-body') : null
      if (!el) return
      const key = cellKey(field, row.id)
      // 容差一行（23px）：92px 是 4 行，内容落在 4~5 行之间时也给出「展开」入口，
      // 避免"折叠后仍有第 5 行被切掉却看不到全文"的情况。
      if (el.scrollHeight > DEFAULT_MAX_HEIGHT + 23) overflows[key] = true
      else delete overflows[key]
    })
  })
}

function toggleCollapse(row, field) {
  const key = cellKey(field, row.id)
  if (expandedCells[key]) delete expandedCells[key]
  else expandedCells[key] = true
  // 展开/收起会改变该单元格高度，需重新测量以正确显示/隐藏「展开」按钮
  scheduleMeasure()
}

// 说明：el-table 的行布局（列宽 / 换行）在挂载回调里尚未完成，因此统一用
// requestAnimationFrame 等一帧布局完成后再测；窗口尺寸变化会改变列宽与换行，需要重测。
let rafId = null
function scheduleMeasure() {
  if (typeof window === 'undefined') return
  if (rafId) cancelAnimationFrame(rafId)
  rafId = requestAnimationFrame(() => {
    rafId = null
    measureCollapse()
  })
}

if (typeof window !== 'undefined') {
  window.addEventListener('resize', scheduleMeasure)
  onBeforeUnmount(() => {
    window.removeEventListener('resize', scheduleMeasure)
    if (rafId) cancelAnimationFrame(rafId)
  })
}

// ── 反馈标注 ──
const { feedbackRef, onFeedback } = useFeedback(contractId)

// 反馈回显与撤销（prop 传递方式，与 ContractDetail 一致，避免 restore 依赖 ref 挂载时序，BUG-031）
const loadedFeedbacks = ref([])

async function fetchFeedback() {
  const id = contractId.value
  if (!id) return
  try {
    const res = await getFeedback(id)
    loadedFeedbacks.value = res.data?.items || []
  } catch { loadedFeedbacks.value = [] }
}

async function onFeedbackUndo(payload) {
  try {
    if (payload.feedback_id) {
      await deleteFeedback(payload.feedback_id)
      fetchFeedback()
    }
    ElMessage.info('已撤销')
  } catch (e) {
    ElMessage.error('撤销失败：' + (e.response?.data?.detail || e.message))
  }
}

const contractName = ref('')
const riskItems = ref([])
// 是否存在当前有效审核结果（BUG-028）
const hasCurrentResult = ref(true)
const loading = ref(true)
const error = ref('')

const levelMap = { high: '高风险', medium: '中风险', low: '低风险' }

const riskSummary = computed(() => {
  const high = riskItems.value.filter(r => r.level === '高风险').length
  const mid = riskItems.value.filter(r => r.level === '中风险').length
  const low = riskItems.value.filter(r => r.level === '低风险').length
  return { total: riskItems.value.length, high, mid, low }
})

// 表格渲染后重新测量（riskItems 变化 ⇒ DOM 重建 ⇒ rAF 等布局完成后重测）
watch(riskItems, () => scheduleMeasure(), { flush: 'post' })

// ── 风险预警 → 修改合同：只传「来源 + 风险 ID」的轻量上下文 ──
// 不把整份风险对象塞进 storage：进入 ContractDetail 后由它自己重新请求 /audit-result
// 取得完整风险数据（避免数据过期、避免重复存储）。
const REVISE_SOURCE_KEY = 'a24_revise_source'
function goRevise(row) {
  try {
    sessionStorage.setItem(REVISE_SOURCE_KEY, JSON.stringify({ source: 'risk', risk_id: row.id }))
  } catch { /* 隐私模式等写入失败时仍跳转，用户可在修改合同里手动选条款 */ }
  router.push(`/contracts/${contractId.value}`)
}

async function fetchContractName(id) {
  try {
    const res = await getContractDetail(id)
    contractName.value = res.data?.file_name || ''
  } catch { contractName.value = '' }
}

async function fetchResult() {
  const id = contractId.value
  if (!id) {
    error.value = '缺少合同 ID 参数'
    loading.value = false
    return
  }
  fetchContractName(id)
  try {
    const res = await getAuditResult(id)
    hasCurrentResult.value = res.data?.has_current_result ?? true
    riskItems.value = (res.data?.items || []).map(r => ({
      id: r.id,
      level: levelMap[r.risk_level] || r.risk_level,
      risk_type: r.risk_type,
      type: r.risk_type,
      clause_text: r.clause_text,
      clause: r.clause_text,
      reason: r.reason,
      suggestion: r.suggestion,
      confidence: r.confidence,
      detection_method: r.detection_method,
      // v6.5 建议层结构化字段（BUG-021）
      recommendation: r.recommendation || {},
      risk_description: r.recommendation?.risk_description || '',
      example: r.recommendation?.example || '',
      legal_basis: r.recommendation?.legal_basis || '',
      grounding_warning: r.recommendation?.grounding?.passed === false,
    }))
  } catch (e) {
    error.value = '加载审核结果失败'
    console.warn('审核结果加载失败:', e)
  } finally {
    loading.value = false
  }
  // 模板渲染完成后测量「原文片段 / 判定理由」的真实高度，决定哪些需要「展开」
  nextTick().then(measureCollapse)
}

watch(contractId, (newId, oldId) => {
  if (newId && newId !== oldId) {
    loading.value = true
    error.value = ''
    riskItems.value = []
    fetchResult().then(() => fetchFeedback())
  }
})

onMounted(() => {
  fetchResult().then(() => fetchFeedback())
})
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



.risk-card {
  margin-top: 20px;
}

.suffix-red { color: #F56C6C; font-size: 14px; }
.suffix-orange { color: #E6A23C; font-size: 14px; }
.suffix-green { color: #67C23A; font-size: 14px; }

.rec-desc { font-size: 13px; color: #606266; margin-bottom: 2px; }
.rec-suggestion { font-size: 13px; color: #303133; }
.rec-example { font-size: 12px; color: #909399; margin-top: 4px; }
.rec-legal { font-size: 12px; color: #909399; margin-top: 2px; }
.rec-warning { font-size: 12px; color: #E6A23C; margin-top: 4px; }

/* ============================================================
   「风险明细」表格展示修复（只影响本页这一张表）
   ============================================================ */

/* ── 修复 1：原文片段 / 判定理由的展示密度（多行 + 默认限高 + 可展开）──
   背景：这两列原先带 `show-overflow-tooltip`，element-plus 会给单元格加
        `.el-table .cell.el-tooltip{white-space:nowrap;min-width:50px}`，
        叠加 `.cell{overflow:hidden;text-overflow:ellipsis}` 后整段被压成**一行**省略。
   现在改为：
        · 内容完整渲染（多行、可换行，长串用 anywhere 断行）；
        · 默认只限制**最大高度**（约 4 行 = 92px，与脚本里的 DEFAULT_MAX_HEIGHT 对应），
          超出部分 CSS 隐藏 —— 不做字符截断，数据始终完整；
        · 由脚本按实际渲染高度判断是否显示「展开 / 收起」，短内容不出现按钮。 */
.rp-collapse {
  position: relative;
}
.rp-collapse.is-collapsed {
  overflow: hidden;          /* 仅隐藏溢出高度，内容仍在 DOM 中 */
}
.rp-collapse-body {
  white-space: normal;
  overflow-wrap: anywhere;   /* 长串（条款编号/无空格长文本）也能断行，不撑破列宽 */
  word-break: break-word;
}
.rp-toggle {
  margin-top: 2px;
  padding: 0;
  height: auto;
  font-size: 12px;
  line-height: 18px;
}

/* ── 修复 2：置信度列（进度条 + 百分比）稳定布局，且与操作列不再互相挤压 ──
   根因（headless 实测真实 el-table 几何）：
     ① 列宽合计原为 100+130+220+220+280+120+130 = 1200px，而 `.page-container`
        (max-width:1200 + padding:24px) 内 `.el-card__body` 的可用宽度**恒为 1198px**，
        合计始终超出（1280 视口下超出 35px）⇒ 表格横向滚动；
        而「操作」列是 fixed="right"（position:sticky + 实色背景），会**压住**滚动
        内容最右侧的像素。
     ② 置信度列内容盒 = 120 − 24（.cell 的 padding:0 12px）= 96px，恰好等于
        「进度条 41px + 文字块 min-width:50px + margin-left:5px」的最小需求，余量为 0，
        于是百分比的「%」正好落在固定列的覆盖区内，被「去修改合同」按钮左边缘遮住。

   修复：① 列宽重新分配，合计 1120px < 1198px ⇒ 不再横向滚动、固定列不再压住任何内容：
            原文片段 220→180、判定理由 220→180（改为完整换行后，宽一点/窄一点都只影响行高）、
            建议 280→250（仍保留展示空间）；
        ② 置信度列 120→150，并在单元格内用显式 flex 行排布：进度条占剩余空间、
            百分比文字不压缩不换行 ⇒ 90% / 88% / 72% 始终完整显示。 */
.rp-conf-cell .el-progress {
  display: flex;
  align-items: center;
  gap: 8px;                 /* 进度条与百分比之间的明确间距 */
}
.rp-conf-cell .el-progress-bar {
  flex: 1 1 auto;
  min-width: 0;             /* 允许进度条先让位，而不是把百分比文字挤掉 */
}
.rp-conf-cell .el-progress__text {
  flex: 0 0 auto;
  min-width: 0;             /* 覆盖组件默认的 50px，避免占用额外宽度 */
  margin-left: 0;           /* 间距统一由上面的 gap 提供，避免重复计算 */
  text-align: right;
  font-size: 13px;
  white-space: nowrap;      /* 百分比（含 %）永不换行、不被截断 */
}
</style>
