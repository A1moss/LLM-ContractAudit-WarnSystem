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

/** 会话分组（左栏展示顺序） */
export const SESSION_GROUPS = [
  { key: 'overview', label: '总体修改会话' },
  { key: 'risk', label: '风险修改' },
  { key: 'cmp', label: '条款比对修改' },
  { key: 'add', label: '新增条款' },
  { key: 'history', label: '历史会话' },
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
    out.push({ num: n, cn: cnNo(n), title: parts[0] || '', start: m.index })
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

/** 导出状态机（文案集中在此，避免组件与状态层口径不一致） */
export function docxStateFor({ fileName = '', exportableCount = 0, blockerCount = 0 }) {
  if (!/\.docx$/i.test(fileName)) {
    return {
      key: 'not_docx',
      text: '仅 DOCX 原始合同支持导出修订版',
      detail: '当前合同不是 .docx 文件，后端无法生成修订版 DOCX。',
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
