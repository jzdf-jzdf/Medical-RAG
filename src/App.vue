<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, shallowRef } from 'vue'

import {
  createQuestion, createQuestionBank, createQuestionsBulk, fetchDocumentChunks,
  fetchKnowledgeBases, fetchQuestionBank, fetchQuestionBanks, fetchQuestions, generateQuestions,
} from './api'
import type {
  DocumentChunks, GeneratedQuestion, KnowledgeBaseSummary, QuestionBankDetail,
  QuestionBankSummary, QuestionInput, QuestionStyle, QuestionType, StoredQuestion,
} from './types'

type View = 'banks' | 'questions' | 'generate' | 'bank'
type Notice = { kind: 'success' | 'error'; text: string }

const activeView = ref<View>('banks')
const banks = ref<QuestionBankSummary[]>([])
const questions = ref<StoredQuestion[]>([])
const selectedBank = shallowRef<QuestionBankDetail | null>(null)
const loadingBanks = ref(true)
const loadingQuestions = ref(true)
const loadingBank = ref(false)
const loadError = ref('')
const notice = ref<Notice | null>(null)
let noticeTimer: ReturnType<typeof setTimeout> | null = null

const questionQuery = ref('')
const questionTypeFilter = ref<'all' | QuestionType>('all')
const questionBankFilter = ref('all')
const openQuestionIds = ref<Set<string>>(new Set())

const showBankDialog = ref(false)
const savingBank = ref(false)
const bankForm = reactive({ name: '', description: '' })
const showQuestionDialog = ref(false)
const savingQuestion = ref(false)
const questionForm = reactive({
  bankId: '',
  questionType: 'qa' as QuestionType,
  prompt: '',
  standardAnswer: '',
  correctAnswer: 'A',
  options: [
    { key: 'A', text: '' }, { key: 'B', text: '' },
    { key: 'C', text: '' }, { key: 'D', text: '' },
  ],
})

const knowledgeBases = ref<KnowledgeBaseSummary[]>([])
const selectedKnowledgeBaseId = ref('')
const selectedDocumentId = ref('')
const documentChunks = shallowRef<DocumentChunks | null>(null)
const loadingCatalog = ref(false)
const loadingChunks = ref(false)
const catalogError = ref('')
const chunksError = ref('')
const chunkQuery = ref('')
const selectedChunkIndexes = ref<number[]>([])
const generationType = ref<QuestionType>('qa')
const questionCount = ref(1)
const questionStyle = ref<QuestionStyle>('clinician')
const generationInstructions = ref('')
const generatedQuestions = ref<GeneratedQuestion[]>([])
const selectedGeneratedIndexes = ref<Set<number>>(new Set())
const generatedStyle = ref<QuestionStyle>('clinician')
const generating = ref(false)
const generationError = ref('')
const targetBankId = ref('')
const savingGenerated = ref(false)

const styles: Array<{ value: QuestionStyle; label: string; hint: string }> = [
  { value: 'patient', label: '患者', hint: '就诊者或家属自然提问' },
  { value: 'clinician', label: '医生', hint: '临床判断与处置决策' },
  { value: 'professional', label: '从业者', hint: '护理、急救、影像等执行视角' },
  { value: 'public', label: '大众', hint: '清晰易懂的健康知识表达' },
]

const totalQuestions = computed(() => banks.value.reduce((sum, item) => sum + item.question_count, 0))
const selectedKnowledgeBase = computed(() =>
  knowledgeBases.value.find((item) => item.id === selectedKnowledgeBaseId.value),
)
const documentOptions = computed(() => selectedKnowledgeBase.value?.documents ?? [])
const filteredChunks = computed(() => {
  const query = chunkQuery.value.trim().toLocaleLowerCase('zh-CN')
  const chunks = documentChunks.value?.chunks ?? []
  if (!query) return chunks
  return chunks.filter((chunk) =>
    `${chunk.heading_path.join(' ')} ${chunk.content}`.toLocaleLowerCase('zh-CN').includes(query),
  )
})
const filteredQuestions = computed(() => {
  const query = questionQuery.value.trim().toLocaleLowerCase('zh-CN')
  return questions.value.filter((item) => {
    const matchesType = questionTypeFilter.value === 'all' || item.question_type === questionTypeFilter.value
    const matchesBank = questionBankFilter.value === 'all' || item.bank_id === questionBankFilter.value
    const matchesQuery = !query || `${item.prompt} ${item.standard_answer} ${item.bank_name}`
      .toLocaleLowerCase('zh-CN').includes(query)
    return matchesType && matchesBank && matchesQuery
  })
})
const allGeneratedSelected = computed(() => generatedQuestions.value.length > 0 && selectedGeneratedIndexes.value.size === generatedQuestions.value.length)

function showNotice(kind: Notice['kind'], text: string) {
  notice.value = { kind, text }
  if (noticeTimer) clearTimeout(noticeTimer)
  noticeTimer = setTimeout(() => { notice.value = null }, 4200)
}

function messageOf(error: unknown) {
  return error instanceof Error ? error.message : '发生未知错误，请重试'
}

function navigate(view: Exclude<View, 'bank'>) {
  activeView.value = view
  selectedBank.value = null
  window.location.hash = view
  requestAnimationFrame(() => document.getElementById('main-content')?.focus())
}

async function openBank(id: string, updateHash = true) {
  activeView.value = 'bank'
  loadingBank.value = true
  selectedBank.value = null
  if (updateHash) window.location.hash = `bank/${id}`
  try {
    selectedBank.value = await fetchQuestionBank(id)
  } catch (error) {
    showNotice('error', messageOf(error))
    navigate('banks')
  } finally {
    loadingBank.value = false
  }
}

function syncHash() {
  const hash = window.location.hash.slice(1)
  if (hash.startsWith('bank/')) {
    const id = hash.slice(5)
    if (id && id !== selectedBank.value?.id) void openBank(id, false)
    return
  }
  if (['banks', 'questions', 'generate'].includes(hash)) {
    activeView.value = hash as View
    selectedBank.value = null
  }
}

async function loadBanks() {
  loadingBanks.value = true
  try {
    banks.value = await fetchQuestionBanks()
    if (!targetBankId.value && banks.value.length) targetBankId.value = banks.value[0].id
  } finally {
    loadingBanks.value = false
  }
}

async function loadQuestions() {
  loadingQuestions.value = true
  try {
    questions.value = await fetchQuestions()
  } finally {
    loadingQuestions.value = false
  }
}

async function loadCatalog() {
  loadingCatalog.value = true
  catalogError.value = ''
  try {
    knowledgeBases.value = await fetchKnowledgeBases()
    selectedKnowledgeBaseId.value = knowledgeBases.value[0]?.id ?? ''
    syncDocument()
    if (selectedDocumentId.value) await loadChunks()
  } catch (error) {
    catalogError.value = messageOf(error)
  } finally {
    loadingCatalog.value = false
  }
}

function syncDocument() {
  if (!documentOptions.value.some((item) => item.id === selectedDocumentId.value)) {
    selectedDocumentId.value = documentOptions.value[0]?.id ?? ''
  }
}

async function onKnowledgeBaseChange() {
  syncDocument()
  await loadChunks()
}

async function loadChunks() {
  documentChunks.value = null
  selectedChunkIndexes.value = []
  generatedQuestions.value = []
  selectedGeneratedIndexes.value = new Set()
  chunksError.value = ''
  if (!selectedKnowledgeBaseId.value || !selectedDocumentId.value) return
  loadingChunks.value = true
  try {
    documentChunks.value = await fetchDocumentChunks(
      selectedKnowledgeBaseId.value, selectedDocumentId.value,
    )
  } catch (error) {
    chunksError.value = messageOf(error)
  } finally {
    loadingChunks.value = false
  }
}

function toggleChunk(index: number) {
  if (selectedChunkIndexes.value.includes(index)) {
    selectedChunkIndexes.value = selectedChunkIndexes.value.filter((item) => item !== index)
  } else if (selectedChunkIndexes.value.length < 8) {
    selectedChunkIndexes.value = [...selectedChunkIndexes.value, index]
  }
}

async function handleCreateBank() {
  if (!bankForm.name.trim()) return
  savingBank.value = true
  try {
    const bank = await createQuestionBank({
      name: bankForm.name.trim(),
      description: bankForm.description.trim() || undefined,
    })
    bankForm.name = ''
    bankForm.description = ''
    showBankDialog.value = false
    await loadBanks()
    showNotice('success', `题库“${bank.name}”已创建`)
    await openBank(bank.id)
  } catch (error) {
    showNotice('error', messageOf(error))
  } finally {
    savingBank.value = false
  }
}

function openCreateQuestion(bankId?: string) {
  questionForm.bankId = bankId || banks.value[0]?.id || ''
  questionForm.questionType = 'qa'
  questionForm.prompt = ''
  questionForm.standardAnswer = ''
  questionForm.correctAnswer = 'A'
  questionForm.options.forEach((item) => { item.text = '' })
  showQuestionDialog.value = true
}

async function handleCreateQuestion() {
  if (!questionForm.bankId || !questionForm.prompt.trim() || !questionForm.standardAnswer.trim()) return
  savingQuestion.value = true
  const payload: QuestionInput = {
    question_type: questionForm.questionType,
    prompt: questionForm.prompt.trim(),
    standard_answer: questionForm.standardAnswer.trim(),
    source: 'manual',
  }
  if (questionForm.questionType === 'single_choice') {
    payload.options = questionForm.options.map((item) => ({ ...item, text: item.text.trim() }))
    payload.correct_answer = questionForm.correctAnswer
  }
  try {
    await createQuestion(questionForm.bankId, payload)
    showQuestionDialog.value = false
    await Promise.all([loadBanks(), loadQuestions()])
    if (selectedBank.value?.id === questionForm.bankId) await openBank(questionForm.bankId, false)
    showNotice('success', '题目已加入题库')
  } catch (error) {
    showNotice('error', messageOf(error))
  } finally {
    savingQuestion.value = false
  }
}

async function handleGenerate() {
  if (!selectedKnowledgeBaseId.value || !selectedDocumentId.value || !selectedChunkIndexes.value.length) return
  generating.value = true
  generationError.value = ''
  generatedQuestions.value = []
  selectedGeneratedIndexes.value = new Set()
  try {
    const result = await generateQuestions({
      collection_id: selectedKnowledgeBaseId.value,
      document_id: selectedDocumentId.value,
      chunk_indexes: selectedChunkIndexes.value,
      question_count: questionCount.value,
      question_type: generationType.value,
      question_style: questionStyle.value,
      instructions: generationInstructions.value.trim() || undefined,
    })
    generatedQuestions.value = result.questions
    selectedGeneratedIndexes.value = new Set(result.questions.map((_, index) => index))
    generatedStyle.value = result.question_style
  } catch (error) {
    generationError.value = messageOf(error)
  } finally {
    generating.value = false
  }
}

async function saveGenerated() {
  if (!targetBankId.value || !selectedGeneratedIndexes.value.size) return
  savingGenerated.value = true
  const selectedIndexes = new Set(selectedGeneratedIndexes.value)
  const questionsToSave: QuestionInput[] = generatedQuestions.value
    .filter((_, index) => selectedIndexes.has(index))
    .map((item) => ({
      question_type: item.question_type,
      prompt: item.question,
      standard_answer: item.standard_answer,
      options: item.options,
      correct_answer: item.correct_answer,
      source: 'generated',
      evidence: item.evidence,
      metadata: {
        difficulty: item.difficulty,
        topic: item.topic,
        rationale: item.rationale,
        question_style: generatedStyle.value,
        source_collection_id: selectedKnowledgeBaseId.value,
        source_document_id: selectedDocumentId.value,
        source_chunks: item.source_chunks,
        agent_run_id: item.agent_run_id,
        model: item.model,
        provider: item.provider,
      },
    }))
  try {
    const saved = await createQuestionsBulk(targetBankId.value, questionsToSave)
    await Promise.all([loadBanks(), loadQuestions()])
    const bankName = banks.value.find((item) => item.id === targetBankId.value)?.name || '目标题库'
    showNotice('success', `已将 ${saved.length} 道题保存到“${bankName}”`)
    generatedQuestions.value = generatedQuestions.value.filter((_, index) => !selectedIndexes.has(index))
    selectedGeneratedIndexes.value = new Set(generatedQuestions.value.map((_, index) => index))
  } catch (error) {
    showNotice('error', messageOf(error))
  } finally {
    savingGenerated.value = false
  }
}

function toggleGeneratedQuestion(index: number) {
  const next = new Set(selectedGeneratedIndexes.value)
  if (next.has(index)) next.delete(index)
  else next.add(index)
  selectedGeneratedIndexes.value = next
}

function toggleAllGeneratedQuestions() {
  selectedGeneratedIndexes.value = allGeneratedSelected.value
    ? new Set()
    : new Set(generatedQuestions.value.map((_, index) => index))
}

function toggleQuestion(id: string) {
  const next = new Set(openQuestionIds.value)
  next.has(id) ? next.delete(id) : next.add(id)
  openQuestionIds.value = next
}

function typeLabel(type: QuestionType) {
  return type === 'qa' ? '问答题' : '单选题'
}

function styleLabel(style: unknown) {
  return styles.find((item) => item.value === style)?.label || '未指定'
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', year: 'numeric' })
    .format(new Date(value))
}

onMounted(async () => {
  window.addEventListener('hashchange', syncHash)
  try {
    await Promise.all([loadBanks(), loadQuestions(), loadCatalog()])
  } catch (error) {
    loadError.value = messageOf(error)
  }
  syncHash()
})
onBeforeUnmount(() => {
  window.removeEventListener('hashchange', syncHash)
  if (noticeTimer) clearTimeout(noticeTimer)
})
</script>

<template>
  <div class="platform-frame">
    <aside class="platform-sidebar">
      <a class="brand" href="/knowledge/" aria-label="返回知识空间">
        <span class="brand-mark" aria-hidden="true">
          <svg viewBox="0 0 26 12"><path d="M0 0h2v12H0zM12 0H6C4.9 0 4 .9 4 2v8c0 1.1.9 2 2 2h6v-2H6V2h6zM15 0h2v5h7V0h2v12h-2V7h-7v5h-2z" /></svg>
        </span>
        <span><strong>循证医学平台</strong><small>ICH EVIDENCE OS</small></span>
      </a>

      <div class="platform-switcher" aria-label="平台应用">
        <a href="/knowledge/">知识空间</a>
        <a href="/agent/">智能问答</a>
        <a class="active" href="/evaluation/" aria-current="page">评测中心</a>
      </div>

      <nav class="primary-nav" aria-label="评测中心导航">
        <span class="nav-label">题库工作区</span>
        <button :class="{ active: activeView === 'questions' }" type="button" @click="navigate('questions')">
          <svg viewBox="0 0 24 24"><path d="M6 3h12v18H6zM9 7h6M9 11h6M9 15h4" /></svg>
          <span><b>全部题目</b><small>{{ totalQuestions }} 道题目</small></span>
        </button>
        <button :class="{ active: activeView === 'banks' || activeView === 'bank' }" type="button" @click="navigate('banks')">
          <svg viewBox="0 0 24 24"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H11v16H6.5A2.5 2.5 0 0 0 4 21.5zm16 0A2.5 2.5 0 0 0 17.5 3H13v16h4.5a2.5 2.5 0 0 1 2.5 2.5z" /></svg>
          <span><b>题库</b><small>{{ banks.length }} 个题库</small></span>
        </button>
        <button :class="{ active: activeView === 'generate' }" type="button" @click="navigate('generate')">
          <svg viewBox="0 0 24 24"><path d="m12 3 1.4 4.1L17.5 8.5l-4.1 1.4L12 14l-1.4-4.1-4.1-1.4 4.1-1.4zM18 14l.8 2.2L21 17l-2.2.8L18 20l-.8-2.2L15 17l2.2-.8z" /></svg>
          <span><b>生成题目</b><small>从知识分块出题</small></span>
        </button>
      </nav>

      <div class="sidebar-foot">
        <span><i></i>评测服务已连接</span>
        <p>题目与来源证据统一归档，作为后续评测的数据基础。</p>
      </div>
    </aside>

    <div class="app-shell">
      <header class="topbar">
        <div>
          <span>评测中心</span>
          <strong>{{ activeView === 'generate' ? '证据出题' : activeView === 'questions' ? '题目总览' : activeView === 'bank' ? '题库详情' : '题库管理' }}</strong>
        </div>
        <button v-if="activeView !== 'generate'" class="top-create" type="button" @click="showBankDialog = true">＋ 创建题库</button>
      </header>

      <main id="main-content" class="main-content" tabindex="-1">
        <div v-if="loadError" class="page-error">{{ loadError }}</div>

        <section v-if="activeView === 'banks'" class="page-section">
          <div class="page-heading">
            <div><span class="eyebrow">Question libraries</span><h1>题库</h1><p>按评测主题组织题目，每个题库可独立维护问答题和选择题。</p></div>
            <div class="inventory">
              <span><b>{{ banks.length }}</b> 个题库</span>
              <span><b>{{ totalQuestions }}</b> 道题目</span>
            </div>
          </div>

          <div v-if="loadingBanks" class="bank-grid" aria-label="正在加载题库">
            <div v-for="item in 3" :key="item" class="bank-card skeleton"></div>
          </div>
          <div v-else-if="!banks.length" class="empty-state">
            <span class="empty-index">00</span><h2>还没有题库</h2>
            <p>先创建一个题库，再手工加入题目或从知识分块生成题目。</p>
            <button class="primary-button" type="button" @click="showBankDialog = true">创建第一个题库</button>
          </div>
          <div v-else class="bank-grid">
            <button v-for="(bank, index) in banks" :key="bank.id" class="bank-card" type="button" @click="openBank(bank.id)">
              <span class="bank-spine" :class="`tone-${index % 4}`"></span>
              <span class="bank-card-head"><small>QB / {{ String(index + 1).padStart(2, '0') }}</small><i>→</i></span>
              <strong>{{ bank.name }}</strong>
              <p>{{ bank.description || '尚未填写题库说明。' }}</p>
              <span class="bank-count"><b>{{ bank.question_count }}</b> 道题</span>
              <span class="bank-breakdown"><i>{{ bank.qa_count }} 问答</i><i>{{ bank.choice_count }} 选择</i></span>
              <time>更新于 {{ formatDate(bank.updated_at) }}</time>
            </button>
            <button class="bank-card create-card" type="button" @click="showBankDialog = true">
              <span class="create-symbol">＋</span><strong>创建新题库</strong><p>建立一个新的评测主题集合</p>
            </button>
          </div>
        </section>

        <section v-else-if="activeView === 'bank'" class="page-section">
          <button class="back-button" type="button" @click="navigate('banks')">← 返回题库</button>
          <div v-if="loadingBank" class="bank-detail-skeleton"></div>
          <template v-else-if="selectedBank">
            <div class="bank-hero">
              <div><span class="eyebrow">Question bank / v{{ selectedBank.version }}</span><h1>{{ selectedBank.name }}</h1><p>{{ selectedBank.description || '尚未填写题库说明。' }}</p></div>
              <div class="hero-actions"><button class="secondary-button" type="button" @click="navigate('generate')">从分块生成</button><button class="primary-button" type="button" @click="openCreateQuestion(selectedBank.id)">＋ 添加题目</button></div>
            </div>
            <div class="bank-summary">
              <span><b>{{ selectedBank.question_count }}</b><small>全部题目</small></span>
              <span><b>{{ selectedBank.qa_count }}</b><small>问答题</small></span>
              <span><b>{{ selectedBank.choice_count }}</b><small>选择题</small></span>
              <span><b>{{ formatDate(selectedBank.updated_at) }}</b><small>最近更新</small></span>
            </div>
            <div class="section-line"><h2>题目清单</h2><span>{{ selectedBank.question_count }} items</span></div>
            <div v-if="!selectedBank.questions.length" class="empty-list"><b>题库仍是空的</b><span>添加一道题目，或从知识分块批量生成。</span></div>
            <div v-else class="question-list">
              <article v-for="(question, index) in selectedBank.questions" :key="question.id" :class="{ open: openQuestionIds.has(question.id) }">
                <button class="question-row" type="button" @click="toggleQuestion(question.id)">
                  <span class="question-number">{{ String(index + 1).padStart(2, '0') }}</span>
                  <span class="type-badge" :class="question.question_type">{{ typeLabel(question.question_type) }}</span>
                  <span class="question-copy"><b>{{ question.prompt }}</b><small>{{ question.source === 'generated' ? '分块生成' : '手工录入' }} · {{ formatDate(question.created_at) }}</small></span>
                  <span class="row-arrow">⌄</span>
                </button>
                <div v-if="openQuestionIds.has(question.id)" class="question-detail">
                  <div v-if="question.options.length" class="option-list"><span v-for="option in question.options" :key="option.key" :class="{ correct: option.key === question.correct_answer }"><b>{{ option.key }}</b>{{ option.text }}</span></div>
                  <div class="answer-panel"><small>标准答案</small><p>{{ question.standard_answer }}</p></div>
                </div>
              </article>
            </div>
          </template>
        </section>

        <section v-else-if="activeView === 'questions'" class="page-section">
          <div class="page-heading">
            <div><span class="eyebrow">Question index</span><h1>全部题目</h1><p>跨题库检索、筛选并核对所有评测题目。</p></div>
            <button class="primary-button" type="button" :disabled="!banks.length" @click="openCreateQuestion()">＋ 添加题目</button>
          </div>
          <div class="filter-bar">
            <label class="search-box"><span>⌕</span><input v-model="questionQuery" type="search" placeholder="搜索题干、答案或题库"></label>
            <select v-model="questionBankFilter" aria-label="按题库筛选"><option value="all">全部题库</option><option v-for="bank in banks" :key="bank.id" :value="bank.id">{{ bank.name }}</option></select>
            <select v-model="questionTypeFilter" aria-label="按题型筛选"><option value="all">全部题型</option><option value="qa">问答题</option><option value="single_choice">单选题</option></select>
          </div>
          <div class="list-caption"><span>题目</span><span>所属题库 / 来源</span><b>{{ filteredQuestions.length }} 条</b></div>
          <div v-if="loadingQuestions" class="list-loading">正在载入题目索引…</div>
          <div v-else-if="!filteredQuestions.length" class="empty-list"><b>没有匹配的题目</b><span>调整筛选条件，或向题库添加新题目。</span></div>
          <div v-else class="question-list all-questions">
            <article v-for="(question, index) in filteredQuestions" :key="question.id" :class="{ open: openQuestionIds.has(question.id) }">
              <button class="question-row" type="button" @click="toggleQuestion(question.id)">
                <span class="question-number">{{ String(index + 1).padStart(2, '0') }}</span>
                <span class="type-badge" :class="question.question_type">{{ typeLabel(question.question_type) }}</span>
                <span class="question-copy"><b>{{ question.prompt }}</b><small>{{ question.bank_name }} · {{ question.source === 'generated' ? '分块生成' : '手工录入' }}</small></span>
                <span class="row-arrow">⌄</span>
              </button>
              <div v-if="openQuestionIds.has(question.id)" class="question-detail">
                <div v-if="question.options.length" class="option-list"><span v-for="option in question.options" :key="option.key" :class="{ correct: option.key === question.correct_answer }"><b>{{ option.key }}</b>{{ option.text }}</span></div>
                <div class="answer-panel"><small>标准答案</small><p>{{ question.standard_answer }}</p></div>
                <div v-if="question.metadata.topic" class="meta-strip"><span>主题：{{ question.metadata.topic }}</span><span>口吻：{{ styleLabel(question.metadata.question_style) }}</span><span>难度：{{ question.metadata.difficulty || '未指定' }}</span></div>
              </div>
            </article>
          </div>
        </section>

        <section v-else class="generation-page">
          <div class="page-heading generation-heading">
            <div><span class="eyebrow">Evidence to question</span><h1>生成题目</h1><p>选择知识服务中的原始分块，生成问答题或单选题并保存到题库。</p></div>
            <div class="source-summary"><b>{{ selectedChunkIndexes.length }}</b><span>已选分块 / 最多 8 个</span></div>
          </div>

          <div class="generation-layout">
            <section class="source-panel">
              <div class="panel-title"><span>01</span><div><h2>选择知识分块</h2><p>题目与答案只能使用所选证据。</p></div></div>
              <div class="source-selects">
                <label><span>知识库</span><select v-model="selectedKnowledgeBaseId" :disabled="loadingCatalog" @change="onKnowledgeBaseChange"><option v-if="!knowledgeBases.length" value="">暂无知识库</option><option v-for="item in knowledgeBases" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
                <label><span>文档</span><select v-model="selectedDocumentId" :disabled="!documentOptions.length" @change="loadChunks"><option v-if="!documentOptions.length" value="">暂无文档</option><option v-for="item in documentOptions" :key="item.id" :value="item.id">{{ item.title }}</option></select></label>
              </div>
              <div class="chunk-tools"><label><span>⌕</span><input v-model="chunkQuery" type="search" placeholder="筛选章节或正文"></label><b>{{ selectedChunkIndexes.length }} / 8</b></div>
              <div v-if="catalogError || chunksError" class="inline-error">{{ catalogError || chunksError }}<button type="button" @click="loadCatalog">重试</button></div>
              <div v-if="loadingChunks" class="chunk-loading">正在读取文档分块…</div>
              <div v-else-if="!filteredChunks.length" class="chunk-empty">当前文档没有可选择的分块</div>
              <div v-else class="chunk-list">
                <label v-for="chunk in filteredChunks" :key="chunk.index" :class="{ selected: selectedChunkIndexes.includes(chunk.index) }">
                  <input type="checkbox" :checked="selectedChunkIndexes.includes(chunk.index)" :disabled="selectedChunkIndexes.length >= 8 && !selectedChunkIndexes.includes(chunk.index)" @change="toggleChunk(chunk.index)">
                  <span class="chunk-index">{{ String(chunk.index + 1).padStart(2, '0') }}</span>
                  <span class="chunk-copy"><b>{{ chunk.heading_path.join(' / ') || '未标注章节' }}</b><span>{{ chunk.content }}</span><small>{{ chunk.char_count }} 字</small></span>
                </label>
              </div>
            </section>

            <section class="generator-panel">
              <div class="panel-title"><span>02</span><div><h2>设置题目</h2><p>生成后预览，再决定保存到哪个题库。</p></div></div>
              <fieldset class="setting-block"><legend>题型</legend><div class="type-selector"><button type="button" :class="{ active: generationType === 'qa' }" @click="generationType = 'qa'"><b>问答题</b><small>开放式回答与标准答案</small></button><button type="button" :class="{ active: generationType === 'single_choice' }" @click="generationType = 'single_choice'"><b>单选题</b><small>四个选项与唯一答案</small></button></div></fieldset>
              <div class="setting-row">
                <fieldset class="setting-block"><legend>每次生成</legend><div class="count-selector"><button v-for="count in 5" :key="count" type="button" :class="{ active: questionCount === count }" @click="questionCount = count">{{ count }}</button></div></fieldset>
                <fieldset class="setting-block target-setting"><legend>保存到题库 <small>生成后执行</small></legend><select v-model="targetBankId"><option v-if="!banks.length" value="">请先创建题库</option><option v-for="bank in banks" :key="bank.id" :value="bank.id">{{ bank.name }}</option></select></fieldset>
              </div>
              <fieldset class="setting-block"><legend>问题口吻</legend><div class="style-selector"><label v-for="item in styles" :key="item.value" :class="{ active: questionStyle === item.value }"><input v-model="questionStyle" type="radio" name="style" :value="item.value"><b>{{ item.label }}</b><small>{{ item.hint }}</small></label></div></fieldset>
              <label class="instruction-field"><span>补充要求 <small>可选</small></span><textarea v-model="generationInstructions" rows="3" maxlength="1000" placeholder="例如：聚焦治疗时机，难度设为专科进阶"></textarea></label>
              <button class="generate-button" type="button" :disabled="!selectedChunkIndexes.length || generating" @click="handleGenerate"><span v-if="generating" class="spinner"></span>{{ generating ? `正在生成 ${questionCount} 道题…` : `生成 ${questionCount} 道${generationType === 'qa' ? '问答题' : '单选题'}` }}</button>
              <div v-if="generationError" class="inline-error">{{ generationError }}<button type="button" @click="handleGenerate">重试</button></div>
            </section>
          </div>

          <section class="preview-section">
            <div class="preview-head"><div><span class="eyebrow">Generated preview</span><h2>生成预览</h2></div><div v-if="generatedQuestions.length" class="preview-actions"><span>{{ selectedGeneratedIndexes.size }} / {{ generatedQuestions.length }} 道已选</span><label class="select-generated-all"><input type="checkbox" :checked="allGeneratedSelected" :indeterminate="selectedGeneratedIndexes.size > 0 && !allGeneratedSelected" @change="toggleAllGeneratedQuestions">全选</label><button class="primary-button" type="button" :disabled="!targetBankId || !selectedGeneratedIndexes.size || savingGenerated" @click="saveGenerated">{{ savingGenerated ? '正在保存…' : `保存选中（${selectedGeneratedIndexes.size}）到题库` }}</button></div></div>
            <div v-if="!generatedQuestions.length" class="preview-empty"><span>Q</span><div><b>{{ generating ? '正在根据证据组织题目' : '尚未生成题目' }}</b><p>选择分块并完成设置后，题目会在这里逐条展示。</p></div></div>
            <div v-else class="generated-list">
              <article v-for="(question, index) in generatedQuestions" :key="`${question.agent_run_id}-${index}`" :class="{ selected: selectedGeneratedIndexes.has(index) }">
                <label class="generated-select"><input type="checkbox" :checked="selectedGeneratedIndexes.has(index)" :aria-label="`选择题目 ${index + 1} 保存到题库`" @change="toggleGeneratedQuestion(index)"><span>选入题库</span></label>
                <header><span>Q{{ String(index + 1).padStart(2, '0') }}</span><div><i>{{ typeLabel(question.question_type) }}</i><i>{{ question.difficulty }}</i><i>{{ question.topic || '未分类' }}</i></div></header>
                <h3>{{ question.question }}</h3>
                <div v-if="question.options.length" class="option-list"><span v-for="option in question.options" :key="option.key" :class="{ correct: option.key === question.correct_answer }"><b>{{ option.key }}</b>{{ option.text }}</span></div>
                <div class="answer-panel"><small>{{ question.question_type === 'single_choice' ? `正确答案 ${question.correct_answer}` : '标准答案' }}</small><p>{{ question.standard_answer }}</p></div>
                <details v-if="question.evidence.length"><summary>查看 {{ question.evidence.length }} 条证据依据</summary><blockquote v-for="evidence in question.evidence" :key="`${evidence.chunk_index}-${evidence.quote}`"><b>分块 #{{ evidence.chunk_index + 1 }} · {{ evidence.heading || '未标注章节' }}</b>{{ evidence.quote }}</blockquote></details>
              </article>
            </div>
          </section>
        </section>
      </main>
    </div>

    <div v-if="showBankDialog" class="dialog-backdrop" @click.self="showBankDialog = false">
      <form class="dialog" @submit.prevent="handleCreateBank">
        <div class="dialog-head"><div><span class="eyebrow">New question bank</span><h2>创建题库</h2><p>为一组同主题评测题建立独立容器。</p></div><button type="button" aria-label="关闭" @click="showBankDialog = false">×</button></div>
        <label><span>题库名称 <b>*</b></span><input v-model.trim="bankForm.name" required maxlength="500" autofocus placeholder="如：脑出血急性期处置题库"></label>
        <label><span>题库说明</span><textarea v-model.trim="bankForm.description" rows="4" maxlength="4000" placeholder="说明适用范围、评测对象或维护约定"></textarea></label>
        <div class="dialog-actions"><button class="secondary-button" type="button" @click="showBankDialog = false">取消</button><button class="primary-button" type="submit" :disabled="savingBank || !bankForm.name">{{ savingBank ? '正在创建…' : '创建题库' }}</button></div>
      </form>
    </div>

    <div v-if="showQuestionDialog" class="dialog-backdrop" @click.self="showQuestionDialog = false">
      <form class="dialog question-dialog" @submit.prevent="handleCreateQuestion">
        <div class="dialog-head"><div><span class="eyebrow">Add question</span><h2>向题库添加题目</h2><p>手工录入问答题或单项选择题。</p></div><button type="button" aria-label="关闭" @click="showQuestionDialog = false">×</button></div>
        <div class="dialog-grid"><label><span>目标题库 <b>*</b></span><select v-model="questionForm.bankId" required><option v-for="bank in banks" :key="bank.id" :value="bank.id">{{ bank.name }}</option></select></label><label><span>题型</span><select v-model="questionForm.questionType"><option value="qa">问答题</option><option value="single_choice">单选题</option></select></label></div>
        <label><span>题干 <b>*</b></span><textarea v-model.trim="questionForm.prompt" rows="3" required maxlength="8000" placeholder="输入题目内容"></textarea></label>
        <fieldset v-if="questionForm.questionType === 'single_choice'" class="manual-options"><legend>选项与正确答案</legend><label v-for="option in questionForm.options" :key="option.key"><input v-model="questionForm.correctAnswer" type="radio" name="correct-option" :value="option.key" :aria-label="`选择 ${option.key} 为正确答案`"><b>{{ option.key }}</b><input v-model.trim="option.text" type="text" required :placeholder="`选项 ${option.key}`"></label></fieldset>
        <label><span>{{ questionForm.questionType === 'qa' ? '标准答案' : '答案解析' }} <b>*</b></span><textarea v-model.trim="questionForm.standardAnswer" rows="4" required maxlength="16000" placeholder="输入准确、可核验的标准答案"></textarea></label>
        <div class="dialog-actions"><button class="secondary-button" type="button" @click="showQuestionDialog = false">取消</button><button class="primary-button" type="submit" :disabled="savingQuestion || !questionForm.bankId">{{ savingQuestion ? '正在保存…' : '加入题库' }}</button></div>
      </form>
    </div>

    <div v-if="notice" class="toast" :class="notice.kind" role="status"><i></i>{{ notice.text }}</div>
  </div>
</template>
