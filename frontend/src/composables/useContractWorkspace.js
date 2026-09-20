/**
 * useContractWorkspace — 合同详情 V2 工作台的唯一状态层
 *
 * 设计原则（全部基于后端真实能力，不虚构字段/接口）：
 * 1. 三个**只读**数据源：GET /audit-result（当前风险）、GET /clause-comparison（当前比对问题）、
 *    GET /revisions（历史修改会话）。三者语义严格区分，不互相替代。
 * 2. 修改能力**只有一套**：POST /revise（replace | add_clause）→ ClauseRevision → GET /revisions
 *    → GET /revised-docx。本文件不新建任何修改/会话/DOCX 体系。
 * 3. 定位能力**只有一套**：POST /locate-clause（只读定位工具；不调 LLM、不写库）。
 *    found=true 只代表「后端在正文里找到了候选」，**不代表已建立 anchor**；
 *    anchor 只能由 POST /revise 在落库时建立（original_clause_text）。
 * 4. 前端预检（previewLocate）只用于**UI 提示与导出前预警**，绝不作为「可导出」的结论；
 *    最终以下载时后端 400 detail 为准。
 * 5. 「用户确认定位」「新增条款的插入位置确认」等**只是 UI 状态**，后端没有对应持久化字段
 *    （clause_revisions / audit_records 均无 location_confirmed / anchor_confirmed 之类列）。
 *    刷新后这些 UI 状态会丢失——但一旦提交过一轮修改，服务端 anchor 已建立，效果不会丢。
 *
 * session key 约定（**不得更改**，真实数据里已固化）：
 *   '__overview__'            总体修改会话
 *   String(AuditRecord.id)    风险会话
 *   '__cmp__' + 标准条款 title 条款比对会话
 */
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  getContractDetail, getAuditResult, getClauseComparison, getRevisions,
  reviseClause, getAddClauseSuggestion, downloadRevisedDocx, locateClause,
  getAuditReport,
  triggerAudit, reviewContract, approveContract,
  getFeedback, submitFeedback, deleteFeedback,
  // 采用态（「确认采用此版」）
  adoptRevision,
  // 总体修改（整份合同总控台）—— 只读汇总 + 结构化方案 + 用户确认落库
  // 注意：/overview/confirm 是「纳入方案」的**唯一**落库入口（见 toggleIncludeItem）。
  getRevisionOverview, createOverviewProposal, listOverviewProposals,
  getOverviewProposal, confirmOverviewProposal,
} from '../api/contract.js'
import { getTemplates } from '../api/template.js'
import { riskName, riskLevelLabel } from '../constants/riskTypes.js'

// ── 纯业务规则全部来自 workspaceLogic.js（无框架依赖，可单测）──
import {
  OVERVIEW_KEY, CMP_PREFIX, SESSION_GROUPS,
  cnNo, cnToInt, parseHeadings, clipText,
  previewLocate, previewLocatable,
  cmpSessionKey, hasComparisonIssue, comparisonAction,
  displayInstruction, buildHistory,
  classifySessionGroup, isAddOnlyRevs, isMissingTypeRiskShape, sessionActionFor,
  addPositionOk, positionText, beforeAnchorOf,
  isExportableRevision, docxStateFor,
  modificationStateFor, countConfirmedSessions, sessionExportable,
  proposalItemKind, proposalItemTitle, canIncludeProposalItem, proposalBlockingText,
  referenceClauseText, comparisonLayout, locateCandidates, roundDiff,
  locationStateFor, hasReliableLocation, LOCATION_STATES,
} from './workspaceLogic.js'

// 兼容既有子组件的 import 来源（它们从本文件取值），统一在此再导出。
export {
  OVERVIEW_KEY, CMP_PREFIX, SESSION_GROUPS,
  cnNo, cnToInt, parseHeadings, clipText,
  previewLocate, previewLocatable,
  cmpSessionKey, hasComparisonIssue, comparisonAction,
  displayInstruction,
  classifySessionGroup, isAddOnlyRevs, isMissingTypeRiskShape, sessionActionFor,
  addPositionOk, positionText, beforeAnchorOf,
  isExportableRevision, docxStateFor,
  modificationStateFor, countConfirmedSessions, sessionExportable,
  proposalItemKind, proposalItemTitle, canIncludeProposalItem, proposalBlockingText,
  referenceClauseText, comparisonLayout, locateCandidates, roundDiff,
  locationStateFor, hasReliableLocation, LOCATION_STATES,
}

// ════════════════════════════════════════════════════════════════
// 主 composable
// ════════════════════════════════════════════════════════════════

export function useContractWorkspace() {
  const route = useRoute()
  const contractId = computed(() => String(route.params.id || ''))

  // ── 竞态令牌：合同切换/重复请求时丢弃过期响应（对齐 AuditReportDetail 的做法）──
  const seq = ref(0)
  let pollToken = 0

  // ── 一级 Tab（页面级导航）──
  // 放在状态层是为了让「风险详情 → 去修改合同」「条款比对 → 去修改条款」等跨 Tab 跳转
  // 只改一处状态，不需要在每个子组件里 emit 一串事件。
  const activeTab = ref('text')
  function setTab(name) {
    activeTab.value = name
  }

  // ── 合同与状态 ──
  const loading = ref(true)
  const detailError = ref('')
  const contract = ref(null)

  // ── 风险（唯一来源 GET /audit-result）──
  const riskLoading = ref(false)
  const riskError = ref('')
  const riskTotal = ref(0)
  const riskItems = ref([])
  const hasCurrentResult = ref(true)

  // ── 条款比对（唯一来源 GET /clause-comparison）──
  const comparing = ref(false)
  const comparisonError = ref('')
  const clauseComparison = ref(null)

  // ── 修改历史（唯一来源 GET /revisions）──
  const revisionsLoading = ref(false)
  const revisionsError = ref('')
  const revisions = ref([])

  // ── 总体修改（整份合同总控台）──
  // 只读汇总 = GET /overview（全部专项会话的原文/最新结果/法律依据/剩余风险/定位/导出状态）
  const overviewLoading = ref(false)
  const overviewError = ref('')
  const overview = ref(null)
  // 综合修改方案 = POST /overview/plan（只写方案表，不产生任何 ClauseRevision）
  const planLoading = ref(false)
  const planError = ref('')
  const proposals = ref([])
  const activeProposalId = ref(null)
  // 「已纳入方案」的**唯一权威来源** = 后端 `proposal.confirmed_ids`
  // （POST /overview/confirm 落库后由 GET /overview/proposals 原样带回）：
  //   - 点击「纳入方案」→ 调接口 → 成功后刷新方案与汇总 → UI 才显示已纳入；
  //   - 刷新页面后状态从后端恢复，不再依赖任何前端会话态；
  //   - 旧实现把 includedItemIds 当本地 Set 维护，刷新即丢、且与真实落库无关，已删除。
  const confirmingItemId = ref(null)
  // 标准条款模板（用于条款比对的「真实参考条款」；无模板时比对区退化为两栏）
  const referenceTemplates = ref([])

  // ── 反馈 ──
  const loadedFeedbacks = ref([])

  // ── 审核报告（GET /audit-report；无报告时后端返回 404，不是 200+null）──
  const report = ref(null)
  const reportLoading = ref(false)
  const reportError = ref('')
  const reportFetched = ref(false)

  // ── 审核/复核/验收 ──
  const auditing = ref(false)
  const reviewing = ref(false)

  // ── DOCX ──
  const docx = reactive({ loading: false, error: '' })

  // ── 采用态确认请求（「确认采用此版」）──
  const adopting = ref(false)

  // ── 每个会话的 UI 状态（**仅 UI，非后端字段**）──
  // 结构：{ input, pending, locate, locateLoading, confirmAnchor, refineMode }
  const uiMap = reactive({})

  function uiFor(key) {
    if (!uiMap[key]) {
      uiMap[key] = {
        input: '',
        pending: null,        // 在途的用户指令（乐观展示）
        locate: null,         // POST /locate-clause 的结果
        locateLoading: false,
        locateError: '',
        confirmAnchor: null,  // 用户确认的位置（UI 态；提交 revise 后才成为服务端 anchor）
        refineMode: false,    // 是否处于「继续修改刚生成的新增条款」
        // 「重新指定位置」：仅 UI 态，把右栏强制切回未定位工作区。
        // 不删除、不修改任何已落库修订；用户重新确认候选后自动清掉。
        relocateMode: false,
      }
    }
    return uiMap[key]
  }

  // ══════════════════════════════════════════════════════════════
  // 派生：比对问题 / 会话列表
  // ══════════════════════════════════════════════════════════════

  /** 需要处理的比对问题（missing → 新增；partial/偏离/补全 → 替换） */
  const comparisonIssues = computed(() => {
    const list = clauseComparison.value?.clauses || []
    return list.filter((r) => r.status === 'missing' || hasComparisonIssue(r))
  })

  const headings = computed(() => parseHeadings(contract.value?.parsed_text || ''))

  const parsedText = computed(() => contract.value?.parsed_text || '')

  /** 当前审核结果里的风险 id 集合（用于会话归类） */
  const currentRiskIds = computed(() => new Set(riskItems.value.map((r) => String(r.id))))

  /**
   * 会话分组优先级由 workspaceLogic.classifySessionGroup 定义并单测：
   *  1. key === '__overview__'                → 总体修改会话
   *  2. 该会话全部 revision 都是 add_clause    → 新增条款
   *  3. key 以 '__cmp__' 开头                  → 条款比对修改
   *  4. key 命中当前 audit-result 的风险 id    → 风险修改
   *  5. 其他                                   → 历史会话
   */
  function classifySession(key, revs, riskIds) {
    return classifySessionGroup(key, revs, riskIds)
  }

  /**
   * 会话列表 = 持久化历史（GET /revisions 按 clause_key 分组）
   *           ∪ 当前风险入口（即使 0 轮也要能发起修改）
   *           ∪ 当前比对问题入口
   *           ∪ 常驻总体会话
   * 多轮 = 同一 key 的多行，按 id 升序 —— **不合并、不删除**。
   */
  const sessions = computed(() => {
    const map = new Map()
    const ensure = (key) => {
      const k = String(key || '')
      if (!map.has(k)) {
        map.set(k, {
          key: k, group: 'history', revs: [], rounds: 0,
          risk: null, cmp: null,
          clauseText: '', clauseNo: null, title: '', subtitle: '',
          operation: 'replace', lastRev: null, prediction: null,
          locatable: false,
        })
      }
      return map.get(k)
    }

    // 1) 持久化历史（先建，保证「当前风险消失后历史仍在」）
    for (const r of revisions.value) ensure(r.clause_key || '').revs.push(r)
    // 2) 当前风险
    for (const risk of riskItems.value) ensure(risk.id).risk = risk
    // 3) 当前比对问题
    for (const row of comparisonIssues.value) ensure(cmpSessionKey(row)).cmp = row
    // 4) 总体会话常驻
    ensure(OVERVIEW_KEY)

    const riskIds = currentRiskIds.value
    const out = []
    for (const s of map.values()) {
      s.revs.sort((a, b) => (a.id || 0) - (b.id || 0))
      s.rounds = s.revs.length
      s.lastRev = s.rounds ? s.revs[s.rounds - 1] : null
      s.operation = s.lastRev?.operation || (s.risk && isMissingTypeRisk(s.risk) ? 'add_clause' : 'replace')
      s.group = classifySession(s.key, s.revs, riskIds)

      // ── 展示文案 ──
      if (s.key === OVERVIEW_KEY) {
        s.title = '总体修改（整个合同）'
        s.subtitle = '面向整个合同的综合讨论 / 修改规划'
      } else if (s.risk) {
        s.title = `${s.risk.risk_type} · ${riskName(s.risk.risk_type)}`
        s.subtitle = `${riskLevelLabel(s.risk.risk_level)}`
        s.clauseNo = s.risk.clause_position?.clause_no ?? null
        s.clauseText = ''
      } else if (s.cmp) {
        s.title = s.cmp.title || '未命名条款'
        s.subtitle = s.cmp.status === 'missing' ? '缺失条款' : '存在偏离'
      } else if (s.key.startsWith(CMP_PREFIX)) {
        s.title = s.key.slice(CMP_PREFIX.length) || '未命名条款'
        s.subtitle = '历史条款比对会话'
      } else if (/^\d+$/.test(s.key)) {
        s.title = `风险记录 #${s.key}`
        s.subtitle = '历史风险会话（该记录已不在当前审核结果中）'
      } else {
        s.title = s.key || '未命名会话'
        s.subtitle = '历史会话'
      }

      // ── 可提交给 /revise 的 clause_text（逐字原文优先）──
      const ui = uiMap[s.key]
      if (s.key !== OVERVIEW_KEY) {
        if (ui?.confirmAnchor?.original_text) s.clauseText = ui.confirmAnchor.original_text
        else if (ui?.locate?.found && ui.locate.original_text) s.clauseText = ui.locate.original_text
        else if (s.risk) {
          const anchorText = s.risk.clause_position?.original_text
          s.clauseText = anchorText || s.risk.clause_text || ''
        } else if (s.cmp) s.clauseText = s.cmp.matched_text || s.cmp.title || ''
        else if (s.lastRev) s.clauseText = s.lastRev.clause_text || ''
      } else {
        s.clauseText = parsedText.value
      }

      // ── 前端定位预检（仅 UI 提示）──
      if (s.key === OVERVIEW_KEY) s.prediction = null
      else if (isAddOnly(s)) s.prediction = null
      else s.prediction = previewLocate(parsedText.value, s.clauseText)

      // ── 是否「后端已自动定位」（审核阶段 Anchor 存在）──
      const autoAnchor = !!s.risk?.clause_position?.original_text
      s.locatable = autoAnchor || s.prediction?.status === 'unique' || s.prediction?.status === 'disambiguated'

      // 会话副标题补充轮数
      s.subtitle = s.subtitle + (s.rounds ? ` · 已修改 ${s.rounds} 轮` : ' · 尚未修改')
      out.push(s)
    }

    // 排序：分组顺序 → 组内按等级/名称稳定排序
    const order = SESSION_GROUPS.reduce((acc, g, i) => ((acc[g.key] = i), acc), {})
    const lvlRank = { high: 0, medium: 1, low: 2 }
    out.sort((a, b) => {
      const ga = order[a.group] ?? 9
      const gb = order[b.group] ?? 9
      if (ga !== gb) return ga - gb
      if (a.key === OVERVIEW_KEY) return -1
      if (b.key === OVERVIEW_KEY) return 1
      const la = lvlRank[a.risk?.risk_level] ?? 3
      const lb = lvlRank[b.risk?.risk_level] ?? 3
      if (la !== lb) return la - lb
      return String(a.title).localeCompare(String(b.title), 'zh')
    })
    return out
  })

  const sessionsByGroup = computed(() => {
    const g = {}
    for (const s of SESSION_GROUPS) g[s.key] = []
    for (const s of sessions.value) (g[s.group] || (g[s.group] = [])).push(s)
    return g
  })

  const activeKey = ref(OVERVIEW_KEY)
  /** 从原文标注跳转过来时要高亮/展开的风险记录 id（纯 UI 态） */
  const focusRiskId = ref(null)
  const activeSession = computed(
    () => sessions.value.find((s) => s.key === activeKey.value) || sessions.value.find((s) => s.key === OVERVIEW_KEY) || null,
  )

  /** 当前选中会话的消息（多轮：一轮 = 一条 ClauseRevision，绝不合并） */
  const activeRounds = computed(() => activeSession.value?.revs || [])

  function selectSession(key) {
    activeKey.value = String(key || OVERVIEW_KEY)
  }

  function ensureActive() {
    if (!sessions.value.some((s) => s.key === activeKey.value)) activeKey.value = OVERVIEW_KEY
  }

  // ══════════════════════════════════════════════════════════════
  // 会话语义判定
  // ══════════════════════════════════════════════════════════════

  /** 纯新增条款会话（全部 revision 都是 add_clause） */
  function isAddOnly(s) {
    return !!s && isAddOnlyRevs(s.revs)
  }

  /**
   * 「缺失型风险」判断 —— **不按 R 编号机械判断**（规则见 workspaceLogic.isMissingTypeRiskShape）。
   * 真实事实：R09(50 条) 与 R08(6 条) 的 clause_text 是「全文未出现…」这类合成描述，
   * 并非合同正文，永远无法在 parsed_text 中定位。
   */
  function isMissingTypeRisk(risk) {
    if (!risk) return false
    return isMissingTypeRiskShape(risk.clause_text, risk.clause_position?.original_text)
  }

  /** 该会话应走「新增条款」还是「替换修改」（规则见 workspaceLogic.sessionActionFor） */
  function sessionAction(s) {
    if (!s) return 'replace'
    return sessionActionFor({
      key: s.key,
      revs: s.revs,
      cmpStatus: s.cmp?.status || '',
      riskMissingType: !!s.risk && isMissingTypeRisk(s.risk),
    })
  }

  // ══════════════════════════════════════════════════════════════
  // 定位状态（UI 态 + 后端 locator 结论）
  // ══════════════════════════════════════════════════════════════

  /**
   * 会话定位状态徽标（左栏文字 + 右栏卡片头部标签）。
   *
   * 口径与右栏工作区**完全统一**（见 locationStateForSession / LOCATION_STATES）：
   *   - 已确认定位      → 用户确认的候选 / 已落库锚点
   *   - 可定位但未确认  → 审核阶段锚点 / 后端只读定位命中 / 前端预检通过
   *   - 未定位          → 以上都没有（含"多处命中无法消歧"）
   *
   * 「可定位但未确认」必须保留"未确认"字样：预检与审核锚点都只是**系统能找到位置**，
   * 不等于用户已经确认，两者混为一谈会让用户以为改哪里已经定了。
   */
  function locateBadge(s) {
    if (!s) return { type: 'info', text: '—' }
    if (s.key === OVERVIEW_KEY) {
      return { type: 'info', text: '整份合同总控台（不直接写入合同文件）' }
    }
    if (sessionAction(s) === 'add_clause') {
      const ui = uiMap[s.key] || {}
      const pos = ui.confirmAnchor?.position || s.lastRev?.position
      return pos
        ? { type: 'success', text: `已确认插入位置：${positionText(pos)}` }
        : { type: 'warning', text: '待确认插入位置' }
    }

    const state = locationStateForSession(s)?.key
    const ui = uiMap[s.key] || {}
    if (state === 'confirmed') {
      const no = ui.confirmAnchor?.clause_no ?? s.risk?.clause_position?.clause_no ?? s.lastRev?.clause_no
      return {
        type: 'success',
        text: no ? `已确认位置：第${cnNo(no)}条` : '已确认位置',
      }
    }
    if (state === 'locatable') {
      const no = ui.locate?.clause_no ?? s.risk?.clause_position?.clause_no
      return {
        type: 'primary',
        text: no ? `可定位但未确认：第${cnNo(no)}条` : '可定位但未确认',
      }
    }
    if (ui.locate && !ui.locate.found && (ui.locate.candidates || []).length > 1) {
      return { type: 'warning', text: '找到多处，需你选择正确的一处' }
    }
    if (s.prediction?.status === 'ambiguous') {
      return { type: 'warning', text: '原文多处命中，无法自动消歧' }
    }
    return { type: 'warning', text: '未定位' }
  }

  /**
   * 是否需要用户指定位置 —— 与右栏工作区同一口径（三态中的 unlocated）。
   * 「可定位但未确认」不算 needsLocate：系统已经能给出位置，只是等用户点确认。
   */
  function needsLocate(s) {
    if (!s) return false
    if (s.key === OVERVIEW_KEY) return false
    if (sessionAction(s) === 'add_clause') return false
    return locationStateForSession(s)?.key === 'unlocated'
  }

  async function runLocate(s, payload) {
    if (!s) return null
    const ui = uiFor(s.key)
    ui.locateLoading = true
    ui.locateError = ''
    try {
      const res = await locateClause(contractId.value, payload || { text: s.clauseText })
      const data = res?.data || null
      ui.locate = data
      return data
    } catch (e) {
      ui.locateError = e?.response?.data?.detail || e?.message || '定位请求失败'
      ui.locate = null
      return null
    } finally {
      ui.locateLoading = false
    }
  }

  /** 用户确认某个定位候选/结果 → 记为 UI 态 anchor（提交 revise 后才成为服务端 anchor） */
  function confirmLocate(s, candidate) {
    if (!s) return
    const ui = uiFor(s.key)
    const c = candidate || ui.locate
    if (!c || !c.original_text) {
      ElMessage.warning('请选择一条候选原文后再确认')
      return
    }
    ui.confirmAnchor = {
      clause_no: c.clause_no ?? null,
      clause_title: c.clause_title ?? null,
      original_text: c.original_text,
      start: c.start ?? null,
      end: c.end ?? null,
    }
    // 已重新确认位置 → 退出「重新指定位置」态，回到已定位工作区
    ui.relocateMode = false
    ElMessage.success('已确认修改位置（下次提交修改时由后端建立原文锚点）')
  }

  /**
   * 「重新指定位置」：仅切换前端工作态，**不碰数据库、不调任何写接口**。
   *
   * 语义：用户觉得当前锚点/位置不对，想重新选一次。已落库的 ClauseRevision 一律不动
   * （历史记录不因前端动作被改写）；用户重新确认候选后回到已定位工作区再提交。
   */
  function startRelocate(s) {
    if (!s) return
    const ui = uiFor(s.key)
    ui.confirmAnchor = null
    ui.locate = null
    ui.locateError = ''
    ui.relocateMode = true
  }

  function clearLocate(s) {
    if (!s) return
    const ui = uiFor(s.key)
    ui.confirmAnchor = null
    ui.locate = null
    ui.locateError = ''
  }

  // ══════════════════════════════════════════════════════════════
  // 右栏定位三态（统一口径，见 workspaceLogic.LOCATION_STATES）
  // ══════════════════════════════════════════════════════════════

  /**
   * 某个会话的定位三态：未定位 / 可定位但未确认 / 已确认定位。
   *
   * 判定来源（全部复用既有能力，不新写第三套定位规则）：
   *   - `lastRev.original_clause_text`：已落库锚点（该替换已能安全写入修订版合同）
   *   - `ui.confirmAnchor`：用户在本次会话里点选的候选
   *   - `risk.clause_position.original_text`：审核阶段锚点
   *   - `ui.locate.found`：后端只读定位接口的结论
   *   - `previewLocatable`：前端预检（**不等于用户确认**，因此单列为 locatable）
   */
  function locationStateForSession(s) {
    if (!s) return LOCATION_STATES.unlocated
    const ui = uiMap[s.key] || {}
    const loc = ui.locate
    return locationStateFor({
      relocateMode: !!ui.relocateMode,
      confirmedAnchor: ui.confirmAnchor?.original_text || '',
      dbAnchor: s.lastRev?.original_clause_text || '',
      autoAnchor: s.risk?.clause_position?.original_text || '',
      locatable: !!(loc?.found) || previewLocatable(parsedText.value, s.clauseText),
    })
  }

  /** 当前会话是否已有可靠当前位置（决定右栏进"已定位工作区"还是"未定位工作区"） */
  function hasReliableLocationNow(s) {
    return hasReliableLocation(locationStateForSession(s)?.key)
  }

  // ══════════════════════════════════════════════════════════════
  // 上下文组装（沿用 instruction 字段，不改请求体、不改后端）
  // ══════════════════════════════════════════════════════════════

  function riskContextItems(risk) {
    const items = []
    if (risk.risk_type) items.push({ k: '风险类型', v: `${risk.risk_type} · ${riskName(risk.risk_type)}` })
    if (risk.risk_level) items.push({ k: '风险等级', v: riskLevelLabel(risk.risk_level) })
    if (risk.clause_text) items.push({ k: '涉及条款', v: risk.clause_text })
    if (risk.reason) items.push({ k: '判定理由', v: risk.reason })
    if (risk.risk_description) items.push({ k: '风险说明', v: risk.risk_description })
    if (risk.suggestion) items.push({ k: '修改建议', v: risk.suggestion })
    if (risk.example) items.push({ k: '参考示例', v: risk.example })
    if (risk.legal_basis) items.push({ k: '法律依据', v: risk.legal_basis })
    if (risk.grounding_warning) items.push({ k: '证据提示', v: '该建议含未经证据核实的数值，请结合合同原文确认' })
    return items
  }

  function cmpContextItems(row) {
    const items = []
    if (row.title) items.push({ k: '标准条款', v: row.title })
    if (row.status) items.push({ k: '比对状态', v: row.status })
    if (row.matched_text) items.push({ k: '当前条款', v: row.matched_text })
    if (row.deviation) items.push({ k: '偏离说明', v: row.deviation })
    if (row.completion) items.push({ k: '补全建议', v: row.completion })
    if (row.risk) items.push({ k: '风险说明', v: row.risk })
    if (row.related_law) items.push({ k: '相关法条', v: row.related_law })
    return items
  }

  const activeContextItems = computed(() => {
    const s = activeSession.value
    if (!s) return []
    if (s.risk) return riskContextItems(s.risk)
    if (s.cmp) return cmpContextItems(s.cmp)
    return []
  })

  /** 单个会话的上下文块（沿用 instruction 字段） */
  function withSessionContext(s, instruction) {
    const items = s?.risk ? riskContextItems(s.risk) : s?.cmp ? cmpContextItems(s.cmp) : []
    const ui = s ? uiMap[s.key] || {} : {}
    const extra = []
    if (ui.confirmAnchor) {
      extra.push({ k: '已确认修改位置', v: (ui.confirmAnchor.clause_no ? `第${ui.confirmAnchor.clause_no}条 ` : '') + clipText(ui.confirmAnchor.original_text, 200) })
    }
    const all = [...items, ...extra]
    if (!all.length) return instruction
    const head = s?.risk
      ? '【当前修改依据】以下问题由「风险预警」发现，请针对该问题修订条款：'
      : '【当前修改依据】以下差异由「条款比对」发现，请针对该差异修订条款：'
    return `${head}\n${all.map((it) => `- ${it.k}：${it.v}`).join('\n')}\n\n【用户修改要求】\n${instruction}`
  }

  /** 总体会话：汇总各专项会话的最新一轮，交给 LLM 做整体规划 */
  function buildOverviewDigest() {
    const risk = []
    const cmp = []
    for (const s of sessions.value) {
      if (s.key === OVERVIEW_KEY || !s.rounds) continue
      const last = s.lastRev
      const lines = [
        `- ${s.title}（共 ${s.rounds} 轮）：`,
        `  用户最终要求：${clipText(displayInstruction(last.instruction), 200)}`,
        `  最新修订结果：${clipText(last.revised_clause, 400)}`,
      ]
      if (last.explanation) lines.push(`  说明：${clipText(last.explanation, 200)}`)
      if (last.operation === 'add_clause') lines.push(`  操作：新增条款（插入位置：${positionText(last.position)}）`)
      if (last.legal_basis?.length) lines.push(`  法律依据：${last.legal_basis.join('；')}`)
      if (last.remaining_risks?.length) lines.push(`  剩余风险：${last.remaining_risks.join('；')}`)
      ;(s.key.startsWith(CMP_PREFIX) ? cmp : risk).push(lines.join('\n'))
    }
    const parts = []
    if (risk.length) parts.push('【风险专项修改记录】\n' + risk.join('\n'))
    if (cmp.length) parts.push('【条款比对专项修改记录】\n' + cmp.join('\n'))
    return parts.join('\n\n')
  }

  function withOverviewContext(instruction) {
    const digest = buildOverviewDigest()
    if (!digest) return instruction
    return (
      '【当前修改依据】以下为各专项会话的修改记录，请结合这些要求从整个合同的角度统一修改：\n' +
      digest +
      '\n\n【用户修改要求】\n' +
      instruction
    )
  }

  // ══════════════════════════════════════════════════════════════
  // 数据加载
  // ══════════════════════════════════════════════════════════════

  async function loadContract() {
    const my = ++seq.value
    loading.value = true
    detailError.value = ''
    try {
      const res = await getContractDetail(contractId.value)
      if (my !== seq.value) return
      contract.value = res.data || null
    } catch (e) {
      if (my !== seq.value) return
      detailError.value = e?.response?.status === 404 ? '合同不存在或无权查看' : '无法加载合同详情，请确认后端已启动'
      contract.value = null
    } finally {
      if (my === seq.value) loading.value = false
    }
  }

  async function loadAuditResult() {
    const id = contractId.value
    if (!id) return
    riskLoading.value = true
    riskError.value = ''
    try {
      const res = await getAuditResult(id)
      if (id !== contractId.value) return
      const d = res?.data || {}
      hasCurrentResult.value = d.has_current_result ?? true
      riskTotal.value = d.total || 0
      riskItems.value = (d.items || []).map((r) => ({
        id: r.id,
        audit_batch: r.audit_batch,
        risk_type: r.risk_type,
        risk_level: r.risk_level,
        level: riskLevelLabel(r.risk_level),
        clause_text: r.clause_text || '',
        clause_position: r.clause_position || null,
        clause_no: r.clause_position?.clause_no ?? null,
        clause_title: r.clause_position?.clause_title ?? null,
        reason: r.reason || '',
        suggestion: r.suggestion || '',
        detection_method: r.detection_method || '',
        confidence: r.confidence ?? 0,
        evidence: r.evidence || null,
        recommendation: r.recommendation || null,
        risk_description: r.recommendation?.risk_description || '',
        example: r.recommendation?.example || '',
        legal_basis: r.recommendation?.legal_basis || '',
        grounding: r.recommendation?.grounding || null,
        grounding_warning: r.recommendation?.grounding?.passed === false,
        feedback_status: r.feedback_status || 'pending',
        result_status: r.result_status || 'valid',
      }))
    } catch (e) {
      if (id !== contractId.value) return
      riskItems.value = []
      riskTotal.value = 0
      riskError.value = e?.response?.data?.detail || '审核结果加载失败'
    } finally {
      riskLoading.value = false
    }
  }

  async function loadComparison() {
    const id = contractId.value
    if (!id) return
    comparing.value = true
    comparisonError.value = ''
    try {
      const res = await getClauseComparison(id)
      if (id !== contractId.value) return
      // 后端在「不可见 / 无正文 / 比对失败 / 缓存无 clauses」时统一返回 200 + data:null，
      // 前端无法区分「未生成」与「生成失败」——这里如实标注，不假装成功。
      clauseComparison.value = res?.data || null
    } catch (e) {
      if (id !== contractId.value) return
      clauseComparison.value = null
      comparisonError.value = e?.response?.data?.detail || '条款比对加载失败'
    } finally {
      comparing.value = false
    }
  }

  async function loadRevisions() {
    const id = contractId.value
    if (!id) return
    revisionsLoading.value = true
    revisionsError.value = ''
    try {
      const res = await getRevisions(id)
      if (id !== contractId.value) return
      revisions.value = Array.isArray(res?.data) ? res.data : []
      ensureActive()
    } catch (e) {
      if (id !== contractId.value) return
      revisions.value = []
      revisionsError.value = e?.response?.data?.detail || '修改历史加载失败'
    } finally {
      revisionsLoading.value = false
    }
  }

  // ══════════════════════════════════════════════════════════════
  // 总体修改（整份合同总控台）数据加载
  // ══════════════════════════════════════════════════════════════

  /**
   * 总控台只读汇总：GET /overview。
   * 返回全部**专项修改会话**的原文 / 当前最新修改结果 / 法律依据 / 剩余风险 / 定位状态 / 导出状态，
   * 以及「已确认修改」的权威判据 sessions[].export.exportable。只读、不调 LLM、可重复调用。
   */
  async function loadOverview() {
    const id = contractId.value
    if (!id) return
    overviewLoading.value = true
    overviewError.value = ''
    try {
      const res = await getRevisionOverview(id)
      if (id !== contractId.value) return
      overview.value = res?.data || null
    } catch (e) {
      if (id !== contractId.value) return
      overview.value = null
      overviewError.value = e?.response?.data?.detail || '修改点汇总加载失败'
    } finally {
      overviewLoading.value = false
    }
  }

  /** 历史综合方案：GET /overview/proposals（刷新后恢复"我看过哪些方案"） */
  async function loadProposals() {
    const id = contractId.value
    if (!id) return
    try {
      const res = await listOverviewProposals(id)
      if (id !== contractId.value) return
      proposals.value = Array.isArray(res?.data) ? res.data : []
      if (!activeProposalId.value && proposals.value.length) {
        // 只自动选中「最近一份」用于展示；不做任何写入
        activeProposalId.value = proposals.value[0].proposal_id
      }
    } catch {
      if (id !== contractId.value) return
      proposals.value = []
    }
  }

  /**
   * 标准条款模板（用于条款比对的"真实参考条款"栏）。
   * 后端 GET /clause-comparison 不返回标准条款正文，正文只存在于 templates.clauses；
   * **拿不到模板就退化为两栏**，绝不伪造参考条款。失败不阻断页面。
   */
  async function loadReferenceTemplates() {
    const id = contractId.value
    const type = contract.value?.contract_type
    if (!id || !type) return
    try {
      const res = await getTemplates({ contract_type: type })
      if (id !== contractId.value) return
      referenceTemplates.value = res?.data?.items || []
    } catch {
      if (id !== contractId.value) return
      referenceTemplates.value = []
    }
  }

  /**
   * 生成综合修改方案：POST /overview/plan（单次 LLM）。
   * **本调用不产生任何 ClauseRevision**；返回的是一份"待用户逐项处理"的方案。
   */
  async function generateProposal(instruction) {
    const text = String(instruction || '').trim()
    if (!text) {
      ElMessage.warning('请先输入你对整份合同的修改要求')
      return null
    }
    planLoading.value = true
    planError.value = ''
    try {
      const res = await createOverviewProposal(contractId.value, { instruction: text })
      const data = res?.data || null
      if (!data) {
        planError.value = '后端未返回方案内容'
        return null
      }
      activeProposalId.value = data.proposal_id
      // 新方案到来 → 上一轮的「已纳入」不再适用；新方案的 confirmed_ids 由后端返回
      await loadProposals()
      return data
    } catch (e) {
      planError.value = e?.response?.data?.detail || '生成综合方案失败'
      throw e
    } finally {
      planLoading.value = false
    }
  }

  const activeProposal = computed(
    () => proposals.value.find((p) => p.proposal_id === activeProposalId.value) || null,
  )

  /** 当前方案里**已被后端确认落库**的方案项 id 集合（来源：confirmed_ids，字符串比较） */
  const confirmedItemIds = computed(() => {
    const p = activeProposal.value
    const out = new Set()
    for (const id of p?.confirmed_ids || []) out.add(String(id))
    return out
  })

  /** 某一方案项是否已被纳入（= 后端已确认落库；刷新后仍为真） */
  function isItemConfirmed(item) {
    return !!item?.id && confirmedItemIds.value.has(String(item.id))
  }

  /**
   * 「纳入方案」—— **真实调用 `POST /overview/confirm` 落库**（本轮 P0-4）。
   *
   * 语义（**与后端实现严格一致，不再只是前端选中态**）：
   *   - 后端把该方案项转换成既有安全的 clause / add_clause `ClauseRevision`，
   *     并置 `adopted=True`（不调 LLM，只做确定性落库）；
   *   - 因此它会进入合同修改链路，后续下载修订版 DOCX 时可被包含；
   *   - 幂等：后端对已确认的项返回「请勿重复确认」，不会重复落库；
   *   - 失败**绝不显示成功**：单项失败原因原样展示，前端状态只在成功刷新后变化。
   *
   * 「取消纳入」只对本轮新生成、**尚未落库**的项开放（本轮前端没有取消落库的入口；
   * 后端也没有 un-confirm 端点，不新增第二套确认/取消机制）。
   *
   * @returns {Promise<boolean>} 是否最终处于「已纳入」状态
   */
  async function toggleIncludeItem(item) {
    if (!item?.id) return false
    const id = String(item.id)

    // 已落库 → 不做任何后端写入，仅提示（避免"取消纳入"给人"已撤销落库"的错觉）
    if (confirmedItemIds.value.has(id)) {
      ElMessage.info('该修改项已纳入并写入合同修改记录，无法在总控台取消；如不再需要，请到对应专项会话继续调整。')
      return true
    }

    if (!canIncludeProposalItem(item)) {
      ElMessage.warning(proposalBlockingText(item))
      return false
    }
    if (confirmingItemId.value) return false

    const proposalId = activeProposal.value?.proposal_id
    if (!proposalId) {
      ElMessage.error('没有可确认的方案（请先生成综合修改方案）')
      return false
    }

    confirmingItemId.value = id
    try {
      const res = await confirmOverviewProposal(contractId.value, {
        proposal_id: proposalId,
        items: [{ id }],
      })
      const applied = res?.data?.applied || []
      const failed = res?.data?.failed || []
      const ok = applied.some((a) => String(a.id) === id)

      // 先刷新后端真实状态（方案 confirmed_ids / 汇总 / 修订记录），再决定 UI 展示
      await loadProposals()
      await Promise.all([loadOverview(), loadRevisions()])

      if (ok) {
        const rev = applied.find((a) => String(a.id) === id)
        ElMessage({
          type: 'success',
          dangerouslyUseHTMLString: true,
          message: '已纳入方案：已写入合同修改记录'
            + (rev?.exportable === false ? '（该修改暂不可导出，需在专项会话确认位置）' : '')
            + '<br/>如需调整，可在中栏继续修改；下载修订版合同时会包含这条修改。',
        })
        return true
      }

      const reason = failed.find((f) => String(f.id) === id)?.reason
      ElMessage.error(reason || '纳入失败：后端未应用该修改项')
      return false
    } catch (e) {
      // 失败绝不能显示成成功：只读后端 detail
      ElMessage.error(e?.response?.data?.detail || '纳入方案失败，请稍后重试')
      return false
    } finally {
      confirmingItemId.value = null
    }
  }

  /** 当前方案中「已纳入」的项（已落库；来自后端 confirmed_ids） */
  const includedItems = computed(() => {
    const p = activeProposal.value
    if (!p) return []
    return (p.items || []).filter((it) => isItemConfirmed(it))
  })

  /**
   * 「去专项会话处理」：把方案项落到它对应的专项会话上。
   *
   * - 已有 target_session_key → 直接切到该会话；
   * - 新增条款类且无会话 → 进入现有「新增条款」内联工作区（位置必须用户确认，绝不默认追加末尾）；
   * - 普通条款修改且无会话 → 落到总体会话并在中栏提示：该修改项还需要先指定位置。
   */
  function gotoProposalItem(item) {
    if (!item) return
    if (item.target_session_key && sessions.value.some((s) => s.key === item.target_session_key)) {
      selectSession(item.target_session_key)
      return
    }
    if (item.operation === 'add_clause') {
      openAddWizard({
        targetKey: item.target_session_key || OVERVIEW_KEY,
        targetScope: item.target_session_key && item.target_session_key !== OVERVIEW_KEY ? 'clause' : 'overview',
        riskType: '',
        riskTypeLabel: proposalItemTitle(item),
        clauseNo: item.clause_no ? String(item.clause_no) : '',
        prefill: item.reason || item.revised_clause || '',
      })
      return
    }
    selectSession(OVERVIEW_KEY)
    ElMessage.info('该修改项还没有对应的专项会话：请先在「修改位置」中确认它对应合同里的哪一段。')
  }

  async function loadFeedback() {
    const id = contractId.value
    if (!id) return
    try {
      const res = await getFeedback(id)
      if (id !== contractId.value) return
      loadedFeedbacks.value = res?.data?.items || []
    } catch {
      loadedFeedbacks.value = []
    }
  }

  /**
   * 审核报告：GET /audit-report。
   * 后端在没有报告时返回 **404**（`contracts.py` 的 `get_audit_report`），
   * 因此这里把 404 归一为「审核尚未完成，暂无报告」，不当作加载失败。
   */
  async function loadReport() {
    const id = contractId.value
    if (!id) return
    reportLoading.value = true
    reportError.value = ''
    try {
      const res = await getAuditReport(id)
      if (id !== contractId.value) return
      report.value = res?.data || null
      reportFetched.value = true
    } catch (e) {
      if (id !== contractId.value) return
      report.value = null
      reportFetched.value = true
      if (e?.response?.status === 404) reportError.value = ''
      else reportError.value = e?.response?.data?.detail || '审核报告加载失败'
    } finally {
      reportLoading.value = false
    }
  }

  async function loadAll() {
    await loadContract()
    // 参考条款依赖 contract.contract_type，必须放在 loadContract 之后
    await Promise.all([
      loadAuditResult(), loadComparison(), loadRevisions(), loadFeedback(),
      loadOverview(), loadProposals(), loadReferenceTemplates(),
    ])
    applyReviseSource()
  }

  /**
   * 跨 Tab 入口：风险详情页的「去修改合同」按钮会先在 sessionStorage 写一条
   * `a24_revise_source`（见 AuditResultDetail.vue），期望「修改合同」Tab 打开后
   * 直接选中对应会话。这里补上消费端（只读、消费即清除，避免下次进入误跳）。
   *
   * 落不到具体会话时**不猜**：退回总体会话，由用户在左栏自行选择。
   */
  const REVISE_SOURCE_KEY = 'a24_revise_source'

  function applyReviseSource() {
    let raw = null
    try {
      raw = sessionStorage.getItem(REVISE_SOURCE_KEY)
      if (raw) sessionStorage.removeItem(REVISE_SOURCE_KEY)
    } catch {
      return // 隐私模式下读不到，不影响页面
    }
    if (!raw) return
    let payload = null
    try {
      payload = JSON.parse(raw)
    } catch {
      return
    }
    const riskId = payload?.risk_id
    if (riskId == null) return
    const key = String(riskId)
    setTab('revise')
    if (sessions.value.some((s) => s.key === key)) {
      selectSession(key)
    } else {
      ElMessage.info('当前审核结果里没有这条风险的修改会话，请在左侧选择要处理的条款')
    }
  }

  // ══════════════════════════════════════════════════════════════
  // 修改：replace / overview / add_clause（统一走 POST /revise）
  // ══════════════════════════════════════════════════════════════

  /**
   * 发送一轮修改。三种语义严格区分，但**共用同一个 /revise 与同一张 ClauseRevision**：
   *  - overview    : scope=overview, clause_key=__overview__, clause_text=整份合同正文
   *  - replace     : scope=clause,   clause_key=会话 key,   clause_text=逐字原文（优先锚点文本）
   *  - add_clause  : scope=clause|overview, operation=add_clause, clause_text='', position=用户确认的位置
   */
  async function sendRevise() {
    const s = activeSession.value
    if (!s) return
    const ui = uiFor(s.key)
    const instruction = String(ui.input || '').trim()
    if (!instruction) {
      ElMessage.warning('请输入修改指令')
      return
    }

    // ① 总体会话里自然表达「新增…条款」→ 进入新增条款工作区（由用户确认位置，绝不默认 append）
    if (s.key === OVERVIEW_KEY && !ui.refineMode && /(新增|增加|补充|添加).{0,12}?条款/.test(instruction)) {
      ui.input = ''
      openAddWizard({ targetKey: OVERVIEW_KEY, targetScope: 'overview', riskType: '', prefill: instruction })
      return
    }

    // ② 「继续修改新增条款」模式（任意会话）：沿用上一轮的插入位置，同位置覆盖
    if (ui.refineMode) {
      const pos = ui.confirmAnchor?.position || s.lastRev?.position
      if (!addPositionOk(pos)) {
        ui.refineMode = false
        ElMessage.warning('该会话没有可复用的插入位置，请用「新增条款」重新确认插入位置')
        return
      }
      return submitRevise(s, ui, {
        clause_text: '',
        instruction: withSessionContext(s, instruction),
        history: [],
        scope: s.lastRev?.scope === 'overview' ? 'overview' : 'clause',
        clause_key: s.key,
        clause_no: s.clauseNo ? String(s.clauseNo) : '',
        operation: 'add_clause',
        position: pos,
      }, instruction)
    }

    // ③ 缺失型会话（R09 / 比对缺失，或任何没有可靠原文的条款）：
    //    没有原文就无法建立 anchor，**不能**走 replace（那会产出一条永远导出不了的修订）。
    //    改为把用户已输入的要求带进「新增条款」工作区，由用户确认插入位置——既不禁止处理，
    //    也不替用户决定位置。
    if (sessionAction(s) === 'add_clause') {
      // ③-0 用户**已经确认过插入位置**（addWizard.confirmedPosition 成立、且工作区目标
      //      就是当前会话）时，中栏发送即视为最终发送 ⇒ 直接复用既有 submitAddClause()
      //      生成草案（不新增接口、不新增第二套生成逻辑）。
      //      边界：只认 confirmedPosition —— 推荐位置/selectedPosition 都不算最终位置。
      if (addPositionOk(addWizard.confirmedPosition) && addWizard.targetKey === s.key) {
        addWizard.requirement = instruction
        setAddRequirement(s.key, instruction)
        return submitAddClause()
      }
      const pos = s.lastRev?.position
      if (isAddOnly(s) && addPositionOk(pos)) {
        return submitRevise(s, ui, {
          clause_text: '',
          instruction: withSessionContext(s, instruction),
          history: [],
          scope: s.lastRev?.scope === 'overview' ? 'overview' : 'clause',
          clause_key: s.key,
          clause_no: s.clauseNo ? String(s.clauseNo) : '',
          operation: 'add_clause',
          position: pos,
        }, instruction)
      }
      // ③′ 还没有最终位置：只把需求带进工作区，**不生成**任何 ClauseRevision
      //     （没有确认位置的 add_clause 无法写入 DOCX，见 docx_reviser._insert_one）。
      ui.input = ''
      openAddWizard({
        targetKey: s.key,
        targetScope: 'clause',
        riskType: s.risk?.risk_type || s.cmp?.title || '',
        riskTypeLabel: s.risk
          ? `${s.risk.risk_type} · ${riskName(s.risk.risk_type)}`
          : (s.cmp?.title || ''),
        clauseNo: s.clauseNo ? String(s.clauseNo) : '',
        prefill: instruction,
      })
      ElMessage.warning('已收到新增条款需求，请先在右栏确认插入位置，再发送或点「生成推荐草案」')
      return
    }

    // ④ 总体修改（讨论/方案性质，不会被写入 DOCX）
    // ⑤ 普通替换修改（必须有可定位的逐字原文）
    let payload
    if (s.key === OVERVIEW_KEY) {
      if (!parsedText.value.trim()) {
        ElMessage.warning('合同正文为空，无法进行总体修改')
        return
      }
      payload = {
        clause_text: parsedText.value,
        instruction: withOverviewContext(instruction),
        history: buildHistory(s.revs),
        scope: 'overview',
        clause_key: OVERVIEW_KEY,
        clause_no: '',
      }
    } else {
      const clauseText = String(s.clauseText || '').trim()
      if (!clauseText) {
        ElMessage.warning('该会话没有可提交的条款原文，请先在右侧「修改位置」中确认位置')
        return
      }
      payload = {
        clause_text: clauseText,
        instruction: withSessionContext(s, instruction),
        history: buildHistory(s.revs),
        scope: 'clause',
        clause_key: s.key,
        clause_no: ui.confirmAnchor?.clause_no ? String(ui.confirmAnchor.clause_no) : s.clauseNo ? String(s.clauseNo) : '',
      }
    }

    return submitRevise(s, ui, payload, instruction)
  }

  /** 统一提交一轮 /revise（replace / overview / add_clause 共用；失败时不留下任何本地伪记录） */
  async function submitRevise(s, ui, payload, instruction) {
    ui.pending = { instruction, at: Date.now() }
    ui.input = ''
    try {
      const res = await reviseClause(contractId.value, payload)
      const data = res?.data || {}
      // 后端在修订失败时已改为 HTTP 502 + detail（见 contracts.py），因此走到这里视为成功；
      // 仍保留 error 兜底，避免任何「伪成功」气泡。
      if (data.error) {
        ui.pending = null
        ElMessage.error('条款修订失败：' + data.error)
        return
      }
      ui.pending = null
      // 以服务端为准刷新历史（revision_id 由后端生成，前端不自行拼装）
      await loadRevisions()
      ElMessage.success('已保存本轮修改')
    } catch (e) {
      ui.pending = null
      if (e?.code === 'ECONNABORTED' || /timeout/i.test(e?.message || '')) {
        ElMessage.error('条款修订超时：合同/条款较长或模型响应过慢，请稍后重试或缩小修改范围')
      }
      // 其余情况由 request.js 拦截器弹出后端 detail（含 502 修订失败原因）
    }
  }

  // ══════════════════════════════════════════════════════════════
  // 新增条款（Phase 6）—— 状态由右栏内联工作区消费
  // ══════════════════════════════════════════════════════════════

  const addWizard = reactive({
    step: 1,
    riskType: '',
    riskTypeLabel: '',
    requirement: '',
    templates: [],
    legalBasis: [],
    suggestedPosition: null,
    loading: false,
    submitting: false,
    loaded: false,
    targetKey: OVERVIEW_KEY,
    targetScope: 'overview',
    targetClauseNo: '',
    // 位置选择
    posMode: '',          // '' | 'suggest' | 'after' | 'before' | 'append' | 'locate'
    posAnchor: '',        // 选中/确认后的 anchor（中文或阿拉伯数字字符串）
    posHint: '',
    locateText: '',
    locateResult: null,
    locateLoading: false,
    // 「选中」与「确认」是两件事（通用定位能力的核心状态）：
    //   selectedPosition  = 用户当前**选中**的候选（推荐位置被点选后也落在这里）
    //   confirmedPosition = 用户点「确认位置」后才成立的**最终位置**，业务只认它
    // 合并二者会导致「确认位置」按钮无动作可做（选择时状态就已是最终态）。
    selectedPosition: null,
    confirmedPosition: null,
  })

  function resetAddWizard() {
    Object.assign(addWizard, {
      step: 1, riskType: '', riskTypeLabel: '', requirement: '',
      templates: [], legalBasis: [], suggestedPosition: null,
      loading: false, submitting: false, loaded: false,
      targetKey: OVERVIEW_KEY, targetScope: 'overview', targetClauseNo: '',
      posMode: '', posAnchor: '', posHint: '', locateText: '', locateResult: null,
      locateLoading: false, selectedPosition: null, confirmedPosition: null,
    })
  }

  /**
   * 准备一次「新增条款」并切到对应会话（**不开弹窗** —— 新增条款只有一个入口：
   * 「修改合同」Tab 右栏的内联工作区 InlinePositionPicker）。
   *
   * 调用方拿到的效果 = 填好 addWizard（需求 / 风险类型 / 目标会话）+ 切到该会话，
   * 右栏随即渲染插入位置选择器。位置**永远由用户显式确认**：`suggested_position`
   * 允许为空，此时绝不默认 append（见 chooseAddPosition / addPositionOk）。
   *
   * @param {boolean} startAtPositionStep 直接把 step 设为「插入位置」这一步（右栏内联工作区
   *        固定只呈现该步骤；需求与 AI 建议已经在会话里出现过）。
   */
  async function openAddWizard({ targetKey, targetScope, riskType, riskTypeLabel, prefill, clauseNo, startAtPositionStep = false }) {
    resetAddWizard()
    addWizard.riskType = riskType || ''
    addWizard.riskTypeLabel = riskTypeLabel || ''
    addWizard.requirement = prefill || ''
    addWizard.targetKey = targetKey || OVERVIEW_KEY
    addWizard.targetScope = targetScope || 'overview'
    addWizard.targetClauseNo = clauseNo || ''
    await reloadAddSuggestion()
    // 直接进位置步骤时，仍需用推荐位置作为**候选**填充 posMode；
    // 但 confirmedPosition 只在用户显式确认位置后才成立（见 chooseAddPosition）。
    addWizard.step = startAtPositionStep ? 3 : 2
    if (startAtPositionStep) {
      const sp = addWizard.suggestedPosition
      if (sp && (sp.append || sp.anchor)) chooseAddPosition('suggest')
    }
  }

  /** 按当前已填写的要求重新拉取建议（只读 /add-clause-suggestion，不重置其它状态） */
  async function reloadAddSuggestion() {
    addWizard.loading = true
    try {
      const res = await getAddClauseSuggestion(contractId.value, {
        risk_type: addWizard.riskType || 'R09',
        instruction: addWizard.requirement || '',
      })
      const d = res?.data || {}
      addWizard.templates = d.templates || []
      addWizard.legalBasis = d.legal_basis || []
      addWizard.suggestedPosition = d.suggested_position || null
      // R-1：后端返回**每一处**标题出现（含所在整行 target_text 与行号）。
      // 位置选择器优先用它，用户就能精确选到「同编号的第 2/3 处」，
      // 而不是只能在去重后的清单里选一个编号、再由后端猜一个位置。
      addWizard.headingOccurrences = Array.isArray(d.heading_occurrences) ? d.heading_occurrences : []
      addWizard.loaded = true
    } catch {
      // 检索失败不阻断：仍可手动指定位置并生成
      addWizard.loaded = true
    } finally {
      addWizard.loading = false
    }
  }

  /**
   * 位置候选：条款选择器。
   *
   * R-1：优先用后端返回的 `heading_occurrences`（**每一处**标题出现，含整行原文与行号），
   * 这样同一编号重复出现时用户能明确选到第几处。后端不可用/老响应时，
   * 退回去重后的 `headings`（此时不带精确定位，后端按编号找第一个 —— 与旧行为一致）。
   */
  const addAnchorHeadings = computed(() => {
    const occ = addWizard.headingOccurrences
    return Array.isArray(occ) && occ.length ? occ : headings.value
  })

  // ══════════════════════════════════════════════════════════════
  // 新增条款右栏工作区（消费上面同一份 addWizard 状态，不新建第二套系统）
  // ══════════════════════════════════════════════════════════════

  /**
   * 右栏插入位置选择器的绑定数据。
   *
   * 所有位置规则（推荐位置只是候选、确认位置才算选定、第X条之前的换算、描述定位候选点选）
   * 全部复用既有实现，右栏只是换了一个外壳渲染同一份 `addWizard` 状态。
   */
  const addPositionCtx = computed(() => ({
    suggest: addWizard.suggestedPosition || null,
    canUseSuggest: !!addWizard.suggestedPosition
      && !!(addWizard.suggestedPosition.append || addWizard.suggestedPosition.anchor),
    // R-1：用「每一处出现」的清单（含整行原文与行号），让用户能精确选到第几处
    headings: addAnchorHeadings.value,
  }))

  /**
   * 进入新增条款右栏工作区：确保位置选择所需的参考数据已就绪。
   * **只读调用** `/add-clause-suggestion`（范本 / 法条 / 推荐位置），不产生任何修改记录。
   */
  async function ensureAddWorkspace(session) {
    if (!session) return
    // 幂等：同一会话已经取过参考数据就不再重拉（按 targetKey + loaded 判断）。
    if (addWizard.targetKey === session.key && addWizard.loaded) return
    const ui = uiFor(session.key)
    // 会话已有的位置选择（用户之前选过 / 已保存记录上的位置）要在重置后恢复，
    // 否则右栏会显示"尚未选定"而「确认新增」却是可点的（状态不一致）
    const kept = ui.confirmAnchor?.position || session.lastRev?.position || null
    resetAddWizard()
    addWizard.step = 3                 // 右栏只呈现"插入位置"这一步
    addWizard.targetKey = session.key
    addWizard.targetScope = (session.lastRev?.scope || 'clause') === 'overview' ? 'overview' : 'clause'
    addWizard.targetClauseNo = session.clauseNo ? String(session.clauseNo) : ''
    addWizard.riskType = session.cmp?.title || session.risk?.risk_type || ''
    addWizard.riskTypeLabel = session.risk ? riskName(session.risk.risk_type) : (session.cmp?.title || '')
    await reloadAddSuggestion()
    // 需求描述按会话恢复：本次输入过 → 恢复本次；否则回落到该会话已保存记录的 instruction。
    // 这样"切走再切回"不会把用户输入的新增内容描述冲掉（与定位状态彼此独立）。
    const rememberedReq = addRequirementFor(session.key)
    addWizard.requirement = rememberedReq
      || String(session.lastRev?.instruction || '').trim()
      || addWizard.requirement
    if (kept) {
      // 已保存/已确认过的位置恢复为"选中 + 已确认"（历史确认态保持，不让用户重选一遍）
      addWizard.selectedPosition = kept
      addWizard.confirmedPosition = kept
      addWizard.posHint = positionText(kept)
      addWizard.posMode = kept.append ? 'append' : 'after'
      if (kept.anchor) addWizard.posAnchor = String(kept.anchor)
    }
  }

  /**
   * 「生成推荐草案」：用中栏描述的要求，按现有 `/revise(add_clause)` 生成条款正文。
   *
   * 诚实的语义（与后端事实一致）：
   *   - 正文由这次调用生成，**并立即写入一条正式修改记录**（ClauseRevision）；
   *   - 因此**必须已经由用户选定插入位置**才允许触发（不允许默认合同末尾）；
   *   - 这与既有实现的"确认位置 → 生成并保存"是同一个动作，不是新的生成入口。
   */
  async function generateAddDraft(session) {
    if (!session) return
    const ui = uiFor(session.key)
    // 需求来源优先级：中栏刚输入的内容 → 已填内容（由 openAddWorkspace / openAddWizard 预填）
    const req = String(ui.input || '').trim() || String(addWizard.requirement || '').trim()
    if (!req) {
      ElMessage.warning('请先在中栏描述你希望新增的内容，再生成推荐草案')
      return
    }
    addWizard.requirement = req
    if (!addPositionOk(addWizard.confirmedPosition)) {
      ElMessage.warning('请先选定插入位置（系统不会替你决定，也不会默认放到合同末尾）')
      return
    }
    await submitAddClause()
  }

  /**
   * 打开新增条款右栏工作区（点「确认新增」或「选择插入位置」时）。
   * 把中栏刚输入的要求带入，不额外要求用户在右栏重新填一遍表单。
   */
  async function openAddWorkspace(session) {
    if (!session) return
    const ui = uiFor(session.key)
    const typed = String(ui.input || '').trim()
    // 先把本次输入记入按会话存储，再进入工作区（避免 ensureAddWorkspace 重置时丢失）
    if (typed) setAddRequirement(session.key, typed)
    await ensureAddWorkspace(session)
    if (typed) {
      addWizard.requirement = typed
      setAddRequirement(session.key, typed)
    }
  }

  /**
   * 右栏空态用：当前会话是否已经具备"可以生成正文"的条件
   * （有需求描述 + 已确认插入位置）。
   */
  function canGenerateAddDraft(session) {
    if (!session) return false
    const ui = uiMap[session.key] || {}
    const hasReq = !!(String(ui.input || '').trim() || String(addWizard.requirement || '').trim())
    return hasReq && addPositionOk(addWizard.confirmedPosition)
  }

  // 「在 X 条之前」的换算规则见 workspaceLogic.beforeAnchorOf（后端只支持「某编号条款之后插入」）。
  // 这里只做「绑定当前合同 headings」的包装，供位置选择器直接调用。

  function chooseAddPosition(mode, payload) {
    addWizard.posMode = mode
    // R-1：位置必须能唯一定位。带上**用户实际选中那一处**的整行原文（target_text）
    // 与行号（paragraph_index）；后端据此精确定位，不再"按编号取第一个"。
    const loc = { target_text: payload?.target_text || '', paragraph_index: payload?.paragraph_index ?? null }
    const withLoc = (base) => {
      const out = { ...base }
      if (loc.target_text) out.target_text = loc.target_text
      if (loc.paragraph_index != null) out.paragraph_index = loc.paragraph_index
      return out
    }
    // 注意：这里只写「选中」(selectedPosition)。「最终位置」必须由用户点「确认位置」
    // 触发 confirmAddPosition() 才算成立 —— 推荐位置只是候选，不能自动成为最终位置。
    if (mode === 'suggest') {
      const sp = addWizard.suggestedPosition || {}
      if (sp.append) {
        addWizard.posAnchor = ''
        addWizard.posHint = sp.hint || '追加到合同末尾'
        addWizard.selectedPosition = { append: true, hint: addWizard.posHint }
      } else if (sp.anchor) {
        addWizard.posAnchor = String(sp.anchor)
        addWizard.posHint = sp.hint || `第${sp.anchor}条之后`
        // 推荐位置自己也带精确定位（后端 _suggest_position 已附带 target_text）
        addWizard.selectedPosition = withLoc({ anchor: String(sp.anchor), hint: addWizard.posHint })
      }
    } else if (mode === 'after') {
      addWizard.posAnchor = String(payload?.anchor || '')
      addWizard.posHint = `第${addWizard.posAnchor}条之后`
      addWizard.selectedPosition = withLoc({ anchor: addWizard.posAnchor, hint: addWizard.posHint })
    } else if (mode === 'before') {
      const prev = payload?.prevAnchor
      if (prev == null) {
        addWizard.selectedPosition = null
        return
      }
      addWizard.posAnchor = String(prev)
      addWizard.posHint = `第${prev}条之后（即第${payload.num}条之前）`
      addWizard.selectedPosition = withLoc({ anchor: String(prev), hint: addWizard.posHint })
    } else if (mode === 'append') {
      addWizard.posAnchor = ''
      addWizard.posHint = '追加到合同末尾'
      addWizard.selectedPosition = { append: true, hint: '追加到合同末尾' }
    }
  }

  /**
   * 「确认位置」：把用户**选中**的候选提交为**最终位置**（通用定位能力的唯一确认动作）。
   *
   * 语义边界（与产品红线一致）：
   *   - 最终位置 = 用户确认时选中的那个；**推荐位置不会覆盖用户的选择**；
   *   - 没有选中任何位置（含推荐位置为空且用户未选）时**拒绝确认**并提示，
   *     绝不替用户决定、也绝不默认追加到合同末尾；
   *   - 幂等：重复确认只是把当前选中项再提交一次。
   *
   * 注意：本函数**不涉及任何业务语义**（不知道 R09 / 条款比对 / 缺失条款），
   * 只负责"让用户选定一个合同位置"。
   */
  function confirmAddPosition() {
    const sel = addWizard.selectedPosition
    if (!addPositionOk(sel)) {
      ElMessage.warning('请先选择一种插入位置，再点「确认位置」（系统不会替你决定）')
      return false
    }
    addWizard.confirmedPosition = sel
    return true
  }

  /** 清空本次的位置选择（选中 + 已确认），供「重新选」与切换方式时复用 */
  function clearAddPosition() {
    addWizard.selectedPosition = null
    addWizard.confirmedPosition = null
    addWizard.posAnchor = ''
    addWizard.posHint = ''
  }

  // ── 新增条款「需求描述」的**按会话**存储（正文描述与定位状态必须解耦）──
  // 背景：`addWizard.requirement` 是全局单字段，而用户是在**每个会话**的中栏输入需求。
  // 若不做按会话存储，只靠全局字段会出现两种断裂：
  //   ① 用户在中栏输入后没有同步进 addWizard.requirement ⇒ 右栏仍显示"还没有条款正文"、
  //      「生成推荐草案」按钮点不动（即使位置已确认）；
  //   ② 从 A 会话切到 B 会话（right 栏 resetAddWizard）会把 A 的需求描述冲掉。
  // 这里只存"用户需求描述"（= 用户希望新增什么），与「推荐草案正文」（lastRev.revised_clause）
  // 、「最终合同内容」严格分开，不混用一个字段。
  const addRequirementBySession = reactive({})

  /** 记录某会话的新增条款需求描述（用 `hasOwnProperty` 区分"空串"与"没存过"） */
  function setAddRequirement(key, text) {
    const k = String(key || '')
    const t = String(text || '')
    if (t) addRequirementBySession[k] = t
    else if (!Object.prototype.hasOwnProperty.call(addRequirementBySession, k)) addRequirementBySession[k] = ''
  }

  /** 读取某会话的需求描述；没存过返回 '' */
  function addRequirementFor(key) {
    const k = String(key || '')
    return Object.prototype.hasOwnProperty.call(addRequirementBySession, k) ? addRequirementBySession[k] : ''
  }

  /** 方式三：自然语言描述 → 只读 locator → 候选 → 用户点选（绝不直接 revise） */
  async function runAddLocate() {
    const text = String(addWizard.locateText || '').trim()
    if (!text) {
      ElMessage.warning('请输入位置描述')
      return
    }
    addWizard.locateLoading = true
    try {
      const res = await locateClause(contractId.value, { text })
      addWizard.locateResult = res?.data || null
    } catch (e) {
      addWizard.locateResult = null
      ElMessage.error(e?.response?.data?.detail || '定位失败')
    } finally {
      addWizard.locateLoading = false
    }
  }

  function chooseAddLocateCandidate(c) {
    if (!c) return
    if (c.clause_no == null) {
      ElMessage.warning('该候选项未能解析出条款编号，无法作为插入锚点，请改选其它候选或使用条款选择器')
      return
    }
    addWizard.posAnchor = String(c.clause_no)
    addWizard.posHint = `第${c.clause_no}条之后（按描述定位）`
    addWizard.posMode = 'locate'
    // R-1：把候选自带的精确定位信息一并保留 —— 否则同编号的多个候选会退化成一个，
    // 用户点的是第二处、后端却插到第一处。
    const pos = { anchor: String(c.clause_no), hint: addWizard.posHint }
    if (c.target_text) pos.target_text = c.target_text
    if (c.paragraph_index != null) pos.paragraph_index = c.paragraph_index
    // 与其他方式一致：这里只是"选中"，仍需用户点「确认位置」才成为最终位置
    addWizard.selectedPosition = pos
  }

  async function submitAddClause() {
    const pos = addWizard.confirmedPosition
    if (!addPositionOk(pos)) {
      ElMessage.warning('请先确认插入位置（系统不会替你决定，也不会默认追加到末尾）')
      return
    }
    const requirement = String(addWizard.requirement || '').trim()
    if (!requirement) {
      ElMessage.warning('请填写新增条款的要求')
      return
    }
    addWizard.submitting = true
    try {
      const res = await reviseClause(contractId.value, {
        clause_text: '',
        instruction: requirement,
        history: [],
        scope: addWizard.targetScope,
        clause_key: addWizard.targetKey,
        clause_no: addWizard.targetClauseNo || '',
        operation: 'add_clause',
        position: pos,
      })
      const data = res?.data || {}
      if (data.error) {
        ElMessage.error('新增条款生成失败：' + data.error)
        return
      }
      await loadRevisions()
      selectSession(addWizard.targetKey)
      const ui = uiFor(addWizard.targetKey)
      ui.refineMode = true
      ui.confirmAnchor = { ...(ui.confirmAnchor || {}), position: pos }
      ElMessage.success('新增条款已保存（下载修订版 DOCX 时按确认位置插入）')
    } catch (e) {
      if (e?.code === 'ECONNABORTED' || /timeout/i.test(e?.message || '')) {
        ElMessage.error('新增条款生成超时，请稍后重试')
      }
    } finally {
      addWizard.submitting = false
    }
  }

  // ══════════════════════════════════════════════════════════════
  // DOCX 导出状态（Phase 8）
  // ══════════════════════════════════════════════════════════════

  /**
   * 将被写入 DOCX 的修订（取数口径由 workspaceLogic.isExportableRevision 定义并单测，
   * 与后端 download_revised_docx 的 `scope='clause' OR (scope='overview' AND operation='add_clause')`
   * 完全一致）。**overview + replace 不会被写入 DOCX**。
   */
  const exportableRevisions = computed(() => {
    const out = []
    for (const s of sessions.value) {
      for (const r of s.revs) {
        if (isExportableRevision(r)) out.push({ session: s, rev: r })
      }
    }
    return out
  })

  const overviewReplaceCount = computed(
    () => sessions.value.reduce((n, s) => n + s.revs.filter((r) => r.scope === 'overview' && r.operation !== 'add_clause').length, 0),
  )

  // ── 总控台：修改点汇总 / 已确认修改统计 / 参考条款 ────────────────

  /** 后端只读汇总里按 session key 索引的权威数据（原文/最新结果/定位/导出） */
  const overviewByKey = computed(() => {
    const m = new Map()
    for (const s of overview.value?.sessions || []) m.set(String(s.key), s)
    return m
  })

  /**
   * 「已确认修改 N」（V2.2 2.2 的唯一口径）：
   * 只统计**专项会话中用户最终确认、且原文位置可靠确定**的具体修改。
   * 判据 = 后端 /overview 的 sessions[].export.exportable（与 DOCX 取数口径镜像）。
   * 不含 AI 生成次数、方案数量、revision_count，也**不含「已纳入方案」**。
   */
  const confirmedCount = computed(() => countConfirmedSessions(overview.value?.sessions))

  function isSessionExportable(key) {
    return sessionExportable(overview.value?.sessions, key)
  }

  /**
   * 总控台「当前修改点汇总」：把三类真实来源合并成一张清单。
   *   ① 每一条待处理风险（GET /audit-result，即使还没有任何修订）
   *   ② 每一个条款比对问题（GET /clause-comparison，即使还没有任何修订）
   *   ③ 每个已有修订记录的会话（GET /revisions，含新增条款与会话）
   * 状态由 modificationStateFor 统一判定；已纳入方案来自前端会话态。
   */
  const modificationPoints = computed(() => {
    const out = []
    const seen = new Set()
    const push = (s) => {
      if (seen.has(s.key)) return
      seen.add(s.key)
      const ov = overviewByKey.value.get(s.key)
      const exportable = isSessionExportable(s.key)
      const state = modificationStateFor({
        included: sessionIncluded(s.key),
        hasRevisions: s.rounds > 0,
        exportable,
      })
      out.push({
        key: s.key,
        group: s.group,
        title: s.title,
        subtitle: s.subtitle,
        state,
        exportable,
        originalText: ov?.original_text || s.clauseText || '',
        revisedClause: ov?.revised_clause || s.lastRev?.revised_clause || '',
        clauseNo: ov?.clause_no || (s.clauseNo != null ? String(s.clauseNo) : ''),
      })
    }
    for (const s of sessions.value) if (s.key !== OVERVIEW_KEY) push(s)
    return out
  })

  /** 当前合同里处于各状态的修改点数量 */
  const modificationCounts = computed(() => {
    const c = { pending: 0, included: 0, processing: 0, confirmed: 0 }
    for (const p of modificationPoints.value) c[p.state.key] = (c[p.state.key] || 0) + 1
    return c
  })

  /** 当前会话在总控台里的状态（用于右栏状态点与文案） */
  function sessionModificationState(s) {
    if (!s || s.key === OVERVIEW_KEY) return null
    return modificationStateFor({
      included: sessionIncluded(s.key),
      hasRevisions: s.rounds > 0,
      exportable: isSessionExportable(s.key),
    })
  }

  /**
   * 某个 session key 是否已被「纳入方案」。
   *
   * 判据 = 当前方案里存在**已被后端确认落库**（confirmed_ids 命中）且指向该会话的项。
   * 不再是前端会话态，因此刷新页面后状态仍然正确。
   */
  function sessionIncluded(key) {
    const p = activeProposal.value
    if (!p) return false
    const confirmed = confirmedItemIds.value
    if (!confirmed.size) return false
    const k = String(key || '')
    for (const it of p.items || []) {
      if (!it?.id || !confirmed.has(String(it.id))) continue
      if (it.target_session_key && String(it.target_session_key) === k) return true
    }
    return false
  }

  /** 该比对项的真实参考条款正文（无模板返回 '' → 比对区退化为两栏） */
  function referenceTextFor(row) {
    return referenceClauseText(referenceTemplates.value, row?.title)
  }

  /** 该比对项应展示两栏还是三栏（依据真实数据，不硬凑） */
  function comparisonLayoutFor(row) {
    return comparisonLayout(row, referenceTextFor(row))
  }

  /** 当前会话的定位候选（多候选必须用户点选，禁止自动选第一个） */
  const activeLocateCandidates = computed(() => {
    const s = activeSession.value
    if (!s) return []
    return locateCandidates(uiMap[s.key]?.locate)
  })

  /** 当前会话是否已有用户确认的修改位置（前端 UI 态；服务端锚点在提交时建立） */
  function hasConfirmedLocation(s) {
    if (!s) return false
    if (s.key === OVERVIEW_KEY) return false
    const ui = uiMap[s.key] || {}
    return !!(ui.confirmAnchor?.original_text || s.risk?.clause_position?.original_text)
  }

  // ══════════════════════════════════════════════════════════════
  // 采用态（adopted）：来自 GET /revisions 的 adopted 字段
  // ══════════════════════════════════════════════════════════════

  /** 该轮修订是否已被用户确认采用 */
  function isRevisionAdopted(rev) {
    return !!(rev && rev.adopted === true)
  }

  /**
   * 会话当前被采用的修订（同一 clause_key 下至多一条）。
   * 刷新后由 /revisions 的 adopted 字段恢复，不依赖任何前端内存态。
   */
  function sessionAdoptedRev(s) {
    if (!s) return null
    return (s.revs || []).find((r) => r.adopted === true) || null
  }

  /**
   * 「确认采用此版」（风险 / 比对）与「确认新增」（新增条款）共用的确认动作。
   *
   * 语义（V2.2 修正）：这是**确认当前已有版本**，不是"再生成一轮"。
   *   - 调 POST /{id}/revisions/{rid}/adopt
   *   - **不调 LLM、不新建 ClauseRevision、不改变轮次、不改变 lastRev**
   *   - 只把该轮置为采用态（后端同时取消同会话旧采用版本）
   *   - DOCX 导出优先使用被采用的这一版
   *
   * 前置条件（缺一不可，缺则明确提示、不发请求）：
   *   - 替换修改：该轮必须已建立可靠原文锚点（否则后端 400）
   *   - 新增条款：该轮必须有用户确认过的合法插入位置
   *
   * @param {Object} session  当前会话
   * @param {Object} rev      要采用的那一轮（默认取最新一轮）
   */
  async function confirmAdopt(session, rev = null) {
    const s = session || activeSession.value
    if (!s) return
    const target = rev || s.lastRev
    if (!target?.id) {
      ElMessage.warning('当前会话还没有可采用的修改版本，请先在中栏告诉 AI 你的修改要求')
      return
    }
    const ui = uiFor(s.key)

    if ((target.operation || 'replace') === 'add_clause') {
      const pos = ui.confirmAnchor?.position || target.position
      if (!addPositionOk(pos)) {
        ElMessage.warning('请先确认插入位置（系统不会替你决定，也不会默认追加到合同末尾）')
        return
      }
    } else if (!hasConfirmedLocation(s) && !target.original_clause_text) {
      ElMessage.warning('这一版还没有可靠的原文定位，无法采用：请先在「修改位置」中确认改的是合同的哪一段')
      return
    }

    adopting.value = true
    try {
      await adoptRevision(contractId.value, target.id, s.key)
      // 以服务端为准刷新（adopted 字段与「已确认修改」计数都来自后端）
      await Promise.all([loadRevisions(), loadOverview()])
      ElMessage.success('已采用这一版（下载修改后的合同时会使用它）')
    } catch (e) {
      const detail = e?.response?.data?.detail
      if (detail) ElMessage.error(detail)
    } finally {
      adopting.value = false
    }
  }

  /**
   * 「确认新增」：复用既有新增条款链路（POST /revise operation=add_clause）。
   * 位置必须已由用户显式确认（addPositionOk），否则拒绝。
   */
  async function confirmNewClause(s) {
    const ui = uiFor(s.key)
    const pos = ui.confirmAnchor?.position || s.lastRev?.position
    if (!addPositionOk(pos)) {
      ElMessage.warning('请先确认插入位置（系统不会替你决定，也不会默认追加到合同末尾）')
      return
    }
    const last = s.lastRev
    if (!last?.revised_clause) {
      ElMessage.warning('还没有可确认的新增条款内容，请先用「新增条款」起草')
      return
    }
    const instruction = displayInstruction(last.instruction) || '确认新增条款'
    return submitRevise(s, ui, {
      clause_text: '',
      instruction: withSessionContext(s, `【确认新增】${instruction}`),
      history: [],
      scope: (last.scope || 'clause') === 'overview' ? 'overview' : 'clause',
      clause_key: s.key,
      clause_no: ui.confirmAnchor?.clause_no ? String(ui.confirmAnchor.clause_no) : s.clauseNo ? String(s.clauseNo) : '',
      operation: 'add_clause',
      position: pos,
    }, `确认新增：${instruction}`)
  }

  /** 前端预判：可能无法定位的替换修订（**只是预判，最终以后端 400 为准**） */
  const docxBlockers = computed(() => {
    const blocked = []
    for (const s of sessions.value) {
      if (s.key === OVERVIEW_KEY) continue
      const replaceRevs = s.revs.filter((r) => (r.operation || 'replace') !== 'add_clause')
      if (!replaceRevs.length) continue
      const ui = uiMap[s.key] || {}
      const ok =
        !!ui.confirmAnchor?.original_text ||
        !!ui.locate?.found ||
        !!s.risk?.clause_position?.original_text ||
        previewLocatable(parsedText.value, s.clauseText)
      if (!ok) blocked.push(s)
    }
    // 新增条款位置不可解析（理论上后端必存 position，此处兜底）
    for (const s of sessions.value) {
      for (const r of s.revs) {
        if (r.operation === 'add_clause' && !addPositionOk(r.position)) blocked.push(s)
      }
    }
    return [...new Set(blocked)]
  })

  /**
   * 导出状态机（文案与判据集中在 workspaceLogic.docxStateFor，可单测）。
   *
   * 格式判断用后端返回的 **`stored_path`**（真实上传文件路径），不用 `file_name`：
   * `file_name` 是用户可编辑的合同显示名，真实数据里多数没有扩展名，
   * 用它判断会把真 DOCX 判成非 Word（后端 /revised-docx 用的也是 stored_path）。
   */
  const docxState = computed(() => {
    if (!contract.value) return { key: 'loading', text: '加载中…', detail: '' }
    return docxStateFor({
      storedPath: contract.value.stored_path || '',
      fileName: contract.value.file_name || '',
      exportableCount: exportableRevisions.value.length,
      blockerCount: docxBlockers.value.length,
    })
  })

  async function downloadDocx() {
    docx.loading = true
    docx.error = ''
    try {
      const blob = await downloadRevisedDocx(contractId.value)
      const base = (contract.value?.file_name || '合同').replace(/\.[^.]+$/, '')
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${base}_修订版.docx`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
      ElMessage.success('已按当前全部已确认修改生成修订版 DOCX')
      return true
    } catch (e) {
      // responseType=blob 时错误体也是 Blob，需要手动解析后端 detail（原样透传，不模糊化）
      const data = e?.response?.data
      let detail = ''
      if (data && typeof data.text === 'function') {
        try {
          detail = JSON.parse(await data.text())?.detail || ''
        } catch { /* 非 JSON，忽略 */ }
      }
      docx.error = detail || e?.response?.data?.detail || e?.message || '生成修订版 DOCX 失败'
      return false
    } finally {
      docx.loading = false
    }
  }

  // ══════════════════════════════════════════════════════════════
  // 审核 / 复核 / 验收 + 轮询（可被合同切换中断）
  // ══════════════════════════════════════════════════════════════

  const canReview = computed(() => ['reviewer', 'admin'].includes(localStorage.getItem('role') || ''))
  const canApprove = computed(() => ['approver', 'admin'].includes(localStorage.getItem('role') || ''))
  const canUpload = computed(() => ['uploader', 'admin'].includes(localStorage.getItem('role') || ''))

  async function triggerAuditAndPoll() {
    auditing.value = true
    const myToken = ++pollToken
    try {
      await triggerAudit(contractId.value)
      if (contract.value) contract.value.status = 'auditing'
      ElMessage.info('审核已提交，后台处理中…')
      const deadline = Date.now() + 180000
      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 2000))
        if (myToken !== pollToken) return // 合同已切换，停止轮询
        await loadContract()
        if (myToken !== pollToken) return
        const st = contract.value?.status
        if (st === 'completed') {
          await Promise.all([loadAuditResult(), loadComparison(), loadReport(), loadFeedback()])
          ElMessage.success('审核完成')
          return
        }
        if (st === 'parsed') {
          ElMessage.error('审核失败，合同已重置为已解析状态')
          return
        }
      }
      ElMessage.warning('审核耗时较长，请稍后在列表中查看结果')
    } catch (e) {
      ElMessage.error(e?.response?.data?.detail || '审核触发失败')
    } finally {
      if (myToken === pollToken) auditing.value = false
    }
  }

  async function doReview(action) {
    reviewing.value = true
    try {
      await reviewContract(contractId.value, action)
      ElMessage.success(action === 'approve' ? '复核通过，待验收' : '已驳回，需重新审核')
      await Promise.all([loadContract(), loadAuditResult()])
    } catch { /* 拦截器已提示 */ } finally {
      reviewing.value = false
    }
  }

  async function doApprove() {
    reviewing.value = true
    try {
      await approveContract(contractId.value)
      ElMessage.success('验收通过')
      await loadContract()
    } catch { /* 拦截器已提示 */ } finally {
      reviewing.value = false
    }
  }

  // ══════════════════════════════════════════════════════════════
  // 反馈
  // ══════════════════════════════════════════════════════════════

  async function onSubmitFeedback(payload) {
    try {
      await submitFeedback(payload)
      ElMessage.success('反馈已保存')
      await Promise.all([loadFeedback(), loadAuditResult()])
    } catch (e) {
      ElMessage.error('反馈提交失败：' + (e?.response?.data?.detail || e?.message || ''))
    }
  }

  async function onUndoFeedback(payload) {
    try {
      if (payload?.feedback_id) {
        await deleteFeedback(payload.feedback_id)
        await Promise.all([loadFeedback(), loadAuditResult()])
      }
      ElMessage.info('已撤销')
    } catch (e) {
      ElMessage.error('撤销失败：' + (e?.response?.data?.detail || e?.message || ''))
    }
  }

  // ══════════════════════════════════════════════════════════════
  // 原文面板状态（搜索 / 导航 / 高亮开关）
  // ══════════════════════════════════════════════════════════════

  const original = reactive({
    query: '',
    activeMatch: 0,
    showRiskHighlight: true,
  })

  const searchMatches = computed(() => {
    const q = String(original.query || '').trim()
    const text = parsedText.value
    if (!q || !text) return []
    const out = []
    let s = 0
    for (;;) {
      const i = text.indexOf(q, s)
      if (i < 0) break
      out.push({ start: i, end: i + q.length })
      s = i + q.length
      if (out.length > 500) break
    }
    return out
  })

  /**
   * 可靠的风险原文区间：仅当审核阶段建立了 anchor 且 start/end 在当前 parsed_text 上
   * 能逐字对上时才使用（否则不画位置 —— 诚实表达「未定位」）。
   */
  const riskRanges = computed(() => {
    const text = parsedText.value
    if (!text) return []
    const out = []
    for (const r of riskItems.value) {
      const p = r.clause_position
      if (!p) continue
      const anchor = p.original_text
      const start = p.start
      const end = p.end
      if (!anchor || typeof start !== 'number' || typeof end !== 'number') continue
      if (start < 0 || end > text.length || end <= start) continue
      // 逐字校验：锚点必须与当前正文一致，否则不标注（parsed_text 与审核时不一致）
      if (text.slice(start, end).trim() !== String(anchor).trim()) continue
      out.push({ start, end, risk: r })
    }
    return out
  })

  // ══════════════════════════════════════════════════════════════
  // 合同切换：彻底重置（Phase 9）
  // ══════════════════════════════════════════════════════════════

  function resetAll() {
    seq.value++
    pollToken++
    activeTab.value = 'text'
    loading.value = true
    detailError.value = ''
    contract.value = null
    riskItems.value = []
    riskTotal.value = 0
    hasCurrentResult.value = true
    riskError.value = ''
    riskLoading.value = false
    clauseComparison.value = null
    comparisonError.value = ''
    comparing.value = false
    revisions.value = []
    revisionsError.value = ''
    revisionsLoading.value = false
    // 总控台：只读汇总 + 方案列表；「已纳入方案」来自后端 confirmed_ids，切合同必须整体清空
    overview.value = null
    overviewError.value = ''
    overviewLoading.value = false
    proposals.value = []
    activeProposalId.value = null
    confirmingItemId.value = null
    planError.value = ''
    planLoading.value = false
    referenceTemplates.value = []
    loadedFeedbacks.value = []
    report.value = null
    reportError.value = ''
    reportLoading.value = false
    reportFetched.value = false
    auditing.value = false
    reviewing.value = false
    docx.loading = false
    docx.error = ''
    activeKey.value = OVERVIEW_KEY
    focusRiskId.value = null
    for (const k of Object.keys(uiMap)) delete uiMap[k]
    resetAddWizard()
    original.query = ''
    original.activeMatch = 0
    original.showRiskHighlight = true
  }

  // ── 报告按需加载：切到「审核报告」Tab 时才请求（一次），不在进入页面时增加负担 ──
  watch(activeTab, (tab) => {
    if (tab !== 'report') return
    if (reportFetched.value || reportLoading.value) return
    loadReport()
  })

  watch(
    contractId,
    (n, o) => {
      if (!n || n === o) return
      resetAll()
      loadAll()
    },
  )

  onMounted(() => {
    if (contractId.value) loadAll()
  })

  onUnmounted(() => {
    pollToken++
    seq.value++
  })

  // 返回值用 reactive 包裹：让消费方（页面壳与各子组件）可以直接 `ws.xxx` 取值，
  // 不需要在模板里写 `.value`（reactive 会自动解包顶层 ref/computed）。
  // 注意：composable 内部仍然使用原始 ref，闭包不受影响。
  return reactive({
    // 标识
    contractId,
    // 一级 Tab
    activeTab, setTab,
    // 基础
    loading, detailError, contract,
    // 风险
    riskItems, riskTotal, hasCurrentResult, riskLoading, riskError,
    // 比对
    clauseComparison, comparisonIssues, comparing, comparisonError,
    // 历史
    revisions, revisionsLoading, revisionsError,
    // 审核报告
    report, reportLoading, reportError, reportFetched, loadReport,
    // 会话
    sessions, sessionsByGroup, activeKey, activeSession, activeRounds, focusRiskId,
    selectSession, uiFor,
    // 语义判定
    sessionAction, isAddOnly, isMissingTypeRisk, locateBadge, needsLocate,
    // 定位
    runLocate, confirmLocate, clearLocate, startRelocate,
    locationStateForSession, hasReliableLocationNow, LOCATION_STATES,
    // 修改
    sendRevise, confirmAdopt, confirmNewClause, adopting,
    // 采用态查询
    sessionAdoptedRev, isRevisionAdopted,
    // 总体修改（总控台）
    overview, overviewLoading, overviewError, loadOverview,
    proposals, loadProposals, activeProposal, activeProposalId,
    generateProposal, planLoading, planError,
    confirmedItemIds, isItemConfirmed, confirmingItemId, includedItems, toggleIncludeItem,
    gotoProposalItem, sessionIncluded,
    modificationPoints, modificationCounts, confirmedCount, isSessionExportable,
    sessionModificationState, referenceTextFor, comparisonLayoutFor, activeLocateCandidates,
    hasConfirmedLocation,
    proposalItemKind, proposalItemTitle, canIncludeProposalItem, proposalBlockingText, positionText,
    // 新增条款（唯一入口 = 右栏内联工作区；addWizard 是两边共用的同一份状态）
    addWizard, openAddWizard, reloadAddSuggestion, chooseAddPosition,
    // 通用定位能力：选中 → 确认 → （可选）重新选；业务侧只认 confirmedPosition
    confirmAddPosition, clearAddPosition,
    // 新增条款「需求描述」的按会话读写（与定位状态解耦，避免互相覆盖）
    addRequirementBySession, setAddRequirement, addRequirementFor,
    beforeAnchorOf: (num) => beforeAnchorOf(headings.value, num),
    runAddLocate, chooseAddLocateCandidate, submitAddClause, resetAddWizard,
    addAnchorHeadings,
    addPositionCtx, ensureAddWorkspace, openAddWorkspace, generateAddDraft, canGenerateAddDraft,
    // 上下文
    activeContextItems,
    // DOCX
    docx, docxState, docxBlockers, exportableRevisions, overviewReplaceCount, downloadDocx,
    // 工作流
    auditing, reviewing, canReview, canApprove, canUpload,
    triggerAuditAndPoll, doReview, doApprove,
    // 反馈
    loadedFeedbacks, onSubmitFeedback, onUndoFeedback,
    // 原文
    parsedText, headings, original, searchMatches, riskRanges,
    // 工具
    loadAll, loadContract, loadAuditResult, loadComparison, loadRevisions, loadFeedback,
    resetAll,
    cnNo, cnToInt, parseHeadings, previewLocate, previewLocatable, clipText,
  })
}
