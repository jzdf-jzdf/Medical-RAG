<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import DOMPurify from 'dompurify'
import { parseAnswerMarkdown } from './markdown'
import { PlatformSidebar } from '@medical-rag/platform-ui'

import {
  createModel,
  createConversation,
  deleteConversation,
  deleteModel,
  getConversation,
  listKnowledgeBases,
  listConversations,
  listModels,
  sendChat,
  updateModel,
  type ChatResult,
  type Citation,
  type ConversationSummary,
  type KnowledgeBaseOption,
  type ModelConfig,
  type ModelConfigInput,
  type SemanticClaim,
  type ToolCall,
} from './api'

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  createdAt: string
  result?: ChatResult
}

interface ModelForm extends ModelConfigInput {
  api_key: string
}

interface RetrievedChunk extends Citation {
  callIndex: number
}

interface TextSegment {
  text: string
  highlighted: boolean
}

const userId = getUserId()
const conversations = ref<ConversationSummary[]>([])
const activeId = ref<string | null>(null)
const messages = ref<ChatMessage[]>([])
const prompt = ref('')
const loading = ref(false)
const loadingConversation = ref(false)
const deletingConversationId = ref<string | null>(null)
const error = ref('')
const sidebarOpen = ref(false)
const inspectorOpen = ref(false)
const modelSettingsOpen = ref(false)
const selectedResult = ref<ChatResult | null>(null)
const messageList = ref<HTMLElement | null>(null)
const models = ref<ModelConfig[]>([])
const knowledgeBases = ref<KnowledgeBaseOption[]>([])
const selectedKnowledgeBaseId = ref(localStorage.getItem('medical-agent-knowledge-base-id') || '')
const knowledgeBaseError = ref('')
const selectedModelId = ref(localStorage.getItem('medical-agent-model-id') || '')
const editingModelId = ref<string | null>(null)
const modelFormOpen = ref(false)
const modelSaving = ref(false)
const modelError = ref('')
const modelForm = ref<ModelForm>(newModelForm())

const activeConversation = computed(() =>
  conversations.value.find((item) => item.id === activeId.value),
)
const toolCalls = computed<ToolCall[]>(() => selectedResult.value?.tool_calls ?? [])
const evidenceCount = computed(() => selectedResult.value?.citations.length ?? 0)
const citedChunks = computed<Citation[]>(() => selectedResult.value?.citations ?? [])
const citedChunkIds = computed(
  () => new Set(citedChunks.value.map((item) => item.chunk_id).filter(Boolean)),
)
const retrievedChunks = computed<RetrievedChunk[]>(() => {
  const chunks: RetrievedChunk[] = []
  toolCalls.value.forEach((call, callIndex) => {
    if (!['search_medical_knowledge', 'hybrid_search'].includes(call.tool)) return
    const payload = unwrapToolResult(call.result)
    const items = payload && Array.isArray(payload.items) ? payload.items : []
    items.forEach((value) => {
      if (!isRecord(value)) return
      chunks.push({
        callIndex: callIndex + 1,
        document_id: stringValue(value.document_id),
        chunk_id: stringValue(value.chunk_id),
        index_version: stringValue(value.index_version),
        title: stringValue(value.document_title) || stringValue(value.title),
        heading: Array.isArray(value.heading_path)
          ? value.heading_path.map(String).join(' › ')
          : stringValue(value.heading),
        page_start: numberValue(value.page_start),
        page_end: numberValue(value.page_end),
        text: stringValue(value.content) || stringValue(value.text),
        score: numberValue(value.score),
        rank: numberValue(value.rank),
      })
    })
  })
  return chunks
})
const activeModel = computed(() =>
  models.value.find((item) => item.id === selectedModelId.value),
)
const activeModelLabel = computed(() => activeModel.value?.name || '环境默认模型')

onMounted(async () => {
  await Promise.all([loadConversations(), loadModels(), loadKnowledgeBases()])
})

async function loadConversations() {
  try {
    conversations.value = await listConversations(userId)
    if (conversations.value.length) {
      await selectConversation(conversations.value[0].id)
    }
  } catch (cause) {
    showError(cause)
  }
}

async function loadModels() {
  try {
    models.value = await listModels()
    const selectedExists = models.value.some((item) => item.id === selectedModelId.value)
    if (!selectedExists) {
      selectedModelId.value = models.value.find((item) => item.is_default)?.id || ''
    }
    persistSelectedModel()
  } catch (cause) {
    showError(cause)
  }
}

async function loadKnowledgeBases() {
  try {
    knowledgeBases.value = await listKnowledgeBases()
    if (!knowledgeBases.value.some((item) => item.id === selectedKnowledgeBaseId.value)) {
      chooseKnowledgeBase('')
    }
  } catch {
    knowledgeBaseError.value = '知识库列表暂不可用，将使用服务端默认设置。'
    chooseKnowledgeBase('')
  }
}

function chooseKnowledgeBase(id: string) {
  selectedKnowledgeBaseId.value = id
  localStorage.setItem('medical-agent-knowledge-base-id', id)
}

async function selectConversation(id: string) {
  if (loading.value || loadingConversation.value) return
  loadingConversation.value = true
  error.value = ''
  try {
    const conversation = await getConversation(id, userId)
    activeId.value = id
    messages.value = conversation.messages.map((item) => ({
      id: item.id,
      role: item.role,
      content: item.content,
      createdAt: item.created_at,
      result: item.result ?? undefined,
    }))
    selectedResult.value = [...messages.value]
      .reverse()
      .find((item) => item.result)?.result ?? null
    inspectorOpen.value = false
    sidebarOpen.value = false
    await scrollToBottom()
  } catch (cause) {
    showError(cause)
  } finally {
    loadingConversation.value = false
  }
}

function startNewConversation() {
  activeId.value = null
  messages.value = []
  prompt.value = ''
  selectedResult.value = null
  sidebarOpen.value = false
  inspectorOpen.value = false
  error.value = ''
}

async function removeConversation(item: ConversationSummary) {
  if (loading.value || deletingConversationId.value) return
  if (!window.confirm(`删除会话“${item.title}”？此操作无法撤销。`)) return

  deletingConversationId.value = item.id
  error.value = ''
  try {
    await deleteConversation(item.id, userId)
    const wasActive = activeId.value === item.id
    conversations.value = conversations.value.filter((conversation) => conversation.id !== item.id)
    if (wasActive) {
      startNewConversation()
      const nextConversation = conversations.value[0]
      if (nextConversation) await selectConversation(nextConversation.id)
    }
  } catch (cause) {
    showError(cause)
  } finally {
    deletingConversationId.value = null
  }
}

async function submit() {
  const content = prompt.value.trim()
  if (!content || loading.value) return

  loading.value = true
  error.value = ''
  prompt.value = ''
  const optimisticId = `local-${Date.now()}`
  messages.value.push({
    id: optimisticId,
    role: 'user',
    content,
    createdAt: new Date().toISOString(),
  })
  await scrollToBottom()

  try {
    let conversationId = activeId.value
    if (!conversationId) {
      const created = await createConversation(userId, makeTitle(content))
      conversations.value.unshift(created)
      activeId.value = created.id
      conversationId = created.id
    }

    const result = await sendChat(
      conversationId,
      userId,
      content,
      selectedModelId.value || undefined,
      selectedKnowledgeBaseId.value || undefined,
    )
    messages.value.push({
      id: result.message_id,
      role: 'assistant',
      content: result.content.trim() || '本次未生成有效回答，请重新发送问题。',
      createdAt: new Date().toISOString(),
      result,
    })
    selectedResult.value = result
    if (window.innerWidth >= 1180) inspectorOpen.value = true
    const current = conversations.value.find((item) => item.id === conversationId)
    if (current) current.updated_at = new Date().toISOString()
  } catch (cause) {
    const detail = cause instanceof Error ? cause.message : '连接服务失败'
    messages.value.push({
      id: `local-error-${Date.now()}`,
      role: 'assistant',
      content: `本次请求未能完成：${detail}。请稍后重新发送问题。`,
      createdAt: new Date().toISOString(),
    })
    prompt.value = content
    showError(cause)
  } finally {
    loading.value = false
    await scrollToBottom()
  }
}

function openInspector(result?: ChatResult) {
  if (!result) return
  selectedResult.value = result
  inspectorOpen.value = true
}

function openModelSettings() {
  inspectorOpen.value = false
  modelSettingsOpen.value = true
  modelFormOpen.value = false
  modelError.value = ''
}

function chooseModel(id: string) {
  selectedModelId.value = id
  persistSelectedModel()
}

function beginCreateModel() {
  editingModelId.value = null
  modelForm.value = newModelForm()
  modelFormOpen.value = true
  modelError.value = ''
}

function beginEditModel(item: ModelConfig) {
  editingModelId.value = item.id
  modelForm.value = {
    name: item.name,
    provider: item.provider,
    model: item.model,
    base_url: item.base_url,
    api_key: '',
    max_tokens: item.max_tokens,
    temperature: item.temperature,
    is_default: item.is_default,
  }
  modelFormOpen.value = true
  modelError.value = ''
}

async function saveModel() {
  if (modelSaving.value) return
  modelSaving.value = true
  modelError.value = ''
  try {
    const payload: Partial<ModelConfigInput> = { ...modelForm.value }
    if (editingModelId.value && !modelForm.value.api_key) delete payload.api_key
    const saved = editingModelId.value
      ? await updateModel(editingModelId.value, payload)
      : await createModel(modelForm.value)
    await loadModels()
    chooseModel(saved.id)
    modelFormOpen.value = false
  } catch (cause) {
    modelError.value = cause instanceof Error ? cause.message : '模型配置保存失败'
  } finally {
    modelSaving.value = false
  }
}

async function removeModel(item: ModelConfig) {
  if (!window.confirm(`停用模型“${item.name}”？`)) return
  modelError.value = ''
  try {
    await deleteModel(item.id)
    await loadModels()
  } catch (cause) {
    modelError.value = cause instanceof Error ? cause.message : '模型配置删除失败'
  }
}

function onComposerKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    void submit()
  }
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

function formatValue(value: unknown) {
  return JSON.stringify(value, null, 2) ?? String(value)
}

function displayedMessageContent(content: string) {
  return content
    .split('\n')
    .filter((line) => !line.includes('引用核验未通过'))
    .join('\n')
    .trim()
}

function renderMarkdown(content: string) {
  const source = (displayedMessageContent(content) || '本次未生成有效回答，请重新发送问题。')
    .replace(/\\([*_])/g, '$1')
    .replace(/^\s*\*\*(#{1,6}\s+)/gm, '$1')
    .replace(/^(#{1,6}\s+.+?) {2,}(?=\*\*)/gm, '$1\n\n')
    .replace(/ {2,}(?=(?:[-*+]\s|\d+[.)、]\s))/g, '\n')
  const html = DOMPurify.sanitize(parseAnswerMarkdown(source))
  return renderCitationSuperscripts(html)
}

function renderCitationSuperscripts(html: string) {
  const template = document.createElement('template')
  template.innerHTML = html
  const walker = document.createTreeWalker(template.content, NodeFilter.SHOW_TEXT)
  const textNodes: Text[] = []
  while (walker.nextNode()) textNodes.push(walker.currentNode as Text)

  textNodes.forEach((node) => {
    if (node.parentElement?.closest('a, code, pre, sup')) return
    const value = node.textContent ?? ''
    const pattern = /\[(\d+)\]/g
    const fragment = document.createDocumentFragment()
    let cursor = 0
    let match: RegExpExecArray | null
    while ((match = pattern.exec(value))) {
      fragment.append(document.createTextNode(value.slice(cursor, match.index)))
      const marker = document.createElement('sup')
      marker.className = 'citation-marker'
      marker.textContent = match[0]
      fragment.append(marker)
      cursor = match.index + match[0].length
    }
    if (cursor === 0) return
    fragment.append(document.createTextNode(value.slice(cursor)))
    node.replaceWith(fragment)
  })
  return template.innerHTML
}

function hasVerificationFailure(result: ChatResult) {
  return result.citations.length > 0 && result.verification?.passed === false
}

function verificationIssueText(result: ChatResult) {
  const labels: Record<string, string> = {
    missing_sources: '未获得有效来源',
    numeric_mismatch: '数字与引用证据不一致',
    uncited_claim: '存在未标注来源的陈述',
    invalid_source: '引用编号无效',
    semantic_mismatch: '陈述与引用原文不完全对应',
    semantic_verification_failed: '语义核验暂不可用',
    generation_failed: '回答生成失败，请重试',
  }
  return (result.verification?.issues ?? []).map((item) => labels[item] || item).join('·')
}

function semanticClaims(result: ChatResult) {
  return result.verification?.claims ?? []
}

function claimsForCitation(citationId: number): SemanticClaim[] {
  if (!selectedResult.value) return []
  return semanticClaims(selectedResult.value).filter((claim) =>
    claim.citation_ids.includes(citationId),
  )
}

function highlightedSegments(text: string, citationId: number): TextSegment[] {
  if (!selectedResult.value || !text) return [{ text, highlighted: false }]
  const quotes = semanticClaims(selectedResult.value)
    .flatMap((claim) => claim.evidence)
    .filter((item) => item.citation_id === citationId && item.quote)
    .map((item) => item.quote)
  const ranges: Array<[number, number]> = []
  quotes.forEach((quote) => {
    let offset = 0
    while (offset < text.length) {
      const start = text.indexOf(quote, offset)
      if (start < 0) break
      ranges.push([start, start + quote.length])
      offset = start + quote.length
    }
  })
  if (!ranges.length) return [{ text, highlighted: false }]
  ranges.sort((left, right) => left[0] - right[0])
  const merged: Array<[number, number]> = []
  ranges.forEach(([start, end]) => {
    const previous = merged[merged.length - 1]
    if (previous && start <= previous[1]) previous[1] = Math.max(previous[1], end)
    else merged.push([start, end])
  })
  const segments: TextSegment[] = []
  let cursor = 0
  merged.forEach(([start, end]) => {
    if (start > cursor) segments.push({ text: text.slice(cursor, start), highlighted: false })
    segments.push({ text: text.slice(start, end), highlighted: true })
    cursor = end
  })
  if (cursor < text.length) segments.push({ text: text.slice(cursor), highlighted: false })
  return segments
}

function runStatus(result: ChatResult) {
  if (result.status === 'failed') return '回答生成失败'
  if (result.verification?.issues?.includes('semantic_verification_failed')) return '核验暂不可用'
  if (hasVerificationFailure(result)) return '引用核验未通过'
  if (result.degraded_without_evidence) return '证据不足'
  return '已完成'
}

function unwrapToolResult(value: unknown): Record<string, unknown> | null {
  if (!isRecord(value)) return null
  if (isRecord(value.content) && Array.isArray(value.content.items)) return value.content
  return value
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function stringValue(value: unknown) {
  return typeof value === 'string' ? value : ''
}

function numberValue(value: unknown) {
  return typeof value === 'number' ? value : null
}

function pageLabel(item: Citation) {
  if (item.page_start == null && item.page_end == null) return '页码未标注'
  return item.page_start === item.page_end || item.page_end == null
    ? `P.${item.page_start}`
    : `P.${item.page_start}–${item.page_end}`
}

function makeTitle(content: string) {
  return content.length > 28 ? `${content.slice(0, 28)}…` : content
}

function newModelForm(): ModelForm {
  return {
    name: 'DeepSeek Chat',
    provider: 'deepseek-official',
    model: 'deepseek-chat',
    base_url: 'https://api.deepseek.com',
    api_key: '',
    max_tokens: 4096,
    temperature: 0.2,
    is_default: models.value.length === 0,
  }
}

function persistSelectedModel() {
  if (selectedModelId.value) {
    localStorage.setItem('medical-agent-model-id', selectedModelId.value)
  } else {
    localStorage.removeItem('medical-agent-model-id')
  }
}

function showError(cause: unknown) {
  error.value = cause instanceof Error ? cause.message : '服务暂时不可用，请稍后重试。'
}

function getUserId() {
  return 'web-shared'
}

async function scrollToBottom() {
  await nextTick()
  messageList.value?.scrollTo({ top: messageList.value.scrollHeight, behavior: 'smooth' })
}
</script>

<template>
  <div class="shell" :class="{ 'has-inspector': inspectorOpen }">
    <button
      v-if="sidebarOpen || inspectorOpen || modelSettingsOpen"
      class="backdrop"
      :class="{ 'model-open': modelSettingsOpen }"
      aria-label="关闭面板"
      @click="sidebarOpen = false; inspectorOpen = false; modelSettingsOpen = false"
    />

    <PlatformSidebar class="sidebar" :class="{ open: sidebarOpen }" active-app="agent" aria-label="智能问答导航">
      <button class="new-chat" type="button" @click="startNewConversation">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
        新建对话
      </button>

      <nav class="conversation-nav" aria-label="会话历史">
        <div class="nav-heading"><span>近期会话</span><b>{{ conversations.length }}</b></div>
        <p v-if="!conversations.length" class="nav-empty">发送第一条问题后，会话将保存在这里。</p>
        <div
          v-for="conversation in conversations"
          :key="conversation.id"
          class="conversation-row"
          :class="{ active: activeId === conversation.id }"
        >
          <button class="conversation-link" type="button" @click="selectConversation(conversation.id)">
            <span>{{ conversation.title }}</span>
            <time>{{ formatTime(conversation.updated_at) }}</time>
          </button>
          <button
            class="conversation-delete"
            type="button"
            :disabled="deletingConversationId === conversation.id"
            :aria-label="`删除会话：${conversation.title}`"
            title="删除会话"
            @click="removeConversation(conversation)"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 7h14M9 7V4h6v3m2 0-1 13H8L7 7m4 4v5m2-5v5" /></svg>
          </button>
        </div>
      </nav>

    </PlatformSidebar>

    <main class="workspace">
      <header class="topbar">
        <button class="icon-button mobile-only" type="button" aria-label="打开会话列表" @click="sidebarOpen = true">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h10" /></svg>
        </button>
        <div class="context">
          <span>当前会话</span>
          <strong>{{ activeConversation?.title || '新对话' }}</strong>
        </div>
        <div class="topbar-actions">
          <button class="model-label" type="button" aria-label="配置模型" @click="openModelSettings">
            <i /> {{ activeModelLabel }}
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 10 5 5 5-5" /></svg>
          </button>
          <button
            class="trace-button"
            type="button"
            :disabled="!selectedResult"
            @click="inspectorOpen = !inspectorOpen"
          >
            运行详情
            <span v-if="selectedResult">{{ toolCalls.length }}</span>
          </button>
        </div>
      </header>

      <section ref="messageList" class="message-list" aria-live="polite">
        <div v-if="loadingConversation" class="loading-history">正在读取会话…</div>

        <div v-else-if="!messages.length" class="welcome">
          <p class="eyebrow">Evidence first · Clinical reasoning</p>
          <h1>从一个临床问题开始。</h1>
          <p>Agent 会按需检索知识库，保留工具调用记录，并明确标记未经文献验证的内容。</p>
          <div class="suggestions" aria-label="示例问题">
            <button type="button" @click="prompt = '急性脑出血患者的血压管理目标是什么？'">急性期血压管理</button>
            <button type="button" @click="prompt = '抗凝相关脑出血应如何进行逆转治疗？'">抗凝药物逆转</button>
            <button type="button" @click="prompt = '脑出血患者外科手术的适应证有哪些？'">外科手术适应证</button>
          </div>
        </div>

        <div v-else class="thread">
          <article
            v-for="message in messages"
            :key="message.id"
            class="message"
            :class="message.role"
          >
            <div class="message-meta">
              <span>{{ message.role === 'user' ? '你的问题' : '循证回答' }}</span>
              <time>{{ formatTime(message.createdAt) }}</time>
            </div>
            <div v-if="message.role === 'assistant'" class="markdown-body" v-html="renderMarkdown(message.content)" />
            <p v-else>{{ displayedMessageContent(message.content) }}</p>
            <div v-if="message.result" class="answer-meta">
              <div class="answer-status">
                <span v-if="message.result.status === 'failed'" class="warning-chip">回答生成失败</span>
                <span v-else-if="message.result.verification?.issues?.includes('semantic_verification_failed')" class="warning-chip">核验暂不可用</span>
                <span v-else-if="hasVerificationFailure(message.result)" class="warning-chip">引用核验未通过</span>
                <span v-else-if="message.result.degraded_without_evidence" class="warning-chip">证据不足</span>
                <span v-else-if="message.result.citations.length" class="verified-chip">来源与数字已检查</span>
                <span v-else-if="message.result.citations.length" class="verified-chip">引用已核验</span>
                <span v-else class="neutral-chip">无需检索</span>
                <small v-if="hasVerificationFailure(message.result)">{{ verificationIssueText(message.result) }}</small>
              </div>
              <span>{{ message.result.model }}</span>
              <button type="button" @click="openInspector(message.result)">
                {{ message.result.tool_calls.length }} 次工具调用
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6" /></svg>
              </button>
            </div>
          </article>

          <article v-if="loading" class="message assistant pending">
            <div class="message-meta"><span>Agent 正在工作</span></div>
            <div class="thinking"><i /><i /><i /></div>
            <small>正在判断是否需要调用医学证据工具…</small>
          </article>
        </div>
      </section>

      <div class="composer-wrap">
        <p v-if="error" class="error-banner" role="alert">
          <span>{{ error }}</span><button type="button" @click="error = ''">关闭</button>
        </p>
        <div class="composer-context">
          <label for="knowledge-base-select">本轮知识库</label>
          <select
            id="knowledge-base-select"
            :value="selectedKnowledgeBaseId"
            :disabled="loading"
            @change="chooseKnowledgeBase(($event.target as HTMLSelectElement).value)"
          >
            <option value="">服务端默认</option>
            <option v-for="item in knowledgeBases" :key="item.id" :value="item.id">{{ item.name }}</option>
          </select>
          <span v-if="knowledgeBaseError" class="knowledge-base-hint" role="status">{{ knowledgeBaseError }}</span>
          <span v-else-if="!knowledgeBases.length" class="knowledge-base-hint">没有已索引的知识库</span>
        </div>
        <form class="composer" @submit.prevent="submit">
          <textarea
            v-model="prompt"
            rows="1"
            :disabled="loading"
            aria-label="输入医学问题"
            placeholder="询问治疗建议、证据强度或适用人群…"
            @keydown="onComposerKeydown"
          />
          <button class="send-button" type="submit" :disabled="!prompt.trim() || loading" aria-label="发送">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 14-7-4 14-3-6-7-1Z" /></svg>
          </button>
        </form>
        <small class="disclaimer">回答仅供专业参考，不替代医生诊断与治疗决策。</small>
      </div>
    </main>

    <aside class="inspector" :class="{ open: inspectorOpen }" aria-label="运行详情">
      <div class="inspector-header">
        <div><span>Run inspector</span><strong>运行详情</strong></div>
        <button class="icon-button" type="button" aria-label="关闭运行详情" @click="inspectorOpen = false">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg>
        </button>
      </div>

      <template v-if="selectedResult">
        <dl class="run-summary">
          <div><dt>状态</dt><dd :class="selectedResult.degraded_without_evidence ? 'warn' : 'success'">{{ runStatus(selectedResult) }}</dd></div>
          <div><dt>模型</dt><dd>{{ selectedResult.model }}</dd></div>
          <div><dt>检索返回</dt><dd>{{ retrievedChunks.length }}</dd></div>
          <div><dt>证据引用</dt><dd>{{ evidenceCount }}</dd></div>
          <div><dt>基础校验</dt><dd>{{ selectedResult.verification.passed ? '通过' : '需复核' }}</dd></div>
          <div><dt>工具调用</dt><dd>{{ toolCalls.length }}</dd></div>
        </dl>

        <div class="inspector-section">
          <div class="section-title"><span>执行轨迹</span><b>{{ toolCalls.length }}</b></div>
          <p v-if="!toolCalls.length" class="inspector-empty">本轮没有执行工具，回答已标记为未经文献验证。</p>
          <details v-for="(call, index) in toolCalls" :key="`${call.server}-${call.tool}-${index}`" class="tool-call">
            <summary>
              <i :class="{ failed: call.is_error }">{{ index + 1 }}</i>
              <span><strong>{{ call.tool }}</strong><small>{{ call.server }} · {{ call.latency_ms }} ms</small></span>
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 10 5 5 5-5" /></svg>
            </summary>
            <div class="tool-payload">
              <span>调用参数</span>
              <pre>{{ formatValue(call.arguments) }}</pre>
              <span>原始返回</span>
              <pre>{{ formatValue(call.result) }}</pre>
            </div>
          </details>
        </div>

        <div class="inspector-section">
          <div class="section-title"><span>全部返回知识块</span><b>{{ retrievedChunks.length }}</b></div>
          <p v-if="!retrievedChunks.length" class="inspector-empty">本轮没有检索到知识块。</p>
          <details
            v-for="(chunk, index) in retrievedChunks"
            :key="`${chunk.callIndex}-${chunk.chunk_id || index}`"
            class="evidence-item"
          >
            <summary>
              <b>{{ chunk.rank || index + 1 }}</b>
              <span><strong>{{ chunk.title || '未命名来源' }}</strong><small>{{ chunk.heading || pageLabel(chunk) }}</small></span>
              <em v-if="chunk.chunk_id && citedChunkIds.has(chunk.chunk_id)">已引用</em>
              <em v-else>调用 {{ chunk.callIndex }}</em>
            </summary>
            <div class="evidence-body">
              <p>{{ chunk.text }}</p>
              <small>{{ pageLabel(chunk) }} · score {{ chunk.score ?? '—' }}</small>
              <code>{{ chunk.chunk_id || '无 chunk_id' }}</code>
            </div>
          </details>
        </div>

        <div class="inspector-section">
          <div class="section-title"><span>当前回复引用</span><b>{{ citedChunks.length }}</b></div>
          <p v-if="!citedChunks.length" class="inspector-empty">当前回复没有使用知识块引用。</p>
          <details v-for="(chunk, index) in citedChunks" :key="chunk.chunk_id || index" class="evidence-item cited">
            <summary>
              <b>[{{ index + 1 }}]</b>
              <span><strong>{{ chunk.title || '未命名来源' }}</strong><small>{{ chunk.heading || pageLabel(chunk) }}</small></span>
              <em>引用</em>
            </summary>
            <div class="evidence-body">
              <div v-if="claimsForCitation(index + 1).length" class="claim-links">
                <span>对应陈述</span>
                <div v-for="claim in claimsForCitation(index + 1)" :key="claim.claim_id">
                  <b :class="{ failed: claim.verified && !claim.supported, pending: !claim.verified }">{{ !claim.verified ? '待核验' : claim.supported ? '已支持' : '未通过' }}</b>
                  <p>{{ claim.text }}</p>
                  <small v-if="!claim.supported && claim.reason">{{ claim.reason }}</small>
                </div>
              </div>
              <p class="source-text">
                <template v-for="(segment, segmentIndex) in highlightedSegments(chunk.text || '', index + 1)" :key="segmentIndex">
                  <mark v-if="segment.highlighted">{{ segment.text }}</mark><template v-else>{{ segment.text }}</template>
                </template>
              </p>
              <small>{{ pageLabel(chunk) }} · score {{ chunk.score ?? '—' }}</small>
              <code>{{ chunk.chunk_id || '无 chunk_id' }}</code>
            </div>
          </details>
        </div>

        <div class="inspector-section">
          <div class="section-title"><span>溯源标识</span></div>
          <code>{{ selectedResult.run_id }}</code>
        </div>
      </template>
      <p v-else class="inspector-empty">完成一次问答后，可在这里查看模型、工具和证据状态。</p>
    </aside>

    <aside class="model-settings" :class="{ open: modelSettingsOpen }" aria-label="模型配置">
      <div class="inspector-header">
        <div><span>Model registry</span><strong>模型配置</strong></div>
        <button class="icon-button" type="button" aria-label="关闭模型配置" @click="modelSettingsOpen = false">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg>
        </button>
      </div>

      <template v-if="!modelFormOpen">
        <p class="settings-help">选择本轮问答使用的模型。未添加配置时，系统继续使用服务端环境变量。</p>
        <button
          class="model-option environment"
          :class="{ selected: !selectedModelId }"
          type="button"
          @click="chooseModel('')"
        >
          <i /><span><strong>环境默认模型</strong><small>由服务端 .env 管理</small></span>
          <b>{{ !selectedModelId ? '使用中' : '' }}</b>
        </button>

        <div class="model-list">
          <div v-for="item in models" :key="item.id" class="model-row" :class="{ selected: item.id === selectedModelId }">
            <button type="button" class="model-option" @click="chooseModel(item.id)">
              <i /><span><strong>{{ item.name }}</strong><small>{{ item.provider }} · {{ item.model }}</small></span>
              <b>{{ item.id === selectedModelId ? '使用中' : item.is_default ? '默认' : '' }}</b>
            </button>
            <div class="model-actions">
              <button type="button" @click="beginEditModel(item)">编辑</button>
              <button type="button" class="danger" @click="removeModel(item)">停用</button>
            </div>
          </div>
        </div>

        <p v-if="modelError" class="form-error" role="alert">{{ modelError }}</p>
        <button class="add-model" type="button" @click="beginCreateModel">＋ 添加模型连接</button>
      </template>

      <form v-else class="model-form" @submit.prevent="saveModel">
        <button class="form-back" type="button" @click="modelFormOpen = false">← 返回模型列表</button>
        <label>配置名称<input v-model.trim="modelForm.name" required maxlength="100" placeholder="例如：DeepSeek Chat" /></label>
        <label>Harness Provider<input v-model.trim="modelForm.provider" required maxlength="100" placeholder="deepseek-official" /></label>
        <label>模型名称<input v-model.trim="modelForm.model" required maxlength="255" placeholder="deepseek-chat" /></label>
        <label>API 地址<input v-model.trim="modelForm.base_url" required type="url" maxlength="500" placeholder="https://api.deepseek.com" /></label>
        <label>
          API Key
          <input v-model="modelForm.api_key" type="password" autocomplete="new-password" :placeholder="editingModelId ? '留空表示保留原密钥' : '可留空并使用环境变量密钥'" />
        </label>
        <div class="form-grid">
          <label>最大输出<input v-model.number="modelForm.max_tokens" required type="number" min="1" max="131072" /></label>
          <label>温度<input v-model.number="modelForm.temperature" required type="number" min="0" max="2" step="0.1" /></label>
        </div>
        <label class="check-row"><input v-model="modelForm.is_default" type="checkbox" /><span>设为默认模型</span></label>
        <p class="secret-note">密钥由后端加密保存，前端无法再次读取。</p>
        <p v-if="modelError" class="form-error" role="alert">{{ modelError }}</p>
        <button class="save-model" type="submit" :disabled="modelSaving">
          {{ modelSaving ? '正在保存…' : editingModelId ? '保存修改' : '添加并使用' }}
        </button>
      </form>
    </aside>
  </div>
</template>
