<template>
  <div class="page-container">
    <!-- 顶部：欢迎语 -->
    <div class="page-header">
      <div>
        <h2>欢迎回来，{{ username }}</h2>
        <p class="page-subtitle">A24 合同智能审核系统</p>
      </div>
    </div>

    <!-- 四个统计卡片 -->
    <el-row :gutter="20" class="stat-row">
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="今日审核" :value="stats.today_audit">
            <template #suffix><span class="stat-suffix suffix-green">份</span></template>
          </el-statistic>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="待处理" :value="stats.pending">
            <template #suffix><span class="stat-suffix suffix-orange">份</span></template>
          </el-statistic>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="本月风险" :value="stats.month_risks">
            <template #suffix><span class="stat-suffix suffix-red">条</span></template>
          </el-statistic>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="通过率" :value="stats.approval_rate">
            <template #suffix><span class="stat-suffix suffix-blue">%</span></template>
          </el-statistic>
        </el-card>
      </el-col>
    </el-row>

    <!-- 最近合同列表 -->
    <el-card shadow="hover" class="section-card">
      <template #header><span>最近合同</span></template>
      <el-table :data="recentContracts" stripe v-loading="loading">
        <template #empty><el-empty description="暂无合同" /></template>
        <el-table-column prop="file_name" label="文件名" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <el-link type="primary" @click="$router.push(`/contracts/${row.id}`)">{{ row.file_name }}</el-link>
          </template>
        </el-table-column>
        <el-table-column prop="contract_type" label="类型" width="130" />
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="statusTag(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="风险等级" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.risk_level" :type="riskTag(row.risk_level)" size="small">{{ riskLabel(row.risk_level) }}</el-tag>
            <span v-else style="color: #909399;">—</span>
          </template>
        </el-table-column>
        <el-table-column label="上传时间" width="160">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 近 7 天审核量柱状图 -->
    <el-card shadow="hover">
      <template #header><span>近 7 天审核量</span></template>
      <div ref="barChartRef" class="chart-container"></div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import request from '../utils/request.js'
import { formatTime } from '../utils/format.js'
import * as echarts from 'echarts'

const router = useRouter()
const username = ref(localStorage.getItem('username') || '用户')
const loading = ref(false)
const barChartRef = ref(null)

// ── 仪表盘数据（来自后端 /stats/dashboard）──
const stats = reactive({
  today_audit: 0,
  pending: 0,
  month_risks: 0,
  approval_rate: 0,
  last7days: [],
})
const recentContracts = ref([])

// ── 状态 / 风险等级映射 ──
function statusLabel(s) {
  const map = { uploaded: '已上传', parsed: '已解析', auditing: '审核中', completed: '审核完成', reviewed: '待验收', approved: '已验收' }
  return map[s] || s || '未知'
}
function statusTag(s) {
  const map = { completed: 'success', approved: 'success', reviewed: 'primary', auditing: 'warning', parsed: 'info', uploaded: 'info' }
  return map[s] || 'info'
}
function riskLabel(level) {
  const map = { high: '高风险', medium: '中风险', low: '低风险' }
  return map[level] || level
}
function riskTag(level) {
  if (level === 'high') return 'danger'
  if (level === 'medium') return 'warning'
  if (level === 'low') return 'success'
  return 'info'
}

async function fetchDashboard() {
  loading.value = true
  try {
    const res = await request.get('/stats/dashboard')
    const d = res.data || {}
    stats.today_audit = d.today_audit ?? 0
    stats.pending = d.pending ?? 0
    stats.month_risks = d.month_risks ?? 0
    stats.approval_rate = d.approval_rate ?? 0
    recentContracts.value = d.recent_contracts || []
    stats.last7days = d.last7days || []
    await nextTick()
    renderChart()
  } catch {
    // 后端未启动时保持 0 值，不打断页面
  } finally {
    loading.value = false
  }
}

// ── ECharts 柱状图 ──
let chartInstance = null

function renderChart() {
  if (!barChartRef.value) return
  if (!chartInstance) chartInstance = echarts.init(barChartRef.value)
  const days = stats.last7days || []
  chartInstance.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
    xAxis: { type: 'category', data: days.map(d => d.date) },
    yAxis: { type: 'value', name: '审核数', minInterval: 1 },
    series: [{
      name: '审核量',
      type: 'bar',
      data: days.map(d => d.count),
      itemStyle: { color: '#409EFF' },
      barWidth: '50%',
    }],
  })
}

function handleResize() {
  chartInstance?.resize()
}

onMounted(() => {
  username.value = localStorage.getItem('username') || '用户'
  window.addEventListener('resize', handleResize)
  fetchDashboard()
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  chartInstance?.dispose()
  chartInstance = null
})
</script>

<style scoped>
.page-container {
  padding: 24px;
  max-width: 1200px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.page-header h2 { margin: 0; }

.page-subtitle {
  margin: 4px 0 0 0;
  color: #909399;
  font-size: 14px;
}

.section-card { margin-bottom: 20px; }

.stat-row { margin-bottom: 20px; }

.stat-suffix { font-size: 14px; }
.suffix-green { color: #67C23A; }
.suffix-orange { color: #E6A23C; }
.suffix-red { color: #F56C6C; }
.suffix-blue { color: #409EFF; }

.chart-container { width: 100%; height: 300px; }
</style>
