import axios from 'axios'
import { ElMessage } from 'element-plus'
import router from '../router/index.js'

const request = axios.create({
  baseURL: 'http://localhost:8080/api',
  timeout: 60000,
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
        localStorage.removeItem('username')
        localStorage.removeItem('role')
        // 不在登录页时才跳转，避免登录失败时死循环
        if (router.currentRoute.value.path !== '/login') {
          router.push('/login')
        }
        const msg = data?.detail || '用户名或密码错误'
        ElMessage.error(msg)
      } else if (status === 403) {
        ElMessage.error(data?.detail || '没有权限执行此操作')
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
