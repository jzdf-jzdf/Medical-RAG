<script setup lang="ts">
defineProps<{ metadata?: Record<string, unknown> }>()
const artifacts = [
  { name: 'chunks.jsonl', label: '知识块' }, { name: 'manifest.json', label: '运行清单' },
  { name: 'processing_report.json', label: '质量报告' }, { name: 'audit.jsonl', label: '切块审计' },
]
</script>

<template>
  <div v-if="metadata?.run_id" class="chunk-run-artifacts">
    <strong>{{ metadata.quality_gate_passed ? '质量门已通过' : '质量门未通过，请检查报告' }}</strong>
    <p v-if="!metadata.quality_gate_passed">默认构建策略会阻止未通过质量门的结果发布为向量索引。</p>
    <nav aria-label="指南切块产物下载"><a v-for="artifact in artifacts" :key="artifact.name" :href="`/api/knowledge/api/v1/guideline-chunking/runs/${metadata.run_id}/artifacts/${artifact.name}`" download>{{ artifact.label }} ↓</a></nav>
  </div>
</template>
