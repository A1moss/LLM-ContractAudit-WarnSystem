<template>
  <Transition name="ai-warm-fade">
    <div v-if="visible" class="ai-warm-inline" :class="inlineClass">
      <span class="text">{{ text }}</span>
      <span v-if="isFailed" class="link" @click="retry()">重新检查</span>
    </div>
  </Transition>
</template>

<script setup>
import { ref, computed, watch, onUnmounted } from 'vue'
import { state, retry } from '../composables/useAiWarmup.js'

const READY_HIDE_MS = 1500 // ready 文案展示 1.5 秒后自动消失

const readyHidden = ref(false)
let hideTimer = null

watch(
  () => state.value,
  (s) => {
    if (hideTimer) {
      clearTimeout(hideTimer)
      hideTimer = null
    }
    readyHidden.value = false
    if (s === 'ready') {
      hideTimer = setTimeout(() => {
        hideTimer = null
        readyHidden.value = true
      }, READY_HIDE_MS)
    }
  },
  { immediate: true }
)

onUnmounted(() => {
  if (hideTimer) {
    clearTimeout(hideTimer)
    hideTimer = null
  }
})

const isWarming = computed(() => state.value === 'warming')
const isReady = computed(() => state.value === 'ready')
const isFailed = computed(() => state.value === 'failed')

const visible = computed(() => {
  if (state.value === 'hidden') return false
  if (isReady.value && readyHidden.value) return false
  return true
})

const inlineClass = computed(() => ({
  'is-warming': isWarming.value,
  'is-ready': isReady.value,
  'is-failed': isFailed.value,
}))

const text = computed(() => {
  if (isReady.value) return '✓ AI 教练已准备好。可以放心上传合同。'
  if (isFailed.value) return '! AI 教练今天状态不太好。智能审核服务暂时未准备完成，文件仍可上传，但首次审核可能失败。'
  return '⏳ AI 教练正在热身……首次处理可能会稍慢，文件仍可正常上传，准备完成后自动加速。'
})
</script>

<style scoped>
.ai-warm-inline {
  display: flex;
  align-items: center;
  gap: 10px;
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 12.5px;
  line-height: 1.6;
  margin-bottom: 14px;
}

.text {
  min-width: 0;
}

/* —— warming：浅蓝 —— */
.is-warming {
  background: #EEF3FE;
  border: 1px solid #D6E2FB;
  color: #3A4E86;
}

/* —— ready：浅绿 —— */
.is-ready {
  background: #EDF8F1;
  border: 1px solid #CFE9D9;
  color: #2E7A4E;
}

/* —— failed：浅琥珀 —— */
.is-failed {
  background: #FDF6EC;
  border: 1px solid #F5E1BF;
  color: #9A6A1C;
}

.link {
  margin-left: auto;
  flex-shrink: 0;
  color: #B77A1E;
  text-decoration: underline;
  cursor: pointer;
  white-space: nowrap;
}
.link:hover {
  color: #E6A23C;
}

/* 状态切换淡入淡出 300ms */
.ai-warm-fade-enter-active,
.ai-warm-fade-leave-active {
  transition: opacity 300ms ease;
}
.ai-warm-fade-enter-from,
.ai-warm-fade-leave-to {
  opacity: 0;
}
</style>
