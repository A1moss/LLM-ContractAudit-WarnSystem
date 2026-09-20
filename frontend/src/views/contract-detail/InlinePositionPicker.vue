<template>
  <div class="ipp">
    <div v-if="!w.posMode" class="ipp-warn">
      请显式选择一种插入位置。系统不会替你决定；当推荐位置为空时也不会默认放到合同末尾。
    </div>

    <el-radio-group v-model="w.posMode" class="ipp-radios" @change="$emit('mode-change', $event)">
      <el-radio v-if="suggest" value="suggest" :disabled="!canUseSuggest">
        使用推荐位置：{{ suggest.hint || positionText(suggest) }}
      </el-radio>
      <el-radio value="after" :disabled="!headings.length">在某一编号条款之后</el-radio>
      <el-radio value="before" :disabled="!headings.length">在某一编号条款之前</el-radio>
      <el-radio value="append">合同末尾</el-radio>
      <el-radio value="locate">用描述定位位置</el-radio>
    </el-radio-group>

    <div v-if="!suggest" class="ipp-tip">
      系统没有给出推荐位置（这份合同可能没有编号结构，或没有可参考的范本），
      因此需要你自行选择：条款编号、合同末尾，或用描述让系统查找。
    </div>
    <div v-else class="ipp-tip">
      「{{ suggest.hint || positionText(suggest) }}」只是系统建议，需要你点下面的「确认位置」才算选定。
    </div>

    <!-- 在某一编号条款之后。R-1：同一编号可能重复出现，因此选项以「出现序号」为值
         （而不是编号），并在标签里标出「第N处」，让用户明确选到的是哪一处。 -->
    <div v-if="w.posMode === 'after'" class="ipp-sec">
      <el-select
        :model-value="afterNum" size="small" filterable placeholder="选择条款" style="width:100%"
        @update:model-value="$emit('update:after-num', $event)" @change="$emit('apply-after')"
      >
        <el-option
          v-for="(h, i) in headings" :key="`after-${i}`"
          :label="headLabel(h, i)" :value="i"
        />
      </el-select>
      <div v-if="hasDuplicateNums" class="ipp-tip">
        该合同存在<b>同编号重复</b>的条款；下拉已分别列出每一处，请选你要插入的那一处。
      </div>
    </div>

    <!-- 在某一编号条款之前（换算成「第 X-1 条之后」） -->
    <div v-if="w.posMode === 'before'" class="ipp-sec">
      <el-select
        :model-value="beforeNum" size="small" filterable placeholder="选择条款" style="width:100%"
        @update:model-value="$emit('update:before-num', $event)" @change="$emit('apply-before')"
      >
        <el-option
          v-for="(h, i) in headings" :key="`before-${i}`"
          :label="headLabel(h, i)" :value="i"
        />
      </el-select>
      <div class="ipp-tip">
        新增条款只会插在某个编号条款之后；选择「第X条之前」会换算成「第 X-1 条之后」。
        <span v-if="beforeNum != null && beforePrevIdx == null" class="ipp-warn-inline">
          该条款已是合同第一条，前面无法再插入，请改选其它位置。
        </span>
      </div>
    </div>

    <!-- 用描述定位位置（只读定位，候选必须点选） -->
    <div v-if="w.posMode === 'locate'" class="ipp-sec">
      <el-input
        v-model="w.locateText" size="small" type="textarea" :rows="2"
        placeholder="例如：违约责任这一节中关于违约金的条款"
      />
      <div class="ipp-row">
        <el-button size="small" type="primary" plain :loading="w.locateLoading" @click="ws.runAddLocate()">
          只做定位
        </el-button>
      </div>
      <div class="ipp-tip">只查找位置，不会修改合同；得到候选后需要你点选确认。</div>

      <div v-if="w.locateResult" class="ipp-result">
        <div class="ipp-result-head">
          <el-tag size="small" :type="w.locateResult.found ? 'success' : 'warning'">
            {{ w.locateResult.found ? '定位成功' : '未能唯一定位' }}
          </el-tag>
          <span class="ipp-muted">{{ w.locateResult.reason }}</span>
        </div>
        <div v-for="(c, i) in locateCandidates" :key="i" class="ipp-cand">
          <div class="ipp-cand-head">
            <span>{{ c.clause_no ? `第${ws.cnNo(c.clause_no)}条` : '（无编号，不能作为插入位置）' }} {{ c.clause_title || '' }}</span>
            <el-button
              size="small" text type="primary"
              :disabled="c.clause_no == null"
              @click="ws.chooseAddLocateCandidate(c)"
            >选为插入位置</el-button>
          </div>
          <div class="ipp-cand-body">{{ c.original_text }}</div>
        </div>
      </div>
    </div>

    <!-- 当前选择 + 确认。三态必须区分清楚：
         ① 已选定   = w.confirmedPosition（用户点过「确认位置」）
         ② 已选待确认 = w.selectedPosition（用户选了候选，但还没确认）
         ③ 尚未选定 = 两者都为空
         「确认位置」按钮的动作是**提交选中项为最终位置**（调用状态层 confirmAddPosition），
         不是父组件里的空动作 —— 推荐位置永远是候选，不得自动成为最终位置。 -->
    <div class="ipp-cur">
      <div v-if="w.confirmedPosition" class="ipp-ok">
        已选定：{{ positionText(w.confirmedPosition) }}
      </div>
      <div v-else-if="w.selectedPosition" class="ipp-pending">
        已选择（待确认）：{{ positionText(w.selectedPosition) }} —— 请点「确认位置」完成选定。
      </div>
      <div v-else class="ipp-warn">尚未选定插入位置。</div>
      <div class="ipp-row ipp-row-end">
        <el-button
          size="small" type="primary" plain
          :disabled="!w.selectedPosition"
          @click="ws.confirmAddPosition()"
        >
          确认位置
        </el-button>
        <el-button v-if="w.confirmedPosition || w.selectedPosition" size="small" text @click="$emit('clear-position')">
          重新选
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { positionText } from '../../composables/useContractWorkspace.js'

/**
 * InlinePositionPicker — 新增条款「插入位置」选择器（**右栏工作区专用**）。
 *
 * 新增条款「插入位置」的**唯一**选择器（右栏工作区专用；原弹窗版 AddClauseSteps 已删除，
 * 因为它的根节点 v-if="mode === 'dialog'" 引用了未定义的 mode，永远不会渲染）。
 *
 * 状态与规则全部来自 useContractWorkspace 的同一份 addWizard
 * （addPositionOk / beforeAnchorOf / chooseAddPosition），不存在第二套位置规则。
 *
 * 硬约束（V2.2 与产品红线，逐条来自既有实现）：
 *   - 推荐位置只作为**候选**（`selectedPosition`），`confirmedPosition` 只有用户点「确认位置」
 *     才成立（调用状态层 `confirmAddPosition()`）；
 *   - **没有推荐位置时绝不自作主张**（尤其不许默认 append）；
 *   - 「第X条之前」换算成「第X-1条之后」，第一条时明确提示无法表达；
 *   - 描述定位只调只读 locate 接口，候选必须用户点选。
 *
 * 本组件不自己改状态：位置计算与落定全部交给 useContractWorkspace 的
 * chooseAddPosition / confirmAddPosition / chooseAddLocateCandidate / runAddLocate
 * （避免出现第二套位置规则）。
 */
const props = defineProps({
  ws: { type: Object, required: true },
  w: { type: Object, required: true },
  headings: { type: Array, default: () => [] },
  suggest: { type: Object, default: null },
  canUseSuggest: { type: Boolean, default: false },
  beforePrev: { type: [Number, null], default: null },
  locateCandidates: { type: Array, default: () => [] },
  afterNum: { type: [Number, null], default: null },
  beforeNum: { type: [Number, null], default: null },
})

defineEmits([
  'update:after-num', 'update:before-num',
  'apply-after', 'apply-before', 'mode-change',
  'confirm-position', 'clear-position',
])

/**
 * R-1：同一编号在合同里可能重复出现。选项值改为**出现序号**（数组下标），
 * 标签里标出「第N处」，用户选中的是「第几个选项」而不是「哪个编号」，
 * 父组件据此把该处的 target_text / paragraph_index 一并保存进插入位置。
 */
const numOccurrence = computed(() => {
  const counts = new Map()
  for (const h of props.headings || []) counts.set(h.num, (counts.get(h.num) || 0) + 1)
  return counts
})
const hasDuplicateNums = computed(() => {
  for (const n of numOccurrence.value.values()) if (n > 1) return true
  return false
})
/** 选项标签：重复编号时补上「（第N处）」 */
function headLabel(h, i) {
  const base = `第${h.cn}条 ${h.title || ''}`
  if ((numOccurrence.value.get(h.num) || 0) <= 1) return base
  const idx = (props.headings || []).filter((x) => x.num === h.num).indexOf(h) + 1
  return `${base}（第${idx}处）`
}
/** 「第 X 条之前」换算：找到所选序号之前的**那一条**（同编号多出现时按序号取紧邻上一条） */
const beforePrevIdx = computed(() => {
  const i = props.beforeNum
  return typeof i === 'number' && i > 0 ? i - 1 : null
})
</script>

<style scoped>
.ipp { display: flex; flex-direction: column; gap: 6px; }
.ipp-radios { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; }
.ipp-sec { margin-top: 2px; }
.ipp-tip { font-size: 11.5px; color: #8A93A6; line-height: 1.7; }
.ipp-warn { font-size: 11.5px; color: #B45309; line-height: 1.7; background: #FFFBEB; border-radius: 6px; padding: 6px 8px; }
.ipp-warn-inline { color: #B45309; }
.ipp-row { display: flex; gap: 6px; margin-top: 6px; }
.ipp-row-end { justify-content: flex-end; }
.ipp-cur { border-top: 1px dashed var(--a24-border); padding-top: 8px; margin-top: 2px; }
.ipp-ok { font-size: 12px; color: #166534; background: #F0FDF4; border: 1px solid #BBF7D0; border-radius: 6px; padding: 6px 8px; }
.ipp-pending { font-size: 12px; color: #1D4ED8; background: #EFF6FF; border: 1px solid #BFDBFE; border-radius: 6px; padding: 6px 8px; }
.ipp-result { margin-top: 6px; }
.ipp-result-head { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-bottom: 6px; }
.ipp-cand { border: 1px solid var(--a24-border); border-radius: 6px; padding: 6px 8px; margin-bottom: 6px; }
.ipp-cand-head { display: flex; align-items: center; gap: 8px; font-size: 12px; color: #303133; margin-bottom: 4px; flex-wrap: wrap; }
.ipp-cand-body { font-size: 11.5px; color: #4B5563; line-height: 1.7; white-space: pre-wrap; }
.ipp-muted { font-size: 11.5px; color: #9AA4B8; }
</style>
