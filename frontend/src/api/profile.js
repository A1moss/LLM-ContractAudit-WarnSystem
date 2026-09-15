import request from '../utils/request.js'

/**
 * 当前登录用户的个人信息（含「个人 DeepSeek Key 是否已配置」）
 * 后端只返回布尔状态，**不会返回 Key 本身或其片段**
 */
export function getProfile() {
  return request.get('/auth/profile')
}

/**
 * 保存「当前用户自己」的 DeepSeek Key（后端加密落库，不回显）
 * @param {string} apiKey — 用户输入的 DeepSeek API Key
 */
export function saveMyDeepseekKey(apiKey) {
  return request.put('/auth/profile/deepseek-key', { api_key: apiKey })
}

/**
 * 删除当前用户的个人 Key（之后自动回退到 .env 的系统默认 Key）
 */
export function deleteMyDeepseekKey() {
  return request.delete('/auth/profile/deepseek-key')
}
