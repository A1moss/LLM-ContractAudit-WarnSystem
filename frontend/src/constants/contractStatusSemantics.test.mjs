import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * 「合同状态」语义修正（P1）守卫测试。
 *
 * `contracts.status` 是**合同生命周期状态**（已上传/已解析/审核中/审核完成/待验收/已验收），
 * 不是正式审批状态。本项目不新增 auditor / report_number / report_version / approval_status
 * 等数据库不存在的字段，只做命名修正：
 *
 *   - 任何**模板文案**都不得再把它叫成「审核状态」「审批状态」；
 *   - 统一使用「合同状态」，中文名唯一来源是 constants/reportTokens.js 的
 *     contractStatusLabel（不在模板里另写一份状态表）。
 */

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..')

function walk(dir) {
  const out = []
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) out.push(...walk(p))
    else if (/\.(vue|js)$/.test(name) && !name.endsWith('.test.mjs')) out.push(p)
  }
  return out
}

/** 去掉 <script> 块，只保留模板 + 样式（注释允许解释旧命名，模板文案不允许） */
function templateOf(vueSource) {
  return vueSource.replace(/<script[\s\S]*?<\/script>/g, '')
}

/** 去掉 JS 注释，只保留可执行代码 */
function codeOf(jsSource) {
  return jsSource
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1')
}

test('P1: 所有 Vue 模板文案不得再把 contracts.status 叫「审核状态」/「审批状态」', () => {
  const offenders = []
  for (const file of walk(SRC)) {
    if (!file.endsWith('.vue')) continue
    const tpl = templateOf(readFileSync(file, 'utf8'))
    if (/审核状态|审批状态/.test(tpl)) {
      offenders.push(relative(SRC, file))
    }
  }
  assert.deepEqual(offenders, [], `以下模板仍在使用旧命名：${offenders.join(', ')}`)
})

test('P1: 报告相关页面统一显示「合同状态」', () => {
  // AuditReportDetail / ReportPanel 的「合同状态」文案由 script 中的 computed 拼出
  // （模板里渲染 headMeta / statusLabel），因此这里对整份源码断言。
  const wholeFile = {
    'views/AuditReportDetail.vue': /合同状态/,
    'views/ContractList.vue': /label="合同状态"/,
    'views/contract-detail/ReportPanel.vue': /合同状态/,
  }
  for (const [rel, re] of Object.entries(wholeFile)) {
    const src = readFileSync(join(SRC, rel), 'utf8')
    assert.match(src, re, `${rel} 必须以「合同状态」呈现 contracts.status`)
  }
})

test('P1: 状态中文名的唯一来源仍是 reportTokens.contractStatusLabel', () => {
  const tokens = readFileSync(join(SRC, 'constants/reportTokens.js'), 'utf8')
  assert.match(tokens, /export function contractStatusLabel/)
  assert.match(tokens, /const CONTRACT_STATUS_LABELS = \{/)

  // 报告页面必须复用该函数，而不是在模板/局部再写一份状态表
  for (const rel of ['views/AuditReportDetail.vue', 'views/contract-detail/ReportPanel.vue']) {
    const src = readFileSync(join(SRC, rel), 'utf8')
    assert.match(codeOf(src), /contractStatusLabel/, `${rel} 必须复用 contractStatusLabel`)
  }
})

test('P1: 没有为语义修正凭空新增审批相关字段', () => {
  const forbidden = ['approval_status', 'report_number', 'report_version', 'auditor_name', 'approved_by']
  const offenders = []
  for (const file of walk(SRC)) {
    const code = codeOf(readFileSync(file, 'utf8'))
    for (const f of forbidden) {
      if (new RegExp(`\\b${f}\\b`).test(code)) offenders.push(`${relative(SRC, file)}: ${f}`)
    }
  }
  assert.deepEqual(offenders, [], `不得新增虚构审批字段：${offenders.join(', ')}`)
})
