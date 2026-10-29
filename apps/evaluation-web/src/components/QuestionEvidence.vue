<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  evidence: Record<string, unknown>[]
  metadata: Record<string, unknown>
}>()

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function documentName(value: unknown): string {
  return Array.isArray(value) ? value.map(text).filter(Boolean).join('、') : text(value)
}

const importFile = computed(() => text(props.metadata.import_file))
const level = computed(() => text(props.metadata.level || props.metadata.difficulty))
const references = computed(() => props.evidence.map((item) => ({
  content: text(item.quote) || text(item.context) || text(item.text) || text(item.content),
  document: documentName(item.document_title) || documentName(item.evidence_doc)
    || documentName(props.metadata.evidence_doc),
  heading: text(item.heading),
  chunkIndex: typeof item.chunk_index === 'number' && Number.isInteger(item.chunk_index)
    && item.chunk_index >= 0 ? item.chunk_index + 1 : null,
})).filter((item) => item.content))
</script>

<template>
  <details v-if="references.length" class="reference-evidence" open>
    <summary>
      <span>{{ importFile ? '测试集参考分块' : '参考证据分块' }}</span>
      <small>{{ references.length }} 个分块<span v-if="level"> · {{ level }}</span></small>
    </summary>
    <p v-if="importFile" class="reference-source">测试集：{{ importFile }}</p>
    <ol class="reference-list">
      <li v-for="(item, index) in references" :key="index" class="reference-chunk">
        <div class="reference-chunk-heading">
          <b>{{ item.chunkIndex == null ? `参考分块 ${index + 1}` : `分块 #${item.chunkIndex}` }}</b>
          <span>{{ item.document || '原题未标注来源文档' }}</span>
        </div>
        <p v-if="item.heading" class="reference-heading">{{ item.heading }}</p>
        <blockquote>{{ item.content }}</blockquote>
      </li>
    </ol>
  </details>
  <p v-else class="reference-empty">原题未提供参考分块。</p>
</template>

<style scoped>
.reference-evidence { min-width: 0; margin-top: 18px; border-top: 1px solid var(--line); }
.reference-evidence summary { padding: 14px 0; color: var(--ink); cursor: pointer; font-size: 13px; font-weight: 600; }
.reference-evidence summary small { margin-left: 12px; color: var(--muted); font-size: 11px; font-weight: 400; }
.reference-source { margin: 0 0 12px; color: var(--muted); font-size: 11px; overflow-wrap: anywhere; }
.reference-list { margin: 0; padding: 0; display: grid; gap: 12px; list-style: none; }
.reference-chunk { min-width: 0; padding: 14px 16px; border: 1px solid var(--line); border-radius: 8px; background: #f7faf8; }
.reference-chunk-heading { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; color: var(--teal); font-size: 11px; overflow-wrap: anywhere; }
.reference-chunk-heading span { color: var(--muted); }
.reference-heading { margin: 8px 0 0; color: var(--muted); font-size: 11px; }
.reference-chunk blockquote { margin: 10px 0 0; color: var(--ink); font-size: 13px; line-height: 1.85; white-space: pre-wrap; overflow-wrap: anywhere; }
.reference-empty { margin: 14px 0 0; color: var(--muted); font-size: 12px; }
@media (max-width: 600px) {
  .reference-chunk { padding: 12px; }
}
</style>
