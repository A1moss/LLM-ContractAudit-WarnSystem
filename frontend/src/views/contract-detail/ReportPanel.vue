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
           B 双列：合同结构化画像 ｜ 风险规则扫描
           ≥1200px 双列；<1200px 单列堆叠（见文件底部媒体查询）
           ══════════════════════════════════════════════════════════════ -->
      <div class="rpt-cols">
        <!-- ── B1 合同结构化画像（字段全部来自合同解析结果，缺失即整项不渲染） ── -->
        <section class="rpt-card rpt-card-profile">
          <div class="rpt-card-head">
            <h4>合同结构化画像</h4>
            <span class="rpt-card-note">来自合同解析结果</span>
          </div>

          <div v-if="profileCells.length" class="rpt-kv">
            <div v-for="c in profileCells" :key="c.key" class="rpt-kv-i">
              <div class="rpt-kv-k">{{ c.label }}</div>
              <div class="rpt-kv-v" :title="c.value">{{ c.value }}</div>
              <div v-if="c.sub" class="rpt-kv-s" :title="c.sub">{{ c.sub }}</div>
            </div>
          </div>
          <div v-else class="rpt-none">本次解析未提取到结构化要素。</div>

          <!-- 风险评分：数字 + 轻量位置条（位置 = 风险评分 / 100，颜色沿用既有 scoreColor 语义） -->
          <div v-if="riskScore !== null" class="rpt-score">
            <div class="rpt-score-row">
              <span class="rpt-score-k">风险评分</span>
              <span class="rpt-score-v" :style="{ color: scoreTone }">{{ riskScore }}</span>
            </div>
            <div class="rpt-bar" role="img" :aria-label="`风险评分 ${riskScore}，满分 100`">
              <span class="rpt-bar-fill" :style="{ width: scorePct + '%', background: scoreTone }" />
              <span class="rpt-bar-dot" :style="{ left: scorePct + '%', borderColor: scoreTone }" />
            </div>
            <div class="rpt-bar-cap">满分 100 · 分数越高风险越高</div>
          </div>
        </section>

        <!-- ── B2 风险规则扫描矩阵（R01–R13 全量规则；命中态来自当前审核结果）
             语义 =「风险扫描结果 + 命中风险导航」：命中格可点击进入既有风险详情，
             未命中格只作为扫描覆盖面的证明，纯展示、不可点。 ── -->
        <section class="rpt-card rpt-card-scan">
          <div class="rpt-card-head">
            <h4>风险规则扫描</h4>
          </div>

          <div
            class="rpt-mx" role="group"
            :aria-label="`风险规则扫描结果：共扫描 ${riskMatrix.length} 类风险规则，命中 ${hitCount} 类；命中的规则可点击查看风险详情`"
          >
            <el-tooltip
              v-for="m in riskMatrix" :key="m.code"
              placement="top" :show-after="100" effect="dark"
            >
              <template #content>
                <!-- 说明：tooltip 内容由 Element Plus teleport 到 body，scoped CSS 到不了，故用内联样式 -->
                <div style="max-width: 280px; line-height: 1.65;">
                  <div style="font-weight: 600;">{{ m.code }} · {{ m.name }}</div>
                  <template v-if="m.hit">
                    <div style="opacity: 0.85;">{{ m.levelLabel }}</div>
                    <div v-if="m.count > 1" style="opacity: 0.85;">本次命中 {{ m.count }} 条记录</div>
                    <div style="opacity: 0.7;">点击查看风险详情</div>
                  </template>
                  <div v-else style="opacity: 0.85;">本次审核已扫描该规则，未发现风险。</div>
                </div>
              </template>

              <!-- ① 命中格：唯一可点击的格子。复用既有风险详情深链（openRisk → /audit/report/:id#risk-<id>），
                   不新建第二套风险详情系统；同一规则命中多条时，取判断把握度最高的一条（与「重点风险」同序）。
                   格子文案 = 两行结构：第一行 R 编号（等宽体，次级识别信息但保持清晰）、
                   第二行 中文风险名称（逐字取 RISK_NAMES，不缩写、不截断），两行居中。 -->
              <button
                v-if="m.hit" type="button" class="rpt-mx-c is-hit"
                :style="{ background: m.color }"
                :aria-label="`${m.code} · ${m.name}，${m.levelLabel}，点击查看风险详情`"
                @click="openRisk(m.record)"
              >
                <span class="rpt-mx-code">{{ m.code }}</span>
                <span class="rpt-mx-name">{{ m.name }}</span>
              </button>

              <!-- ② 未命中格：浅灰底，文字结构与命中格**完全一致**（R 编号 / 中文风险名称 两行居中）。
                   依旧无 @click、无 role=button、无 tabindex、无任何 hover 效果、不跳转、不弹窗、
                   不改 URL、不伪造 AuditRecord（只保留 tooltip 信息提示）。 -->
              <div v-else class="rpt-mx-c">
                <span class="rpt-mx-code">{{ m.code }}</span>
                <span class="rpt-mx-name">{{ m.name }}</span>
              </div>
            </el-tooltip>
          </div>

          <div class="rpt-mx-cap">
            共扫描 <b>{{ riskMatrix.length }}</b> 类风险规则 · 命中 <b>{{ hitCount }}</b> 类
            <span class="rpt-mx-hint">彩色＝发现问题，可点击查看详情；灰底＝已扫描、未命中</span>
          </div>

          <!-- 风险等级分布（保留原有横向分布条，职责是「严重程度」，与矩阵的「类别」互补） -->
          <div class="rpt-dist">
            <div class="rpt-dist-k">风险等级分布</div>
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
            <div v-else class="rpt-none">本次审核未检出风险项。</div>
          </div>
        </section>
      </div>

      <!-- ══════════════════════════════════════════════════════════════
           C 条款完整性（最终版）—— 无可用的逐条比对数据时整块隐藏
           阅读链：30 项标准条款 → 覆盖率 → 20/4/6 分布 → 问题 N 项 →
                  具体问题条款（缺失 / 部分偏离 Tag，可点击）→ 已覆盖（默认收起）
           · 数字与条款名逐字来自当前比对结果，**不硬编码任何条款名或数字**；
           · 「关键缺失」判定完全来自后端 missing_critical，不新增关键条款规则；
           · Tag 一律降级定位到完整报告「条款完整性检查」章节（#clauses）：
             完整报告目前没有条款卡片级 anchor，因此不做下标猜测、不新建第二套定位机制；
           · 页面上不出现任何后端字段名，也不展示 similarity / deviation / related_law /
             matched_text / confidence / 风险等级（缺失 ≠ R01–R13 风险命中）。
           ══════════════════════════════════════════════════════════════ -->
      <section v-if="comparison" class="rpt-card">
        <div class="rpt-card-head">
          <h4>条款完整性</h4>
          <span class="rpt-card-note">按标准条款清单逐项比对</span>
          <button type="button" class="rpt-cmp-open" @click="openFullReport">完整报告 ↗</button>
        </div>

        <div class="rpt-cmp-top">
          <span class="rpt-cmp-total">共 <b>{{ cmpSummary?.total ?? 0 }}</b> 项标准条款</span>
          <span class="rpt-cmp-rate">
            <span class="rpt-cmp-rate-l">覆盖率</span>
            <b>{{ coveragePct !== null ? coveragePct + '%' : '—' }}</b>
          </span>
        </div>

        <div v-if="cmpSegments.length" class="rpt-stack rpt-stack-lg" role="img" aria-label="条款覆盖情况分布">
          <span
            v-for="s in cmpSegments" :key="s.key"
            class="rpt-stack-seg" :style="{ width: s.pct + '%', background: s.color }"
          />
        </div>

        <div class="rpt-cmp-facts">
          <span class="rpt-cmp-fact">{{ CMP_STATUS_LABELS.covered }} <b :style="{ color: CMP_COLORS.covered }">{{ cmpSummary?.covered ?? 0 }}</b></span>
          <span class="rpt-cmp-fact">{{ CMP_STATUS_LABELS.partial }} <b :style="{ color: CMP_COLORS.partial }">{{ cmpSummary?.partial ?? 0 }}</b></span>
          <span class="rpt-cmp-fact">{{ CMP_STATUS_LABELS.missing }} <b :style="{ color: CMP_COLORS.missing }">{{ cmpSummary?.missing ?? 0 }}</b></span>
        </div>

        <!-- 存在问题 N 项 · 其中 M 项为关键条款（14px 半粗，不与覆盖率抢主视觉） -->
        <div v-if="problemCount" class="rpt-prob">
          <i class="rpt-prob-dot" />存在问题 <b>{{ problemCount }}</b> 项<template v-if="criticalCount"> · 其中 <b>{{ criticalCount }}</b> 项为关键条款</template>
        </div>

        <!-- 缺失（关键缺失 = 后端 missing_critical，红；普通缺失，灰）—— 全部可点击 -->
        <div v-if="missingItems.length" class="rpt-ctag-group">
          <div class="rpt-ctag-k">{{ CMP_STATUS_LABELS.missing }}（{{ missingItems.length }} 项）</div>
          <div class="rpt-ctags">
            <button
              v-for="(c, i) in missingItems" :key="'m-' + i" type="button"
              class="rpt-ctag" :class="c.critical ? 'is-critical' : 'is-plain'"
              :title="clauseTip(c)" @click="openClause()"
            >
              <i class="rpt-ctag-dot" /><span class="rpt-ctag-t">{{ c.title || '未命名条款' }}</span><span class="rpt-ctag-arrow">›</span>
            </button>
          </div>
        </div>

        <!-- 部分偏离（橙）—— 全部可点击 -->
        <div v-if="partialItems.length" class="rpt-ctag-group">
          <div class="rpt-ctag-k">{{ CMP_STATUS_LABELS.partial }}（{{ partialItems.length }} 项）</div>
          <div class="rpt-ctags">
            <button
              v-for="(c, i) in partialItems" :key="'p-' + i" type="button"
              class="rpt-ctag is-partial"
              :title="clauseTip(c)" @click="openClause()"
            >
              <i class="rpt-ctag-dot" /><span class="rpt-ctag-t">{{ c.title || '未命名条款' }}</span><span class="rpt-ctag-arrow">›</span>
            </button>
          </div>
        </div>

        <!-- 已覆盖：默认收起；展开为 3 列自适应 grid，只显示真实条款名 -->
        <div v-if="coveredItems.length" class="rpt-cov">
          <button
            type="button" class="rpt-cov-head"
            :aria-expanded="coveredOpen ? 'true' : 'false'" aria-controls="rpt-cov-list"
            @click="coveredOpen = !coveredOpen"
          >
            <span class="rpt-cov-t">{{ CMP_STATUS_LABELS.covered }} {{ coveredItems.length }} 项标准条款</span>
            <span class="rpt-cov-act">{{ coveredOpen ? '收起 ▴' : '展开 ▾' }}</span>
          </button>
          <div v-show="coveredOpen" id="rpt-cov-list" class="rpt-cov-list">
            <span v-for="(c, i) in coveredItems" :key="'c-' + i" class="rpt-cov-i">
              <i class="rpt-cov-tick">✓</i><span class="rpt-cov-n">{{ c.title || '未命名条款' }}</span>
            </span>
          </div>
        </div>
      </section>

      <!-- ══════════════════════════════════════════════════════════════
           D 关联风险 —— 仅在后端真的返回了跨条款风险时渲染
           为空时**整个区块不产出**（不显示「暂无关联风险」制造空白模块）
           ══════════════════════════════════════════════════════════════ -->
      <section v-if="crossRisks.length" class="rpt-card">
        <div class="rpt-card-head">
          <h4>关联风险</h4>
          <span class="rpt-card-note">后端跨条款图分析结果</span>
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
           E 重点风险 —— 按 高→中→低 分组，组内按判断把握度排序
           每条给「等级 + 业务名称 + R编号 + reason 摘要 + 判断把握度 + 详情入口」，
           完整内容在「完整审核报告」；本 Tab 不展开全部风险内容。
           风险名取 business name（如「保密期间不合理」），R 编号弱化。
           判断把握度只做中性灰圆点 + 百分比，**不复用风险等级色**（与严重程度无关）。
           ══════════════════════════════════════════════════════════════ -->
      <section class="rpt-card">
        <div class="rpt-card-head">
          <h4>重点风险</h4>
          <span class="rpt-card-note">按风险等级分组，组内按判断把握度排序</span>
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
                <el-tag
                  class="rpt-risk-lv" size="small" effect="plain"
                  :type="RISK_LEVEL_TAGS[r.risk_level] || 'info'"
                >{{ r.level || riskLevelLabel(r.risk_level) }}</el-tag>
                <span class="rpt-risk-name">{{ riskName(r.risk_type) }}</span>
                <span class="rpt-risk-code">{{ r.risk_type }}</span>
              </div>
              <p class="rpt-risk-reason">{{ r.reason || '后端未给出判定理由' }}</p>
              <div class="rpt-risk-meta">
                <el-tooltip
                  v-if="confidencePct(r) !== null"
                  placement="top" :show-after="120" effect="dark"
                >
                  <template #content>
                    <div style="max-width: 300px; line-height: 1.65;">
                      <div style="font-weight: 600;">AI 判断把握度 {{ confidencePct(r) }}%</div>
                      <div style="opacity: 0.85;">表示模型对该风险识别的确定程度，与风险严重程度无关</div>
                    </div>
                  </template>
                  <span class="rpt-conf"><i class="rpt-conf-dot" />{{ confidencePct(r) }}%</span>
                </el-tooltip>
                <span v-if="r.detection_method" class="rpt-detect">{{ detectionLabel(r.detection_method) }}</span>
              </div>
              <button type="button" class="rpt-risk-more" @click="openRisk(r)">详情 →</button>
            </li>
          </ul>
        </div>
      </section>

      <!-- ══════════════════════════════════════════════════════════════
           F 操作区 —— 只保留真实存在的入口
           「导出 PDF 报告」属于完整审核报告页（AuditReportDetail），本 Tab 不重复放置导出按钮；
           本 Tab 只提供「查看完整审核报告 / 去修改合同」两个真实入口。
           ══════════════════════════════════════════════════════════════ -->
      <section class="rpt-actions">
        <div class="rpt-actions-l">
          <el-button type="primary" @click="openFullReport">查看完整审核报告</el-button>
          <el-button @click="goRevise">去修改合同</el-button>
        </div>
        <div class="rpt-actions-r">
          <span v-if="auditTime">审核时间 {{ auditTime }}</span>
          <span v-if="modeLabel">审核方式 {{ modeLabel }}</span>
          <span v-if="statusLabel">合同状态 {{ statusLabel }}</span>
        </div>
      </section>

      <p class="rpt-foot">
        本区数字为<b>审核时</b>的快照：风险评分与高/中/低计数不随之后的条款比对刷新而变化；
        条款覆盖率取自最近一次比对结果。「风险规则扫描」的命中状态与「重点风险」均取<b>本次审核</b>结果。
        完整分析见「查看完整审核报告」。
      </p>
    </template>
  </div>
</template>

<script setup>
/**
 * 审核报告 Tab —— **审核结果驾驶舱（信息密度版）**
 *
 * 定位（与「完整审核报告」页严格区分）：
 *   本组件 = 结论 + 合同结构化画像 + R01–R13 规则扫描 + 条款完整性 + 重点风险摘要 + 操作入口；
 *   不做正式报告排版，不展开每条风险的完整证据与建议。
 *   完整报告 = frontend/src/views/AuditReportDetail.vue（合同信息 / 结论 / 风险完整分析 /
 *             条款逐项比对 / 关联风险 / 合同原文 / 打印）。
 *
 * 数据来源（全部是 workspace 已加载的现有数据，**不新增任何 API、不改后端**）：
 *   - props.ws.report          ← GET /contracts/{id}/audit-report（切到本 Tab 时按需加载一次）
 *   - props.ws.riskItems       ← GET /contracts/{id}/audit-result（**只含当前审核批次**）
 *   - props.ws.clauseComparison← GET /contracts/{id}/clause-comparison
 *   - props.ws.contract        ← GET /contracts/{id}（合同类型 / extracted_elements / audit_mode）
 *
 * 数据真实性约束（本轮新增模块一律遵守）：
 *   - 规则扫描矩阵只认当前审核结果里的 risk_type：命中即按 risk_level 上色，
 *     未命中即中性灰；**不查历史批次、不跨批次去重、不从页面文本推测**；
 *   - 矩阵恒为 R01–R13（来源：constants/riskTypes.js 的 RISK_NAMES，**不新开一份风险清单**）；
 *   - 风险总数 = 后端 high+mid+low 三个计数相加（后端没有 total 字段，**不伪造**）；
 *   - 条款比对兼容后端三种历史形态（dict / list / null），非 dict 一律视为「无逐条比对」；
 *   - 合同画像字段缺一个就不渲染，**不填「暂无」占位**；
 *   - 页面上不出现任何后端字段名，不展示后端没有的能力（导出/模板/审批）。
 *
 * 交互约束（本轮最终定义：矩阵 =「扫描结果 + 命中风险导航」，不是 13 个规则菜单）：
 *   - **只有命中格可点击**：命中格有真实 AuditRecord、用风险等级色、显示业务名称 + R 编号、
 *     `cursor: pointer`、hover 只有「边框轻微加深 + 背景轻微变化」（无 transition / 无放大位移 / 无阴影）；
 *   - 命中格的点击走**既有**风险详情深链 `openRisk()` → `/audit/report/:id#risk-<id>`，
 *     与「重点风险」的「详情 →」完全同一条链路，**不新建第二套风险详情系统**；
 *   - **未命中格完全不可点**：无 `@click`、无 `role=button`、无 `tabindex`、无 hover 效果、
 *     不跳转、不弹窗、不改 URL、不伪造 AuditRecord；只保留 tooltip 信息提示（已扫描、未发现风险）。
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  RISK_NAMES, RISK_LEVEL_TAGS, CMP_STATUS_LABELS,
  riskName, riskLevelLabel, detectionLabel,
} from '../../constants/riskTypes.js'
import {
  CMP_COLORS, REPORT_LEVELS,
  groupRisksByLevel, sortByConfidence, stackSegments, auditModeLabel, contractStatusLabel,
  scoreColor, amountParts, performancePeriodText, priorityLabel,
} from '../../constants/reportTokens.js'
import { typeLabel } from '../../constants/contractTypes.js'
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

/** 评分色与位置：颜色沿用既有 scoreColor 三档语义，位置 = 评分 / 100（钳到 0–100） */
const scoreTone = computed(() => scoreColor(riskScore.value))
const scorePct = computed(() => {
  const v = Number(riskScore.value)
  if (!Number.isFinite(v)) return 0
  return Math.min(100, Math.max(0, v))
})

/** 只列出非零档位，如「高 2 / 中 2」（避免出现「低 0」这类噪声） */
const levelParts = computed(() => {
  const c = counts.value
  const out = []
  if (c.high) out.push(`高 ${c.high}`)
  if (c.medium) out.push(`中 ${c.medium}`)
  if (c.low) out.push(`低 ${c.low}`)
  return out
})

// ── B1 合同结构化画像（只取后端真实存在的字段，逐项与完整报告页同源同口径） ──

const elements = computed(() => {
  const e = props.ws.contract?.extracted_elements
  return e && typeof e === 'object' && !Array.isArray(e) ? e : null
})

const typeText = computed(() => {
  const c = props.ws.contract
  if (!c) return ''
  return c.type_label || typeLabel(c.contract_type)
})

const profileCells = computed(() => {
  const out = []
  const el = elements.value

  // 当事人：后端 parties 是对象，键由后端决定（真实数据为 甲方 / 乙方），逐项如实展示
  const parties = el?.parties
  const partyCells = []
  if (parties && typeof parties === 'object' && !Array.isArray(parties)) {
    for (const [k, v] of Object.entries(parties)) {
      if (v) partyCells.push({ key: `party-${k}`, label: k, value: String(v) })
    }
  }

  if (typeText.value) out.push({ key: 'type', label: '合同类型', value: typeText.value })
  out.push(...partyCells)

  const amt = amountParts(el?.amount)
  if (amt.main || amt.text) {
    out.push({ key: 'amount', label: '合同金额', value: amt.main || amt.text, sub: amt.main ? amt.text : '' })
  }

  if (el?.sign_date) out.push({ key: 'sign', label: '签订日期', value: String(el.sign_date) })

  const period = performancePeriodText(el?.performance_period)
  if (period) out.push({ key: 'period', label: '履行期限', value: period })

  if (el?.dispute_resolution) out.push({ key: 'dispute', label: '争议解决', value: String(el.dispute_resolution) })

  const law = typeof el?.governing_law === 'string' ? el.governing_law.trim() : ''
  if (law) out.push({ key: 'law', label: '适用法律', value: law })

  return out
})

// ── B2 风险规则扫描矩阵（恒 R01–R13；命中态只认当前审核结果的 risk_type） ──

/** 规则代码顺序即 RISK_NAMES 的定义顺序（R01 → R13），不另立清单 */
const RISK_CODES = Object.keys(RISK_NAMES)

/**
 * 规则扫描矩阵：恒 R01–R13，每格带「是否命中 + 命中等级 + 可跳转的真实记录」。
 * - `record` 只可能是**当前审核结果里真实存在的 AuditRecord**（命中格才有）；
 *   同一规则命中多条时取判断把握度最高的一条（复用 sortByConfidence，与「重点风险」同序）；
 *   未命中格 `record` 恒为 null —— 绝不为了「可点击」而伪造记录。
 */
const riskMatrix = computed(() => {
  const byType = new Map()
  for (const r of props.ws.riskItems || []) {
    if (!r || !r.risk_type) continue
    const cur = byType.get(r.risk_type) || { items: [], levels: new Set() }
    cur.items.push(r)
    if (r.risk_level) cur.levels.add(r.risk_level)
    byType.set(r.risk_type, cur)
  }

  return RISK_CODES.map((code) => {
    const s = byType.get(code)
    // 同一规则命中多条记录时，按 REPORT_LEVELS 既有顺序（高→中→低）取最高档，不新增等级权重
    const lv = s ? REPORT_LEVELS.find((l) => s.levels.has(l.key)) || null : null
    return {
      code,
      name: RISK_NAMES[code],
      hit: !!s,
      count: s ? s.items.length : 0,
      levelLabel: lv ? lv.label : '',
      color: lv ? lv.color : '',
      record: s ? sortByConfidence(s.items)[0] : null,
    }
  })
})

const hitCount = computed(() => riskMatrix.value.filter((m) => m.hit).length)

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

/** 逐条比对清单（只保留对象形态；status 未知的条目原样保留，不做臆测） */
const clauseList = computed(() => {
  const list = comparison.value?.clauses
  return Array.isArray(list) ? list.filter((c) => c && typeof c === 'object') : []
})

/**
 * 缺失条款：关键缺失在前、普通缺失在后（两组内部都保持后端返回顺序）。
 * 「关键」判定**完全来自后端 `missing_critical`**（与逐条清单按真实 title 对齐），
 * **不新增任何关键条款规则**，也不按 priority 自行推导。
 */
const missingItems = computed(() => {
  const crit = new Set(missingCritical.value)
  const all = clauseList.value.filter((c) => c.status === 'missing')
  return [
    ...all.filter((c) => crit.has(c.title)).map((c) => ({ ...c, critical: true })),
    ...all.filter((c) => !crit.has(c.title)).map((c) => ({ ...c, critical: false })),
  ]
})

const partialItems = computed(() => clauseList.value.filter((c) => c.status === 'partial'))
const coveredItems = computed(() => clauseList.value.filter((c) => c.status === 'covered'))

/** 存在问题 = 缺失 + 部分偏离；关键项数 = 其中的关键缺失（均实时计算，不写死） */
const problemCount = computed(() => missingItems.value.length + partialItems.value.length)
const criticalCount = computed(() => missingItems.value.filter((c) => c.critical).length)

/** 已覆盖清单展开态（默认收起，只有一个布尔，不是第二个组件） */
const coveredOpen = ref(false)

/** 等级分布：档位、中文名、颜色全部取既有 REPORT_LEVELS（不新立一套等级清单） */
const riskSegments = computed(() => stackSegments(
  REPORT_LEVELS.map((l) => ({ key: l.key, label: l.label, value: counts.value[l.key], color: l.color })),
))

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

/**
 * 合同状态（`contracts.status`）—— 生命周期状态，**不是审批状态**。
 * 与 AuditReportDetail.vue / ContractList.vue 统一用同一名称「合同状态」，
 * 避免同一个字段在不同页面被叫成审核状态 / 审批状态。
 */
const statusLabel = computed(() => contractStatusLabel(props.ws.contract?.status))

/**
 * 判断把握度 → 百分比整数。
 * 缺失（后端无该字段，workspace 归一为 0）时返回 null，调用方据此**整个不渲染**，
 * 而不是显示「0%」这种会被误读为「模型完全没把握」的假信息。
 */
function confidencePct(r) {
  const v = r?.confidence
  if (typeof v !== 'number' || !Number.isFinite(v) || v <= 0) return null
  return Math.round(v * 100)
}

/** 完整报告页对应风险卡片的锚点（AuditReportDetail 为每条风险渲染 id="risk-<id>"） */
function openRisk(r) {
  router.push({ path: `/audit/report/${props.ws.contractId}`, hash: `#risk-${r.id}` })
}

function openFullReport() {
  router.push(`/audit/report/${props.ws.contractId}`)
}

/**
 * 完整报告「条款完整性检查」章节锚点。
 * 与 AuditReportDetail.vue 的 `<section id="clauses">` 严格一致（全文注释互相指向）。
 * 该章节锚点只用于**降级定位**：完整报告没有条款卡片级 anchor（唯一锚点是风险卡的 #risk-<id>），
 * 所以条款问题 Tag 统一落到这个章节，**不用数组下标去猜某一张卡片**，也不新增第二套定位机制。
 */
const CLAUSES_ANCHOR = 'clauses'

/** 条款 Tag 的 hover 提示（只描述「可进入比对详情」，可点击性由 Tag 自身视觉表达，不靠 tooltip） */
function clauseTip(c) {
  const name = c?.title || '未命名条款'
  const pri = c?.priority ? `（${priorityLabel(c.priority)}）` : ''
  return `查看「${name}」的比对详情${pri}`
}

/**
 * 条款问题 Tag → 完整报告条款完整性章节。
 * 当前完整报告没有条款卡片级 anchor，按约定降级到章节锚点；因此本函数**不接收条款参数**，
 * 不存在任何依据下标/模糊字符串的定位。真实落点见本轮汇报「Tag 点击定位实现方式」。
 */
function openClause() {
  router.push({ path: `/audit/report/${props.ws.contractId}`, hash: `#${CLAUSES_ANCHOR}` })
}

/** 去修改合同：切到工作台 Tab（不新增任何修改接口，也不替用户选中会话） */
function goRevise() {
  props.ws.setTab('revise')
}
</script>

<style scoped>
/* ── 视觉系统：主色 #1935C3 / 卡白 / 边框 #E5E7EB / 8px 圆角 / 无渐变、无阴影、无动画 ── */
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

/* 卡片通用 */
.rpt-card {
  background: #fff; border: 1px solid #E5E7EB; border-radius: 8px;
  padding: 14px 16px;
}
.rpt-card-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 12px; }
.rpt-card-head h4 { margin: 0; font-size: 15px; font-weight: 600; color: #111827; }
.rpt-card-note { font-size: 12px; color: #9CA3AF; }
.rpt-none { font-size: 13px; color: #9CA3AF; }

/* B 双列（≥1200px 双列，见文末媒体查询） */
.rpt-cols { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 12px; }

/* B1 合同结构化画像 */
.rpt-card-profile { display: flex; flex-direction: column; }
.rpt-kv { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 16px; }
.rpt-kv-i { min-width: 0; }
.rpt-kv-k { font-size: 11.5px; line-height: 1.4; color: #6B7280; }
.rpt-kv-v {
  font-size: 13.5px; line-height: 1.4; color: #111827; margin-top: 3px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.rpt-kv-s {
  font-size: 11.5px; line-height: 1.4; color: #9CA3AF; margin-top: 1px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

/* 风险评分 + 轻量位置条 */
.rpt-score { margin-top: auto; padding-top: 12px; border-top: 1px solid #F3F4F6; }
.rpt-score-row { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.rpt-score-k { font-size: 12px; color: #6B7280; }
.rpt-score-v { font-size: 24px; font-weight: 700; line-height: 1.1; }
.rpt-bar {
  position: relative; height: 6px; border-radius: 3px;
  background: #F3F4F6; margin-top: 8px;
}
.rpt-bar-fill { position: absolute; left: 0; top: 0; height: 100%; border-radius: 3px; }
.rpt-bar-dot {
  position: absolute; top: 50%; width: 11px; height: 11px; border-radius: 50%;
  background: #fff; border: 2px solid #9CA3AF; box-sizing: border-box;
  transform: translate(-50%, -50%);
}
.rpt-bar-cap { margin-top: 6px; font-size: 11.5px; color: #9CA3AF; }

/* B2 规则扫描矩阵：彩色命中格 = 可点击查看详情；灰色未命中格 = 纯展示、不可点。
   命中格的 hover 只有「边框轻微加深 + 背景轻微变化」两项，且**无 transition**
   （不放大、不位移、不投影、不产生任何动画）。 */
.rpt-mx { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 6px; }
.rpt-mx-c {
  height: 52px; box-sizing: border-box;
  border: 1px solid transparent; border-radius: 6px;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 2px; padding: 2px 4px; text-align: center;
  background: #F3F4F6; color: #9CA3AF;
  font-size: 12px; line-height: 1.2; font-weight: 600;
  cursor: default; user-select: none;
}
/* 格子文案：13 类统一为两行结构 —— 第一行 R 编号（等宽体，次级识别信息但保持清晰），
   第二行 中文风险名称（逐字取 RISK_NAMES，8 字名最多折 2 行、不截断），两行居中、结构完全统一。 */
.rpt-mx-code { font-family: ui-monospace, Consolas, monospace; font-size: 11.5px; font-weight: 600; letter-spacing: 0.02em; }
.rpt-mx-name {
  max-width: 100%; font-size: 10.5px; font-weight: 500; line-height: 1.25;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
/* 命中格：唯一可点的状态 */
.rpt-mx-c.is-hit { color: #fff; cursor: pointer; }
.rpt-mx-c.is-hit:hover { border-color: rgba(17, 24, 39, 0.45); filter: brightness(0.9); }
.rpt-mx-c.is-hit:active { filter: brightness(0.82); }
.rpt-mx-c.is-hit:focus-visible { outline: 2px solid #1935C3; outline-offset: 1px; }
/* 未命中格：没有任何 :hover 规则，鼠标移上去不产生任何变化 */
.rpt-mx-cap { margin-top: 10px; font-size: 12.5px; color: #6B7280; }
.rpt-mx-cap b { font-size: 14px; font-weight: 700; color: #111827; padding: 0 1px; }
.rpt-mx-hint { margin-left: 8px; font-size: 11.5px; color: #9CA3AF; }

/* 风险等级分布（保留原有横向分布条） */
.rpt-dist {
  margin-top: 12px; padding-top: 12px; border-top: 1px solid #F3F4F6;
  display: flex; flex-direction: column; gap: 8px;
}
.rpt-dist-k { font-size: 12px; font-weight: 600; color: #6B7280; }

.rpt-stack { display: flex; height: 10px; border-radius: 5px; overflow: hidden; background: #F3F4F6; }
.rpt-stack-lg { height: 12px; border-radius: 6px; margin-top: 12px; }
.rpt-stack-seg { display: block; height: 100%; }
.rpt-legend { display: flex; flex-wrap: wrap; gap: 16px; font-size: 12px; color: #6B7280; }
.rpt-legend-i { display: inline-flex; align-items: center; gap: 6px; }
.rpt-legend-i i { width: 8px; height: 8px; border-radius: 2px; }
.rpt-legend-i b { color: #111827; font-weight: 600; }

/* C 条款完整性（最终版）—— 30 项 → 覆盖率 → 20/4/6 → 问题 N 项 → 问题条款 Tag → 已覆盖折叠 */
.rpt-cmp-open {
  margin-left: auto; background: none; border: none; padding: 0; cursor: pointer;
  font-size: 12.5px; color: #1935C3;
}
.rpt-cmp-open:hover { text-decoration: underline; }

.rpt-cmp-top { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; }
.rpt-cmp-total { font-size: 13px; color: #6B7280; }
.rpt-cmp-total b { font-size: 15px; font-weight: 700; color: #111827; padding: 0 2px; }
.rpt-cmp-rate { display: inline-flex; align-items: baseline; gap: 8px; }
.rpt-cmp-rate-l { font-size: 12px; color: #6B7280; }
.rpt-cmp-rate b { font-size: 24px; font-weight: 700; line-height: 1.1; color: #111827; }

.rpt-cmp-facts { display: flex; margin-top: 10px; }
.rpt-cmp-fact { flex: 1 1 0; font-size: 13px; color: #6B7280; }
.rpt-cmp-fact b { font-size: 14px; font-weight: 700; padding-left: 2px; }

/* 存在问题 N 项（14px 半粗，不做大数字，不与覆盖率抢主视觉） */
.rpt-prob {
  display: flex; align-items: center; gap: 6px; margin-top: 12px;
  font-size: 14px; font-weight: 600; color: #374151;
}
.rpt-prob-dot { flex: 0 0 auto; width: 8px; height: 8px; border-radius: 50%; background: #EF4444; }
.rpt-prob b { font-weight: 700; color: #111827; }

/* 条款问题 Tag 分组 */
.rpt-ctag-group { margin-top: 12px; }
.rpt-ctag-k { margin-bottom: 8px; font-size: 12px; font-weight: 600; color: #6B7280; }
.rpt-ctags { display: flex; flex-wrap: wrap; gap: 8px; }

/* 条款问题 Tag：浅底 + 深色字 + 6px 状态点 + 右箭头（与风险等级 Tag 的实心白字严格区分）
   hover 只改背景/边框，active 只改背景，focus 用 2px 蓝色 outline；
   无 transition、不缩放、不位移、无阴影。 */
.rpt-ctag {
  display: inline-flex; align-items: center; gap: 8px; max-width: 100%;
  height: 30px; padding: 6px 12px; box-sizing: border-box;
  border: 1px solid transparent; border-radius: 6px;
  font-size: 13px; font-weight: 500; line-height: 1;
  cursor: pointer;
}
.rpt-ctag-dot { flex: 0 0 auto; width: 6px; height: 6px; border-radius: 50%; }
.rpt-ctag-t { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rpt-ctag-arrow { flex: 0 0 auto; font-size: 14px; line-height: 1; }
.rpt-ctag:focus-visible { outline: 2px solid #1935C3; outline-offset: 1px; }

.rpt-ctag.is-critical { background: #FEF2F2; color: #B91C1C; border-color: #FECACA; }
.rpt-ctag.is-critical .rpt-ctag-dot { background: #EF4444; }
.rpt-ctag.is-critical .rpt-ctag-arrow { color: #B91C1C; }
.rpt-ctag.is-critical:hover { background: #FEE2E2; border-color: #FCA5A5; }
.rpt-ctag.is-critical:active { background: #FECACA; }

.rpt-ctag.is-plain { background: #F9FAFB; color: #4B5563; border-color: #E5E7EB; }
.rpt-ctag.is-plain .rpt-ctag-dot { background: #9CA3AF; }
.rpt-ctag.is-plain .rpt-ctag-arrow { color: #6B7280; }
.rpt-ctag.is-plain:hover { background: #F3F4F6; border-color: #D1D5DB; }
.rpt-ctag.is-plain:active { background: #E5E7EB; }

.rpt-ctag.is-partial { background: #FFF7ED; color: #C2410C; border-color: #FED7AA; }
.rpt-ctag.is-partial .rpt-ctag-dot { background: #F97316; }
.rpt-ctag.is-partial .rpt-ctag-arrow { color: #C2410C; }
.rpt-ctag.is-partial:hover { background: #FFEDD5; border-color: #FDBA74; }
.rpt-ctag.is-partial:active { background: #FED7AA; }

/* 已覆盖：默认收起，展开为 3 列自适应 grid，只显示真实条款名 */
.rpt-cov { margin-top: 14px; padding-top: 10px; border-top: 1px solid #F3F4F6; }
.rpt-cov-head {
  display: flex; align-items: center; justify-content: space-between; gap: 12px; width: 100%;
  padding: 6px 0; background: none; border: none; cursor: pointer;
  font-size: 13px; color: #374151; text-align: left;
}
.rpt-cov-t { font-weight: 600; }
.rpt-cov-act { font-size: 12.5px; color: #1935C3; }
.rpt-cov-head:hover .rpt-cov-act { text-decoration: underline; }
.rpt-cov-head:focus-visible { outline: 2px solid #1935C3; outline-offset: 1px; }
.rpt-cov-list {
  display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px 16px; margin-top: 6px;
}
.rpt-cov-i { display: flex; align-items: baseline; gap: 6px; min-width: 0; font-size: 12.5px; color: #4B5563; }
.rpt-cov-tick { flex: 0 0 auto; font-style: normal; color: #16A34A; opacity: 0.55; }
.rpt-cov-n { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* D 关联风险 */
.rpt-cross { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.rpt-cross-i {
  display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap;
  background: #F9FAFB; border: 1px solid #E5E7EB; border-radius: 8px; padding: 8px 12px;
}
.rpt-cross-txt { font-size: 13px; color: #374151; }
.rpt-cross-ref { font-size: 12px; color: #6B7280; }

/* E 重点风险 */
.rpt-group + .rpt-group { margin-top: 14px; }
.rpt-group-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.rpt-dot { width: 8px; height: 8px; border-radius: 50%; }
.rpt-group-t { font-size: 13px; font-weight: 600; color: #374151; }
.rpt-group-n { font-size: 12px; color: #9CA3AF; }
.rpt-risks { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.rpt-risk {
  display: flex; align-items: center; gap: 12px;
  background: #F9FAFB; border: 1px solid #E5E7EB; border-radius: 8px;
  padding: 10px 12px;
}
.rpt-risk-l { flex: 0 0 auto; display: flex; align-items: center; gap: 6px; min-width: 240px; }
.rpt-risk-lv { flex: 0 0 auto; }
.rpt-risk-name { font-size: 14px; font-weight: 600; color: #111827; }
.rpt-risk-code { font-family: ui-monospace, Consolas, monospace; font-size: 11.5px; color: #9CA3AF; }
.rpt-risk-reason {
  flex: 1 1 auto; min-width: 0; margin: 0;
  font-size: 13px; line-height: 1.7; color: #374151;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.rpt-risk-meta { flex: 0 0 auto; display: flex; align-items: center; gap: 8px; }
/* 判断把握度：固定中性灰，不用等级色、不做渐变（与风险严重程度无关） */
.rpt-conf {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 12px; color: #6B7280; white-space: nowrap; cursor: default;
}
.rpt-conf-dot { width: 6px; height: 6px; border-radius: 50%; background: #9CA3AF; }
.rpt-detect {
  font-size: 11.5px; line-height: 18px; color: #9CA3AF; white-space: nowrap;
  border: 1px solid #E5E7EB; border-radius: 4px; padding: 0 6px;
}
.rpt-risk-more {
  flex: 0 0 auto;
  background: none; border: none; padding: 0; cursor: pointer;
  font-size: 12.5px; color: #1935C3;
}
.rpt-risk-more:hover { text-decoration: underline; }

/* F 操作区 */
.rpt-actions {
  background: #fff; border: 1px solid #E5E7EB; border-radius: 8px;
  padding: 12px 16px;
  display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap;
}
.rpt-actions-l { display: flex; gap: 10px; flex-wrap: wrap; }
.rpt-actions-r { display: flex; gap: 18px; flex-wrap: wrap; font-size: 12px; color: #6B7280; }

.rpt-foot { margin: 0; font-size: 12px; line-height: 1.8; color: #9CA3AF; }
.rpt-foot b { color: #6B7280; }

/* <1200px：双列改单列堆叠（与页面既有 1200 断点一致）；已覆盖清单 3 列 → 2 列 */
@media (max-width: 1200px) {
  .rpt-cols { grid-template-columns: minmax(0, 1fr); }
  .rpt-cov-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

@media (max-width: 900px) {
  .rpt-kv { grid-template-columns: minmax(0, 1fr); }
  .rpt-cov-list { grid-template-columns: minmax(0, 1fr); }
  .rpt-risk { flex-direction: column; align-items: stretch; gap: 6px; }
  .rpt-risk-l { min-width: 0; }
  .rpt-risk-more { align-self: flex-start; }
  .rpt-actions { flex-direction: column; align-items: stretch; }
}
</style>
