<template>
  <div class="cd2">
    <!-- 加载 / 错误 -->
    <div v-if="ws.loading" class="cd2-state"><el-skeleton :rows="8" animated /></div>
    <div v-else-if="ws.detailError" class="cd2-state">
      <el-result icon="error" title="加载失败" :sub-title="ws.detailError">
        <template #extra>
          <el-button type="primary" @click="ws.loadAll()">重新加载</el-button>
          <el-button @click="$router.push('/contracts')">返回列表</el-button>
        </template>
      </el-result>
    </div>

    <template v-else-if="ws.contract">
      <!-- 合同信息条（压缩为一行；不展示页数——后端不持久化页数，主格式 docx 也不提供页数） -->
      <div class="cd2-header">
        <div class="cd2-header-l">
          <span class="icon"><el-icon><Document /></el-icon></span>
          <div class="cd2-header-text">
            <div class="cd2-title-row">
              <h3>{{ ws.contract.file_name }}</h3>
              <el-tag :type="statusTag" size="small">{{ statusLabel }}</el-tag>
              <el-tag v-if="ws.contract.is_outsourcing" type="warning" size="small" effect="plain">服务外包</el-tag>
            </div>
            <div class="cd2-sub">
              <span>#{{ ws.contract.id }}</span>
              <span>{{ typeLabel(ws.contract.contract_type) }}</span>
              <span>上传于 {{ formatTime(ws.contract.created_at) }}</span>
              <span>{{ ws.contract.audit_mode === 'precise' ? '精细审核' : ws.contract.audit_mode === 'fast' ? '快速初筛' : '—' }}</span>
            </div>
          </div>
        </div>
        <div class="cd2-header-r">
          <template v-if="ws.contract.status === 'completed' && ws.canReview">
            <span class="cd2-hint">审核人复核：</span>
            <el-button type="success" size="small" :loading="ws.reviewing" @click="ws.doReview('approve')">复核通过</el-button>
            <el-button type="danger" size="small" :loading="ws.reviewing" @click="ws.doReview('reject')">驳回重审</el-button>
          </template>
          <template v-if="ws.contract.status === 'reviewed' && ws.canApprove">
            <span class="cd2-hint">验收人验收：</span>
            <el-button type="success" size="small" :loading="ws.reviewing" @click="ws.doApprove()">验收通过</el-button>
          </template>
        </div>
      </div>

      <!-- 一级 Tab（与设计一致：原文 / 风险 / 比对 / 修改合同 / 审核报告） -->
      <el-tabs v-model="ws.activeTab" type="border-card" class="cd2-tabs">
        <el-tab-pane label="原始文本" name="text">
          <div class="cd2-pane cd2-pane-scroll">
            <OriginalTextPanel :ws="ws" @go-risk="onGoRisk" />
          </div>
        </el-tab-pane>

        <el-tab-pane label="风险详情" name="audit">
          <div class="cd2-pane cd2-pane-scroll">
            <RiskPanel :ws="ws" />
          </div>
        </el-tab-pane>

        <el-tab-pane label="条款比对" name="compare">
          <div class="cd2-pane cd2-pane-scroll">
            <ComparisonPanel :ws="ws" />
          </div>
        </el-tab-pane>

        <el-tab-pane label="修改合同" name="revise">
          <div class="cd2-pane cd2-pane-flush">
            <RevisionWorkbench :ws="ws" />
          </div>
        </el-tab-pane>

        <el-tab-pane label="审核报告" name="report">
          <!-- 审核报告是纵向长摘要（真实内容高度 900+px）：
               用 .cd2-pane-flow 让它随页面自然滚动，而不是被压进
               calc(100vh - 300px) 的第二层固定高度滚动区里（底部按钮会被裁掉看不见）。 -->
          <div class="cd2-pane cd2-pane-flow">
            <ReportPanel :ws="ws" />
          </div>
        </el-tab-pane>
      </el-tabs>
    </template>
  </div>
</template>

<script setup>
import { computed, nextTick, watch } from 'vue'
import { onBeforeRouteLeave, useRoute } from 'vue-router'
import { Document } from '@element-plus/icons-vue'
import OriginalTextPanel from './contract-detail/OriginalTextPanel.vue'
import RiskPanel from './contract-detail/RiskPanel.vue'
import ComparisonPanel from './contract-detail/ComparisonPanel.vue'
import RevisionWorkbench from './contract-detail/RevisionWorkbench.vue'
import ReportPanel from './contract-detail/ReportPanel.vue'
import { useContractWorkspace } from '../composables/useContractWorkspace.js'
import { formatTime } from '../utils/format.js'
import { typeLabel } from '../constants/contractTypes.js'
import {
  PANE, RETURN_SNAPSHOT_TAB,
  paneFromQueryTab, saveReturnSnapshot, takeReturnSnapshot,
} from '../constants/navigation.js'

/**
 * 合同详情 V2 工作台（页面壳）
 *
 * - 路由不变（/contracts/:id）、API 不变、审核链路不变。
 * - 全部状态与逻辑集中在 useContractWorkspace（含 watch(contractId) 彻底重置），
 *   子组件只消费 `ws`，不各自持有会串合同的副本。
 * - 不依赖 PDF 渲染：原文一律用后端 parsed_text 渲染；页数不再展示。
 */
const ws = useContractWorkspace()
const route = useRoute()

const STATUS_LABELS = {
  uploaded: '已上传', parsed: '已解析', auditing: '审核中',
  completed: '审核完成', reviewed: '待验收', approved: '已验收',
}
const statusLabel = computed(() => STATUS_LABELS[ws.contract?.status] || ws.contract?.status || '未知')
const statusTag = computed(() => {
  const s = ws.contract?.status
  if (s === 'completed' || s === 'approved') return 'success'
  if (s === 'auditing' || s === 'parsing') return 'warning'
  if (s === 'reviewed') return 'primary'
  return 'info'
})

/** 原文中点击风险标注 → 切到风险详情并定位该风险卡片 */
function onGoRisk(riskId) {
  ws.focusRiskId = riskId
  ws.setTab('audit')
}

/**
 * 深链 Tab：`/contracts/:id?tab=audit` → 「审核报告」Tab。
 *
 * 只做**导航状态**：调用 workspace 既有的 `setTab()`，不读接口、不改数据加载、
 * 不碰风险定位数据（切到审核报告后按需加载报告，仍是 workspace 的既有行为）。
 *
 * - 无 `tab` 参数、或参数无法识别 → **保持原有默认行为**（默认停在「原始文本」）；
 * - 刷新带 `?tab=audit` 的地址仍停在审核报告（immediate + watch query，参数不被清除）；
 * - 合同切换时 workspace 的 `watch(contractId)` 会先 resetAll()（回默认 Tab），
 *   本 watcher 在其之后创建、按创建顺序后执行，因此仍能按 query 落位。
 */
function applyTabFromQuery() {
  const pane = paneFromQueryTab(route.query.tab)
  if (pane) ws.setTab(pane)
  // 位置恢复是 Tab 落位之后的**附加行为**：只有「从完整报告返回」这一条路径会命中快照
  if (pane === PANE.report) restoreReportScroll()
}
watch([() => route.params.id, () => route.query.tab], applyTabFromQuery, { immediate: true })

/**
 * 位置记忆 · 采集：**离开合同详情时**，若正停在「审核报告」Tab，记下该 Tab 的滚动位置。
 *
 * 为什么在"离开"而不是"完整报告的返回按钮"上采集：
 *   两页滚动坐标不属于同一页面（完整报告页可滚数千像素，审核报告 Tab 只有几百像素），
 *   用完整报告的 scrollY 恢复会被截断成"贴底"；采集点取本页 → 恢复值与离开前一致。
 *
 * 用组件级路由守卫（不改 router/index.js、不改全局路由行为，也不阻断任何跳转）。
 */
onBeforeRouteLeave((_to, from) => {
  if (ws.activeTab !== PANE.report) return
  saveReturnSnapshot(from?.params?.id, RETURN_SNAPSHOT_TAB, window.scrollY)
})

/**
 * 位置记忆 · 恢复（**一次性**）。
 *
 * 触发条件严格限定为「完整报告 → 返回审核报告 → /contracts/:id?tab=audit」：
 *   - 先确认 query 已落到审核报告 Tab（pane === report），否则直接返回；
 *   - 快照读取即**消费**（takeReturnSnapshot）：普通进入 /contracts/:id、普通刷新
 *     都不会跳到旧位置，也不会反复命中同一份快照。
 *
 * 审核报告内容是异步加载的：文档还没长到目标高度时 scrollTo 会被浏览器截断，
 * 因此用有上限的 rAF 轮询，等"能滚到目标"或"高度稳定"后再滚一次（behavior: 'auto'，不产生动画）。
 */
async function restoreReportScroll() {
  const snap = takeReturnSnapshot(route.params.id)
  if (!snap || snap.tab !== RETURN_SNAPSHOT_TAB || !snap.scrollY) return
  const target = snap.scrollY
  const deadline = Date.now() + 1500
  let lastHeight = -1
  let stableFrames = 0
  for (;;) {
    await nextTick()
    await new Promise((resolve) => requestAnimationFrame(() => resolve()))
    const height = document.documentElement.scrollHeight
    if (height - window.innerHeight >= target) break   // 已经能滚到目标位置
    stableFrames = height === lastHeight ? stableFrames + 1 : 0
    lastHeight = height
    if (stableFrames >= 10 || Date.now() > deadline) break  // 内容已稳定 / 超时 → 按当前高度取最接近位置
  }
  const maxY = Math.max(0, document.documentElement.scrollHeight - window.innerHeight)
  window.scrollTo({ top: Math.min(target, maxY), behavior: 'auto' })
}
</script>

<style scoped>
.cd2 { padding: 20px 24px 32px; max-width: 1440px; margin: 0 auto; }
.cd2-state { padding: 40px 0; }

/* 信息条：压缩为一行 */
.cd2-header {
  display: flex; align-items: center; justify-content: space-between; gap: 20px;
  background: #fff; border: 1px solid var(--a24-border); border-radius: 12px;
  padding: 12px 16px; margin-bottom: 14px;
}
.cd2-header-l { display: flex; align-items: center; gap: 12px; min-width: 0; }
.cd2-header-l .icon {
  width: 38px; height: 38px; border-radius: 10px; color: #fff; font-size: 19px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg, #1935C3, #1B70F0);
}
.cd2-header-text { min-width: 0; }
.cd2-title-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.cd2-title-row h3 { font-size: 17px; font-weight: 700; color: var(--a24-heading); margin: 0; }
.cd2-sub { display: flex; gap: 14px; flex-wrap: wrap; font-size: 12px; color: var(--a24-muted); margin-top: 3px; }
.cd2-header-r { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.cd2-hint { font-size: 12.5px; color: #606266; }

.cd2-tabs { background: transparent; }
.cd2-tabs :deep(.el-tabs__content) { padding: 12px; background: #fff; }

/* Tab 内容区独立滚动；视口过小时由 min-height 撑开，页面整体滚动 */
.cd2-pane { height: calc(100vh - 300px); min-height: 540px; }
.cd2-pane-scroll { overflow-y: auto; padding-right: 4px; }
.cd2-pane-flush { overflow: hidden; }

/* 审核报告 Tab：内容是纵向长摘要，高度不固定。
   若沿用 .cd2-pane 的 calc(100vh - 300px)（1366×768 下约 468px、1080p 下约 780px），
   内容会被压进一个第二层固定高度滚动区，底部「条款完整性 / 查看完整审核报告 / 去修改合同」
   在首屏之外，验收时表现为"被裁掉"。
   这里改为高度自适应 + 不产生内部滚动，由页面（窗口）统一纵向滚动：
   - 桌面端 1366 / 1440 / 1920 均可继续向下浏览完整内容；
   - 不裁切、不压缩、不删除任何内容，也不产生第二层滚动条。 */
.cd2-pane-flow { height: auto; min-height: 0; overflow: visible; }

@media (max-width: 1200px) {
  .cd2-pane { height: auto; min-height: 0; }
  .cd2-pane-flush { overflow: visible; }
}
</style>
