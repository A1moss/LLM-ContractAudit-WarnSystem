<template>
  <Transition name="ai-warm-fade">
    <div v-if="visible" class="ai-warm-banner">
      <div class="banner-card" :class="cardClass">
        <!-- 教练图标（内联 SVG，无图片资源） -->
        <svg class="coach" viewBox="0 0 64 64" width="60" height="60" aria-hidden="true">
          <defs>
            <linearGradient id="aiWarmCoachBlue" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stop-color="#1935C3" />
              <stop offset="100%" stop-color="#1B70F0" />
            </linearGradient>
            <linearGradient id="aiWarmCoachGreen" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stop-color="#2E9B5B" />
              <stop offset="100%" stop-color="#46B873" />
            </linearGradient>
            <linearGradient id="aiWarmCoachAmber" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stop-color="#E6A23C" />
              <stop offset="100%" stop-color="#F0BE72" />
            </linearGradient>
          </defs>

          <!-- 外圈虚线圆 -->
          <circle class="ring ring-dashed" cx="32" cy="36" r="26" stroke-dasharray="3 5" />
          <!-- 内圈实线圆 -->
          <circle class="ring ring-solid" cx="32" cy="36" r="20" />

          <!-- 顶部短天线 -->
          <line class="antenna" x1="32" y1="17" x2="32" y2="9" />
          <circle class="antenna-tip" cx="32" cy="8" r="1.8" />

          <!-- 中心圆角矩形 -->
          <rect x="13" y="17" width="38" height="38" rx="8" :fill="`url(#${gradientId})`" />

          <!-- 表情：ready = ✓，failed = !，其余 = 两个白点 -->
          <path
            v-if="isReady"
            d="M23 36.5 L29 42.5 L41 29.5"
            fill="none"
            stroke="#fff"
            stroke-width="3.2"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
          <g v-else-if="isFailed" fill="#fff">
            <rect x="30.4" y="25" width="3.2" height="13" rx="1.6" />
            <circle cx="32" cy="43.5" r="2.1" />
          </g>
          <g v-else fill="#fff">
            <circle cx="25" cy="34" r="3" />
            <circle cx="39" cy="34" r="3" />
          </g>
        </svg>

        <!-- 文案 -->
        <div class="body">
          <div class="title-row">
            <span class="title">{{ title }}</span>
            <span v-if="isWarming" class="dots" aria-hidden="true">
              <i /><i /><i />
            </span>
          </div>
          <div class="sub">{{ subtitle }}</div>
          <div v-if="isWarming" class="bar" aria-hidden="true"><span /></div>
        </div>

        <!-- 失败：重新检查 -->
        <button v-if="isFailed" type="button" class="retry-btn" @click="retry()">重新检查</button>
      </div>
    </div>
  </Transition>
</template>

<script setup>
import { computed } from 'vue'
import { state, retry } from '../composables/useAiWarmup.js'

const current = computed(() => state.value)

// hidden（含 ready 展示 2.5 秒后自动收起）不显示
const visible = computed(() => current.value !== 'hidden')

const isWarming = computed(() => current.value === 'warming')
const isReady = computed(() => current.value === 'ready')
const isFailed = computed(() => current.value === 'failed')

const cardClass = computed(() => ({
  'is-warming': isWarming.value,
  'is-ready': isReady.value,
  'is-failed': isFailed.value,
}))

const gradientId = computed(() => {
  if (isReady.value) return 'aiWarmCoachGreen'
  if (isFailed.value) return 'aiWarmCoachAmber'
  return 'aiWarmCoachBlue'
})

const title = computed(() => {
  if (isReady.value) return 'AI 教练已准备好'
  if (isFailed.value) return 'AI 教练今天状态不太好'
  return 'AI 教练正在热身……'
})

const subtitle = computed(() => {
  if (isReady.value) return '智能审核能力已就绪，现在可以开始上传合同。'
  if (isFailed.value) return '智能审核服务暂时未准备完成，请稍后再试。'
  return '首次使用需要一点准备时间，准备完成后处理会更快。'
})
</script>

<style scoped>
/* 位置与现有 metrics 的负边距视觉关系一致：上移 30px 压住 hero 底部 */
.ai-warm-banner {
  position: relative;
  z-index: 5;
  margin: -30px auto 34px;
  padding: 0 40px;
}

.banner-card {
  max-width: 1200px;
  margin: 0 auto;
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 14px 22px;
  background: #fff;
  border: 1px solid var(--a24-border);
  border-radius: 12px;
  box-shadow: 0 2px 10px rgba(13, 36, 71, 0.04);
}

.coach {
  flex-shrink: 0;
  display: block;
}

/* —— 教练图标：外圈虚线 / 内圈实线 —— */
.ring {
  fill: none;
  stroke-width: 1.6;
}
.ring-dashed {
  stroke: rgba(25, 53, 195, 0.45);
  transform-origin: 32px 36px;
}
.ring-solid {
  stroke: rgba(25, 53, 195, 0.22);
  transform-origin: 32px 36px;
}
.is-ready .ring-dashed { stroke: rgba(46, 155, 91, 0.5); }
.is-ready .ring-solid { stroke: rgba(46, 155, 91, 0.25); }
.is-failed .ring-dashed { stroke: rgba(230, 162, 60, 0.55); }
.is-failed .ring-solid { stroke: rgba(230, 162, 60, 0.28); }

.antenna {
  stroke: rgba(25, 53, 195, 0.5);
  stroke-width: 1.6;
  stroke-linecap: round;
}
.antenna-tip { fill: rgba(25, 53, 195, 0.5); }
.is-ready .antenna,
.is-ready .antenna-tip { stroke: rgba(46, 155, 91, 0.55); fill: rgba(46, 155, 91, 0.55); }
.is-failed .antenna,
.is-failed .antenna-tip { stroke: rgba(230, 162, 60, 0.6); fill: rgba(230, 162, 60, 0.6); }

/* warming：外圈顺时针 3.2s，内圈逆时针 5.5s */
.is-warming .ring-dashed { animation: ai-warm-spin-cw 3.2s linear infinite; }
.is-warming .ring-solid { animation: ai-warm-spin-ccw 5.5s linear infinite; }

@keyframes ai-warm-spin-cw {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
@keyframes ai-warm-spin-ccw {
  from { transform: rotate(0deg); }
  to { transform: rotate(-360deg); }
}

/* —— 文案 —— */
.body {
  min-width: 0;
  flex: 1;
}

.title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.title {
  font-size: 15px;
  font-weight: 600;
  color: var(--a24-heading);
}

.sub {
  margin-top: 3px;
  font-size: 12.5px;
  color: var(--a24-muted);
}

/* 三个跳动小圆点 */
.dots {
  display: inline-flex;
  align-items: flex-end;
  gap: 4px;
  height: 14px;
}
.dots i {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--a24-primary-2);
  display: block;
  animation: ai-warm-dot 1.2s ease-in-out infinite;
}
.dots i:nth-child(2) { animation-delay: 0.15s; }
.dots i:nth-child(3) { animation-delay: 0.3s; }

@keyframes ai-warm-dot {
  0%, 60%, 100% { transform: translateY(0); opacity: 0.4; }
  30% { transform: translateY(-4px); opacity: 1; }
}

/* 不确定进度（非百分比） */
.bar {
  position: relative;
  width: 220px;
  height: 3px;
  margin-top: 10px;
  border-radius: 2px;
  background: rgba(25, 53, 195, 0.1);
  overflow: hidden;
}
.bar span {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 40%;
  border-radius: 2px;
  background: linear-gradient(90deg, var(--a24-primary), var(--a24-primary-2));
  animation: ai-warm-bar 1.6s ease-in-out infinite;
}

@keyframes ai-warm-bar {
  0% { left: -40%; }
  100% { left: 100%; }
}

/* —— 失败：描边按钮 —— */
.retry-btn {
  flex-shrink: 0;
  margin-left: auto;
  padding: 6px 16px;
  font-size: 12.5px;
  color: #B77A1E;
  background: transparent;
  border: 1px solid #E6A23C;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.18s, color 0.18s;
}
.retry-btn:hover {
  background: #FDF6EC;
}

/* —— 状态切换：整体 300ms 淡入淡出 —— */
.ai-warm-fade-enter-active,
.ai-warm-fade-leave-active {
  transition: opacity 300ms ease;
}
.ai-warm-fade-enter-from,
.ai-warm-fade-leave-to {
  opacity: 0;
}

@media (max-width: 900px) {
  .ai-warm-banner { padding: 0 20px; }
  .banner-card { flex-wrap: wrap; }
}
</style>
