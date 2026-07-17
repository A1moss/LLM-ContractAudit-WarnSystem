<template>
  <el-tag
    :type="tagType"
    :size="size"
    :effect="effect"
    :disable-transitions="true"
  >
    {{ displayText }}
  </el-tag>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  level: {
    type: String,
    required: true,
    validator: (v) =>
      ['高风险', '中风险', '低风险', 'high', 'medium', 'low'].includes(v),
  },
  size: {
    type: String,
    default: 'default',
    validator: (v) => ['large', 'default', 'small'].includes(v),
  },
  effect: {
    type: String,
    default: 'dark',
    validator: (v) => ['dark', 'light', 'plain'].includes(v),
  },
})

const LEVEL_MAP = {
  high: '高风险',
  medium: '中风险',
  low: '低风险',
  高风险: '高风险',
  中风险: '中风险',
  低风险: '低风险',
}

const TYPE_MAP = {
  high: 'danger',
  medium: 'warning',
  low: 'success',
  高风险: 'danger',
  中风险: 'warning',
  低风险: 'success',
}

const displayText = computed(() => LEVEL_MAP[props.level] || props.level)
const tagType = computed(() => TYPE_MAP[props.level] || 'info')
</script>
