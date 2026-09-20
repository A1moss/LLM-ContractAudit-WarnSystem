# AI 首次加载 / 服务预热状态 · UI/UX 设计方案

> 版本：v1.0　日期：2026-09-19
> 范围：仅前端展示层，不改业务逻辑、不改后端、不改上传流程。
> 配套视觉稿：`A24-design-sketches/ai-warmup-design.html`（已移出仓库，位于仓库同级目录；浏览器直接打开可交互预览三态）。

---

## 1. 背景与目标

### 1.1 现状问题

后端 `services/warmup.py` 已经实现了「静启动」：进程起来后在后台线程预热 torch / 向量模型 / 向量库 / OCR，并把状态写入 `STATE`，通过 `GET /api/health` 暴露：

```
{
  "status": "ok",
  "warmup": {
    "rag": "idle | warming | ready | failed",
    "ocr": "idle | warming | ready | unavailable | failed",
    "rag_seconds": 9.2,
    "ocr_seconds": null
  }
}
```

但前端**目前完全没有消费这个接口**。用户在后端刚启动的前 5–10 秒打开页面时会看到：

(1) 首页数据卡空白、图表转圈，没有任何解释；
(2) 一旦点上传，第一次请求会慢到 10 秒以上，用户不知道是「卡了」还是「在算」；
(3) 一旦预热失败，前端只能拿到一个泛化的「网络异常」，无法引导重试。

本方案只解决一个问题：**当后端在热身时，用户眼前看到什么。**

### 1.2 设计目标

(1) 让用户一眼明白「这不是卡死，是 AI 正在准备」；
(2) 用一句有记忆点、不技术化的话替代「RAG 预热中 / 模型加载中 / 向量库准备中」；
(3) 保持企业合同审核系统的专业感，不二次元、不游戏化；
(4) **不阻塞任何业务操作**——用户可以照常浏览、照常上传，只是得到一句温柔的提示；
(5) 不动后端、不动 RAG 分类、不动 LLM / OCR / 风险判定 / 审核流程。

---

## 2. 核心文案（定稿）

| 状态 | 主文案 | 副文案 |
| --- | --- | --- |
| 正在准备 | **AI 教练正在热身……** | 首次使用需要一点准备时间，准备完成后处理会更快。 |
| 准备完成 | AI 教练已准备好 ✓ | （自动 2.5 秒消失，无需副文案） |
| 准备异常 | AI 教练今天状态不太好 | 智能审核服务暂时未准备完成，请稍后再试。 |

红线：

(1) 界面上**禁止**出现 RAG、Embedding、Chroma、向量库、向量模型、torch、ONNX、OCR 等技术词；
(2) 不使用「加载中…」「请稍候」这种干巴巴的系统腔；
(3) 不使用 emoji 表情人物（🤖🏃‍♂️💪 一律禁用），保持商务克制；
(4) 异常态不暴露 traceback / HTTP 状态码 / 接口名。

---

## 3. 视觉方向

### 3.1 与现有系统的关系

完全沿用 `src/styles/theme.css` 的设计 token，不引入新色板：

(1) 主色 `#1935C3`，辅助亮蓝 `#1B70F0`；
(2) 成功绿 `#2E9B5B`，警告琥珀 `#E6A23C`；
(3) 背景 `#F8FAFD`，卡片白底、12px 圆角、1px `#E5EAF2` 边框、`0 2px 10px rgba(13,36,71,.04)` 轻阴影；
(4) 字体栈与全局一致：PingFang / 微软雅黑。

### 3.2 「AI 教练热身」视觉元素

不做复杂插画、不做 chibi 机器人。只做一个 **56×56 的几何徽标**：

(1) 中心是 38×38 的圆角方块（11px 圆角），颜色走主色渐变 `linear-gradient(135deg,#1935C3,#1B70F0)`，和现有 `.a24-page-header .icon` 完全同源；
(2) 方块内只有两个小白圆点当眼睛，头顶一根短天线，保持「抽象助手」感；
(3) 外圈两圈虚线圆环，热身时缓慢反向旋转（外圈 3.2s 顺时针、内圈 5.5s 逆时针），暗示「在活动关节」；
(4) 方块本身做 1.6s 的轻微上下浮动（±2px），模拟「原地踏步热身」；
(5) 完成态：方块变绿，圆环变实线，眼睛换成一个白色 ✓；
(6) 失败态：方块变琥珀，圆环变实线，眼睛换成白色 !。

动画总原则：**动得很慢、幅度很小**。这是企业软件，不是开场动画。

### 3.3 不做的事

(1) 不做全屏 Loading 页；
(2) 不做进度条百分比（后端无法给出真实百分比，做了反而撒谎）；用不确定进度条（indeterminate slide）代替；
(3) 不做骨架屏代替真实内容——真实页面继续渲染，只在上面叠一条提示；
(4) 不更换 `hero.svg`，不改首页 hero 区任何一个像素。

---

## 4. 三个落点（按侵入度从轻到重）

### 4.1 落点 A：导航栏右侧常驻状态点（最轻，全局）

位置：`App.vue` 顶部深色导航右侧，紧贴「个人中心」之前。

(1) 形态：一个 8px 圆点 + 12.5px 文字的胶囊（pill），半透明白底 `rgba(255,255,255,.07)`；
(2) 三态颜色：
   - warming：蓝色 `#3D87F5`，带 1.8s 呼吸扩散动画；
   - ready：绿色 `#2E9B5B`，静态；
   - failed：琥珀 `#E6A23C`，静态；
(3) 文字随状态变化：「AI 教练热身中」/「AI 教练已就绪」/「AI 教练未就绪」；
(4) 登录页 `/login` 不显示（登录时用户还没进入系统，没必要打扰）。

这个点的作用是**全局空气表**：用户在任何子页面都能余光瞟到 AI 的状态，不需要专门看。

### 4.2 落点 B：首页 hero 下方横向 Banner（中等，主动告知）

位置：`HomeView.vue` 里，`.hero` 结束后、`.metrics` 数据条之前。利用现有 `.metrics { margin-top: -34px }` 的负边距位置，Banner 叠在 hero 与数据条之间，宽 100%、左右 40px 边距（与 hero 内容对齐）。

(1) 高度约 92px，白色卡片，左侧 56px 教练徽标，右侧主副文案 + 一条 220px 的不确定进度条；
(2) warming 态：显示主副文案 + 三点跳动 + 进度条滑动；
(3) ready 态：整卡变浅绿 `#F4FBF7`，文案切到「AI 教练已准备好」，**2.5 秒后自动 display:none**；
(4) failed 态：整卡变浅琥珀 `#FDF8EF`，右侧放一个描边按钮「重新检查」，点击重新拉一次 `/api/health`。

### 4.3 落点 C：上传页卡片内联提示（精准，靠近业务动作）

位置：`ContractUpload.vue` 右侧 `el-card` 内部、`<el-form>` 之前（即文件选择后、点提交前的视觉焦点区）。

(1) 形态：一个 8px 圆角的浅色横条，高度自适应，左侧一个小图标 + 12.5px 文字；
(2) warming：浅蓝底 `#EEF3FE`，文案「AI 教练正在热身……首次处理可能会稍慢，文件仍可正常上传，准备完成后自动加速。」
(3) ready：浅绿底，文案「AI 教练已准备好。可以放心上传合同。」，1.5 秒后消失；
(4) failed：浅琥珀底，文案「AI 教练今天状态不太好。智能审核服务暂时未准备完成，文件仍可上传，但首次审核可能失败。」末尾一个「重新检查」文字链接；
(5) **业务按钮「开始上传」始终保持可用**（只要选了文件）。这是硬约束——不因为热身中就禁用提交，只做提示。

> 为什么不屏蔽上传？因为后端 `warmup.py` 的设计哲学本来就是「预热失败也不影响服务，首次使用时会自动重试」。前端若禁用上传，反而把一个非阻塞问题变成了阻塞问题，违背后端原意。

---

## 5. 状态机与轮询策略

### 5.1 状态映射

把后端的 `rag` 字段翻译成前端三态（`ocr` 字段忽略——它只在传扫描件时才相关，普通 docx/pdf 用户不需要感知）：

(1) `rag === 'warming'` → 前端 `warming`；
(2) `rag === 'ready'` → 前端 `ready`（短暂展示后自动落回 `hidden`）；
(3) `rag === 'failed'` 或接口网络错误 → 前端 `failed`；
(4) `rag === 'idle'` → 视为 `warming`（预热线程刚启动但 STATE 还没翻牌的窗口通常 <200ms）；
(5) `ocr` 字段完全不参与 UI 判断。

### 5.2 轮询

(1) 应用启动（`App.vue onMounted`）立刻请求一次 `/api/health`；
(2) 若结果是 `warming`，每 **3 秒**轮询一次；
(3) 一旦切到 `ready` 或 `failed`，停止轮询；
(4) `ready` 态保留 2.5s 后自动隐藏 Banner，但导航小圆点仍保持绿色（让用户随时能看到「现在是热的」）；
(5) `failed` 态下「重新检查」按钮点击后立即拉一次，不改轮询节奏；
(6) 用户重新登录 / 刷新页面时重新开始轮询（无持久化状态，避免「昨天热过今天还用旧状态」的错位）。

### 5.3 容错

(1) `/api/health` 本身 600ms 内没响应，不立即判 failed，等下一次轮询；连续 3 次失败才显示 failed；
(2) 接口 401 由现有 `request.js` 拦截器处理（跳登录），不影响；
(3) 网络完全不通时，Banner 不显示（让首页自己的空状态接管），导航点保持灰色「未知」，避免在离线状态下误报「AI 教练状态不好」。

---

## 6. 落地实现（复制即用）

### 6.1 新增 composable：`src/composables/useAiWarmup.js`

把轮询和状态机收口在一处，三个落点（导航 / 首页 / 上传页）共用同一份状态，避免各写各的。

```javascript
import { ref, onMounted, onUnmounted } from 'vue'
import request from '../utils/request.js'

// 单例：整个 App 共享一份状态
const state = ref('hidden')        // hidden | warming | ready | failed
const pollTimer = null
const failCount = 0

async function checkOnce() {
  try {
    const data = await request.get('/health', { timeout: 1500 })
    const rag = data?.warmup?.rag
    if (rag === 'ready') {
      state.value = 'ready'
      stopPolling()
      // 首页 Banner 2.5s 后自动隐藏，但导航点保持绿色
      setTimeout(() => { if (state.value === 'ready') state.value = 'hidden' }, 2500)
    } else if (rag === 'failed') {
      state.value = 'failed'
      stopPolling()
    } else {
      // idle / warming
      state.value = 'warming'
    }
  } catch {
    failCount++
    if (failCount >= 3) {
      state.value = 'failed'
      stopPolling()
    }
  }
}

function startPolling() {
  checkOnce()
  pollTimer = setInterval(checkOnce, 3000)
}
function stopPolling() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
}

export function useAiWarmup() {
  onMounted(() => { if (!pollTimer) startPolling() })
  onUnmounted(() => { /* 单例不在这里 stop，否则路由切走就停了 */ })
  return {
    warmupState: state,
    retry: () => { failCount = 0; checkOnce() },
  }
}
```

### 6.2 新增组件：`src/components/AiWarmPill.vue`（导航圆点）

挂在 `App.vue` 导航右侧。

```vue
<template>
  <span v-if="state !== 'hidden'" class="ai-warm-pill" :class="state">
    <span class="dot" />
    <span class="text">{{ text }}</span>
  </span>
</template>

<script setup>
import { computed } from 'vue'
import { useAiWarmup } from '../composables/useAiWarmup.js'
const { warmupState: state } = useAiWarmup()
const TEXT = { warming: 'AI 教练热身中', ready: 'AI 教练已就绪', failed: 'AI 教练未就绪' }
const text = computed(() => TEXT[state.value] || '')
</script>

<style scoped>
.ai-warm-pill {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 5px 12px; border-radius: 999px;
  background: rgba(255,255,255,.07); border: 1px solid rgba(255,255,255,.10);
  font-size: 12.5px; color: #C9D6F2;
}
.ai-warm-pill .dot { width: 8px; height: 8px; border-radius: 50%; background: #6E7A95; }
.ai-warm-pill.warming { border-color: rgba(27,112,240,.45); color: #B9D2FF; }
.ai-warm-pill.warming .dot {
  background: #3D87F5;
  animation: breathe 1.8s ease-out infinite;
}
.ai-warm-pill.ready { border-color: rgba(46,155,91,.45); color: #9FE0BC; }
.ai-warm-pill.ready .dot { background: #2E9B5B; }
.ai-warm-pill.failed { border-color: rgba(230,162,60,.55); color: #F0CE9A; }
.ai-warm-pill.failed .dot { background: #E6A23C; }
@keyframes breathe {
  0%   { box-shadow: 0 0 0 0 rgba(61,135,245,.55); }
  70%  { box-shadow: 0 0 0 9px rgba(61,135,245,0); }
  100% { box-shadow: 0 0 0 0 rgba(61,135,245,0); }
}
</style>
```

### 6.3 新增组件：`src/components/AiWarmBanner.vue`（首页横向 Banner）

挂在 `HomeView.vue` 的 hero 与 metrics 之间。教练徽标用 SVG 内联（避免新增静态资源），样式与视觉稿一致。

```vue
<template>
  <div v-if="state !== 'hidden'" class="ai-warm-banner" :class="state">
    <!-- 教练徽标 -->
    <div class="coach">
      <svg viewBox="0 0 56 56" class="coach-svg">
        <circle cx="28" cy="28" r="26" class="ring r1" />
        <circle cx="28" cy="28" r="19" class="ring r2" />
        <rect x="9" y="19" width="38" height="28" rx="8" class="head" />
        <circle cx="20" cy="33" r="2.2" class="eye" />
        <circle cx="36" cy="33" r="2.2" class="eye" />
        <line x1="28" y1="19" x2="28" y2="14" class="antenna" />
      </svg>
    </div>

    <div class="txt">
      <div class="t1">
        {{ state === 'failed' ? 'AI 教练今天状态不太好'
           : state === 'ready' ? 'AI 教练已准备好'
           : 'AI 教练正在热身……' }}
        <span v-if="state === 'warming'" class="dots"><i></i><i></i><i></i></span>
      </div>
      <div class="t2">
        <template v-if="state === 'warming'">首次使用需要一点准备时间，准备完成后处理会更快。</template>
        <template v-else-if="state === 'ready'">智能审核能力已就绪，现在可以开始上传合同。</template>
        <template v-else>智能审核服务暂时未准备完成，请稍后再试。</template>
      </div>
      <div v-if="state === 'warming'" class="bar"><i></i></div>
    </div>

    <button v-if="state === 'failed'" class="retry" @click="retry">重新检查</button>
  </div>
</template>

<script setup>
import { useAiWarmup } from '../composables/useAiWarmup.js'
const { warmupState: state, retry } = useAiWarmup()
</script>

<style scoped>
.ai-warm-banner {
  margin: -30px 40px 0; position: relative; z-index: 5;
  display: flex; align-items: center; gap: 16px;
  padding: 14px 20px; background: #fff;
  border: 1px solid #D6DFF5; border-radius: 12px;
  box-shadow: 0 8px 28px rgba(13,36,71,.10);
}
.ai-warm-banner.ready  { background: #F4FBF7; border-color: #BFE3CE; }
.ai-warm-banner.failed { background: #FDF8EF; border-color: #F0D9B0; }
.t1 { font-size: 15px; font-weight: 600; color: var(--a24-heading); }
.ready .t1 { color: #1E7A48; }
.failed .t1 { color: #A86A14; }
.t2 { font-size: 13px; color: var(--a24-muted); margin-top: 2px; }
.ready .t2 { color: #4A8A68; }
.failed .t2 { color: #8A7040; }
.bar { margin-top: 8px; height: 3px; width: 220px; background: #EDF1F9; border-radius: 2px; overflow: hidden; }
.bar i { display: block; height: 100%; width: 40%; background: linear-gradient(90deg,#1935C3,#1B70F0); animation: slide 1.6s ease-in-out infinite; }
@keyframes slide { 0% { margin-left: -40%; } 100% { margin-left: 100%; } }
.dots { display: inline-flex; gap: 4px; margin-left: 6px; }
.dots i { width: 5px; height: 5px; border-radius: 50%; background: #1B70F0; animation: dot 1.2s infinite; }
.dots i:nth-child(2) { animation-delay: .18s; }
.dots i:nth-child(3) { animation-delay: .36s; }
@keyframes dot { 0%,80%,100% { transform: scale(.6); opacity: .4; } 40% { transform: scale(1); opacity: 1; } }
.retry { border: 1px solid #D9B26A; background: #fff; color: #A86A14; padding: 7px 16px; border-radius: 6px; cursor: pointer; }
.retry:hover { background: #FDF2DC; }

/* 教练徽标动画 */
.coach-svg .ring { fill: none; stroke: rgba(27,112,240,.35); stroke-width: 2; stroke-dasharray: 3 5; transform-origin: 28px 28px; }
.ai-warm-banner.warming .coach-svg .ring.r1 { animation: spin 3.2s linear infinite; }
.ai-warm-banner.warming .coach-svg .ring.r2 { stroke: rgba(25,53,195,.18); animation: spin 5.5s linear infinite reverse; }
.head { fill: url(#g1); }
.eye { fill: #fff; }
.antenna { stroke: #7FB0FF; stroke-width: 1.5; stroke-linecap: round; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
```

> 注：SVG 渐变 `url(#g1)` 需要在组件模板里加一个 `<defs><linearGradient id="g1">…</linearGradient></defs>`，与现有 `.a24-page-header .icon` 同色。

### 6.4 新增组件：`src/components/AiWarmInline.vue`（上传页内联提示）

挂在 `ContractUpload.vue` 的 `el-card` 内、`<el-form>` 之前。

```vue
<template>
  <div v-if="state !== 'hidden'" class="ai-warm-inline" :class="state">
    <span class="ic">{{ state === 'ready' ? '✓' : state === 'failed' ? '!' : '⏳' }}</span>
    <div>
      <b v-if="state === 'warming'">AI 教练正在热身……</b>
      <b v-else-if="state === 'ready'">AI 教练已准备好。</b>
      <b v-else>AI 教练今天状态不太好。</b>
      <template v-if="state === 'warming'">
        <br/>首次处理可能会稍慢，文件仍可正常上传，准备完成后自动加速。
      </template>
      <template v-else-if="state === 'ready'">可以放心上传合同。</template>
      <template v-else>
        <br/>智能审核服务暂时未准备完成，文件仍可上传，但首次审核可能失败。
        <a class="retry" @click="retry">重新检查</a>
      </template>
    </div>
  </div>
</template>

<script setup>
import { useAiWarmup } from '../composables/useAiWarmup.js'
const { warmupState: state, retry } = useAiWarmup()
</script>

<style scoped>
.ai-warm-inline {
  display: flex; gap: 10px; padding: 10px 12px;
  border-radius: 8px; font-size: 12.5px; line-height: 1.55; margin-bottom: 14px;
}
.ai-warm-inline.warming { background: #EEF3FE; border: 1px solid #D6E2FB; color: #3A4E86; }
.ai-warm-inline.ready    { background: #F0FAF4; border: 1px solid #C8E8D5; color: #2F6B48; }
.ai-warm-inline.failed   { background: #FDF6EC; border: 1px solid #F2DDB4; color: #8A6520; }
.ic { font-size: 16px; line-height: 1.3; }
.retry { color: #A86A14; text-decoration: underline; margin-left: 6px; cursor: pointer; }
</style>
```

### 6.5 接入点（最小改动）

(1) `App.vue`：在导航右侧 `<div class="nav-spacer" />` 之后插入 `<AiWarmPill />`；
(2) `HomeView.vue`：在 `</section>`（hero 结束）之后、`<section class="metrics">` 之前插入 `<AiWarmBanner />`；
(3) `ContractUpload.vue`：在 `<el-card>` 内 `<el-form>` 之前插入 `<AiWarmInline />`；
(4) **不修改**：路由、权限、上传接口、审核触发、进度条、错误处理、`request.js` 拦截器。

---

## 7. 验收清单

(1) 后端刚启动（预热进行中），打开首页：右上角有蓝色呼吸点，hero 下方出现「AI 教练正在热身……」Banner，三点在跳，进度条在滑；
(2) 3–10 秒后（后端预热完成）：Banner 变绿，显示「AI 教练已准备好」，2.5 秒后自动收起，导航点保持绿色；
(3) 手动把 `WARMUP_STRICT=1` 配个错误依赖模拟失败：Banner 变浅琥珀，文案不出现任何技术词，点「重新检查」会重新请求；
(4) 上传页：选了文件后，无论热身中还是就绪态，「开始上传」按钮始终可点；点上传后走的还是原来的 `uploadContract → triggerAudit`，没有任何新分支；
(5) 登录页 `/login`：右上角不显示状态点；
(6) 网络断开时：不显示红色错误 Banner（避免在离线场景误报），导航点回到灰色；
(7) 控制台 Network 面板：3 秒一次 `/api/health` GET，就绪后停止，没有内存泄漏。

---

## 8. 明确不做的事（Non-Goals）

(1) 不改 `services/warmup.py`、不改 `/api/health` 响应结构；
(2) 不改 RAG 分类、LLM 调用、OCR、风险判定、F1/Gold/测试集；
(3) 不改 Docker、交付包、启动脚本；
(4) 不做全屏 Loading、不做骨架屏、不做进度百分比；
(5) 不更换 `hero.svg`，不重画首页主视觉；
(6) 不新增第三方动画库（Lottie / GSAP），全部用 CSS keyframes；
(7) 不持久化预热状态到 localStorage（避免跨天误判）。
