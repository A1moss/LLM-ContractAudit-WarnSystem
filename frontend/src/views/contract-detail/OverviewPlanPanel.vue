<template>
  <div class="opp">
    <!-- ① 当前修改点汇总 -->
    <section class="opp-sec">
      <div class="opp-head">
        <span class="opp-t">当前修改点汇总</span>
        <span class="opp-n">{{ points.length }} 个修改点</span>
      </div>

      <div v-if="loading" class="opp-muted">正在汇总…</div>
      <div v-else-if="error" class="opp-err">{{ error }}</div>
      <div v-else-if="!points.length" class="opp-muted">
        这份合同暂时没有需要修改的风险点。
      </div>
      <div v-else class="opp-points">
        <div
          v-for="p in points"
          :key="p.key"
          class="opp-point"
          :class="{ 'is-active': p.key === ws.activeKey }"
          @click="ws.selectSession(p.key)"
        >
          <span class="opp-dot" :class="'dot-' + p.state.dot" />
          <span class="opp-point-title">{{ p.title }}</span>
          <span class="opp-point-state" :class="'st-' + p.state.tone">{{ p.state.label }}</span>
        </div>
      </div>
    </section>

    <!-- ② AI 综合修改方案（用户提出整体要求后出现） -->
    <section class="opp-sec">
      <div class="opp-head">
        <span class="opp-t">本轮综合修改方案</span>
        <span v-if="proposal" class="opp-n">
          共 {{ proposal.items.length }} 项 · 已纳入 {{ includedCount }} 项
        </span>
      </div>

      <div v-if="ws.planLoading" class="opp-muted">
        <div class="opp-loading">正在结合整份合同与已有修改情况生成方案…</div>
        <el-skeleton :rows="4" animated />
      </div>
      <div v-else-if="ws.planError" class="opp-err">{{ ws.planError }}</div>
      <div v-else-if="!proposal" class="opp-muted">
        输入你对整份合同的修改要求，AI 会结合当前修改情况给出综合方案。
      </div>

      <template v-else>
        <div v-if="proposal.summary" class="opp-summary">{{ proposal.summary }}</div>

        <div class="opp-items">
          <div v-for="(it, idx) in proposal.items" :key="it.id" class="opp-item" :class="{ 'is-open': openId === it.id }">
            <div class="opp-item-main">
              <span class="opp-dot" :class="'dot-' + stateOf(it).dot" />
              <span class="opp-item-no">{{ idx + 1 }}</span>
              <span class="opp-item-title">{{ ws.proposalItemTitle(it) }}</span>
              <el-tag size="small" effect="plain" :type="it.operation === 'add_clause' ? 'success' : 'primary'">
                {{ ws.proposalItemKind(it) }}
              </el-tag>
              <el-tag v-if="included(it)" size="small" type="warning" effect="light">已纳入方案</el-tag>
              <el-tag v-else-if="!it.resolved" size="small" type="info" effect="plain">待确认位置</el-tag>
              <span class="opp-spacer" />
              <el-button size="small" text @click="toggle(it.id)">{{ openId === it.id ? '收起' : '查看详情' }}</el-button>
            </div>

            <div v-if="openId === it.id" class="opp-item-body">
              <div v-if="!it.resolved" class="opp-need-locate">
                <div class="opp-need-t">⚠ {{ ws.proposalBlockingText(it) }}</div>
                <div class="opp-need-d">
                  系统不能替你决定改哪里。请先用下方任一方式确认位置，再回到专项会话完成最终确认。
                </div>
                <el-button size="small" type="primary" plain @click="ws.gotoProposalItem(it)">
                  去指定位置
                </el-button>
              </div>

              <div class="opp-kv"><span class="k">原文</span><span class="v mono">{{ it.original_quote || (it.operation === 'add_clause' ? '（新增条款，无原文）' : '—') }}</span></div>
              <div class="opp-kv"><span class="k">{{ it.operation === 'add_clause' ? '新增条款正文' : '建议改为' }}</span><span class="v mono">{{ it.revised_clause || '—' }}</span></div>
              <div v-if="it.position" class="opp-kv"><span class="k">插入位置</span><span class="v">{{ ws.positionText(it.position) }}</span></div>
              <div v-if="it.suggested_position && !it.position" class="opp-kv">
                <span class="k">建议位置</span>
                <span class="v warn">系统建议：{{ ws.positionText(it.suggested_position) }}（仅为建议，需你确认）</span>
              </div>
              <div v-if="it.reason" class="opp-kv"><span class="k">理由</span><span class="v">{{ it.reason }}</span></div>
              <div v-if="it.legal_basis?.length" class="opp-kv"><span class="k">法律依据</span><span class="v">{{ it.legal_basis.join('；') }}</span></div>

              <div class="opp-item-actions">
                <el-button
                  v-if="!included(it)"
                  size="small" type="warning"
                  :disabled="!ws.canIncludeProposalItem(it)"
                  @click="ws.toggleIncludeItem(it)"
                >纳入方案</el-button>
                <el-button
                  v-else
                  size="small" text type="info"
                  @click="ws.toggleIncludeItem(it)"
                >取消纳入</el-button>
                <el-button size="small" text @click="skip(it)">跳过</el-button>
                <el-button size="small" type="primary" plain @click="ws.gotoProposalItem(it)">
                  去专项会话处理
                </el-button>
                <el-button size="small" text @click="useAsInstruction(it)">在中栏继续调整</el-button>
              </div>
            </div>
          </div>
        </div>

        <div class="opp-foot">
          <div class="opp-foot-line">
            ● {{ includedCount }} 项已纳入方案（待专项处理）<span v-if="skippedCount">　○ {{ skippedCount }} 项本轮跳过</span>
          </div>
          <div class="opp-foot-note">
            「已纳入方案」只表示你选中了这条综合方案、准备进入专项处理，<b>合同还没有被改动</b>。
            真正写入修改后的合同，需要在专项会话里完成位置确认与最终确认。
          </div>
          <div class="opp-foot-note">
            跳过只表示本轮暂不采用，不会永久关闭或删除这条风险；下次重新生成方案时它仍可能被再次提出。
          </div>
        </div>
      </template>
    </section>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

const props = defineProps({ ws: { type: Object, required: true } })

const openId = ref(null)
// 「跳过」= 仅本轮不纳入（纯前端会话态，不做任何持久化、不删风险）
const skipped = ref(new Set())

// 换了一份方案 → 本轮的"跳过"选择不再适用（它是前端会话态，不跨方案沿用）
watch(
  () => props.ws.activeProposalId,
  () => {
    skipped.value = new Set()
    openId.value = null
  },
)

const proposal = computed(() => props.ws.activeProposal)
const points = computed(() => props.ws.modificationPoints || [])
const includedCount = computed(() => (proposal.value?.items || []).filter((i) => included(i)).length)
const skippedCount = computed(() => (proposal.value?.items || []).filter((i) => skipped.value.has(i.id)).length)

function included(it) {
  return props.ws.includedItemIds?.has?.(it.id) === true
}

/** 方案项在总控台里的展示状态（与修改点状态同一套语义） */
function stateOf(it) {
  if (props.ws.sessionIncluded(it.target_session_key) && props.ws.isSessionExportable(it.target_session_key)) {
    return { dot: 'green', tone: 'success', label: '已确认修改' }
  }
  if (included(it)) return { dot: 'yellow', tone: 'warning', label: '已纳入方案' }
  if (skipped.value.has(it.id)) return { dot: 'grey', tone: 'info', label: '本轮跳过' }
  return { dot: 'grey', tone: 'info', label: '待处理' }
}

function toggle(id) {
  openId.value = openId.value === id ? null : id
}

function skip(it) {
  const next = new Set(skipped.value)
  if (next.has(it.id)) next.delete(it.id)
  else {
    next.add(it.id)
    // 跳过同时取消纳入（两者互斥，避免同一项既"纳入"又"跳过"）
    if (included(it)) props.ws.toggleIncludeItem(it)
  }
  skipped.value = next
}

/** 把该方案项的修改要求带进中栏输入框，用户可继续多轮调整（不直接提交） */
function useAsInstruction(it) {
  props.ws.gotoProposalItem(it)
  const key = props.ws.activeKey
  const ui = props.ws.uiFor(key)
  const req = it.reason || it.revised_clause || ''
  ui.input = ui.input ? `${ui.input}；${req}` : req
  ElMessage.info('已把该修改项的要求带到中栏，补充你的具体意见后发送')
}
</script>

<style scoped>
.opp { display: flex; flex-direction: column; gap: 12px; }
.opp-sec { border: 1px solid var(--a24-border); border-radius: 10px; padding: 10px 12px; background: #fff; }
.opp-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.opp-t { font-size: 12.5px; font-weight: 600; color: #6B7280; }
.opp-n { font-size: 11.5px; color: #8A93A6; }
.opp-muted { font-size: 12px; color: #9AA4B8; line-height: 1.75; }
.opp-err { font-size: 12px; color: #B91C1C; background: #FEF2F2; border-radius: 6px; padding: 6px 8px; line-height: 1.7; }
.opp-points { max-height: 220px; overflow-y: auto; }
.opp-point {
  display: flex; align-items: center; gap: 6px; padding: 5px 6px; border-radius: 6px;
  cursor: pointer; font-size: 12px;
}
.opp-point:hover { background: #F8FAFD; }
.opp-point.is-active { background: #F3F6FD; }
.opp-point-title { flex: 1; min-width: 0; color: #303133; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.opp-point-state { font-size: 11px; flex-shrink: 0; }
.opp-point-state.st-info { color: #9AA4B8; }
.opp-point-state.st-warning { color: #B45309; }
.opp-point-state.st-primary { color: #1D4ED8; }
.opp-point-state.st-success { color: #166534; }

.opp-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.dot-grey { background: #C0C4CC; }
.dot-yellow { background: #D97706; }
.dot-blue { background: #1D4ED8; }
.dot-green { background: #16A34A; }

.opp-loading { margin-bottom: 6px; }
.opp-summary { font-size: 12.5px; color: #4B5563; line-height: 1.8; background: #F8FAFD; border-radius: 6px; padding: 8px 10px; }
.opp-items { margin-top: 8px; }
.opp-item { border: 1px solid var(--a24-border); border-radius: 8px; margin-bottom: 6px; }
.opp-item.is-open { border-color: #C7D2E5; }
.opp-item-main { display: flex; align-items: center; gap: 6px; padding: 7px 8px; flex-wrap: wrap; }
.opp-item-no { font-size: 11.5px; color: #8A93A6; }
.opp-item-title { font-size: 12.5px; font-weight: 600; color: #131313; }
.opp-spacer { flex: 1; }
.opp-item-body { border-top: 1px dashed var(--a24-border); padding: 8px; }
.opp-kv { display: flex; gap: 8px; font-size: 12px; line-height: 1.75; margin-bottom: 4px; }
.opp-kv .k { flex: 0 0 68px; color: #8A93A6; }
.opp-kv .v { flex: 1; color: #303133; word-break: break-word; white-space: pre-wrap; }
.opp-kv .v.mono { font-family: ui-monospace, Consolas, monospace; font-size: 11.5px; }
.opp-kv .v.warn { color: #B45309; }
.opp-item-actions { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 6px; }
.opp-need-locate { background: #FFFBEB; border: 1px solid #FDE68A; border-radius: 6px; padding: 8px; margin-bottom: 8px; }
.opp-need-t { font-size: 12px; color: #B45309; line-height: 1.7; margin-bottom: 4px; }
.opp-need-d { font-size: 11.5px; color: #6B7280; line-height: 1.7; margin-bottom: 6px; }
.opp-foot { margin-top: 8px; border-top: 1px dashed var(--a24-border); padding-top: 8px; }
.opp-foot-line { font-size: 12px; color: #303133; margin-bottom: 4px; }
.opp-foot-note { font-size: 11.5px; color: #9AA4B8; line-height: 1.7; }
</style>
