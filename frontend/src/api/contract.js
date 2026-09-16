import request from '../utils/request.js'
import {
  REVISE_TIMEOUT, COMPARE_TIMEOUT, FILE_TIMEOUT, LOCATE_TIMEOUT, OVERVIEW_PLAN_TIMEOUT,
} from './timeouts.js'

/**
 * 上传合同文件
 * @param {File} file — 合同文件（.docx/.pdf）
 * @param {Object} params — { name, contract_type, audit_mode, onProgress }
 */
export function uploadContract(file, params = {}) {
  const formData = new FormData()
  formData.append('file', file)
  if (params.name) formData.append('name', params.name)
  if (params.contract_type) formData.append('contract_type', params.contract_type)
  if (params.audit_mode) formData.append('audit_mode', params.audit_mode)

  return request.post('/contracts/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: params.onProgress || null,
  })
}

/**
 * 获取合同列表（分页 + 筛选）
 * @param {Object} params — { page, page_size, keyword, contract_type, status }
 */
export function getContractList(params = {}) {
  return request.get('/contracts', { params })
}

/**
 * 获取合同详情
 * @param {number|string} id — 合同 ID
 */
export function getContractDetail(id) {
  return request.get(`/contracts/${id}`)
}

/**
 * 删除合同
 * @param {number|string} id — 合同 ID
 */
export function deleteContract(id) {
  return request.delete(`/contracts/${id}`)
}

/**
 * 触发合同审核
 * @param {number|string} id — 合同 ID
 */
export function triggerAudit(id) {
  return request.post(`/contracts/${id}/audit`)
}

/**
 * 获取合同审核结果（风险列表）
 * @param {number|string} id — 合同 ID
 */
export function getAuditResult(id) {
  return request.get(`/contracts/${id}/audit-result`)
}

/**
 * 获取合同审核报告
 * @param {number|string} id — 合同 ID
 */
export function getAuditReport(id) {
  return request.get(`/contracts/${id}/audit-report`)
}

/**
 * 获取条款比对结果
 * @param {number|string} id — 合同 ID
 */
export function getClauseComparison(id) {
  return request.get(`/contracts/${id}/clause-comparison`, { timeout: COMPARE_TIMEOUT })
}

/**
 * 审核人复核：approve(通过→待验收) / reject(驳回→退回重新审核)
 * @param {number|string} id — 合同 ID
 * @param {'approve'|'reject'} action — 复核动作
 */
export function reviewContract(id, action = 'approve') {
  return request.post(`/contracts/${id}/review`, null, { params: { action } })
}

/**
 * 验收人验收：待验收 → 已验收
 * @param {number|string} id — 合同 ID
 */
export function approveContract(id) {
  return request.post(`/contracts/${id}/approve`)
}

/**
 * 多轮对话式改条款
 * @param {number|string} id — 合同 ID
 * @param {Object} data — { clause_text, instruction, history }
 */
export function reviseClause(id, data) {
  return request.post(`/contracts/${id}/revise`, data, { timeout: REVISE_TIMEOUT })
}

/**
 * 获取合同全部修订会话（用于刷新后重建对话）。
 * 每条记录含 `adopted`：该轮是否为用户明确「确认采用」的版本。
 */
export function getRevisions(id) {
  return request.get(`/contracts/${id}/revisions`)
}

/**
 * 「确认采用此版」：把用户确认的那一轮修订标记为采用态。
 *
 * 语义（与"生成新一轮修改"彻底分开）：
 *  - **不调用 LLM**、**不新建修订记录**、**不改变轮次**；
 *  - 只把该 revision 置 adopted，并把同一会话下其它行取消采用；
 *  - 之后 DOCX 导出优先使用被采用的是这一版。
 *
 * @param {number|string} id — 合同 ID
 * @param {number|string} revisionId — 用户确认的那一轮修订 id
 * @param {string} clauseKey — 会话标识（后端二次校验，必须与该修订一致）
 */
export function adoptRevision(id, revisionId, clauseKey) {
  return request.post(`/contracts/${id}/revisions/${revisionId}/adopt`, { clause_key: clauseKey })
}

/**
 * 只读定位：根据用户提供的原文片段 / 定位描述 / 条款编号，返回候选定位结果。
 * 后端侧不调用 LLM、不写库、不建立任何 revision —— 只是定位工具。
 * 返回的 found 不等于「后端已建立 anchor」；anchor 仍由 POST /revise 建立。
 * @param {number|string} id — 合同 ID
 * @param {Object} data — { text?, clause_anchor? }
 *   text          用户在原文中选中的逐字原文，或自然语言定位描述
 *   clause_anchor 条款编号锚点（中文或阿拉伯数字，如 "五" / "5"）
 */
export function locateClause(id, data) {
  return request.post(`/contracts/${id}/locate-clause`, data, { timeout: LOCATE_TIMEOUT })
}

/**
 * 获取缺失条款（R09 等）的新增建议：同类范本 + 法律依据 + 建议插入位置
 * @param {number|string} id — 合同 ID
 * @param {Object} data — { risk_type, instruction }
 */
export function getAddClauseSuggestion(id, data) {
  return request.post(`/contracts/${id}/add-clause-suggestion`, data, { timeout: COMPARE_TIMEOUT })
}

/**
 * 下载修订版 DOCX（返回 Blob）
 */
export function downloadRevisedDocx(id) {
  return request.get(`/contracts/${id}/revised-docx`, { responseType: 'blob', timeout: FILE_TIMEOUT })
}

// ====== 总体修改会话（总控会话：scope=overview / clause_key=__overview__）======
//
// 语义（不要在 UI 里混同）：
//  - 总体会话不是聊天区，也不是"整份合同重写后覆盖原文件"；
//  - 它的链路是：读全部专项会话 → AI 出**结构化修改方案** → 用户逐项确认 →
//    每项转换成现有安全的 clause/add_clause 修改 → 复用现有定位与 DOCX 安全导出；
//  - 方案本身（revision_proposals）**永远不写入 DOCX**，只有确认后的具体修改项会被写入。

/**
 * 总体会话总览：当前合同全部专项修改会话的
 * 原文 / 当前最新修改结果 / 法律依据 / 剩余风险 / 定位状态 / 导出状态（只读、不调 LLM）
 * @param {number|string} id — 合同 ID
 */
export function getRevisionOverview(id) {
  return request.get(`/contracts/${id}/overview`)
}

/**
 * 在总体会话中提出整体修改要求 → 生成**结构化综合修改方案**（单次 LLM，不写任何修订记录）
 * @param {number|string} id — 合同 ID
 * @param {Object} data — { instruction, session_keys? }
 *   instruction   用户对整个合同的修改要求
 *   session_keys  可选：只针对这些专项会话统筹（不传 = 全部）
 * 返回 data.items[]：每个修改项含 operation / original_quote / revised_clause / reason /
 * legal_basis / position / resolved / anchor_text / blocking_reason。
 * **resolved=false 的项必须先让用户确认位置**，否则确认落库会被后端拒绝。
 */
export function createOverviewProposal(id, data) {
  return request.post(`/contracts/${id}/overview/plan`, data, { timeout: OVERVIEW_PLAN_TIMEOUT })
}

/**
 * 历史综合修改方案列表（刷新后恢复）
 * @param {number|string} id — 合同 ID
 */
export function listOverviewProposals(id) {
  return request.get(`/contracts/${id}/overview/proposals`)
}

/**
 * 读取某一份综合修改方案（逐项查看）
 * @param {number|string} id — 合同 ID
 * @param {number|string} proposalId — 方案 ID
 */
export function getOverviewProposal(id, proposalId) {
  return request.get(`/contracts/${id}/overview/proposals/${proposalId}`)
}

/**
 * 用户确认综合方案中的**具体修改项** → 后端逐项转换成安全的 clause/add_clause 修订
 * （不调用 LLM；未定位的项会被拒绝并返回原因）
 * @param {number|string} id — 合同 ID
 * @param {Object} data — { proposal_id, items: [{ id, revised_clause?, position?, target_session_key?, original_quote?, clause_no? }] }
 * 返回 data.applied[]（含 revision_id / clause_key / exportable）与 data.failed[]（含 reason / needs_location）
 */
export function confirmOverviewProposal(id, data) {
  return request.post(`/contracts/${id}/overview/confirm`, data, { timeout: REVISE_TIMEOUT })
}

// ====== 反馈标注 ======

/**
 * 提交反馈标注
 * @param {Object} data — { record_id, action_type, corrected_risk?, comment? }
 */
export function submitFeedback(data) {
  return request.post('/feedback', data)
}

/**
 * 获取某合同的所有反馈记录
 * @param {number|string} contractId — 合同 ID
 */
export function getFeedback(contractId) {
  return request.get(`/feedback/${contractId}`)
}

/**
 * 撤销一条反馈标注
 * @param {number|string} feedbackId — 反馈记录 ID
 */
export function deleteFeedback(feedbackId) {
  return request.delete(`/feedback/${feedbackId}`)
}

/**
 * 获取合同原始文件（二进制，供 pdf.js 使用）
 * @param {number|string} id — 合同 ID
 */
export function getContractFile(id) {
  return request.get(`/contracts/${id}/file`, { responseType: 'arraybuffer', timeout: FILE_TIMEOUT })
}
