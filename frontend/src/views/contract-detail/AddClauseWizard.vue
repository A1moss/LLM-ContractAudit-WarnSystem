<template>
  <el-dialog
    v-model="w.visible"
    title="新增条款"
    width="820px"
    :close-on-click-modal="false"
    append-to-body
    @closed="onClosed"
  >
    <!-- 步骤条 -->
    <el-steps :active="w.step - 1" simple class="ac-steps">
      <el-step title="需求" />
      <el-step title="AI 建议" />
      <el-step title="插入位置（必须确认）" />
      <el-step title="保存" />
    </el-steps>

    <!-- Step 1 需求 -->
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

    <!-- Step 2 AI 建议 -->
    <template v-else-if="w.step === 2">
      <div v-if="w.loading" class="ac-loading"><el-skeleton :rows="4" animated /><div class="ac-tip">正在检索同类范本与法条…</div></div>
      <template v-else>
        <div class="ac-sec">
          <div class="ac-label">RAG 参考范本（{{ w.templates.length }}）</div>
          <div v-if="!w.templates.length" class="ac-muted">后端未返回同类范本（可能知识库未初始化）。</div>
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
          <div v-if="!w.legalBasis.length" class="ac-muted">后端未返回法律依据。</div>
          <div v-for="(l, i) in w.legalBasis" :key="i" class="ac-item">
            <div class="ac-item-head">{{ l.law }}{{ l.article }} <span class="ac-muted">{{ l.title }}</span></div>
            <div class="ac-item-body">{{ l.content }}</div>
          </div>
        </div>
        <div class="ac-sec">
          <div class="ac-label">AI 建议插入位置</div>
          <template v-if="w.suggestedPosition">
            <div class="ac-suggest">
              {{ w.suggestedPosition.hint || positionText(w.suggestedPosition) }}
              <el-tag size="small" effect="plain" type="warning">仅为建议，需你确认</el-tag>
            </div>
          </template>
          <div v-else class="ac-muted">
            后端未给出建议位置（合同无「第X条 / X、」标题结构）。下一步必须由你显式选择插入位置。
          </div>
        </div>
        <div class="ac-sec">
          <div class="ac-note">
            条款正文由 AI 在你确认位置后生成（<code>POST /revise</code> 的 add_clause 分支），
            本步骤只展示检索到的参考材料，尚未写入任何修改记录。
          </div>
        </div>
      </template>
    </template>

    <!-- Step 3 插入位置 -->
    <template v-else-if="w.step === 3">
      <div class="ac-sec">
        <div class="ac-label">选择插入位置（必选）</div>
        <div v-if="!w.posMode" class="ac-warn">
          请显式选择一种插入位置。系统不会替你决定；当 AI 推荐位置为空时也不会默认追加到末尾。
        </div>
        <el-radio-group v-model="w.posMode" class="ac-radio-col" @change="onModeChange">
          <el-radio v-if="w.suggestedPosition" value="suggest" :disabled="!canUseSuggest">
            使用 AI 推荐位置：{{ w.suggestedPosition?.hint || positionText(w.suggestedPosition) }}
          </el-radio>
          <el-radio value="after" :disabled="!headings.length">在某一编号条款之后</el-radio>
          <el-radio value="before" :disabled="!headings.length">在某一编号条款之前</el-radio>
          <el-radio value="append">追加到合同末尾</el-radio>
          <el-radio value="locate">用描述定位位置</el-radio>
        </el-radio-group>
        <div v-if="!w.suggestedPosition" class="ac-tip">
          后端未给出建议位置（本合同可能没有「第X条 / X、」标题结构，或建议为空），
          因此需要你自行选择：条款编号（若有标题结构）、追加到末尾，或用描述让后端定位。
        </div>
      </div>

      <!-- 之后 -->
      <div v-if="w.posMode === 'after'" class="ac-sec">
        <el-select v-model="afterNum" size="small" filterable placeholder="选择条款" style="width:260px" @change="applyAfter">
          <el-option v-for="h in headings" :key="h.num" :label="`第${h.cn}条 ${h.title || ''}`" :value="h.num" />
        </el-select>
      </div>

      <!-- 之前 -->
      <div v-if="w.posMode === 'before'" class="ac-sec">
        <el-select v-model="beforeNum" size="small" filterable placeholder="选择条款" style="width:260px" @change="applyBefore">
          <el-option v-for="h in headings" :key="h.num" :label="`第${h.cn}条 ${h.title || ''}`" :value="h.num" />
        </el-select>
        <div class="ac-tip">
          后端只支持「在某编号条款之后插入」；选择「第X条之前」时会被转换为「第 X-1 条之后」。
          <span v-if="beforeNum && beforePrev == null" class="ac-warn">该条款已是合同第一条，其后端无法表达「之前」，请改选其它位置。</span>
        </div>
      </div>

      <!-- 描述定位 -->
      <div v-if="w.posMode === 'locate'" class="ac-sec">
        <div class="ac-row">
          <el-input v-model="w.locateText" size="small" type="textarea" :rows="2" placeholder="例如：违约责任这一节中关于违约金的条款" />
          <el-button size="small" type="primary" :loading="w.locateLoading" @click="ws.runAddLocate()">只做定位</el-button>
        </div>
        <div class="ac-tip">该调用为只读定位接口（不调 LLM、不写库）；得到候选后需你点选确认。</div>
        <div v-if="w.locateResult" class="ac-locate-result">
          <div class="ac-item-head">
            <el-tag size="small" :type="w.locateResult.found ? 'success' : 'warning'">
              {{ w.locateResult.found ? '定位成功' : '未能唯一定位' }}
            </el-tag>
            <span class="ac-muted">{{ w.locateResult.reason }}</span>
          </div>
          <div v-for="(c, i) in locateCandidates" :key="i" class="ac-item">
            <div class="ac-item-head">
              <span>{{ c.clause_no ? `第${ws.cnNo(c.clause_no)}条` : '（无编号，不可作为锚点）' }} {{ c.clause_title || '' }}</span>
              <el-button
                size="small" text type="primary"
                :disabled="c.clause_no == null"
                @click="ws.chooseAddLocateCandidate(c)"
              >选为插入锚点</el-button>
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

    <!-- Step 4 保存 -->
    <template v-else>
      <div class="ac-sec">
        <div class="ac-kv"><span class="k">新增要求</span><span class="v">{{ w.requirement }}</span></div>
        <div class="ac-kv"><span class="k">插入位置</span><span class="v">{{ w.posHint || positionText(w.confirmedPosition) }}</span></div>
        <div class="ac-kv"><span class="k">目标会话</span><span class="v">
          {{ w.targetKey === '__overview__' ? '总体修改会话' : w.targetKey }}
        </span></div>
        <div class="ac-note">
          点击「生成并保存」后调用现有 <code>POST /revise</code>（operation=add_clause），
          生成一条 ClauseRevision；下载修订版 DOCX 时由后端按该位置插入并顺延后续编号。
        </div>
      </div>
    </template>

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
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { positionText } from '../../composables/useContractWorkspace.js'

const props = defineProps({ ws: { type: Object, required: true } })
const w = computed(() => props.ws.addWizard)
const headings = computed(() => props.ws.headings || [])

const afterNum = ref(null)
const beforeNum = ref(null)

const canUseSuggest = computed(() => {
  const sp = w.value.suggestedPosition
  if (!sp) return false
  return !!sp.append || !!sp.anchor
})

const beforePrev = computed(() => (beforeNum.value == null ? null : props.ws.beforeAnchorOf(beforeNum.value)))

const locateCandidates = computed(() => {
  const r = w.value.locateResult
  if (!r) return []
  const list = r.candidates?.length ? r.candidates : r.found ? [r] : []
  return list
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
  // 位置**必须由用户确认**：
  // - 有 AI 推荐位置时，仅把该选项设为当前选择（用户仍需点「确认位置」）；
  // - **没有推荐位置时绝不自作主张**（尤其不许默认 append），posMode 留空，强制用户显式选一种。
  w.value.confirmedPosition = null
  w.value.posHint = ''
  if (canUseSuggest.value) {
    w.value.posMode = 'suggest'
    props.ws.chooseAddPosition('suggest')
  } else {
    w.value.posMode = ''
    afterNum.value = null
    beforeNum.value = null
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
.ac-suggest { font-size: 13px; color: #B45309; display: flex; align-items: center; gap: 8px; }
.ac-radio-col { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; }
.ac-row { display: flex; gap: 8px; align-items: flex-start; }
.ac-kv { display: flex; gap: 8px; font-size: 12.5px; line-height: 1.7; margin-bottom: 4px; }
.ac-kv .k { flex: 0 0 70px; color: #8A93A6; }
.ac-kv .v { flex: 1; color: #303133; white-space: pre-wrap; }
.ac-loading { padding: 8px 0; }
code { background: #F1F5F9; padding: 0 3px; border-radius: 3px; font-size: 11.5px; }
</style>
