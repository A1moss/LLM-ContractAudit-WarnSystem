<template>
  <div style="padding: 30px; max-width: 900px; margin: 0 auto;">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px;">
      <h1 style="margin: 0;">A24 合同智能审核系统</h1>
      <el-button type="primary" size="large" @click="goUpload">
        <el-icon style="margin-right: 6px;"><Plus /></el-icon>
        上传新合同
      </el-button>
    </div>

    <!-- 后端连通性 -->
    <el-card shadow="hover" style="margin-bottom: 20px;">
      <div style="display: flex; align-items: center; gap: 12px;">
        <span style="color: #606266;">后端状态：</span>
        <el-tag v-if="backendStatus === '未检测'" type="info">未检测</el-tag>
        <el-tag v-else-if="backendStatus.startsWith('✅')" type="success">已连通</el-tag>
        <el-tag v-else type="danger">未连通</el-tag>
        <el-button size="small" @click="checkBackend">测试连通性</el-button>
      </div>
    </el-card>

    <!-- 近 7 天审核量柱状图 -->
    <el-card shadow="hover">
      <template #header>
        <span>近 7 天审核量</span>
      </template>
      <div ref="barChartRef" style="width: 100%; height: 320px;"></div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { Plus } from '@element-plus/icons-vue'
import axios from 'axios'
import * as echarts from 'echarts'

const router = useRouter()
const backendStatus = ref('未检测')
const barChartRef = ref(null)

function goUpload() {
  router.push('/contracts/upload')
}

async function checkBackend() {
  try {
    const res = await axios.get('/api/health')
    backendStatus.value = '✅ 后端连通！' + JSON.stringify(res.data)
  } catch (e) {
    backendStatus.value = '❌ 后端未启动，请确认 uvicorn 在运行'
  }
}

let chartInstance = null

// ── ECharts：近 7 天审核量（写死数据） ──
function initBarChart() {
  chartInstance = echarts.init(barChartRef.value)
  chartInstance.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
    xAxis: {
      type: 'category',
      data: ['07/11', '07/12', '07/13', '07/14', '07/15', '07/16', '07/17'],
    },
    yAxis: {
      type: 'value',
      name: '审核数',
      minInterval: 1,
    },
    series: [
      {
        name: '审核量',
        type: 'bar',
        data: [3, 5, 2, 8, 4, 6, 7],
        itemStyle: { color: '#409EFF' },
        barWidth: '50%',
      },
    ],
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
