<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

interface Method {
  id: string
  name: string
  strategy: string
  description?: string
  color: string
  config: Record<string, unknown>
  created_at: string
}

const props = defineProps<{ method: Method; engineLabel: string }>()
const emit = defineEmits<{ close: [] }>()
const dialog = ref<HTMLDialogElement | null>(null)
let returnFocus: HTMLElement | null = null

const labels: Record<string, string> = {
  target_chars: '目标块字符数', max_chars: '最大块字符数', min_chars: '最小块字符数',
  overlap: '重叠字符数', max_chunks: '最多知识块数', heading_levels: '参与切分的标题层级',
  merge_short_sections: '合并过短章节', prepend_heading_path: '嵌入文本携带标题路径',
  preserve_tables: '尽量保持表格完整', preserve_code_blocks: '尽量保持代码块完整',
  cache_enabled: '复用语义缓存', require_quality_gate: '发布索引前必须通过质量门',
  'deepseek.model': '语义模型', 'deepseek.api_key_env': '密钥环境变量名称',
  'deepseek.timeout_seconds': '单次请求超时（秒）', 'deepseek.max_attempts': '最多尝试次数',
  'deepseek.concurrent_requests': '请求并发参数', 'deepseek.json_output': 'JSON 格式输出',
  'batching.target_chars': '批次目标字符数', 'batching.max_chars': '批次最大字符数',
  'batching.target_atoms': '批次目标原子数', 'batching.max_atoms': '批次最大原子数',
  'batching.context_before_atoms': '前文上下文原子数', 'batching.context_after_atoms': '后文上下文原子数',
  'chunking.review_max_chars': '待核查区间最大字符数',
  'summary.enabled': '生成知识摘要', 'summary.max_chars': '摘要最大字符数',
}
const groupLabels: Record<string, string> = {
  deepseek: '语义模型', batching: '语义批次', chunking: '原子知识规则', summary: '知识摘要', cache: '缓存配置',
}

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function formatValue(key: string, value: unknown): string {
  if (typeof value === 'boolean') return value ? '开启' : '关闭'
  if (value === null) return '未设置'
  if (key === 'heading_levels' && Array.isArray(value)) return value.map((level) => `H${level}`).join('、') || '[]'
  if (Array.isArray(value) || isObject(value)) return JSON.stringify(value)
  if (value === '') return '空字符串'
  return String(value)
}

function rowsFor(values: Record<string, unknown>, prefix = ''): Array<{ key: string; label: string; value: string }> {
  return Object.entries(values).flatMap(([key, value]) => {
    const path = prefix ? `${prefix}.${key}` : key
    return isObject(value)
      ? rowsFor(value, path)
      : [{ key: path, label: labels[path] || path, value: formatValue(path, value) }]
  })
}

const groups = computed(() => {
  const common = Object.fromEntries(Object.entries(props.method.config).filter(([, value]) => !isObject(value)))
  const nested = Object.entries(props.method.config).filter(([, value]) => isObject(value)).map(([key, value]) => ({
    key, title: groupLabels[key] || key, rows: rowsFor(value as Record<string, unknown>, key),
  }))
  return [...(Object.keys(common).length ? [{ key: 'common', title: '通用规则', rows: rowsFor(common) }] : []), ...nested]
})
const rawConfig = computed(() => JSON.stringify(props.method.config, null, 2))
const createdAt = computed(() => {
  const date = new Date(props.method.created_at)
  return Number.isNaN(date.getTime()) ? props.method.created_at : date.toLocaleString('zh-CN', { hour12: false })
})

function handleBackdrop(event: MouseEvent): void {
  const rect = dialog.value?.getBoundingClientRect()
  if (rect && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) emit('close')
}

onMounted(() => {
  returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
  dialog.value?.showModal()
})
onBeforeUnmount(() => {
  dialog.value?.close()
  if (returnFocus?.isConnected) returnFocus.focus()
})
</script>

<template>
  <dialog ref="dialog" class="entry-dialog method-detail-dialog" aria-labelledby="method-detail-title" aria-describedby="method-detail-description" @cancel.prevent="emit('close')" @click="handleBackdrop">
    <div class="dialog-heading">
      <div><p class="eyebrow">Chunking method</p><h2 id="method-detail-title"><span class="method-detail-swatch" :style="{ background: method.color }" aria-hidden="true"></span>{{ method.name }}</h2><p id="method-detail-description">{{ engineLabel }} · 已保存的分块方法配置</p></div>
      <button class="icon-button" type="button" aria-label="关闭分块方法详情" autofocus @click="emit('close')">×</button>
    </div>
    <dl class="method-detail-basics">
      <div><dt>引擎标识</dt><dd><code>{{ method.strategy }}</code></dd></div>
      <div><dt>创建时间</dt><dd>{{ createdAt }}</dd></div>
      <div class="method-detail-full"><dt>方法 ID</dt><dd><code>{{ method.id }}</code></dd></div>
      <div class="method-detail-full"><dt>使用说明</dt><dd class="method-detail-description">{{ method.description || '暂无使用说明。' }}</dd></div>
    </dl>
    <section v-for="group in groups" :key="group.key" class="method-parameter-group" :aria-label="group.title">
      <h3>{{ group.title }}</h3>
      <dl><div v-for="row in group.rows" :key="row.key"><dt>{{ row.label }}<code>{{ row.key }}</code></dt><dd>{{ row.value }}</dd></div></dl>
      <p v-if="!group.rows.length" class="method-no-config">未保存该组参数。</p>
    </section>
    <p v-if="!groups.length" class="method-no-config">未保存额外参数，执行时使用引擎默认值。</p>
    <details class="method-config-json"><summary>查看原始配置 JSON</summary><pre tabindex="0" aria-label="原始配置 JSON">{{ rawConfig }}</pre></details>
    <div class="method-detail-footer"><button class="soft-button" type="button" @click="emit('close')">关闭详情</button></div>
  </dialog>
</template>
