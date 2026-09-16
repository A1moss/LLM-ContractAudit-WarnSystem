<template>
  <div class="rp">
    <!-- 统计条（仅来自 /audit-result；不用 contract.status 推导风险） -->
    <div class="rp-stats">
      <div class="rp-stat">
        <div class="n">{{ ws.riskItems.length }}</div>
        <div class="l">当前审核风险</div>
      </div>
      <div class="rp-stat"><div class="n hi">{{ levelCount.high }}</div><div class="l">高风险</div></div>
      <div class="rp-stat"><div class="n mid">{{ levelCount.medium }}</div><div class="l">中风险</div></div>
      <div class="rp-stat"><div class="n low">{{ levelCount.low }}</div><div class="l">低风险</div></div>
      <div class="rp-stat"><div class="n">{{ ws.sessions.length }}</div><div class="l">修改会话总数</div></div>
      <div class="rp-spacer" />
      <el-button v-if="ws.contract?.status === 'parsed' && !ws.riskItems.length" type="primary" size="small" :loading="ws.auditing" @click="ws.triggerAuditAndPoll()">
        开始审核
      </el-button>
      <el-button v-if="ws.riskItems.length" size="small" :loading="ws.auditing" @click="ws.triggerAuditAndPoll()">重新审核</el-button>
    </div>

    <el-alert
      v-if="!ws.hasCurrentResult && ws.riskItems.length"
      type="error" show-icon :closable="false" class="rp-alert"
      title="上次审核已被驳回，以下为已驳回的历史结果（result_status 不再有效）"
    />

    <!-- 筛选 -->
    <div v-if="ws.riskItems.length" class="rp-filters">
      <el-select v-model="filters.level" size="small" placeholder="等级" clearable style="width:110px">
        <el-option label="高风险" value="high" />
        <el-option label="中风险" value="medium" />
        <el-option label="低风险" value="low" />
      </el-select>
      <el-select v-model="filters.riskType" size="small" placeholder="规则" clearable filterable style="width:190px">
        <el-option v-for="(name, code) in RISK_NAMES" :key="code" :label="`${code} · ${name}`" :value="code" />
      </el-select>
      <el-select v-model="filters.locate" size="small" placeholder="定位状态" clearable style="width:130px">
        <el-option label="已自动定位" value="auto" />
        <el-option label="可定位（预检）" value="predicted" />
        <el-option label="未定位" value="none" />
      </el-select>
      <el-select v-model="filters.modified" size="small" placeholder="修改状态" clearable style="width:130px">
        <el-option label="未修改" value="no" />
        <el-option label="已修改" value="yes" />
      </el-select>
      <el-input v-model="filters.keyword" size="small" placeholder="关键词（条款/理由/建议）" clearable style="width:220px" />
      <span class="rp-count">显示 {{ filtered.length }} / {{ ws.riskItems.length }} 条</span>
    </div>

    <!-- 加载/空/错误态 -->
    <div v-if="ws.riskLoading" class="rp-empty"><el-skeleton :rows="5" animated /></div>
    <el-alert v-else-if="ws.riskError" type="error" show-icon :closable="false" :title="ws.riskError" class="rp-alert">
      <el-button size="small" @click="ws.loadAuditResult()">重试</el-button>
    </el-alert>
    <el-empty
      v-else-if="ws.contract?.status === 'auditing'"
      description="审核进行中，AI 正在分析合同条款…"
    />
    <el-empty
      v-else-if="!ws.riskItems.length && ws.contract?.status === 'parsed'"
      description="尚未审核此合同"
    />
    <div v-else-if="!ws.riskItems.length" class="rp-clean">
      <el-result icon="success" title="未发现风险" sub-title="本次审核未检出 R01–R13 风险；可继续查看「条款比对」结果。" />
    </div>

    <!-- 风险卡片列表 -->
    <div v-else ref="listRef" class="rp-list">
      <div v-for="r in filtered" :key="r.id" class="rp-card" :data-risk-id="r.id">
        <div class="rp-card-head">
          <el-tag :type="levelTag(r.risk_level)" size="small" effect="dark">{{ riskLevelLabel(r.risk_level) }}</el-tag>
          <span class="rp-code">{{ r.risk_type }}</span>
          <span class="rp-name">{{ riskName(r.risk_type) }}</span>
          <span class="rp-dot" :class="'dot-' + locateKind(r)" :title="locateText(r)" />
          <span class="rp-locate">{{ locateText(r) }}</span>
          <el-tag v-if="roundsOf(r.id)" size="small" type="success" effect="plain">已修改 {{ roundsOf(r.id) }} 轮</el-tag>
          <el-tag v-else size="small" type="info" effect="plain">未修改</el-tag>
          <span class="rp-spacer" />
          <span v-if="r.clause_no" class="rp-pos">第{{ ws.cnNo(r.clause_no) }}条</span>
          <span class="rp-conf">置信度 {{ Math.round((r.confidence || 0) * 100) }}%</span>
          <el-tag size="small" effect="plain" type="info">{{ detectionLabel(r.detection_method) }}</el-tag>
        </div>

        <div class="rp-clause">{{ r.clause_text || '（该风险没有关联的条款原文）' }}</div>

        <div class="rp-sum">
          <div class="rp-sum-row"><span class="k">判定理由</span><span class="v">{{ clipText(r.reason, 160) || '—' }}</span></div>
          <div class="rp-sum-row"><span class="k">修改建议</span><span class="v">{{ clipText(r.suggestion, 160) || '—' }}</span></div>
        </div>

        <div class="rp-actions">
          <el-button size="small" type="primary" plain @click="goRevise(r, false)">去修改合同</el-button>
          <el-button v-if="!isLocatable(r)" size="small" type="warning" plain @click="goRevise(r, true)">
            去修改合同 · 需指定位置
          </el-button>
          <el-button size="small" text @click="toggle(r.id)">{{ expanded.has(r.id) ? '收起详情' : '展开详情' }}</el-button>
        </div>

        <div v-if="expanded.has(r.id)" class="rp-detail">
          <div class="rp-sec">
            <div class="rp-sec-t">原文证据</div>
            <template v-if="r.clause_position?.original_text">
              <div class="rp-kv"><span class="k">锚点原文</span><span class="v">{{ r.clause_position.original_text }}</span></div>
              <div class="rp-kv"><span class="k">位置</span><span class="v">
                {{ r.clause_position.clause_no ? `第${ws.cnNo(r.clause_position.clause_no)}条` : '（未识别到条款编号）' }}
                {{ r.clause_position.clause_title || '' }}
                字符区间 [{{ r.clause_position.start }}, {{ r.clause_position.end }}]
              </span></div>
            </template>
            <div v-else class="rp-none">审核阶段未定位到可靠原文锚点（该风险无法直接原位替换，需在修改工作台中指定位置）。</div>
            <template v-if="r.evidence">
              <div class="rp-kv"><span class="k">证据来源</span><span class="v">{{ detectionLabel(r.evidence.method) }}</span></div>
              <div v-if="r.evidence.law" class="rp-kv"><span class="k">命中法条</span><span class="v">{{ r.evidence.law }}</span></div>
              <div v-if="r.evidence.clause_text" class="rp-kv"><span class="k">证据条款</span><span class="v">{{ r.evidence.clause_text }}</span></div>
              <div v-if="r.evidence.references?.length" class="rp-kv">
                <span class="k">知识库引用</span>
                <span class="v">
                  <div v-for="(ref, i) in r.evidence.references" :key="i">
                    {{ ref.law }}{{ ref.article }} {{ ref.title }}<span class="muted">（{{ ref.source }}）</span>
                  </div>
                </span>
              </div>
              <div v-if="r.evidence.agreement != null" class="rp-kv"><span class="k">一致度</span><span class="v">{{ r.evidence.agreement }}</span></div>
            </template>
            <div v-else class="rp-none">该风险记录未写入证据链（evidence 为空）。</div>
          </div>

          <div class="rp-sec">
            <div class="rp-sec-t">判定理由</div>
            <div class="rp-full">{{ r.reason || '—' }}</div>
          </div>

          <div class="rp-sec">
            <div class="rp-sec-t">修改建议</div>
            <div class="rp-full">{{ r.suggestion || '—' }}</div>
          </div>

          <div class="rp-sec">
            <div class="rp-sec-t">建议层（v6.5）</div>
            <template v-if="r.recommendation">
              <div v-if="r.risk_description" class="rp-kv"><span class="k">风险说明</span><span class="v">{{ r.risk_description }}</span></div>
              <div v-if="r.example" class="rp-kv"><span class="k">修改示例</span><span class="v">{{ r.example }}</span></div>
              <div v-if="r.legal_basis" class="rp-kv"><span class="k">法律依据</span><span class="v">{{ r.legal_basis }}</span></div>
              <div v-if="r.grounding" class="rp-kv">
                <span class="k">接地检查</span>
                <span class="v">
                  {{ r.grounding.passed ? '通过（建议中的数值可在证据/法条中找到来源）' : '未通过：建议含未经证据核实的数值，请结合合同原文确认' }}
                  <span v-if="r.grounding.issues?.length" class="muted">· {{ r.grounding.issues.join('；') }}</span>
                </span>
              </div>
            </template>
            <div v-else class="rp-none">该风险没有建议层数据（recommendation 为空，后端仅在生成成功时写入）。</div>
          </div>

          <div class="rp-sec">
            <div class="rp-sec-t">记录元信息</div>
            <div class="rp-kv"><span class="k">审核批次</span><span class="v">{{ r.audit_batch }}</span></div>
            <div class="rp-kv"><span class="k">结果状态</span><span class="v">{{ r.result_status }}</span></div>
            <div class="rp-kv"><span class="k">反馈状态</span><span class="v">{{ r.feedback_status }}</span></div>
          </div>
        </div>
      </div>
    </div>

    <!-- 反馈标注（沿用现有组件与现有反馈接口） -->
    <FeedbackPanel
      v-if="ws.riskItems.length"
      ref="feedbackRef"
      :risk-items="ws.riskItems"
      :contract-id="ws.contract?.id"
      :loaded-feedbacks="ws.loadedFeedbacks"
      @feedback-change="ws.onSubmitFeedback"
      @feedback-undo="ws.onUndoFeedback"
    />
  </div>
</template>

<script setup>
import { reactive, ref, computed, watch, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import FeedbackPanel from '../../components/FeedbackPanel.vue'
import {
  RISK_NAMES, riskName, riskLevelLabel, detectionLabel,
} from '../../constants/riskTypes.js'

const props = defineProps({ ws: { type: Object, required: true } })

const feedbackRef = ref(null)
const listRef = ref(null)
const expanded = reactive(new Set())
function toggle(id) {
  if (expanded.has(id)) expanded.delete(id)
  else expanded.add(id)
}

const filters = reactive({ level: '', riskType: '', locate: '', modified: '', keyword: '' })

/**
 * 从「原始文本」的风险标注跳到风险详情：清空筛选、展开该卡片并滚动到位。
 * 纯 UI 行为；`focusRiskId` 不是后端字段。
 */
watch(
  () => props.ws.focusRiskId,
  async (id) => {
    if (!id) return
    filters.level = ''
    filters.riskType = ''
    filters.locate = ''
    filters.modified = ''
    filters.keyword = ''
    expanded.add(Number(id))
    await nextTick()
    const el = listRef.value?.querySelector(`[data-risk-id="${id}"]`)
    if (el) {
      el.scrollIntoView({ block: 'center', behavior: 'smooth' })
      el.classList.add('is-focus')
      setTimeout(() => el.classList.remove('is-focus'), 1600)
    }
    props.ws.focusRiskId = null
  },
)

const levelCount = computed(() => ({
  high: props.ws.riskItems.filter((r) => r.risk_level === 'high').length,
  medium: props.ws.riskItems.filter((r) => r.risk_level === 'medium').length,
  low: props.ws.riskItems.filter((r) => r.risk_level === 'low').length,
}))

/** 该风险是否「可原位替换」：审核阶段锚点 或 前端预检可定位 */
function locateKind(r) {
  if (r.clause_position?.original_text) return 'auto'
  const pre = props.ws.previewLocate(props.ws.parsedText, r.clause_text)
  if (pre.status === 'unique' || pre.status === 'disambiguated') return 'predicted'
  return 'none'
}
function isLocatable(r) {
  return locateKind(r) !== 'none'
}
function locateText(r) {
  const k = locateKind(r)
  if (k === 'auto') return r.clause_position?.clause_no ? `已定位 第${props.ws.cnNo(r.clause_position.clause_no)}条` : '已自动定位'
  if (k === 'predicted') return '预检可定位（待后端确认）'
  return '未定位'
}

function roundsOf(riskId) {
  const s = props.ws.sessions.find((x) => x.key === String(riskId))
  return s?.rounds || 0
}

const filtered = computed(() => {
  const kw = filters.keyword.trim().toLowerCase()
  return props.ws.riskItems.filter((r) => {
    if (filters.level && r.risk_level !== filters.level) return false
    if (filters.riskType && r.risk_type !== filters.riskType) return false
    if (filters.locate && locateKind(r) !== filters.locate) return false
    if (filters.modified === 'yes' && !roundsOf(r.id)) return false
    if (filters.modified === 'no' && roundsOf(r.id)) return false
    if (kw) {
      const hay = [r.clause_text, r.reason, r.suggestion, r.risk_description, r.risk_type, riskName(r.risk_type)]
        .join(' ')
        .toLowerCase()
      if (!hay.includes(kw)) return false
    }
    return true
  })
})

function levelTag(level) {
  return level === 'high' ? 'danger' : level === 'medium' ? 'warning' : 'success'
}
function clipText(s, n) {
  return props.ws.clipText(s, n)
}

/** 去修改合同：切到工作台并选中该风险会话（会话 key = String(AuditRecord.id)，不得改） */
function goRevise(r, needLocate) {
  props.ws.selectSession(String(r.id))
  props.ws.setTab('revise')
  if (needLocate) {
    ElMessage.info('该风险在审核阶段未定位到原文，请在工作台右侧「修改位置」中指定并确认位置')
  }
}
</script>

<style scoped>
.rp { display: flex; flex-direction: column; gap: 12px; min-height: 0; }
.rp-stats { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.rp-stat {
  background: #fff; border: 1px solid var(--a24-border); border-radius: 10px;
  padding: 8px 14px; min-width: 92px;
}
.rp-stat .n { font-size: 20px; font-weight: 700; color: #131313; line-height: 1.1; }
.rp-stat .n.hi { color: #DC2626; }
.rp-stat .n.mid { color: #D97706; }
.rp-stat .n.low { color: #2563EB; }
.rp-stat .l { font-size: 11.5px; color: #8A93A6; margin-top: 2px; }
.rp-spacer { flex: 1; }
.rp-alert { margin: 0; }
.rp-filters { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.rp-count { font-size: 12px; color: #8A93A6; }
.rp-empty { padding: 20px 0; }
.rp-clean { padding: 10px 0; }
.rp-list { display: flex; flex-direction: column; gap: 10px; }
.rp-card { background: #fff; border: 1px solid var(--a24-border); border-radius: 10px; padding: 12px 14px; }
.rp-card:hover { border-color: #C7D2E5; }
.rp-card.is-focus { border-color: var(--a24-primary); box-shadow: 0 0 0 3px rgba(25, 53, 195, 0.14); }
.rp-card-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.rp-code { font-family: ui-monospace, Consolas, monospace; font-size: 12px; color: #6B7280; }
.rp-name { font-size: 14px; font-weight: 600; color: #131313; }
.rp-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.rp-dot.dot-auto, .rp-dot.dot-predicted { background: #16A34A; }
.rp-dot.dot-none { background: #D97706; }
.rp-locate { font-size: 12px; color: #6B7280; }
.rp-pos { font-size: 12.5px; color: var(--a24-primary); font-weight: 600; }
.rp-conf { font-size: 12px; color: #8A93A6; }
.rp-clause { margin-top: 8px; font-size: 13px; color: #303133; line-height: 1.7; }
.rp-sum { margin-top: 6px; display: flex; flex-direction: column; gap: 4px; }
.rp-sum-row { display: flex; gap: 8px; font-size: 12.5px; }
.rp-sum-row .k { flex: 0 0 60px; color: #8A93A6; }
.rp-sum-row .v { flex: 1; color: #4B5563; }
.rp-actions { margin-top: 10px; display: flex; gap: 8px; }
.rp-detail { margin-top: 12px; border-top: 1px dashed var(--a24-border); padding-top: 10px; display: flex; flex-direction: column; gap: 12px; }
.rp-sec-t { font-size: 12.5px; font-weight: 600; color: #6B7280; margin-bottom: 6px; }
.rp-kv { display: flex; gap: 8px; font-size: 12.5px; line-height: 1.7; margin-bottom: 4px; }
.rp-kv .k { flex: 0 0 76px; color: #8A93A6; }
.rp-kv .v { flex: 1; color: #303133; word-break: break-word; white-space: pre-wrap; }
.rp-full { font-size: 12.5px; color: #303133; line-height: 1.8; white-space: pre-wrap; }
.rp-none { font-size: 12.5px; color: #9AA4B8; background: #F8FAFD; border-radius: 6px; padding: 8px 10px; }
.muted { color: #9AA4B8; }
</style>
