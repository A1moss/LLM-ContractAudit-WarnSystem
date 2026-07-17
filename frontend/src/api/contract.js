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
