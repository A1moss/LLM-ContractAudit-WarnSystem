<template>
  <div class="lc">
    <div class="lc-head">
      <span class="t">修改位置</span>
      <el-tag size="small" :type="badge.type" effect="light">{{ badge.text }}</el-tag>
    </div>

    <!-- 总体会话 / 新增条款会话：定位不适用 -->
    <div v-if="!applicable" class="lc-note">
      <template v-if="isOverview">
        总体修改面向整个合同，不涉及单条条款的原文定位，也不会直接写回 DOCX。
      </template>
      <template v-else>
        该会话是「新增条款」，写入位置由你在新增条款向导中确认，无需替换定位。
      </template>
    </div>

    <template v-else>
      <!-- 已自动定位（审核阶段锚点） -->
      <div v-if="autoAnchor" class="lc-ok">
        <div class="lc-ok-t">审核阶段已建立原文锚点</div>
        <div class="lc-kv"><span class="k">条款</span><span class="v">
          {{ s.risk?.clause_position?.clause_no ? `第${ws.cnNo(s.risk.clause_position.clause_no)}条` : '（无编号）' }}
          {{ s.risk?.clause_position?.clause_title || '' }}
        </span></div>
        <div class="lc-kv"><span class="k">原文</span><span class="v mono">{{ s.risk?.clause_position?.original_text }}</span></div>
      </div>

      <!-- 用户已确认的位置（UI 态） -->
      <div v-if="ui.confirmAnchor?.original_text" class="lc-confirmed">
        <div class="lc-ok-t">已确认位置<span class="tip">（界面状态；服务端锚点在提交修改时建立）</span></div>
        <div class="lc-kv"><span class="k">条款</span><span class="v">
          {{ ui.confirmAnchor.clause_no ? `第${ws.cnNo(ui.confirmAnchor.clause_no)}条` : '（无编号）' }}
          {{ ui.confirmAnchor.clause_title || '' }}
        </span></div>
        <div class="lc-kv"><span class="k">原文</span><span class="v mono">{{ ui.confirmAnchor.original_text }}</span></div>
        <el-button size="small" text type="danger" @click="ws.clearLocate(s)">清除确认</el-button>
      </div>

      <!-- 三种方式 -->
      <el-tabs v-model="mode" class="lc-tabs">
        <!-- 方式一：条款选择 -->
        <el-tab-pane label="条款选择" name="anchor">
          <div class="lc-row">
            <el-select v-model="anchorCn" size="small" filterable placeholder="选择条款编号" style="width:200px">
              <el-option
                v-for="h in headings"
                :key="h.num"
                :label="`第${h.cn}条 ${h.title || ''}`"
                :value="String(h.num)"
              />
            </el-select>
            <el-button size="small" type="primary" :loading="ui.locateLoading" @click="runAnchor">定位该条款</el-button>
          </div>
          <div class="lc-hint">
            条款列表由前端从合同正文解析（与后端 `_parse_headings` 同规则）；点击「定位该条款」后由后端
            <code>POST /locate-clause</code> 返回该条款的逐字原文。
          </div>
        </el-tab-pane>

        <!-- 方式二：原文搜索 -->
        <el-tab-pane label="原文搜索" name="search">
          <div class="lc-row">
            <el-input v-model="kw" size="small" placeholder="输入关键词，例如：违约金" clearable style="width:200px" />
            <el-button size="small" @click="doSearch">搜索</el-button>
          </div>
          <div v-if="hits.length" class="lc-hits">
            <div class="lc-hits-t">命中 {{ hits.length }} 处，请选择要修改的那一处：</div>
            <div v-for="(h, i) in hits" :key="i" class="lc-hit" @click="pickHit(h)">
              <span class="idx">#{{ i + 1 }}</span>
              <span class="snip">{{ h.snippet }}</span>
              <el-button size="small" text type="primary">选为修改位置</el-button>
            </div>
          </div>
          <div v-else-if="searched" class="lc-hint">未在合同正文中找到该关键词。</div>
          <div class="lc-hint">
            选中后会把该处的<b>逐字原文</b>作为修改对象提交给后端，由后端同一套定位逻辑建立锚点；
            若该段原文在合同中重复出现，后端会返回多个候选，需要你再次确认。
          </div>
        </el-tab-pane>

        <!-- 方式三：自然语言描述 -->
        <el-tab-pane label="描述位置" name="describe">
          <div class="lc-row">
            <el-input
              v-model="desc"
              size="small"
              type="textarea"
              :rows="2"
              placeholder="例如：合同第四部分验收条款中的第六项履约验收标准"
            />
          </div>
          <div class="lc-row">
            <el-button size="small" type="primary" :loading="ui.locateLoading" @click="runDescribe">只做定位（不修改合同）</el-button>
          </div>
          <div class="lc-hint">
            该操作调用只读定位接口：不调用 LLM、不写库、不生成任何修改记录。
            得到候选后需由你确认，确认后再进入修改。
          </div>
        </el-tab-pane>
      </el-tabs>

      <!-- 后端定位结果 -->
      <div v-if="ui.locateError" class="lc-err">{{ ui.locateError }}</div>

      <div v-if="ui.locate" class="lc-result">
        <div class="lc-result-head">
          <el-tag size="small" :type="ui.locate.found ? 'success' : 'warning'">
            {{ ui.locate.found ? '后端定位成功' : '后端未能唯一定位' }}
          </el-tag>
          <span class="muted">方式：{{ modeLabel(ui.locate.match_mode) }}</span>
          <span class="muted">{{ ui.locate.reason }}</span>
        </div>

        <div v-if="ui.locate.found" class="lc-cand">
          <div class="lc-cand-body">
            <div class="lc-kv"><span class="k">条款</span><span class="v">
              {{ ui.locate.clause_no ? `第${ws.cnNo(ui.locate.clause_no)}条` : '（无编号）' }}
              {{ ui.locate.clause_title || '' }}
            </span></div>
            <div class="lc-kv"><span class="k">原文</span><span class="v mono">{{ ui.locate.original_text }}</span></div>
          </div>
          <el-button size="small" type="primary" @click="ws.confirmLocate(s, ui.locate)">确认此位置</el-button>
        </div>

        <div v-if="ui.locate.candidates?.length" class="lc-cands">
          <div class="lc-cands-t">候选（{{ ui.locate.candidates.length }}）—— 必须由你确认：</div>
          <div v-for="(c, i) in ui.locate.candidates" :key="i" class="lc-cand">
            <div class="lc-cand-body">
              <div class="lc-kv"><span class="k">#{{ i + 1 }}</span><span class="v">
                {{ c.clause_no ? `第${ws.cnNo(c.clause_no)}条` : '（无编号）' }}
                {{ c.clause_title || '' }}
                <span class="muted">字符区间 [{{ c.start }}, {{ c.end }}]</span>
              </span></div>
              <div class="lc-kv"><span class="k">原文</span><span class="v mono">{{ c.original_text }}</span></div>
            </div>
            <el-button size="small" type="primary" plain @click="ws.confirmLocate(s, c)">确认</el-button>
          </div>
        </div>
      </div>

      <div class="lc-foot">
        定位不等于可导出：<b>真正的原文锚点只能由后端在你提交修改时建立</b>（写入
        <code>ClauseRevision.original_clause_text</code>）。本卡片只帮助你在提交前确认「改的是合同的哪一段」。
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'

const props = defineProps({ ws: { type: Object, required: true } })

const s = computed(() => props.ws.activeSession)
const ui = computed(() => (s.value ? props.ws.uiFor(s.value.key) : {}))
const badge = computed(() => props.ws.locateBadge(s.value))
const isOverview = computed(() => s.value?.key === '__overview__')
const applicable = computed(() => !!s.value && !isOverview.value && props.ws.sessionAction(s.value) !== 'add_clause')
const autoAnchor = computed(() => !!s.value?.risk?.clause_position?.original_text)
const headings = computed(() => props.ws.headings || [])

const mode = ref('search')
const anchorCn = ref('')
const kw = ref('')
const desc = ref('')
const searched = ref(false)
const hits = ref([])

watch(s, () => {
  // 切换会话时清空局部输入（定位结果本身挂在会话 UI 状态上，不在这里重复保存）
  kw.value = ''
  hits.value = []
  searched.value = false
  desc.value = ''
  anchorCn.value = ''
  mode.value = autoAnchor.value ? 'anchor' : 'search'
})

function modeLabel(m) {
  return { clause_anchor: '按条款编号', exact: '原文精确命中', prefix: '前缀匹配', keyword: '描述关键词' }[m] || '—'
}

/** 方式一：按条款编号 → 后端返回该条款逐字原文 */
async function runAnchor() {
  if (!anchorCn.value) {
    ElMessage.warning('请选择条款编号')
    return
  }
  await props.ws.runLocate(s.value, { clause_anchor: anchorCn.value })
}

/** 方式二：前端在正文里定位关键词，用户点选一处，再交给后端确认唯一性 */
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
    // 同一段窗口只保留一次（避免关键词反复命中同一句时出现重复项）
    if (!out.some((h) => h.start === win.start)) out.push({ ...win, snippet, keywordAt: p })
    i = p + q.length
    if (out.length >= 50) break
  }
  hits.value = out
  if (!out.length) ElMessage.info('未找到该关键词')
}

/**
 * 与后端 `_locate_at` 同规则截取「子条款窗口」：边界为（N）子项 / 句号 / 分号 / 换行，
 * 窗口过长（>150 字）时回退 ±60 字。
 * 这只是候选窗口的**前端镜像**；最终是否可定位由后端 /locate-clause 判定。
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

/** 用户点选命中 → 把该处逐字原文交给后端确认/建立候选定位 */
async function pickHit(h) {
  const res = await props.ws.runLocate(s.value, { text: h.snippet })
  if (res?.found) {
    props.ws.confirmLocate(s.value, res)
  } else if (res) {
    ElMessage.warning(res.reason || '该段原文未能唯一定位，请从下方候选中确认')
  }
}

/** 方式三：自然语言描述 → 只定位，不修改 */
async function runDescribe() {
  const t = String(desc.value || '').trim()
  if (!t) {
    ElMessage.warning('请输入位置描述')
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
.lc-ok, .lc-confirmed { border: 1px solid #BBF7D0; background: #F0FDF4; border-radius: 8px; padding: 8px 10px; }
.lc-confirmed { border-color: #BFDBFE; background: #EFF6FF; }
.lc-ok-t { font-size: 12.5px; font-weight: 600; color: #166534; margin-bottom: 6px; }
.lc-confirmed .lc-ok-t { color: #1D4ED8; }
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
.lc-hit { display: flex; align-items: center; gap: 6px; font-size: 12px; padding: 6px; border: 1px solid var(--a24-border); border-radius: 6px; margin-bottom: 4px; }
.lc-hit:hover { border-color: #C7D2E5; background: #F8FAFD; }
.lc-hit .idx { color: #8A93A6; flex-shrink: 0; }
.lc-hit .snip { flex: 1; color: #303133; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.lc-err { font-size: 12px; color: #B91C1C; background: #FEF2F2; border-radius: 6px; padding: 6px 8px; }
.lc-result { border: 1px solid var(--a24-border); border-radius: 8px; padding: 8px 10px; }
.lc-result-head { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-bottom: 6px; }
.lc-cands { margin-top: 8px; }
.lc-cands-t { font-size: 12px; color: #6B7280; margin-bottom: 4px; }
.lc-cand { display: flex; align-items: flex-start; gap: 8px; border: 1px solid var(--a24-border); border-radius: 6px; padding: 6px 8px; margin-bottom: 6px; }
.lc-cand-body { flex: 1; min-width: 0; }
.lc-foot { font-size: 11.5px; color: #8A93A6; line-height: 1.7; }
.muted { color: #9AA4B8; font-size: 11.5px; }
code { background: #F1F5F9; padding: 0 3px; border-radius: 3px; font-size: 11px; }
</style>
