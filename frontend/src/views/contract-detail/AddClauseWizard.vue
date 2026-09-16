<template>
  <!-- 弹窗模式（从风险详情 / 条款比对 / 其它 Tab 发起）
       注意：新增条款会话的右栏工作区**不经过本组件** —— 它直接用 InlinePositionPicker
       渲染同一份 addWizard 状态，中栏因此保持为普通聊天工作区。 -->
  <el-dialog
    v-if="mode === 'dialog'"
    v-model="dialogVisible"
    title="新增条款"
    width="820px"
    :close-on-click-modal="false"
    append-to-body
    @closed="onClosed"
  >
    <el-steps :active="w.step - 1" simple class="ac-steps">
      <el-step title="需求" />
      <el-step title="AI 建议" />
      <el-step title="插入位置（必须确认）" />
      <el-step title="保存" />
    </el-steps>

    <AddClauseSteps
      :w="w" :ws="ws" :headings="headings" :suggest="suggest"
      :can-use-suggest="canUseSuggest" :before-prev="beforePrev" :locate-candidates="locateCandidates"
      :after-num="afterNum" :before-num="beforeNum"
      @update:after-num="(v) => (afterNum = v)" @update:before-num="(v) => (beforeNum = v)"
      @apply-after="applyAfter" @apply-before="applyBefore" @mode-change="onModeChange"
    />

    <template #footer>
      <el-button @click="w.visible = false">放弃</el-button>
      <el-button v-if="w.step === 1" type="primary" :disabled="!w.requirement.trim()" :loading="w.loading" @click="go2">
        生成 AI 建议
      </el-button>
      <template v-else-if="w.step === 2">
        <el-button @click="w.step = 1">上一步</el-button>
        <el-button type="primary" @click="go3">下一步：选择插入位置</el-button>
      </template>
      <template v-else-if="w.step === 3">
        <el-button @click="w.step = 2">上一步</el-button>
        <el-button type="primary" :disabled="!w.confirmedPosition" @click="go4">确认位置</el-button>
      </template>
      <template v-else>
        <el-button @click="w.step = 3">上一步</el-button>
        <el-button type="primary" :loading="w.submitting" :disabled="!w.confirmedPosition" @click="ws.submitAddClause()">
          生成并保存
        </el-button>
      </template>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import AddClauseSteps from './AddClauseSteps.vue'

/**
 * 新增条款向导（**仅弹窗**）。
 *
 * 用途：从风险详情 / 条款比对 / 其它 Tab 发起新增条款时的独立流程（需求 → AI 建议 → 插入位置 → 保存）。
 *
 * 「修改合同」Tab 的新增条款会话**不再内嵌本组件**：中栏保持为普通聊天工作区，
 * 右栏用 InlinePositionPicker 渲染同一份 addWizard 状态。
 * 两个入口共用同一套位置规则（addPositionOk / chooseAddPosition / beforeAnchorOf），
 * 不存在第二套新增条款系统。
 */
const props = defineProps({
  ws: { type: Object, required: true },
})
const w = computed(() => props.ws.addWizard)

const dialogVisible = computed({
  get: () => !!w.value.visible,
  set: (v) => { w.value.visible = v },
})

/**
 * 位置选择用的条款列表：以当前合同正文解析结果为准。
 * 额外缓存一份，避免会话切换导致列表瞬时为空时需要重新解析。
 */
const lastHeadings = ref([])
watch(
  () => props.ws.headings,
  (hs) => { if (hs && hs.length) lastHeadings.value = hs },
  { immediate: true },
)
const headings = computed(() => (props.ws.headings?.length ? props.ws.headings : lastHeadings.value))

const afterNum = ref(null)
const beforeNum = ref(null)

const suggest = computed(() => w.value.suggestedPosition || null)
const canUseSuggest = computed(() => {
  const sp = suggest.value
  return !!sp && (!!sp.append || !!sp.anchor)
})
const beforePrev = computed(() => (beforeNum.value == null ? null : props.ws.beforeAnchorOf(beforeNum.value)))
const locateCandidates = computed(() => {
  const r = w.value.locateResult
  if (!r) return []
  return r.candidates?.length ? r.candidates : r.found ? [r] : []
})

async function go2() {
  if (!w.value.requirement.trim()) {
    ElMessage.warning('请填写新增条款的要求')
    return
  }
  w.value.loading = true
  try {
    await props.ws.reloadAddSuggestion()
    w.value.step = 2
  } finally {
    w.value.loading = false
  }
}

function go3() {
  w.value.step = 3
  // 位置必须由用户确认：
  //  - 有推荐位置时只把它设为当前选项，用户仍需点「确认位置」；
  //  - 没有推荐位置时绝不自作主张（尤其不许默认 append），强制用户显式选一种。
  w.value.confirmedPosition = null
  w.value.posHint = ''
  afterNum.value = null
  beforeNum.value = null
  if (canUseSuggest.value) {
    w.value.posMode = 'suggest'
    props.ws.chooseAddPosition('suggest')
  } else {
    w.value.posMode = ''
  }
}

function go4() {
  if (!w.value.confirmedPosition) {
    ElMessage.warning('请先确认插入位置')
    return
  }
  w.value.step = 4
}

function onModeChange(mode) {
  if (mode === 'suggest') props.ws.chooseAddPosition('suggest')
  else if (mode === 'append') props.ws.chooseAddPosition('append')
  else if (mode === 'after' && afterNum.value != null) applyAfter()
  else if (mode === 'before' && beforeNum.value != null) applyBefore()
  else {
    // 切换方式后清掉上一次的确认，避免沿用旧位置
    w.value.confirmedPosition = null
    w.value.posHint = ''
  }
}

function applyAfter() {
  if (afterNum.value == null) return
  props.ws.chooseAddPosition('after', { anchor: afterNum.value })
}

function applyBefore() {
  if (beforeNum.value == null) return
  const prev = props.ws.beforeAnchorOf(beforeNum.value)
  props.ws.chooseAddPosition('before', { num: beforeNum.value, prevAnchor: prev })
}

function onClosed() {
  props.ws.resetAddWizard()
}
</script>

<style scoped>
.ac-steps { margin-bottom: 16px; }
</style>
