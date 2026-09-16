<template>
  <!-- 步骤 1：需求 -->
  <template v-if="w.step === 1">
    <div class="ac-sec">
      <div class="ac-label">条款来源</div>
      <el-tag v-if="w.riskTypeLabel" size="small" effect="plain">{{ w.riskTypeLabel }}</el-tag>
      <el-tag v-else-if="w.riskType" size="small" effect="plain">{{ w.riskType }}</el-tag>
      <span v-else class="ac-muted">总体会话发起（未绑定具体缺失条款）</span>
      <div class="ac-tip">
        新增条款会插入到合同的指定位置，<b>插入位置必须由你确认</b>，系统不会替你决定，
        也不会在推荐位置为空时默认追加到末尾。
      </div>
    </div>
    <div class="ac-sec">
      <div class="ac-label">新增要求</div>
      <el-input
        v-model="w.requirement"
        type="textarea"
        :rows="4"
        placeholder="例如：新增不可抗力条款，明确不可抗力的定义、通知义务与免责安排"
      />
    </div>
  </template>

  <!-- 步骤 2：AI 建议 -->
  <template v-else-if="w.step === 2">
    <div v-if="w.loading" class="ac-loading">
      <el-skeleton :rows="4" animated />
      <div class="ac-tip">正在检索同类范本与法条…</div>
    </div>
    <template v-else>
      <div class="ac-sec">
        <div class="ac-label">参考范本（{{ w.templates.length }}）</div>
        <div v-if="!w.templates.length" class="ac-muted">没有找到同类范本。</div>
        <div v-for="(t, i) in w.templates" :key="i" class="ac-item">
          <div class="ac-item-head">
            <el-tag size="small" effect="plain">{{ t.type || '范本' }}</el-tag>
            <span v-if="t.score != null" class="ac-muted">相似度 {{ t.score }}</span>
          </div>
          <div class="ac-item-body">{{ t.text }}</div>
        </div>
      </div>
      <div class="ac-sec">
        <div class="ac-label">法律依据（{{ w.legalBasis.length }}）</div>
        <div v-if="!w.legalBasis.length" class="ac-muted">没有找到可引用的法律依据。</div>
        <div v-for="(l, i) in w.legalBasis" :key="i" class="ac-item">
          <div class="ac-item-head">{{ l.law }}{{ l.article }} <span class="ac-muted">{{ l.title }}</span></div>
          <div class="ac-item-body">{{ l.content }}</div>
        </div>
      </div>
      <div class="ac-sec">
        <div class="ac-label">AI 建议插入位置</div>
        <template v-if="suggest">
          <div class="ac-suggest">
            {{ suggest.hint || positionText(suggest) }}
            <el-tag size="small" effect="plain" type="warning">仅为建议，需你确认</el-tag>
          </div>
        </template>
        <div v-else class="ac-muted">
          系统没有给出建议位置。下一步必须由你显式选择插入位置。
        </div>
      </div>
      <div class="ac-sec">
        <div class="ac-note">
          条款正文在确认位置后生成；本步骤只展示检索到的参考材料，尚未写入任何修改记录。
        </div>
      </div>
    </template>
  </template>

  <!-- 步骤 3：插入位置（必须用户确认） -->
  <template v-else-if="w.step === 3">
    <div class="ac-sec">
      <div class="ac-label">选择插入位置（必选）</div>
      <div v-if="!w.posMode" class="ac-warn">
        请显式选择一种插入位置。系统不会替你决定；当 AI 推荐位置为空时也不会默认追加到末尾。
      </div>
      <el-radio-group v-model="w.posMode" class="ac-radio-col" @change="$emit('mode-change', $event)">
        <el-radio v-if="suggest" value="suggest" :disabled="!canUseSuggest">
          使用推荐位置：{{ suggest.hint || positionText(suggest) }}
        </el-radio>
        <el-radio value="after" :disabled="!headings.length">在某一编号条款之后</el-radio>
        <el-radio value="before" :disabled="!headings.length">在某一编号条款之前</el-radio>
        <el-radio value="append">合同末尾</el-radio>
        <el-radio value="locate">用描述定位位置</el-radio>
      </el-radio-group>
      <div v-if="!suggest" class="ac-tip">
        系统没有给出建议位置，因此需要你自行选择：条款编号（若合同有编号结构）、合同末尾，或用描述让系统查找。
      </div>
    </div>

    <div v-if="w.posMode === 'after'" class="ac-sec">
      <el-select
        :model-value="afterNum" size="small" filterable placeholder="选择条款" style="width:280px"
        @update:model-value="$emit('update:after-num', $event)" @change="$emit('apply-after')"
      >
        <el-option v-for="h in headings" :key="h.num" :label="`第${h.cn}条 ${h.title || ''}`" :value="h.num" />
      </el-select>
    </div>

    <div v-if="w.posMode === 'before'" class="ac-sec">
      <el-select
        :model-value="beforeNum" size="small" filterable placeholder="选择条款" style="width:280px"
        @update:model-value="$emit('update:before-num', $event)" @change="$emit('apply-before')"
      >
        <el-option v-for="h in headings" :key="h.num" :label="`第${h.cn}条 ${h.title || ''}`" :value="h.num" />
      </el-select>
      <div class="ac-tip">
        新增条款只会插在某个编号条款之后；选择「第X条之前」时会换算成「第 X-1 条之后」。
        <span v-if="beforeNum && beforePrev == null" class="ac-warn">
          该条款已是合同第一条，前面无法再插入，请改选其它位置。
        </span>
      </div>
    </div>

    <div v-if="w.posMode === 'locate'" class="ac-sec">
      <div class="ac-row">
        <el-input v-model="w.locateText" size="small" type="textarea" :rows="2" placeholder="例如：违约责任这一节中关于违约金的条款" />
        <el-button size="small" type="primary" :loading="w.locateLoading" @click="ws.runAddLocate()">
          只做定位
        </el-button>
      </div>
      <div class="ac-tip">只查找位置，不会修改合同；得到候选后需要你点选确认。</div>
      <div v-if="w.locateResult" class="ac-locate-result">
        <div class="ac-item-head">
          <el-tag size="small" :type="w.locateResult.found ? 'success' : 'warning'">
            {{ w.locateResult.found ? '定位成功' : '未能唯一定位' }}
          </el-tag>
          <span class="ac-muted">{{ w.locateResult.reason }}</span>
        </div>
        <div v-for="(c, i) in locateCandidates" :key="i" class="ac-item">
          <div class="ac-item-head">
            <span>{{ c.clause_no ? `第${ws.cnNo(c.clause_no)}条` : '（无编号，不能作为插入位置）' }} {{ c.clause_title || '' }}</span>
            <el-button
              size="small" text type="primary"
              :disabled="c.clause_no == null"
              @click="ws.chooseAddLocateCandidate(c)"
            >选为插入位置</el-button>
          </div>
          <div class="ac-item-body">{{ c.original_text }}</div>
        </div>
      </div>
    </div>

    <div class="ac-sec">
      <div class="ac-label">当前选择</div>
      <div v-if="w.confirmedPosition" class="ac-ok">{{ w.posHint || positionText(w.confirmedPosition) }}</div>
      <div v-else class="ac-warn">尚未确认插入位置（保存按钮已禁用）。</div>
    </div>
  </template>

  <!-- 步骤 4：保存 -->
  <template v-else>
    <div class="ac-sec">
      <div class="ac-kv"><span class="k">新增要求</span><span class="v">{{ w.requirement }}</span></div>
      <div class="ac-kv"><span class="k">插入位置</span><span class="v">{{ w.posHint || positionText(w.confirmedPosition) }}</span></div>
      <div class="ac-note">
        点击「生成并保存」后生成一条新增条款记录；下载修改后的合同时会按该位置插入，并顺延后续编号。
      </div>
    </div>
  </template>
</template>

<script setup>
import { positionText } from '../../composables/useContractWorkspace.js'

/**
 * AddClauseSteps — 新增条款向导的步骤内容（弹窗与右栏内嵌**共用同一份实现**）。
 *
 * 之所以拆出来：V2.2 要求「修改合同」右栏把插入位置选择直接摆在工作区里，
 * 而弹窗入口（风险/比对/其它 Tab）要继续可用；两套模板会漂移，因此内容只写一份，
 * 由 AddClauseWizard 决定用弹窗壳还是内嵌壳。
 *
 * 硬约束在本组件里体现为：
 *   - 无推荐位置时 posMode 保持空 → 保存按钮禁用（不会默认 append）；
 *   - 「第X条之前」换算成「第X-1条之后」，第一条时明确提示无法表达；
 *   - 描述定位只调只读 locate 接口，候选必须点选。
 */
defineProps({
  w: { type: Object, required: true },
  ws: { type: Object, required: true },
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
])
</script>

<style scoped>
.ac-sec { margin-bottom: 14px; }
.ac-label { font-size: 12.5px; font-weight: 600; color: #6B7280; margin-bottom: 6px; }
.ac-muted { font-size: 12px; color: #8A93A6; }
.ac-tip { font-size: 12px; color: #8A93A6; line-height: 1.7; margin-top: 6px; }
.ac-warn { font-size: 12px; color: #B45309; }
.ac-ok { font-size: 13px; color: #166534; background: #F0FDF4; border: 1px solid #BBF7D0; border-radius: 6px; padding: 8px 10px; }
.ac-note { font-size: 12px; color: #6B7280; background: #F8FAFD; border-radius: 6px; padding: 8px 10px; line-height: 1.7; }
.ac-item { border: 1px solid var(--a24-border); border-radius: 6px; padding: 8px 10px; margin-bottom: 8px; }
.ac-item-head { display: flex; align-items: center; gap: 8px; font-size: 12.5px; color: #303133; margin-bottom: 4px; flex-wrap: wrap; }
.ac-item-body { font-size: 12.5px; color: #4B5563; line-height: 1.75; white-space: pre-wrap; }
.ac-suggest { font-size: 13px; color: #B45309; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ac-radio-col { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; }
.ac-row { display: flex; gap: 8px; align-items: flex-start; }
.ac-kv { display: flex; gap: 8px; font-size: 12.5px; line-height: 1.7; margin-bottom: 4px; }
.ac-kv .k { flex: 0 0 70px; color: #8A93A6; }
.ac-kv .v { flex: 1; color: #303133; white-space: pre-wrap; }
.ac-loading { padding: 8px 0; }
</style>
