import test from 'node:test'
import assert from 'node:assert/strict'

import { REVISE_TIMEOUT, COMPARE_TIMEOUT, FILE_TIMEOUT } from './timeouts.js'

// 本文件是「timeout 配置契约」regression：只验证各长耗时接口的客户端 timeout
// 高于当前已知同步处理耗时预算；未做端到端（真实网络 + 后端 + 前端）耗时验证。

// 后端最坏耗时基准（与后端实现一致）：
//   /revise：Leader→Follower→Self-QA 三次串行 LLM × 30s = 90s
//   /file：LibreOffice --convert-to timeout=60s
const BACKEND_REVISE_WORST_MS = 90_000
const BACKEND_FILE_WORST_MS = 60_000
const MARGIN_MS = 30_000

test('BUG-037: /revise 超时覆盖后端最坏 90s 并留 ≥30s 余量', () => {
  assert.ok(
    REVISE_TIMEOUT >= BACKEND_REVISE_WORST_MS + MARGIN_MS,
    `REVISE_TIMEOUT=${REVISE_TIMEOUT} 应 >= ${BACKEND_REVISE_WORST_MS + MARGIN_MS}`,
  )
})

test('BUG-037: /file docx 转换超时覆盖 60s 并留 ≥30s 余量', () => {
  assert.ok(
    FILE_TIMEOUT >= BACKEND_FILE_WORST_MS + MARGIN_MS,
    `FILE_TIMEOUT=${FILE_TIMEOUT} 应 >= ${BACKEND_FILE_WORST_MS + MARGIN_MS}`,
  )
})

test('BUG-037: /compare 按 N 块特性给足超时（不假定固定 90s）', () => {
  // compare 耗时随 N 块增长，不能按 /revise 的 90s 假定；给 5 分钟覆盖大合同
  assert.ok(COMPARE_TIMEOUT >= 300_000, `COMPARE_TIMEOUT=${COMPARE_TIMEOUT} 应 >= 300000`)
})

test('BUG-037: 三个长耗时接口超时均 > 默认 60s（确为按接口配置，非统一 60s）', () => {
  for (const [name, t] of Object.entries({ REVISE_TIMEOUT, COMPARE_TIMEOUT, FILE_TIMEOUT })) {
    assert.ok(t > 60_000, `${name}=${t} 应 > 60000`)
  }
})
