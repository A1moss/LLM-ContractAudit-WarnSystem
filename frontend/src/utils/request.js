import axios from 'axios'
import { ElMessage } from 'element-plus'
import router from '../router/index.js'

const request = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

// ── request 拦截器：自动注入 token ──
request.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('token')
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// ── response 拦截器：401 → /login，其他错误统一弹 message ──
request.interceptors.response.use(
  (response) => {
    // 直接返回 data，调用方不用每次都 .data
    return response.data
  },
  (error) => {
    if (error.response) {
      const { status, data } = error.response
      if (status === 401) {
        localStorage.removeItem('token')
        router.push('/login')
        ElMessage.error('登录已过期，请重新登录')
      } else {
        const msg = data?.detail || data?.message || `请求失败 (${status})`
        ElMessage.error(msg)
      }
    } else {
      ElMessage.error('网络异常，请检查后端是否启动')
    }
    return Promise.reject(error)
  }
)

export default request
