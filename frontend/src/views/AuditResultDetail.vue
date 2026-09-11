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
        <el-table :data="riskItems" stripe border>
          <el-table-column prop="level" label="等级" width="100">
            <template #default="{ row }">
              <el-tag
                :type="row.level === '高风险' ? 'danger' : row.level === '中风险' ? 'warning' : 'success'"
                size="small"
              >{{ row.level }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="type" label="风险类型" width="130" />
          <el-table-column prop="clause" label="原文片段" min-width="220" show-overflow-tooltip />
          <el-table-column prop="reason" label="判定理由" min-width="220" show-overflow-tooltip />
          <el-table-column label="建议" min-width="280">
            <template #default="{ row }">
              <div v-if="row.risk_description" class="rec-desc">{{ row.risk_description }}</div>
              <div class="rec-suggestion">{{ row.suggestion }}</div>
              <div v-if="row.example" class="rec-example">{{ row.example }}</div>
              <div v-if="row.legal_basis" class="rec-legal">法律依据：{{ row.legal_basis }}</div>
              <div v-if="row.grounding_warning" class="rec-warning">⚠ 该建议含未经证据核实的数值，请结合合同原文确认。</div>
            </template>
          </el-table-column>
          <el-table-column prop="confidence" label="置信度" width="120">
            <template #default="{ row }">
              <el-progress
                :percentage="Math.round((row.confidence || 0) * 100)"
                :color="row.confidence >= 0.7 ? '#67C23A' : row.confidence >= 0.5 ? '#E6A23C' : '#F56C6C'"
                :stroke-width="6"
              />
            </template>
          </el-table-column>
        </el-table>

        <!-- 反馈标注面板 -->
        <FeedbackPanel
          ref="feedbackRef"
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
import { ref, computed, watch, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute } from 'vue-router'
import { ArrowLeft, Search } from '@element-plus/icons-vue'
import { getAuditResult, getContractDetail, getFeedback, deleteFeedback } from '../api/contract.js'
import { useFeedback } from '../composables/useFeedback.js'
import FeedbackPanel from '../components/FeedbackPanel.vue'

const route = useRoute()
const contractId = computed(() => route.params.contractId || '')

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
</style>
