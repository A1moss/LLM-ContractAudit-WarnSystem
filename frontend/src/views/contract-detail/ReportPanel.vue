<template>
  <div class="rpp">
    <div v-if="ws.reportLoading" class="rpp-loading"><el-skeleton :rows="5" animated /></div>

    <el-alert v-else-if="ws.reportError" type="error" show-icon :closable="false" :title="ws.reportError">
      <el-button size="small" @click="ws.loadReport()">重试</el-button>
    </el-alert>

    <el-empty v-else-if="!ws.report" description="审核尚未完成，暂无审核报告">
      <el-button v-if="ws.contract?.status === 'parsed'" type="primary" :loading="ws.auditing" @click="ws.triggerAuditAndPoll()">
        开始审核
      </el-button>
      <el-button v-else-if="ws.contract?.status === 'auditing'" type="primary" loading>审核进行中</el-button>
    </el-empty>

    <template v-else>
      <!-- 摘要卡（真实字段） -->
      <div class="rpp-cards">
        <div class="rpp-card">
          <div class="n">{{ (ws.report.high_risk_count || 0) + (ws.report.mid_risk_count || 0) + (ws.report.low_risk_count || 0) }}</div>
          <div class="l">风险总数</div>
        </div>
        <div class="rpp-card"><div class="n hi">{{ ws.report.high_risk_count || 0 }}</div><div class="l">高风险</div></div>
        <div class="rpp-card"><div class="n mid">{{ ws.report.mid_risk_count || 0 }}</div><div class="l">中风险</div></div>
        <div class="rpp-card"><div class="n low">{{ ws.report.low_risk_count || 0 }}</div><div class="l">低风险</div></div>
        <div class="rpp-card">
          <div class="n score">{{ ws.report.risk_score ?? '—' }}</div>
          <div class="l">风险评分</div>
        </div>
      </div>

      <div class="rpp-meta">
        <span>审核批次：<code>{{ ws.report.audit_batch }}</code></span>
        <span>生成时间：{{ fmtTime(ws.report.created_at) }}</span>
        <span>审核模式：{{ ws.contract?.audit_mode === 'precise' ? '精细审核' : ws.contract?.audit_mode === 'fast' ? '快速初筛' : '—' }}</span>
      </div>

      <!-- 风险热力图（risk_heatmap_data，真实字段；仅基础展示） -->
      <div class="rpp-sec">
        <div class="rpp-sec-t">风险等级分布（risk_heatmap_data）</div>
        <div class="rpp-heat">
          <div v-for="lv in heatLevels" :key="lv.key" class="rpp-heat-cell" :class="'lv-' + lv.key">
            <div class="bar" :style="{ width: heatPercent(lv) + '%' }" />
            <div class="lbl">
              <span>{{ lv.label }}</span>
              <b>{{ heatValue(lv.key) }}</b>
            </div>
          </div>
        </div>
      </div>

      <!-- 缺失条款摘要（missing_clauses，真实字段） -->
      <div class="rpp-sec">
        <div class="rpp-sec-t">条款比对摘要（missing_clauses）</div>
        <template v-if="cmpSummary">
          <div class="rpp-cmp">
            <span>覆盖率 {{ Math.round((cmpSummary.coverage_rate || 0) * 100) }}%</span>
            <span>标准条款 {{ cmpSummary.total || 0 }}</span>
            <span>已覆盖 {{ cmpSummary.covered || 0 }}</span>
            <span>部分偏离 {{ cmpSummary.partial || 0 }}</span>
            <span>缺失 {{ cmpSummary.missing || 0 }}</span>
          </div>
          <div v-if="missingCritical.length" class="rpp-missing">
            <span class="t">缺失关键条款：</span>
            <el-tag v-for="c in missingCritical" :key="c" type="danger" size="small" effect="plain">{{ c }}</el-tag>
          </div>
        </template>
        <div v-else class="rpp-muted">报告中未包含条款比对结果（审核时比对可能未完成）。</div>
      </div>

      <!-- 完整报告入口 -->
      <div class="rpp-sec">
        <div class="rpp-sec-t">完整报告</div>
        <div class="rpp-actions">
          <el-button type="primary" @click="openFullReport">查看完整审核报告</el-button>
          <span class="rpp-muted">在报告页可查看报告 HTML 与合同原文对照。</span>
        </div>
      </div>

      <!-- 明确占位：未实现的能力只置灰，不伪造 -->
      <div class="rpp-sec">
        <div class="rpp-sec-t">报告导出与配置</div>
        <div class="rpp-actions">
          <el-button disabled>导出 PDF</el-button>
          <el-button disabled>导出 Word</el-button>
          <el-button disabled>打印</el-button>
          <el-button disabled>报告模板配置</el-button>
        </div>
        <div class="rpp-muted">
          该能力开发中：后端当前只提供报告 HTML 与结构化字段，未提供报告文件导出接口，因此这里只做置灰入口。
        </div>
      </div>

      <div class="rpp-foot">
        报告内容为<b>审核时</b>的快照：risk_score 与高/中/低计数不会随之后的条款比对刷新而变化；
        条款比对被单独重跑时，后端只会幂等替换报告 HTML 中的条款比对片段。
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const props = defineProps({ ws: { type: Object, required: true } })
const route = useRoute()
const router = useRouter()

const heatLevels = [
  { key: 'high', label: '高风险' },
  { key: 'mid', label: '中风险' },
  { key: 'low', label: '低风险' },
]

function heatValue(key) {
  const d = props.ws.report?.risk_heatmap_data
  if (!d) return 0
  return d[key] || 0
}
function heatPercent(lv) {
  const vals = heatLevels.map((l) => heatValue(l.key))
  const max = Math.max(1, ...vals)
  return Math.max(2, Math.round((heatValue(lv.key) / max) * 100))
}

const missingClauses = computed(() => props.ws.report?.missing_clauses || null)
const cmpSummary = computed(() => {
  const mc = missingClauses.value
  if (!mc) return null
  return mc.summary || null
})
const missingCritical = computed(() => missingClauses.value?.missing_critical || [])

function fmtTime(ts) {
  if (!ts) return '—'
  try {
    return new Date(ts).toLocaleString('zh-CN', { hour12: false })
  } catch {
    return ts
  }
}
function openFullReport() {
  router.push(`/audit/report/${route.params.id}`)
}
</script>

<style scoped>
.rpp { display: flex; flex-direction: column; gap: 12px; }
.rpp-loading { padding: 12px 0; }
.rpp-cards { display: flex; gap: 10px; flex-wrap: wrap; }
.rpp-card { flex: 1 1 120px; background: #fff; border: 1px solid var(--a24-border); border-radius: 10px; padding: 12px 14px; }
.rpp-card .n { font-size: 22px; font-weight: 700; color: #131313; line-height: 1.1; }
.rpp-card .n.hi { color: #DC2626; }
.rpp-card .n.mid { color: #D97706; }
.rpp-card .n.low { color: #2563EB; }
.rpp-card .n.score { color: var(--a24-primary); }
.rpp-card .l { font-size: 11.5px; color: #8A93A6; margin-top: 3px; }
.rpp-meta { display: flex; gap: 18px; flex-wrap: wrap; font-size: 12px; color: #6B7280; }
.rpp-meta code { background: #F1F5F9; padding: 1px 4px; border-radius: 3px; font-size: 11.5px; }
.rpp-sec { background: #fff; border: 1px solid var(--a24-border); border-radius: 10px; padding: 12px 14px; }
.rpp-sec-t { font-size: 12.5px; font-weight: 600; color: #6B7280; margin-bottom: 10px; }
.rpp-heat { display: flex; flex-direction: column; gap: 8px; }
.rpp-heat-cell { position: relative; }
.rpp-heat-cell .bar { position: absolute; inset: 0 auto 0 0; border-radius: 4px; opacity: 0.16; }
.rpp-heat-cell.lv-high .bar { background: #DC2626; }
.rpp-heat-cell.lv-mid .bar { background: #D97706; }
.rpp-heat-cell.lv-low .bar { background: #2563EB; }
.rpp-heat-cell .lbl { position: relative; display: flex; justify-content: space-between; font-size: 12.5px; color: #303133; padding: 4px 8px; }
.rpp-cmp { display: flex; gap: 16px; flex-wrap: wrap; font-size: 12.5px; color: #303133; }
.rpp-missing { margin-top: 8px; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.rpp-missing .t { font-size: 12px; color: #6B7280; }
.rpp-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.rpp-muted { font-size: 12px; color: #9AA4B8; line-height: 1.7; }
.rpp-foot { font-size: 11.5px; color: #9AA4B8; line-height: 1.7; }
</style>
