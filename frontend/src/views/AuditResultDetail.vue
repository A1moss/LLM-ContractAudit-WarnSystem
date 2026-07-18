<template>
  <div class="page-container">
    <!-- 加载状态 -->
    <div v-if="loading" class="loading-state">
      <el-skeleton :rows="6" animated />
    </div>

    <!-- 错误状态 -->
    <div v-else-if="error" class="error-state">
      <el-result icon="error" :title="error">
        <template #extra>
          <el-button @click="$router.push('/audit/result')">返回审核结果列表</el-button>
        </template>
      </el-result>
    </div>

    <!-- 空状态（无风险） -->
    <div v-else-if="riskItems.length === 0 && !loading" class="empty-state">
      <el-empty description="未检测到风险项">
        <el-button @click="$router.push('/audit/result')">返回列表</el-button>
      </el-empty>
    </div>

    <template v-else>
      <!-- 页面导航 -->
      <div class="page-nav-bar">
        <el-button @click="$router.push('/audit/result')">
          <el-icon><ArrowLeft /></el-icon>返回审核结果列表
        </el-button>
        <el-button-group>
          <el-button type="primary" size="small" @click="$router.push(`/contracts/${contractId}`)">
            查看合同详情
          </el-button>
          <el-button type="primary" size="small" @click="$router.push(`/audit/report/${contractId}`)">
            查看审核报告
          </el-button>
        </el-button-group>
      </div>

      <!-- 合同名称 -->
      <div class="contract-title" v-if="contractName">
        <h3>{{ contractName }}</h3>
      </div>

      <!-- 统计卡片 -->
      <el-row :gutter="20">
        <el-col :span="6">
          <el-card shadow="hover">
            <el-statistic title="风险总数" :value="riskSummary.total" />
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="hover">
            <el-statistic title="高风险" :value="riskSummary.high">
              <template #suffix><span class="suffix-red">条</span></template>
            </el-statistic>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="hover">
            <el-statistic title="中风险" :value="riskSummary.mid">
              <template #suffix><span class="suffix-orange">条</span></template>
            </el-statistic>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="hover">
            <el-statistic title="低风险" :value="riskSummary.low">
              <template #suffix><span class="suffix-green">条</span></template>
            </el-statistic>
          </el-card>
        </el-col>
      </el-row>

      <!-- 风险列表 -->
      <el-card shadow="hover" class="risk-card">
        <template #header>
          <span>风险明细</span>
        </template>
        <el-table :data="riskItems" stripe border>
          <el-table-column prop="level" label="等级" width="100">
            <template #default="{ row }">
              <el-tag
                :type="row.level === '高风险' ? 'danger' : row.level === '中风险' ? 'warning' : 'success'"
                size="small"
              >{{ row.level }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="type" label="风险类型" width="130" />
          <el-table-column prop="clause" label="原文片段" min-width="220" show-overflow-tooltip />
          <el-table-column prop="reason" label="判定理由" min-width="220" show-overflow-tooltip />
          <el-table-column prop="suggestion" label="建议" min-width="180" />
          <el-table-column prop="confidence" label="置信度" width="120">
            <template #default="{ row }">
              <el-progress
                :percentage="Math.round((row.confidence || 0) * 100)"
                :color="row.confidence >= 0.7 ? '#67C23A' : row.confidence >= 0.5 ? '#E6A23C' : '#F56C6C'"
                :stroke-width="6"
              />
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ArrowLeft } from '@element-plus/icons-vue'
import { getAuditResult, getContractDetail } from '../api/contract.js'

const route = useRoute()
const contractId = computed(() => route.params.contractId || '')

const contractName = ref('')
const riskItems = ref([])
const loading = ref(true)
const error = ref('')

const levelMap = { high: '高风险', medium: '中风险', low: '低风险' }

const riskSummary = computed(() => {
  const high = riskItems.value.filter(r => r.level === '高风险').length
  const mid = riskItems.value.filter(r => r.level === '中风险').length
  const low = riskItems.value.filter(r => r.level === '低风险').length
  return { total: riskItems.value.length, high, mid, low }
})

async function fetchContractName(id) {
  try {
    const res = await getContractDetail(id)
    contractName.value = res.data?.file_name || ''
  } catch { contractName.value = '' }
}

async function fetchResult() {
  const id = contractId.value
  if (!id) {
    error.value = '缺少合同 ID 参数'
    loading.value = false
    return
  }
  fetchContractName(id)
  try {
    const res = await getAuditResult(id)
    riskItems.value = (res.data?.items || []).map(r => ({
      level: levelMap[r.risk_level] || r.risk_level,
      type: r.risk_type,
      clause: r.clause_text,
      reason: r.reason,
      suggestion: r.suggestion,
      confidence: r.confidence,
      detection_method: r.detection_method,
    }))
  } catch (e) {
    error.value = '加载审核结果失败'
    console.warn('审核结果加载失败:', e)
  } finally {
    loading.value = false
  }
}

watch(contractId, (newId, oldId) => {
  if (newId && newId !== oldId) {
    loading.value = true
    error.value = ''
    riskItems.value = []
    fetchResult()
  }
})

onMounted(() => fetchResult())
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

.page-nav-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.contract-title {
  margin-bottom: 16px;
}
.contract-title h3 {
  margin: 0;
  font-size: 18px;
  color: #303133;
}

.risk-card {
  margin-top: 20px;
}

.suffix-red { color: #F56C6C; font-size: 14px; }
.suffix-orange { color: #E6A23C; font-size: 14px; }
.suffix-green { color: #67C23A; font-size: 14px; }
</style>
