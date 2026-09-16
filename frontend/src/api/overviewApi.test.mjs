import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

import { OVERVIEW_PLAN_TIMEOUT } from './timeouts.js'

// 「总体修改会话」前端 API 层契约 regression：
// 只验证 URL 路径与后端路由字面对齐 + 长耗时接口给足超时，不做端到端联调。
// 后端路由定义见 backend/api/overview.py（router prefix="/contracts"）。

const HERE = dirname(fileURLToPath(import.meta.url))
const SRC = readFileSync(join(HERE, 'contract.js'), 'utf8')

const EXPECTED = {
  getRevisionOverview: '`/contracts/${id}/overview`',
  createOverviewProposal: '`/contracts/${id}/overview/plan`',
  listOverviewProposals: '`/contracts/${id}/overview/proposals`',
  getOverviewProposal: '`/contracts/${id}/overview/proposals/${proposalId}`',
  confirmOverviewProposal: '`/contracts/${id}/overview/confirm`',
}

test('总控会话 API 全部指向后端真实路由（overview.py）', () => {
  for (const [fn, path] of Object.entries(EXPECTED)) {
    assert.ok(SRC.includes(`export function ${fn}(`), `缺少导出函数 ${fn}`)
    assert.ok(SRC.includes(path), `${fn} 未使用后端真实路径 ${path}`)
  }
})

test('确认接口用 POST、总览/方案查询用 GET（不误用动词）', () => {
  assert.ok(/export function createOverviewProposal[\s\S]{0,400}?request\.post\(/.test(SRC))
  assert.ok(/export function confirmOverviewProposal[\s\S]{0,600}?request\.post\(/.test(SRC))
  assert.ok(/export function getRevisionOverview[\s\S]{0,300}?request\.get\(/.test(SRC))
  assert.ok(/export function listOverviewProposals[\s\S]{0,300}?request\.get\(/.test(SRC))
  assert.ok(/export function getOverviewProposal[\s\S]{0,300}?request\.get\(/.test(SRC))
})

test('/overview/plan 是单次 LLM 但输入含整份合同，超时必须远大于默认 60s', () => {
  assert.ok(OVERVIEW_PLAN_TIMEOUT > 60_000, `OVERVIEW_PLAN_TIMEOUT=${OVERVIEW_PLAN_TIMEOUT}`)
  assert.ok(OVERVIEW_PLAN_TIMEOUT >= 180_000, `OVERVIEW_PLAN_TIMEOUT=${OVERVIEW_PLAN_TIMEOUT} 应 >= 180000`)
  assert.ok(/createOverviewProposal[\s\S]{0,400}?OVERVIEW_PLAN_TIMEOUT/.test(SRC))
})

test('前端不得把方案直接当作 DOCX 修改（不得出现"方案写回合同"的暗示）', () => {
  // 只有确认接口产出修订；createOverviewProposal 的响应是方案，不是合同修改
  const plan = SRC.slice(SRC.indexOf('export function createOverviewProposal'),
                         SRC.indexOf('export function listOverviewProposals'))
  assert.ok(!/downloadRevisedDocx|revised-docx/.test(plan), '生成方案阶段不得触发 DOCX 导出')
})
