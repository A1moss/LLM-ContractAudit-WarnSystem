/**
 * workspaceLogic 单测（node:test）—— 合同详情 V2 的关键业务规则回归。
 *
 * 运行：
 *   cd frontend && node --test src/composables/workspaceLogic.test.mjs
 *
 * 覆盖的都是「错了会导致业务语义错」的规则：
 * - 会话分组互斥优先级（新增条款 vs 风险 vs 比对 vs 总体 vs 历史）
 * - 缺失型风险判定（不按 R 编号机械判断）
 * - DOCX 可导出口径（必须与后端 download_revised_docx 取数一致）
 * - 定位预检镜像（必须与后端 _pick_best_hit 同结论）
 * - session key 约定（改动会导致历史会话孤儿化）
 *
 * fixture 说明：标注「真实」的字符串取自开发库 contract.db 中 contract_id=92 的
 * audit_records / audit_reports 真实内容（只读查询），其余为保持字段形状的最小构造数据。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  OVERVIEW_KEY, CMP_PREFIX, SESSION_GROUPS,
  cnNo, cnToInt, parseHeadings, clipText,
  previewLocate, previewLocatable,
  cmpSessionKey, hasComparisonIssue, comparisonAction,
  displayInstruction, buildHistory,
  classifySessionGroup, isAddOnlyRevs, isMissingTypeRiskShape, sessionActionFor,
  addPositionOk, positionText, beforeAnchorOf,
  isExportableRevision, docxStateFor,
} from './workspaceLogic.js'

// ── 真实数据（contract_id=92）：R08 的 clause_position.original_text 逐字原文 ──
const REAL_R08_ANCHOR = '（6）履约验收标准：设备正常运行生产30日并验收合格后'
// ── 真实数据：R09 的 clause_text 是「合成描述」，不在合同正文中 ──
const REAL_R09_TEXT = '全文未出现不可抗力相关条款'

// ════════════════════════════════════════════════════════════════
// 1. 中文数字 / 标题解析（必须与后端 _cn_to_int / _int_to_cn / _parse_headings 一致）
// ════════════════════════════════════════════════════════════════
test('cnToInt/cnNo 与后端口径一致（含 十/十五/二十/九十九）', () => {
  assert.equal(cnToInt('一'), 1)
  assert.equal(cnToInt('十'), 10)
  assert.equal(cnToInt('十五'), 15)
  assert.equal(cnToInt('二十'), 20)
  assert.equal(cnToInt('五'), 5)
  assert.equal(cnToInt('九十九'), 99)
  assert.equal(cnToInt('5'), 5)       // 阿拉伯数字也必须支持（后端 _cn_to_int 支持）
  assert.equal(cnToInt('三十'), 30)
  assert.equal(cnToInt('X'), null)
  assert.equal(cnToInt(''), null)
  assert.equal(cnToInt(null), null)

  assert.equal(cnNo(5), '五')
  assert.equal(cnNo(10), '十')
  assert.equal(cnNo(15), '十五')
  assert.equal(cnNo(20), '二十')
  assert.equal(cnNo(0), '')
  assert.equal(cnNo(99), '九十九')
})

test('parseHeadings 识别「第X条」与「X、」，去重且按位置升序', () => {
  const text = '第一条 服务内容：乙方提供服务。\n第二条 价款：十万元。\n三、验收标准：由甲方组织。'
  const hs = parseHeadings(text)
  assert.deepEqual(hs.map((h) => h.num), [1, 2, 3])
  assert.equal(hs[0].title, '服务内容')
  assert.equal(hs[1].title, '价款')
  assert.equal(hs[2].title, '验收标准')
  assert.ok(hs[0].start < hs[1].start && hs[1].start < hs[2].start)
})

test('parseHeadings：真实合同 92 正文无「第X条」，返回空数组（不伪造条款编号）', () => {
  // 真实 92 号合同使用「（N）」子项编号，正文没有「第X条」结构 →
  // 这正是真实库 366/376 条风险 clause_no 为 null 的原因，前端必须接受空 headings。
  const realLike = '地点后(甲乙双方签订到货验收单)，达到付款条件起7日，支付合同总金额的30%\n' + REAL_R08_ANCHOR
  assert.deepEqual(parseHeadings(realLike), [])
})

// ════════════════════════════════════════════════════════════════
// 2. session key 约定（不得更改）
// ════════════════════════════════════════════════════════════════
test('session key 约定：总体固定、比对带 __cmp__ 前缀、风险用 AuditRecord.id', () => {
  assert.equal(OVERVIEW_KEY, '__overview__')
  assert.equal(CMP_PREFIX, '__cmp__')
  // 真实比对条款名（contract 92 的 clauses[0].title = 合同主体）
  assert.equal(cmpSessionKey({ title: '合同主体' }), '__cmp__合同主体')
  assert.equal(cmpSessionKey({ title: '' }), '__cmp__')
  assert.equal(cmpSessionKey(null), '__cmp__')
})

// ════════════════════════════════════════════════════════════════
// 3. 条款比对问题判定
// ════════════════════════════════════════════════════════════════
test('比对问题判定：missing → 新增；partial/有偏离/有补全 → 替换；covered 干净 → 不动', () => {
  const missing = { title: '不可抗力', status: 'missing', deviation: null, completion: '补充不可抗力条款' }
  const partial = { title: '违约责任', status: 'partial', deviation: '违约金过高', completion: null }
  const coveredWithDev = { title: '合同主体', status: 'covered', deviation: '甲方全称为占位符', completion: null }
  const coveredClean = { title: '合同标的', status: 'covered', deviation: null, completion: null }

  // hasComparisonIssue 只回答「是否需要进修改」，missing 由 caller 单独判（返回 false）
  assert.equal(hasComparisonIssue(missing), false)
  assert.equal(hasComparisonIssue(partial), true)
  assert.equal(hasComparisonIssue(coveredWithDev), true, 'covered 也可能有偏离 → 仍需可改')
  assert.equal(hasComparisonIssue(coveredClean), false)

  // 动作判定：missing 走 add_clause，其余走 replace
  assert.equal(comparisonAction(missing), 'add_clause')
  assert.equal(comparisonAction(partial), 'replace')
  assert.equal(comparisonAction(coveredWithDev), 'replace')
  assert.equal(comparisonAction(null), 'replace')
})

// ════════════════════════════════════════════════════════════════
// 4. 会话分组互斥优先级（Phase 4 的核心规则）
// ════════════════════════════════════════════════════════════════
test('分组优先级 1：__overview__ 恒为总体（即使全部 revision 都是 add_clause）', () => {
  const revs = [{ operation: 'add_clause' }]
  assert.equal(classifySessionGroup(OVERVIEW_KEY, revs, new Set()), 'overview')
})

test('分组优先级 2：纯 add_clause 会话归「新增条款」，优先于 __cmp__ / 风险 id', () => {
  const addRevs = [{ operation: 'add_clause' }, { operation: 'add_clause' }]
  // R09 风险会话：key = AuditRecord.id，且当前审核结果里存在 → 仍必须归「新增条款」
  assert.equal(classifySessionGroup('376', addRevs, new Set(['376'])), 'add')
  // 比对缺失会话：key = __cmp__+title → 仍必须归「新增条款」
  assert.equal(classifySessionGroup('__cmp__不可抗力', addRevs, new Set()), 'add')
})

test('分组优先级 2 的边界：混合会话（既有 replace 又有 add_clause）按 key 归类', () => {
  const mixed = [{ operation: 'replace' }, { operation: 'add_clause' }]
  assert.equal(classifySessionGroup('376', mixed, new Set(['376'])), 'risk')
  assert.equal(classifySessionGroup('__cmp__违约责任', mixed, new Set()), 'cmp')
})

test('分组优先级 3/4/5：__cmp__ → 比对；命中当前风险 id → 风险；其余 → 历史', () => {
  const replaceRevs = [{ operation: 'replace' }]
  assert.equal(classifySessionGroup('__cmp__验收标准', replaceRevs, new Set()), 'cmp')
  assert.equal(classifySessionGroup('376', replaceRevs, new Set(['376'])), 'risk')
  // 真实场景：重审后 AuditRecord.id 变了，旧会话的 id 已不在当前结果中 → 历史（不得硬编码成比对）
  assert.equal(classifySessionGroup('374', replaceRevs, new Set(['376'])), 'history')
  assert.equal(classifySessionGroup('374', [], new Set(['376'])), 'history')
  // 无比对上下文时，__cmp__ 前缀仍足以判定为比对来源
  assert.equal(classifySessionGroup('__cmp__付款条件', [], new Set()), 'cmp')
})

test('分组优先级 5：无法归类的 key 一律进历史，不猜来源', () => {
  assert.equal(classifySessionGroup('', [], new Set()), 'history')
  assert.equal(classifySessionGroup('__overview__x', [], new Set()), 'history')
})

test('SESSION_GROUPS 覆盖全部 5 个分组且顺序稳定', () => {
  assert.deepEqual(SESSION_GROUPS.map((g) => g.key), ['overview', 'risk', 'cmp', 'add', 'history'])
})

test('isAddOnlyRevs：空数组不算纯新增（避免 0 轮会话被塞进新增分组）', () => {
  assert.equal(isAddOnlyRevs([]), false)
  assert.equal(isAddOnlyRevs(null), false)
  assert.equal(isAddOnlyRevs([{ operation: 'add_clause' }]), true)
  assert.equal(isAddOnlyRevs([{ operation: 'replace' }, { operation: 'add_clause' }]), false)
})

// ════════════════════════════════════════════════════════════════
// 5. 缺失型风险判定（不按 R 编号机械判断）
// ════════════════════════════════════════════════════════════════
test('缺失型判定：真实 R09 合成描述 → 走新增；真实 R08 有锚点原文 → 走替换', () => {
  // 真实 R09：clause_text 是「全文未出现…」，无 clause_position → 缺失型
  assert.equal(isMissingTypeRiskShape(REAL_R09_TEXT, null), true)
  // 真实 R08：有审核阶段锚点原文 → 不是缺失型，可直接替换
  assert.equal(isMissingTypeRiskShape(REAL_R08_ANCHOR, REAL_R08_ANCHOR), false)
  // R08 也有 6 条是合成描述（真实数据）→ 同样必须判定为缺失型
  assert.equal(isMissingTypeRiskShape('全文未定义验收标准或验收方式', null), true)
  // R01 但无定位且文本不像原文 → 缺失型（不能按 R 编号机械判断）
  assert.equal(isMissingTypeRiskShape('缺少违约金上限约定', null), true)
  // 空文本 → 亦无法替换
  assert.equal(isMissingTypeRiskShape('', null), true)
})

test('sessionActionFor：四种会话语义矩阵', () => {
  assert.equal(sessionActionFor({ key: OVERVIEW_KEY }), 'overview')
  assert.equal(sessionActionFor({ key: '376', revs: [{ operation: 'add_clause' }] }), 'add_clause')
  assert.equal(sessionActionFor({ key: '__cmp__不可抗力', revs: [], cmpStatus: 'missing' }), 'add_clause')
  assert.equal(
    sessionActionFor({ key: '376', revs: [], riskMissingType: true }),
    'add_clause',
    'R09 风险：0 轮且无定位 → 必须走新增条款，而不是替换',
  )
  assert.equal(sessionActionFor({ key: '376', revs: [], riskMissingType: false }), 'replace')
  assert.equal(sessionActionFor({ key: '__cmp__违约责任', revs: [], cmpStatus: 'partial' }), 'replace')
  // 已有轮次的会话不再按「缺失型」改判（已存在真实修订）
  assert.equal(
    sessionActionFor({ key: '376', revs: [{ operation: 'replace' }], riskMissingType: true }),
    'replace',
  )
})

// ════════════════════════════════════════════════════════════════
// 6. DOCX 可导出口径（必须与后端 download_revised_docx 一致）
// ════════════════════════════════════════════════════════════════
test('DOCX 口径：scope=clause 全收；overview 只有 add_clause 收；overview+replace 永不写入', () => {
  assert.equal(isExportableRevision({ scope: 'clause', operation: 'replace' }), true)
  assert.equal(isExportableRevision({ scope: 'clause', operation: 'add_clause' }), true)
  assert.equal(isExportableRevision({ scope: 'overview', operation: 'add_clause' }), true)
  assert.equal(
    isExportableRevision({ scope: 'overview', operation: 'replace' }),
    false,
    '总体修改不会被写入 DOCX —— UI 不得暗示它会写回合同',
  )
  // 后端 getattr 兜底：缺失字段按 clause/replace 处理
  assert.equal(isExportableRevision({}), true)
  assert.equal(isExportableRevision(null), true)
})

test('docxStateFor 状态机：不支持格式 → 无修改 → 暂不能导出 → 可以导出', () => {
  // 阶段 1 起：PDF 与 DOCX 一样可导出（修订版统一输出 DOCX）
  assert.equal(docxStateFor({ storedPath: '/data/x.pdf', exportableCount: 3, blockerCount: 0 }).key, 'ready')
  // 图片本轮仍不支持
  assert.equal(docxStateFor({ storedPath: '/data/x.png', exportableCount: 3, blockerCount: 0 }).key, 'not_docx')
  assert.equal(docxStateFor({ storedPath: '/data/x.docx', exportableCount: 0, blockerCount: 0 }).key, 'none')
  assert.equal(docxStateFor({ storedPath: '/data/x.docx', exportableCount: 2, blockerCount: 1 }).key, 'blocked')
  assert.equal(docxStateFor({ storedPath: '/data/x.DOCX', exportableCount: 2, blockerCount: 0 }).key, 'ready')
  assert.equal(docxStateFor({ storedPath: '/data/x.PDF', exportableCount: 2, blockerCount: 0 }).key, 'ready')
  // blocked 文案必须带上条数，便于用户定位问题会话
  assert.match(docxStateFor({ storedPath: '/data/a.docx', exportableCount: 2, blockerCount: 3 }).text, /3 条/)
})

test('BUG：file_name 是显示名（无后缀）不能决定格式，必须以真实 stored_path 为准', () => {
  // 真实现场：file_name='测试合同文件'（无扩展名）+ stored_path 是 .docx
  const real = docxStateFor({
    storedPath: 'C:/data/71a1988e-eadc-4e71-a058-4147dd231ffb.docx',
    fileName: '测试合同文件',
    exportableCount: 1,
    blockerCount: 0,
  })
  assert.notEqual(real.key, 'not_docx', '真 DOCX 不得被判成非 Word')
  assert.equal(real.key, 'ready')

  // 反过来：file_name 恰好带 .docx，但真实文件是图片 → 必须以真实 stored_path 判定为不支持
  assert.equal(
    docxStateFor({ storedPath: '/data/x.png', fileName: '合同.docx', exportableCount: 2, blockerCount: 0 }).key,
    'not_docx',
  )
  // file_name 带 .docx 但真实文件是 PDF → 以 stored_path 为准（PDF 可导出）
  assert.equal(
    docxStateFor({ storedPath: '/data/x.pdf', fileName: '合同.docx', exportableCount: 2, blockerCount: 0 }).key,
    'ready',
  )

  // stored_path 缺失时才退回显示名（兼容历史数据）
  assert.equal(docxStateFor({ storedPath: '', fileName: '合同.docx', exportableCount: 1, blockerCount: 0 }).key, 'ready')
  assert.equal(docxStateFor({ storedPath: '', fileName: '测试合同文件', exportableCount: 1, blockerCount: 0 }).key, 'not_docx')
})

// ════════════════════════════════════════════════════════════════
// 7. 新增条款插入位置
// ════════════════════════════════════════════════════════════════
test('addPositionOk：只认 append 或可解析的 anchor（与后端 _insert_one 判据一致）', () => {
  assert.equal(addPositionOk({ append: true }), true)
  assert.equal(addPositionOk({ anchor: '五' }), true)
  assert.equal(addPositionOk({ anchor: '5' }), true)
  assert.equal(addPositionOk({ anchor: 'X' }), false)
  assert.equal(addPositionOk({}), false)
  assert.equal(addPositionOk(null), false)
  // 推荐位置为空时前端绝不默认 append → 未确认的位置必须判为不可用
  assert.equal(addPositionOk({ hint: '第五条之后' }), false)
})

test('positionText：优先 hint，其次 append/anchor，最后明确「未指定位置」', () => {
  assert.equal(positionText({ hint: '第五条之后', anchor: '五' }), '第五条之后')
  assert.equal(positionText({ append: true }), '追加到合同末尾')
  assert.equal(positionText({ anchor: '五' }), '第五条之后')
  assert.equal(positionText({}), '未指定位置')
})

test('beforeAnchorOf：「第X条之前」翻译为「第X-1条之后」；首条无法表达 → null', () => {
  const hs = [{ num: 1 }, { num: 2 }, { num: 3 }]
  assert.equal(beforeAnchorOf(hs, 3), 2)
  assert.equal(beforeAnchorOf(hs, 2), 1)
  assert.equal(beforeAnchorOf(hs, 1), null, '已是第一条 → 后端无法表达「之前」')
  assert.equal(beforeAnchorOf([], 2), null)
})

// ════════════════════════════════════════════════════════════════
// 8. 定位预检（镜像后端 _pick_best_hit）
// ════════════════════════════════════════════════════════════════
test('previewLocate：真实 R08 锚点原文在正文中唯一命中', () => {
  const text = '第十二条 付款：\n' + REAL_R08_ANCHOR + '\n第十三条 违约责任：逾期按日支付违约金。'
  const r = previewLocate(text, REAL_R08_ANCHOR)
  assert.equal(r.status, 'unique')
  assert.equal(r.hits.length, 1)
  assert.equal(previewLocatable(text, REAL_R08_ANCHOR), true)
})

test('previewLocate：多命中且恰有一处在「（N）」子项内 → disambiguated（与后端同结论）', () => {
  // 构造要点：只有第一处命中落在「（N）」子项 40 字窗口内；第二处必须远离任何（N）标记，
  // 否则按后端 _pick_best_hit 规则会判为「多命中无法消歧」（那是另一个用例）。
  const text =
    '（3）付款：支付合同总金额的30%。' +
    '前言：本条适用于全部采购项目，双方确认按下列支付方式分期结算，逾期部分另行处理协商解决。' +
    '违约责任：逾期超过30%的部分不予支付。'
  const r = previewLocate(text, '30%')
  assert.equal(r.hits.length, 2, '该 fixture 必须恰好两处命中')
  assert.equal(r.status, 'disambiguated')
  assert.equal(previewLocatable(text, '30%'), true)
})

test('previewLocate：多命中且无法消歧 → ambiguous（前端必须提示用户指定位置）', () => {
  const text = '第一条 违约金按30%计算。第二条 违约金按30%计算。'
  const r = previewLocate(text, '违约金按30%')
  assert.equal(r.status, 'ambiguous')
  assert.equal(previewLocatable(text, '违约金按30%'), false)
})

test('previewLocate：完全无命中 → none（含真实 R09 合成描述）', () => {
  const text = '第一条 服务内容：乙方按约定提供服务。'
  assert.equal(previewLocate(text, REAL_R09_TEXT).status, 'none')
  assert.equal(previewLocatable(text, REAL_R09_TEXT), false)
  assert.equal(previewLocate(text, '').status, 'none')
  assert.equal(previewLocate('', 'x').status, 'none')
})

test('previewLocate：前缀逐级缩短到 4 字（容忍 LLM 证据对原文的改写）', () => {
  const text = '第一条 履约验收标准：设备正常运行生产30日并验收合格。'
  // 证据被改写：真实原文前 6 字命中的探针应在缩短后定位到
  const r = previewLocate(text, '履约验收标准设备正常运行生产30日并验收')
  assert.notEqual(r.status, 'none')
  assert.equal(previewLocatable(text, '履约验收标准设备正常运行生产30日并验收'), true)
})

// ════════════════════════════════════════════════════════════════
// 9. instruction 上下文处理（不污染展示、不丢多轮）
// ════════════════════════════════════════════════════════════════
test('displayInstruction 剥离上下文块，只留用户要求；无标记时原样返回', () => {
  const s = '【当前修改依据】以下问题由「风险预警」发现：\n- 风险类型：R08\n\n【用户修改要求】\n请按上述修改建议修订本条款。'
  assert.equal(displayInstruction(s), '请按上述修改建议修订本条款。')
  assert.equal(displayInstruction('把违约金改为20%'), '把违约金改为20%')
  assert.equal(displayInstruction(''), '')
})

test('buildHistory：一轮一条，保留多轮顺序（绝不合并成一条）', () => {
  const revs = [
    { instruction: '【用户修改要求】\n第一轮要求', revised_clause: 'A1' },
    { instruction: '【用户修改要求】\n第二轮要求', revised_clause: 'A2' },
    { instruction: '第三轮要求', revised_clause: 'A3' },
  ]
  const h = buildHistory(revs)
  assert.equal(h.length, 3)
  assert.deepEqual(h.map((x) => x.instruction), ['第一轮要求', '第二轮要求', '第三轮要求'])
  assert.deepEqual(h.map((x) => x.revised_clause), ['A1', 'A2', 'A3'])
  assert.deepEqual(buildHistory([]), [])
})

// ════════════════════════════════════════════════════════════════
// 10. 文本裁剪
// ════════════════════════════════════════════════════════════════
test('clipText 折叠空白并截断', () => {
  assert.equal(clipText('a\n\nb   c', 10), 'a b c')
  assert.equal(clipText('0123456789', 4), '0123…')
  assert.equal(clipText(null, 4), '')
})
