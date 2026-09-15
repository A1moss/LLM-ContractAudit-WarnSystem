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
 * 5. 「用户确认定位」「新增条款向导进度」等**只是 UI 状态**，后端没有对应持久化字段
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
} from '../api/contract.js'
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
   * 会话定位状态徽标。
   * 优先级：新增条款 > 用户已确认 > 后端定位成功 > 自动定位（审核阶段 anchor）
   *        > 后端判定未定位 > 未定位（默认）
   * 说明：`backend` 状态来自 POST /locate-clause 的真实结论；
   *      `auto` 来自审核阶段 AuditRecord.clause_position.original_text（后端真实锚点）；
   *      两者都不等于「本轮 revision 一定可导出」，导出判定始终以后端 400/200 为准。
   */
  function locateBadge(s) {
    if (!s) return { type: 'info', text: '—' }
    const ui = uiMap[s.key] || {}
    if (s.key === OVERVIEW_KEY) {
      return { type: 'info', text: '讨论 / 方案性质，不直接写回 DOCX' }
    }
    if (sessionAction(s) === 'add_clause') {
      return { type: 'success', text: '新增条款（按用户确认的插入位置写入）' }
    }
    if (ui.confirmAnchor) {
      const no = ui.confirmAnchor.clause_no
      return { type: 'primary', text: no ? `已确认位置：第${cnNo(no)}条` : '已确认位置（用户指定）' }
    }
    if (ui.locate?.found) {
      const no = ui.locate.clause_no
      return { type: 'success', text: no ? `后端定位成功：第${cnNo(no)}条` : '后端定位成功' }
    }
    if (ui.locate && !ui.locate.found) {
      return { type: 'warning', text: ui.locate.reason || '后端未能唯一定位' }
    }
    if (s.risk?.clause_position?.original_text) {
      const no = s.risk.clause_position.clause_no
      return { type: 'success', text: no ? `已自动定位：第${cnNo(no)}条` : '已自动定位（审核阶段锚点）' }
    }
    if (s.prediction?.status === 'unique' || s.prediction?.status === 'disambiguated') {
      return { type: 'success', text: '预检可定位（待后端确认）' }
    }
    if (s.prediction?.status === 'ambiguous') {
      return { type: 'warning', text: '原文多处命中，无法自动消歧' }
    }
    return { type: 'warning', text: '暂未定位' }
  }

  /** 是否需要用户指定位置（未定位 / 定位失败） */
  function needsLocate(s) {
    if (!s) return false
    if (s.key === OVERVIEW_KEY) return false
    if (sessionAction(s) === 'add_clause') return false
    const ui = uiMap[s.key] || {}
    if (ui.confirmAnchor || ui.locate?.found) return false
    if (s.risk?.clause_position?.original_text) return false
    return !s.locatable
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
    ElMessage.success('已确认修改位置（下次提交修改时由后端建立原文锚点）')
  }

  function clearLocate(s) {
    if (!s) return
    const ui = uiFor(s.key)
    ui.confirmAnchor = null
    ui.locate = null
    ui.locateError = ''
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
    await Promise.all([loadAuditResult(), loadComparison(), loadRevisions(), loadFeedback()])
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

    // ① 总体会话里自然表达「新增…条款」→ 进入新增条款向导（由用户确认位置，绝不默认 append）
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
        ElMessage.warning('该会话没有可复用的插入位置，请用「新增条款」向导重新确认位置')
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
    //    改为把用户已输入的要求带进「新增条款」向导，由用户确认插入位置——既不禁止处理，
    //    也不替用户决定位置。
    if (sessionAction(s) === 'add_clause') {
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
  // 新增条款向导（Phase 6）
  // ══════════════════════════════════════════════════════════════

  const addWizard = reactive({
    visible: false,
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
    confirmedPosition: null,
  })

  function resetAddWizard() {
    Object.assign(addWizard, {
      visible: false, step: 1, riskType: '', riskTypeLabel: '', requirement: '',
      templates: [], legalBasis: [], suggestedPosition: null,
      loading: false, submitting: false, loaded: false,
      targetKey: OVERVIEW_KEY, targetScope: 'overview', targetClauseNo: '',
      posMode: '', posAnchor: '', posHint: '', locateText: '', locateResult: null,
      locateLoading: false, confirmedPosition: null,
    })
  }

  /**
   * 打开新增条款向导。
   * 注意：`suggested_position` 允许为空，此时**绝不默认 append**，必须由用户显式选择。
   */
  async function openAddWizard({ targetKey, targetScope, riskType, riskTypeLabel, prefill, clauseNo }) {
    resetAddWizard()
    addWizard.visible = true
    addWizard.riskType = riskType || ''
    addWizard.riskTypeLabel = riskTypeLabel || ''
    addWizard.requirement = prefill || ''
    addWizard.targetKey = targetKey || OVERVIEW_KEY
    addWizard.targetScope = targetScope || 'overview'
    addWizard.targetClauseNo = clauseNo || ''
    await reloadAddSuggestion()
    addWizard.step = 2
  }

  function gotoAddStep3() {
    if (!String(addWizard.requirement || '').trim()) return
    addWizard.step = 3
  }

  /** 按当前已填写的要求重新拉取建议（向导内「生成 AI 建议 / 重新生成」用，不重置其它状态） */
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
      addWizard.loaded = true
    } catch {
      // 检索失败不阻断：仍可手动指定位置并生成
      addWizard.loaded = true
    } finally {
      addWizard.loading = false
    }
  }

  /** 位置候选：条款选择器（前端从 parsed_text 派生 headings，与后端 _parse_headings 同规则） */
  const addAnchorHeadings = computed(() => headings.value)

  // 「在 X 条之前」的换算规则见 workspaceLogic.beforeAnchorOf（后端只支持「某编号条款之后插入」）。
  // 这里只做「绑定当前合同 headings」的包装，供向导直接调用。

  function chooseAddPosition(mode, payload) {
    addWizard.posMode = mode
    if (mode === 'suggest') {
      const sp = addWizard.suggestedPosition || {}
      if (sp.append) {
        addWizard.posAnchor = ''
        addWizard.posHint = sp.hint || '追加到合同末尾'
        addWizard.confirmedPosition = { append: true, hint: addWizard.posHint }
      } else if (sp.anchor) {
        addWizard.posAnchor = String(sp.anchor)
        addWizard.posHint = sp.hint || `第${sp.anchor}条之后`
        addWizard.confirmedPosition = { anchor: String(sp.anchor), hint: addWizard.posHint }
      }
    } else if (mode === 'after') {
      addWizard.posAnchor = String(payload?.anchor || '')
      addWizard.posHint = `第${addWizard.posAnchor}条之后`
      addWizard.confirmedPosition = { anchor: addWizard.posAnchor, hint: addWizard.posHint }
    } else if (mode === 'before') {
      const prev = payload?.prevAnchor
      if (prev == null) {
        addWizard.confirmedPosition = null
        return
      }
      addWizard.posAnchor = String(prev)
      addWizard.posHint = `第${prev}条之后（即第${payload.num}条之前）`
      addWizard.confirmedPosition = { anchor: String(prev), hint: addWizard.posHint }
    } else if (mode === 'append') {
      addWizard.posAnchor = ''
      addWizard.posHint = '追加到合同末尾'
      addWizard.confirmedPosition = { append: true, hint: '追加到合同末尾' }
    }
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
    addWizard.confirmedPosition = { anchor: String(c.clause_no), hint: addWizard.posHint }
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
      addWizard.visible = false
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

  /** 导出状态机（文案与判据集中在 workspaceLogic.docxStateFor，可单测） */
  const docxState = computed(() => {
    if (!contract.value) return { key: 'loading', text: '加载中…', detail: '' }
    return docxStateFor({
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
    runLocate, confirmLocate, clearLocate,
    // 修改
    sendRevise,
    // 新增条款向导
    addWizard, openAddWizard, gotoAddStep3, reloadAddSuggestion, chooseAddPosition,
    beforeAnchorOf: (num) => beforeAnchorOf(headings.value, num),
    runAddLocate, chooseAddLocateCandidate, submitAddClause, resetAddWizard,
    addAnchorHeadings,
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
