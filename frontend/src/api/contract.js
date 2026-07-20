import request from '../utils/request.js'

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
  if (params.our_role) formData.append('our_role', params.our_role)

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
 * 获取合同原始文件（二进制，供 pdf.js 使用）
 * @param {number|string} id — 合同 ID
 */
export function getContractFile(id) {
  return request.get(`/contracts/${id}/file`, { responseType: 'arraybuffer' })
}

/**
 * 获取合同风险热力图数据
 * @param {number|string} id — 合同 ID
 */
export function getHeatmapData(id) {
  return request.get(`/contracts/${id}/heatmap`)
}

/**
 * 条款比对：对比合同原文与标准条款模板
 * @param {number|string} id — 合同 ID
 */
export function compareContractClauses(id) {
  return request.post(`/contracts/${id}/compare`)
}
