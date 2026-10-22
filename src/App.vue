<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'

import {
  fetchDocumentChunks,
  fetchKnowledgeBases,
  generateQuestion,
  runAgentTest,
} from './api'
import type {
  AgentTestResult,
  DocumentChunks,
  GeneratedQuestion,
  KnowledgeBaseSummary,
} from './types'

type WorkbenchView = 'evidence' | 'generation' | 'agent'

const knowledgeBases = ref<KnowledgeBaseSummary[]>([])
const selectedKnowledgeBaseId = ref('')
const selectedDocumentId = ref('')
const documentChunks = shallowRef<DocumentChunks | null>(null)
const selectedChunkIndexes = ref<number[]>([])
const chunkQuery = ref('')
const generationInstructions = ref('')
const generatedQuestion = ref<GeneratedQuestion | null>(null)
const agentResult = ref<AgentTestResult | null>(null)
const editableQuestion = ref('')
const activeView = ref<WorkbenchView>('evidence')

const loadingCatalog = ref(false)
const loadingChunks = ref(false)
const generating = ref(false)
const testing = ref(false)
const catalogError = ref('')
const chunksError = ref('')
const generationError = ref('')
const testError = ref('')

const selectedKnowledgeBase = computed(() =>
  knowledgeBases.value.find((item) => item.id === selectedKnowledgeBaseId.value),
)
const documentOptions = computed(() => selectedKnowledgeBase.value?.documents ?? [])
const filteredChunks = computed(() => {
  const query = chunkQuery.value.trim().toLocaleLowerCase('zh-CN')
  if (!query) return documentChunks.value?.chunks ?? []
  return (documentChunks.value?.chunks ?? []).filter((chunk) =>
    `${chunk.heading_path.join(' ')} ${chunk.content}`.toLocaleLowerCase('zh-CN').includes(query),
  )
})
const currentStage = computed(() => {
  if (agentResult.value) return 3
  if (generatedQuestion.value) return 2
  if (selectedChunkIndexes.value.length) return 1
  return 0
})

function canOpen(view: WorkbenchView) {
  if (view === 'generation') return selectedChunkIndexes.value.length > 0
  if (view === 'agent') return generatedQuestion.value !== null
  return true
}

function goTo(view: WorkbenchView, focusContent = true) {
  if (!canOpen(view)) return
  activeView.value = view
  if (window.location.hash !== `#${view}`) window.location.hash = view
  if (focusContent) {
    requestAnimationFrame(() => document.getElementById('stage-content')?.focus())
  }
}

function syncViewFromHash() {
  const view = window.location.hash.slice(1) as WorkbenchView
  if (['evidence', 'generation', 'agent'].includes(view) && canOpen(view)) {
    activeView.value = view
  }
}

function asMessage(error: unknown) {
  return error instanceof Error ? error.message : '发生未知错误，请重试'
}

async function loadCatalog() {
  loadingCatalog.value = true
  catalogError.value = ''
  try {
    knowledgeBases.value = await fetchKnowledgeBases()
    if (!knowledgeBases.value.length) return
    const current = knowledgeBases.value.find((item) => item.id === selectedKnowledgeBaseId.value)
    selectedKnowledgeBaseId.value = current?.id ?? knowledgeBases.value[0].id
    syncDocumentSelection()
    if (selectedDocumentId.value) await loadChunks()
  } catch (error) {
    catalogError.value = asMessage(error)
  } finally {
    loadingCatalog.value = false
  }
}

function syncDocumentSelection() {
  const documents = documentOptions.value
  if (!documents.some((item) => item.id === selectedDocumentId.value)) {
    selectedDocumentId.value = documents[0]?.id ?? ''
  }
}

async function onKnowledgeBaseChange() {
  syncDocumentSelection()
  await loadChunks()
}

async function loadChunks() {
  documentChunks.value = null
  selectedChunkIndexes.value = []
  generatedQuestion.value = null
  agentResult.value = null
  editableQuestion.value = ''
  chunksError.value = ''
  activeView.value = 'evidence'
  window.history.replaceState(null, '', '#evidence')
  if (!selectedKnowledgeBaseId.value || !selectedDocumentId.value) return
  loadingChunks.value = true
  try {
    documentChunks.value = await fetchDocumentChunks(
      selectedKnowledgeBaseId.value,
      selectedDocumentId.value,
    )
  } catch (error) {
    chunksError.value = asMessage(error)
  } finally {
    loadingChunks.value = false
  }
}

function toggleChunk(index: number) {
  if (selectedChunkIndexes.value.includes(index)) {
    selectedChunkIndexes.value = selectedChunkIndexes.value.filter((item) => item !== index)
    return
  }
  if (selectedChunkIndexes.value.length >= 8) return
  selectedChunkIndexes.value = [...selectedChunkIndexes.value, index]
}

async function handleGenerate() {
  if (!selectedKnowledgeBaseId.value || !selectedDocumentId.value || !selectedChunkIndexes.value.length) return
  generating.value = true
  generationError.value = ''
  generatedQuestion.value = null
  agentResult.value = null
  try {
    const result = await generateQuestion({
      collection_id: selectedKnowledgeBaseId.value,
      document_id: selectedDocumentId.value,
      chunk_indexes: selectedChunkIndexes.value,
      instructions: generationInstructions.value.trim() || undefined,
    })
    generatedQuestion.value = result
    editableQuestion.value = result.question
  } catch (error) {
    generationError.value = asMessage(error)
  } finally {
    generating.value = false
  }
}

async function handleAgentTest() {
  if (!editableQuestion.value.trim()) return
  testing.value = true
  testError.value = ''
  agentResult.value = null
  try {
    agentResult.value = await runAgentTest({
      question: editableQuestion.value.trim(),
      expected_answer: generatedQuestion.value?.standard_answer,
    })
  } catch (error) {
    testError.value = asMessage(error)
  } finally {
    testing.value = false
  }
}

onMounted(() => {
  window.addEventListener('hashchange', syncViewFromHash)
  void loadCatalog()
})
onBeforeUnmount(() => window.removeEventListener('hashchange', syncViewFromHash))
</script>

<template>
  <div class="platform-frame">
    <aside class="platform-sidebar">
      <a class="brand-lockup" href="/knowledge/" aria-label="返回知识空间">
        <span class="brand-monogram" aria-hidden="true">
          <svg class="brand-glyph" viewBox="0 0 26 12" focusable="false">
            <path d="M0 0h2v12H0zM12 0H6C4.9 0 4 .9 4 2v8c0 1.1.9 2 2 2h6v-2H6V2h6zM15 0h2v5h7V0h2v12h-2V7h-7v5h-2z" />
          </svg>
        </span>
        <span class="brand-copy"><strong>循证医学平台</strong><small>ICH EVIDENCE OS</small></span>
      </a>

      <div class="platform-switcher" role="navigation" aria-label="平台应用切换">
        <a href="/knowledge/">知识空间</a>
        <a href="/agent/">智能问答</a>
        <a class="active" href="/evaluation/" aria-current="page">评测中心</a>
      </div>

      <nav class="sidebar-workflow" aria-label="评测流程">
        <span class="sidebar-section-label">评测工作台</span>
        <ol>
          <li :class="{ active: activeView === 'evidence', complete: currentStage > 0 }">
            <button type="button" :aria-current="activeView === 'evidence' ? 'step' : undefined" @click="goTo('evidence')">
              <b>01</b><span>选择证据</span><small>查看文档分块</small>
            </button>
          </li>
          <li :class="{ active: activeView === 'generation', complete: currentStage > 1 }">
            <button
              type="button"
              :disabled="!selectedChunkIndexes.length"
              :aria-current="activeView === 'generation' ? 'step' : undefined"
              @click="goTo('generation')"
            >
              <b>02</b><span>生成题目</span><small>{{ selectedChunkIndexes.length ? `已选 ${selectedChunkIndexes.length} 个分块` : '等待选择证据' }}</small>
            </button>
          </li>
          <li :class="{ active: activeView === 'agent', complete: currentStage > 2 }">
            <button
              type="button"
              :disabled="!generatedQuestion"
              :aria-current="activeView === 'agent' ? 'step' : undefined"
              @click="goTo('agent')"
            >
              <b>03</b><span>Agent 试答</span><small>{{ generatedQuestion ? '测试题已就绪' : '等待生成题目' }}</small>
            </button>
          </li>
        </ol>
      </nav>

      <div class="sidebar-note"><strong>证据优先</strong><span>每道题都保留原始分块与模型运行记录。</span></div>
      <div class="sidebar-status"><i />评测服务已连接</div>
    </aside>

    <div class="app-shell">
      <a class="skip-link" href="#main-workbench">跳到工作区</a>

      <header class="topbar">
        <div class="module-context">
          <span>评测中心 / 题目实验</span>
          <strong>从证据分块到 Agent 试答</strong>
        </div>
        <div class="topbar-status"><i />服务已连接</div>
      </header>

      <main id="main-workbench" class="workbench" :class="{ 'single-stage': activeView !== 'evidence' }">
      <aside v-if="activeView === 'evidence'" class="setup-panel" aria-labelledby="setup-title">
        <div class="panel-heading">
          <span class="eyebrow">01 / 证据来源</span>
          <h1 id="setup-title">从真实分块开始出题</h1>
          <p>选择知识库和文档，测试服务只通过公开接口读取数据。</p>
        </div>

        <div class="field-group">
          <label for="knowledge-base">知识库</label>
          <select
            id="knowledge-base"
            v-model="selectedKnowledgeBaseId"
            :disabled="loadingCatalog || !knowledgeBases.length"
            @change="onKnowledgeBaseChange"
          >
            <option v-if="!knowledgeBases.length" value="">暂无可用知识库</option>
            <option v-for="item in knowledgeBases" :key="item.id" :value="item.id">
              {{ item.name }} · {{ item.status }}
            </option>
          </select>
        </div>

        <div class="field-group">
          <label for="document">文档</label>
          <select
            id="document"
            v-model="selectedDocumentId"
            :disabled="loadingCatalog || !documentOptions.length"
            @change="loadChunks"
          >
            <option v-if="!documentOptions.length" value="">该知识库暂无文档</option>
            <option v-for="item in documentOptions" :key="item.id" :value="item.id">
              {{ item.title }}
            </option>
          </select>
        </div>

        <button v-if="catalogError" class="error-box" type="button" @click="loadCatalog">
          <span>{{ catalogError }}</span>
          <b>重新连接</b>
        </button>

        <div v-if="documentChunks" class="method-note">
          <span>当前分块方法</span>
          <strong>{{ documentChunks.method_name }}</strong>
          <code>{{ documentChunks.method_strategy }}</code>
        </div>
      </aside>

      <section
        v-if="activeView === 'evidence'"
        id="stage-content"
        class="chunks-panel"
        aria-labelledby="chunks-title"
        tabindex="-1"
      >
        <div class="section-header">
          <div>
            <span class="eyebrow">02 / 文档分块</span>
            <h2 id="chunks-title">{{ documentChunks?.document_title || '等待选择文档' }}</h2>
          </div>
          <div v-if="documentChunks" class="stats-line" aria-label="分块统计">
            <span><b>{{ documentChunks.stats.total }}</b> 块</span>
            <span><b>{{ documentChunks.stats.average_chars }}</b> 平均字数</span>
          </div>
        </div>

        <div class="chunk-toolbar">
          <label class="search-field" for="chunk-search">
            <span>筛选分块</span>
            <input
              id="chunk-search"
              v-model="chunkQuery"
              type="search"
              placeholder="检索章节或正文关键词"
              :disabled="!documentChunks"
            >
          </label>
          <div class="selection-count" aria-live="polite">
            已选 <b>{{ selectedChunkIndexes.length }}</b> / 8
          </div>
        </div>

        <div v-if="loadingChunks" class="chunk-list" aria-label="正在加载分块">
          <div v-for="item in 4" :key="item" class="chunk-skeleton"><i></i><i></i><i></i></div>
        </div>
        <div v-else-if="chunksError" class="center-state" role="alert">
          <strong>分块加载失败</strong>
          <p>{{ chunksError }}</p>
          <button class="secondary-button" type="button" @click="loadChunks">重试</button>
        </div>
        <div v-else-if="!documentChunks" class="center-state">
          <strong>尚未载入文档</strong>
          <p>从左侧选择一个包含持久化分块的知识库文档。</p>
        </div>
        <div v-else-if="!filteredChunks.length" class="center-state">
          <strong>没有匹配的分块</strong>
          <p>清除关键词，或换一个文档继续查找。</p>
        </div>
        <div v-else class="chunk-list" aria-label="文档分块列表">
          <article
            v-for="chunk in filteredChunks"
            :key="chunk.index"
            v-memo="[chunk.index, selectedChunkIndexes.includes(chunk.index)]"
            class="chunk-card"
            :class="{ selected: selectedChunkIndexes.includes(chunk.index) }"
          >
            <label>
              <input
                type="checkbox"
                :checked="selectedChunkIndexes.includes(chunk.index)"
                :disabled="selectedChunkIndexes.length >= 8 && !selectedChunkIndexes.includes(chunk.index)"
                @change="toggleChunk(chunk.index)"
              >
              <span class="custom-check" aria-hidden="true"></span>
              <span class="chunk-body">
                <span class="chunk-meta">
                  <b>#{{ String(chunk.index + 1).padStart(2, '0') }}</b>
                  <span>{{ chunk.heading_path.join(' / ') || '未标注章节' }}</span>
                  <small>{{ chunk.char_count }} 字</small>
                </span>
                <span class="chunk-content">{{ chunk.content }}</span>
              </span>
            </label>
          </article>
        </div>
        <div class="stage-actions">
          <span>已选择 {{ selectedChunkIndexes.length }} 个证据分块</span>
          <button
            class="primary-button next-button"
            type="button"
            :disabled="!selectedChunkIndexes.length"
            @click="goTo('generation')"
          >
            前往生成题目
          </button>
        </div>
      </section>

      <section
        v-else
        id="stage-content"
        class="lab-panel"
        aria-labelledby="lab-title"
        tabindex="-1"
      >
        <div class="section-header compact">
          <div>
            <span class="eyebrow">{{ activeView === 'generation' ? '02 / 题目生成' : '03 / Agent 试答' }}</span>
            <h2 id="lab-title">{{ activeView === 'generation' ? '基于证据生成测试题' : '发送题目并查看回答轨迹' }}</h2>
          </div>
        </div>

        <label v-if="activeView === 'generation'" class="field-group" for="instructions">
          <span>补充出题要求 <small>可选</small></span>
          <textarea
            id="instructions"
            v-model="generationInstructions"
            rows="3"
            maxlength="1000"
            placeholder="例如：聚焦治疗时机，难度设为专科进阶"
          ></textarea>
        </label>
        <button
          v-if="activeView === 'generation'"
          class="primary-button"
          type="button"
          :disabled="!selectedChunkIndexes.length || generating || testing"
          @click="handleGenerate"
        >
          <span v-if="generating" class="spinner" aria-hidden="true"></span>
          {{ generating ? 'Agent 正在编题…' : `根据 ${selectedChunkIndexes.length} 个分块生成题目` }}
        </button>
        <div v-if="activeView === 'generation' && generationError" class="inline-error" role="alert">
          <span>{{ generationError }}</span>
          <button type="button" @click="handleGenerate">重试</button>
        </div>

        <div v-if="activeView === 'generation' && !generatedQuestion" class="empty-lab">
          <div class="evidence-glyph" aria-hidden="true"><i></i><i></i><i></i></div>
          <strong>题目将在这里生成</strong>
          <p>先勾选能共同支撑答案的分块，再让 Agent 形成题干和标准答案。</p>
        </div>

        <div v-if="generatedQuestion" class="question-sheet">
          <div class="question-tags">
            <span>{{ generatedQuestion.difficulty }}</span>
            <span>{{ generatedQuestion.topic || '未分类' }}</span>
          </div>
          <label for="question-text">测试题目</label>
          <textarea
            id="question-text"
            v-model="editableQuestion"
            rows="4"
            :readonly="activeView === 'generation'"
          ></textarea>

          <div class="answer-block">
            <span>标准答案</span>
            <p>{{ generatedQuestion.standard_answer }}</p>
          </div>
          <details v-if="generatedQuestion.evidence.length" class="evidence-details">
            <summary>查看 {{ generatedQuestion.evidence.length }} 条出题依据</summary>
            <blockquote v-for="item in generatedQuestion.evidence" :key="`${item.chunk_index}-${item.quote}`">
              <b>分块 #{{ item.chunk_index + 1 }} · {{ item.heading || '未标注章节' }}</b>
              {{ item.quote }}
            </blockquote>
          </details>
          <button
            v-if="activeView === 'generation'"
            class="test-button"
            type="button"
            @click="goTo('agent')"
          >
            前往 Agent 试答
          </button>
          <button
            v-if="activeView === 'agent'"
            class="test-button"
            type="button"
            :disabled="!editableQuestion.trim() || testing || generating"
            @click="handleAgentTest"
          >
            <span v-if="testing" class="spinner dark" aria-hidden="true"></span>
            {{ testing ? 'Agent 正在检索并回答…' : '发送给 Agent 试答' }}
          </button>
          <div v-if="activeView === 'agent' && testError" class="inline-error" role="alert">
            <span>{{ testError }}</span>
            <button type="button" @click="handleAgentTest">重试</button>
          </div>
        </div>

        <div v-if="activeView === 'agent' && agentResult" class="result-sheet" aria-live="polite">
          <div class="result-heading">
            <div>
              <span class="eyebrow">Agent response</span>
              <h3>试答结果</h3>
            </div>
            <span :class="agentResult.degraded_without_evidence ? 'status-warning' : 'status-ok'">
              {{ agentResult.degraded_without_evidence ? '无证据降级' : '证据链已启用' }}
            </span>
          </div>
          <div class="agent-answer">{{ agentResult.answer }}</div>
          <dl class="run-meta">
            <div><dt>模型</dt><dd>{{ agentResult.provider }} / {{ agentResult.model }}</dd></div>
            <div><dt>工具调用</dt><dd>{{ agentResult.tool_calls.length }} 次</dd></div>
            <div><dt>运行 ID</dt><dd>{{ agentResult.run_id }}</dd></div>
          </dl>
          <details v-if="agentResult.tool_calls.length" class="trace-details">
            <summary>查看工具调用轨迹</summary>
            <pre>{{ JSON.stringify(agentResult.tool_calls, null, 2) }}</pre>
          </details>
        </div>
      </section>
      </main>
    </div>
  </div>
</template>
