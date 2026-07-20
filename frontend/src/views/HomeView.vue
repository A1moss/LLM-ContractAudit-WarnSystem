<template>
  <div class="page-container">
    <!-- 顶部：欢迎语 -->
    <div class="page-header">
      <div>
        <h2>欢迎回来，{{ username }}</h2>
        <p class="page-subtitle">A24 合同智能审核系统</p>
      </div>
    </div>

    <!-- 后端连通性 -->
    <el-card shadow="hover" class="section-card">
      <div class="backend-row">
        <span class="backend-label">后端状态：</span>
        <el-tag v-if="backendStatus === '未检测'" type="info">未检测</el-tag>
        <el-tag v-else-if="backendStatus.startsWith('✅')" type="success">已连通</el-tag>
        <el-tag v-else type="danger">未连通</el-tag>
        <el-button size="small" @click="checkBackend">测试连通性</el-button>
      </div>
    </el-card>

    <!-- 四个统计卡片 -->
    <el-row :gutter="20" class="stat-row">
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="合同总数" :value="stats.totalContracts">
            <template #suffix><span class="stat-suffix suffix-green">份</span></template>
          </el-statistic>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="待处理" :value="stats.pendingCount">
            <template #suffix><span class="stat-suffix suffix-orange">份</span></template>
          </el-statistic>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="检出风险" :value="stats.totalRisks">
            <template #suffix><span class="stat-suffix suffix-red">条</span></template>
          </el-statistic>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="审核完成" :value="stats.completedCount">
            <template #suffix><span class="stat-suffix suffix-blue">份</span></template>
          </el-statistic>
        </el-card>
      </el-col>
    </el-row>

    <!-- 最近合同列表 -->
    <el-card shadow="hover" class="section-card">
      <template #header>
        <div class="card-header-row">
          <span>最近合同</span>
          <el-button size="small" text @click="fetchData">刷新</el-button>
        </div>
      </template>

      <div v-if="loading" class="loading-state">
        <el-skeleton :rows="4" animated />
      </div>

      <el-table v-else-if="recentContracts.length > 0" :data="recentContracts" stripe>
        <el-table-column prop="file_name" label="文件名" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <el-link type="primary" :underline="false" @click="$router.push(`/contracts/${row.id}`)">{{ row.file_name }}</el-link>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="120">
          <template #default="{ row }">{{ typeLabel(row.contract_type) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusTag(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="风险评分" width="100" align="center">
          <template #default="{ row }">
            <template v-if="row._score !== undefined">
              <el-tag :type="scoreTag(row._score)" size="small">{{ row._score }}</el-tag>
            </template>
            <span v-else style="color:#c0c4cc">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="上传时间" width="160">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
      </el-table>

      <el-empty v-else description="暂无合同，请先上传">
        <el-button type="primary" @click="$router.push('/contracts/upload')">上传合同</el-button>
      </el-empty>
    </el-card>

    <!-- 近 7 天审核量柱状图 -->
    <el-card shadow="hover">
      <template #header>
        <span>近 7 天审核量</span>
      </template>
      <div v-if="loading" class="loading-state">
        <el-skeleton :rows="3" animated />
      </div>
      <div v-else ref="barChartRef" class="chart-container"></div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, nextTick } from 'vue'
import request from '../utils/request.js'
import { getContractList } from '../api/contract.js'
import { formatTime } from '../utils/format.js'
import * as echarts from 'echarts'

const username = ref(localStorage.getItem('username') || '用户')
const backendStatus = ref('未检测')
const barChartRef = ref(null)
const loading = ref(true)

// ── 统计 ──
const stats = reactive({
  totalContracts: 0,
  pendingCount: 0,
  totalRisks: 0,
  completedCount: 0,
})

// ── 最近合同 ──
const recentContracts = ref([])

const TYPE_MAP = {
  purchase: '采购合同', sales: '销售合同', nda: '保密协议',
  outsourcing: '服务外包合同', employment: '劳动合同', other: '其他合同',
}
function typeLabel(type) { return TYPE_MAP[type] || type || '未分类' }

function statusLabel(s) {
  const map = { uploaded: '已上传', parsed: '已解析', auditing: '审核中', completed: '已完成', deleted: '已删除' }
  return map[s] || s || '未知'
}
function statusTag(s) {
  if (s === 'completed') return 'success'
  if (s === 'auditing') return 'warning'
  return 'info'
}
function scoreTag(score) {
  if (score >= 60) return 'danger'
  if (score >= 30) return 'warning'
  return 'success'
}

// ── 后端连通性 ──
async function checkBackend() {
  try {
    const res = await request.get('/health')
    backendStatus.value = '✅ 后端连通 — ' + JSON.stringify(res)
  } catch (e) {
    backendStatus.value = '❌ 后端未启动，请确认 uvicorn 在运行'
  }
}

// ── 加载数据 ──
async function fetchData() {
  loading.value = true
  try {
    const res = await getContractList({ page: 1, page_size: 5, status: '' })
    const items = res.data?.items || []
    const total = res.data?.total || 0

    // 计数统计
    let pending = 0
    let completed = 0
    let risks = 0
    for (const c of items) {
      if (c.status === 'parsed' || c.status === 'uploaded') pending++
      if (c.status === 'completed') completed++
    }

    // 尝试取报告拿评分 + 累计风险数
    const enriched = await Promise.all(
      items.map(async (c) => {
        try {
          const reportRes = await request.get(`/contracts/${c.id}/audit-report`)
          const rd = reportRes.data || {}
          const score = rd.risk_score ?? 0
          risks += (rd.high_risk_count || 0) + (rd.mid_risk_count || 0) + (rd.low_risk_count || 0)
          return { ...c, _score: score }
        } catch {
          return { ...c, _score: undefined }
        }
      })
    )

    recentContracts.value = enriched
    stats.totalContracts = total
    stats.pendingCount = pending
    stats.completedCount = completed
    stats.totalRisks = risks
  } catch (e) {
    console.warn('首页数据加载失败:', e)
    recentContracts.value = []
  } finally {
    loading.value = false
    await nextTick()
    initBarChart()
  }
}

// ── 近 7 天审核量（从 contract 列表推算） ──
let chartInstance = null

async function initBarChart() {
  if (!barChartRef.value) return

  // 生成近 7 天日期标签
  const days = []
  const now = new Date()
  for (let i = 6; i >= 0; i--) {
    const d = new Date(now)
    d.setDate(d.getDate() - i)
    days.push(`${d.getMonth() + 1}/${d.getDate()}`)
  }

  // 从 API 获取更多合同来算每日审核量
  let countByDay = new Array(7).fill(0)
  try {
    const res = await getContractList({ page: 1, page_size: 100 })
    const items = res.data?.items || []
    const today = new Date()
    today.setHours(0, 0, 0, 0)
    for (const c of items) {
      if (!c.created_at) continue
      const dt = new Date(c.created_at)
      if (isNaN(dt.getTime())) continue
      const diffDays = Math.floor((today - dt) / (1000 * 60 * 60 * 24))
      const idx = 6 - diffDays
      if (idx >= 0 && idx < 7) countByDay[idx]++
    }
  } catch { /* fallback to zeros */ }

  chartInstance?.dispose()
  chartInstance = echarts.init(barChartRef.value)
  chartInstance.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
    xAxis: { type: 'category', data: days },
    yAxis: { type: 'value', name: '审核数', minInterval: 1 },
    series: [{
      name: '审核量',
      type: 'bar',
      data: countByDay,
      itemStyle: { color: '#409EFF' },
      barWidth: '50%',
    }],
  })
  window.addEventListener('resize', handleResize)
}

function handleResize() {
  chartInstance?.resize()
}

onMounted(() => {
  fetchData()
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

.backend-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.backend-label { color: #606266; }

.stat-row { margin-bottom: 20px; }

.stat-suffix { font-size: 14px; }
.suffix-green { color: #67C23A; }
.suffix-orange { color: #E6A23C; }
.suffix-red { color: #F56C6C; }
.suffix-blue { color: #409EFF; }

.card-header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.loading-state { padding: 20px 0; }

.chart-container {
  width: 100%;
  height: 300px;
}
</style>
