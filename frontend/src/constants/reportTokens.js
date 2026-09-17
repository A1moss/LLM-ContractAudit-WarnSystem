/**
 * 审核报告双页面共用 UI token（**纯前端展示常量**）
 *
 * 边界说明（不得越界）：
 * - 本文件只做「后端真实值 → 中文标签 / 颜色 / 百分比宽度」的映射，
 *   **不参与任何风险判定**，也**不生成任何数据**。
 * - 所有 key 都逐字对齐后端真实取值，不做同义扩展：
 *     risk_level          → high | medium | low            （backend/models/audit_record.py）
 *     clauses[].status    → covered | partial | missing    （ai/matcher/matcher.py）
 *     clauses[].priority  → required | recommended         （同上）
 *     contracts.status    → uploaded/parsed/auditing/completed/reviewed/approved
 *     contracts.audit_mode→ precise | fast
 * - 风险中文名不在本文件：唯一来源是 constants/riskTypes.js 的 RISK_NAMES（与后端一致）。
 */

/** 风险等级档位（顺序即展示顺序：高 → 中 → 低） */
export const REPORT_LEVELS = [
  { key: 'high', label: '高风险', color: '#DC2626' },
  { key: 'medium', label: '中风险', color: '#D97706' },
  { key: 'low', label: '低风险', color: '#2563EB' },
]

/** 风险等级 → 颜色（模板里直接用，避免再 find 一遍） */
export const LEVEL_COLORS = {
  high: '#DC2626',
  medium: '#D97706',
  low: '#2563EB',
}

/** 条款比对状态 → 颜色（覆盖=成功色；偏离=中风险色；缺失=高风险色） */
export const CMP_COLORS = {
  covered: '#16A34A',
  partial: '#D97706',
  missing: '#DC2626',
}

/** 条款优先级（后端 clauses[].priority） */
export const PRIORITY_LABELS = {
  required: '必需条款',
  recommended: '建议条款',
}

export function priorityLabel(priority) {
  return PRIORITY_LABELS[priority] || priority || ''
}

/**
 * 按 高→中→低 分组。
 * 空分组不产出 —— 避免页面上出现「高风险 0 项」这种空白模块。
 */
export function groupRisksByLevel(items) {
  const list = Array.isArray(items) ? items : []
  return REPORT_LEVELS
    .map((lv) => ({ ...lv, items: list.filter((r) => r && r.risk_level === lv.key) }))
    .filter((g) => g.items.length > 0)
}

/**
 * 组内排序：判断把握度（confidence）高者优先。
 * 同值时保持后端返回的原始顺序（稳定排序），**不引入任何新的风险权重**。
 */
export function sortByConfidence(items) {
  return (items || [])
    .map((r, i) => ({ r, i }))
    .sort((a, b) => ((b.r.confidence || 0) - (a.r.confidence || 0)) || (a.i - b.i))
    .map((x) => x.r)
}

/**
 * 堆叠分布条：把真实计数换算成百分比宽度。
 * 全部为 0 时返回空数组（调用方据此不渲染这个条，而不是画一条 0 宽度的空条）。
 */
export function stackSegments(pairs) {
  const list = (pairs || []).filter((p) => p && typeof p.value === 'number' && p.value > 0)
  const total = list.reduce((n, p) => n + p.value, 0)
  if (!total) return []
  return list.map((p) => ({ ...p, pct: (p.value / total) * 100 }))
}

/** 审核方式（后端 contracts.audit_mode） */
const AUDIT_MODE_LABELS = { precise: '精细审核', fast: '快速初筛' }

export function auditModeLabel(mode) {
  return AUDIT_MODE_LABELS[mode] || ''
}

/** 合同状态（后端 contracts.status） */
const CONTRACT_STATUS_LABELS = {
  uploaded: '已上传',
  parsed: '已解析',
  auditing: '审核中',
  completed: '审核完成',
  reviewed: '待验收',
  approved: '已验收',
}

export function contractStatusLabel(status) {
  return CONTRACT_STATUS_LABELS[status] || status || ''
}

const CURRENCY_SYMBOLS = { CNY: '¥', USD: '$', EUR: '€', GBP: '£', JPY: '¥' }

/**
 * 合同金额拆分（后端 contracts.extracted_elements.amount = {value, currency, text}）。
 * 只做数字格式化与币种符号映射，**不换算、不推断**；缺字段就返回空串，由调用方隐藏该项。
 */
export function amountParts(amount) {
  if (!amount || typeof amount !== 'object') return { main: '', text: '' }
  const { value, currency, text } = amount
  let main = ''
  if (typeof value === 'number' && Number.isFinite(value)) {
    const sym = CURRENCY_SYMBOLS[currency] || ''
    main = sym + value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    if (!sym && currency) main += ` ${currency}`
  } else if (currency) {
    main = String(currency)
  }
  return { main, text: typeof text === 'string' ? text : '' }
}

/** 履行期限（后端 extracted_elements.performance_period = {start, end}） */
export function performancePeriodText(period) {
  if (!period || typeof period !== 'object') return ''
  const { start, end } = period
  if (start && end) return `${start} 至 ${end}`
  if (start) return `${start} 起`
  if (end) return `至 ${end}`
  return ''
}

/**
 * 报告免责声明（固定文案，不属于业务结论）。
 * 单独抽出来是为了两个页面用同一句，不出现第二种表述。
 */
export const REPORT_DISCLAIMER = '本报告由 AI 合同审核系统自动生成，仅供法务与业务参考，不构成法律意见。'

/** 报告标题（固定文案；合同正式名称后端不存在，绝不推导） */
export const REPORT_TITLE = '合同智能审核报告'

/**
 * 风险评分 → 评分环颜色。
 *
 * **阈值完全沿用项目既有实现**（`views/AuditReport.vue` 的 scoreColor：≥60 / ≥30 / 其余 三档），
 * 本轮没有新增任何评分阈值，也没有改动后端 `risk_score` 的计算方式。
 * 返回的三色直接取本页既有的风险等级色（LEVEL_COLORS），
 * 让评分环的红色与页面上「高风险」标签 / 等级分布条 / 分组圆点**完全一致**。
 */
export function scoreColor(score) {
  const v = Number(score)
  if (!Number.isFinite(v)) return LEVEL_COLORS.low
  if (v >= 60) return LEVEL_COLORS.high
  if (v >= 30) return LEVEL_COLORS.medium
  return LEVEL_COLORS.low
}

/**
 * 后端内部标记 → 展示用业务措辞。
 *
 * 措辞依据后端真实取数定义（`ai/auditor/evidence_extractor.py` 的 R06_IP / R08_验收 字段语义），
 * 不是凭空拟词：
 *   has_consideration / consideration_text → 是否已约定对价 / 对价约定原文
 *   objective_basis    / basis_evidence    → 是否存在客观验收标准 / 验收依据原文
 */
const INTERNAL_MARKER_TEXT = {
  consideration_text: '对价约定缺失',
  has_consideration: '未约定对价',
  basis_evidence: '合同中未载明验收依据',
  objective_basis: '缺少客观验收标准',
}

/** 内容型标记比布尔标记更具体：同一括号内两者并存时，只用内容型
 *  （真实数据 R06「has_consideration为false，consideration_text为空」是同一个事实的
 *    布尔位与内容位，各给一句业务措辞会读成重复，故只取内容型。） */
const INTERNAL_DETAIL_MARKERS = new Set(['consideration_text', 'basis_evidence'])

/** 内部字段名（snake_case）以及可选的「为/＝/= 取值」尾巴 */
const INTERNAL_MARKER_RE = /[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+(?:\s*(?:为|＝|=)\s*(?:false|true|空|无|None|null))?/g
/** 括号内除内部标记外只剩分隔符 → 认为整段括号都是机器标注 */
const MARKER_SEP_RE = /^[\s，,、；;：:和与]*$/
/** 机器标注的英文前缀标签（真实数据：R08 的「（Evidence：objective_basis为false…）」）。
 *  只在括号内**已经含有内部字段名**时才一并视为标注内容，不会误伤正常的业务括号。 */
const MARKER_LABEL_RE = /^\s*(?:evidence|proof)\s*[:：]\s*/i

/**
 * 展示层文案清理：把后端建议层文本里夹带的**内部字段名标记**改写为业务语言。
 *
 * 背景（真实数据，非假设）：后端 `recommendation.risk_description` 由 LLM 生成，会在括号里
 * 留下机器核查标记，例如 R06 的「（has_consideration为false，consideration_text为空）」、
 * R08 的「（objective_basis为false）」「（basis_evidence为空）」，直接展示即内部字段名泄露。
 *
 * 边界：
 * - **只做显示层替换**：不改后端数据、不改审核逻辑、不改变句子的事实含义、不新增任何结论；
 * - 括号内若除标记外还有业务文字，只替换标记本身；整段都是标记时，整段换成一句业务措辞；
 * - 未知标记一律整体移除（宁可少一句机器标注，也不泄露字段名）；
 * - **绝不用在合同正文类字段**（clause_text / matched_text / original_text / 锚点原文）——
 *   那些必须逐字保持原样，否则会破坏原文引用与逐字定位校验。
 */
export function cleanAdviceText(text) {
  const s = String(text == null ? '' : text)
  if (!s) return ''

  // ① 括号内全是机器标记 → 整段括号换成一个业务短语
  let out = s.replace(/[（(]([^（）()]*)[）)]/g, (whole, inner) => {
    const names = (String(inner).match(INTERNAL_MARKER_RE) || [])
      .map((t) => t.split(/\s*(?:为|＝|=)/)[0].trim())
      .filter(Boolean)
    if (!names.length) return whole
    const rest = String(inner).replace(INTERNAL_MARKER_RE, '').replace(MARKER_LABEL_RE, '')
    if (!MARKER_SEP_RE.test(rest)) return whole
    const key = names.find((n) => INTERNAL_DETAIL_MARKERS.has(n)) || names[0]
    const phrase = INTERNAL_MARKER_TEXT[key]
    return phrase ? `（${phrase}）` : ''
  })

  // ② 兜底：括号外的残留标记（含未知字段名）→ 已知的换业务措辞，未知的整段移除
  out = out.replace(INTERNAL_MARKER_RE, (m) => {
    const key = m.split(/\s*(?:为|＝|=)/)[0].trim()
    return INTERNAL_MARKER_TEXT[key] || ''
  })

  // ③ 清理替换后可能出现的空括号与重复标点
  return out
    .replace(/[（(]\s*[）)]/g, '')
    .replace(/([，,、；;])\s*(?=[。！？；;，,])/g, '')
    .replace(/\s{2,}/g, ' ')
    .trim()
}
