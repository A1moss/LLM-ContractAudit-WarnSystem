<template>
  <div class="page-container">
    <h3>审核报告</h3>
    <el-divider />

    <!-- ECharts 饼图 + 柱状图 -->
    <el-row :gutter="20">
      <el-col :span="12">
        <el-card shadow="hover">
          <template #header>
            <span>风险等级分布</span>
          </template>
          <div ref="pieChartRef" class="chart-box"></div>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="hover">
          <template #header>
            <span>各类型风险数量</span>
          </template>
          <div ref="barChartRef" class="chart-box"></div>
        </el-card>
      </el-col>
    </el-row>

    <!-- PDF 预览 -->
    <el-card shadow="hover" class="pdf-card">
      <template #header>
        <span>合同原文预览</span>
      </template>
      <div class="pdf-preview">
        <canvas v-if="!pdfError" ref="pdfCanvasRef" class="pdf-canvas"></canvas>
        <div v-if="pdfError" class="pdf-error">
          <el-icon :size="40"><Warning /></el-icon>
          <p>PDF 加载失败：{{ pdfError }}</p>
          <p class="pdf-error-hint">请确保 frontend/public/test.pdf 文件存在</p>
        </div>
        <div class="pdf-footer">
          第 1 页 / 共 <span v-if="totalPages">{{ totalPages }}</span><span v-else>...</span> 页
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { Warning } from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import * as pdfjsLib from 'pdfjs-dist'
import pdfjsWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

// --- ECharts 饼图 ---
const pieChartRef = ref(null)
let pieChartInstance = null

const initPieChart = () => {
  pieChartInstance = echarts.init(pieChartRef.value)
  pieChartInstance.setOption({
    tooltip: { trigger: 'item' },
    legend: { bottom: '0%' },
    series: [
      {
        name: '风险等级',
        type: 'pie',
        radius: ['40%', '70%'],
        label: { show: true, formatter: '{b}: {c} 条' },
        data: [
          { value: 3, name: '高风险', itemStyle: { color: '#F56C6C' } },
          { value: 7, name: '中风险', itemStyle: { color: '#E6A23C' } },
          { value: 5, name: '低风险', itemStyle: { color: '#67C23A' } },
        ],
      },
    ],
  })
}

// --- ECharts 柱状图 ---
const barChartRef = ref(null)
let barChartInstance = null

const initBarChart = () => {
  barChartInstance = echarts.init(barChartRef.value)
  barChartInstance.setOption({
    tooltip: { trigger: 'axis' },
    xAxis: {
      type: 'category',
      data: ['R01', 'R02', 'R03', 'R04', 'R05', 'R06', 'R07', 'R08', 'R09', 'R10', 'R11', 'R12'],
      axisLabel: { rotate: 45 }
    },
    yAxis: { type: 'value', name: '数量' },
    series: [
      {
        name: '风险数量',
        type: 'bar',
        data: [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
        itemStyle: { color: '#409EFF' }
      },
    ],
  })
}

// --- pdf.js 渲染第一页 ---
const pdfCanvasRef = ref(null)
const totalPages = ref(0)
const pdfError = ref('')

const renderPdf = async () => {
  pdfjsLib.GlobalWorkerOptions.workerSrc = pdfjsWorker

  try {
    const loadingTask = pdfjsLib.getDocument({ url: '/test.pdf' })
    const pdf = await loadingTask.promise
    totalPages.value = pdf.numPages

    const page = await pdf.getPage(1)
    const canvas = pdfCanvasRef.value
    const viewport = page.getViewport({ scale: 1.0 })

    const maxWidth = 600
    const containerWidth = Math.min(canvas.parentElement.clientWidth - 2, maxWidth)
    const scale = containerWidth / viewport.width
    const scaledViewport = page.getViewport({ scale })

    canvas.width = scaledViewport.width
    canvas.height = scaledViewport.height

    const ctx = canvas.getContext('2d')
    await page.render({ canvasContext: ctx, viewport: scaledViewport }).promise
  } catch (e) {
    pdfError.value = e.message
    console.warn('PDF 加载失败:', e.message)
  }
}

onMounted(() => {
  initPieChart()
  initBarChart()
  renderPdf()
})

onUnmounted(() => {
  pieChartInstance?.dispose()
  pieChartInstance = null
  barChartInstance?.dispose()
  barChartInstance = null
})
</script>

<style scoped>
.page-container {
  padding: 24px;
  max-width: 1200px;
  margin: 0 auto;
}

.chart-box {
  width: 100%;
  height: 350px;
}

.pdf-card {
  margin-top: 20px;
}

.pdf-preview {
  text-align: center;
}

.pdf-canvas {
  border: 1px solid #ddd;
  max-width: 100%;
}

.pdf-error {
  padding: 50px;
  color: #999;
}

.pdf-error-hint {
  font-size: 12px;
}

.pdf-footer {
  margin-top: 10px;
  color: #999;
  font-size: 13px;
}
</style>
