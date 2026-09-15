import request from '../utils/request.js'

/**
 * 用户列表（仅管理员；非管理员后端返回 403）
 */
export function getUsers() {
  return request.get('/auth/users')
}

/**
 * 修改指定用户角色（仅管理员）
 * @param {number|string} id — 用户 ID
 * @param {string} role — uploader | reviewer | approver | admin
 */
export function updateUserRole(id, role) {
  return request.put(`/auth/users/${id}/role`, { role })
}
