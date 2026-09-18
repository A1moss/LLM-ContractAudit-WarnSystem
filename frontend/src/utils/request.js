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

// ── response 拦截器：401 → /login，网络错误静默重试一次，其他错误统一弹 message ──
request.interceptors.response.use(
  (response) => {
    // 二进制响应（如审核报告 PDF 导出）把后端文件名一并带上，
    // 因为拦截器只把 `data` 交给调用方，headers 会被丢掉。
    const data = response.data
    if (typeof Blob !== 'undefined' && data instanceof Blob) {
      const disp = response.headers?.['content-disposition'] || ''
      if (disp) {
        data.__disposition = disp
        const star = /filename\*=UTF-8''([^;]+)/i.exec(disp)
        if (star?.[1]) {
          try {
            data.__filename = decodeURIComponent(star[1])
          } catch {
            /* 非法编码就用后端给的 ASCII 回退名 */
          }
        }
        if (!data.__filename) {
          const plain = /filename="?([^";]+)"?/i.exec(disp)
          if (plain?.[1]) data.__filename = plain[1]
        }
      }
      return data
    }
    // 直接返回 data，调用方不用每次都 .data
    return data
  },
  async (error) => {
    const config = error.config
    // 网络错误（后端刚启动/连接未建立）时静默重试一次，避免页面首刷就弹"网络异常"
    // 只对幂等方法（GET/HEAD/OPTIONS）重试；POST 重试会导致重复审核/重复写库（BUG-030）
    const method = (config?.method || 'get').toLowerCase()
    if (!error.response && config && !config.__retried && ['get', 'head', 'options'].includes(method)) {
      config.__retried = true
      await new Promise(resolve => setTimeout(resolve, 1000))
      return request(config)
    }
    if (error.response) {
      const { status, data } = error.response
      if (status === 401) {
        localStorage.removeItem('token')
        localStorage.removeItem('username')
        localStorage.removeItem('role')
        localStorage.removeItem('email')
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

/**
 * 从 axios 错误里读出后端返回的业务错误文案。
 *
 * 必要性：当请求使用 `responseType: 'blob'`（如审核报告 PDF 导出）时，失败响应体
 * 也会被浏览器包成 Blob，`error.response.data.detail` 直接读是 `undefined`，
 * 于是用户只会看到「导出失败」这类无信息量的兜底文案。这里按需把 Blob 读回 JSON。
 *
 * @param {any} error — axios 抛出的错误对象
 * @param {string} fallback — 无法解析时使用的兜底文案
 * @returns {Promise<string>}
 */
export async function readApiError(error, fallback = '请求失败') {
  const data = error?.response?.data
  if (!data) return fallback
  if (typeof data === 'string') return data || fallback
  if (typeof Blob !== 'undefined' && data instanceof Blob) {
    try {
      const text = await data.text()
      if (!text) return fallback
      try {
        const parsed = JSON.parse(text)
        return parsed?.detail || parsed?.message || fallback
      } catch {
        return text.slice(0, 200) || fallback
      }
    } catch {
      return fallback
    }
  }
  return data.detail || data.message || fallback
}

/**
 * 触发浏览器下载一个 Blob，并尽量采用后端 `Content-Disposition` 里的文件名。
 *
 * 优先读取 `filename*=UTF-8''...`（中文文件名走 RFC 5987 编码，避免乱码），
 * 读不到才退回调用方给定的文件名。
 *
 * @param {Blob} blob
 * @param {string} fallbackName
 */
export function saveBlob(blob, fallbackName) {
  // 后端文件名来自 response 拦截器写在 Blob 上的 __filename（RFC 5987 已解码）
  const name = blob?.__filename || fallbackName
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  // 交给浏览器读取后再释放，立刻 revoke 在部分浏览器会导致下载中断
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}
