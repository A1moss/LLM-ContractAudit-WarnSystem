<template>
  <div class="rp-page">
    <!-- ══════════════════════════════════════════════════════════════════
         工具条（打印时整体隐藏）
         ══════════════════════════════════════════════════════════════════ -->
    <div class="rp-toolbar">
      <div class="rp-toolbar-l">
        <div class="rp-crumb">审核中心 / 审核报告 / <b>报告详情</b></div>
        <el-button text size="small" @click="router.push('/audit/report')">
          <el-icon><ArrowLeft /></el-icon>返回报告列表
        </el-button>
      </div>
      <div class="rp-toolbar-r">
        <el-button text size="small" @click="router.push(`/audit/result/${contractId}`)">查看审核历史</el-button>
        <el-button type="primary" size="small" @click="printReport">
          <el-icon><Printer /></el-icon>打印报告
        </el-button>
      </div>
    </div>

    <!-- 加载 -->
    <div v-if="loading" class="rp-state"><el-skeleton :rows="8" animated /></div>

    <!-- 错误 -->
    <div v-else-if="error" class="rp-state">
      <el-result icon="error" :title="error">
        <template #extra>
          <el-button type="primary" @click="reload">重新加载</el-button>
          <el-button @click="router.push('/audit/report')">返回报告列表</el-button>
        </template>
      </el-result>
    </div>

    <!-- ══════════════════════════════════════════════════════════════════
         正式报告主体
         所有数值 / 文本均来自后端真实字段；缺字段就隐藏该项，不做任何推导
         ══════════════════════════════════════════════════════════════════ -->
    <article v-else class="rp-doc">
      <!-- ══════════ B1 报告头部 ══════════ -->
      <header class="rp-head">
        <h1 class="rp-title">{{ REPORT_TITLE }}</h1>
        <div v-if="headSub" class="rp-head-sub">{{ headSub }}</div>
        <div v-if="headMeta.length" class="rp-head-meta">
          <span v-for="m in headMeta" :key="m">{{ m }}</span>
        </div>

        <div v-if="infoCells.length" class="rp-grid">
          <div v-for="c in infoCells" :key="c.key" class="rp-cell">
            <div class="rp-cell-k">{{ c.label }}</div>
            <div class="rp-cell-v">{{ c.value }}</div>
            <div v-if="c.sub" class="rp-cell-sub">{{ c.sub }}</div>
          </div>
        </div>

        <div v-if="governingLaw" class="rp-grid-wide">
          <div class="rp-cell-k">适用法律</div>
          <div class="rp-cell-v rp-cell-v-sm">{{ governingLaw }}</div>
        </div>
      </header>

      <!-- 审核结果有效性提示（真实字段 has_current_result） -->
      <el-alert
        v-if="!hasCurrentResult && riskItems.length"
        type="error" show-icon :closable="false" class="rp-alert"
        title="该合同的当前审核结果已被驳回，以下内容为已失效的历史审核结果"
      />

      <!-- ══════════ 01 审核结论 ══════════ -->
      <section class="rp-sec">
        <h2 class="rp-h2"><span class="rp-h2-no">{{ secNo('conclusion') }}</span>审核结论</h2>
        <p class="rp-p rp-p-lg">
          <template v-if="totalRisks > 0">
            本次审核共识别 <b>{{ totalRisks }}</b> 项风险，其中<template v-if="levelParts.length"><b>{{ levelParts.join('、') }}</b></template><template v-else>各等级均无检出</template>。<template v-if="coveragePct !== null">条款完整性覆盖率为 <b>{{ coveragePct }}%</b>（标准条款 {{ cmpSummary?.total ?? 0 }} 条，已覆盖 {{ cmpSummary?.covered ?? 0 }} 条、部分偏离 {{ cmpSummary?.partial ?? 0 }} 条、缺失 {{ cmpSummary?.missing ?? 0 }} 条）<template v-if="missingCritical.length">，另有 <b>{{ missingCritical.length }}</b> 项关键条款缺失</template>。</template>
          </template>
          <template v-else>本次审核未识别到 R01–R13 风险项。<template v-if="coveragePct !== null">条款完整性覆盖率为 <b>{{ coveragePct }}%</b><template v-if="missingCritical.length">，另有 <b>{{ missingCritical.length }}</b> 项关键条款缺失</template>。</template></template>
        </p>
        <p class="rp-p rp-note">建议结合下方「风险问题明细」与「条款完整性检查」结果进行进一步复核。</p>
        <p class="rp-disclaimer">{{ REPORT_DISCLAIMER }}</p>
      </section>

      <!-- ══════════ 02 风险概览 ══════════ -->
      <section class="rp-sec">
        <h2 class="rp-h2"><span class="rp-h2-no">{{ secNo('overview') }}</span>风险概览</h2>
        <div class="rp-overview">
          <div v-if="riskScore !== null" class="rp-ring">
            <el-progress
              type="circle" :percentage="scorePct" :width="132" :stroke-width="10"
              :color="ringColor"
            >
              <template #default>
                <div class="rp-ring-n">{{ riskScore }}</div>
                <div class="rp-ring-l">风险评分</div>
              </template>
            </el-progress>
          </div>
          <div class="rp-dist">
            <div v-for="d in levelDist" :key="d.key" class="rp-dist-row">
              <span class="rp-dist-l">{{ d.label }}</span>
              <span class="rp-dist-n">{{ d.value }}</span>
              <span class="rp-dist-bar">
                <i :style="{ width: d.pct + '%', background: d.color }" />
              </span>
            </div>
            <div v-if="!levelDist.length" class="rp-none">本次审核未检出风险项。</div>
          </div>
        </div>
      </section>

      <!-- ══════════ 03 风险问题明细 ══════════ -->
      <section class="rp-sec">
        <h2 class="rp-h2"><span class="rp-h2-no">{{ secNo('risks') }}</span>风险问题明细</h2>

        <div v-if="!riskGroups.length" class="rp-none">本次审核未检出风险项。</div>

        <div v-for="g in riskGroups" :key="g.key" class="rp-rgroup">
          <h3 class="rp-h3">
            <i class="rp-dot" :style="{ background: g.color }" />{{ g.label }}
            <span class="rp-h3-n">{{ g.items.length }} 项</span>
          </h3>

          <article
            v-for="v in g.items" :key="v.id"
            :id="riskAnchorId(v.id)" class="rp-risk"
          >
            <div class="rp-risk-head">
              <span class="rp-badge" :style="{ background: v.levelColor }">{{ v.levelLabel }}</span>
              <span class="rp-risk-name">{{ v.name }}</span>
              <span class="rp-risk-code">{{ v.code }}</span>
            </div>

            <div class="rp-field">
              <div class="rp-field-k">涉及条款<template v-if="v.clauseNoText"> · {{ v.clauseNoText }}</template></div>
              <blockquote v-if="v.clauseText" class="rp-quote">{{ v.clauseText }}</blockquote>
              <div v-else class="rp-none rp-none-sm">后端未返回该风险对应的条款原文。</div>
            </div>

            <div v-if="v.anchor" class="rp-field">
              <div class="rp-field-k">原文证据</div>
              <blockquote class="rp-quote">{{ v.anchor }}</blockquote>
              <div v-if="v.anchorRange" class="rp-field-note">{{ v.anchorRange }}</div>
            </div>

            <div v-if="v.reason" class="rp-field">
              <div class="rp-field-k">风险分析</div>
              <div class="rp-field-v">{{ v.reason }}</div>
            </div>

            <div class="rp-field">
              <div class="rp-field-k">修改建议</div>
              <div v-if="v.suggestion" class="rp-field-v">{{ v.suggestion }}</div>
              <div v-else class="rp-none rp-none-sm">后端未给出修改建议。</div>
              <div v-if="v.example" class="rp-sub-field">
                <span class="rp-sub-k">修改示例</span>
                <span class="rp-field-v">{{ v.example }}</span>
              </div>
            </div>

            <div v-if="v.riskDescription" class="rp-field">
              <div class="rp-field-k">风险说明</div>
              <div class="rp-field-v">{{ v.riskDescription }}</div>
            </div>

            <div v-if="v.legalBasis || v.evidenceLaw" class="rp-field">
              <div class="rp-field-k">法律依据</div>
              <div v-if="v.legalBasis" class="rp-field-v">{{ v.legalBasis }}</div>
              <div v-if="v.evidenceLaw" class="rp-sub-field">
                <span class="rp-sub-k">{{ v.legalBasis ? '命中法条' : '依据' }}</span>
                <span class="rp-field-v">{{ v.evidenceLaw }}</span>
              </div>
            </div>

            <div v-if="v.groundingWarning" class="rp-warn">
              该建议含未经证据核实的数值，请结合合同原文确认。<template v-if="v.groundingIssues.length">（{{ v.groundingIssues.join('；') }}）</template>
            </div>

            <div class="rp-risk-foot">
              <div class="rp-risk-meta">
                <span>{{ v.method }} · 判断把握度 {{ v.confidencePct }}%</span>
                <span class="rp-locate" :class="'is-' + v.locate.key">{{ v.locate.text }}</span>
              </div>
              <div class="rp-risk-actions">
                <el-button
                  v-if="v.locate.ranges.length" size="small"
                  @click="openText(v)"
                >查看原文</el-button>
                <el-button size="small" type="primary" plain @click="goRevise(v)">去修改此条款</el-button>
              </div>
            </div>

            <!-- 风险卡间连续浏览：只在**风险卡之间**移动，不进入条款完整性 / 合同原文等章节 -->
            <div class="rp-risk-nav">
              <el-button
                size="small" text :disabled="!canGotoRisk(v.id, -1)"
                @click="gotoRiskOffset(v.id, -1)"
              >← 上一项风险</el-button>
              <span class="rp-risk-nav-pos">{{ riskIndex(v.id) + 1 }} / {{ flatRisks.length }}</span>
              <el-button
                size="small" text :disabled="!canGotoRisk(v.id, 1)"
                @click="gotoRiskOffset(v.id, 1)"
              >下一项风险 →</el-button>
            </div>
          </article>
        </div>
      </section>

      <!-- ══════════ 04 条款完整性检查 ══════════ -->
      <!-- id="clauses"：供「审核报告 Tab → 条款完整性问题 Tag」降级定位使用。
           消费方 = views/contract-detail/ReportPanel.vue（openClause → hash `#clauses`）。
           本页目前**没有**条款卡片级 anchor（唯一锚点是风险卡的 #risk-<id>），
           因此按约定只提供章节级锚点，不做下标猜测。 -->
      <section id="clauses" class="rp-sec">
        <h2 class="rp-h2"><span class="rp-h2-no">{{ secNo('clauses') }}</span>条款完整性检查</h2>

        <template v-if="comparison">
          <div class="rp-metrics">
            <div class="rp-metric">
              <div class="rp-metric-n">{{ coveragePct !== null ? coveragePct + '%' : '—' }}</div>
              <div class="rp-metric-l">覆盖率</div>
            </div>
            <div class="rp-metric">
              <div class="rp-metric-n">{{ cmpSummary?.total ?? 0 }}</div>
              <div class="rp-metric-l">标准条款</div>
            </div>
            <div class="rp-metric">
              <div class="rp-metric-n" :style="{ color: CMP_COLORS.covered }">{{ cmpSummary?.covered ?? 0 }}</div>
              <div class="rp-metric-l">已覆盖</div>
            </div>
            <div class="rp-metric">
              <div class="rp-metric-n" :style="{ color: CMP_COLORS.partial }">{{ cmpSummary?.partial ?? 0 }}</div>
              <div class="rp-metric-l">部分偏离</div>
            </div>
            <div class="rp-metric">
              <div class="rp-metric-n" :style="{ color: CMP_COLORS.missing }">{{ cmpSummary?.missing ?? 0 }}</div>
              <div class="rp-metric-l">缺失</div>
            </div>
          </div>

          <div v-if="cmpSegments.length" class="rp-stack" role="img" aria-label="条款覆盖情况分布">
            <span
              v-for="s in cmpSegments" :key="s.key"
              class="rp-stack-seg" :style="{ width: s.pct + '%', background: s.color }"
            />
          </div>
          <div v-if="cmpSegments.length" class="rp-stack-legend">
            <span v-for="s in cmpSegments" :key="s.key" class="rp-stack-legend-i">
              <i :style="{ background: s.color }" />{{ s.label }} <b>{{ s.value }}</b>
            </span>
          </div>

          <div v-if="missingCritical.length" class="rp-critical">
            <div class="rp-critical-k">缺失关键条款</div>
            <div class="rp-critical-tags">
              <el-tag v-for="c in missingCritical" :key="c" type="danger" size="small" effect="plain">{{ c }}</el-tag>
            </div>
          </div>

          <h3 v-if="clauses.length" class="rp-h3 rp-h3-plain">
            标准条款逐项比对<span class="rp-h3-n">共 {{ clauses.length }} 条</span>
          </h3>
          <div v-for="(c, i) in clauses" :key="(c.title || '') + i" class="rp-clause">
            <div class="rp-clause-head">
              <span class="rp-badge" :style="{ background: cmpStatusColor(c.status) }">{{ cmpStatusLabel(c.status) }}</span>
              <span class="rp-clause-title">{{ c.title || '未命名条款' }}</span>
              <span v-if="c.priority" class="rp-clause-pri">{{ priorityLabel(c.priority) }}</span>
              <span v-if="typeof c.similarity === 'number'" class="rp-clause-sim">相似度 {{ Math.round(c.similarity * 100) }}%</span>
            </div>
            <div class="rp-clause-body">
              <div class="rp-field">
                <div class="rp-field-k">合同中匹配条款</div>
                <blockquote v-if="c.matched_text" class="rp-quote">{{ c.matched_text }}</blockquote>
                <div v-else class="rp-none rp-none-sm">本合同中未匹配到对应条款。</div>
              </div>
              <div v-if="c.deviation" class="rp-field">
                <div class="rp-field-k">偏离说明</div>
                <div class="rp-field-v">{{ cleanAdviceText(c.deviation) }}</div>
              </div>
              <div v-if="c.completion" class="rp-field">
                <div class="rp-field-k">补全建议</div>
                <div class="rp-field-v">{{ cleanAdviceText(c.completion) }}</div>
              </div>
              <div v-if="c.risk" class="rp-field">
                <div class="rp-field-k">风险说明</div>
                <div class="rp-field-v">{{ cleanAdviceText(c.risk) }}</div>
              </div>
              <div v-if="c.related_law" class="rp-field">
                <div class="rp-field-k">相关法条</div>
                <div class="rp-field-v">{{ c.related_law }}</div>
              </div>
            </div>
          </div>
        </template>

        <div v-else class="rp-none">
          本次审核报告中未包含条款比对结果（审核时比对未完成或该次比对未产出逐条结果）。
        </div>
      </section>

      <!-- ══════════ 05 关联风险（仅在真实存在时渲染） ══════════ -->
      <section v-if="crossRisks.length" class="rp-sec">
        <h2 class="rp-h2"><span class="rp-h2-no">{{ secNo('cross') }}</span>关联风险</h2>
        <ul class="rp-cross">
          <li v-for="(c, i) in crossRisks" :key="i" class="rp-cross-i">
            <el-tag size="small" :type="c.type === '前置依赖缺失' ? 'danger' : 'warning'">{{ c.type }}</el-tag>
            <div class="rp-cross-body">
              <div class="rp-field-v">{{ cleanAdviceText(c.risk) }}</div>
              <div class="rp-cross-ref">
                {{ c.clause }}<template v-if="c.depends_on"> → 依赖：{{ c.depends_on }}</template><template v-if="c.conflicts_with"> → 与「{{ c.conflicts_with }}」互斥</template>
              </div>
            </div>
          </li>
        </ul>
      </section>

      <!-- ══════════ 06 合同原文 ══════════ -->
      <section class="rp-sec">
        <h2 class="rp-h2"><span class="rp-h2-no">{{ secNo('original') }}</span>合同原文</h2>

        <div v-if="pdfLoading" class="rp-pdf-state">
          <el-icon class="is-loading" :size="22"><Loading /></el-icon>
          <p>正在加载合同原文…</p>
          <p class="rp-pdf-hint">Word 文档由后端实时转换为 PDF，首次加载可能需要数秒。</p>
        </div>

        <template v-else-if="pdfReady">
          <!-- 工具栏（打印时隐藏），小于 900px 自动切单页 -->
          <div class="rp-pdf-toolbar">
            <div class="rp-pdf-nav">
              <el-button size="small" :disabled="!canPrev" @click="prevPage">
                <el-icon><ArrowLeft /></el-icon>上一页
              </el-button>
              <span class="rp-pdf-label">{{ pageLabel }}</span>
              <el-button size="small" :disabled="!canNext" @click="nextPage">
                下一页<el-icon><ArrowRight /></el-icon>
              </el-button>
            </div>
            <div class="rp-pdf-jump">
              <span>跳至</span>
              <el-input
                v-model="jumpPage" size="small" class="rp-pdf-jump-input"
                placeholder="页码" @keyup.enter="jumpTo"
              />
              <span>页</span>
              <el-button size="small" text @click="jumpTo">跳转</el-button>
            </div>
          </div>

          <div class="rp-pdf-viewport">
            <div class="rp-pdf-slot">
              <canvas ref="leftCanvasRef" class="rp-pdf-canvas" />
            </div>
            <div v-if="!isNarrow" class="rp-pdf-slot">
              <canvas ref="rightCanvasRef" class="rp-pdf-canvas" />
            </div>
          </div>
        </template>

        <div v-else class="rp-pdf-state">
          <el-icon :size="22"><Warning /></el-icon>
          <p>{{ pdfError || '合同原文暂不可用' }}</p>
        </div>
      </section>
    </article>

    <!-- ══════════════════════════════════════════════════════════════════
         悬浮返回：固定在视口右下角（z-index 1000，任意滚动位置可见）。
         蓝色实心主按钮样式（与「查看完整审核报告」同一主色），左侧 ← 图标；打印时隐藏。
         回到合同详情的「审核报告」Tab（?tab=audit，由 ContractDetail 读取 query 落位）。
         ══════════════════════════════════════════════════════════════════ -->
    <div v-if="contractId" class="rp-back">
      <button type="button" class="rp-back-btn" @click="backToAuditTab">
        <el-icon class="rp-back-arrow"><ArrowLeft /></el-icon>返回审核报告
      </button>
    </div>

    <!-- ══════════════════════════════════════════════════════════════════
         原文定位对话框 —— 只做**逐字匹配**，不推算页码 / 坐标 / 高亮位置
         ══════════════════════════════════════════════════════════════════ -->
    <el-dialog
      v-model="viewer.visible" :title="viewer.title" width="880px"
      append-to-body class="rp-dialog"
    >
      <div class="rp-viewer">
        <div class="rp-viewer-bar">
          <span class="rp-viewer-note">{{ viewer.note }}</span>
          <span v-if="viewer.ranges.length > 1" class="rp-viewer-step">
            <el-button size="small" text @click="stepMatch(-1)">上一处</el-button>
            <span>{{ viewer.index + 1 }} / {{ viewer.ranges.length }}</span>
            <el-button size="small" text @click="stepMatch(1)">下一处</el-button>
          </span>
        </div>
        <div class="rp-viewer-body">
          <pre class="rp-viewer-text"><span>{{ viewerView.before }}</span><mark ref="markRef" class="rp-viewer-mark">{{ viewerView.match }}</mark><span>{{ viewerView.after }}</span></pre>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
/**
 * 完整审核报告 —— **正式《合同智能审核报告》**
 *
 * 定位（与「审核报告」Tab 严格区分）：
 *   Tab（contract-detail/ReportPanel.vue）= 审核结果驾驶舱（结论 + 数字 + 重点风险摘要 + 入口）。
 *   本页 = 正式报告（合同信息 / 审核结论 / 风险完整分析 / 条款逐项比对 / 关联风险 / 合同原文 / 打印）。
 *
 * 数据来源（全部为**现有 API**，本轮未新增任何后端接口 / 未改动后端）：
 *   - GET /contracts/{id}/audit-report     → 报告快照（评分、三档计数、missing_clauses、created_at）
 *   - GET /contracts/{id}/audit-result     → 风险明细（含 clause_position / evidence / recommendation）
 *   - GET /contracts/{id}                  → 合同信息（file_name / contract_type / extracted_elements / parsed_text）
 *   - GET /contracts/{id}/file             → 合同原文（二进制，供 pdf.js 渲染）
 *
 * 真实性约束（本轮硬性要求）：
 *   1. 标题固定为「合同智能审核报告」——后端没有「合同正式名称」字段，**绝不**按合同类型推导；
 *   2. 头部不出现 数据库 ID / audit_batch / UUID；
 *   3. 风险原文、风险分析、修改建议、法律依据**完整展示**，不做 80 字截断、不放进 tooltip；
 *   4. 原文定位只做「逐字匹配」，三态如实呈现，**不伪造页码 / 坐标 / 高亮位置**；
 *   5. 合同金额、当事人、签订日期等一律取自 extracted_elements，字段缺失即隐藏；
 *   6. 不放后端并不存在的导出能力（PDF / Word / 报告模板）——只提供真实可用的浏览器打印。
 *
 * 刻意不做的事：
 *   - **不调用 GET /clause-comparison**：该接口在无缓存时会当场跑 LLM 比对并回写报告，
 *     而正式报告页应当是「一次审核结果的只读呈现」，不应因打开页面触发后端重算。
 *     条款比对数据只取报告快照里的那份（兼容 dict / list / null 三种后端历史形态）。
 */
import { ref, reactive, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft, ArrowRight, Printer, Warning, Loading } from '@element-plus/icons-vue'
import * as pdfjsLib from 'pdfjs-dist'
import pdfjsWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { getAuditReport, getAuditResult, getContractDetail, getContractFile } from '../api/contract.js'
import { riskName, riskLevelLabel, detectionLabel, CMP_STATUS_LABELS } from '../constants/riskTypes.js'
import { typeLabel } from '../constants/contractTypes.js'
import {
  REPORT_TITLE, REPORT_DISCLAIMER, LEVEL_COLORS, CMP_COLORS,
  groupRisksByLevel, sortByConfidence, stackSegments, priorityLabel,
  auditModeLabel, contractStatusLabel, amountParts, performancePeriodText,
  scoreColor, cleanAdviceText,
} from '../constants/reportTokens.js'
import { cnNo } from '../composables/workspaceLogic.js'
import { contractDetailPath, riskAnchorId, riskIdFromHash } from '../constants/navigation.js'
import { formatTime } from '../utils/format.js'

const route = useRoute()
const router = useRouter()
const contractId = computed(() => String(route.params.contractId || ''))

// ── 数据 ──
const report = ref(null)
const riskItems = ref([])
const contract = ref(null)
const hasCurrentResult = ref(true)
const loading = ref(true)
const error = ref('')

// 请求序列号：路由快速切换时丢弃过期请求的结果
let requestSeq = 0

function reportErrorText(e) {
  const s = e?.response?.status
  if (s === 404) return '该合同暂无审核报告'
  return e?.response?.data?.detail || '加载审核报告失败'
}

/**
 * 并发加载四个数据源（各自独立容错）。
 * 与旧实现的区别：不再用 Promise.all 硬绑定——某一路失败不会让整页变成错误页，
 * 缺哪一块就隐藏哪一块（例如没有合同信息就隐藏信息网格）。
 */
async function fetchAll(seq) {
  const id = contractId.value
  if (!id) {
    error.value = '缺少合同 ID 参数'
    loading.value = false
    return
  }

  const [rep, res, con] = await Promise.allSettled([
    getAuditReport(id),
    getAuditResult(id),
    getContractDetail(id),
  ])
  if (seq !== undefined && seq !== requestSeq) return

  report.value = rep.status === 'fulfilled' ? (rep.value?.data || null) : null
  riskItems.value = res.status === 'fulfilled' ? (res.value?.data?.items || []) : []
  hasCurrentResult.value = res.status === 'fulfilled' ? (res.value?.data?.has_current_result ?? true) : true
  contract.value = con.status === 'fulfilled' ? (con.value?.data || null) : null

  // 真正的失败：报告取不到且审核结果也没有 → 没有可呈现的内容
  if (!report.value && !riskItems.value.length) {
    error.value = rep.status === 'rejected' ? reportErrorText(rep.reason) : '该合同暂无审核报告'
  } else {
    error.value = ''
  }
  loading.value = false
}

async function reload() {
  const seq = ++requestSeq
  loading.value = true
  error.value = ''
  report.value = null
  riskItems.value = []
  contract.value = null
  await fetchAll(seq)
}

// ── 报告头部 ──
const elements = computed(() => {
  const e = contract.value?.extracted_elements
  return e && typeof e === 'object' && !Array.isArray(e) ? e : null
})

const fileName = computed(() => contract.value?.file_name || '')
const typeText = computed(() => {
  const c = contract.value
  if (!c) return ''
  return c.type_label || typeLabel(c.contract_type)
})

/** 副标题只拼真实存在的字段（类型 / 文件名），缺失即不产出 */
const headSub = computed(() => [typeText.value, fileName.value].filter(Boolean).join(' · '))

const auditTime = computed(() => (report.value?.created_at ? formatTime(report.value.created_at) : ''))
const modeText = computed(() => (auditModeLabel(contract.value?.audit_mode) ? `审核方式 ${auditModeLabel(contract.value.audit_mode)}` : ''))
const statusText = computed(() => (contractStatusLabel(contract.value?.status) ? `审核状态 ${contractStatusLabel(contract.value.status)}` : ''))
const headMeta = computed(() => [
  auditTime.value ? `审核时间 ${auditTime.value}` : '',
  modeText.value,
  statusText.value,
].filter(Boolean))

const counts = computed(() => {
  const rep = report.value
  if (rep) {
    return {
      high: rep.high_risk_count || 0,
      medium: rep.mid_risk_count || 0,
      low: rep.low_risk_count || 0,
    }
  }
  const items = riskItems.value || []
  return {
    high: items.filter((r) => r.risk_level === 'high').length,
    medium: items.filter((r) => r.risk_level === 'medium').length,
    low: items.filter((r) => r.risk_level === 'low').length,
  }
})

const totalRisks = computed(() => counts.value.high + counts.value.medium + counts.value.low)
const riskScore = computed(() => (report.value?.risk_score ?? null))

/**
 * 评分环颜色：沿用项目既有 scoreColor 的三档阈值（≥60 红 / ≥30 橙 / 其余 蓝，
 * 与 views/AuditReport.vue 的实现完全一致，本轮**没有新增阈值**），
 * 色值取本页既有的「高风险」红，使评分环与等级分布条 / 高风险标签同色。
 * 90 分落入 ≥60 档 → 红色。
 */
const ringColor = computed(() => scoreColor(riskScore.value))

const scorePct = computed(() => {
  const v = Number(riskScore.value)
  if (!Number.isFinite(v)) return 0
  return Math.min(100, Math.max(0, v))
})

const levelParts = computed(() => {
  const c = counts.value
  const out = []
  if (c.high) out.push(`高风险 ${c.high} 项`)
  if (c.medium) out.push(`中风险 ${c.medium} 项`)
  if (c.low) out.push(`低风险 ${c.low} 项`)
  return out
})

/** 报告头部信息网格：只放后端真实存在的字段 */
const infoCells = computed(() => {
  const out = []
  const el = elements.value

  // 当事人：后端 parties 是对象，键由后端决定（真实数据为 甲方 / 乙方），逐项如实展示
  const parties = el?.parties
  if (parties && typeof parties === 'object' && !Array.isArray(parties)) {
    for (const [k, v] of Object.entries(parties)) {
      if (v) out.push({ key: `party-${k}`, label: k, value: String(v) })
    }
  }

  if (typeText.value) out.push({ key: 'type', label: '合同类型', value: typeText.value })

  const amt = amountParts(el?.amount)
  if (amt.main || amt.text) {
    out.push({ key: 'amount', label: '合同金额', value: amt.main || amt.text, sub: amt.main ? amt.text : '' })
  }

  if (el?.sign_date) out.push({ key: 'sign', label: '签订日期', value: String(el.sign_date) })

  const period = performancePeriodText(el?.performance_period)
  if (period) out.push({ key: 'period', label: '履行期限', value: period })

  if (el?.dispute_resolution) out.push({ key: 'dispute', label: '争议解决', value: String(el.dispute_resolution) })

  if (riskScore.value !== null) out.push({ key: 'score', label: '风险评分', value: String(riskScore.value) })
  if (report.value) {
    out.push({ key: 'risks', label: '风险项', value: `${totalRisks.value} 项`, sub: levelParts.value.join('、') })
  }
  return out
})

const governingLaw = computed(() => {
  const v = elements.value?.governing_law
  return typeof v === 'string' && v.trim() ? v.trim() : ''
})

// ── 条款比对（只取报告快照） ──
const comparison = computed(() => {
  const mc = report.value?.missing_clauses
  if (!mc || typeof mc !== 'object' || Array.isArray(mc)) return null
  return mc.summary ? mc : null
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
const clauses = computed(() => {
  const list = comparison.value?.clauses
  return Array.isArray(list) ? list.filter((c) => c && typeof c === 'object') : []
})
const cmpSegments = computed(() => {
  const s = cmpSummary.value
  if (!s) return []
  return stackSegments([
    { key: 'covered', label: '已覆盖', value: s.covered || 0, color: CMP_COLORS.covered },
    { key: 'partial', label: '部分偏离', value: s.partial || 0, color: CMP_COLORS.partial },
    { key: 'missing', label: '缺失', value: s.missing || 0, color: CMP_COLORS.missing },
  ])
})

function cmpStatusLabel(status) {
  return CMP_STATUS_LABELS[status] || status || '—'
}
function cmpStatusColor(status) {
  return CMP_COLORS[status] || '#6B7280'
}

// ── 章节编号（关联风险缺失时不占用编号，避免出现断号） ──
const secNos = computed(() => {
  let n = 0
  const m = {}
  m.conclusion = ++n
  m.overview = ++n
  m.risks = ++n
  m.clauses = ++n
  if (crossRisks.value.length) m.cross = ++n
  m.original = ++n
  return m
})
function secNo(key) {
  const v = secNos.value[key]
  return v ? String(v).padStart(2, '0') : ''
}

// ── 风险原文定位（只做逐字匹配） ──
/** 逐字检索全部命中区间（上限 200，避免超长正文卡顿） */
function findRanges(text, needle) {
  const out = []
  const n = String(needle || '').trim()
  if (!text || n.length < 2) return out
  let i = text.indexOf(n)
  while (i >= 0 && out.length < 200) {
    out.push([i, i + n.length])
    i = text.indexOf(n, i + n.length)
  }
  return out
}

/**
 * 风险的原文定位三态（口径与 useContractWorkspace.riskRanges 一致）：
 *   anchored   审核阶段已建立锚点，且锚点与当前正文**逐字一致**（才认它）
 *   searchable 无可用锚点，退化为对「锚点原文 / 涉及条款原文」的逐字检索
 *   none / unavailable   找不到 → 不显示定位按钮，也绝不伪造位置
 */
function locateOf(r) {
  const text = contract.value?.parsed_text || ''
  if (!text) return { key: 'unavailable', text: '合同正文不可用', ranges: [] }

  const pos = r.clause_position && typeof r.clause_position === 'object' ? r.clause_position : {}
  const anchor = typeof pos.original_text === 'string' ? pos.original_text.trim() : ''

  if (
    anchor
    && typeof pos.start === 'number' && typeof pos.end === 'number'
    && pos.start >= 0 && pos.end <= text.length && pos.end > pos.start
    && text.slice(pos.start, pos.end).trim() === anchor
  ) {
    return { key: 'anchored', text: '已定位原文', ranges: [[pos.start, pos.end]] }
  }

  const ranges = findRanges(text, anchor || r.clause_text)
  if (ranges.length) {
    return { key: 'searchable', text: `可定位（原文逐字匹配 ${ranges.length} 处）`, ranges }
  }
  return { key: 'none', text: '未在原文中定位到该条款', ranges: [] }
}

function clauseNoText(r) {
  const pos = r.clause_position && typeof r.clause_position === 'object' ? r.clause_position : null
  const no = pos?.clause_no
  if (no === null || no === undefined || no === '') return ''
  const cn = cnNo(no)
  const head = cn ? `第${cn}条` : `第 ${no} 条`
  return pos.clause_title ? `${head} · ${pos.clause_title}` : head
}

/** 风险的展示视图（一次性算好，模板里不做重复计算） */
const riskViews = computed(() => {
  const m = new Map()
  for (const r of riskItems.value) {
    const pos = r.clause_position && typeof r.clause_position === 'object' ? r.clause_position : {}
    const anchor = typeof pos.original_text === 'string' ? pos.original_text : ''
    const rec = r.recommendation && typeof r.recommendation === 'object' ? r.recommendation : {}
    const ev = r.evidence && typeof r.evidence === 'object' ? r.evidence : {}
    m.set(r.id, {
      id: r.id,
      level: r.risk_level,
      levelLabel: riskLevelLabel(r.risk_level),
      levelColor: LEVEL_COLORS[r.risk_level] || '#6B7280',
      name: riskName(r.risk_type),
      code: r.risk_type || '',
      clauseNoText: clauseNoText(r),
      clauseText: r.clause_text || '',
      anchor,
      anchorRange: (typeof pos.start === 'number' && typeof pos.end === 'number')
        ? `字符区间 ${pos.start}–${pos.end}` : '',
      // 建议层文本经展示层清理：后端 LLM 生成的文本会夹带内部字段名标记
      // （如 "（has_consideration为false，consideration_text为空）"），只做显示替换，不改数据。
      // 注意：clauseText / anchor 是合同正文类内容，**必须逐字原样**，绝不清洗。
      reason: cleanAdviceText(r.reason),
      suggestion: cleanAdviceText(r.suggestion),
      example: cleanAdviceText(rec.example),
      riskDescription: cleanAdviceText(rec.risk_description),
      legalBasis: cleanAdviceText(rec.legal_basis),
      evidenceLaw: ev.law || '',
      method: detectionLabel(r.detection_method),
      confidencePct: Math.round((r.confidence || 0) * 100),
      // 接地检查判据：后端只返回 recommendation.grounding = {passed, issues[]}；
      // `grounding_warning` 是 workspace 的派生字段，本页用的是原始 /audit-result，
      // 因此这里**按同一口径**自行判定，而不是去读一个本页不存在的字段。
      groundingWarning: !!(rec.grounding && rec.grounding.passed === false),
      groundingIssues: Array.isArray(rec.grounding?.issues) ? rec.grounding.issues.filter(Boolean) : [],
      locate: locateOf(r),
    })
  }
  return m
})

const riskGroups = computed(() => groupRisksByLevel(riskItems.value).map((g) => ({
  ...g,
  items: sortByConfidence(g.items).map((r) => riskViews.value.get(r.id)).filter(Boolean),
})))

/**
 * 完整报告页**实际展示顺序**的风险平铺列表（= 分组顺序 高→中→低，组内按判断把握度）。
 * 「上一项 / 下一项风险」严格按这个顺序走，只负责风险卡之间的移动，
 * 不会跳到条款完整性 / 合同原文等其它章节。
 */
const flatRisks = computed(() => riskGroups.value.flatMap((g) => g.items))
const riskIndexById = computed(() => {
  const m = new Map()
  flatRisks.value.forEach((v, i) => m.set(v.id, i))
  return m
})
function riskIndex(id) {
  const i = riskIndexById.value.get(id)
  return i === undefined ? -1 : i
}
/** 该卡是否还有 ±1 偏移的目标（用于按钮 disabled：首项禁用「上一项」、末项禁用「下一项」） */
function canGotoRisk(id, delta) {
  const i = riskIndex(id)
  if (i < 0) return false
  const j = i + delta
  return j >= 0 && j < flatRisks.value.length
}
/** 滚动到相邻风险卡（仅窗口滚动，**不改 URL** —— 避免每次点击都产生历史记录） */
function gotoRiskOffset(id, delta) {
  const j = riskIndex(id) + delta
  const target = flatRisks.value[j]
  if (!target) return
  nextTick(() => scrollToRiskCard(target.id, true))
}
/**
 * 滚动到某张风险卡顶部。
 * `block: 'start'` 让卡片顶边对齐视口顶部，配合样式里的 `scroll-margin-top: 80px`
 * 留出顶部导航（sticky nav ≈60px）的高度，保证第一眼就能看到
 * 「风险等级 + Rxx + 风险名称」这一行。
 */
function scrollToRiskCard(riskId, smooth = false) {
  const el = document.getElementById(riskAnchorId(riskId))
  if (!el) return
  try {
    el.scrollIntoView({ block: 'start', behavior: smooth ? 'smooth' : 'auto' })
  } catch {
    el.scrollIntoView(true)
  }
}

const levelDist = computed(() => {
  const rows = [
    { key: 'high', label: '高风险', value: counts.value.high, color: LEVEL_COLORS.high },
    { key: 'medium', label: '中风险', value: counts.value.medium, color: LEVEL_COLORS.medium },
    { key: 'low', label: '低风险', value: counts.value.low, color: LEVEL_COLORS.low },
  ]
  const max = Math.max(0, ...rows.map((r) => r.value))
  if (!max) return []
  return rows.map((r) => ({ ...r, pct: Math.max(2, (r.value / max) * 100) }))
})

// ── 原文定位对话框 ──
const viewer = reactive({
  visible: false,
  title: '',
  note: '',
  ranges: [],
  index: 0,
  text: '',
})
const markRef = ref(null)

const viewerView = computed(() => {
  const t = viewer.text
  const r = viewer.ranges[viewer.index]
  if (!t || !r) return { before: t, match: '', after: '' }
  return { before: t.slice(0, r[0]), match: t.slice(r[0], r[1]), after: t.slice(r[1]) }
})

function scrollToMark() {
  try {
    markRef.value?.scrollIntoView({ block: 'center' })
  } catch {
    /* 忽略：定位失败不影响阅读 */
  }
}

function openText(v) {
  if (!v?.locate?.ranges?.length) return
  viewer.text = contract.value?.parsed_text || ''
  viewer.ranges = v.locate.ranges
  viewer.index = 0
  viewer.title = v.code ? `${v.name}（${v.code}）` : v.name
  viewer.note = v.locate.key === 'anchored'
    ? `审核阶段已建立原文锚点，字符区间 ${v.locate.ranges[0][0]}–${v.locate.ranges[0][1]}`
    : `未建立原文锚点；以下位置由原文逐字检索得到，共 ${v.locate.ranges.length} 处`
  viewer.visible = true
  nextTick(scrollToMark)
}

function stepMatch(d) {
  if (viewer.ranges.length < 2) return
  viewer.index = (viewer.index + d + viewer.ranges.length) % viewer.ranges.length
  nextTick(scrollToMark)
}

/**
 * 去修改此条款：复用**现有的跨页跳转约定**
 * （sessionStorage['a24_revise_source'] = { source, risk_id }，由 contract-detail 的
 *  useContractWorkspace.applyReviseSource 消费并切到「修改合同」Tab）。
 * 不新增任何修改接口、不替用户选中会话。
 */
const REVISE_SOURCE_KEY = 'a24_revise_source'
function goRevise(v) {
  try {
    sessionStorage.setItem(REVISE_SOURCE_KEY, JSON.stringify({ source: 'risk', risk_id: v.id }))
  } catch {
    /* 隐私模式下写不进去：仍跳转到合同详情，由用户自行选择会话 */
  }
  router.push(`/contracts/${contractId.value}`)
}

// ── 打印（唯一的「导出」能力：真实可用的浏览器打印） ──
function printReport() {
  window.print()
}

// ══════════════════════════════════════════════════════════════════
// 合同原文阅读器（pdf.js）
// 保留原有双页并排；< 900px 自动切单页；工具栏简化为「上一页 / 第 X 页 / 下一页」
// ══════════════════════════════════════════════════════════════════
const leftCanvasRef = ref(null)
const rightCanvasRef = ref(null)
const currentSpread = ref(1)
const totalPages = ref(0)
const pdfReady = ref(false)
const pdfError = ref('')
const pdfLoading = ref(false)
const jumpPage = ref('')
const isNarrow = ref(false)
let pdfLoadingTask = null
let pdfDoc = null

const perPage = computed(() => (isNarrow.value ? 1 : 2))
const totalSpreads = computed(() => Math.max(1, Math.ceil(totalPages.value / perPage.value)))
const firstPage = computed(() => (currentSpread.value - 1) * perPage.value + 1)
const secondPage = computed(() => firstPage.value + 1)
const canPrev = computed(() => currentSpread.value > 1)
const canNext = computed(() => currentSpread.value < totalSpreads.value)
const pageLabel = computed(() => {
  if (!totalPages.value) return '—'
  if (perPage.value === 1) return `第 ${firstPage.value} / ${totalPages.value} 页`
  const end = Math.min(firstPage.value + 1, totalPages.value)
  return end > firstPage.value
    ? `第 ${firstPage.value} - ${end} / ${totalPages.value} 页`
    : `第 ${firstPage.value} / ${totalPages.value} 页`
})

async function loadPdf(seq) {
  pdfjsLib.GlobalWorkerOptions.workerSrc = pdfjsWorker
  const id = contractId.value
  if (!id || !contract.value) return

  pdfLoading.value = true
  pdfReady.value = false
  pdfError.value = ''

  let fileData = null
  let fileErr = null
  try {
    fileData = await getContractFile(id)
  } catch (e) {
    fileErr = e
  }
  if (seq !== undefined && seq !== requestSeq) return

  if (!fileData) {
    pdfLoading.value = false
    // 后端 /file 在「磁盘上没有原始文件」与「合同不存在/无权」时都返回 404，
    // 这里不猜测具体原因，只如实说明结果。
    pdfError.value = fileErr?.response?.status === 404
      ? '无法获取该合同的原文文件（后端没有可读取的原始文件）'
      : '合同原文加载失败'
    return
  }

  try {
    pdfLoadingTask = pdfjsLib.getDocument({ data: new Uint8Array(fileData) })
    pdfDoc = await pdfLoadingTask.promise
    if (seq !== undefined && seq !== requestSeq) {
      pdfDoc = null
      return
    }
    totalPages.value = pdfDoc.numPages
    currentSpread.value = 1
    pdfLoading.value = false
    pdfReady.value = true
    await nextTick()
    renderSpread(1)
  } catch (e) {
    pdfLoading.value = false
    // 后端在 docx→PDF 转换失败时会回退返回原始 .docx 字节流，pdf.js 必然解析失败
    pdfError.value = '原文文件无法按 PDF 渲染（后端可能回退返回了原始 Word 文件）'
    console.warn('PDF 渲染失败:', e?.message)
  }
}

async function renderPageToCanvas(pageNum, canvas) {
  if (!pdfDoc || !canvas || pageNum < 1 || pageNum > totalPages.value) return
  const page = await pdfDoc.getPage(pageNum)
  const vp = page.getViewport({ scale: 1.0 })
  const slotW = canvas.parentElement?.clientWidth || 400
  const scale = Math.max(0.2, (slotW - 16) / vp.width)
  const svp = page.getViewport({ scale })
  canvas.width = svp.width
  canvas.height = svp.height
  const ctx = canvas.getContext('2d')
  await page.render({ canvasContext: ctx, viewport: svp }).promise
}

function clearCanvas(canvas) {
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  ctx?.clearRect(0, 0, canvas.width, canvas.height)
  canvas.width = 0
  canvas.height = 0
}

async function renderSpread(spreadNum) {
  if (!pdfDoc) return
  const s = Math.min(Math.max(1, spreadNum), totalSpreads.value)
  currentSpread.value = s
  const left = (s - 1) * perPage.value + 1
  const right = left + 1
  await Promise.all([
    renderPageToCanvas(left, leftCanvasRef.value),
    perPage.value === 2 && right <= totalPages.value
      ? renderPageToCanvas(right, rightCanvasRef.value)
      : Promise.resolve(clearCanvas(rightCanvasRef.value)),
  ])
}

function prevPage() {
  if (canPrev.value) renderSpread(currentSpread.value - 1)
}
function nextPage() {
  if (canNext.value) renderSpread(currentSpread.value + 1)
}
function jumpTo() {
  const n = parseInt(jumpPage.value, 10)
  if (!Number.isFinite(n) || n < 1 || n > totalPages.value) {
    ElMessage.warning(`请输入 1-${totalPages.value} 之间的页码`)
    return
  }
  renderSpread(Math.ceil(n / perPage.value))
  jumpPage.value = ''
}

// 宽窄切换：保持「切换前正在看的首页」不变
const pendingAnchor = ref(null)
function onResize() {
  const next = window.innerWidth < 900
  if (next === isNarrow.value) return
  pendingAnchor.value = firstPage.value
  isNarrow.value = next
}
watch(isNarrow, async () => {
  if (!pdfDoc) return
  const anchor = pendingAnchor.value || 1
  pendingAnchor.value = null
  await nextTick()
  renderSpread(Math.ceil(anchor / perPage.value))
})

// ── 路由 hash 变化：页面已挂载时再点「详情 →」（同页不同风险）也要重新定位 ──
// 只滚动，不改 URL、不新增历史记录，因此前进/后退不会产生导航回环。
watch(() => route.hash, () => nextTick(scrollToHash))

// ── 路由参数变化：彻底重置 ──
watch(contractId, async (newId, oldId) => {
  if (!newId || newId === oldId) return
  const seq = ++requestSeq
  pdfLoadingTask?.destroy()
  pdfLoadingTask = null
  pdfDoc = null
  loading.value = true
  error.value = ''
  report.value = null
  riskItems.value = []
  contract.value = null
  hasCurrentResult.value = true
  pdfReady.value = false
  pdfError.value = ''
  pdfLoading.value = false
  totalPages.value = 0
  currentSpread.value = 1
  jumpPage.value = ''
  viewer.visible = false
  await fetchAll(seq)
  if (seq !== requestSeq) return
  loadPdf(seq)
})

onMounted(async () => {
  isNarrow.value = window.innerWidth < 900
  window.addEventListener('resize', onResize)
  const seq = ++requestSeq
  await fetchAll(seq)
  if (seq !== requestSeq) return
  loadPdf(seq)
  // 从「审核报告」Tab 的「详情 →」跳进来时定位到对应风险卡片
  await nextTick()
  scrollToHash()
})

onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  requestSeq++
  pdfLoadingTask?.destroy()
})

/**
 * 支持 `/audit/report/:id#risk-<风险记录id>` 深链（风险锚点由本页每条风险真实渲染）。
 *
 * 另加一条**章节锚点兜底**：本页没有条款卡片级 anchor，而「审核报告 Tab」的条款完整性问题
 * Tag 需要落到「条款完整性检查」章节（`<section id="clauses">`），故当 hash 不是风险锚点时，
 * 按 DOM id 直接定位。风险锚点分支逻辑与行为完全不变。
 */
function scrollToHash() {
  const riskId = riskIdFromHash(route.hash)
  if (riskId) {
    // block:'start' + .rp-risk 的 scroll-margin-top:80px → 卡片顶部（等级/编号/名称）落在视口可读区
    scrollToRiskCard(riskId, false)
    return
  }
  const el = document.getElementById(String(route.hash || '').replace(/^#/, ''))
  if (!el) return
  try {
    el.scrollIntoView({ block: 'start', behavior: 'auto' })
  } catch {
    el.scrollIntoView(true)
  }
}

/**
 * 「← 返回审核报告」：回到合同详情的**审核报告** Tab。
 * 用 `?tab=audit`（由 ContractDetail 读 query 落位），因此返回后不需要用户再手动切 Tab。
 */
function backToAuditTab() {
  router.push(contractDetailPath(contractId.value, 'audit'))
}
</script>

<style scoped>
/* ══════════════════════════════════════════════════════════════════
   视觉系统：主色 #1935C3；高/中/低 #DC2626 / #D97706 / #2563EB；
   成功 #16A34A；页面底 #F7F8FA；卡片 #FFF；引用块 #F9FAFB；边框 #E5E7EB；
   主文字 #111827；正文 #374151；辅助 #6B7280；弱文字 #9CA3AF。
   8px 圆角、1px 边框、无 hover 阴影、无渐变、无 emoji。
   ══════════════════════════════════════════════════════════════════ */
.rp-page {
  padding: 16px 24px 48px;
  max-width: 1120px;
  margin: 0 auto;
  background: #F7F8FA;
}

/* ── 工具条 ── */
.rp-toolbar {
  display: flex; align-items: center; justify-content: space-between;
  gap: 16px; flex-wrap: wrap; margin-bottom: 14px;
}
.rp-toolbar-l { display: flex; align-items: center; gap: 12px; }
.rp-crumb { font-size: 12.5px; color: #9CA3AF; }
.rp-crumb b { color: #1935C3; font-weight: 500; }
.rp-toolbar-r { display: flex; align-items: center; gap: 8px; }

.rp-state { padding: 40px 0; }

/* ── 报告纸张 ── */
.rp-doc {
  background: #fff;
  border: 1px solid #E5E7EB;
  border-radius: 8px;
  padding: 32px 40px 40px;
}
.rp-alert { margin: 0 0 20px; }

/* ── B1 报告头部 ── */
.rp-head { padding-bottom: 22px; border-bottom: 2px solid #1935C3; margin-bottom: 26px; }
.rp-title { margin: 0; font-size: 28px; font-weight: 700; color: #111827; letter-spacing: 0.01em; }
.rp-head-sub { margin-top: 8px; font-size: 15px; color: #374151; }
.rp-head-meta {
  margin-top: 10px; display: flex; flex-wrap: wrap; gap: 20px;
  font-size: 12.5px; color: #6B7280;
}

.rp-grid {
  margin-top: 20px;
  display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px;
}
.rp-cell { background: #F9FAFB; border: 1px solid #E5E7EB; border-radius: 8px; padding: 10px 12px; }
.rp-cell-k { font-size: 12px; color: #6B7280; }
.rp-cell-v { font-size: 14px; font-weight: 600; color: #111827; margin-top: 4px; word-break: break-word; }
.rp-cell-v-sm { font-weight: 400; color: #374151; line-height: 1.7; }
.rp-cell-sub { font-size: 12px; color: #9CA3AF; margin-top: 3px; word-break: break-word; }

.rp-grid-wide {
  margin-top: 12px; background: #F9FAFB; border: 1px solid #E5E7EB;
  border-radius: 8px; padding: 10px 12px;
}

/* ── 章节 ── */
.rp-sec { margin-bottom: 30px; }
/* 章节锚点被跳转时（#clauses），留出与风险卡一致的顶部导航高度 */
#clauses { scroll-margin-top: 80px; }
.rp-h2 {
  display: flex; align-items: baseline; gap: 10px;
  margin: 0 0 14px; padding-bottom: 8px;
  font-size: 16px; font-weight: 700; color: #111827;
  border-bottom: 1px solid #E5E7EB;
}
.rp-h2-no {
  font-family: ui-monospace, Consolas, monospace;
  font-size: 13px; font-weight: 700; color: #1935C3;
  background: #EEF1FC; border-radius: 4px; padding: 1px 7px;
}
.rp-h3 {
  display: flex; align-items: center; gap: 8px;
  margin: 18px 0 10px; font-size: 14px; font-weight: 600; color: #374151;
}
.rp-h3-plain { margin-top: 22px; }
.rp-h3-n { font-size: 12px; font-weight: 400; color: #9CA3AF; }
.rp-dot { width: 8px; height: 8px; border-radius: 50%; }

.rp-p { margin: 0 0 10px; font-size: 14px; line-height: 1.85; color: #374151; }
.rp-p-lg { font-size: 14.5px; }
.rp-p b { font-size: 16px; font-weight: 700; color: #111827; padding: 0 1px; }
.rp-note { color: #6B7280; }
.rp-disclaimer {
  margin: 14px 0 0; padding-top: 10px; border-top: 1px dashed #E5E7EB;
  font-size: 12px; color: #9CA3AF; line-height: 1.8;
}
.rp-none { font-size: 13px; color: #9CA3AF; }
.rp-none-sm { font-size: 12.5px; }

/* ── 02 风险概览 ── */
.rp-overview { display: flex; align-items: center; gap: 36px; flex-wrap: wrap; }
.rp-ring { flex: 0 0 auto; }
.rp-ring-n { font-size: 26px; font-weight: 700; color: #111827; line-height: 1.15; }
.rp-ring-l { font-size: 12px; color: #6B7280; }
.rp-dist { flex: 1 1 320px; min-width: 260px; display: flex; flex-direction: column; gap: 12px; }
.rp-dist-row { display: flex; align-items: center; gap: 12px; }
.rp-dist-l { flex: 0 0 56px; font-size: 13px; color: #374151; }
.rp-dist-n { flex: 0 0 34px; font-size: 15px; font-weight: 700; color: #111827; text-align: right; }
.rp-dist-bar { flex: 1 1 auto; height: 10px; background: #F3F4F6; border-radius: 5px; overflow: hidden; }
.rp-dist-bar i { display: block; height: 100%; border-radius: 5px; }

/* ── 03 风险明细 ── */
.rp-rgroup { margin-bottom: 20px; }
.rp-risk {
  border: 1px solid #E5E7EB; border-radius: 8px; padding: 16px 18px;
  background: #fff; margin-bottom: 12px;
  /* 深链 / 上一项-下一项定位时，卡片顶边再留出 80px（顶部 sticky 导航约 60px），
     保证第一眼就看到「风险等级 + Rxx + 风险名称」这一行，而不是被导航遮住 */
  scroll-margin-top: 80px;
}
.rp-risk:target { border-color: #1935C3; box-shadow: 0 0 0 3px rgba(25, 53, 195, 0.10); }
.rp-risk-nav {
  display: flex; align-items: center; justify-content: space-between;
  gap: 10px; margin-top: 10px; padding-top: 8px; border-top: 1px dashed #E5E7EB;
}
.rp-risk-nav-pos { font-size: 12px; color: #9CA3AF; }

/* 悬浮返回（右下角常驻，醒目主按钮） */
.rp-back {
  position: fixed; right: 24px; bottom: 24px; z-index: 1000;
}
.rp-back-btn {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 12px 22px; font-size: 14px; line-height: 1; font-family: inherit;
  color: #fff; cursor: pointer;
  /* 与「查看完整审核报告」同一个主色 token（theme.css 的 --el-color-primary = #1935C3） */
  background: var(--el-color-primary, #1935C3);
  border: none; border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, .15);
  transition: background-color .15s ease, box-shadow .15s ease;
}
/* hover 时主色稍微加深（theme.css 的 --el-color-primary-dark-2 = #142A9C） */
.rp-back-btn:hover,
.rp-back-btn:active {
  background: var(--el-color-primary-dark-2, #142A9C);
  box-shadow: 0 6px 20px rgba(0, 0, 0, .2);
}
.rp-back-btn:focus-visible { outline: 2px solid #fff; outline-offset: 2px; }
.rp-back-arrow { font-size: 15px; }
@media (max-width: 900px) {
  .rp-back { right: 14px; bottom: 14px; }
}
.rp-risk-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; }
.rp-badge {
  display: inline-block; flex: 0 0 auto;
  font-size: 12px; font-weight: 600; color: #fff;
  border-radius: 4px; padding: 2px 8px; line-height: 1.6;
}
.rp-risk-name { font-size: 16px; font-weight: 700; color: #111827; }
.rp-risk-code {
  font-family: ui-monospace, Consolas, monospace;
  font-size: 12px; color: #9CA3AF;
}

.rp-field { margin-bottom: 12px; }
.rp-field:last-child { margin-bottom: 0; }
.rp-field-k { font-size: 12px; font-weight: 600; color: #6B7280; margin-bottom: 5px; }
.rp-field-v { font-size: 14px; line-height: 1.85; color: #374151; word-break: break-word; white-space: pre-wrap; }
.rp-field-note { margin-top: 5px; font-size: 12px; color: #9CA3AF; }
.rp-quote {
  margin: 0; padding: 10px 14px;
  background: #F9FAFB; border: 1px solid #E5E7EB; border-left: 3px solid #D1D5DB;
  border-radius: 4px;
  font-size: 14px; line-height: 1.85; color: #374151;
  white-space: pre-wrap; word-break: break-word;
}
.rp-sub-field { display: flex; gap: 8px; margin-top: 6px; font-size: 13.5px; }
.rp-sub-k { flex: 0 0 auto; font-size: 12px; color: #9CA3AF; padding-top: 2px; }
.rp-warn {
  margin-bottom: 12px; padding: 8px 12px;
  background: #FFFBEB; border: 1px solid #FDE68A; border-radius: 4px;
  font-size: 12.5px; color: #92400E;
}

.rp-risk-foot {
  display: flex; align-items: center; justify-content: space-between;
  gap: 14px; flex-wrap: wrap;
  margin-top: 14px; padding-top: 12px; border-top: 1px dashed #E5E7EB;
}
.rp-risk-meta { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; font-size: 12px; color: #6B7280; }
.rp-locate.is-anchored { color: #16A34A; }
.rp-locate.is-searchable { color: #1935C3; }
.rp-locate.is-none, .rp-locate.is-unavailable { color: #9CA3AF; }
.rp-risk-actions { display: flex; gap: 8px; flex-wrap: wrap; }

/* ── 04 条款完整性 ── */
.rp-metrics { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 14px; }
.rp-metric {
  flex: 1 1 96px; min-width: 88px;
  background: #F9FAFB; border: 1px solid #E5E7EB; border-radius: 8px; padding: 10px 12px;
}
.rp-metric-n { font-size: 22px; font-weight: 700; color: #111827; line-height: 1.15; }
.rp-metric-l { font-size: 12px; color: #6B7280; margin-top: 4px; }

.rp-stack { display: flex; height: 10px; border-radius: 5px; overflow: hidden; background: #F3F4F6; }
.rp-stack-seg { display: block; height: 100%; }
.rp-stack-legend { display: flex; flex-wrap: wrap; gap: 16px; margin-top: 8px; font-size: 12px; color: #6B7280; }
.rp-stack-legend-i { display: inline-flex; align-items: center; gap: 6px; }
.rp-stack-legend-i i { width: 8px; height: 8px; border-radius: 2px; }
.rp-stack-legend-i b { color: #111827; font-weight: 600; }

.rp-critical {
  margin-top: 16px; padding: 12px 14px;
  background: #FEF2F2; border: 1px solid #FECACA; border-radius: 8px;
}
.rp-critical-k { font-size: 12.5px; font-weight: 600; color: #B91C1C; margin-bottom: 8px; }
.rp-critical-tags { display: flex; flex-wrap: wrap; gap: 6px; }

.rp-clause { border: 1px solid #E5E7EB; border-radius: 8px; margin-bottom: 10px; }
.rp-clause-head {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding: 10px 14px; background: #F9FAFB;
  border-bottom: 1px solid #E5E7EB; border-radius: 8px 8px 0 0;
}
.rp-clause-title { font-size: 14px; font-weight: 600; color: #111827; }
.rp-clause-pri {
  font-size: 11.5px; color: #6B7280;
  border: 1px solid #E5E7EB; background: #fff; border-radius: 3px; padding: 1px 6px;
}
.rp-clause-sim { margin-left: auto; font-size: 12px; color: #9CA3AF; }
.rp-clause-body { padding: 12px 14px; }

/* ── 05 关联风险 ── */
.rp-cross { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.rp-cross-i {
  display: flex; align-items: flex-start; gap: 12px;
  background: #F9FAFB; border: 1px solid #E5E7EB; border-radius: 8px; padding: 12px 14px;
}
.rp-cross-body { min-width: 0; }
.rp-cross-ref { margin-top: 4px; font-size: 12px; color: #6B7280; }

/* ── 06 合同原文 ── */
.rp-pdf-state { padding: 40px 20px; text-align: center; color: #6B7280; }
.rp-pdf-state p { margin: 10px 0 0; font-size: 13.5px; }
.rp-pdf-state .is-loading { animation: rp-rotate 1.6s linear infinite; }
.rp-pdf-hint { font-size: 12px !important; color: #9CA3AF; }
@keyframes rp-rotate { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

.rp-pdf-toolbar {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; flex-wrap: wrap; margin-bottom: 12px;
}
.rp-pdf-nav { display: flex; align-items: center; gap: 10px; }
.rp-pdf-label { font-size: 13.5px; color: #374151; min-width: 120px; text-align: center; }
.rp-pdf-jump { display: flex; align-items: center; gap: 6px; font-size: 12.5px; color: #6B7280; }
.rp-pdf-jump-input { width: 62px; }

.rp-pdf-viewport {
  display: flex; gap: 12px; justify-content: center;
  background: #F9FAFB; border: 1px solid #E5E7EB; border-radius: 8px; padding: 14px 10px;
}
.rp-pdf-slot { flex: 1; min-width: 0; text-align: center; }
.rp-pdf-canvas { border: 1px solid #E5E7EB; max-width: 100%; background: #fff; }

/* ── 原文定位对话框 ── */
.rp-viewer-bar {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; flex-wrap: wrap; margin-bottom: 10px;
}
.rp-viewer-note { font-size: 12.5px; color: #6B7280; }
.rp-viewer-step { display: flex; align-items: center; gap: 6px; font-size: 12.5px; color: #374151; }
.rp-viewer-body {
  max-height: 56vh; overflow: auto;
  background: #F9FAFB; border: 1px solid #E5E7EB; border-radius: 8px;
}
.rp-viewer-text {
  margin: 0; padding: 14px 16px;
  font-family: ui-monospace, Consolas, "Microsoft YaHei", monospace;
  font-size: 13px; line-height: 1.9; color: #374151;
  white-space: pre-wrap; word-break: break-word;
}
.rp-viewer-mark { background: #FEF08A; color: #111827; padding: 1px 0; }

/* ── 打印：隐藏交互外壳，报告主体自然展开 ── */
@media print {
  .rp-toolbar { display: none !important; }
  .rp-page { padding: 0 !important; max-width: none !important; background: #fff !important; }
  .rp-doc {
    border: none !important; border-radius: 0 !important;
    padding: 0 !important; box-shadow: none !important;
  }
  .rp-pdf-toolbar { display: none !important; }
  .rp-risk-actions, .rp-viewer-step { display: none !important; }
  /* 导航类元素不打印：悬浮返回、风险卡间跳转 */
  .rp-back, .rp-risk-nav { display: none !important; }
  .rp-viewer-body { max-height: none !important; overflow: visible !important; }
  .rp-risk, .rp-clause, .rp-critical { break-inside: avoid; page-break-inside: avoid; }
  .rp-h2, .rp-h3 { break-after: avoid; page-break-after: avoid; }
  .rp-sec { margin-bottom: 18px; }
  /* 打印时保留等级色块 / 分布条 / 标签底色 */
  .rp-doc { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
}

@media (max-width: 900px) {
  .rp-page { padding: 12px 14px 40px; }
  .rp-doc { padding: 20px 16px 28px; }
  .rp-title { font-size: 22px; }
  .rp-overview { gap: 20px; }
  .rp-clause-sim { margin-left: 0; }
}
</style>

<!-- 打印时隐藏全局外壳（顶部导航 / 返回栏 / 装饰背景） -->
<style>
@media print {
  .app-nav,
  .back-bar,
  .app-bg { display: none !important; }
  body { background: #fff !important; }
}
</style>
