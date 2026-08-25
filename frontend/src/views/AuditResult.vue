<template>
  <div class="page-container">
    <h3>审核结果</h3>
    <el-divider />

    <!-- 加载状态 -->
    <div v-if="loading" class="loading-state">
      <el-skeleton :rows="6" animated />
    </div>

    <!-- 错误状态 -->
    <div v-else-if="error" class="error-state">
      <el-result icon="error" :title="error">
        <template #extra>
          <el-button @click="fetchList">重试</el-button>
        </template>
      </el-result>
    </div>

    <!-- 空状态 -->
    <div v-else-if="contracts.length === 0" class="empty-state">
      <el-empty description="暂无已完成审核的合同">
        <el-button type="primary" @click="$router.push('/contracts/upload')">上传合同</el-button>
      </el-empty>
    </div>

    <!-- 合同审核结果列表 -->
    <template v-else>
      <el-table
        :data="contracts"
        stripe
        border
        row-key="id"
        @expand-change="handleExpand"
      >
        <el-table-column type="expand">
          <template #default="{ row }">
            <div v-if="expandingRows[row.id]" class="expand-content">
              <div v-if="row._riskLoading" class="expand-loading">
                <el-skeleton :rows="3" animated />
              </div>
              <div v-else-if="row._riskError" class="expand-error">
                <el-result icon="error" :title="row._riskError" />
              </div>
              <template v-else>
                <div class="expand-summary">
                  共 {{ row._riskTotal }} 条风险：
                  <el-tag type="danger" size="small">高风险 {{ row._riskHigh }}</el-tag>
                  <el-tag type="warning" size="small">中风险 {{ row._riskMid }}</el-tag>
                  <el-tag type="success" size="small">低风险 {{ row._riskLow }}</el-tag>
                </div>
                <el-table :data="row._riskItems" size="small" border class="expand-table">
                  <el-table-column prop="level" label="等级" width="80">
                    <template #default="{ row: r }">
                      <el-tag
                        :type="r.level === '高风险' ? 'danger' : r.level === '中风险' ? 'warning' : 'success'"
                        size="small"
                      >{{ r.level }}</el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column prop="type" label="风险类型" width="130" />
                  <el-table-column prop="clause" label="涉及条款" min-width="200" show-overflow-tooltip />
                  <el-table-column prop="suggestion" label="建议" min-width="200" show-overflow-tooltip />
                  <el-table-column prop="confidence" label="置信度" width="110">
                    <template #default="{ row: r }">
                      <el-progress
                        :percentage="Math.round((r.confidence || 0) * 100)"
                        :color="r.confidence >= 0.7 ? '#67C23A' : r.confidence >= 0.5 ? '#E6A23C' : '#F56C6C'"
                        :stroke-width="6"
                      />
                    </template>
                  </el-table-column>
                </el-table>
                <el-button
                  v-if="row._riskItems.length === 0"
                  type="primary"
                  size="small"
                  class="expand-action"
                  @click="$router.push(`/audit/result/${row.id}`)"
                >查看合同详情</el-button>
              </template>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="file_name" label="合同名称" min-width="200">
          <template #default="{ row }">
            <el-link type="primary" @click="$router.push(`/audit/result/${row.id}`)">{{ row.file_name }}</el-link>
          </template>
        </el-table-column>
        <el-table-column prop="contract_type" label="合同类型" width="130">
          <template #default="{ row }">{{ typeLabel(row.contract_type) }}</template>
        </el-table-column>
        <el-table-column label="审核时间" width="170">
          <template #default="{ row }">{{ formatTime(row.updated_at) }}</template>
        </el-table-column>
        <el-table-column label="风险概览" width="280">
          <template #default="{ row }">
            <div class="risk-badges">
              <el-tag type="danger" size="small">高 {{ row._riskHigh ?? '—' }}</el-tag>
              <el-tag type="warning" size="small">中 {{ row._riskMid ?? '—' }}</el-tag>
              <el-tag type="success" size="small">低 {{ row._riskLow ?? '—' }}</el-tag>
              <span class="risk-total">共 {{ row._riskTotal ?? '—' }} 条</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" @click="$router.push(`/audit/result/${row.id}`)">
              风险明细
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- 分页 -->
      <div class="pagination-wrapper">
        <el-pagination
          v-model:current-page="pagination.page"
          v-model:page-size="pagination.page_size"
          :page-sizes="[10, 20, 50]"
          :total="pagination.total"
          layout="total, sizes, prev, pager, next, jumper"
          @current-change="fetchList"
          @size-change="() => { fetchList(1) }"
        />
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { getContractList, getAuditResult } from '../api/contract.js'
import { formatTime } from '../utils/format.js'

// ── 列表状态 ──
const contracts = ref([])
const loading = ref(true)
const error = ref('')
const pagination = reactive({
  page: 1,
  page_size: 10,
  total: 0,
})

// ── 展开行状态（记录哪些行已加载过风险数据）──
const expandingRows = reactive({})

// ── 合同类型映射 ──
const typeMap = {
  purchase: '采购合同', sales: '销售合同', nda: '保密协议 (NDA)',
  outsourcing: '服务外包合同', employment: '劳动合同', other: '其他合同',
}
function typeLabel(type) { return typeMap[type] || type || '未分类' }

// ── 获取已完成审核的合同列表 ──
async function fetchList(page = pagination.page) {
  loading.value = true
  error.value = ''
  pagination.page = page
  // 切换页面时清理旧的展开行缓存
  Object.keys(expandingRows).forEach(k => delete expandingRows[k])
  try {
    const res = await getContractList({
      page,
      page_size: pagination.page_size,
      status: 'completed',
    })
    const items = res.data?.items || []
    pagination.total = res.data?.total || 0

    // 直接用列表接口返回的风险计数（后端已一次汇总，避免 N+1 逐条请求）
    contracts.value = items.map(c => ({
      ...c,
      _riskTotal: c.risk_count || 0,
      _riskHigh: c.high_risk_count || 0,
      _riskMid: c.mid_risk_count || 0,
      _riskLow: c.low_risk_count || 0,
    }))
  } catch (e) {
    error.value = '加载审核结果列表失败'
    console.warn('审核结果列表加载失败:', e)
  } finally {
    loading.value = false
  }
}

// ── 展开行时按需加载详细风险项 ──
const levelMap = { high: '高风险', medium: '中风险', low: '低风险' }

async function handleExpand(row, expandedRows) {
  const isExpanding = expandedRows.some(r => r.id === row.id)
  if (!isExpanding) return
  if (expandingRows[row.id]) return // 已加载过

  expandingRows[row.id] = true
  row._riskLoading = true
  try {
    const res = await getAuditResult(row.id)
    row._riskItems = (res.data?.items || []).map(r => ({
      level: levelMap[r.risk_level] || r.risk_level,
      type: r.risk_type,
      clause: r.clause_text,
      reason: r.reason,
      suggestion: r.suggestion,
      confidence: r.confidence,
      detection_method: r.detection_method,
    }))
  } catch {
    row._riskError = '加载风险详情失败'
  } finally {
    row._riskLoading = false
  }
}

onMounted(() => fetchList())
</script>

<style scoped>
.page-container {
  padding: 24px;
  max-width: 1200px;
  margin: 0 auto;
}

.loading-state, .error-state, .empty-state {
  padding: 60px 0;
}

.risk-badges {
  display: flex;
  align-items: center;
  gap: 6px;
}

.risk-total {
  margin-left: 4px;
  font-size: 12px;
  color: #909399;
}

.expand-content {
  padding: 12px 24px;
}

.expand-loading, .expand-error {
  padding: 20px 0;
}

.expand-summary {
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #606266;
}

.expand-table {
  margin-bottom: 8px;
}

.expand-action {
  margin-top: 8px;
}

.pagination-wrapper {
  margin-top: 20px;
  display: flex;
  justify-content: flex-end;
}
</style>
