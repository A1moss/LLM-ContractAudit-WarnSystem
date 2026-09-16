<template>
  <div class="otp">
    <!-- 左：条款导航 -->
    <aside class="otp-nav">
      <div class="otp-nav-head">
        <span>条款导航</span>
        <el-tag size="small" effect="plain">{{ headings.length }}</el-tag>
      </div>
      <div v-if="!headings.length" class="otp-nav-empty">未识别到「第X条 / X、」标题结构</div>
      <ul v-else class="otp-nav-list">
        <li
          v-for="h in headings"
          :key="h.num + '-' + h.start"
          :class="{ 'is-active': h.num === activeHeading }"
          @click="scrollToHeading(h)"
        >
          <span class="cn">第{{ h.cn }}条</span>
          <span class="ti">{{ h.title || '（无标题）' }}</span>
        </li>
      </ul>
    </aside>

    <!-- 右：工具条 + 正文 -->
    <section class="otp-main">
      <div class="otp-toolbar">
        <el-input
          v-model="ws.original.query"
          placeholder="在合同正文中搜索（回车定位下一个）"
          clearable
          size="small"
          class="otp-search"
          @keyup.enter="nextMatch"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-button size="small" :disabled="!ws.searchMatches.length" @click="prevMatch">上一个</el-button>
        <el-button size="small" :disabled="!ws.searchMatches.length" @click="nextMatch">下一个</el-button>
        <span class="otp-search-info">
          <template v-if="ws.original.query.trim()">
            {{ ws.searchMatches.length ? `${ws.original.activeMatch + 1} / ${ws.searchMatches.length}` : '无匹配' }}
          </template>
          <template v-else>—</template>
        </span>
        <span class="otp-spacer" />
        <el-checkbox v-model="ws.original.showRiskHighlight" size="small">风险标注</el-checkbox>
        <el-tag size="small" effect="plain" type="info">
          已定位 {{ ws.riskRanges.length }} / {{ ws.riskItems.length }} 条风险
        </el-tag>
      </div>

      <el-alert
        v-if="ws.riskItems.length && ws.riskRanges.length < ws.riskItems.length"
        type="info"
        :closable="false"
        show-icon
        class="otp-hint"
      >
        <template #title>
          有 {{ ws.riskItems.length - ws.riskRanges.length }} 条风险在审核阶段未建立可靠原文锚点，
          因此不在正文中标注位置（不伪造位置）。可在「风险详情」中查看，或进「修改合同」为它指定位置。
        </template>
      </el-alert>

      <div v-if="!ws.parsedText.trim()" class="otp-empty">
        <el-empty description="合同文本为空（解析失败或无文字层）">
          <el-button @click="downloadOriginal">下载原始文件查看</el-button>
        </el-empty>
      </div>

      <div v-else ref="bodyRef" class="otp-body">
        <div
          v-for="(line, li) in renderedLines"
          :key="li"
          :data-line="li"
          :class="['otp-line', { 'is-heading': line.isHeading }]"
        >
          <template v-if="!line.segments.length">{{ line.text === '' ? '\u00A0' : line.text }}</template>
          <template v-else>
            <template v-for="(seg, si) in line.segments" :key="si">
              <mark
                v-if="seg.searchIdx >= 0"
                :id="seg.searchIdx === ws.original.activeMatch ? 'otp-active-match' : null"
                :class="['otp-hit', { 'is-current': seg.searchIdx === ws.original.activeMatch }]"
              >{{ seg.text }}</mark>
              <span
                v-else-if="seg.level"
                :class="['otp-risk', 'lvl-' + seg.level]"
                :title="seg.riskTitle"
                @click="emit('go-risk', seg.riskId)"
              >{{ seg.text }}</span>
              <template v-else>{{ seg.text }}</template>
            </template>
          </template>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { riskName } from '../../constants/riskTypes.js'
import { getContractFile } from '../../api/contract.js'

const props = defineProps({ ws: { type: Object, required: true } })
const emit = defineEmits(['go-risk'])

const bodyRef = ref(null)
const activeHeading = ref(null)

/** 条款导航数据（前端从 parsed_text 派生，与后端 _parse_headings 同规则） */
const headings = computed(() => props.ws.headings || [])

/** 正文按行切分，并保留每行的全局字符偏移（用于区间裁剪与滚动定位） */
const lines = computed(() => {
  const text = props.ws.parsedText || ''
  const out = []
  let idx = 0
  for (const ln of text.split('\n')) {
    out.push({ start: idx, end: idx + ln.length, text: ln })
    idx += ln.length + 1
  }
  return out
})

const headingLineIndex = computed(() => {
  const m = new Map()
  for (const h of headings.value) {
    const i = lines.value.findIndex((l) => l.start <= h.start && h.start <= l.end)
    if (i >= 0 && !m.has(i)) m.set(i, h)
  }
  return m
})

/**
 * 渲染行：把「风险标注（仅可靠 start/end）」与「搜索命中」裁剪到行内，
 * 并在行内切分出互不重叠的片段。片段上的 class 决定高亮样式。
 */
const renderedLines = computed(() => {
  const text = props.ws.parsedText || ''
  const risks = props.ws.original.showRiskHighlight ? props.ws.riskRanges || [] : []
  const matches = (props.ws.searchMatches || []).slice(0, 300)
  const activeIdx = props.ws.original.activeMatch

  const marks = []
  for (const rr of risks) {
    marks.push({ start: rr.start, end: rr.end, level: rr.risk.risk_level, riskId: rr.risk.id, riskType: rr.risk.risk_type })
  }
  matches.forEach((m, i) => marks.push({ start: m.start, end: m.end, searchIdx: i }))

  return lines.value.map((line, li) => {
    const h = headingLineIndex.value.get(li)
    const inLine = marks.filter((m) => m.end > line.start && m.start < line.end && m.end > m.start)
    if (!inLine.length) return { text: line.text, segments: [], isHeading: !!h }

    const bounds = new Set([line.start, line.end])
    for (const m of inLine) {
      bounds.add(Math.max(line.start, m.start))
      bounds.add(Math.min(line.end, m.end))
    }
    const pts = [...bounds].sort((a, b) => a - b)
    const segments = []
    for (let i = 0; i < pts.length - 1; i++) {
      const s = pts[i]
      const e = pts[i + 1]
      if (e <= s) continue
      const cover = inLine.filter((m) => m.start <= s && m.end >= e)
      const search = cover.find((m) => m.searchIdx != null)
      const level = cover.find((m) => m.level)?.level || null
      const riskId = cover.find((m) => m.riskId != null)?.riskId ?? null
      const riskType = cover.find((m) => m.riskType)?.riskType || ''
      segments.push({
        text: text.slice(s, e),
        searchIdx: search ? search.searchIdx : -1,
        level,
        riskId,
        riskTitle: riskType ? `${riskType} · ${riskName(riskType)} · 点击查看` : '',
        active: search ? search.searchIdx === activeIdx : false,
      })
    }
    return { text: line.text, segments, isHeading: !!h }
  })
})

function scrollToHeading(h) {
  const i = [...headingLineIndex.value.entries()].find(([, v]) => v.num === h.num)?.[0]
  if (i == null) return
  activeHeading.value = h.num
  const el = bodyRef.value?.querySelector(`[data-line="${i}"]`)
  if (el) el.scrollIntoView({ block: 'start', behavior: 'smooth' })
}

watch(
  () => props.ws.original.activeMatch,
  async () => {
    await nextTick()
    document.getElementById('otp-active-match')?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  },
)
watch(
  () => props.ws.original.query,
  () => {
    props.ws.original.activeMatch = 0
  },
)

function nextMatch() {
  const n = props.ws.searchMatches.length
  if (!n) return
  props.ws.original.activeMatch = (props.ws.original.activeMatch + 1) % n
}
function prevMatch() {
  const n = props.ws.searchMatches.length
  if (!n) return
  props.ws.original.activeMatch = (props.ws.original.activeMatch - 1 + n) % n
}

async function downloadOriginal() {
  try {
    const buf = await getContractFile(props.ws.contractId)
    const blob = new Blob([buf], { type: 'application/octet-stream' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = props.ws.contract?.file_name || '合同原文'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '原始文件下载失败（可能已不在服务器上）')
  }
}
</script>

<style scoped>
.otp { display: flex; gap: 14px; height: 100%; min-height: 0; }
.otp-nav {
  flex: 0 0 232px; display: flex; flex-direction: column; min-height: 0;
  border-right: 1px solid var(--a24-border); padding-right: 10px;
}
.otp-nav-head {
  display: flex; align-items: center; justify-content: space-between;
  font-size: 12.5px; font-weight: 600; color: #6B7280; margin-bottom: 8px;
}
.otp-nav-empty { font-size: 12px; color: #9AA4B8; line-height: 1.7; }
.otp-nav-list { list-style: none; margin: 0; padding: 0; overflow-y: auto; min-height: 0; flex: 1; }
.otp-nav-list li {
  padding: 6px 8px; border-radius: 5px; cursor: pointer; font-size: 12.5px;
  display: flex; gap: 6px; align-items: baseline; border-left: 2px solid transparent;
}
.otp-nav-list li:hover { background: #F3F5F9; }
.otp-nav-list li.is-active { background: #E8EBF9; border-left-color: var(--a24-primary); }
.otp-nav-list .cn { color: var(--a24-primary); font-weight: 600; flex-shrink: 0; }
.otp-nav-list .ti { color: #6B7280; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.otp-main { flex: 1; min-width: 0; display: flex; flex-direction: column; min-height: 0; }
.otp-toolbar { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; flex-shrink: 0; }
.otp-search { max-width: 320px; }
.otp-search-info { font-size: 12px; color: #8A93A6; min-width: 62px; }
.otp-spacer { flex: 1; }
.otp-hint { margin-bottom: 8px; flex-shrink: 0; }
.otp-body {
  flex: 1; min-height: 0; overflow-y: auto; padding: 4px 2px 24px;
  font-family: "Noto Serif SC", "Source Han Serif SC", "Songti SC", "SimSun", serif;
  font-size: 13.5px; line-height: 1.85; color: #1F2937;
  word-break: break-word; white-space: pre-wrap;
}
.otp-line { min-height: 1.85em; }
.otp-line.is-heading { font-weight: 700; color: #131313; margin-top: 6px; }
.otp-hit { background: #FDE68A; color: inherit; padding: 0; border-radius: 2px; }
.otp-hit.is-current { background: #F59E0B; color: #fff; }
.otp-risk { cursor: pointer; border-radius: 2px; border-bottom: 1px solid transparent; }
.otp-risk.lvl-high { background: #FEE2E2; border-bottom-color: #DC2626; }
.otp-risk.lvl-medium { background: #FEF3C7; border-bottom-color: #D97706; }
.otp-risk.lvl-low { background: #DBEAFE; border-bottom-color: #2563EB; }
.otp-risk:hover { filter: brightness(0.94); }
.otp-empty { flex: 1; display: flex; align-items: center; justify-content: center; }
</style>
