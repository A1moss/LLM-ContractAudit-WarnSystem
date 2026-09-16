<template>
  <div class="cdv" :class="{ 'cdv-3': !!reference }">
    <div class="cdv-col">
      <div class="cdv-t">
        <span>{{ beforeLabel }}</span>
        <el-tag v-if="!before" size="small" type="info" effect="plain">暂无原文</el-tag>
      </div>
      <pre class="cdv-b cdv-before">{{ before || '—' }}</pre>
    </div>

    <div v-if="reference" class="cdv-col">
      <div class="cdv-t"><span>标准 / 参考条款</span></div>
      <pre class="cdv-b cdv-ref">{{ reference }}</pre>
    </div>

    <div class="cdv-col">
      <div class="cdv-t">
        <span>{{ afterLabel }}</span>
        <el-tag v-if="!after" size="small" type="info" effect="plain">暂无修改稿</el-tag>
      </div>
      <pre class="cdv-b cdv-after">{{ after || '—' }}</pre>
    </div>
  </div>
</template>

<script setup>
/**
 * ClauseDiffView — 原文 ↔ 修改后 对比块（右栏主体）。
 *
 * 只消费**后端真实返回**的文本，不做任何前端生成/补全：
 *   - `reference` 只有拿到真实的标准/参考条款正文时才传入；
 *     拿不到就**不渲染中间栏**（比对区退化为两栏，V2.2 3.4）。
 *   - 空文本如实显示"暂无"，不伪造占位内容。
 */
defineProps({
  before: { type: String, default: '' },
  after: { type: String, default: '' },
  reference: { type: String, default: '' },
  beforeLabel: { type: String, default: '合同原文' },
  afterLabel: { type: String, default: '修改后' },
})
</script>

<style scoped>
.cdv { display: flex; gap: 8px; align-items: stretch; }
.cdv-col { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.cdv-t {
  display: flex; align-items: center; gap: 6px;
  font-size: 11.5px; color: #8A93A6; margin-bottom: 4px;
}
.cdv-b {
  flex: 1; margin: 0; padding: 8px 10px; border-radius: 6px;
  font-family: "Noto Serif SC", "Songti SC", serif;
  font-size: 12.5px; line-height: 1.85; color: #1F2937;
  white-space: pre-wrap; word-break: break-word;
  max-height: 300px; overflow: auto;
}
.cdv-before { background: #FEF2F2; border: 1px solid #FECACA; text-decoration: line-through; text-decoration-color: #F87171; }
.cdv-ref { background: #F8FAFD; border: 1px solid var(--a24-border); }
.cdv-after { background: #F0FDF4; border: 1px solid #BBF7D0; }
@media (max-width: 1200px) {
  .cdv { flex-direction: column; }
}
</style>
