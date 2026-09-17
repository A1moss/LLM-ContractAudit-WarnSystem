<template>
  <div class="rpt">
    <!-- ══════════ 三态：加载 / 错误 / 无报告（与原实现同口径，不丢功能） ══════════ -->
    <div v-if="ws.reportLoading && !hasAny" class="rpt-state">
      <el-skeleton :rows="5" animated />
    </div>

    <el-alert
      v-else-if="ws.reportError" type="error" show-icon :closable="false"
      :title="ws.reportError" class="rpt-alert"
    >
      <el-button size="small" @click="ws.loadReport()">重试</el-button>
    </el-alert>

    <el-empty v-else-if="!hasAny" description="审核尚未完成，暂无审核报告">
      <el-button
        v-if="ws.contract?.status === 'parsed'" type="primary"
        :loading="ws.auditing" @click="ws.triggerAuditAndPoll()"
      >
        开始审核
      </el-button>
      <el-button v-else-if="ws.contract?.status === 'auditing'" type="primary" loading>审核进行中</el-button>
    </el-empty>

    <template v-else>
      <!-- ══════════════════════════════════════════════════════════════
           A1 审核结论 —— 顶部通栏，只做事实陈述
           数字全部来自后端真实字段；不写「建议不要签署 / 不合格 / 存在重大法律问题」
           ══════════════════════════════════════════════════════════════ -->
      <section class="rpt-conclusion">
        <div class="rpt-conclusion-k">审核结论</div>
        <p class="rpt-conclusion-v">
          <template v-if="totalRisks > 0">
            本次审核发现 <b>{{ totalRisks }}</b> 项风险<template v-if="levelParts.length">（<b>{{ levelParts.join(' / ') }}</b>）</template>
          </template>
          <template v-else>本次审核未发现 R01–R13 风险项</template><template v-if="coveragePct !== null">，条款覆盖率 <b>{{ coveragePct }}%</b></template><template v-if="missingCritical.length > 0">，缺失关键条款 <b>{{ missingCritical.length }}</b> 项</template>。
        </p>
      </section>

      <!-- ══════════════════════════════════════════════════════════════
           A2 风险概览 —— 评分 + 三档计数 + 堆叠分布条
           无饼图、无柱状图、无 emoji
           ══════════════════════════════════════════════════════════════ -->
      <section class="rpt-panel">
        <div class="rpt-metrics">
          <div v-if="riskScore !== null" class="rpt-metric">
            <div class="n">{{ riskScore }}</div>
            <div class="l">风险评分</div>
          </div>
          <div class="rpt-metric">
            <div class="n" :style="{ color: LEVEL_COLORS.high }">{{ counts.high }}</div>
            <div class="l">高风险</div>
          </div>
          <div class="rpt-metric">
            <div class="n" :style="{ color: LEVEL_COLORS.medium }">{{ counts.medium }}</div>
            <div class="l">中风险</div>
          </div>
          <div class="rpt-metric">
            <div class="n" :style="{ color: LEVEL_COLORS.low }">{{ counts.low }}</div>
            <div class="l">低风险</div>
          </div>
          <div v-if="coveragePct !== null" class="rpt-metric">
            <div class="n">{{ coveragePct }}%</div>
            <div class="l">条款覆盖率</div>
          </div>
        </div>

        <template v-if="riskSegments.length">
          <div class="rpt-stack" role="img" aria-label="风险等级分布">
            <span
              v-for="s in riskSegments" :key="s.key"
              class="rpt-stack-seg" :style="{ width: s.pct + '%', background: s.color }"
            />
          </div>
          <div class="rpt-legend">
            <span v-for="s in riskSegments" :key="s.key" class="rpt-legend-i">
              <i :style="{ background: s.color }" />{{ s.label }} <b>{{ s.value }}</b>
            </span>
          </div>
        </template>
      </section>

      <!-- ══════════════════════════════════════════════════════════════
           A3 重点风险 —— 按 高→中→低 分组，组内按判断把握度排序
           每条只给「等级 + 业务名称 + R编号 + reason 摘要 + 详情入口」，
           完整内容在「完整审核报告」；本 Tab 不展开全部风险内容。
           风险名取 business name（如「保密期间不合理」），R 编号弱化。
           ══════════════════════════════════════════════════════════════ -->
      <section class="rpt-sec">
        <div class="rpt-sec-head">
          <h4>重点风险</h4>
          <span class="rpt-sec-note">按风险等级分组，组内按判断把握度排序</span>
        </div>

        <div v-if="!riskGroups.length" class="rpt-none">本次审核未检出风险项。</div>

        <div v-for="g in riskGroups" :key="g.key" class="rpt-group">
          <div class="rpt-group-head">
            <i class="rpt-dot" :style="{ background: g.color }" />
            <span class="rpt-group-t">{{ g.label }}</span>
            <span class="rpt-group-n">{{ g.items.length }} 项</span>
          </div>
          <ul class="rpt-risks">
            <li v-for="r in g.items" :key="r.id" class="rpt-risk">
              <div class="rpt-risk-l">
                <span class="rpt-risk-name">{{ riskName(r.risk_type) }}</span>
                <span class="rpt-risk-code">{{ r.risk_type }}</span>
              </div>
              <p class="rpt-risk-reason">{{ r.reason || '后端未给出判定理由' }}</p>
              <button type="button" class="rpt-risk-more" @click="openRisk(r)">详情 →</button>
            </li>
          </ul>
        </div>
      </section>

      <!-- ══════════════════════════════════════════════════════════════
           A4 条款完整性 —— 无可用的逐条比对数据时整块隐藏（不产出空白模块）
           页面上不出现任何后端字段名
           ══════════════════════════════════════════════════════════════ -->
      <section v-if="comparison" class="rpt-sec">
        <div class="rpt-sec-head">
          <h4>条款完整性</h4>
        </div>

        <div class="rpt-metrics rpt-metrics-sm">
          <div class="rpt-metric">
            <div class="n">{{ coveragePct !== null ? coveragePct + '%' : '—' }}</div>
            <div class="l">覆盖率</div>
          </div>
          <div class="rpt-metric">
            <div class="n">{{ cmpSummary?.total ?? 0 }}</div>
            <div class="l">标准条款</div>
          </div>
          <div class="rpt-metric">
            <div class="n" :style="{ color: CMP_COLORS.covered }">{{ cmpSummary?.covered ?? 0 }}</div>
            <div class="l">已覆盖</div>
          </div>
          <div class="rpt-metric">
            <div class="n" :style="{ color: CMP_COLORS.partial }">{{ cmpSummary?.partial ?? 0 }}</div>
            <div class="l">部分偏离</div>
          </div>
          <div class="rpt-metric">
            <div class="n" :style="{ color: CMP_COLORS.missing }">{{ cmpSummary?.missing ?? 0 }}</div>
            <div class="l">缺失</div>
          </div>
        </div>

        <div v-if="cmpSegments.length" class="rpt-stack" role="img" aria-label="条款覆盖情况分布">
          <span
            v-for="s in cmpSegments" :key="s.key"
            class="rpt-stack-seg" :style="{ width: s.pct + '%', background: s.color }"
          />
        </div>

        <div v-if="missingCritical.length" class="rpt-chips">
          <span class="rpt-chips-k">缺失关键条款</span>
          <el-tag
            v-for="c in missingCritical" :key="c"
            type="danger" size="small" effect="plain"
          >
            {{ c }}
          </el-tag>
        </div>
      </section>

      <!-- ══════════════════════════════════════════════════════════════
           A5 关联风险 —— 仅在后端真的返回了跨条款风险时渲染
           为空时**整个区块不产出**（不显示「暂无关联风险」制造空白模块）
           ══════════════════════════════════════════════════════════════ -->
      <section v-if="crossRisks.length" class="rpt-sec">
        <div class="rpt-sec-head">
          <h4>关联风险</h4>
          <span class="rpt-sec-note">后端跨条款图分析结果</span>
        </div>
        <ul class="rpt-cross">
          <li v-for="(c, i) in crossRisks" :key="i" class="rpt-cross-i">
            <el-tag size="small" :type="c.type === '前置依赖缺失' ? 'danger' : 'warning'">{{ c.type }}</el-tag>
            <span class="rpt-cross-txt">{{ c.risk }}</span>
            <span class="rpt-cross-ref">
              {{ c.clause }}<template v-if="c.depends_on"> → 依赖：{{ c.depends_on }}</template><template v-if="c.conflicts_with"> → 与「{{ c.conflicts_with }}」互斥</template>
            </span>
          </li>
        </ul>
      </section>

      <!-- ══════════════════════════════════════════════════════════════
           A6 操作区 —— 只保留真实存在的两个入口
           后端没有报告导出 / 模板配置能力，因此这里不放任何占位按钮
           ══════════════════════════════════════════════════════════════ -->
      <section class="rpt-actions">
        <div class="rpt-actions-l">
          <el-button type="primary" @click="openFullReport">查看完整审核报告</el-button>
          <el-button @click="goRevise">去修改合同</el-button>
        </div>
        <div class="rpt-actions-r">
          <span v-if="auditTime">审核时间 {{ auditTime }}</span>
          <span v-if="modeLabel">审核方式 {{ modeLabel }}</span>
        </div>
      </section>

      <p class="rpt-foot">
        本区数字为<b>审核时</b>的快照：风险评分与高/中/低计数不随之后的条款比对刷新而变化；
        条款覆盖率取自最近一次比对结果。完整分析见「查看完整审核报告」。
      </p>
    </template>
  </div>
</template>

<script setup>
/**
 * 审核报告 Tab —— **审核结果驾驶舱**
 *
 * 定位（与「完整审核报告」页严格区分）：
 *   本组件 = 总览 + 重点风险摘要 + 条款完整性 + 操作入口；不做正式报告排版。
 *   完整报告 = frontend/src/views/AuditReportDetail.vue（合同信息 / 结论 / 风险完整分析 /
 *             条款逐项比对 / 关联风险 / 合同原文 / 打印）。
 *
 * 数据来源（全部是 workspace 已加载的现有数据，**不新增任何 API**）：
 *   - props.ws.report          ← GET /contracts/{id}/audit-report（切到本 Tab 时按需加载一次）
 *   - props.ws.riskItems       ← GET /contracts/{id}/audit-result（含 clause_text/reason/confidence 等）
 *   - props.ws.clauseComparison← GET /contracts/{id}/clause-comparison
 *   - props.ws.contract        ← GET /contracts/{id}（只取 audit_mode）
 *
 * 真实性约束：
 *   - 风险总数 = 后端 high+mid+low 三个计数相加（后端没有 total 字段，**不伪造**）；
 *   - 条款比对兼容后端三种历史形态（dict / list / null），非 dict 一律视为「无逐条比对」；
 *   - 页面不出现后端字段名，不展示后端没有的能力（导出/模板/审批）。
 */
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { riskName } from '../../constants/riskTypes.js'
import {
  LEVEL_COLORS, CMP_COLORS,
  groupRisksByLevel, sortByConfidence, stackSegments, auditModeLabel,
} from '../../constants/reportTokens.js'
import { formatTime } from '../../utils/format.js'

const props = defineProps({ ws: { type: Object, required: true } })
const router = useRouter()

/** 报告或审核结果任一存在即可渲染（报告 404 但已有风险结果时仍给用户看到真实内容） */
const hasAny = computed(() => !!props.ws.report || (props.ws.riskItems || []).length > 0)

/**
 * 风险计数：优先用审核报告快照（后端 high/mid/low_risk_count，非空整数）；
 * 报告缺失时退化为对当前审核结果的计数——两者都来自后端，不做任何推算。
 */
const counts = computed(() => {
  const rep = props.ws.report
  if (rep) {
    return {
      high: rep.high_risk_count || 0,
      medium: rep.mid_risk_count || 0,
      low: rep.low_risk_count || 0,
    }
  }
  const items = props.ws.riskItems || []
  return {
    high: items.filter((r) => r.risk_level === 'high').length,
    medium: items.filter((r) => r.risk_level === 'medium').length,
    low: items.filter((r) => r.risk_level === 'low').length,
  }
})

const totalRisks = computed(() => counts.value.high + counts.value.medium + counts.value.low)
const riskScore = computed(() => props.ws.report?.risk_score ?? null)

/** 只列出非零档位，如「高 2 / 中 2」（避免出现「低 0」这类噪声） */
const levelParts = computed(() => {
  const c = counts.value
  const out = []
  if (c.high) out.push(`高 ${c.high}`)
  if (c.medium) out.push(`中 ${c.medium}`)
  if (c.low) out.push(`低 ${c.low}`)
  return out
})

/**
 * 条款比对数据：优先用 workspace 已加载的实时比对结果（与「条款比对」Tab 同源同口径），
 * 缺失时回退到报告里的快照。**两种后端历史形态都容忍**：
 *   dict（含 summary/clauses）/ list（旧 R09 格式）/ null → 只有 dict 形态可用。
 */
const comparison = computed(() => {
  const live = props.ws.clauseComparison
  if (live && !Array.isArray(live) && live.summary) return live
  const mc = props.ws.report?.missing_clauses
  if (mc && typeof mc === 'object' && !Array.isArray(mc) && mc.summary) return mc
  return null
})

const cmpSummary = computed(() => comparison.value?.summary || null)

const coveragePct = computed(() => {
  const rate = cmpSummary.value?.coverage_rate
  return typeof rate === 'number' ? Math.round(rate * 100) : null
})

const missingCritical = computed(() => {
  const mc = comparison.value?.missing_critical
  return Array.isArray(mc) ? mc.filter(Boolean) : []
})

const crossRisks = computed(() => {
  const mc = comparison.value?.cross_clause_risks
  return Array.isArray(mc) ? mc : []
})

const riskSegments = computed(() => stackSegments([
  { key: 'high', label: '高风险', value: counts.value.high, color: LEVEL_COLORS.high },
  { key: 'medium', label: '中风险', value: counts.value.medium, color: LEVEL_COLORS.medium },
  { key: 'low', label: '低风险', value: counts.value.low, color: LEVEL_COLORS.low },
]))

const cmpSegments = computed(() => {
  const s = cmpSummary.value
  if (!s) return []
  return stackSegments([
    { key: 'covered', label: '已覆盖', value: s.covered || 0, color: CMP_COLORS.covered },
    { key: 'partial', label: '部分偏离', value: s.partial || 0, color: CMP_COLORS.partial },
    { key: 'missing', label: '缺失', value: s.missing || 0, color: CMP_COLORS.missing },
  ])
})

const riskGroups = computed(() => groupRisksByLevel(props.ws.riskItems).map((g) => ({
  ...g,
  items: sortByConfidence(g.items),
})))

const auditTime = computed(() => (props.ws.report?.created_at ? formatTime(props.ws.report.created_at) : ''))
const modeLabel = computed(() => auditModeLabel(props.ws.contract?.audit_mode))

/** 完整报告页对应风险卡片的锚点（AuditReportDetail 为每条风险渲染 id="risk-<id>"） */
function openRisk(r) {
  router.push({ path: `/audit/report/${props.ws.contractId}`, hash: `#risk-${r.id}` })
}

function openFullReport() {
  router.push(`/audit/report/${props.ws.contractId}`)
}

/** 去修改合同：切到工作台 Tab（不新增任何修改接口，也不替用户选中会话） */
function goRevise() {
  props.ws.setTab('revise')
}
</script>

<style scoped>
/* ── 视觉系统：主色 #1935C3 / 卡白 / 边框 #E5E7EB / 8px 圆角 / 无 hover 阴影 ── */
.rpt { display: flex; flex-direction: column; gap: 12px; }
.rpt-state { padding: 12px 0; }
.rpt-alert { margin: 0; }

/* A1 审核结论 */
.rpt-conclusion {
  background: #fff; border: 1px solid #E5E7EB; border-radius: 8px;
  border-left: 3px solid #1935C3;
  padding: 14px 18px;
}
.rpt-conclusion-k {
  font-size: 12px; font-weight: 600; color: #6B7280;
  letter-spacing: 0.06em; margin-bottom: 6px;
}
.rpt-conclusion-v { margin: 0; font-size: 15px; line-height: 1.8; color: #374151; }
.rpt-conclusion-v b { font-size: 19px; font-weight: 700; color: #111827; padding: 0 2px; }

/* A2 风险概览 */
.rpt-panel {
  background: #fff; border: 1px solid #E5E7EB; border-radius: 8px;
  padding: 14px 16px; display: flex; flex-direction: column; gap: 12px;
}
.rpt-metrics { display: flex; flex-wrap: wrap; gap: 10px; }
.rpt-metric {
  flex: 1 1 108px; min-width: 96px;
  background: #F9FAFB; border: 1px solid #E5E7EB; border-radius: 8px;
  padding: 10px 12px;
}
.rpt-metric .n { font-size: 24px; font-weight: 700; color: #111827; line-height: 1.15; }
.rpt-metric .l { font-size: 12px; color: #6B7280; margin-top: 4px; }
.rpt-metrics-sm { gap: 8px; }
.rpt-metrics-sm .rpt-metric { flex: 1 1 84px; min-width: 78px; padding: 8px 10px; }
.rpt-metrics-sm .rpt-metric .n { font-size: 20px; }

.rpt-stack { display: flex; height: 10px; border-radius: 5px; overflow: hidden; background: #F3F4F6; }
.rpt-stack-seg { display: block; height: 100%; }
.rpt-legend { display: flex; flex-wrap: wrap; gap: 16px; font-size: 12px; color: #6B7280; }
.rpt-legend-i { display: inline-flex; align-items: center; gap: 6px; }
.rpt-legend-i i { width: 8px; height: 8px; border-radius: 2px; }
.rpt-legend-i b { color: #111827; font-weight: 600; }

/* 通用区块 */
.rpt-sec {
  background: #fff; border: 1px solid #E5E7EB; border-radius: 8px;
  padding: 14px 16px;
}
.rpt-sec-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 12px; }
.rpt-sec-head h4 { margin: 0; font-size: 16px; font-weight: 600; color: #111827; }
.rpt-sec-note { font-size: 12px; color: #9CA3AF; }
.rpt-none { font-size: 13px; color: #9CA3AF; }

/* A3 重点风险 */
.rpt-group + .rpt-group { margin-top: 14px; }
.rpt-group-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.rpt-dot { width: 8px; height: 8px; border-radius: 50%; }
.rpt-group-t { font-size: 13px; font-weight: 600; color: #374151; }
.rpt-group-n { font-size: 12px; color: #9CA3AF; }
.rpt-risks { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.rpt-risk {
  display: flex; align-items: flex-start; gap: 12px;
  background: #F9FAFB; border: 1px solid #E5E7EB; border-radius: 8px;
  padding: 10px 12px;
}
.rpt-risk-l { flex: 0 0 auto; display: flex; align-items: baseline; gap: 6px; min-width: 168px; }
.rpt-risk-name { font-size: 14px; font-weight: 600; color: #111827; }
.rpt-risk-code { font-family: ui-monospace, Consolas, monospace; font-size: 11.5px; color: #9CA3AF; }
.rpt-risk-reason {
  flex: 1 1 auto; min-width: 0; margin: 0;
  font-size: 13px; line-height: 1.7; color: #374151;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.rpt-risk-more {
  flex: 0 0 auto; align-self: center;
  background: none; border: none; padding: 0; cursor: pointer;
  font-size: 12.5px; color: #1935C3;
}
.rpt-risk-more:hover { text-decoration: underline; }

/* A4 缺失关键条款 */
.rpt-chips { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 12px; }
.rpt-chips-k { font-size: 12px; font-weight: 600; color: #6B7280; margin-right: 2px; }

/* A5 关联风险 */
.rpt-cross { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.rpt-cross-i {
  display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap;
  background: #F9FAFB; border: 1px solid #E5E7EB; border-radius: 8px; padding: 8px 12px;
}
.rpt-cross-txt { font-size: 13px; color: #374151; }
.rpt-cross-ref { font-size: 12px; color: #6B7280; }

/* A6 操作区 */
.rpt-actions {
  background: #fff; border: 1px solid #E5E7EB; border-radius: 8px;
  padding: 12px 16px;
  display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap;
}
.rpt-actions-l { display: flex; gap: 10px; flex-wrap: wrap; }
.rpt-actions-r { display: flex; gap: 18px; flex-wrap: wrap; font-size: 12px; color: #6B7280; }

.rpt-foot { margin: 0; font-size: 12px; line-height: 1.8; color: #9CA3AF; }
.rpt-foot b { color: #6B7280; }

@media (max-width: 900px) {
  .rpt-risk { flex-direction: column; gap: 6px; }
  .rpt-risk-l { min-width: 0; }
  .rpt-risk-more { align-self: flex-start; }
  .rpt-actions { flex-direction: column; align-items: stretch; }
}
</style>
