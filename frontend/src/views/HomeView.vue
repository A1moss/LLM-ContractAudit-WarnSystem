<template>
  <div class="page-container">
    <!-- 顶部：欢迎语 + 退出按钮 -->
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
          <el-statistic title="今日审核" :value="12">
            <template #suffix>
              <span class="stat-suffix suffix-green">份</span>
            </template>
          </el-statistic>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="待处理" :value="5">
            <template #suffix>
              <span class="stat-suffix suffix-orange">份</span>
            </template>
          </el-statistic>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="本月风险" :value="38">
            <template #suffix>
              <span class="stat-suffix suffix-red">条</span>
            </template>
          </el-statistic>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="通过率" :value="92.3">
            <template #suffix>
              <span class="stat-suffix suffix-blue">%</span>
            </template>
          </el-statistic>
        </el-card>
      </el-col>
    </el-row>

    <!-- 最近合同列表 -->
    <el-card shadow="hover" class="section-card">
      <template #header>
        <span>最近合同</span>
      </template>
      <el-table :data="recentContracts" stripe>
        <el-table-column prop="filename" label="文件名" min-width="200" show-overflow-tooltip />
        <el-table-column prop="type" label="类型" width="120" />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusTag(row.status)" size="small">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="riskLevel" label="风险等级" width="100">
          <template #default="{ row }">
            <el-tag :type="riskTag(row.riskLevel)" size="small">{{ row.riskLevel }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="uploadTime" label="上传时间" width="160" />
      </el-table>
    </el-card>

    <!-- 近 7 天审核量柱状图 -->
    <el-card shadow="hover">
      <template #header>
        <span>近 7 天审核量</span>
      </template>
      <div ref="barChartRef" class="chart-container"></div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import axios from 'axios'
import * as echarts from 'echarts'

const router = useRouter()
const username = ref(localStorage.getItem('username') || '用户')
const backendStatus = ref('未检测')
const barChartRef = ref(null)

async function checkBackend() {
  try {
    const res = await axios.get('/api/health')
    backendStatus.value = '✅ 后端连通！' + JSON.stringify(res.data)
  } catch (e) {
    backendStatus.value = '❌ 后端未启动，请确认 uvicorn 在运行'
  }
}

// ── 最近合同（写死数据） ──
const recentContracts = [
  { filename: '2026年度采购框架协议.pdf', type: '采购合同', status: '审核完成', riskLevel: '高风险', uploadTime: '2026-07-17 14:30' },
  { filename: '员工保密协议-李四.docx', type: '保密协议', status: '审核完成', riskLevel: '中风险', uploadTime: '2026-07-17 11:20' },
  { filename: '软件开发外包合同-v2.pdf', type: '服务合同', status: '审核中', riskLevel: '—', uploadTime: '2026-07-17 09:15' },
  { filename: '办公室租赁合同.pdf', type: '租赁合同', status: '待审核', riskLevel: '—', uploadTime: '2026-07-16 16:45' },
  { filename: '战略合作协议-XX科技.docx', type: '合作协议', status: '审核完成', riskLevel: '低风险', uploadTime: '2026-07-16 10:00' },
]

function statusTag(status) {
  if (status === '审核完成') return 'success'
  if (status === '审核中') return 'warning'
  return 'info'
}

function riskTag(level) {
  if (level === '高风险') return 'danger'
  if (level === '中风险') return 'warning'
  if (level === '低风险') return 'success'
  return 'info'
}

// ── ECharts 柱状图 ──
let chartInstance = null

function initBarChart() {
  chartInstance = echarts.init(barChartRef.value)
  chartInstance.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
    xAxis: {
      type: 'category',
      data: ['07/11', '07/12', '07/13', '07/14', '07/15', '07/16', '07/17'],
    },
    yAxis: { type: 'value', name: '审核数', minInterval: 1 },
    series: [{
      name: '审核量',
      type: 'bar',
      data: [3, 5, 2, 8, 4, 6, 7],
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
  initBarChart()
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

.page-header h2 {
  margin: 0;
}

.page-subtitle {
  margin: 4px 0 0 0;
  color: #909399;
  font-size: 14px;
}

.section-card {
  margin-bottom: 20px;
}

.backend-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.backend-label {
  color: #606266;
}

.stat-row {
  margin-bottom: 20px;
}

.stat-suffix {
  font-size: 14px;
}

.suffix-green {
  color: #67C23A;
}

.suffix-orange {
  color: #E6A23C;
}

.suffix-red {
  color: #F56C6C;
}

.suffix-blue {
  color: #409EFF;
}

.chart-container {
  width: 100%;
  height: 300px;
}
</style>
