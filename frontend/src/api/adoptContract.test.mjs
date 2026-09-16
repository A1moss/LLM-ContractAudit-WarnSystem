/**
 * 采用态（adopted）前端契约测试（node:test）。
 *
 * 运行：cd frontend && node --test src/api/adoptContract.test.mjs
 *
 * 只做**源码契约**校验（不联网、不渲染）：确认「确认采用此版」走的是 adopt 端点，
 * 而不是再调一次 /revise —— 这正是本轮要修掉的错误语义。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const API_SRC = readFileSync(join(HERE, 'contract.js'), 'utf8')
const WS_SRC = readFileSync(join(HERE, '..', 'composables', 'useContractWorkspace.js'), 'utf8')
const WB_SRC = readFileSync(join(HERE, '..', 'views', 'contract-detail', 'RevisionWorkbench.vue'), 'utf8')

test('adopt API 指向后端真实路由，且用 POST', () => {
  assert.match(API_SRC, /export function adoptRevision\(/)
  assert.ok(
    API_SRC.includes('`/contracts/${id}/revisions/${revisionId}/adopt`'),
    'adoptRevision 必须指向 /contracts/{id}/revisions/{rid}/adopt',
  )
  const seg = API_SRC.slice(API_SRC.indexOf('export function adoptRevision'))
  assert.match(seg.slice(0, 400), /request\.post\(/)
})

test('「确认采用此版」不调用 /revise：confirmAdopt 只调 adoptRevision', () => {
  const i = WS_SRC.indexOf('async function confirmAdopt')
  assert.ok(i > 0, 'confirmAdopt 必须存在')
  const body = WS_SRC.slice(i, WS_SRC.indexOf('\n  }', i))
  assert.match(body, /adoptRevision\(/, 'confirmAdopt 必须调用 adoptRevision')
  assert.doesNotMatch(body, /submitRevise\(|reviseClause\(/, 'confirmAdopt 绝不能调用 /revise')
})

test('旧的 adoptCurrentVersion（采纳即再生成一轮）已不存在', () => {
  assert.doesNotMatch(WS_SRC, /adoptCurrentVersion/, '旧的错误语义函数必须被移除')
  assert.doesNotMatch(WB_SRC, /采纳此版/, 'UI 上不得再出现「采纳此版」')
})

test('右栏按钮文案为「确认采用此版」，并显示已采用状态', () => {
  assert.match(WB_SRC, /确认采用此版/)
  assert.match(WB_SRC, /shownRevIsAdopted/)
  assert.match(WB_SRC, /已采用/)
  assert.match(WB_SRC, /ws\.confirmAdopt\(s, currentRev\)/)
})

test('采用态来自后端 adopted 字段（刷新后可恢复）', () => {
  assert.match(WS_SRC, /function isRevisionAdopted\(rev\)[\s\S]{0,120}rev\.adopted === true/)
  assert.match(WS_SRC, /function sessionAdoptedRev\(s\)/)
  assert.match(WB_SRC, /r\.adopted === true/)
})

test('「继续调整」仍是聚焦输入框，不写库', () => {
  const i = WB_SRC.indexOf('function focusChatInput')
  assert.ok(i > 0)
  const body = WB_SRC.slice(i, WB_SRC.indexOf('\n}', i))
  assert.doesNotMatch(body, /adoptRevision\(|confirmAdopt\(|request\./)
})
