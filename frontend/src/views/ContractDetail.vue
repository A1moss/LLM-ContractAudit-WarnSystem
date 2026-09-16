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
          <div class="cd2-pane cd2-pane-scroll">
            <ReportPanel :ws="ws" />
          </div>
        </el-tab-pane>
      </el-tabs>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Document } from '@element-plus/icons-vue'
import OriginalTextPanel from './contract-detail/OriginalTextPanel.vue'
import RiskPanel from './contract-detail/RiskPanel.vue'
import ComparisonPanel from './contract-detail/ComparisonPanel.vue'
import RevisionWorkbench from './contract-detail/RevisionWorkbench.vue'
import ReportPanel from './contract-detail/ReportPanel.vue'
import { useContractWorkspace } from '../composables/useContractWorkspace.js'
import { formatTime } from '../utils/format.js'
import { typeLabel } from '../constants/contractTypes.js'

/**
 * 合同详情 V2 工作台（页面壳）
 *
 * - 路由不变（/contracts/:id）、API 不变、审核链路不变。
 * - 全部状态与逻辑集中在 useContractWorkspace（含 watch(contractId) 彻底重置），
 *   子组件只消费 `ws`，不各自持有会串合同的副本。
 * - 不依赖 PDF 渲染：原文一律用后端 parsed_text 渲染；页数不再展示。
 */
const ws = useContractWorkspace()

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

@media (max-width: 1200px) {
  .cd2-pane { height: auto; min-height: 0; }
  .cd2-pane-flush { overflow: visible; }
}
</style>
