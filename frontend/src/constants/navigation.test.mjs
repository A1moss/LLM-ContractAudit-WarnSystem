/**
 * 导航/交互契约测试（node:test）—— 纯逻辑 + 源码契约，不联网、不渲染。
 *
 * 运行：cd frontend && node --test src/constants/navigation.test.mjs
 *
 * 覆盖本轮 4 项导航优化的**可测部分**：
 *   ① 完整报告页悬浮「返回审核报告」
 *   ② 合同详情处理 `?tab=` 落位
 *   ③ 风险卡「上一项 / 下一项风险」
 *   ④ 深链滚动定位（scroll-margin-top + block:'start'）
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import {
  PANE, TAB_QUERY_ALIASES, TAB_QUERY_VALUES,
  paneFromQueryTab, contractDetailPath, riskAnchorId, riskAnchorHash, riskIdFromHash,
  RETURN_SNAPSHOT_TAB, returnSnapshotKey, saveReturnSnapshot, peekReturnSnapshot, takeReturnSnapshot,
} from './navigation.js'

const HERE = dirname(fileURLToPath(import.meta.url))
const DETAIL_SRC = readFileSync(join(HERE, '..', 'views', 'ContractDetail.vue'), 'utf8')
const REPORT_SRC = readFileSync(join(HERE, '..', 'views', 'AuditReportDetail.vue'), 'utf8')

/** node 环境没有 sessionStorage：装一个最小 stub（模块内是"调用时"读取，故此处装即生效） */
function installSessionStorage() {
  const map = new Map()
  globalThis.sessionStorage = {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
    clear: () => map.clear(),
  }
  return map
}

// ── ① 别名表：?tab=audit 必须落在「审核报告」（内部 pane 名 report） ──
test('?tab=audit 映射到审核报告 Tab（内部 pane 名 report），不是风险详情', () => {
  assert.equal(paneFromQueryTab('audit'), PANE.report)
  assert.equal(PANE.report, 'report')
  assert.equal(PANE.risks, 'audit', '内部「风险详情」pane 名仍是 audit —— 两者同名但语义不同')
  assert.notEqual(paneFromQueryTab('audit'), PANE.risks)
})

test('paneFromQueryTab：大小写/空白容错，未知值与空值返回空串（→ 保持默认行为）', () => {
  assert.equal(paneFromQueryTab(' AUDIT '), PANE.report)
  assert.equal(paneFromQueryTab('report'), PANE.report)
  assert.equal(paneFromQueryTab('text'), PANE.text)
  assert.equal(paneFromQueryTab('compare'), PANE.compare)
  assert.equal(paneFromQueryTab('revise'), PANE.revise)
  for (const bad of ['', '   ', undefined, null, 'nope', 0]) {
    assert.equal(paneFromQueryTab(bad), '', `${JSON.stringify(bad)} 必须返回空串`)
  }
})

test('paneFromQueryTab：重复参数（数组）取第一个，不抛错', () => {
  assert.equal(paneFromQueryTab(['audit', 'text']), PANE.report)
  assert.equal(paneFromQueryTab([]), '')
})

test('别名表覆盖的取值集合稳定（改动需同步本测试）', () => {
  assert.deepEqual(TAB_QUERY_VALUES.sort(), ['audit', 'compare', 'report', 'revise', 'text'])
  assert.equal(Object.keys(TAB_QUERY_ALIASES).length, 5)
})

// ── ①② 路径与锚点工具 ──
test('contractDetailPath：带 tab 生成 ?tab=audit，无 tab 保持干净路径', () => {
  assert.equal(contractDetailPath(109, 'audit'), '/contracts/109?tab=audit')
  assert.equal(contractDetailPath('109', ''), '/contracts/109')
  assert.equal(contractDetailPath(109), '/contracts/109', '缺省 tab 不得拼出空 query')
  assert.equal(contractDetailPath('', 'audit'), '/contracts')
  assert.equal(contractDetailPath(null, 'audit'), '/contracts')
})

test('风险锚点 id / hash 同源，且能反解；非风险锚点返回空串', () => {
  assert.equal(riskAnchorId(395), 'risk-395')
  assert.equal(riskAnchorHash(395), '#risk-395')
  assert.equal(riskIdFromHash('#risk-395'), '395')
  for (const other of ['', '#', '#clauses', '#risk-', '#risk-abc', '#risk-395x', undefined, null]) {
    assert.equal(riskIdFromHash(other), '', `${JSON.stringify(other)} 不应被当成风险锚点`)
  }
})

// ── ② ContractDetail.vue：只处理导航状态 ──
test('ContractDetail 读取 query.tab 并调用 workspace 既有 setTab 落位', () => {
  assert.match(DETAIL_SRC, /from '\.\.\/constants\/navigation\.js'/)
  assert.match(DETAIL_SRC, /paneFromQueryTab/)
  assert.match(DETAIL_SRC, /watch\(\[\(\) => route\.params\.id, \(\) => route\.query\.tab\], applyTabFromQuery, \{ immediate: true \}\)/)
  assert.match(DETAIL_SRC, /const pane = paneFromQueryTab\(route\.query\.tab\)/)
  assert.match(DETAIL_SRC, /if \(pane\) ws\.setTab\(pane\)/, '只在能识别出 Tab 时才切换，否则保持默认')
})

test('ContractDetail 的 Tab 落位不触碰数据加载 / 定位逻辑', () => {
  const i = DETAIL_SRC.indexOf('function applyTabFromQuery')
  const body = DETAIL_SRC.slice(i, DETAIL_SRC.indexOf('\nwatch(', i))
  assert.doesNotMatch(body, /loadAll|loadAuditResult|loadComparison|loadRevisions|loadReport|loadContract/, '不得触发任何数据加载')
  assert.doesNotMatch(body, /riskRanges|clause_position|focusRiskId/, '不得触碰风险定位数据')
})

// ── ① AuditReportDetail.vue：悬浮返回 ──
test('完整报告页有悬浮「返回审核报告」，指向 /contracts/:id?tab=audit', () => {
  assert.match(REPORT_SRC, /class="rp-back"/)
  assert.match(REPORT_SRC, /返回审核报告/)
  assert.match(REPORT_SRC, /function backToAuditTab\(\)[\s\S]{0,200}router\.push\(contractDetailPath\(contractId\.value, 'audit'\)\)/)
  assert.match(REPORT_SRC, /\.rp-back \{[\s\S]{0,120}position: fixed;[\s\S]{0,120}bottom: 24px;/)
  assert.match(REPORT_SRC, /\.rp-back, \.rp-risk-nav \{ display: none !important; \}/, '打印时隐藏导航元素')
})

// ── ③ 风险卡上一项 / 下一项 ──
test('风险卡按页面实际展示顺序（分组后平铺）导航', () => {
  assert.match(REPORT_SRC, /const flatRisks = computed\(\(\) => riskGroups\.value\.flatMap\(\(g\) => g\.items\)\)/)
  assert.match(REPORT_SRC, /function riskIndex\(id\)/)
  assert.match(REPORT_SRC, /function canGotoRisk\(id, delta\)/)
})

test('上一项/下一项按钮：首项禁用上一项、末项禁用下一项，且只滚动不改 URL', () => {
  assert.match(REPORT_SRC, /:disabled="!canGotoRisk\(v\.id, -1\)"/)
  assert.match(REPORT_SRC, /:disabled="!canGotoRisk\(v\.id, 1\)"/)
  assert.match(REPORT_SRC, /← 上一项风险/)
  assert.match(REPORT_SRC, /下一项风险 →/)
  const i = REPORT_SRC.indexOf('function gotoRiskOffset')
  const body = REPORT_SRC.slice(i, REPORT_SRC.indexOf('\n}', i))
  assert.doesNotMatch(body, /router\.|location\.hash|history\./, '风险卡间跳转不得改 URL / 产生历史记录')
  assert.match(body, /scrollToRiskCard\(target\.id, true\)/)
})

test('风险导航只在风险卡之间（不跳条款完整性/合同原文）', () => {
  const i = REPORT_SRC.indexOf('const flatRisks')
  const body = REPORT_SRC.slice(i, REPORT_SRC.indexOf('function scrollToRiskCard', i))
  assert.doesNotMatch(body, /clauses|comparison|crossRisks|pdf|original/i, '风险导航数据源只允许 riskGroups')
})

// ── ④ 深链定位视觉位置 ──
test('风险卡有 scroll-margin-top:80px，深链用 block:start 定位', () => {
  assert.match(REPORT_SRC, /\.rp-risk \{[\s\S]{0,320}scroll-margin-top: 80px;/)
  assert.match(REPORT_SRC, /function scrollToRiskCard\(riskId, smooth = false\)/)
  assert.match(REPORT_SRC, /scrollIntoView\(\{ block: 'start', behavior: smooth \? 'smooth' : 'auto' \}\)/)
  assert.match(REPORT_SRC, /const riskId = riskIdFromHash\(route\.hash\)/)
  assert.match(REPORT_SRC, /scrollToRiskCard\(riskId, false\)/)
})

test('风险卡 id 由 riskAnchorId 统一生成（与深链解析同源）', () => {
  assert.match(REPORT_SRC, /:id="riskAnchorId\(v\.id\)"/)
  assert.doesNotMatch(REPORT_SRC, /:id="`risk-\$\{v\.id\}`"/, '不得再手写锚点格式')
})

test('hash 变化只在已挂载页面上重新滚动，不产生导航', () => {
  assert.match(REPORT_SRC, /watch\(\(\) => route\.hash, \(\) => nextTick\(scrollToHash\)\)/)
})

// ════════════════════════════════════════════════════════════════
// 本轮新增：① 醒目的悬浮返回按钮
// ════════════════════════════════════════════════════════════════
test('悬浮返回按钮为蓝色实心主按钮（同一主色 token）+ 白色文字 + 指定尺寸', () => {
  assert.match(REPORT_SRC, /class="rp-back-btn"/)
  assert.match(REPORT_SRC, /rp-back-arrow/)
  assert.match(REPORT_SRC, /<ArrowLeft \/>/)
  assert.match(REPORT_SRC, /返回审核报告/)
  const i = REPORT_SRC.indexOf('.rp-back-btn {')
  assert.ok(i > 0, '.rp-back-btn 样式必须存在')
  const rule = REPORT_SRC.slice(i, REPORT_SRC.indexOf('}', i))
  assert.match(rule, /padding: 12px 22px;/)
  assert.match(rule, /font-size: 14px;/)
  assert.match(rule, /border-radius: 8px;/)
  assert.match(rule, /color: #fff;/)
  assert.match(rule, /background: var\(--el-color-primary/, '主色必须用与主按钮同一个 theme token')
  assert.match(rule, /box-shadow: 0 4px 16px rgba\(0, 0, 0, \.15\);/)
})

test('悬浮返回定位/层级/移动端/hover/打印 均符合要求', () => {
  const wrap = REPORT_SRC.slice(REPORT_SRC.indexOf('.rp-back {'), REPORT_SRC.indexOf('.rp-back-btn {') + 4)
  assert.match(wrap, /position: fixed;/)
  assert.match(wrap, /right: 24px;/)
  assert.match(wrap, /bottom: 24px;/)
  assert.match(wrap, /z-index: 1000;/)
  // hover 主色加深
  const hover = REPORT_SRC.slice(REPORT_SRC.indexOf('.rp-back-btn:hover'), REPORT_SRC.indexOf('.rp-back-btn:focus-visible'))
  assert.match(hover, /background: var\(--el-color-primary-dark-2/)
  // 移动端边距
  assert.match(REPORT_SRC, /@media \(max-width: 900px\) \{\s*\.rp-back \{ right: 14px; bottom: 14px; \}/)
  // 打印隐藏
  assert.match(REPORT_SRC, /\.rp-back, \.rp-risk-nav \{ display: none !important; \}/)
})

test('返回目标未被改变（仍是 /contracts/:id?tab=audit）', () => {
  const i = REPORT_SRC.indexOf('function backToAuditTab')
  const body = REPORT_SRC.slice(i, REPORT_SRC.indexOf('\n}', i))
  assert.match(body, /router\.push\(contractDetailPath\(contractId\.value, 'audit'\)\)/)
})

// ════════════════════════════════════════════════════════════════
// 本轮新增：② 返回审核报告的位置记忆（sessionStorage，一次性）
// ════════════════════════════════════════════════════════════════
test('快照 key 形如 contract_<id>_report_return，id 为空则不产生 key', () => {
  assert.equal(returnSnapshotKey(109), 'contract_109_report_return')
  assert.equal(returnSnapshotKey(' 109 '), 'contract_109_report_return')
  assert.equal(returnSnapshotKey(''), '')
  assert.equal(returnSnapshotKey(null), '')
  assert.equal(returnSnapshotKey(undefined), '')
})

test('快照写入/读取结构为 {tab, scrollY}，且读取即消费（一次性）', () => {
  installSessionStorage()
  assert.equal(saveReturnSnapshot(109, RETURN_SNAPSHOT_TAB, 1234), true)
  assert.equal(RETURN_SNAPSHOT_TAB, 'audit')
  // 只读不消费
  assert.deepEqual(peekReturnSnapshot(109), { tab: 'audit', scrollY: 1234 })
  assert.deepEqual(peekReturnSnapshot(109), { tab: 'audit', scrollY: 1234 })
  // 消费
  assert.deepEqual(takeReturnSnapshot(109), { tab: 'audit', scrollY: 1234 })
  assert.equal(takeReturnSnapshot(109), null, '第二次必须为空 —— 否则刷新会一直跳回旧位置')
  assert.equal(peekReturnSnapshot(109), null)
})

test('快照异常输入归一化，且不入库/不跨合同串味', () => {
  const map = installSessionStorage()
  assert.equal(saveReturnSnapshot(109, 'audit', -5), true)
  assert.deepEqual(takeReturnSnapshot(109), { tab: 'audit', scrollY: 0 }, '负值归一为 0')
  assert.equal(saveReturnSnapshot(109, 'audit', NaN), true)
  assert.deepEqual(takeReturnSnapshot(109), { tab: 'audit', scrollY: 0 })
  assert.equal(saveReturnSnapshot(109, 'audit', undefined), true)
  assert.deepEqual(takeReturnSnapshot(109), { tab: 'audit', scrollY: 0 })
  // 不同合同互不影响
  saveReturnSnapshot(109, 'audit', 111)
  saveReturnSnapshot(110, 'audit', 222)
  assert.deepEqual(takeReturnSnapshot(110), { tab: 'audit', scrollY: 222 })
  assert.deepEqual(takeReturnSnapshot(109), { tab: 'audit', scrollY: 111 })
  // 非法 json 不抛错
  map.set('contract_999_report_return', '{not json')
  assert.equal(takeReturnSnapshot(999), null)
  // id 为空时不写
  map.clear()
  assert.equal(saveReturnSnapshot('', 'audit', 10), false)
  assert.equal(map.size, 0)
})

test('无 sessionStorage 时静默降级（不影响导航）', () => {
  const saved = globalThis.sessionStorage
  delete globalThis.sessionStorage
  assert.equal(saveReturnSnapshot(109, 'audit', 10), false)
  assert.equal(peekReturnSnapshot(109), null)
  assert.equal(takeReturnSnapshot(109), null)
  globalThis.sessionStorage = saved
})

test('采集点：离开合同详情且停在「审核报告」Tab 时才写快照', () => {
  const i = DETAIL_SRC.indexOf('onBeforeRouteLeave((_to, from)')
  assert.ok(i > 0, '必须有组件级路由守卫 onBeforeRouteLeave((_to, from) => {...})')
  const body = DETAIL_SRC.slice(i, DETAIL_SRC.indexOf('\n})', i))
  assert.match(body, /if \(ws\.activeTab !== PANE\.report\) return/, '其他 Tab 不得写快照')
  assert.match(body, /saveReturnSnapshot\(from\?\.params\?\.id, RETURN_SNAPSHOT_TAB, window\.scrollY\)/)
  assert.doesNotMatch(body, /request\.|router\.|loadAll|loadReport/, '守卫只写 sessionStorage，不调接口、不改路由')
})

test('恢复只在 ?tab=audit 落到审核报告 Tab 时发生，且一次性消费', () => {
  const i = DETAIL_SRC.indexOf('function applyTabFromQuery')
  assert.match(DETAIL_SRC.slice(i, i + 320), /if \(pane === PANE\.report\) restoreReportScroll\(\)/)
  const j = DETAIL_SRC.indexOf('async function restoreReportScroll')
  const body = DETAIL_SRC.slice(j, DETAIL_SRC.indexOf('\n}', DETAIL_SRC.lastIndexOf('window.scrollTo', DETAIL_SRC.length)))
  assert.match(body, /const snap = takeReturnSnapshot\(route\.params\.id\)/)
  assert.match(body, /if \(!snap \|\| snap\.tab !== RETURN_SNAPSHOT_TAB \|\| !snap\.scrollY\) return/)
  assert.match(body, /window\.scrollTo\(\{ top: Math\.min\(target, maxY\), behavior: 'auto' \}\)/)
  assert.match(body, /requestAnimationFrame/, '需等渲染完成再滚')
})

test('恢复逻辑不触碰审核数据/定位数据，也不做全局 keep-alive', () => {
  const j = DETAIL_SRC.indexOf('async function restoreReportScroll')
  const body = DETAIL_SRC.slice(j, DETAIL_SRC.length)
  assert.doesNotMatch(body, /loadAll|loadAuditResult|loadComparison|loadReport|riskRanges|clause_position/)
  assert.doesNotMatch(DETAIL_SRC, /keep-alive|KeepAlive/)
  assert.doesNotMatch(REPORT_SRC, /keep-alive|KeepAlive/)
})
