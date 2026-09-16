/**
 * 总体修改（整份合同总控台）前端规则单测（node:test）。
 *
 * 运行：
 *   cd frontend && node --test src/composables/overviewLogic.test.mjs
 *
 * 这一组测试盯的是「错了会导致业务语义错」的地方，尤其是 V2.2 明令区分的：
 *   - 已纳入方案 ≠ 已确认修改（前者不得计入「已确认修改 N」）
 *   - 「已确认修改 N」只统计后端判定 exportable 的专项会话
 *   - 参考条款拿不到就退化为两栏（绝不伪造标准条款）
 *   - 多候选位置必须用户点选（前端不得自动选第一个）
 */
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  MODIFICATION_STATES,
  modificationStateFor,
  countConfirmedSessions,
  sessionExportable,
  proposalItemKind,
  proposalItemTitle,
  canIncludeProposalItem,
  proposalBlockingText,
  referenceClauseText,
  comparisonLayout,
  locateCandidates,
  locateNeedsChoice,
  roundDiff,
  LOCATION_STATES,
  locationStateFor,
  hasReliableLocation,
} from './workspaceLogic.js'

// ════════════════════════════════════════════════════════════════
// 1. 修改点状态机
// ════════════════════════════════════════════════════════════════

test('修改点四态：未纳入 → 已纳入方案 → 专项处理中 → 已确认修改', () => {
  // 什么都没做
  assert.equal(modificationStateFor({}).key, 'pending')
  // 总控台纳入，但目标会话还没有任何修订 → 已纳入方案（待专项处理）
  assert.equal(modificationStateFor({ included: true }).key, 'included')
  // 纳入且目标会话已有 AI 修订，但尚未具备可写入条件 → 专项处理中
  assert.equal(modificationStateFor({ included: true, hasRevisions: true }).key, 'processing')
  // 后端判定具备写入条件 → 已确认修改（最高优先级）
  assert.equal(modificationStateFor({ included: true, hasRevisions: true, exportable: true }).key, 'confirmed')
  assert.equal(modificationStateFor({ exportable: true }).key, 'confirmed')
})

test('状态标签与色点符合 V2.2（绿=已确认 / 黄=已纳入 / 灰=未纳入）', () => {
  assert.equal(MODIFICATION_STATES.confirmed.dot, 'green')
  assert.equal(MODIFICATION_STATES.included.dot, 'yellow')
  assert.equal(MODIFICATION_STATES.pending.dot, 'grey')
  assert.equal(MODIFICATION_STATES.included.label, '已纳入方案（待专项处理）')
  assert.equal(MODIFICATION_STATES.confirmed.label, '已确认修改')
})

// ════════════════════════════════════════════════════════════════
// 2. 「已确认修改 N」的口径（V2.2 2.2 的核心约束）
// ════════════════════════════════════════════════════════════════

const OVERVIEW_SESSIONS = [
  { key: '11', kind: 'risk', export: { exportable: true, applied: 1, blocker: '' } },
  { key: '__cmp__验收标准', kind: 'cmp', export: { exportable: false, applied: 0, blocker: '有 1 条修改尚未建立可靠原文定位' } },
  { key: '__cmp__付款条件', kind: 'cmp', export: { exportable: true, applied: 2, blocker: '' } },
  // 总体会话自身的 replace 讨论稿：永不写入 DOCX，也不算"具体修改"
  { key: '__overview__', kind: 'overview', export: { exportable: false, applied: 0, blocker: '记录不参与修订版合同导出' } },
]

test('「已确认修改 N」= 后端判定 exportable 的专项会话数', () => {
  assert.equal(countConfirmedSessions(OVERVIEW_SESSIONS), 2)
})

test('总体会话自身的讨论稿不计入「已确认修改」', () => {
  const onlyOverview = [{ key: '__overview__', kind: 'overview', export: { exportable: true } }]
  assert.equal(countConfirmedSessions(onlyOverview), 0)
})

test('「已纳入方案」不是「已确认修改」：没有任何 exportable 会话时计数必须为 0', () => {
  // 场景：用户在总控台把 3 项都点了「纳入方案」，但还没进专项会话确认
  const notConfirmed = [
    { key: '11', kind: 'risk', export: { exportable: false, blocker: '有 1 条修改尚未建立可靠原文定位' } },
    { key: '12', kind: 'risk', export: { exportable: false, blocker: '有 1 条修改尚未建立可靠原文定位' } },
    { key: '13', kind: 'risk', export: { exportable: false, blocker: '有 1 条修改尚未建立可靠原文定位' } },
  ]
  assert.equal(countConfirmedSessions(notConfirmed), 0)
  // 逐项核对：这些会话的状态是 pending/included/processing，绝不是 confirmed
  for (const f of [{}, { included: true }, { included: true, hasRevisions: true }]) {
    assert.notEqual(modificationStateFor(f).key, 'confirmed')
  }
})

test('countConfirmedSessions 对异常输入安全（null / 非数组 / 缺 export）', () => {
  assert.equal(countConfirmedSessions(null), 0)
  assert.equal(countConfirmedSessions(undefined), 0)
  assert.equal(countConfirmedSessions('nope'), 0)
  assert.equal(countConfirmedSessions([{ key: '1' }, null]), 0)
})

test('sessionExportable 只看对应会话，找不到会话一律 false', () => {
  assert.equal(sessionExportable(OVERVIEW_SESSIONS, '11'), true)
  assert.equal(sessionExportable(OVERVIEW_SESSIONS, '__cmp__验收标准'), false)
  assert.equal(sessionExportable(OVERVIEW_SESSIONS, '404'), false)
  assert.equal(sessionExportable(null, '11'), false)
})

// ════════════════════════════════════════════════════════════════
// 3. 方案项
// ════════════════════════════════════════════════════════════════

test('方案项类型与标题用用户语言（不暴露 operation 枚举）', () => {
  assert.equal(proposalItemKind({ operation: 'add_clause' }), '新增条款')
  assert.equal(proposalItemKind({ operation: 'replace' }), '条款修改')
  assert.equal(proposalItemTitle({ clause_no: '3' }), '第三条')
  assert.equal(proposalItemTitle({ clause_no: '', original_quote: '违约金不得超过合同总价的百分之二十' }), '违约金不得超过合同总价的百分之二十')
  assert.equal(proposalItemTitle({ clause_no: '', original_quote: '', operation: 'add_clause' }), '新增条款')
})

test('只有后端已定位（resolved=true）的方案项才允许「纳入方案」', () => {
  assert.equal(canIncludeProposalItem({ resolved: true }), true)
  assert.equal(canIncludeProposalItem({ resolved: false }), false)
  assert.equal(canIncludeProposalItem(null), false)
})

test('未定位项的提示原样使用后端 blocking_reason', () => {
  const it = { resolved: false, blocking_reason: '缺少条款逐字原文，无法建立可靠定位，请指定要修改的条款位置' }
  assert.equal(proposalBlockingText(it), it.blocking_reason)
  assert.match(proposalBlockingText({}), /尚未建立可靠定位/)
})

// ════════════════════════════════════════════════════════════════
// 4. 条款比对的参考条款（有真实数据才三栏）
// ════════════════════════════════════════════════════════════════

const TEMPLATES = [
  {
    id: 1,
    contract_type: '买卖合同',
    clauses: [
      { title: '验收标准', content: '验收应采用人工审核方式，验收合格后30日内出具验收书。', priority: 'required' },
      { title: '付款条件', content: '合同签订后10日内支付30%。', priority: 'required' },
    ],
  },
]

test('参考条款从标准条款模板按 title 精确匹配取正文', () => {
  assert.equal(
    referenceClauseText(TEMPLATES, '验收标准'),
    '验收应采用人工审核方式，验收合格后30日内出具验收书。',
  )
})

test('没有模板 / 标题不匹配 → 返回空串（前端不得伪造标准条款）', () => {
  assert.equal(referenceClauseText([], '验收标准'), '')
  assert.equal(referenceClauseText(null, '验收标准'), '')
  assert.equal(referenceClauseText(TEMPLATES, '不存在的条款'), '')
  assert.equal(referenceClauseText(TEMPLATES, ''), '')
})

test('clauses 为对象形态（{标题: 正文}）时也能取到，且不把 AI 建议当标准条款', () => {
  const objTemplates = [{ clauses: { 验收标准: '标准正文', 付款条件: { title: '付款条件', content: '付款标准正文' } } }]
  assert.equal(referenceClauseText(objTemplates, '验收标准'), '标准正文')
  // 「补全建议」字段不参与参考条款取值（它不是标准条款正文）
  const row = { title: '验收标准', status: 'partial', completion: 'AI 建议的补全内容' }
  const ref = referenceClauseText([], row.title)
  assert.equal(ref, '')
  assert.equal(comparisonLayout(row, ref), 'two')
})

test('比对区布局：有真实参考条款才三栏；missing/covered 走专门分支', () => {
  assert.equal(comparisonLayout({ status: 'partial' }, '标准正文'), 'three')
  assert.equal(comparisonLayout({ status: 'partial' }, ''), 'two')
  assert.equal(comparisonLayout({ status: 'missing' }, '标准正文'), 'missing')
  assert.equal(comparisonLayout({ status: 'covered' }, '标准正文'), 'covered')
})

test('missing 状态解析为"合同中缺少这条款"（引导去新增），不产生参考条款栏', () => {
  assert.equal(comparisonLayout({ status: 'missing' }, ''), 'missing')
})

// ════════════════════════════════════════════════════════════════
// 5. 定位候选：多候选必须用户点选
// ════════════════════════════════════════════════════════════════

const CAND_A = { start: 10, end: 30, clause_no: 3, clause_title: '违约责任', original_text: '违约金上限为30%' }
const CAND_B = { start: 88, end: 108, clause_no: 5, clause_title: '违约责任', original_text: '违约金上限为30%' }

test('多候选：全部作为候选项返回，且没有任何一项被标记为已选', () => {
  const locate = { found: false, candidates: [CAND_A, CAND_B], reason: '原文出现 2 处且无法唯一消歧，请从候选中选择' }
  const list = locateCandidates(locate)
  assert.equal(list.length, 2)
  assert.equal(list.every((c) => c.chosen === false), true)
  assert.equal(locateNeedsChoice(locate), true)
})

test('found=true（唯一命中）也仍然渲染为候选项，必须用户点确认', () => {
  const locate = { found: true, candidates: [CAND_A], original_text: CAND_A.original_text, start: 10, end: 30, clause_no: 3 }
  const list = locateCandidates(locate)
  assert.equal(list.length, 1)
  // 只是"确认"而不是"多选一"，但依旧需要用户点选（chosen 仅作展示标记）
  assert.equal(locateNeedsChoice(locate), false)
  assert.equal(list[0].original_text, CAND_A.original_text)
})

test('候选去重（相同原文 + 相同起点只保留一条）且无 original_text 的候选被丢弃', () => {
  const locate = { found: true, original_text: CAND_A.original_text, start: 10, end: 30, candidates: [CAND_A, { start: 10, original_text: CAND_A.original_text }, { start: 1, original_text: '' }] }
  assert.equal(locateCandidates(locate).length, 1)
})

test('locateCandidates 对空结果安全', () => {
  assert.deepEqual(locateCandidates(null), [])
  assert.deepEqual(locateCandidates({ found: false, candidates: [] }), [])
  assert.equal(locateNeedsChoice(null), false)
})

// ════════════════════════════════════════════════════════════════
// 6. 历史轮次的对比（V2.2 第七节最低要求）
// ════════════════════════════════════════════════════════════════

test('roundDiff：每轮的"原文"是该轮写入的 clause_text，修改后是 revised_clause', () => {
  assert.deepEqual(
    roundDiff({ clause_text: '违约金为30%', revised_clause: '违约金为20%' }),
    { before: '违约金为30%', after: '违约金为20%' },
  )
  assert.deepEqual(roundDiff(null), { before: '', after: '' })
  // 多轮链条：第 2 轮的"原文"是第 1 轮的修订稿
  const r1 = { clause_text: 'A', revised_clause: 'A1' }
  const r2 = { clause_text: 'A1', revised_clause: 'A2' }
  assert.equal(roundDiff(r2).before, roundDiff(r1).after)
})

// ════════════════════════════════════════════════════════════════
// 7. 右栏定位三态（统一左栏徽标与右栏工作区的口径）
// ════════════════════════════════════════════════════════════════

test('定位三态：无任何线索 → 未定位；仅预检/审核锚点 → 可定位但未确认', () => {
  assert.equal(locationStateFor({}).key, 'unlocated')
  // 预检可定位 ≠ 用户已确认
  assert.equal(locationStateFor({ locatable: true }).key, 'locatable')
  // 审核阶段锚点也只是"系统能找到"，不是用户确认
  assert.equal(locationStateFor({ autoAnchor: '第三条 违约责任' }).key, 'locatable')
})

test('定位三态：用户确认候选 / 已有落库锚点 → 已确认定位', () => {
  assert.equal(locationStateFor({ confirmedAnchor: '第三条 违约责任' }).key, 'confirmed')
  assert.equal(locationStateFor({ dbAnchor: '第三条 违约责任' }).key, 'confirmed')
  // 已落库锚点优先级高于预检与审核锚点
  assert.equal(
    locationStateFor({ dbAnchor: 'A', autoAnchor: 'B', locatable: true, confirmedAnchor: 'C' }).key,
    'confirmed',
  )
})

test('「重新指定位置」强制回到未定位工作区（优先级最高，且不改动数据）', () => {
  const withEverything = {
    relocateMode: true,
    confirmedAnchor: 'A',
    dbAnchor: 'B',
    autoAnchor: 'C',
    locatable: true,
  }
  assert.equal(locationStateFor(withEverything).key, 'unlocated')
  assert.equal(hasReliableLocation(locationStateFor(withEverything).key), false)
})

test('hasReliableLocation：confirmed / locatable 进已定位工作区，只有 unlocated 才显示定位方式', () => {
  assert.equal(hasReliableLocation('confirmed'), true)
  assert.equal(hasReliableLocation('locatable'), true)
  assert.equal(hasReliableLocation('unlocated'), false)
  assert.equal(hasReliableLocation(null), false)
  assert.equal(hasReliableLocation(undefined), false)
})

test('三态标签用用户语言且互不相同（不暴露预检/锚点等术语）', () => {
  const labels = Object.values(LOCATION_STATES).map((s) => s.label)
  assert.deepEqual(labels, ['未定位', '可定位但未确认', '已确认定位'])
  for (const l of labels) {
    assert.doesNotMatch(l, /预检|锚点|anchor|preview/i)
  }
  assert.equal(LOCATION_STATES.unlocated.dot, 'grey')
  assert.equal(LOCATION_STATES.locatable.dot, 'blue')
  assert.equal(LOCATION_STATES.confirmed.dot, 'green')
})
