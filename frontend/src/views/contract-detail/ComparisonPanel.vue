<template>
  <div class="cp">
    <!-- 概览条 -->
    <div v-if="summary" class="cp-overview">
      <div class="cp-metric">
        <div class="n">{{ Math.round((summary.coverage_rate || 0) * 100) }}%</div>
        <div class="l">条款覆盖率</div>
      </div>
      <div class="cp-metric"><div class="n">{{ summary.total || 0 }}</div><div class="l">比对标准条款</div></div>
      <div class="cp-metric"><div class="n ok">{{ summary.covered || 0 }}</div><div class="l">已覆盖</div></div>
      <div class="cp-metric"><div class="n mid">{{ summary.partial || 0 }}</div><div class="l">部分偏离</div></div>
      <div class="cp-metric"><div class="n bad">{{ summary.missing || 0 }}</div><div class="l">缺失</div></div>
      <div class="cp-spacer" />
      <el-button size="small" :loading="ws.comparing" @click="ws.loadComparison()">刷新比对结果</el-button>
    </div>

    <el-alert
      v-if="ws.comparisonError"
      type="error" show-icon :closable="false" :title="ws.comparisonError"
      class="cp-alert"
    >
      <el-button size="small" @click="ws.loadComparison()">重试</el-button>
    </el-alert>

    <!-- 后端在「未生成 / 生成失败 / 无正文 / 无权」时都返回 200 + data:null，前端无法区分，
         因此这里如实说明两种可能，不假装成功。 -->
    <el-alert
      v-else-if="!ws.comparing && !ws.clauseComparison"
      type="warning" show-icon :closable="false" class="cp-alert"
      title="暂无条款比对结果"
    >
      <template #default>
        <div class="cp-alert-body">
          后端在「尚未生成」「生成失败」「合同无正文」三种情况下都返回空结果，前端无法区分。
          可点击「重新比对」触发一次（后端同步执行 LLM 比对，长合同可能需要 30–300 秒）。
        </div>
        <el-button size="small" type="primary" @click="ws.loadComparison()">重新比对</el-button>
      </template>
    </el-alert>

    <div v-if="ws.comparing" class="cp-loading">
      <el-skeleton :rows="5" animated />
      <div class="cp-loading-tip">正在执行条款比对（同步 LLM，长合同耗时较长，请勿关闭页面）…</div>
    </div>

    <template v-else-if="ws.clauseComparison">
      <!-- 缺失关键条款 chips -->
      <div v-if="missingCritical.length" class="cp-chips">
        <span class="cp-chips-t">缺失关键条款</span>
        <el-tag v-for="c in missingCritical" :key="c" type="danger" size="small" effect="plain">{{ c }}</el-tag>
      </div>

      <!-- 跨条款关联风险（真实字段：type / clause / depends_on|conflicts_with / risk） -->
      <div v-if="crossRisks.length" class="cp-cross">
        <div class="cp-cross-t">跨条款关联风险（后端图分析）</div>
        <div v-for="(r, i) in crossRisks" :key="i" class="cp-cross-item">
          <el-tag size="small" :type="r.type === '前置依赖缺失' ? 'danger' : 'warning'">{{ r.type }}</el-tag>
          <span class="cp-cross-txt">{{ r.risk }}</span>
          <span class="cp-cross-ref">
            {{ r.clause }}
            <template v-if="r.depends_on">→ 依赖：{{ r.depends_on }}</template>
            <template v-if="r.conflicts_with">→ 与「{{ r.conflicts_with }}」互斥</template>
          </span>
        </div>
      </div>

      <!-- 缺失条款 -->
      <section class="cp-group">
        <div class="cp-group-head">
          <span class="t">缺失条款</span>
          <el-tag size="small" type="danger" effect="plain">{{ groups.missing.length }}</el-tag>
          <span class="hint">该条款在标准范本中存在，本合同中缺失 → 走「新增条款」</span>
        </div>
        <div v-if="!groups.missing.length" class="cp-none">无缺失条款</div>
        <div v-for="row in groups.missing" :key="row.title" class="cp-card">
          <div class="cp-card-head">
            <el-tag size="small" type="danger">缺失</el-tag>
            <span class="cp-title">{{ row.title }}</span>
            <el-tag v-if="row.priority" size="small" effect="plain" type="info">{{ row.priority }}</el-tag>
            <span class="cp-spacer" />
            <el-button size="small" type="success" @click="goAdd(row)">新增条款</el-button>
          </div>
          <div class="cp-body">
            <div v-if="row.risk" class="cp-kv"><span class="k">风险说明</span><span class="v">{{ row.risk }}</span></div>
            <div v-if="row.completion" class="cp-kv"><span class="k">补全建议</span><span class="v">{{ row.completion }}</span></div>
            <div v-if="row.related_law" class="cp-kv"><span class="k">相关法条</span><span class="v">{{ row.related_law }}</span></div>
            <div v-if="!row.risk && !row.completion" class="cp-none">后端未给出该缺失条款的风险说明与补全建议。</div>
          </div>
        </div>
      </section>

      <!-- 存在偏离 -->
      <section class="cp-group">
        <div class="cp-group-head">
          <span class="t">存在偏离</span>
          <el-tag size="small" type="warning" effect="plain">{{ groups.deviating.length }}</el-tag>
          <span class="hint">partial，或 covered 但确有偏离/补全建议 → 走「修改条款」</span>
        </div>
        <div v-if="!groups.deviating.length" class="cp-none">无偏离条款</div>
        <div v-for="row in groups.deviating" :key="row.title" class="cp-card">
          <div class="cp-card-head">
            <el-tag size="small" :type="row.status === 'covered' ? 'success' : 'warning'">
              {{ row.status === 'covered' ? '已覆盖（有偏离）' : '部分偏离' }}
            </el-tag>
            <span class="cp-title">{{ row.title }}</span>
            <el-tag v-if="row.priority" size="small" effect="plain" type="info">{{ row.priority }}</el-tag>
            <span v-if="typeof row.similarity === 'number'" class="cp-sim">相似度 {{ Math.round(row.similarity * 100) }}%</span>
            <span class="cp-spacer" />
            <el-button size="small" type="warning" plain @click="goRevise(row)">修改条款</el-button>
          </div>
          <div class="cp-body">
            <div v-if="row.matched_text" class="cp-kv"><span class="k">当前合同条款</span><span class="v mono">{{ row.matched_text }}</span></div>
            <div v-if="row.deviation" class="cp-kv"><span class="k">偏离说明</span><span class="v">{{ row.deviation }}</span></div>
            <div v-if="row.completion" class="cp-kv"><span class="k">补全建议</span><span class="v">{{ row.completion }}</span></div>
            <div v-if="row.risk" class="cp-kv"><span class="k">风险说明</span><span class="v">{{ row.risk }}</span></div>
            <div v-if="row.related_law" class="cp-kv"><span class="k">相关法条</span><span class="v">{{ row.related_law }}</span></div>
          </div>
        </div>
      </section>

      <!-- 已覆盖 -->
      <section class="cp-group">
        <div class="cp-group-head">
          <span class="t">已覆盖（无偏离）</span>
          <el-tag size="small" type="success" effect="plain">{{ groups.covered.length }}</el-tag>
          <span class="hint">正常展示；展开可查看后端是否仍给出偏离/补全说明</span>
        </div>
        <div v-if="!groups.covered.length" class="cp-none">无</div>
        <div v-else class="cp-covered">
          <div v-for="row in groups.covered" :key="row.title" class="cp-covered-item">
            <el-tag size="small" type="success" effect="plain">已覆盖</el-tag>
            <span class="cp-title">{{ row.title }}</span>
            <span v-if="typeof row.similarity === 'number'" class="cp-sim">相似度 {{ Math.round(row.similarity * 100) }}%</span>
            <span class="cp-spacer" />
            <span v-if="!row.deviation && !row.completion" class="cp-none-inline">后端无偏离说明</span>
            <template v-else>
              <span class="cp-has-note">后端仍给出说明</span>
              <span class="cp-covered-note">{{ row.deviation || row.completion }}</span>
            </template>
          </div>
        </div>
      </section>

      <div class="cp-foot">
        说明：标准条款正文（范本内容）后端不随比对结果返回，因此本页只展示条款名与优先级；
        `depends_on` / `conflict_with` 是后端内部用于计算「跨条款关联风险」的字段，不在单条比对结果中。
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { ElMessage } from 'element-plus'
import { cmpSessionKey, hasComparisonIssue } from '../../composables/useContractWorkspace.js'

const props = defineProps({ ws: { type: Object, required: true } })

const clauses = computed(() => props.ws.clauseComparison?.clauses || [])
const summary = computed(() => props.ws.clauseComparison?.summary || null)
const missingCritical = computed(() => props.ws.clauseComparison?.missing_critical || [])
const crossRisks = computed(() => props.ws.clauseComparison?.cross_clause_risks || [])

const groups = computed(() => ({
  missing: clauses.value.filter((r) => r.status === 'missing'),
  deviating: clauses.value.filter((r) => r.status !== 'missing' && hasComparisonIssue(r)),
  covered: clauses.value.filter((r) => r.status !== 'missing' && !hasComparisonIssue(r)),
}))

/** missing → 新增条款（会话 key 恒为 '__cmp__'+title，不得更改） */
function goAdd(row) {
  const key = cmpSessionKey(row)
  props.ws.openAddWizard({
    targetKey: key,
    targetScope: 'clause',
    riskType: row.title || '',
    riskTypeLabel: row.title || '',
    prefill: `${row.title || '缺失条款'}：${(row.completion || '').trim() || '请依据标准范本起草该条款'}`,
  })
}

/** partial/偏离 → 替换修改（进同一套 revision 系统） */
function goRevise(row) {
  props.ws.selectSession(cmpSessionKey(row))
  props.ws.setTab('revise')
  if (!row.matched_text) {
    ElMessage.info('该条款后端未返回合同中的匹配原文，请在工作台右侧「修改位置」中指定并确认位置')
  }
}
</script>

<style scoped>
.cp { display: flex; flex-direction: column; gap: 12px; }
.cp-overview { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.cp-metric { background: #fff; border: 1px solid var(--a24-border); border-radius: 10px; padding: 8px 14px; min-width: 88px; }
.cp-metric .n { font-size: 20px; font-weight: 700; color: #131313; line-height: 1.1; }
.cp-metric .n.ok { color: #16A34A; }
.cp-metric .n.mid { color: #D97706; }
.cp-metric .n.bad { color: #DC2626; }
.cp-metric .l { font-size: 11.5px; color: #8A93A6; margin-top: 2px; }
.cp-spacer { flex: 1; }
.cp-alert { margin: 0; }
.cp-alert-body { font-size: 12.5px; line-height: 1.7; margin-bottom: 8px; }
.cp-loading-tip { font-size: 12.5px; color: #8A93A6; margin-top: 10px; }
.cp-chips { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.cp-chips-t { font-size: 12.5px; color: #6B7280; font-weight: 600; }
.cp-cross { background: #FEF2F2; border: 1px solid #FBC4C4; border-radius: 10px; padding: 10px 12px; }
.cp-cross-t { font-size: 12.5px; font-weight: 600; color: #B91C1C; margin-bottom: 6px; }
.cp-cross-item { display: flex; align-items: baseline; gap: 8px; font-size: 12.5px; margin-top: 6px; flex-wrap: wrap; }
.cp-cross-txt { color: #7F1D1D; }
.cp-cross-ref { color: #9A3412; }
.cp-group { background: #fff; border: 1px solid var(--a24-border); border-radius: 10px; padding: 12px 14px; }
.cp-group-head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.cp-group-head .t { font-size: 14px; font-weight: 600; color: #131313; }
.cp-group-head .hint { font-size: 12px; color: #8A93A6; }
.cp-none { font-size: 12.5px; color: #9AA4B8; }
.cp-none-inline { font-size: 12px; color: #9AA4B8; }
.cp-has-note { font-size: 12px; color: #D97706; }
.cp-card { border: 1px solid var(--a24-border); border-radius: 8px; padding: 10px 12px; margin-bottom: 8px; }
.cp-card-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.cp-title { font-size: 13.5px; font-weight: 600; color: #131313; }
.cp-sim { font-size: 12px; color: #8A93A6; }
.cp-body { margin-top: 8px; display: flex; flex-direction: column; gap: 4px; }
.cp-kv { display: flex; gap: 8px; font-size: 12.5px; line-height: 1.7; }
.cp-kv .k { flex: 0 0 84px; color: #8A93A6; }
.cp-kv .v { flex: 1; color: #303133; word-break: break-word; white-space: pre-wrap; }
.cp-kv .v.mono { font-family: ui-monospace, Consolas, monospace; font-size: 12px; }
.cp-covered { display: flex; flex-direction: column; gap: 6px; }
.cp-covered-item { display: flex; align-items: center; gap: 8px; font-size: 12.5px; padding: 6px 8px; border-radius: 6px; background: #F8FAFD; flex-wrap: wrap; }
.cp-covered-note { flex-basis: 100%; font-size: 12px; color: #4B5563; padding-left: 4px; }
.cp-foot { font-size: 12px; color: #8A93A6; line-height: 1.7; }
</style>
