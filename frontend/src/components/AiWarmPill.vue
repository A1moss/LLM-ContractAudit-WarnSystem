<template>
  <div v-if="visible" class="ai-warm-pill" :class="pillClass">
    <span class="dot" aria-hidden="true"></span>
    <span class="txt">{{ text }}</span>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { state, everReady } from '../composables/useAiWarmup.js'

const route = useRoute()

// 登录页不显示
const visible = computed(() => {
  if (route.path === '/login') return false
  // 未显示且从未 ready 过 → 完全不显示
  if (state.value === 'hidden') return everReady.value
  return true
})

const pillClass = computed(() => {
  if (state.value === 'warming') return 'is-warming'
  if (state.value === 'failed') return 'is-failed'
  if (state.value === 'ready') return 'is-ready'
  // hidden + everReady：保持绿色已就绪
  return everReady.value ? 'is-ready' : ''
})

const text = computed(() => {
  if (state.value === 'warming') return 'AI 教练热身中'
  if (state.value === 'failed') return 'AI 教练未就绪'
  return 'AI 教练已就绪'
})
</script>

<style scoped>
.ai-warm-pill {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 26px;
  padding: 0 12px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.07);
  font-size: 12.5px;
  line-height: 1;
  white-space: nowrap;
  user-select: none;
  flex-shrink: 0;
  margin-right: 12px;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  display: block;
}

.txt {
  color: #C9D6EE;
}

/* —— warming：蓝点 + 呼吸扩散 —— */
.is-warming .dot {
  background: #3D87F5;
  animation: ai-warm-breath 1.8s ease-out infinite;
}
.is-warming .txt {
  color: #CFE0FF;
}

@keyframes ai-warm-breath {
  0% { box-shadow: 0 0 0 0 rgba(61, 135, 245, 0.75); }
  70% { box-shadow: 0 0 0 9px rgba(61, 135, 245, 0); }
  100% { box-shadow: 0 0 0 9px rgba(61, 135, 245, 0); }
}

/* —— ready：绿色静态 —— */
.is-ready .dot {
  background: #2E9B5B;
}
.is-ready .txt {
  color: #BFE4CE;
}

/* —— failed：琥珀静态 —— */
.is-failed .dot {
  background: #E6A23C;
}
.is-failed .txt {
  color: #F3D8A8;
}
</style>
