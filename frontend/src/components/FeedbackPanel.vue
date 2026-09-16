<template>
  <div class="feedback-panel">
    <el-empty v-if="!items.length" description="暂无风险项需要反馈" />

    <div
      v-for="item in items"
      :key="item.id"
      class="risk-item"
      :class="{ 'risk-processed': isProcessed(item) }"
    >
      <div class="risk-header">
        <div class="risk-meta">
          <RiskBadge :level="levelLabel(item.risk_level || item.level)" size="small" />
          <span class="risk-type">{{ riskTypeLabel(item.risk_type) }}</span>
          <span class="risk-method" v-if="item.detection_method">
            <el-tag size="small" type="info" effect="plain">
              {{ methodLabel(item.detection_method) }}
            </el-tag>
          </span>
          <span class="risk-confidence" v-if="item.confidence != null">
            置信度 {{ (item.confidence * 100).toFixed(0) }}%
          </span>
        </div>
      </div>

      <div class="risk-clause" v-if="item.clause_text">
        <span class="label">涉及条款：</span>
        <span class="text">{{ item.clause_text }}</span>
      </div>
      <div class="risk-reason" v-if="item.reason">
        <span class="label">判定理由：</span>
        <span class="text">{{ item.reason }}</span>
      </div>
      <div class="risk-suggestion" v-if="item.suggestion">
        <span class="label">修改建议：</span>
        <span class="text">{{ item.suggestion }}</span>
      </div>
      <!-- v6.5 建议层结构化字段（BUG-021） -->
      <div class="risk-recommendation" v-if="item.risk_description">
        <span class="label">风险说明：</span>
        <span class="text">{{ item.risk_description }}</span>
      </div>
      <div class="risk-recommendation" v-if="item.example">
        <span class="label">修改示例：</span>
        <span class="text">{{ item.example }}</span>
      </div>
      <div class="risk-recommendation" v-if="item.legal_basis">
        <span class="label">法律依据：</span>
        <span class="text">{{ item.legal_basis }}</span>
      </div>
      <div class="risk-grounding-warning" v-if="item.grounding_warning">
        ⚠ 该建议含未经证据核实的数值，请结合合同原文确认。
      </div>

      <!-- 未处理：显示四个操作按钮 -->
      <div v-if="!isProcessed(item)" class="risk-actions">
        <el-button type="success" size="small" :icon="Check" @click="handleConfirm(item)">
          确认
        </el-button>
        <el-button type="warning" size="small" :icon="Edit" @click="openCorrect(item)">
          修正
        </el-button>
        <el-button type="danger" size="small" :icon="Close" @click="openFalsePositive(item)">
          误报
        </el-button>
        <el-button type="primary" size="small" :icon="Plus" @click="openSupplement(item)">
          补充
        </el-button>
      </div>

      <!-- 已处理：显示反馈结果卡片 + 撤销 -->
      <div v-else class="feedback-result">
        <div class="feedback-result-header">
          <el-tag :type="statusTagType(feedbackStates[item.id])" size="small">
            {{ statusLabel(feedbackStates[item.id]) }}
          </el-tag>
          <el-tag
            v-if="feedbackReasons[item.id]"
            size="small" effect="plain" type="info"
          >归因：{{ reasonLabel(feedbackReasons[item.id]) }}</el-tag>
          <el-button type="warning" size="small" text :icon="RefreshLeft" @click="undoFeedback(item)">
            撤销
          </el-button>
        </div>
        <div v-if="feedbackComments[item.id]" class="feedback-result-body">
          <el-icon><ChatLineSquare /></el-icon>
          <span>{{ feedbackComments[item.id] }}</span>
        </div>
        <div
          v-if="feedbackStates[item.id] === 'corrected' && feedbackCorrected[item.id]"
          class="feedback-result-extra"
        >
          <span class="corrected-label">修正等级：</span>
          <RiskBadge :level="feedbackCorrected[item.id].risk_level" size="small" />
        </div>
        <div class="feedback-review-hint">
          该反馈已进入反馈池；需经审核 + 管理员批准后才可能用于持续优化（不会立即生效）。
        </div>
      </div>
    </div>

    <!-- 误报对话框（含归因，反馈归因是学习经验的关键输入） -->
    <el-dialog v-model="falsePositiveDialog.visible" title="标记误报" width="560px" :close-on-click-modal="false">
      <el-form label-position="top">
        <el-form-item label="误报原因（归因）">
          <el-select v-model="falsePositiveDialog.reason" style="width: 100%">
            <el-option v-for="r in reasonsForFalsePositive" :key="r.value" :label="r.label" :value="r.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="补充说明">
          <el-input
            v-model="falsePositiveDialog.comment"
            type="textarea"
            :rows="3"
            placeholder="例如：第四条第（2）款已经约定验收标准"
            maxlength="500"
            show-word-limit
          />
        </el-form-item>
        <el-alert
          type="info" :closable="false" show-icon
          title="提交后进入反馈池，需经审核与管理员批准；批准前不会用于任何自动优化。"
        />
      </el-form>
      <template #footer>
        <el-button @click="falsePositiveDialog.visible = false">取消</el-button>
        <el-button type="danger" @click="submitFalsePositive">确定标记误报</el-button>
      </template>
    </el-dialog>

    <!-- 修正对话框 -->
    <el-dialog v-model="correctDialog.visible" title="修正风险标注" width="520px" :close-on-click-modal="false">
      <el-form label-position="top">
        <el-form-item label="风险等级">
          <el-select v-model="correctDialog.level" style="width: 100%">
            <el-option label="高风险" value="high" />
            <el-option label="中风险" value="medium" />
            <el-option label="低风险" value="low" />
          </el-select>
        </el-form-item>
        <el-form-item label="修正理由（归因）">
          <el-select v-model="correctDialog.reason" style="width: 100%">
            <el-option v-for="r in REASON_OPTIONS" :key="r.value" :label="r.label" :value="r.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="修正说明">
          <el-input
            v-model="correctDialog.comment"
            type="textarea"
            :rows="3"
            placeholder="请说明修正理由..."
            maxlength="500"
            show-word-limit
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="correctDialog.visible = false">取消</el-button>
        <el-button type="primary" @click="submitCorrect">提交修正</el-button>
      </template>
    </el-dialog>

    <!-- 补充说明对话框 -->
    <el-dialog v-model="supplementDialog.visible" title="补充风险标注" width="520px" :close-on-click-modal="false">
      <el-form label-position="top">
        <el-form-item label="补充说明">
          <el-input
            v-model="supplementDialog.comment"
            type="textarea"
            :rows="4"
            placeholder="请输入补充的风险说明或遗漏的风险项..."
            maxlength="2000"
            show-word-limit
          />
        </el-form-item>
        <el-form-item label="归因">
          <el-select v-model="supplementDialog.reason" style="width: 100%">
            <el-option v-for="r in REASON_OPTIONS" :key="r.value" :label="r.label" :value="r.value" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="supplementDialog.visible = false">取消</el-button>
        <el-button type="primary" @click="submitSupplement">提交补充</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Check, Edit, Close, Plus, ChatLineSquare, RefreshLeft } from '@element-plus/icons-vue'
import RiskBadge from './RiskBadge.vue'

const props = defineProps({
  riskItems: { type: Array, default: () => [] },
  contractId: { type: [Number, String], default: null },
  /** API 加载的已有反馈记录: [{ record_id, action_type, comment, corrected_risk? }] */
  loadedFeedbacks: { type: Array, default: () => [] },
})

const emit = defineEmits([
  'feedback-change',
  'feedback-undo',
])

const items = ref([])
const feedbackStates = reactive({})
const feedbackComments = reactive({})
const feedbackCorrected = reactive({})
const feedbackReasons = reactive({})

// ── 反馈归因（与后端 services/feedback_experience.FEEDBACK_REASONS 一致）
// 说明：归因只是"为什么这么判"的说明，**不是**规则改动，也不会自动影响任何判定。
const REASON_OPTIONS = [
  { value: 'clause_exists', label: '条款实际存在（模型漏看）' },
  { value: 'evidence_gap', label: '证据提取错误' },
  { value: 'semantic_error', label: '语义理解错误' },
  { value: 'contract_type_error', label: '合同类型判断错误' },
  { value: 'adjudicator_rule_suspect', label: '怀疑裁决规则本身有误（仅进规则反馈池）' },
  { value: 'other', label: '其他' },
]
// 误报场景下"怀疑规则"不在首位引导；规则反馈池仅用于人工排查
const reasonsForFalsePositive = REASON_OPTIONS

function reasonLabel(v) {
  return REASON_OPTIONS.find(r => r.value === v)?.label || v
}

watch(() => props.riskItems, (val) => { items.value = val || [] }, { immediate: true, deep: true })

// 从 API 加载已有反馈状态
watch(() => props.loadedFeedbacks, (list) => {
  if (!list || !list.length) return
  for (const fb of list) {
    const rid = fb.record_id
    if (!feedbackStates[rid]) {
      feedbackStates[rid] = fb.action_type
      if (fb.comment) feedbackComments[rid] = fb.comment
      if (fb.corrected_risk) feedbackCorrected[rid] = fb.corrected_risk
      if (fb.feedback_reason) feedbackReasons[rid] = fb.feedback_reason
    }
  }
}, { immediate: true, deep: true })

// ── 映射 ──
const LEVEL_MAP = { high: '高风险', medium: '中风险', low: '低风险' }
const reverseLevelMap = { '高风险': 'high', '中风险': 'medium', '低风险': 'low' }
function levelLabel(level) { return LEVEL_MAP[level] || level || '未知' }

const TYPE_LABELS = { R01:'违约金过高',R02:'无限责任',R03:'单方解约权',R04:'管辖条款不利',R05:'保密期间不合理',R06:'知识产权归属不清',R07:'付款条件不公平',R08:'验收标准缺失',R09:'不可抗力条款缺失',R10:'竞业限制过宽',R11:'自动续约陷阱',R12:'数据隐私条款不当',R13:'疑似名实不符' }
function riskTypeLabel(type) { return TYPE_LABELS[type] || type || '未分类' }

const METHOD_LABELS = { rule:'规则引擎',rag:'RAG语义',llm:'LLM分析',evidence:'证据裁决' }
function methodLabel(method) { return METHOD_LABELS[method] || method || '未知方法' }

function isProcessed(item) { return !!feedbackStates[item.id] }

function statusLabel(s) {
  const m = { confirmed:'已确认',corrected:'已修正',false_positive:'已标记误报',supplemented:'已补充' }
  return m[s] || s
}
function statusTagType(s) {
  const m = { confirmed:'success',corrected:'warning',false_positive:'danger',supplemented:'primary' }
  return m[s] || 'info'
}

// ── 提交反馈 ──
function apply(item, actionType, extra = {}) {
  const id = item.id
  feedbackStates[id] = actionType
  if (extra.comment) feedbackComments[id] = extra.comment
  if (extra.corrected_risk) feedbackCorrected[id] = extra.corrected_risk
  feedbackReasons[id] = extra.feedback_reason || 'other'
  ElMessage.success(statusLabel(actionType) + '成功')
  emit('feedback-change', {
    record_id: id,
    action_type: actionType,
    corrected_risk: extra.corrected_risk || null,
    comment: extra.comment || null,
    // 归因：后端据此生成学习经验（并区分"规则反馈池"）
    feedback_reason: extra.feedback_reason || 'other',
    contract_id: props.contractId,
  })
}

// ── 撤销反馈 ──
function undoFeedback(item) {
  const id = item.id
  const prevAction = feedbackStates[id]
  // 从已加载的反馈记录里找到对应的反馈 id，供后端 DELETE 使用
  const fb = props.loadedFeedbacks.find(f => f.record_id === id)
  const feedbackId = fb?.id || null
  delete feedbackStates[id]
  delete feedbackComments[id]
  delete feedbackCorrected[id]
  delete feedbackReasons[id]
  ElMessage.info('已撤销' + statusLabel(prevAction))
  emit('feedback-undo', {
    record_id: id,
    feedback_id: feedbackId,
    prev_action_type: prevAction,
    contract_id: props.contractId,
  })
}

// ── 确认 ──
function handleConfirm(item) { apply(item, 'confirmed', { feedback_reason: 'other' }) }

// ── 误报（含归因。误报是学习闭环里最有价值的负样本）──
const falsePositiveDialog = reactive({ visible: false, recordId: null, reason: 'clause_exists', comment: '' })
function openFalsePositive(item) {
  falsePositiveDialog.recordId = item.id
  falsePositiveDialog.reason = 'clause_exists'
  falsePositiveDialog.comment = ''
  falsePositiveDialog.visible = true
}
function submitFalsePositive() {
  const item = items.value.find(i => i.id === falsePositiveDialog.recordId)
  if (!item) return
  apply(item, 'false_positive', {
    feedback_reason: falsePositiveDialog.reason,
    comment: falsePositiveDialog.comment || undefined,
  })
  falsePositiveDialog.visible = false
}

// ── 修正 ──
const correctDialog = reactive({ visible: false, recordId: null, level: 'medium', reason: 'other', comment: '' })
function openCorrect(item) {
  correctDialog.recordId = item.id
  correctDialog.level = item.risk_level || reverseLevelMap[item.level] || 'medium'
  correctDialog.reason = 'other'
  correctDialog.comment = ''
  correctDialog.visible = true
}
function submitCorrect() {
  const item = items.value.find(i => i.id === correctDialog.recordId)
  if (!item) return
  apply(item, 'corrected', {
    corrected_risk: { risk_level: correctDialog.level },
    feedback_reason: correctDialog.reason,
    comment: correctDialog.comment || undefined,
  })
  correctDialog.visible = false
}

// ── 补充 ──
const supplementDialog = reactive({ visible: false, recordId: null, reason: 'evidence_gap', comment: '' })
function openSupplement(item) {
  supplementDialog.recordId = item.id
  supplementDialog.reason = 'evidence_gap'
  supplementDialog.comment = ''
  supplementDialog.visible = true
}
function submitSupplement() {
  const item = items.value.find(i => i.id === supplementDialog.recordId)
  if (!item) return
  apply(item, 'supplemented', {
    comment: supplementDialog.comment || undefined,
    feedback_reason: supplementDialog.reason,
  })
  supplementDialog.visible = false
}

function clearStates() {
  Object.keys(feedbackStates).forEach(k => delete feedbackStates[k])
  Object.keys(feedbackComments).forEach(k => delete feedbackComments[k])
  Object.keys(feedbackCorrected).forEach(k => delete feedbackCorrected[k])
  Object.keys(feedbackReasons).forEach(k => delete feedbackReasons[k])
}

defineExpose({
  resetAll() {
    clearStates()
  },
  getSnapshot() {
    return {
      states: { ...feedbackStates },
      comments: { ...feedbackComments },
      corrected: { ...feedbackCorrected },
    }
  },
  restore(records) {
    clearStates()
    records.forEach(r => {
      feedbackStates[r.record_id] = r.action_type
      if (r.comment) feedbackComments[r.record_id] = r.comment
      if (r.corrected_risk) feedbackCorrected[r.record_id] = r.corrected_risk
    })
  },
})
</script>

<style scoped>
.feedback-panel { width: 100%; }

.risk-item {
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 12px;
  background: #fff;
  transition: border-color .2s, background .2s;
}
.risk-item:hover { border-color: #c0c4cc; }
.risk-item.risk-processed { background: #f9fafb; border-color: #dcdfe6; }

.risk-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
  flex-wrap: wrap;
  gap: 8px;
}
.risk-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.risk-type { font-weight: 600; font-size: 14px; color: #303133; }
.risk-method { font-size: 12px; }
.risk-confidence { font-size: 12px; color: #909399; }

.risk-clause, .risk-reason, .risk-suggestion, .risk-recommendation {
  margin-bottom: 6px;
  font-size: 13px;
  line-height: 1.6;
}
.label { color: #909399; margin-right: 4px; }
.risk-grounding-warning {
  margin-top: 6px;
  font-size: 12px;
  color: #E6A23C;
}
.text { color: #303133; }

.risk-actions {
  margin-top: 12px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

/* 反馈结果卡片 */
.feedback-result {
  margin-top: 12px;
  padding: 12px;
  background: #f0f9eb;
  border: 1px solid #b7eb8f;
  border-radius: 6px;
}
.feedback-result-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.feedback-result-body {
  margin-top: 8px;
  display: flex;
  align-items: flex-start;
  gap: 6px;
  font-size: 13px;
  color: #303133;
  line-height: 1.6;
}
.feedback-result-extra {
  margin-top: 8px;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}
.corrected-label { color: #909399; }
.feedback-review-hint { margin-top: 6px; font-size: 11.5px; color: #9AA4B8; line-height: 1.7; }
</style>
