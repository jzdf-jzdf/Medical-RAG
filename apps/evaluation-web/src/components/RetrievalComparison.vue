<script setup lang="ts">
import { computed } from 'vue'
import type { RetrievalLayerScore } from '../types'

const props = defineProps<{ evaluation?: unknown }>()
const layer = computed(() => props.evaluation && typeof props.evaluation === 'object'
  ? props.evaluation as RetrievalLayerScore : null)
const textMode = computed(() => layer.value?.method === 'reference_text_v1')
const matches = computed(() => layer.value?.matches ?? [])

function percent(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? `${Math.round(value * 100)}%` : '—'
}
</script>

<template>
  <section v-if="layer" class="retrieval-comparison" aria-label="检索评分">
    <div class="retrieval-heading">
      <h3>{{ textMode ? '参考分块文本匹配' : layer.method ? '检索文档匹配' : '检索评分' }}</h3>
      <b>{{ percent(layer.score) }}</b>
    </div>
    <template v-if="textMode">
      <p class="retrieval-help">每个参考分块与前 {{ layer.metrics?.k ?? 5 }} 条检索结果比较，取最佳文本覆盖率后求平均。</p>
      <div class="retrieval-metrics">
        <span>参考分块 {{ layer.metrics?.reference_count ?? 0 }}</span>
        <span>检索分块 {{ layer.metrics?.retrieved_count ?? 0 }}</span>
        <span>平均精确率 {{ percent(layer.metrics?.text_precision) }}</span>
        <span>平均 F1 {{ percent(layer.metrics?.text_f1) }}</span>
      </div>
      <p v-if="layer.source === 'citations'" class="retrieval-help">本次只返回了最终引用，文本匹配以这些引用内容为范围。</p>
      <details v-if="matches.length">
        <summary>查看 {{ matches.length }} 个参考分块的匹配详情</summary>
        <ol class="retrieval-matches">
          <li v-for="match in matches" :key="match.reference_index">
            <header><b>参考分块 {{ match.reference_index }}</b><span>文本覆盖率 {{ percent(match.recall) }}</span></header>
            <div class="retrieval-pair">
              <div><h4>参考原文</h4><p>{{ match.reference_text }}</p></div>
              <div><h4>{{ match.retrieved_rank == null ? '未匹配到检索分块' : `匹配检索结果 #${match.retrieved_rank}` }}<span v-if="match.title"> · {{ match.title }}</span></h4><p>{{ match.retrieved_text || '没有可用于对比的匹配文本。' }}</p></div>
            </div>
            <p class="retrieval-help">精确率 {{ percent(match.precision) }} · F1 {{ percent(match.f1) }}</p>
          </li>
        </ol>
      </details>
    </template>
    <p v-else-if="!layer.method" class="retrieval-help">该历史记录未计算参考分块文本匹配，重新测试后可查看匹配详情。</p>
    <p v-else-if="layer.score == null" class="retrieval-help">题目未提供可用的参考分块或文档标注，检索评分未计算。</p>
    <p v-else class="retrieval-help">本题按参考文档 ID 计算检索命中、召回和排序得分。</p>
  </section>
</template>

<style scoped>
.retrieval-comparison { min-width: 0; margin: 0 18px 18px 66px; padding: 16px; border: 1px solid var(--line); border-radius: 8px; background: #f7faf8; }
.retrieval-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; color: var(--teal); }
.retrieval-heading h3 { margin: 0; font-size: 13px; }
.retrieval-heading > b { font-size: 20px; }
.retrieval-help { margin: 8px 0 0; color: var(--muted); font-size: 11px; line-height: 1.7; }
.retrieval-metrics { margin-top: 10px; display: flex; flex-wrap: wrap; gap: 8px 16px; color: var(--ink); font-size: 11px; }
.retrieval-comparison details { margin-top: 12px; }
.retrieval-comparison summary { color: var(--teal); font-size: 12px; cursor: pointer; }
.retrieval-matches { margin: 14px 0 0; padding: 0; display: grid; gap: 16px; list-style: none; }
.retrieval-matches li { min-width: 0; padding-top: 12px; border-top: 1px solid var(--line); }
.retrieval-matches header { display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap; color: var(--ink); font-size: 12px; }
.retrieval-pair { margin-top: 10px; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.retrieval-pair > div { min-width: 0; padding: 12px; background: #fff; border: 1px solid var(--line); border-radius: 6px; overflow-wrap: anywhere; }
.retrieval-pair h4 { margin: 0 0 8px; color: var(--teal); font-size: 11px; }
.retrieval-pair p { margin: 0; color: var(--ink); font-size: 13px; line-height: 1.8; white-space: pre-wrap; }
@media (max-width: 820px) { .retrieval-pair { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 600px) { .retrieval-comparison { margin: 0 10px 14px 44px; padding: 12px; } }
</style>
