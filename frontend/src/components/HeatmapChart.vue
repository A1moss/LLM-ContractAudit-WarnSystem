<template>
  <div class="heatmap-wrapper">
    <!-- Loading -->
    <div v-if="loading" class="heatmap-loading">
      <el-skeleton :rows="4" animated />
    </div>

    <!-- Empty / no data -->
    <div v-else-if="!hasData" class="heatmap-empty">
      <el-icon :size="28"><Warning /></el-icon>
      <p>暂无风险热力图数据</p>
      <span class="hint">上传合同并完成审核后，风险分布将在此展示</span>
    </div>

    <!-- Chart -->
    <div
      v-else
      ref="chartRef"
      class="heatmap-chart"
      :style="{ height: chartHeight + 'px' }"
    ></div>

    <!-- Legend -->
    <div v-if="hasData" class="heatmap-legend">
      <span class="legend-label">风险密度</span>
      <div class="legend-gradient"></div>
      <span class="legend-end">低</span>
      <span class="legend-end">高</span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { Warning } from '@element-plus/icons-vue'
import * as echarts from 'echarts'

const props = defineProps({
  /** Backend heatmap data: { matrix: [[x,y,value],...], xAxis: [...], yAxis: [...], maxDensity: int } */
  data: { type: Object, default: null },
  /** Loading state from parent */
  loading: { type: Boolean, default: false },
  /** Chart height in px */
  height: { type: Number, default: 300 },
})

const emit = defineEmits(['ready'])

const chartRef = ref(null)
let chartInstance = null

const chartHeight = computed(() => props.height)
const hasData = computed(() => {
  const d = props.data
  return d && d.matrix && d.matrix.length > 0
})

function buildOption() {
  const d = props.data
  if (!d || !d.matrix || d.matrix.length === 0) return null

  const maxVal = d.maxDensity || 1

  // Color stops: green(0) -> yellow(mid) -> orange -> red(max)
  const pieces = []
  if (maxVal >= 4) {
    pieces.push(
      { min: 0, max: 0, color: '#f5f7fa' },
      { min: 1, max: 1, color: '#a3cfbb' },
      { min: 2, max: 2, color: '#f9e45b' },
      { min: 3, max: 3, color: '#f5a623' },
      { min: 4, color: '#d92b2b' }
    )
  } else if (maxVal >= 3) {
    pieces.push(
      { min: 0, max: 0, color: '#f5f7fa' },
      { min: 1, max: 1, color: '#a3cfbb' },
      { min: 2, max: 2, color: '#f9e45b' },
      { min: 3, color: '#d92b2b' }
    )
  } else {
    pieces.push(
      { min: 0, max: 0, color: '#f5f7fa' },
      { min: 1, max: 1, color: '#f9e45b' },
      { min: 2, color: '#d92b2b' }
    )
  }

  // Truncate x-axis labels for readability
  const xLabels = (d.xAxis || []).map(label =>
    label.length > 8 ? label.slice(0, 7) + '…' : label
  )

  return {
    tooltip: {
      position: 'top',
      formatter: (params) => {
        if (!params.value) return ''
        const [x, y, v] = params.value
        const riskType = (d.yAxis || [])[y] || '?'
        const paragraph = (d.xAxis || [])[x] || `第${x + 1}段`
        if (v === 0 || v == null) return ''
        return `${paragraph}<br/>风险：${riskType}<br/>密度：<b>${v}</b>`
      },
    },
    grid: {
      left: 60,
      right: 20,
      top: 10,
      bottom: xLabels.length > 15 ? 80 : 60,
    },
    xAxis: {
      type: 'category',
      data: xLabels,
      splitArea: { show: true },
      axisLabel: {
        rotate: xLabels.length > 12 ? 45 : 0,
        fontSize: 11,
      },
    },
    yAxis: {
      type: 'category',
      data: d.yAxis || [],
      splitArea: { show: true },
      axisLabel: { fontSize: 11 },
    },
    visualMap: {
      min: 0,
      max: maxVal,
      calculable: true,
      orient: 'horizontal',
      left: 'center',
      bottom: 0,
      pieces,
      show: false, // we render our own legend
    },
    series: [
      {
        name: '风险密度',
        type: 'heatmap',
        data: d.matrix,
        label: {
          show: d.matrix.length <= 30,
          fontSize: 10,
        },
        emphasis: {
          itemStyle: {
            shadowBlur: 10,
            shadowColor: 'rgba(0, 0, 0, 0.5)',
          },
        },
      },
    ],
  }
}

function initChart() {
  if (!chartRef.value || !hasData.value) return

  disposeChart()

  chartInstance = echarts.init(chartRef.value)
  const option = buildOption()
  if (option) {
    chartInstance.setOption(option)
    emit('ready')
  }
}

function disposeChart() {
  chartInstance?.dispose()
  chartInstance = null
}

function handleResize() {
  chartInstance?.resize()
}

watch(
  () => [props.data, props.loading],
  async () => {
    if (!props.loading && props.data) {
      await nextTick()
      initChart()
    }
  },
  { deep: true }
)

onMounted(() => {
  if (!props.loading && props.data) {
    nextTick(() => initChart())
  }
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  disposeChart()
})
</script>

<style scoped>
.heatmap-wrapper {
  width: 100%;
}

.heatmap-loading {
  padding: 20px 0;
}

.heatmap-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
  color: #909399;
  min-height: 200px;
  gap: 8px;
}

.heatmap-empty .hint {
  font-size: 12px;
  color: #c0c4cc;
}

.heatmap-chart {
  width: 100%;
  min-height: 200px;
}

.heatmap-legend {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin-top: 8px;
  font-size: 12px;
  color: #606266;
}

.legend-label {
  font-weight: 500;
}

.legend-gradient {
  width: 120px;
  height: 12px;
  border-radius: 6px;
  background: linear-gradient(to right, #a3cfbb, #f9e45b, #f5a623, #d92b2b);
}

.legend-end {
  font-size: 11px;
  color: #909399;
}
</style>
