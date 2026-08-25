import request from '../utils/request.js'

/**
 * 获取标准条款模板列表
 * @param {Object} params — { contract_type }
 */
export function getTemplates(params = {}) {
  return request.get('/templates', { params })
}

/**
 * 创建标准条款模板
 * @param {Object} data — { name, contract_type, clauses }
 */
export function createTemplate(data) {
  return request.post('/templates', data)
}

/**
 * 更新标准条款模板（版本号 +1）
 * @param {number|string} id — 模板 ID
 * @param {Object} data — { name?, clauses? }
 */
export function updateTemplate(id, data) {
  return request.put(`/templates/${id}`, data)
}

/**
 * 删除标准条款模板
 * @param {number|string} id — 模板 ID
 */
export function deleteTemplate(id) {
  return request.delete(`/templates/${id}`)
}
