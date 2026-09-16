<template>
  <div class="lc">
    <div class="lc-head">
      <span class="t">修改位置</span>
      <el-tag size="small" :type="badge.type" effect="light">{{ badge.text }}</el-tag>
    </div>

    <!-- 新增条款会话：定位不适用（位置在新增条款工作区里选） -->
    <div v-if="!applicable" class="lc-note">
      该会话是「新增条款」，插入位置由你在新增条款工作区里确认，不需要在这里做原文替换定位。
    </div>

    <template v-else>
      <!-- 已确认 / 已自动找到 -->
      <div v-if="currentAnchor" class="lc-confirmed">
        <div class="lc-ok-t">
          ✓ 已确认位置
          <span class="tip">{{ currentAnchorFromAuto ? '（审核阶段已自动找到原文）' : '（你确认的位置）' }}</span>
        </div>
        <div class="lc-kv"><span class="k">条款</span><span class="v">
          {{ currentAnchor.clause_no ? `第${ws.cnNo(currentAnchor.clause_no)}条` : '（无编号）' }}
          {{ currentAnchor.clause_title || '' }}
        </span></div>
        <div class="lc-kv"><span class="k">原文</span><span class="v mono">{{ currentAnchor.original_text }}</span></div>
        <el-button v-if="!currentAnchorFromAuto" size="small" text type="danger" @click="ws.clearLocate(s)">清除确认</el-button>
      </div>

      <!-- 三种指定位置方式（条款号 / 搜索 / 描述） -->
      <el-tabs v-model="mode" class="lc-tabs">
        <el-tab-pane label="按条款号" name="anchor">
          <div class="lc-row">
            <el-select v-model="anchorCn" size="small" filterable placeholder="选择条款编号" style="width:200px">
              <el-option
                v-for="h in headings"
                :key="h.num"
                :label="`第${h.cn}条 ${h.title || ''}`"
                :value="String(h.num)"
              />
            </el-select>
            <el-button size="small" type="primary" :loading="ui.locateLoading" @click="runAnchor">查找位置</el-button>
          </div>
          <div class="lc-hint">按条款编号查找，系统会返回该条款在合同里的原文。</div>
        </el-tab-pane>

        <el-tab-pane label="搜索原文" name="search">
          <div class="lc-row">
            <el-input v-model="kw" size="small" placeholder="搜索合同原文，例如：违约金" clearable style="width:200px" />
            <el-button size="small" @click="doSearch">搜索</el-button>
          </div>
          <div v-if="hits.length" class="lc-hits">
            <div class="lc-hits-t">找到 {{ hits.length }} 处，请选择要修改的那一处：</div>
            <div v-for="(h, i) in hits" :key="i" class="lc-hit" @click="pickHit(h)">
              <span class="idx">#{{ i + 1 }}</span>
              <span class="snip">{{ h.snippet }}</span>
            </div>
          </div>
          <div v-else-if="searched" class="lc-hint">没找到匹配的位置，请换个关键词或描述。</div>
          <div class="lc-hint">
            选中后会把该处的<b>逐字原文</b>作为修改对象；若该段在合同里重复出现，系统会给出多个候选，需要你再确认一次。
          </div>
        </el-tab-pane>

        <el-tab-pane label="描述位置" name="describe">
          <div class="lc-row">
            <el-input
              v-model="desc"
              size="small"
              type="textarea"
              :rows="2"
              placeholder="描述这段大概在合同哪里，例如：违约责任章节里关于违约金的条款"
            />
          </div>
          <div class="lc-row">
            <el-button size="small" type="primary" :loading="ui.locateLoading" @click="runDescribe">查找位置</el-button>
          </div>
          <div class="lc-hint">只用文字描述也可以找位置；系统会给出候选，由你点选确认。</div>
        </el-tab-pane>
      </el-tabs>

      <div v-if="ui.locateError" class="lc-err">{{ ui.locateError }}</div>
      <div v-if="ui.locateLoading" class="lc-hint">正在查找…</div>

      <!-- 候选：必须由用户点选，系统不替你选 -->
      <div v-if="candidates.length" class="lc-result">
        <div class="lc-result-head">
          <el-tag size="small" :type="ui.locate?.found ? 'success' : 'warning'">
            {{ ui.locate?.found ? '找到 1 处' : `找到 ${candidates.length} 处候选` }}
          </el-tag>
          <span class="muted">{{ ui.locate?.reason }}</span>
        </div>

        <div class="lc-cands-t">
          {{ candidates.length > 1 ? '请点选正确的那一处（系统不会替你选）：' : '请确认是这一处：' }}
        </div>
        <div v-for="(c, i) in candidates" :key="c.key" class="lc-cand">
          <div class="lc-cand-body">
            <div class="lc-kv"><span class="k">#{{ i + 1 }}</span><span class="v">
              {{ c.clause_no ? `第${ws.cnNo(c.clause_no)}条` : '（无编号）' }}
              {{ c.clause_title || '' }}
            </span></div>
            <div class="lc-kv"><span class="k">原文</span><span class="v mono">{{ c.original_text }}</span></div>
          </div>
          <el-button size="small" type="primary" plain @click="ws.confirmLocate(s, c)">确认这一处</el-button>
        </div>
      </div>

      <div v-else-if="ui.locate && !ui.locate.found" class="lc-hint">
        没找到匹配的位置，请换个关键词或描述。
      </div>

      <div class="lc-foot">
        确认位置后，这条修改才会成为可写入合同文件的修改。
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'

/**
 * LocateClausePanel — 指定修改位置（三种方式：按条款号 / 搜索原文 / 描述位置）。
 *
 * 红线（V2.2 3.3）：
 *   - 系统只缩小范围，最终位置由用户确认；
 *   - 后端可能已给出唯一命中，但前端仍把它当作**候选项**渲染，必须用户点选；
 *   - **禁止**自动选中第一个候选并继续。
 *
 * 数据来源：只读 `POST /locate-clause`（不调 LLM、不写库、不建立任何修改记录）。
 */
const props = defineProps({
  ws: { type: Object, required: true },
})

const s = computed(() => props.ws.activeSession)
const ui = computed(() => (s.value ? props.ws.uiFor(s.value.key) : {}))
const badge = computed(() => props.ws.locateBadge(s.value))
const applicable = computed(
  () => !!s.value && s.value.key !== '__overview__' && props.ws.sessionAction(s.value) !== 'add_clause',
)

/** 审核阶段已自动找到的锚点（后端真实锚点，不是前端预检） */
const autoAnchor = computed(() => {
  const p = s.value?.risk?.clause_position
  if (!p?.original_text) return null
  return { clause_no: p.clause_no ?? null, clause_title: p.clause_title || '', original_text: p.original_text }
})
/** 用户已确认的位置（前端 UI 态；服务端锚点在提交修改时建立） */
const userAnchor = computed(() => ui.value.confirmAnchor?.original_text ? ui.value.confirmAnchor : null)
const currentAnchor = computed(() => userAnchor.value || autoAnchor.value)
const currentAnchorFromAuto = computed(() => !userAnchor.value && !!autoAnchor.value)

const headings = computed(() => props.ws.headings || [])

/** 候选列表：后端命中项 + 多候选，统一交给用户点选 */
const candidates = computed(() => props.ws.activeLocateCandidates || [])

const mode = ref('search')
const anchorCn = ref('')
const kw = ref('')
const desc = ref('')
const searched = ref(false)
const hits = ref([])

watch(s, () => {
  // 切换会话时清空局部输入（定位结果本身挂在会话 UI 状态上）
  kw.value = ''
  hits.value = []
  searched.value = false
  desc.value = ''
  anchorCn.value = ''
  mode.value = autoAnchor.value ? 'anchor' : 'search'
})

/** 方式一：按条款编号 → 后端返回该条款原文 */
async function runAnchor() {
  if (!anchorCn.value) {
    ElMessage.warning('请选择条款编号')
    return
  }
  await props.ws.runLocate(s.value, { clause_anchor: anchorCn.value })
}

/** 方式二：先在正文里搜关键词，用户点一处，再交给后端确认 */
function doSearch() {
  const q = String(kw.value || '').trim()
  searched.value = true
  hits.value = []
  if (!q) return
  const text = props.ws.parsedText || ''
  const out = []
  let i = 0
  for (;;) {
    const p = text.indexOf(q, i)
    if (p < 0) break
    const win = windowAt(text, p)
    const snippet = text.slice(win.start, win.end).trim()
    // 同一段窗口只保留一次（关键词反复命中同一句时不重复）
    if (!out.some((h) => h.start === win.start)) out.push({ ...win, snippet })
    i = p + q.length
    if (out.length >= 50) break
  }
  hits.value = out
  if (!out.length) ElMessage.info('没找到匹配的位置，请换个关键词或描述')
}

/**
 * 与后端 `_locate_at` 同规则截取「子条款窗口」：边界为（N）子项 / 句号 / 分号 / 换行，
 * 窗口过长（>150 字）时回退 ±60 字。这只是候选窗口的前端镜像；最终能否定位由后端判定。
 */
function windowAt(text, idx) {
  const re = /（\s*[一二三四五六七八九十百千\d]+\s*）|[。；;]|\n/g
  const positions = []
  let m
  while ((m = re.exec(text))) positions.push(m.index)
  const before = positions.filter((p) => p <= idx)
  let start = before.length ? Math.max(...before) : Math.max(0, idx - 60)
  const after = positions.filter((p) => p > idx)
  let end = after.length ? after[0] : Math.min(text.length, idx + 60)
  if (end < text.length && '。；;'.includes(text[end])) end += 1
  if (end - start > 150) {
    start = Math.max(0, idx - 60)
    end = Math.min(text.length, idx + 60)
  }
  return { start, end }
}

/** 用户点选命中项 → 交给后端确认；**不自动确认**，由用户在上方候选里点「确认这一处」 */
async function pickHit(h) {
  const res = await props.ws.runLocate(s.value, { text: h.snippet })
  if (!res) return
  if (res.found && !(res.candidates?.length > 1)) {
    // 唯一命中：仍然要求用户点一下确认（保持"人确认"这一关）
    ElMessage.info('已找到 1 处位置，请在上方点「确认这一处」')
  } else {
    ElMessage.info('找到多处位置，请点选正确的那一处')
  }
}

/** 方式三：描述位置 → 只定位，不修改 */
async function runDescribe() {
  const t = String(desc.value || '').trim()
  if (!t) {
    ElMessage.warning('请描述这段大概在合同哪里')
    return
  }
  await props.ws.runLocate(s.value, { text: t })
}
</script>

<style scoped>
.lc { display: flex; flex-direction: column; gap: 8px; }
.lc-head { display: flex; align-items: center; justify-content: space-between; }
.lc-head .t { font-size: 12.5px; font-weight: 600; color: #6B7280; }
.lc-note { font-size: 12.5px; color: #6B7280; background: #F8FAFD; border-radius: 6px; padding: 8px 10px; line-height: 1.7; }
.lc-confirmed { border: 1px solid #BFDBFE; background: #EFF6FF; border-radius: 8px; padding: 8px 10px; }
.lc-ok-t { font-size: 12.5px; font-weight: 600; color: #1D4ED8; margin-bottom: 6px; }
.lc-ok-t .tip { font-weight: 400; color: #6B7280; margin-left: 4px; }
.lc-kv { display: flex; gap: 8px; font-size: 12.5px; line-height: 1.7; margin-bottom: 3px; }
.lc-kv .k { flex: 0 0 42px; color: #8A93A6; }
.lc-kv .v { flex: 1; color: #303133; word-break: break-word; }
.mono { font-family: ui-monospace, Consolas, monospace; font-size: 12px; }
.lc-tabs { margin-top: 2px; }
.lc-row { display: flex; gap: 8px; align-items: center; margin-bottom: 6px; }
.lc-hint { font-size: 11.5px; color: #8A93A6; line-height: 1.7; }
.lc-hits { max-height: 200px; overflow-y: auto; margin-bottom: 6px; }
.lc-hits-t { font-size: 12px; color: #6B7280; margin-bottom: 4px; }
.lc-hit { display: flex; align-items: center; gap: 6px; font-size: 12px; padding: 6px; border: 1px solid var(--a24-border); border-radius: 6px; margin-bottom: 4px; cursor: pointer; }
.lc-hit:hover { border-color: #C7D2E5; background: #F8FAFD; }
.lc-hit .idx { color: #8A93A6; flex-shrink: 0; }
.lc-hit .snip { flex: 1; color: #303133; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.lc-err { font-size: 12px; color: #B91C1C; background: #FEF2F2; border-radius: 6px; padding: 6px 8px; }
.lc-result { border: 1px solid var(--a24-border); border-radius: 8px; padding: 8px 10px; }
.lc-result-head { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-bottom: 6px; }
.lc-cands-t { font-size: 12px; color: #6B7280; margin-bottom: 6px; }
.lc-cand { display: flex; align-items: flex-start; gap: 8px; border: 1px solid var(--a24-border); border-radius: 6px; padding: 6px 8px; margin-bottom: 6px; }
.lc-cand-body { flex: 1; min-width: 0; }
.lc-foot { font-size: 11.5px; color: #8A93A6; line-height: 1.7; }
.muted { color: #9AA4B8; font-size: 11.5px; }
</style>
