/**
 * 合同详情 ↔ 完整审核报告之间的**纯导航约定**（node 可直接测试，不依赖 Vue / 网络）
 *
 * 边界（本轮严格约束）：
 * - 只描述「URL ↔ 界面 Tab 状态」的映射，以及风险锚点的 id 格式；
 * - **不读接口、不碰审核数据、不碰风险定位数据、不碰 workspace**。
 *
 * ⚠️ 命名约定（query 取值 与 内部 pane 名 **同名但语义不同**，勿混用）：
 *   - 作为 **query 取值**：`?tab=audit` → **审核报告** Tab（内部 pane 名 `'report'`）
 *   - 作为 **内部 pane 名**：`'audit'` → 「风险详情」Tab
 *   这层翻译只允许通过 paneFromQueryTab() 完成，页面不得直接拿 query 值当 pane 名用。
 */

/** 内部 pane 名（与 ContractDetail.vue 的 <el-tab-pane name> 一一对应，不得随意更改） */
export const PANE = {
  text: 'text',       // 原始文本
  risks: 'audit',     // 风险详情
  compare: 'compare', // 条款比对
  revise: 'revise',   // 修改合同
  report: 'report',   // 审核报告
}

/** query `tab` 取值 → 内部 pane 名 */
export const TAB_QUERY_ALIASES = {
  audit: PANE.report,   // ★ 导航需求约定：?tab=audit 指「审核报告」
  report: PANE.report,  // 同时接受内部名，便于手工拼 URL
  text: PANE.text,
  compare: PANE.compare,
  revise: PANE.revise,
}

/** 允许的全部 query 取值（供测试与排查用） */
export const TAB_QUERY_VALUES = Object.keys(TAB_QUERY_ALIASES)

/**
 * 把 query 里的 `tab` 解析成内部 pane 名。
 * 无法识别 / 空值 / 数组（如 ?tab=a&tab=b）→ 返回 ''，调用方据此**保持默认行为**。
 */
export function paneFromQueryTab(tab) {
  const raw = Array.isArray(tab) ? tab[0] : tab
  const key = String(raw == null ? '' : raw).trim().toLowerCase()
  return TAB_QUERY_ALIASES[key] || ''
}

/**
 * 合同详情路径（可带 Tab 深链）。
 * tab 为空 → 返回不含 query 的干净路径，保证「无参数时行为不变」。
 */
export function contractDetailPath(contractId, tab = '') {
  const id = String(contractId == null ? '' : contractId).trim()
  if (!id) return '/contracts'
  const t = String(tab == null ? '' : tab).trim()
  return t ? `/contracts/${id}?tab=${t}` : `/contracts/${id}`
}

/** 完整报告页里单张风险卡的 DOM id / 锚点（生成方与消费方必须同源） */
export function riskAnchorId(riskId) {
  return `risk-${riskId}`
}

export function riskAnchorHash(riskId) {
  return `#${riskAnchorId(riskId)}`
}

/** 从 location.hash 反解风险记录 id；不匹配（含条款完整性等其它锚点）返回 '' */
export function riskIdFromHash(hash) {
  const m = /^#risk-(\d+)$/.exec(String(hash == null ? '' : hash))
  return m ? m[1] : ''
}

// ════════════════════════════════════════════════════════════════
// 「返回审核报告」位置记忆（sessionStorage，**仅当次浏览器会话**有效）
//
// 唯一使用路径：
//   ① 在合同详情停在「审核报告」Tab 上、离开该页面时 → 记下这个 Tab 的 window.scrollY；
//   ② 从完整报告页点「返回审核报告」回到 /contracts/:id?tab=audit 时 → **一次性消费**它。
//
// 明确不做：不写数据库、不新增接口、不跨会话保存；
// 普通进入 /contracts/:id、普通刷新都不会使用它（读取即删除）。
//
// 为什么采集点在「离开合同详情」而不是「完整报告页的返回按钮」：
//   两页的滚动坐标不属于同一页面 —— 完整报告页可滚动数千像素，而审核报告 Tab
//   只有几百像素的可滚动范围；拿前者的 scrollY 去恢复会被截断成"贴底"。
//   采集点取**审核报告 Tab 自己的** Y，恢复才能与离开前一致。
// ════════════════════════════════════════════════════════════════

/** 快照约定代表的 Tab（审核报告，见上方别名表） */
export const RETURN_SNAPSHOT_TAB = 'audit'

/** sessionStorage key：`contract_<id>_report_return`（id 为空时不产生 key） */
export function returnSnapshotKey(contractId) {
  const id = String(contractId == null ? '' : contractId).trim()
  return id ? `contract_${id}_report_return` : ''
}

/** 安全取 sessionStorage：不存在（node 测试环境）/ 隐私模式取不到时返回 null，调用方静默降级 */
function sessionStore() {
  try {
    return typeof sessionStorage === 'undefined' ? null : sessionStorage
  } catch {
    return null
  }
}

function normalizeSnapshot(data) {
  if (!data || typeof data !== 'object') return null
  const y = Number(data.scrollY)
  return {
    tab: String(data.tab == null ? '' : data.tab),
    scrollY: Number.isFinite(y) && y > 0 ? Math.round(y) : 0,
  }
}

/** 写入位置快照（覆盖写）。无 sessionStorage / 配额异常 → 返回 false，绝不影响导航本身 */
export function saveReturnSnapshot(contractId, tab, scrollY) {
  const key = returnSnapshotKey(contractId)
  const store = sessionStore()
  if (!key || !store) return false
  try {
    store.setItem(key, JSON.stringify(normalizeSnapshot({ tab, scrollY })))
    return true
  } catch {
    return false
  }
}

/** 只读快照（**不消费**）：用于判断"是否已有本次返回位置" */
export function peekReturnSnapshot(contractId) {
  const key = returnSnapshotKey(contractId)
  const store = sessionStore()
  if (!key || !store) return null
  try {
    const raw = store.getItem(key)
    return raw == null ? null : normalizeSnapshot(JSON.parse(raw))
  } catch {
    return null
  }
}

/** 读取并**消费**快照（读取即删除 → 一次性，避免之后普通刷新反复跳回旧位置） */
export function takeReturnSnapshot(contractId) {
  const snap = peekReturnSnapshot(contractId)
  const key = returnSnapshotKey(contractId)
  const store = sessionStore()
  if (key && store) {
    try {
      store.removeItem(key)
    } catch {
      /* 删不掉也不影响本次恢复 */
    }
  }
  return snap
}
