/**
 * workspaceLogic — 合同详情工作台的**纯业务规则**（无框架依赖，可单测）
 *
 * 这里只放「不依赖 Vue / 不依赖 axios」的确定性规则：
 * 会话分组优先级、会话动作判定、DOCX 可导出口径、定位预检、标题解析、数字转换等。
 * `useContractWorkspace.js` 只做框架粘合，规则一律从这里引入 —— 保证规则可被
 * `workspaceLogic.test.mjs` 直接验证，不会因为写在组件里而无法测试。
 *
 * 所有规则都以**后端真实实现**为准，不虚构字段：
 * - 会话分组：只用 scope / operation / clause_key（后端无 source 字段）
 * - DOCX 口径：与 `backend/api/contracts.py::download_revised_docx` 的取数完全一致
 * - 定位预检：镜像后端 `_pick_best_hit`；权威结论仍来自 `/locate-clause` 与 `/revised-docx`
 */

// ════════════════════════════════════════════════════════════════
// 会话 key 约定（**不得更改**：真实数据里已按此固化）
// ════════════════════════════════════════════════════════════════
export const OVERVIEW_KEY = '__overview__'
export const CMP_PREFIX = '__cmp__'

/** 会话分组（左栏展示顺序；文案与《修改合同 Tab·完整交互设计 V2.2》2.2 一致） */
export const SESSION_GROUPS = [
  { key: 'overview', label: '总体修改' },
  { key: 'risk', label: '待处理风险' },
  { key: 'cmp', label: '条款比对' },
  { key: 'add', label: '新增条款' },
  { key: 'history', label: '历史修改' },
]

/** 条款比对来源的会话 key（与后端 ClauseRevision.clause_key 的既有约定一致） */
export function cmpSessionKey(row) {
  return CMP_PREFIX + (row?.title || '')
}

// ════════════════════════════════════════════════════════════════
// 中文数字 / 标题解析（与后端 _cn_to_int / _int_to_cn / _parse_headings 同规则）
// ════════════════════════════════════════════════════════════════
const CN_DIGITS = ['零', '一', '二', '三', '四', '五', '六', '七', '八', '九']
const CN_NUM = { 一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9 }

/** 整数 → 中文数字（1~99），与后端 _int_to_cn 一致 */
export function cnNo(n) {
  const v = Number(n)
  if (!v || v <= 0) return ''
  if (v < 10) return CN_DIGITS[v]
  if (v === 10) return '十'
  if (v < 20) return '十' + (v % 10 === 0 ? '' : CN_DIGITS[v % 10])
  if (v < 100) {
    const t = Math.floor(v / 10)
    const o = v % 10
    return CN_DIGITS[t] + '十' + (o === 0 ? '' : CN_DIGITS[o])
  }
  return String(v)
}

/** 中文/阿拉伯数字 → 整数（1~99），与后端 _cn_to_int 一致；失败返回 null */
export function cnToInt(s) {
  const t = String(s == null ? '' : s).trim()
  if (!t) return null
  if (/^\d+$/.test(t)) return Number(t)
  if (t === '十') return 10
  if (t.includes('十')) {
    const [a, b] = t.split('十')
    const tens = a ? (CN_NUM[a] || 0) : 1
    const ones = b ? (CN_NUM[b] || 0) : 0
    const v = tens * 10 + ones
    return v > 0 ? v : null
  }
  return CN_NUM[t] != null ? CN_NUM[t] : null
}

/**
 * 从合同正文解析顶层标题（第X条 / X、），与后端 _parse_headings 同规则。
 * 仅收 1~99 编号；标题取标题之后到下一个换行/标点为止的短句。
 *
 * 额外返回 `target_text` = 该标题所在的**整行原文**（原样，不做空白折叠）。
 * 用途（R-1）：同一编号在合同里会重复出现多次（真实合同实测 55% 存在重复顶层编号），
 * 只带编号会让后端只能"按编号取第一个"，与用户实际选中的那一处不一致。
 * 把整行原文一并带回，后端就能唯一定位用户真正选择的那一处。
 * 归一化交给后端统一处理，前端不预处理，避免两侧折叠口径不一致。
 */
export function parseHeadings(text) {
  const out = []
  if (!text) return out
  const re = /(第\s*)?([一二三四五六七八九十百千\d]+)\s*(条|、)/g
  const seen = new Set()
  let m
  while ((m = re.exec(text))) {
    const n = cnToInt(m[2])
    if (n === null || n < 1 || n > 99) continue
    if (seen.has(n)) continue
    const segStart = m.index + m[0].length
    const seg = text.slice(segStart, segStart + 20)
    const parts = seg.split(/[\n　\s。；;：，,]/).filter((p) => p.trim())
    seen.add(n)
    // 标题所在整行（与后端 _heading_line 同口径：按换行取行，不折叠空白）
    const lineStart = text.lastIndexOf('\n', m.index) + 1
    let lineEnd = text.indexOf('\n', segStart)
    if (lineEnd < 0) lineEnd = text.length
    out.push({
      num: n,
      cn: cnNo(n),
      title: parts[0] || '',
      start: m.index,
      target_text: text.slice(lineStart, lineEnd),
      paragraph_index: (text.slice(0, m.index).match(/\n/g) || []).length,
    })
  }
  return out.sort((a, b) => a.start - b.start)
}

export function clipText(s, n) {
  const t = String(s == null ? '' : s).replace(/\s+/g, ' ').trim()
  return t.length > n ? t.slice(0, n) + '…' : t
}

// ════════════════════════════════════════════════════════════════
// 定位预检（镜像后端 _pick_best_hit，仅 UI 提示）
// ════════════════════════════════════════════════════════════════
/**
 * 返回 { status: 'unique'|'disambiguated'|'ambiguous'|'none', hits, probe }
 * 后端规则：唯一命中 → 成功；多命中且恰有一个落在「（N）子项」内 → 成功；否则失败；
 * 无命中时把探针前缀逐级缩短到 4 字重试（与后端 (30,20,10,6,4) 一致）。
 * 这是**预检**，不是权威结论。
 */
export function previewLocate(fullText, probe) {
  const text = String(fullText || '')
  const needle = String(probe || '').trim()
  if (!text || !needle) return { status: 'none', hits: [], probe: '' }
  const ITEM_RE = /（\s*[一二三四五六七八九十百千\d]+\s*）/
  const findHits = (p) => {
    const hits = []
    let s = 0
    for (;;) {
      const i = text.indexOf(p, s)
      if (i < 0) break
      hits.push(i)
      s = i + 1
    }
    return hits
  }
  const judge = (hits) => {
    if (!hits.length) return null
    if (hits.length === 1) return 'unique'
    const inItem = hits.filter((p) => ITEM_RE.test(text.slice(Math.max(0, p - 40), p)))
    return inItem.length === 1 ? 'disambiguated' : 'ambiguous'
  }
  for (const n of [30, 20, 10, 6, 4]) {
    const p = needle.length >= n ? needle.slice(0, n) : needle
    if (!p) continue
    const hits = findHits(p)
    const verdict = judge(hits)
    if (verdict && verdict !== 'ambiguous') return { status: verdict, hits, probe: p }
    if (verdict === 'ambiguous' && n === 4) return { status: 'ambiguous', hits, probe: p }
  }
  return { status: 'none', hits: [], probe: '' }
}

export function previewLocatable(fullText, probe) {
  const r = previewLocate(fullText, probe)
  return r.status === 'unique' || r.status === 'disambiguated'
}

// ════════════════════════════════════════════════════════════════
// 条款比对问题判定
// ════════════════════════════════════════════════════════════════
/** 需要进「修改合同」的比对问题（missing 走新增；partial/有偏离/有补全建议 走替换） */
export function hasComparisonIssue(row) {
  if (!row) return false
  if (row.status === 'missing') return false
  return row.status === 'partial' || !!row.deviation || !!row.completion
}

/** 该比对问题应走 add_clause 还是 replace（用于面板分组与按钮） */
export function comparisonAction(row) {
  if (!row) return 'replace'
  if (row.status === 'missing') return 'add_clause'
  return 'replace'
}

// ════════════════════════════════════════════════════════════════
// instruction 上下文
// ════════════════════════════════════════════════════════════════
export const USER_REQ_MARKER = '【用户修改要求】'

/** 剥离 instruction 里的上下文块，只留用户自己的修改要求 */
export function displayInstruction(text) {
  const t = String(text || '')
  const i = t.lastIndexOf(USER_REQ_MARKER)
  return i >= 0 ? t.slice(i + USER_REQ_MARKER.length).trim() : t
}

/** 从持久化历史里提取多轮上下文（发给后端的 history） */
export function buildHistory(revs) {
  return (revs || []).map((r) => ({
    instruction: displayInstruction(r.instruction),
    revised_clause: r.revised_clause || '',
  }))
}

// ════════════════════════════════════════════════════════════════
// 会话分组（Phase 4 的互斥优先级，写死在此，可单测）
// ════════════════════════════════════════════════════════════════
/** 纯新增条款会话：该 key 下的全部 revision 都是 add_clause */
export function isAddOnlyRevs(revs) {
  return !!(revs && revs.length) && revs.every((r) => (r.operation || 'replace') === 'add_clause')
}

/**
 * 会话分组优先级（稳定、互斥）：
 *  1. key === '__overview__'                     → overview
 *  2. 该会话全部 revision 都是 add_clause         → add
 *  3. key 以 '__cmp__' 开头                       → cmp
 *  4. key 命中当前 audit-result 的风险 id          → risk
 *  5. 其他                                        → history
 *
 * 为什么第 2 条优先于第 3/4 条：新增条款是**操作语义**，而 R09 风险会话的 key 是
 * AuditRecord.id、比对缺失会话的 key 是 `__cmp__+title`，两者都会与风险/比对分类重叠。
 * 若按 key 优先，新增条款会被误显示成「替换修改」，用户会以为在改原条款。
 * 采用「全部都是 add_clause」而不是「存在任一 add_clause」，避免一条会话里既替换又
 * 新增时被整组搬走（真实数据目前无混合会话，但规则必须自洽）。
 */
export function classifySessionGroup(key, revs, riskIds) {
  const k = String(key || '')
  if (k === OVERVIEW_KEY) return 'overview'
  if (isAddOnlyRevs(revs)) return 'add'
  if (k.startsWith(CMP_PREFIX)) return 'cmp'
  if (riskIds && riskIds.has(k)) return 'risk'
  return 'history'
}

/**
 * 缺失型风险判断 —— **不按 R 编号机械判断**。
 * 真实数据：R09（50 条）与 R08（6 条）的 clause_text 是「全文未出现…」这类合成描述，
 * 并非合同正文，永远无法在 parsed_text 中定位，因此必须走「新增条款」而不是替换。
 */
export function isMissingTypeRiskShape(clauseText, anchorText) {
  if (String(anchorText || '').trim()) return false
  const t = String(clauseText || '').trim()
  if (!t) return true
  if (/^全文/.test(t)) return true
  if (/未(出现|定义|明确|约定)|缺少|缺失|未提及/.test(t) && t.length <= 40) return true
  return false
}

/** 会话应走哪条路：'overview' | 'add_clause' | 'replace' */
export function sessionActionFor({ key, revs = [], cmpStatus = '', riskMissingType = false }) {
  if (String(key || '') === OVERVIEW_KEY) return 'overview'
  if (isAddOnlyRevs(revs)) return 'add_clause'
  if (cmpStatus === 'missing') return 'add_clause'
  if (riskMissingType && !revs.length) return 'add_clause'
  return 'replace'
}

// ════════════════════════════════════════════════════════════════
// 新增条款插入位置
// ════════════════════════════════════════════════════════════════
/** 插入位置能否被后端解析（镜像 docx_reviser._insert_one 的判据） */
export function addPositionOk(position) {
  const p = position || {}
  if (p.append) return true
  return cnToInt(p.anchor) !== null
}

/** 位置可读文案 */
export function positionText(position) {
  const p = position || {}
  if (p.hint) return p.hint
  if (p.append) return '追加到合同末尾'
  if (p.anchor) return `第${p.anchor}条之后`
  return '未指定位置'
}

/**
 * 「在 X 条之前」的可表达性：后端 `_insert_one` 只支持「在某编号条款之后插入」
 * （`_insert_paragraph_level` / `_insert_inline` 都插在 anchor 的下一标题之前），
 * 因此「第 X 条之前」必须翻译成「第 X-1 条之后」；若 X 已是第一条则无法表达。
 */
export function beforeAnchorOf(headings, num) {
  const list = headings || []
  const i = list.findIndex((h) => h.num === num)
  if (i <= 0) return null
  return list[i - 1].num
}

// ════════════════════════════════════════════════════════════════
// DOCX 可导出口径（与后端 download_revised_docx 完全一致）
// ════════════════════════════════════════════════════════════════
/**
 * 该修订是否会被写入修订版 DOCX。
 * 后端取数：`scope == 'clause' OR (scope == 'overview' AND operation == 'add_clause')`。
 * 因此 **scope=overview 的 replace 永远不会被写入 DOCX**，前端不得暗示它会写回。
 */
export function isExportableRevision(rev) {
  const scope = (rev && rev.scope) || 'clause'
  const op = (rev && rev.operation) || 'replace'
  if (scope === 'clause') return true
  return scope === 'overview' && op === 'add_clause'
}

/**
 * 支持导出修订版的原始格式（与后端 `download_revised_docx` 的真实能力对齐）。
 *
 * - `docx`：直接打开原 DOCX 应用修订（既有链路）；
 * - `pdf` ：普通 PDF 直接解析；**扫描型 PDF** 逐页栅格化后走 OCR，
 *           再由已落库的 `parsed_text` 生成中间 DOCX 后应用修订；
 * - 图片（jpg/jpeg/png/tiff/tif/bmp）：OCR → `parsed_text` → 中间 DOCX。
 *
 * 三者最终**统一输出 Word（DOCX）**。
 */
export const EXPORTABLE_SOURCE_EXTS = ['docx', 'pdf', 'jpg', 'jpeg', 'png', 'tiff', 'tif', 'bmp']

/** 从真实上传路径取小写扩展名（不含点）；取不到返回空串 */
export function sourceExt(storedPath, fileName = '') {
  // 优先 stored_path；仅当它缺失（历史脏数据）才退回显示名，避免回归出"永远不能下载"
  const path = String(storedPath || '').trim() || String(fileName || '').trim()
  const m = /\.([a-z0-9]+)$/i.exec(path)
  return m ? m[1].toLowerCase() : ''
}

/**
 * 导出状态机（文案集中在此，避免组件与状态层口径不一致）。
 *
 * **判断源格式只看真实上传文件路径 `stored_path`**，绝不看 `file_name`：
 * `file_name` 是用户可编辑的「合同显示名称」（上传时默认取文件名去后缀），
 * 真实数据里大量 file_name 根本没有扩展名（如 '测试合同文件'、'111'），
 * 拿它判断格式会把真 DOCX 误判成非 Word，导致下载按钮被永久禁用。
 * 后端 `download_revised_docx` 也是按 `stored_path` 判断，此处与之对齐。
 *
 * 本轮变更：PDF 与**图片**（OCR 输入）由「不可导出」改为「可导出」——
 * 所有非 DOCX 输入统一走「parsed_text → 中间 DOCX」，修订版统一输出 Word（DOCX）。
 */
export function docxStateFor({ storedPath = '', fileName = '', exportableCount = 0, blockerCount = 0 }) {
  const ext = sourceExt(storedPath, fileName)

  if (!EXPORTABLE_SOURCE_EXTS.includes(ext)) {
    return {
      key: 'not_docx',
      text: '当前合同的修订版暂不支持导出',
      detail: ext
        ? `当前源文件格式（.${ext}）暂不支持导出修订版；已支持 .docx / .pdf / 图片。`
        : '无法识别当前合同的原始文件格式，暂不支持导出修订版。',
    }
  }
  if (!exportableCount) {
    return {
      key: 'none',
      text: '当前合同还没有任何条款修改',
      detail: '完成至少一条条款修改后才可导出。',
    }
  }
  if (blockerCount) {
    return {
      key: 'blocked',
      text: `预计有 ${blockerCount} 条修改尚未建立可靠原文定位，暂不能安全导出`,
      detail: '请对这些会话指定并确认修改位置后再导出（最终以下载时后端判定为准）。',
    }
  }
  return {
    key: 'ready',
    text: '修改位置已确认，可生成修订版',
    detail: '最终以下载时后端校验结果为准。',
  }
}

// ════════════════════════════════════════════════════════════════
// 总体修改（整份合同总控台）—— 纯规则，无框架依赖
// ════════════════════════════════════════════════════════════════

/**
 * 总控台修改点的四种状态（V2.2 第五节：必须彻底区分）。
 *
 *   pending    未纳入 —— 尚未纳入本轮综合方案
 *   included   已纳入方案（待专项处理）—— **只是总控台工作流状态**：
 *              不写后端、不产生 ClauseRevision、不进入 DOCX 可导出集合、
 *              不计入左侧「已确认修改」。
 *   processing 专项处理中 —— 已纳入且目标专项会话已有 AI 修订，但尚未最终确认
 *   confirmed  已确认修改 —— 用户已在专项会话最终确认（[采纳此版]/[确认新增]）
 *
 * 已知边界（本轮明确接受，不自行补后端）：
 *   后端没有持久化「已纳入方案」的位置，因此 `included` 属于**前端会话态**；
 *   刷新页面后回到 `pending`，需要重新纳入。这不影响已确认修改、ClauseRevision 与 DOCX 链路。
 */
export const MODIFICATION_STATES = {
  pending: { key: 'pending', label: '未纳入', tone: 'info', dot: 'grey' },
  included: { key: 'included', label: '已纳入方案（待专项处理）', tone: 'warning', dot: 'yellow' },
  processing: { key: 'processing', label: '专项处理中', tone: 'primary', dot: 'blue' },
  confirmed: { key: 'confirmed', label: '已确认修改', tone: 'success', dot: 'green' },
}

/**
 * 单个修改点的总控台状态。
 *
 * @param {{ included?: boolean, hasRevisions?: boolean, exportable?: boolean }} f
 *   included     该修改点是否已被用户纳入本轮综合方案（仅前端会话态）
 *   hasRevisions 目标专项会话是否已有修订记录
 *   exportable   目标专项会话是否已具备写入修订版合同的条件（后端真实判定）
 *                 —— 即「已确认修改」的唯一判据（见 countConfirmedSessions）
 */
export function modificationStateFor({ included = false, hasRevisions = false, exportable = false } = {}) {
  // 已确认修改优先：后端判定为可写入，说明具体修改文本与位置都已可靠确定
  if (exportable) return MODIFICATION_STATES.confirmed
  if (included) return hasRevisions ? MODIFICATION_STATES.processing : MODIFICATION_STATES.included
  return hasRevisions ? MODIFICATION_STATES.processing : MODIFICATION_STATES.pending
}

/**
 * 「已确认修改 N」的唯一口径（V2.2 2.2）：
 * 只统计**专项会话中用户最终确认、且原文位置可靠确定**的具体修改。
 *
 * 判据取后端 `/overview` 给出的 `sessions[].export.exportable`（后端真实判定，
 * 与 download_revised_docx 取数口径镜像），因此：
 *   - 不统计 AI 生成次数、不统计总控台方案数量、不统计 revision_count；
 *   - **不统计「已纳入方案」**（那只是前端工作流状态，且不入 ClauseRevision）。
 *
 * 排除总体会话自身的 replace 讨论稿（它永不写入 DOCX，也不构成"具体修改"）。
 */
export function countConfirmedSessions(overviewSessions) {
  const list = Array.isArray(overviewSessions) ? overviewSessions : []
  return list.filter((s) => s && s.kind !== 'overview' && s.export && s.export.exportable === true).length
}

/** 某个 session key 是否有可写入修订版合同的条件（无该会话 / 无 overview 数据时返回 false） */
export function sessionExportable(overviewSessions, key) {
  const list = Array.isArray(overviewSessions) ? overviewSessions : []
  const hit = list.find((s) => s && String(s.key) === String(key))
  return !!(hit && hit.export && hit.export.exportable === true)
}

// ── 综合方案项的展示与操作 ──────────────────────────────────────

/** 方案项类型（用户视角，不暴露 operation 枚举） */
export function proposalItemKind(item) {
  return (item && item.operation) === 'add_clause' ? '新增条款' : '条款修改'
}

/** 方案项标题：优先条款编号，其次逐字原文摘要，最后类型 */
export function proposalItemTitle(item) {
  if (!item) return '修改项'
  const no = cnToInt(item.clause_no)
  const quote = clipText(item.original_quote || '', 24)
  if (no) return `第${cnNo(no)}条`
  if (quote) return quote
  return proposalItemKind(item)
}

/** 方案项状态（与总控台修改点状态同一套语义） */
export function proposalItemState(item, includedSet) {
  const included = !!(includedSet && includedSet.has && includedSet.has(item && item.id))
  return modificationStateFor({ included }).key
}

/**
 * 方案项是否可被「纳入方案」。
 *
 * 只有后端已确定性定位（`resolved === true`）的项才可以纳入；
 * 未定位的项必须先由用户指定位置（或先进入专项会话确认位置），否则纳入后也无法落地。
 */
export function canIncludeProposalItem(item) {
  return !!(item && item.resolved === true)
}

/** 方案项需要用户先确认位置时的引导文案（原样展示后端 blocking_reason） */
export function proposalBlockingText(item) {
  if (!item) return ''
  return item.blocking_reason || '该修改项尚未建立可靠定位，请先指定位置的条款。'
}

/**
 * 位置选择器：把「第 X 条之前」翻译成后端可表达的「第 X-1 条之后」。
 * 已是第一条时返回 beforeAnchorOf 的 null → 前端必须提示无法表达，不得静默改成别的位置。
 */

// ── 条款比对的「真实参考条款」 ──────────────────────────────────
/**
 * 从标准条款模板（GET /templates）里取出与该比对项 title 匹配的参考条款正文。
 *
 * 为什么需要它：`GET /clause-comparison` **不返回**标准条款正文，只返回
 * title/status/matched_text/deviation/completion/risk/related_law。
 * 参考条款正文只存在于标准条款模板里（企业自定义模板 → templates.clauses）。
 *
 * **没有匹配到就返回空串** —— 前端绝不自己生成标准条款、绝不把 AI 建议当标准条款
 * （V2.2 3.4 与 8.4 的硬约束）。模板不存在时比对区退化为两栏。
 */
export function referenceClauseText(templates, title) {
  const t = String(title || '').trim()
  if (!t) return ''
  const list = Array.isArray(templates) ? templates : []
  for (const tpl of list) {
    const clauses = tpl && tpl.clauses
    if (!clauses) continue
    const arr = Array.isArray(clauses)
      ? clauses
      : Object.entries(clauses).map(([k, v]) => (v && typeof v === 'object' ? { title: v.title || k, ...v } : { title: k, content: v }))
    for (const c of arr) {
      if (!c) continue
      const name = String(c.title || c.name || c.clause || '').trim()
      if (name && name === t) {
        const text = c.content || c.text || c.description || ''
        if (text) return String(text)
      }
    }
  }
  return ''
}

/** 比对项是否应展示三栏（有真实参考条款正文才展示，否则按实际数据两栏） */
export function comparisonLayout(row, referenceText) {
  if (row && row.status === 'missing') return 'missing'
  if (row && row.status === 'covered') return 'covered'
  return referenceText ? 'three' : 'two'
}

// ── 定位候选（多候选必须由用户点选）────────────────────────────
/**
 * 把 `POST /locate-clause` 的结果整理成**必须由用户点选的候选列表**。
 *
 * 硬约束（V2.2 3.3 红线）：后端可能已给出唯一命中（found=true），但前端仍把它作为
 * **候选项**渲染，由用户点选后才建立"已确认位置"。禁止自动替用户选中并继续。
 *
 * 返回 [{ key, clause_no, clause_title, original_text, start, end, chosen }]
 */
export function locateCandidates(locate) {
  if (!locate) return []
  const out = []
  const push = (c, chosen) => {
    if (!c || !c.original_text) return
    const start = c.start == null ? null : c.start
    if (out.some((x) => x.original_text === c.original_text && x.start === start)) return
    out.push({
      key: `${start == null ? 'x' : start}-${out.length}`,
      clause_no: c.clause_no ?? null,
      clause_title: c.clause_title || '',
      original_text: c.original_text,
      start,
      end: c.end == null ? null : c.end,
      chosen,
    })
  }
  if (locate.found) push(locate, true)
  for (const c of locate.candidates || []) push(c, false)
  return out
}

/** 定位结果是否需要用户在多候选之间选择（>=1 个候选都要求点选，只是文案不同） */
export function locateNeedsChoice(locate) {
  if (!locate) return false
  const cands = locateCandidates(locate)
  if (!cands.length) return false
  // found=true 且只有一个候选：仍需用户确认，但属于"确认"而非"多选一"
  if (locate.found && cands.length === 1) return false
  return cands.length > 1
}

/**
 * 历史轮次的对比数据（V2.2 第七节最低要求：原文 ↔ 该轮修改文本）。
 * 每轮的"原文"= 该轮写入的 clause_text（第一轮是条款原文，后续轮是上一轮修订稿）。
 */
export function roundDiff(rev) {
  return {
    before: (rev && rev.clause_text) || '',
    after: (rev && rev.revised_clause) || '',
  }
}

// ════════════════════════════════════════════════════════════════
// 右栏定位三态（统一口径：左栏徽标 / 右栏工作区共用同一份判定）
// ════════════════════════════════════════════════════════════════
/**
 * 定位三态——必须区分"系统能定位"与"用户已确认"，两者语义不同：
 *
 *   unlocated    未定位           —— 没有系统锚点、也没预检到位置 → 只显示定位方式
 *   locatable    可定位但未确认   —— 预检到位置或审核阶段有锚点，但用户没在这次会话里确认过
 *   confirmed    已确认定位       —— 用户点了候选确认，或有已落库修订带锚点
 *
 * 为什么还要区分后两者：`previewLocatable` 只是**前端预检**（镜像后端 _pick_best_hit），
 * 不等于"用户已确认"；而 `lastRev.original_clause_text` 是**已落库锚点**，
 * 说明这条替换已经能安全写入 DOCX。三者混为一谈会让用户以为改的位置已经定了。
 *
 * 注意：本判定**只影响右栏工作区的进入条件**，不改变左栏 locateBadge 的既有口径。
 */
export const LOCATION_STATES = {
  unlocated: { key: 'unlocated', label: '未定位', tone: 'warning', dot: 'grey' },
  locatable: { key: 'locatable', label: '可定位但未确认', tone: 'primary', dot: 'blue' },
  confirmed: { key: 'confirmed', label: '已确认定位', tone: 'success', dot: 'green' },
}

/**
 * @param {Object} f
 *   relocateMode   用户点了「重新指定位置」→ 强制回到未定位工作区
 *   confirmedAnchor 用户本次会话内确认的候选（ui.confirmAnchor.original_text）
 *   dbAnchor       已落库修订的原文锚点（lastRev.original_clause_text）
 *   autoAnchor     审核阶段锚点（risk.clause_position.original_text）
 *   locatable      前端预检结果（previewLocatable(...)）
 */
export function locationStateFor({
  relocateMode = false,
  confirmedAnchor = '',
  dbAnchor = '',
  autoAnchor = '',
  locatable = false,
} = {}) {
  if (relocateMode) return LOCATION_STATES.unlocated
  // 已落库锚点最可信：它意味着这条替换已经能写进修订版合同
  if (dbAnchor || confirmedAnchor) return LOCATION_STATES.confirmed
  if (autoAnchor || locatable) return LOCATION_STATES.locatable
  return LOCATION_STATES.unlocated
}

/** 有"可靠当前位置"（可定位或已确认）→ 进入已定位工作区；只有 unlocated 才显示定位方式 */
export function hasReliableLocation(state) {
  return state === 'confirmed' || state === 'locatable'
}
