<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ metadata?: Record<string, unknown> }>()
const tags = computed(() => Array.isArray(props.metadata?.topic_tags) ? props.metadata.topic_tags as string[] : [])
const levels = computed(() => Array.isArray(props.metadata?.recommendation_levels) ? props.metadata.recommendation_levels as string[] : [])
</script>

<template>
  <div v-if="metadata?.type" class="chunk-semantic-meta">
    <span class="tag">{{ metadata.type === 'recommendation' ? 'REC · 推荐意见' : 'FACT · 事实知识' }}</span>
    <span v-if="metadata.chunk_id" class="chunk-source-id">{{ metadata.chunk_id }}</span>
    <p v-if="metadata.summary" class="chunk-summary">{{ metadata.summary }}</p>
    <div v-if="tags.length" class="chunk-topic-tags"><span v-for="tag in tags" :key="tag">{{ tag }}</span></div>
    <p v-if="levels.length" class="chunk-recommendation-level">推荐等级：{{ levels.join('；') }}</p>
  </div>
</template>
