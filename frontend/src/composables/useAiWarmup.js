/**
 * useAiWarmup — AI 服务预热状态（全局单例）
 *
 * 数据来源：GET /api/health 的 warmup.rag（idle | warming | ready | failed）。
 * 该接口只作为「AI 教练」的产品化状态来源；OCR 等其它字段一律忽略。
 * 轮询在模块顶层启动，全 App 共享同一份状态，路由切换不会创建第二个轮询。
 */
import { ref } from 'vue'
import request from '../utils/request.js'

/** 供 UI 观察的状态：hidden | warming | ready | failed */
export const state = ref('hidden')

/** 本次前端生命周期内 AI 是否曾经 ready 过（ready → hidden 之后仍保持 true） */
export const everReady = ref(false)

const POLL_INTERVAL = 3000 // 预热中：每 3 秒继续查询
const READY_HIDE_MS = 2500 // ready 展示 2.5 秒后隐藏
const MAX_CONSECUTIVE_FAILS = 3 // 连续失败 3 次才判定为 failed
const REQUEST_TIMEOUT = 1500

let pollTimer = null
let hideTimer = null
let inflight = false
let failCount = 0

function clearPollTimer() {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

function clearHideTimer() {
  if (hideTimer) {
    clearTimeout(hideTimer)
    hideTimer = null
  }
}

// 用 setTimeout 链代替 setInterval：请求慢于轮询间隔时也不会叠加多个在途请求
function schedulePoll(delay = POLL_INTERVAL) {
  clearPollTimer()
  pollTimer = setTimeout(tick, delay)
}

function onReady() {
  clearPollTimer()
  clearHideTimer()
  everReady.value = true
  state.value = 'ready'
  hideTimer = setTimeout(() => {
    hideTimer = null
    state.value = 'hidden' // everReady 保持 true
  }, READY_HIDE_MS)
}

function onFailed() {
  clearPollTimer()
  clearHideTimer()
  state.value = 'failed'
}

async function tick() {
  if (inflight) {
    // 已有在途请求：不并发，稍后再查（避免丢失轮询）
    schedulePoll(300)
    return
  }
  inflight = true

  let body = null
  try {
    // 响应拦截器已解包 response.data，这里直接就是响应 body
    body = await request.get('/health', { timeout: REQUEST_TIMEOUT })
    failCount = 0
  } catch {
    inflight = false
    failCount += 1
    if (failCount >= MAX_CONSECUTIVE_FAILS) {
      onFailed()
    } else {
      // 第 1、2 次失败保持当前状态，继续轮询
      schedulePoll()
    }
    return
  }
  inflight = false

  const rag = body?.warmup?.rag
  if (rag === 'ready') {
    onReady()
    return
  }
  if (rag === 'failed') {
    onFailed()
    return
  }
  // idle / warming（以及任何未知值）：视为仍在热身，继续轮询
  state.value = 'warming'
  schedulePoll()
}

/**
 * 重新检查：清掉现有轮询与定时器，失败计数归零，立即重新查询一次。
 * 不刷新页面，后续按真实状态继续运行。
 */
export function retry() {
  clearPollTimer()
  clearHideTimer()
  failCount = 0
  if (inflight) {
    // 已有在途请求：等它落地后再查一次，避免并发与重复 timer
    schedulePoll(200)
    return
  }
  tick()
}

// 模块顶层启动：整个前端生命周期只有这一套轮询
tick()
