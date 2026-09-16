/**
 * 风险类型展示映射（**纯前端 UI 常量**）
 *
 * 重要边界说明：
 * - 这里只是「风险代码 → 中文展示名」的 UI 映射，**不是后端字段**。
 * - 后端 `/audit-result` 只返回 `risk_type`（如 "R08"）+ `risk_level`，不返回风险中文名。
 *   （`ai/taxonomy.py` 只维护合同类型体系，不含风险名；风险名在后端内部为
 *    `ai/auditor/recommendation_engine.py` 的 RISK_NAMES 字典。）
 * - 因此：**一切业务判断必须使用 `risk_type` 原值**（如 R09 是否走新增条款），
 *   中文名仅用于展示，缺失时回退显示代码本身。
 * - 变更风险规则时必须同步此表；此表不参与任何判定逻辑。
 *
 * 该表与后端 RISK_NAMES 逐条对齐：R01 违约金过高 / R02 无限责任 / R03 单方解约权 /
 * R04 管辖条款不利 / R05 保密期间不合理 / R06 知识产权归属不清 / R07 付款条件不公平 /
 * R08 验收标准缺失 / R09 不可抗力条款缺失 / R10 竞业限制过宽 / R11 自动续约陷阱 /
 * R12 数据隐私条款不当 / R13 疑似名实不符
 */
export const RISK_NAMES = {
  R01: '违约金过高',
  R02: '无限责任',
  R03: '单方解约权',
  R04: '管辖条款不利',
  R05: '保密期间不合理',
  R06: '知识产权归属不清',
  R07: '付款条件不公平',
  R08: '验收标准缺失',
  R09: '不可抗力条款缺失',
  R10: '竞业限制过宽',
  R11: '自动续约陷阱',
  R12: '数据隐私条款不当',
  R13: '疑似名实不符',
}

/** 风险等级 → 中文（后端 risk_level: high | medium | low） */
export const RISK_LEVEL_LABELS = { high: '高风险', medium: '中风险', low: '低风险' }

/** 风险等级 → Element Plus tag 类型 */
export const RISK_LEVEL_TAGS = { high: 'danger', medium: 'warning', low: 'success' }

/**
 * 检测方式 → 中性中文标签。
 * 后端真实取值（`_build_evidence`）：rule / rag / corex_review / evidence。
 * 注意：`corex_review` 仅映射为中性标签「多智能体验证」，不出现任何品牌化命名。
 */
export const DETECTION_METHOD_LABELS = {
  rule: '规则引擎',
  rag: '检索引用',
  corex_review: '多智能体验证',
  evidence: '证据裁决',
}

/** 条款比对状态 → 中文（后端 status: covered | partial | missing） */
export const CMP_STATUS_LABELS = {
  covered: '已覆盖',
  partial: '部分偏离',
  missing: '缺失',
}

/** 风险代码 → 展示名（未知代码回退为代码本身，不编造名称） */
export function riskName(riskType) {
  if (!riskType) return ''
  return RISK_NAMES[riskType] || riskType
}

/** 风险代码 + 名称的组合展示（如 "R08 · 验收标准缺失"） */
export function riskLabel(riskType) {
  const name = riskName(riskType)
  if (!riskType) return ''
  return name === riskType ? riskType : `${riskType} · ${name}`
}

/** 等级代码 → 中文；未知回退为原值 */
export function riskLevelLabel(level) {
  return RISK_LEVEL_LABELS[level] || level || '—'
}

/** 检测方式 → 中文；未知回退为原值 */
export function detectionLabel(method) {
  if (!method) return '—'
  return DETECTION_METHOD_LABELS[method] || method
}
