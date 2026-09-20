/**
 * 合同分类体系（对齐后端 ai.taxonomy）
 * 前端唯一数据源：类型下拉、标签映射统一从这里取，改类别只改这一处。
 * CONTRACT_TYPES = 法理分类（11 类，单一互斥）
 * BUSINESS_TAGS  = 业务标签（多值、可叠加，如"服务外包"）
 *
 * ⚠️ 「无名合同」与「分类失败/待分类」是两件事（BUG-1，不得混用）
 * ---------------------------------------------------------------
 * * `无名合同` 是 taxonomy 里的**正式法理类别**——模型真的判断该合同不属于任何有名合同
 *   （民法典第467条，如战略合作框架协议、培训/养老/电商/医美服务）。它必须原样显示
 *   「无名合同」，置信度正常（0.9+）。
 * * 分类**失败**（LLM 超时/限流/非法 JSON）时后端落 `contract_type=null` +
 *   `classification_status='failed'`，**不再**落任何伪类型。前端一律显示「待分类」，
 *   绝不再显示成「无名合同」。
 *
 * 历史问题（本文件即根因之一）：旧映射把 `'其他合同'` / `'other'` 归一成「无名合同」，
 * 于是分类失败在列表里长得和"模型判定的无名合同"一模一样，
 * 用户看到同一份合同"一会儿建设工程合同、一会儿无名合同"。
 */
export const CONTRACT_TYPES = [
  '买卖合同',
  '租赁合同',
  '承揽合同',
  '建设工程合同',
  '技术合同',
  '委托合同',
  '物业服务合同',
  '中介合同',
  '保密协议',
  '无名合同',
  '劳动合同',
]

export const BUSINESS_TAGS = ['服务外包']

/** 分类未成功（后端 classification_status='failed'）时的展示文案。 */
export const UNCLASSIFIED_LABEL = '待分类'

/** 类型标签归一：旧名/英文 slug → 新 11 类名（兼容历史数据与遗留字段）。 */
export const TYPE_LABEL = {
  '买卖合同': '买卖合同',
  '租赁合同': '租赁合同',
  '承揽合同': '承揽合同',
  '建设工程合同': '建设工程合同',
  '技术合同': '技术合同',
  '委托合同': '委托合同',
  '中介合同': '中介合同',
  '服务外包合同': '服务外包合同',
  '保密协议': '保密协议',
  '无名合同': '无名合同',
  '物业服务合同': '物业服务合同',
  '劳动合同': '劳动合同',
  // 旧名归一
  '采购合同': '买卖合同',
  '销售合同': '买卖合同',
  '保密合同': '保密协议',
  '服务合同': '服务外包合同',
  '服务外包': '服务外包合同',
  // 分类失败态：**不映射成任何法理类别**（旧版这里错映射为「无名合同」）
  '其他合同': UNCLASSIFIED_LABEL,
  'other': UNCLASSIFIED_LABEL,
  'unclassified': UNCLASSIFIED_LABEL,
  '未分类': UNCLASSIFIED_LABEL,
  '待分类': UNCLASSIFIED_LABEL,
}

/** 空值/未知值一律显示「待分类」（空类型 = 分类失败，不是某个类别）。 */
export function typeLabel(type) {
  if (!type) return UNCLASSIFIED_LABEL
  return TYPE_LABEL[type] || type
}

/** 是否为「分类未成功」的唯一判据（后端字段优先，缺失时按类型是否为空推导）。 */
export function isUnclassified(row) {
  if (!row) return true
  if (row.classification_status === 'failed') return true
  if (row.classification_status === 'success' || row.classification_status === 'manual') {
    // 显式状态可信：有类型即已分类（含模型判定的「无名合同」）
    return !row.contract_type
  }
  // 历史数据（无状态字段）：按类型是否为空推导
  return !row.contract_type
}
