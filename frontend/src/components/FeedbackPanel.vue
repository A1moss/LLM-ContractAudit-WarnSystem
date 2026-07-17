<template>
  <div class="feedback-panel">
    <!-- 空态 -->
    <el-empty v-if="!items.length" description="暂无风险项需要反馈" />

    <!-- 风险项列表 -->
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
        <div class="risk-status" v-if="isProcessed(item)">
          <el-tag :type="statusTagType(feedbackStates[item.id])" size="small" effect="plain">
            {{ statusLabel(feedbackStates[item.id]) }}
          </el-tag>
        </div>
      </div>

      <!-- 条款原文 -->
      <div class="risk-clause" v-if="item.clause_text">
        <span class="label">涉及条款：</span>
        <span class="text">{{ item.clause_text }}</span>
      </div>

      <!-- 判定理由 -->
      <div class="risk-reason" v-if="item.reason">
        <span class="label">判定理由：</span>
        <span class="text">{{ item.reason }}</span>
      </div>

      <!-- 修改建议 -->
      <div class="risk-suggestion" v-if="item.suggestion">
        <span class="label">修改建议：</span>
        <span class="text">{{ item.suggestion }}</span>
      </div>

      <!-- 操作按钮栏 -->
      <div class="risk-actions">
        <el-button
          type="success"
          size="small"
          :icon="Check"
          :disabled="isProcessed(item)"
          @click="handleConfirm(item)"
        >
          确认
        </el-button>
        <el-button
          type="warning"
          size="small"
          :icon="Edit"
          :disabled="isProcessed(item)"
          @click="openCorrect(item)"
        >
          修正
        </el-button>
        <el-button
          type="danger"
          size="small"
          :icon="Close"
          :disabled="isProcessed(item)"
          @click="handleFalsePositive(item)"
        >
          误报
        </el-button>
        <el-button
          type="primary"
          size="small"
          :icon="Plus"
          :disabled="isProcessed(item)"
          @click="openSupplement(item)"
        >
          补充
        </el-button>
      </div>

      <!-- 已处理时显示反馈备注 -->
      <div v-if="isProcessed(item) && feedbackComments[item.id]" class="risk-comment">
        <el-icon><ChatLineSquare /></el-icon>
        {{ feedbackComments[item.id] }}
      </div>
    </div>

    <!-- ====== 修正对话框 ====== -->
    <el-dialog
      v-model="correctDialog.visible"
      title="修正风险标注"
      width="520px"
      :close-on-click-modal="false"
    >
      <el-form label-position="top">
        <el-form-item label="风险等级">
          <el-select v-model="correctDialog.level" style="width: 100%">
            <el-option label="高风险" value="high">
              <el-tag type="danger" size="small">高风险</el-tag>
            </el-option>
            <el-option label="中风险" value="medium">
              <el-tag type="warning" size="small">中风险</el-tag>
            </el-option>
            <el-option label="低风险" value="low">
              <el-tag type="success" size="small">低风险</el-tag>
            </el-option>
          </el-select>
        </el-form-item>
        <el-form-item label="修正理由">
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

    <!-- ====== 补充说明对话框 ====== -->
    <el-dialog
      v-model="supplementDialog.visible"
      title="补充风险标注"
      width="520px"
      :close-on-click-modal="false"
    >
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
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, Edit, Close, Plus, ChatLineSquare } from '@element-plus/icons-vue'
import RiskBadge from './RiskBadge.vue'

const props = defineProps({
  /** 风险项列表，每项需有 id, risk_type, risk_level, clause_text, reason, suggestion, confidence, detection_method */
  riskItems: {
    type: Array,
    default: () => [],
  },
  /** 合同 ID，透传给父组件的 emit 事件 */
  contractId: {
    type: [Number, String],
    default: null,
  },
})

const emit = defineEmits([
  /** 每次反馈操作后触发，payload: { record_id, action_type, corrected_risk?, comment?, contract_id } */
  'feedback-change',
])

// ── 本地列表 ──
const items = ref([])

// 反馈状态: record_id → action_type  (本地内存，不持久化)
const feedbackStates = reactive({})
// 反馈备注: record_id → comment
const feedbackComments = reactive({})

// ── 同步外部数据 ──
watch(
  () => props.riskItems,
  (val) => {
    items.value = val || []
  },
  { immediate: true, deep: true }
)

// ── 映射 ──
const LEVEL_MAP = { high: '高风险', medium: '中风险', low: '低风险' }
function levelLabel(level) {
  return LEVEL_MAP[level] || level || '未知'
}

const TYPE_LABELS = {
  R01: '违约金过高',
  R02: '无限责任',
  R03: '单方解约权',
  R04: '保密期限不合理',
  R05: '知识产权归属不清',
  R06: '争议管辖不利',
  R07: '付款期限不合理',
  R08: '缺失验收标准',
  R09: '缺失不可抗力',
  R10: '缺失数据保护',
  R11: 'SLA罚则过高',
  R12: '源代码托管缺失',
}
function riskTypeLabel(type) {
  return TYPE_LABELS[type] || type || '未分类'
}

const METHOD_LABELS = {
  rule: '规则引擎',
  rag: 'RAG语义',
  llm: 'LLM分析',
  corex_review: 'Corex交叉验证',
  dify_fallback: 'Dify备用',
}
function methodLabel(method) {
  return METHOD_LABELS[method] || method || '未知方法'
}

function isProcessed(item) {
  return !!feedbackStates[item.id]
}

function statusLabel(status) {
  const map = {
    confirmed: '已确认',
    corrected: '已修正',
    false_positive: '已标记误报',
    supplemented: '已补充',
  }
  return map[status] || status
}
function statusTagType(status) {
  const map = {
    confirmed: 'success',
    corrected: 'warning',
    false_positive: 'danger',
    supplemented: 'primary',
  }
  return map[status] || 'info'
}

// ── 通用操作 ──
function apply(item, actionType, extra = {}) {
  const id = item.id
  feedbackStates[id] = actionType
  if (extra.comment) feedbackComments[id] = extra.comment
  ElMessage.success(statusLabel(actionType) + '成功')
  emit('feedback-change', {
    record_id: id,
    action_type: actionType,
    corrected_risk: extra.corrected_risk || null,
    comment: extra.comment || null,
    contract_id: props.contractId,
  })
}

// ── 确认 ──
function handleConfirm(item) {
  apply(item, 'confirmed')
}

// ── 误报 ──
async function handleFalsePositive(item) {
  try {
    await ElMessageBox.confirm(
      '确定将此项标记为误报吗？标记后该风险将不计入报告统计。',
      '确认误报',
      { confirmButtonText: '确定标记', cancelButtonText: '取消', type: 'warning' }
    )
    apply(item, 'false_positive')
  } catch {
    // 用户取消
  }
}

// ── 修正 ──
const correctDialog = reactive({ visible: false, recordId: null, level: 'medium', comment: '' })
function openCorrect(item) {
  correctDialog.recordId = item.id
  correctDialog.level = item.risk_level || item.level || 'medium'
  correctDialog.comment = ''
  correctDialog.visible = true
}
function submitCorrect() {
  const item = items.value.find((i) => i.id === correctDialog.recordId)
  apply(item, 'corrected', {
    corrected_risk: {
      risk_level: correctDialog.level,
      risk_type: item?.risk_type,
      clause_text: item?.clause_text,
      reason: item?.reason,
      suggestion: item?.suggestion,
      confidence: item?.confidence,
    },
    comment: correctDialog.comment || undefined,
  })
  correctDialog.visible = false
}

// ── 补充 ──
const supplementDialog = reactive({ visible: false, recordId: null, comment: '' })
function openSupplement(item) {
  supplementDialog.recordId = item.id
  supplementDialog.comment = ''
  supplementDialog.visible = true
}
function submitSupplement() {
  const item = items.value.find((i) => i.id === supplementDialog.recordId)
  apply(item, 'supplemented', {
    comment: supplementDialog.comment || undefined,
  })
  supplementDialog.visible = false
}

// ── 暴露方法给父组件（如重置所有反馈状态） ──
defineExpose({
  /** 清空所有反馈状态 */
  resetAll() {
    Object.keys(feedbackStates).forEach((k) => delete feedbackStates[k])
    Object.keys(feedbackComments).forEach((k) => delete feedbackComments[k])
  },
  /** 获取当前反馈状态快照 */
  getSnapshot() {
    return {
      states: { ...feedbackStates },
      comments: { ...feedbackComments },
    }
  },
})
</script>

<style scoped>
.feedback-panel {
  width: 100%;
}

.risk-item {
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 12px;
  background: #fff;
  transition: border-color 0.2s, background 0.2s;
}
.risk-item:hover {
  border-color: #c0c4cc;
}
.risk-item.risk-processed {
  background: #fafafa;
  border-color: #dcdfe6;
}

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
.risk-type {
  font-weight: 600;
  font-size: 14px;
  color: #303133;
}
.risk-method {
  font-size: 12px;
}
.risk-confidence {
  font-size: 12px;
  color: #909399;
}

.risk-clause,
.risk-reason,
.risk-suggestion {
  margin-bottom: 6px;
  font-size: 13px;
  line-height: 1.6;
}
.label {
  color: #909399;
  margin-right: 4px;
}
.text {
  color: #303133;
}

.risk-actions {
  margin-top: 12px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.risk-comment {
  margin-top: 10px;
  padding: 8px 12px;
  background: #ecf5ff;
  border-radius: 6px;
  font-size: 13px;
  color: #409eff;
  display: flex;
  align-items: flex-start;
  gap: 6px;
}
</style>
