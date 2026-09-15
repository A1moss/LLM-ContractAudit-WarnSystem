<template>
  <div class="wb">
    <!-- 顶栏 -->
    <div class="wb-top">
      <div class="wb-top-l">
        <span class="wb-session-title">{{ s ? s.title : '未选择会话' }}</span>
        <el-tag v-if="s" size="small" :type="badge.type" effect="light">{{ badge.text }}</el-tag>
        <el-tag v-if="s" size="small" effect="plain" type="info">{{ opLabel(s) }}</el-tag>
        <span v-if="s?.rounds" class="wb-rounds">共 {{ s.rounds }} 轮</span>
      </div>
      <div class="wb-top-r">
        <el-button v-if="s && ws.sessionAction(s) === 'add_clause'" size="small" type="success" @click="openAddForSession">
          新增条款
        </el-button>
        <el-button v-if="s && ws.needsLocate(s)" size="small" type="warning" plain @click="focusLocate">
          指定修改位置
        </el-button>
        <el-button
          size="small" type="primary"
          :loading="ws.docx.loading"
          :disabled="ws.docxState.key === 'none' || ws.docxState.key === 'not_docx'"
          @click="ws.downloadDocx()"
        >下载修订版 DOCX</el-button>
      </div>
    </div>

    <div class="wb-body">
      <!-- ── 左栏：会话列表 ── -->
      <aside class="wb-left">
        <div class="wb-stats">
          <div class="wb-stat"><b>{{ ws.riskItems.length }}</b><span>当前待处理风险</span></div>
          <div class="wb-stat"><b>{{ ws.comparisonIssues.length }}</b><span>条款比对问题</span></div>
          <div class="wb-stat"><b>{{ ws.sessions.filter((x) => x.rounds > 0).length }}</b><span>已保存修改会话</span></div>
        </div>
        <div class="wb-stats-note" title="三个数字来自三个不同接口，不是同一个口径">风险来自 /audit-result · 比对来自 /clause-comparison · 会话来自 /revisions</div>

        <div v-if="ws.revisionsError" class="wb-left-err">{{ ws.revisionsError }}</div>

        <div class="wb-groups">
          <template v-for="g in SESSION_GROUPS" :key="g.key">
            <div class="wb-group-title">
              {{ g.label }}<span class="n">{{ (ws.sessionsByGroup[g.key] || []).length }}</span>
            </div>
            <div v-if="!(ws.sessionsByGroup[g.key] || []).length" class="wb-group-empty">
              <template v-if="g.key === 'add'">尚无新增条款会话：从 R09 风险或条款比对缺失项发起</template>
              <template v-else-if="g.key === 'history'">无无法归类的历史会话</template>
              <template v-else>—</template>
            </div>
            <div
              v-for="item in ws.sessionsByGroup[g.key] || []"
              :key="item.key"
              class="wb-item"
              :class="{ 'is-active': item.key === ws.activeKey }"
              @click="ws.selectSession(item.key)"
            >
              <div class="wb-item-top">
                <el-tag size="small" :type="groupTag(item.group)" effect="plain">{{ groupShort(item.group) }}</el-tag>
                <span class="wb-item-title">{{ item.title }}</span>
                <span v-if="item.rounds" class="wb-item-rounds">{{ item.rounds }} 轮</span>
              </div>
              <div class="wb-item-sub">{{ item.subtitle }}</div>
              <div class="wb-item-locate">
                <span class="dot" :class="'dot-' + ws.locateBadge(item).type" />
                <span class="txt">{{ ws.locateBadge(item).text }}</span>
              </div>
            </div>
          </template>
        </div>
      </aside>

      <!-- ── 中栏：修订工作区 ── -->
      <section class="wb-center">
        <template v-if="!s">
          <el-empty description="请选择左侧会话，或从「风险详情 / 条款比对」进入修改" />
        </template>
        <template v-else>
          <!-- 会话上下文头 -->
          <div class="wb-ctx">
            <div class="wb-ctx-row">
              <span class="k">正在修改</span>
              <span class="v">{{ s.key === '__overview__' ? '整个合同' : s.title }}</span>
              <el-tag v-if="s.clauseNo" size="small" effect="plain">第{{ ws.cnNo(s.clauseNo) }}条</el-tag>
              <span class="wb-spacer" />
              <el-button v-if="isOverview" size="small" text @click="ws.loadRevisions()">刷新历史</el-button>
            </div>
            <div v-if="!isOverview" class="wb-ctx-orig">
              <span class="k">修改对象原文</span>
              <span class="v" :class="{ muted: !s.clauseText }">{{ s.clauseText ? ws.clipText(s.clauseText, 220) : '（暂无可用原文，请在右侧确认修改位置）' }}</span>
            </div>
          </div>

          <!-- overview 明示 -->
          <el-alert
            v-if="isOverview" type="info" show-icon :closable="false" class="wb-overview-note"
            title="总体修改会话用于分析、讨论与生成整体修改方案"
          >
            <template #default>
              本会话的成果<b>不会</b>被 /revised-docx 写入合同文件（后端只消费 scope=clause 的替换修订
              与 overview+add_clause 的新增条款）。需要真正落到合同上的修改，请进入对应条款的会话，
              确认修改位置后提交。
              <span v-if="ws.overviewReplaceCount" class="wb-overview-count">
                当前已有 {{ ws.overviewReplaceCount }} 轮总体讨论记录不计入 DOCX。
              </span>
            </template>
          </el-alert>

          <!-- 消息流：一轮 = 一条 ClauseRevision，绝不合并 -->
          <div class="wb-chat">
            <div v-if="!s.rounds && !uiFor.pending" class="wb-empty">
              <template v-if="isOverview">输入整体修改要求，AI 会结合各专项会话记录给出方案。</template>
              <template v-else-if="ws.sessionAction(s) === 'add_clause'">
                该会话为「新增条款」：点右上角「新增条款」按向导流程起草并确认插入位置。
              </template>
              <template v-else-if="!s.clauseText">
                尚未确定修改对象：请在右侧「修改位置」中选择条款或原文，确认后再输入修改要求。
              </template>
              <template v-else>输入修改要求，开始修订该条款。</template>
            </div>

            <div v-for="(r, i) in s.revs" :key="r.id" class="wb-round">
              <div class="wb-round-head">
                <span class="wb-round-no">第 {{ i + 1 }} 轮</span>
                <el-tag size="small" :type="r.operation === 'add_clause' ? 'success' : 'primary'" effect="plain">
                  {{ r.operation === 'add_clause' ? '新增条款' : '替换修改' }}
                </el-tag>
                <span v-if="r.clause_no" class="wb-muted">第{{ ws.cnNo(r.clause_no) }}条</span>
                <span class="wb-muted">{{ fmtTime(r.created_at) }}</span>
              </div>

              <div class="wb-user-card">
                <div class="wb-card-label">用户要求</div>
                <div class="wb-user-text">{{ displayInstruction(r.instruction) }}</div>
              </div>

              <div class="wb-ai-card">
                <div class="wb-card-label">AI 修订结果</div>
                <pre class="wb-ai-text">{{ r.revised_clause || '（空）' }}</pre>
                <div v-if="r.explanation" class="wb-ai-exp">说明：{{ r.explanation }}</div>
                <div class="wb-chips">
                  <el-tag v-for="(c, ci) in r.constraints || []" :key="'c' + ci" size="small" effect="plain" type="info">约束：{{ c }}</el-tag>
                  <el-tag v-for="(lb, li) in r.legal_basis || []" :key="'l' + li" size="small" effect="plain" type="warning">依据：{{ lb }}</el-tag>
                </div>
                <div v-if="r.operation === 'add_clause' && r.position" class="wb-ai-pos">
                  插入位置：{{ positionText(r.position) }}
                </div>
                <div v-if="r.remaining_risks?.length" class="wb-ai-risk">
                  ⚠ 剩余风险：{{ r.remaining_risks.join('；') }}
                </div>
                <div class="wb-ai-actions">
                  <el-button size="small" text @click="toggleDiff(r.id)">{{ openDiff.has(r.id) ? '收起对比' : '修改前后对比' }}</el-button>
                  <el-button size="small" text @click="copyText(r.revised_clause)">复制修订稿</el-button>
                </div>
                <div v-if="openDiff.has(r.id)" class="wb-diff">
                  <div class="wb-diff-col">
                    <div class="wb-diff-t">修改前（本轮输入）</div>
                    <pre class="wb-diff-b">{{ r.clause_text || '（无）' }}</pre>
                  </div>
                  <div class="wb-diff-col">
                    <div class="wb-diff-t">修改后</div>
                    <pre class="wb-diff-b">{{ r.revised_clause || '（无）' }}</pre>
                  </div>
                </div>
              </div>
            </div>

            <!-- 在途轮次 -->
            <div v-if="uiFor.pending" class="wb-round">
              <div class="wb-round-head"><span class="wb-round-no">第 {{ s.rounds + 1 }} 轮</span><span class="wb-muted">进行中</span></div>
              <div class="wb-user-card"><div class="wb-card-label">用户要求</div><div class="wb-user-text">{{ uiFor.pending.instruction }}</div></div>
              <div class="wb-ai-card">
                <div class="wb-card-label">AI 正在生成修订建议…</div>
                <el-skeleton :rows="3" animated />
              </div>
            </div>
          </div>

          <!-- 输入区 -->
          <div class="wb-input">
            <div v-if="ws.sessionAction(s) === 'add_clause' && s.rounds" class="wb-refine-tip">
              正在继续修改「新增条款」（插入位置：{{ positionText(s.lastRev?.position) }}）；
              提交将覆盖同一位置的最终版本，不会重复插入。
            </div>
            <div v-if="!isOverview && !s.clauseText" class="wb-refine-tip warn">
              尚未确定修改对象：请先在右侧「修改位置」中确认位置，否则提交会被后端判定为无法定位，DOCX 也无法安全生成。
            </div>
            <div class="wb-chips-input">
              <el-tag
                v-for="c in quickChips" :key="c" size="small" effect="plain" class="wb-chip"
                @click="appendChip(c)"
              >{{ c }}</el-tag>
            </div>
            <div class="wb-input-row">
              <el-input
                v-model="uiFor.input"
                type="textarea"
                :rows="2"
                :placeholder="placeholder"
                @keydown.enter.exact.prevent="ws.sendRevise()"
              />
              <el-button type="primary" :loading="!!uiFor.pending" @click="ws.sendRevise()">发送</el-button>
            </div>
          </div>
        </template>
      </section>

      <!-- ── 右栏：修改上下文 ── -->
      <aside ref="ctxRailRef" class="wb-right">
        <!-- ① 定位状态 -->
        <div class="wb-card" :class="{ 'is-focus': locateFocus }">
          <LocateClausePanel :ws="ws" />
        </div>

        <!-- ② 风险 / 条款信息 -->
        <div class="wb-card">
          <div class="wb-card-t">风险 / 条款信息</div>
          <template v-if="s?.risk">
            <div class="wb-kv"><span class="k">风险</span><span class="v">{{ s.risk.risk_type }} · {{ riskName(s.risk.risk_type) }}</span></div>
            <div class="wb-kv"><span class="k">等级</span><span class="v">{{ riskLevelLabel(s.risk.risk_level) }}</span></div>
            <div class="wb-kv"><span class="k">置信度</span><span class="v">{{ Math.round((s.risk.confidence || 0) * 100) }}%</span></div>
            <div class="wb-kv"><span class="k">检测方式</span><span class="v">{{ detectionLabel(s.risk.detection_method) }}</span></div>
            <div class="wb-kv"><span class="k">判定理由</span><span class="v">{{ s.risk.reason || '—' }}</span></div>
            <div class="wb-kv"><span class="k">修改建议</span><span class="v">{{ s.risk.suggestion || '—' }}</span></div>
            <div v-if="s.risk.risk_description" class="wb-kv"><span class="k">风险说明</span><span class="v">{{ s.risk.risk_description }}</span></div>
            <div v-if="s.risk.legal_basis" class="wb-kv"><span class="k">法律依据</span><span class="v">{{ s.risk.legal_basis }}</span></div>
            <div class="wb-kv"><span class="k">涉及条款</span><span class="v mono">{{ s.risk.clause_text || '—' }}</span></div>
          </template>
          <template v-else-if="s?.cmp">
            <div class="wb-kv"><span class="k">标准条款</span><span class="v">{{ s.cmp.title }}</span></div>
            <div class="wb-kv"><span class="k">状态</span><span class="v">
              {{ { covered: '已覆盖', partial: '部分偏离', missing: '缺失' }[s.cmp.status] || s.cmp.status }}
            </span></div>
            <div class="wb-kv"><span class="k">优先级</span><span class="v">{{ s.cmp.priority || '—' }}</span></div>
            <div v-if="s.cmp.matched_text" class="wb-kv"><span class="k">当前条款</span><span class="v mono">{{ s.cmp.matched_text }}</span></div>
            <div v-if="s.cmp.deviation" class="wb-kv"><span class="k">偏离说明</span><span class="v">{{ s.cmp.deviation }}</span></div>
            <div v-if="s.cmp.completion" class="wb-kv"><span class="k">补全建议</span><span class="v">{{ s.cmp.completion }}</span></div>
            <div v-if="s.cmp.risk" class="wb-kv"><span class="k">风险说明</span><span class="v">{{ s.cmp.risk }}</span></div>
            <div v-if="s.cmp.related_law" class="wb-kv"><span class="k">相关法条</span><span class="v">{{ s.cmp.related_law }}</span></div>
          </template>
          <div v-else-if="isOverview" class="wb-muted">
            总体会话不绑定单一风险/比对项，上下文按各专项会话最新一轮汇总后提交。
          </div>
          <div v-else class="wb-muted">
            该会话在当前审核结果与比对结果中都不存在对应条目（历史会话）。
            后端只保存了它的条款文本与多轮修订，没有风险/比对元信息。
          </div>
        </div>

        <!-- ③ RAG 推荐 -->
        <div class="wb-card">
          <div class="wb-card-t">RAG 参考范本 / 法律依据</div>
          <div v-if="addTemplates.length" class="wb-rag">
            <div v-for="(t, i) in addTemplates" :key="i" class="wb-rag-item">{{ t }}</div>
          </div>
          <div v-else class="wb-muted">
            仅「新增条款」向导会展示后端返回的同类范本与法律依据；后端未把范本持久化到修改记录中，
            因此历史会话刷新后无法恢复这部分内容。
          </div>
        </div>

        <!-- ④ 剩余风险 -->
        <div class="wb-card">
          <div class="wb-card-t">剩余风险</div>
          <template v-if="s?.lastRev?.remaining_risks?.length">
            <ul class="wb-list">
              <li v-for="(r, i) in s.lastRev.remaining_risks" :key="i">{{ r }}</li>
            </ul>
          </template>
          <div v-else class="wb-muted">
            {{ s?.rounds ? '后端最新一轮未报告剩余风险（Self-QA 校验通过或未返回）。' : '尚无修订记录。' }}
          </div>
        </div>

        <!-- ⑤ DOCX 导出状态 -->
        <div class="wb-card">
          <div class="wb-card-t">DOCX 导出状态</div>
          <div class="wb-docx" :class="'st-' + ws.docxState.key">
            <el-tag size="small" :type="docxTag" effect="dark">{{ docxStateLabel }}</el-tag>
            <div class="wb-docx-t">{{ ws.docxState.text }}</div>
            <div class="wb-docx-d">{{ ws.docxState.detail }}</div>
          </div>

          <div v-if="ws.docxBlockers.length" class="wb-docx-blockers">
            <div class="t">预计尚未建立可靠定位的会话（前端预判，最终以后端判定为准）：</div>
            <div v-for="b in ws.docxBlockers" :key="b.key" class="item" @click="ws.selectSession(b.key)">
              {{ b.title }}<span class="wb-muted"> · 点击进入处理</span>
            </div>
          </div>

          <el-alert
            v-if="ws.docx.error" type="error" show-icon :closable="false" class="wb-docx-err"
            title="导出失败（后端原文返回）"
          >
            <template #default>
              <div class="wb-docx-err-t">{{ ws.docx.error }}</div>
              <el-button size="small" @click="ws.downloadDocx()">重试</el-button>
            </template>
          </el-alert>

          <div class="wb-docx-note">
            后端在「有修订无法在合同原文中定位」时会整体拒绝导出（宁可不导出，也不交付漏改的文件）。
            遇到这种情况：在该会话右侧指定并确认修改位置，或改用「新增条款」，然后重新下载。
          </div>
        </div>

        <div class="wb-card">
          <div class="wb-card-t">修改记录元信息</div>
          <div class="wb-kv"><span class="k">会话标识</span><span class="v mono">{{ s?.key || '—' }}</span></div>
          <div class="wb-kv"><span class="k">scope</span><span class="v mono">{{ s?.lastRev?.scope || (isOverview ? 'overview' : 'clause') }}</span></div>
          <div class="wb-kv"><span class="k">轮数</span><span class="v">{{ s?.rounds || 0 }}（一轮 = 一条 ClauseRevision，不合并）</span></div>
          <div class="wb-kv"><span class="k">DOCX 消费</span><span class="v">{{ isOverview ? '仅 overview+add_clause 会被写入' : (s?.lastRev?.operation === 'add_clause' ? '按插入位置写入' : '按原文锚点替换') }}</span></div>
        </div>
      </aside>
    </div>

    <AddClauseWizard :ws="ws" />
  </div>
</template>

<script setup>
import { ref, computed, reactive } from 'vue'
import { ElMessage } from 'element-plus'
import LocateClausePanel from './LocateClausePanel.vue'
import AddClauseWizard from './AddClauseWizard.vue'
import { SESSION_GROUPS, displayInstruction, positionText } from '../../composables/useContractWorkspace.js'
import { riskName, riskLevelLabel, detectionLabel } from '../../constants/riskTypes.js'

const props = defineProps({ ws: { type: Object, required: true } })

const ctxRailRef = ref(null)
const locateFocus = ref(false)
const openDiff = reactive(new Set())

const s = computed(() => props.ws.activeSession)
const isOverview = computed(() => s.value?.key === '__overview__')
const badge = computed(() => props.ws.locateBadge(s.value))
const uiFor = computed(() => (s.value ? props.ws.uiFor(s.value.key) : { input: '', pending: null }))

/** 新增条款向导里返回的范本（未持久化，只对本次会话引导有效） */
const addTemplates = computed(() => {
  const w = props.ws.addWizard
  if (!w.templates?.length) return []
  return w.templates.map((t) => `${t.type || '范本'}｜${t.text}`)
})

const quickChips = ['按推荐条款修改', '更严格', '更宽松', '补强法律依据', '重新起草']

const placeholder = computed(() => {
  if (isOverview.value) return '对整个合同的修改要求…（例如：结合前面讨论过的问题统一调整违约金与验收安排）'
  if (props.ws.sessionAction(s.value) === 'add_clause') return '继续修改新增条款，例如：把通知义务改为「15 日内书面通知」…'
  return '输入修改要求，例如：把违约金上限从 30% 改为 20%'
})

function groupShort(g) {
  return { overview: '总体', risk: '风险', cmp: '比对', add: '新增', history: '历史' }[g] || g
}
function groupTag(g) {
  return { overview: 'info', risk: 'danger', cmp: 'warning', add: 'success', history: 'info' }[g] || 'info'
}
function opLabel(item) {
  if (!item) return ''
  if (item.key === '__overview__') return 'scope=overview'
  return item.lastRev?.operation === 'add_clause' ? 'operation=add_clause' : 'operation=replace'
}
function fmtTime(ts) {
  if (!ts) return ''
  try {
    return new Date(ts).toLocaleString('zh-CN', { hour12: false })
  } catch {
    return ts
  }
}
function toggleDiff(id) {
  if (openDiff.has(id)) openDiff.delete(id)
  else openDiff.add(id)
}
async function copyText(t) {
  try {
    await navigator.clipboard.writeText(t || '')
    ElMessage.success('已复制')
  } catch {
    ElMessage.warning('复制失败，请手动选择文本')
  }
}
function appendChip(c) {
  const ui = props.ws.uiFor(s.value.key)
  ui.input = ui.input ? ui.input + '；' + c : c
}
function focusLocate() {
  locateFocus.value = true
  ctxRailRef.value?.querySelector('.wb-card')?.scrollIntoView({ block: 'start', behavior: 'smooth' })
  setTimeout(() => (locateFocus.value = false), 1200)
}
function openAddForSession() {
  const item = s.value
  if (!item) return
  if (item.risk) {
    props.ws.openAddWizard({
      targetKey: item.key,
      targetScope: 'clause',
      riskType: item.risk.risk_type,
      riskTypeLabel: `${item.risk.risk_type} · ${riskName(item.risk.risk_type)}`,
      clauseNo: item.risk.clause_no ? String(item.risk.clause_no) : '',
      prefill: item.risk.risk_type === 'R09'
        ? '新增不可抗力条款，明确不可抗力的定义、通知义务与免责安排'
        : `新增缺失条款：${riskName(item.risk.risk_type)}`,
    })
  } else if (item.cmp) {
    props.ws.openAddWizard({
      targetKey: item.key,
      targetScope: 'clause',
      riskType: item.cmp.title || '',
      riskTypeLabel: item.cmp.title || '',
      prefill: `${item.cmp.title || '缺失条款'}：${(item.cmp.completion || '').trim() || '请依据标准范本起草该条款'}`,
    })
  } else {
    props.ws.openAddWizard({ targetKey: item.key, targetScope: 'clause', riskType: '', prefill: '' })
  }
}

const docxTag = computed(() => {
  return { ready: 'success', none: 'info', not_docx: 'info', blocked: 'warning', loading: 'info' }[props.ws.docxState.key] || 'info'
})
const docxStateLabel = computed(() => {
  return { ready: '可以导出', none: '无修改', not_docx: '非 DOCX 源文件', blocked: '暂不能导出', loading: '加载中' }[props.ws.docxState.key] || '未知'
})
</script>

<style scoped>
.wb { display: flex; flex-direction: column; height: 100%; min-height: 0; gap: 10px; }
.wb-top { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; flex-shrink: 0; }
.wb-top-l { display: flex; align-items: center; gap: 8px; min-width: 0; }
.wb-top-r { margin-left: auto; display: flex; gap: 8px; }
.wb-session-title { font-size: 15px; font-weight: 700; color: #131313; }
.wb-rounds { font-size: 12px; color: #8A93A6; }
.wb-spacer { flex: 1; }

.wb-body { display: flex; gap: 12px; flex: 1; min-height: 0; }

/* 左栏 */
.wb-left { flex: 0 0 276px; display: flex; flex-direction: column; min-height: 0; border-right: 1px solid var(--a24-border); padding-right: 10px; }
.wb-stats { display: flex; gap: 6px; flex-shrink: 0; }
.wb-stat { flex: 1; background: #F8FAFD; border-radius: 8px; padding: 6px 8px; text-align: center; }
.wb-stat b { display: block; font-size: 17px; color: #131313; }
.wb-stat span { font-size: 10.5px; color: #8A93A6; }
.wb-stats-note { font-size: 10.5px; color: #9AA4B8; margin: 6px 0 8px; line-height: 1.5; flex-shrink: 0; }
.wb-left-err { font-size: 12px; color: #B91C1C; margin-bottom: 6px; }
.wb-groups { flex: 1; min-height: 0; overflow-y: auto; }
.wb-group-title { display: flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 600; color: #6B7280; margin: 10px 2px 6px; }
.wb-group-title .n { background: #EEF1F6; color: #6B7280; border-radius: 8px; padding: 0 6px; font-size: 11px; }
.wb-group-empty { font-size: 11.5px; color: #C0C4CC; padding: 2px 2px 4px; }
.wb-item { border: 1px solid var(--a24-border); border-left: 2px solid transparent; border-radius: 8px; padding: 8px 10px; margin-bottom: 6px; cursor: pointer; }
.wb-item:hover { background: #F8FAFD; }
.wb-item.is-active { border-color: #C7D2E5; border-left-color: var(--a24-primary); background: #F3F6FD; }
.wb-item-top { display: flex; align-items: center; gap: 6px; }
.wb-item-title { font-size: 12.5px; font-weight: 600; color: #131313; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.wb-item-rounds { margin-left: auto; font-size: 11px; color: #16A34A; flex-shrink: 0; }
.wb-item-sub { font-size: 11.5px; color: #8A93A6; margin-top: 3px; }
.wb-item-locate { display: flex; align-items: center; gap: 5px; margin-top: 5px; }
.wb-item-locate .dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.dot-success { background: #16A34A; }
.dot-primary { background: #1935C3; }
.dot-warning { background: #D97706; }
.dot-danger { background: #DC2626; }
.dot-info { background: #9AA4B8; }
.wb-item-locate .txt { font-size: 11px; color: #8A93A6; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* 中栏 */
.wb-center { flex: 1; min-width: 0; display: flex; flex-direction: column; min-height: 0; }
.wb-ctx { flex-shrink: 0; background: #F8FAFD; border-radius: 8px; padding: 8px 10px; margin-bottom: 8px; }
.wb-ctx-row { display: flex; align-items: center; gap: 8px; }
.wb-ctx-row .k { font-size: 12px; color: #8A93A6; flex-shrink: 0; }
.wb-ctx-row .v { font-size: 13.5px; font-weight: 600; color: #131313; }
.wb-ctx-orig { display: flex; gap: 8px; margin-top: 6px; }
.wb-ctx-orig .k { font-size: 12px; color: #8A93A6; flex-shrink: 0; }
.wb-ctx-orig .v { font-size: 12.5px; color: #4B5563; line-height: 1.7; }
.wb-ctx-orig .v.muted { color: #C0C4CC; }
.wb-overview-note { flex-shrink: 0; margin-bottom: 8px; }
.wb-overview-count { display: block; margin-top: 4px; color: #B45309; }
.wb-chat { flex: 1; min-height: 0; overflow-y: auto; padding-right: 4px; }
.wb-empty { font-size: 12.5px; color: #9AA4B8; text-align: center; padding: 36px 12px; line-height: 1.8; }
.wb-round { margin-bottom: 14px; }
.wb-round-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.wb-round-no { font-size: 12px; font-weight: 700; color: var(--a24-primary); }
.wb-muted { font-size: 11.5px; color: #9AA4B8; }
.wb-user-card { border: 1px solid var(--a24-border); background: #FBFCFE; border-radius: 8px; padding: 8px 10px; }
.wb-card-label { font-size: 11.5px; color: #8A93A6; margin-bottom: 4px; }
.wb-user-text { font-size: 12.5px; color: #303133; line-height: 1.75; white-space: pre-wrap; }
.wb-ai-card { border: 1px solid var(--a24-border); border-left: 3px solid var(--a24-primary); border-radius: 8px; padding: 10px 12px; margin-top: 6px; }
.wb-ai-text { font-family: "Noto Serif SC", "Songti SC", serif; font-size: 13px; line-height: 1.85; color: #1F2937; white-space: pre-wrap; word-break: break-word; margin: 0; }
.wb-ai-exp { font-size: 12px; color: #6B7280; margin-top: 6px; }
.wb-chips { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
.wb-ai-pos, .wb-ai-risk { font-size: 12px; margin-top: 6px; color: #B45309; }
.wb-ai-actions { margin-top: 6px; display: flex; gap: 4px; }
.wb-diff { display: flex; gap: 8px; margin-top: 8px; }
.wb-diff-col { flex: 1; min-width: 0; }
.wb-diff-t { font-size: 11.5px; color: #8A93A6; margin-bottom: 4px; }
.wb-diff-b { font-size: 12px; line-height: 1.8; white-space: pre-wrap; word-break: break-word; background: #F8FAFD; border-radius: 6px; padding: 8px; margin: 0; max-height: 220px; overflow: auto; }

.wb-input { flex-shrink: 0; border-top: 1px solid var(--a24-border); padding-top: 8px; margin-top: 8px; }
.wb-refine-tip { font-size: 11.5px; color: #166534; background: #F0FDF4; border-radius: 6px; padding: 5px 8px; margin-bottom: 6px; }
.wb-refine-tip.warn { color: #B45309; background: #FFFBEB; }
.wb-chips-input { display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 6px; }
.wb-chip { cursor: pointer; }
.wb-input-row { display: flex; gap: 8px; align-items: flex-end; }
.wb-input-row .el-textarea { flex: 1; }

/* 右栏 */
.wb-right { flex: 0 0 340px; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; }
.wb-card { border: 1px solid var(--a24-border); border-radius: 10px; padding: 10px 12px; background: #fff; }
.wb-card.is-focus { box-shadow: 0 0 0 2px rgba(25, 53, 195, 0.18); }
.wb-card-t { font-size: 12.5px; font-weight: 600; color: #6B7280; margin-bottom: 8px; }
.wb-kv { display: flex; gap: 8px; font-size: 12px; line-height: 1.7; margin-bottom: 4px; }
.wb-kv .k { flex: 0 0 62px; color: #8A93A6; }
.wb-kv .v { flex: 1; color: #303133; word-break: break-word; white-space: pre-wrap; }
.wb-kv .v.mono { font-family: ui-monospace, Consolas, monospace; font-size: 11.5px; }
.wb-muted { font-size: 11.5px; color: #9AA4B8; line-height: 1.7; }
.wb-rag-item { font-size: 12px; color: #4B5563; line-height: 1.7; border-left: 2px solid #E5E7EB; padding-left: 8px; margin-bottom: 6px; }
.wb-list { margin: 0; padding-left: 16px; font-size: 12px; color: #B45309; line-height: 1.8; }
.wb-docx { margin-bottom: 8px; }
.wb-docx-t { font-size: 12.5px; font-weight: 600; margin-top: 6px; color: #303133; }
.wb-docx-d { font-size: 11.5px; color: #8A93A6; line-height: 1.7; margin-top: 2px; }
.wb-docx.st-ready .wb-docx-t { color: #166534; }
.wb-docx.st-blocked .wb-docx-t { color: #B45309; }
.wb-docx-blockers { font-size: 11.5px; color: #B45309; border-top: 1px dashed var(--a24-border); padding-top: 8px; margin-bottom: 8px; }
.wb-docx-blockers .t { margin-bottom: 4px; }
.wb-docx-blockers .item { cursor: pointer; padding: 3px 0; color: #303133; }
.wb-docx-blockers .item:hover { color: var(--a24-primary); }
.wb-docx-err { margin-bottom: 8px; }
.wb-docx-err-t { font-size: 12px; line-height: 1.7; margin-bottom: 6px; }
.wb-docx-note { font-size: 11px; color: #9AA4B8; line-height: 1.7; border-top: 1px dashed var(--a24-border); padding-top: 8px; }
</style>
