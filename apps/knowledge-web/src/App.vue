<script setup lang="ts">
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ChunkSemanticMetadata from './components/ChunkSemanticMetadata.vue'
import ChunkRunArtifacts from './components/ChunkRunArtifacts.vue'
import ChunkingMethodDetails from './components/ChunkingMethodDetails.vue'
import { PlatformSidebar } from '@medical-rag/platform-ui'

type Tab = 'welcome' | 'documents' | 'configurations' | 'knowledge' | 'tasks'
type NavigationTab = Exclude<Tab, 'welcome'>
type NoticeKind = 'success' | 'error' | 'info'

interface Attachment {
  id: string
  name: string
  kind?: 'pdf' | 'markdown'
  source?: 'upload' | 'mineru' | string
  mediaType: string
  sizeBytes: number
  createdAt?: string
}

interface LibraryEntry {
  id: string
  title: string
  itemType: string
  authors: string[]
  year?: number
  doi?: string
  abstract?: string
  attachments: Attachment[]
  createdAt?: string
}

interface ApiDocument {
  id: string
  title: string
  doi: string | null
  authors: Array<string | Record<string, unknown>>
  abstract: string | null
  publication_year: number | null
  metadata: Record<string, unknown>
  files: Array<{
    id: string
    original_name: string
    kind: 'pdf' | 'markdown'
    source: string
    media_type: string
    size_bytes: number
    created_at: string
  }>
  created_at: string
}

interface DocumentDeletionSummary {
  total_count: number
  protected_count: number
  processing_count: number
  deletable_count: number
}

interface ChunkingMethod {
  id: string
  name: string
  strategy: string
  description?: string
  color: string
  config: Record<string, unknown>
  created_at: string
}

interface EmbeddingModel {
  id: string
  name: string
  provider: string
  model_name: string
  dimension?: number
  base_url?: string
  config: Record<string, unknown>
  created_at: string
}

interface ApiKnowledgeBase {
  id: string
  name: string
  description?: string
  status: string
  chunking_method: ChunkingMethod
  embedding_model: EmbeddingModel
  documents: ApiDocument[]
  created_at: string
  updated_at: string
}

interface KnowledgeBase extends Omit<ApiKnowledgeBase, 'chunking_method' | 'embedding_model' | 'documents'> {
  chunkingMethod: ChunkingMethod
  embeddingModel: EmbeddingModel
  documents: LibraryEntry[]
}

interface ChunkPreviewItem {
  index: number
  content: string
  embedding_text: string
  char_start: number
  char_end: number
  char_count: number
  heading_path: string[]
  metadata?: Record<string, unknown>
}

interface ChunkPreviewResponse {
  chunks: ChunkPreviewItem[]
  stats: ChunkPreviewStats
  document_title?: string
  run_id?: string
  run_metadata?: Record<string, unknown>
  processing_report?: { quality_gate_passed: boolean }
}

interface ChunkPreviewStats {
  total: number
  average_chars: number
  minimum_chars: number
  maximum_chars: number
  too_short: number
  too_long: number
}

interface DashboardDocument {
  id: string
  title: string
  item_type: string
  attachment_count: number
  chunk_count: number
  has_pdf: boolean
  has_markdown: boolean
  created_at: string
}

interface DashboardData {
  documents_total: number
  documents_this_week: number
  markdown_ready: number
  knowledge_bases: number
  persisted_chunks: number
  recent_documents: DashboardDocument[]
}

/**
 * Normalized activity returned by the backend task timeline.
 * A UUID id denotes a retryable durable Worker row; synthetic history records
 * use string ids. Null progress is an explicit indeterminate state.
 */
interface ProcessingTask {
  id: string
  resource_id: string
  resource_type: 'document' | 'knowledge_base' | 'embedding_model'
  name: string
  operation: string
  processor: string
  status: string
  phase: string
  progress: number | null
  message?: string
  error_message?: string
  attempt_count: number
  max_attempts: number
  has_chunking_result?: boolean
  created_at: string
  started_at?: string
  finished_at?: string
  updated_at?: string
}

interface KnowledgeSearchHit {
  chunk_id: string
  document_id: string
  document_title: string
  content: string
  heading_path: string[]
  metadata?: Record<string, unknown>
  score: number
  citation: { title: string; heading?: string; pages?: Array<number | null> }
}

// Vite proxies this stable prefix to the knowledge API in development. Keeping
// API paths relative also allows the production site to live below /knowledge/.
const apiBase = '/api/knowledge'

// The welcome page is deliberately absent from the sidebar. Clicking the brand
// is the single, predictable way to return to it.
const route = useRoute()
const router = useRouter()
const tabPaths: Record<Tab, string> = {
  welcome: '/',
  documents: '/documents',
  configurations: '/configurations',
  knowledge: '/knowledge-bases',
  tasks: '/tasks',
}
const activeTab = computed<Tab>(() => {
  const routeName = String(route.name || 'welcome')
  return (['documents', 'configurations', 'knowledge', 'tasks'].includes(routeName)
    ? routeName
    : 'welcome') as Tab
})
// Navigation and service reachability are independent: routes remain usable so
// an offline API can show explicit recovery states instead of a blank screen.
const tabs: Array<{ id: NavigationTab; label: string; path: string }> = [
  { id: 'documents', label: '文献管理', path: tabPaths.documents },
  { id: 'configurations', label: '模型注册', path: tabPaths.configurations },
  { id: 'knowledge', label: '知识库管理', path: tabPaths.knowledge },
  { id: 'tasks', label: '解析任务', path: tabPaths.tasks },
]

// API-backed collections start empty so an unavailable service can never expose
// sample records that look like persisted medical literature.
const entries = ref<LibraryEntry[]>([])
const selectedEntryId = ref<string | null>(null)
const expandedEntryIds = ref<Set<string>>(new Set())
const isEntriesLoading = ref(true)
const entriesLoadError = ref('')
const documentDeletionSummary = ref<DocumentDeletionSummary | null>(null)
const isLoadingDeletionSummary = ref(false)
const isBulkDeletingDocuments = ref(false)
const searchQuery = ref('')
const itemFilter = ref('all')
const isCreateDialogOpen = ref(false)
const isSavingEntry = ref(false)
const isUploading = ref(false)
const isUploadDialogOpen = ref(false)
const uploadDestination = ref<'new' | 'existing'>('new')
const uploadTargetId = ref<string>('')
const selectedUploadFiles = ref<File[]>([])
const uploadInput = ref<HTMLInputElement | null>(null)
const previewAttachment = ref<Attachment | null>(null)
const markdownPreviewHtml = ref('')
const markdownPreviewError = ref('')
const isMarkdownPreviewLoading = ref(false)
let markdownPreviewController: AbortController | null = null
const movingAttachment = ref<Attachment | null>(null)
const moveTargetId = ref<string>('')
const isMoving = ref(false)
const deletingAttachmentId = ref<string | null>(null)
const convertingDocumentId = ref<string | null>(null)
const isDeletingEntry = ref(false)
const titleInput = ref<HTMLInputElement | null>(null)
const notice = ref<{ kind: NoticeKind; message: string } | null>(null)
const chunkingMethods = ref<ChunkingMethod[]>([])
const selectedChunkingMethod = ref<ChunkingMethod | null>(null)
const embeddingModels = ref<EmbeddingModel[]>([])
const knowledgeBases = ref<KnowledgeBase[]>([])
const isConfigurationsLoading = ref(true)
const configurationsLoadError = ref('')
const isKnowledgeBasesLoading = ref(true)
const knowledgeBasesLoadError = ref('')
const dashboard = ref<DashboardData | null>(null)
const isDashboardLoading = ref(true)
const dashboardLoadError = ref('')

// Task records are always server-backed. The timer is deliberately kept outside
// Vue reactivity because it is only a scheduling handle, not renderable state.
const tasks = ref<ProcessingTask[]>([])
const isTasksLoading = ref(true)
const tasksLoadError = ref('')
const selectedTaskIds = ref<Set<string>>(new Set())
const deletingTaskIds = ref<Set<string>>(new Set())
let taskPollTimer: ReturnType<typeof setTimeout> | null = null
const isSavingChunkingMethod = ref(false)
const isSavingEmbeddingModel = ref(false)
const deletingConfigurationId = ref<string | null>(null)
const isChunkingDialogOpen = ref(false)
const chunkingStep = ref(1)
const chunkingPreviewDocumentId = ref('')
const chunkingPreview = ref<ChunkPreviewItem[]>([])
const chunkingPreviewStats = ref<ChunkPreviewStats | null>(null)
const chunkingPreviewError = ref('')
const isChunkingPreviewLoading = ref(false)
const chunkingPreviewSignature = ref('')
const chunkingProgressMessage = ref('')
const knowledgeChunkProgressMessage = ref('')
const guidelineEngine = ref<{ api_key_configured: boolean; model: string; topic_count: number } | null>(null)
const guidelineTaskResult = ref<ChunkPreviewResponse | null>(null)
let recipePreviewController: AbortController | null = null
let knowledgePreviewController: AbortController | null = null
const isEmbeddingDialogOpen = ref(false)
const isKnowledgeDialogOpen = ref(false)
const isSavingKnowledgeBase = ref(false)
const selectedKnowledgeBase = ref<KnowledgeBase | null>(null)
const selectedKnowledgeDocument = ref<LibraryEntry | null>(null)
const chunkPreview = ref<ChunkPreviewItem[]>([])
const isChunkPreviewLoading = ref(false)
const chunkPreviewError = ref('')
const downloadingModelId = ref<string | null>(null)
const buildingKnowledgeBaseId = ref<string | null>(null)
const deletingKnowledgeBaseId = ref<string | null>(null)
const knowledgeSearchQuery = ref('')
const knowledgeSearchResults = ref<KnowledgeSearchHit[]>([])
const knowledgeSearchError = ref('')
const isKnowledgeSearching = ref(false)

const createForm = reactive({
  title: '',
  itemType: 'journalArticle',
  authors: '',
  year: undefined as number | undefined,
  doi: '',
  abstract: '',
})

const chunkingForm = reactive({
  name: '',
  strategy: 'heading',
  description: '',
  color: '#5f918c',
  targetChars: 800,
  maxChars: 1200,
  minChars: 200,
  overlap: 120,
  maxChunks: 2000,
  headingLevels: [1, 2, 3, 4] as number[],
  mergeShortSections: true,
  prependHeadingPath: true,
  preserveTables: true,
  preserveCodeBlocks: true,
  batchTargetChars: 2600,
  batchMaxChars: 4200,
  batchTargetAtoms: 12,
  batchMaxAtoms: 18,
  contextBefore: 2,
  contextAfter: 2,
  reviewMaxChars: 500,
  summaryEnabled: true,
  summaryMaxChars: 300,
  semanticTimeout: 180,
  semanticAttempts: 3,
  semanticConcurrency: 1,
  cacheEnabled: true,
  requireQualityGate: true,
})

const embeddingForm = reactive({
  name: 'MedCPT（本地双编码器）',
  provider: 'medcpt',
  modelName: 'ncbi/MedCPT-Article-Encoder',
  dimension: 768 as number | undefined,
  baseUrl: '',
})

const knowledgeForm = reactive({
  name: '',
  description: '',
  documentIds: [] as string[],
  chunkingMethodId: '',
  embeddingModelId: '',
})

const filteredEntries = computed(() => {
  const query = searchQuery.value.trim().toLocaleLowerCase()
  return entries.value.filter((entry) => {
    const matchesType = itemFilter.value === 'all' || entry.itemType === itemFilter.value
    const haystack = [entry.title, entry.doi, entry.authors.join(' ')].filter(Boolean).join(' ').toLocaleLowerCase()
    return matchesType && (!query || haystack.includes(query))
  })
})

const selectedEntry = computed(() => entries.value.find((entry) => entry.id === selectedEntryId.value) ?? null)
// Only queued/running are Worker-owned active states. Knowledge-base "building"
// entries are historical projections and currently do not drive this poller.
const activeTasks = computed(() => tasks.value.filter((task) => ['queued', 'running'].includes(task.status)))
const removableTasks = computed(() => tasks.value.filter(isRemovableTask))
const allRemovableTasksSelected = computed(() => (
  removableTasks.value.length > 0
  && removableTasks.value.every((task) => selectedTaskIds.value.has(task.id))
))
const selectedEntryActiveTask = computed(() => (
  selectedEntry.value ? activeTaskForDocument(selectedEntry.value.id) : undefined
))
const selectedEntryKnowledgeBases = computed(() => {
  if (!selectedEntry.value) return []
  // Knowledge-base membership protects the whole entry, including its physical
  // PDF/MD files, so the inspector can explain why deletion is unavailable.
  return knowledgeBases.value.filter((library) => (
    library.documents.some((document) => document.id === selectedEntry.value?.id)
  ))
})
const totalAttachments = computed(() => entries.value.reduce((total, entry) => total + entry.attachments.length, 0))
const markdownReadyEntries = computed(() => entries.value.filter((entry) => entry.attachments.some((file) => attachmentKind(file) === 'MD')))
const recentDocuments = computed(() => dashboard.value?.recent_documents ?? [])
const moveTargets = computed(() => entries.value.filter((entry) => (
  entry.id !== selectedEntryId.value
  && (!movingAttachment.value || !entry.attachments.some((file) => attachmentKind(file) === attachmentKind(movingAttachment.value!)))
)))

function itemTypeLabel(type: string): string {
  return ({ journalArticle: '期刊论文', guideline: '临床指南', report: '报告', bookSection: '书籍章节', document: '文档' } as Record<string, string>)[type] || '文献条目'
}

function formatFileSize(size: number): string {
  if (size < 1024 * 1024) return `${Math.max(1, Math.round(size / 1024))} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function normalizeAuthor(author: string | Record<string, unknown>): string {
  if (typeof author === 'string') return author
  return String(author.name || [author.family, author.given].filter(Boolean).join(' ') || '未知作者')
}

function mapApiDocument(document: ApiDocument): LibraryEntry {
  // Convert snake_case transport fields once at the API boundary. Templates and
  // interaction code can then use one predictable client-side domain shape.
  const metadata = document.metadata || {}
  return {
    id: document.id,
    title: document.title,
    itemType: String(metadata.item_type || 'document'),
    authors: document.authors.map(normalizeAuthor),
    year: document.publication_year || undefined,
    doi: document.doi || undefined,
    abstract: document.abstract || undefined,
    attachments: (document.files || []).map((file) => ({
      id: file.id,
      name: file.original_name,
      kind: file.kind,
      source: file.source,
      mediaType: file.media_type,
      sizeBytes: file.size_bytes,
      createdAt: file.created_at,
    })),
    createdAt: document.created_at,
  }
}

function mapKnowledgeBase(item: ApiKnowledgeBase): KnowledgeBase {
  return {
    id: item.id,
    name: item.name,
    description: item.description,
    status: item.status,
    chunkingMethod: item.chunking_method,
    embeddingModel: item.embedding_model,
    documents: item.documents.map(mapApiDocument),
    created_at: item.created_at,
    updated_at: item.updated_at,
  }
}

function strategyLabel(strategy: string): string {
  return ({ heading: '标题层级', recursive: '递归字符', fixed: '固定长度', guideline_semantic: '指南原子知识（DeepSeek）' } as Record<string, string>)[strategy] || strategy
}

function methodSize(method: ChunkingMethod): string | number {
  const batching = method.config.batching as { max_chars?: number } | undefined
  return method.strategy === 'guideline_semantic' ? batching?.max_chars || 4200 : method.config.max_chars as number || '—'
}

function methodContext(method: ChunkingMethod): string | number {
  const batching = method.config.batching as { context_before_atoms?: number; context_after_atoms?: number } | undefined
  return method.strategy === 'guideline_semantic' ? `${batching?.context_before_atoms ?? 2} / ${batching?.context_after_atoms ?? 2} 原子` : method.config.overlap as number || 0
}

function providerLabel(provider: string): string {
  return ({ medcpt: 'MedCPT 本地', local: '本地服务', openai: 'OpenAI 兼容', huggingface: 'Hugging Face', ollama: 'Ollama' } as Record<string, string>)[provider] || provider
}

function navigateTo(tab: Tab): void {
  // Router navigation preserves browser history, so back/forward returns to the
  // previous workspace page instead of only changing an in-memory tab value.
  if (route.path !== tabPaths[tab]) void router.push(tabPaths[tab])
}

function recentDocumentMeta(document: DashboardDocument): string {
  const type = itemTypeLabel(document.item_type)
  if (document.chunk_count) return `${type} · ${document.chunk_count} 个持久化知识块`
  return `${type} · ${document.attachment_count} 个附件`
}

function recentDocumentState(document: DashboardDocument): string {
  if (document.has_markdown) return 'MD 已就绪'
  if (document.has_pdf) return '等待转换'
  return '暂无附件'
}

function taskStatusLabel(status: string): string {
  // Keep raw unknown states visible as a forward-compatible fallback instead of
  // incorrectly labelling a state introduced by a newer backend.
  return ({
    completed: '已完成',
    superseded: '已被替代',
    building: '构建中',
    configured: '已配置',
    queued: '等待中',
    running: '处理中',
    failed: '失败',
    cancelled: '已取消',
  } as Record<string, string>)[status] || status
}

function activeTaskForDocument(documentId: string): ProcessingTask | undefined {
  // Resource identity, not title or array position, connects a queue row to the
  // document inspector and protects it from duplicate conversion actions.
  return tasks.value.find((task) => (
    task.resource_type === 'document'
    && task.resource_id === documentId
    && ['queued', 'running'].includes(task.status)
  ))
}

function isDurableTask(task: ProcessingTask): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(task.id)
}

function isDeletableTask(task: ProcessingTask): boolean {
  return isDurableTask(task) && ['completed', 'failed', 'cancelled'].includes(task.status)
}

function isRemovableTask(task: ProcessingTask): boolean {
  return isDeletableTask(task) || task.id.startsWith('document-version:')
}

function toggleTaskSelection(taskId: string): void {
  const next = new Set(selectedTaskIds.value)
  next.has(taskId) ? next.delete(taskId) : next.add(taskId)
  selectedTaskIds.value = next
}

function toggleAllRemovableTasks(): void {
  selectedTaskIds.value = allRemovableTasksSelected.value
    ? new Set()
    : new Set(removableTasks.value.map((task) => task.id))
}

function conversionButtonLabel(entry: LibraryEntry): string {
  // Reflect the server task rather than the short POST request. Once enqueued,
  // convertingDocumentId is cleared but the durable task continues to progress.
  if (convertingDocumentId.value === entry.id) return '正在创建任务…'
  const task = activeTaskForDocument(entry.id)
  if (task?.status === 'queued') return '等待 MinerU'
  if (task?.status === 'running') {
    return task.progress === null ? 'MinerU 解析中…' : `MinerU ${task.progress}%`
  }
  return 'PDF转MD'
}

function formatDateTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '时间未知'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function toggleEntry(entryId: string): void {
  const next = new Set(expandedEntryIds.value)
  next.has(entryId) ? next.delete(entryId) : next.add(entryId)
  expandedEntryIds.value = next
}

function selectEntry(entryId: string): void {
  selectedEntryId.value = entryId
}

function showNotice(kind: NoticeKind, message: string): void {
  notice.value = { kind, message }
  window.setTimeout(() => {
    if (notice.value?.message === message) notice.value = null
  }, 5000)
}

async function responseError(response: Response): Promise<string> {
  // FastAPI exposes user-safe messages in detail. Non-JSON proxy/network errors
  // fall back to a stable message rather than surfacing parsing exceptions.
  try {
    const body = await response.json()
    return typeof body.detail === 'string' ? body.detail : '请求未完成，请重试'
  } catch {
    return '请求未完成，请检查服务连接后重试'
  }
}

async function loadEntries(preferredId?: string | null): Promise<void> {
  isEntriesLoading.value = true
  entriesLoadError.value = ''
  try {
    const response = await fetch(`${apiBase}/api/v1/documents?page_size=100`)
    if (!response.ok) throw new Error(await responseError(response))
    const body = await response.json() as { items: ApiDocument[] }
    entries.value = body.items.map(mapApiDocument)
    const nextId = preferredId && entries.value.some((entry) => entry.id === preferredId)
      ? preferredId
      : entries.value[0]?.id ?? null
    selectedEntryId.value = nextId
    if (nextId) expandedEntryIds.value = new Set([...expandedEntryIds.value, nextId])
  } catch (error) {
    // Clear stale records when the request fails; showing old data as current is
    // more dangerous than an explicit recoverable error in a medical workspace.
    entries.value = []
    selectedEntryId.value = null
    entriesLoadError.value = error instanceof Error ? error.message : '文献数据加载失败'
  } finally {
    isEntriesLoading.value = false
  }
}

async function loadDocumentDeletionSummary(): Promise<void> {
  isLoadingDeletionSummary.value = true
  try {
    const response = await fetch(`${apiBase}/api/v1/documents/deletion-summary`)
    if (!response.ok) throw new Error(await responseError(response))
    documentDeletionSummary.value = await response.json() as DocumentDeletionSummary
  } catch {
    documentDeletionSummary.value = null
  } finally {
    isLoadingDeletionSummary.value = false
  }
}

async function loadConfigurations(): Promise<void> {
  isConfigurationsLoading.value = true
  configurationsLoadError.value = ''
  try {
    const [chunkingResponse, embeddingResponse] = await Promise.all([
      fetch(`${apiBase}/api/v1/configurations/chunking-methods`),
      fetch(`${apiBase}/api/v1/configurations/embedding-models`),
    ])
    if (!chunkingResponse.ok) throw new Error(await responseError(chunkingResponse))
    if (!embeddingResponse.ok) throw new Error(await responseError(embeddingResponse))
    chunkingMethods.value = await chunkingResponse.json() as ChunkingMethod[]
    embeddingModels.value = await embeddingResponse.json() as EmbeddingModel[]
    const engineResponse = await fetch(`${apiBase}/api/v1/configurations/guideline-chunker`)
    if (engineResponse.ok) guidelineEngine.value = await engineResponse.json()
  } catch (error) {
    chunkingMethods.value = []
    embeddingModels.value = []
    configurationsLoadError.value = error instanceof Error ? error.message : '方法与模型配置加载失败'
  } finally {
    isConfigurationsLoading.value = false
  }
}

async function loadKnowledgeBases(preferredId?: string): Promise<void> {
  isKnowledgeBasesLoading.value = true
  knowledgeBasesLoadError.value = ''
  try {
    const response = await fetch(`${apiBase}/api/v1/knowledge-bases`)
    if (!response.ok) throw new Error(await responseError(response))
    knowledgeBases.value = (await response.json() as ApiKnowledgeBase[]).map(mapKnowledgeBase)
    if (preferredId) {
      selectedKnowledgeBase.value = knowledgeBases.value.find((item) => item.id === preferredId) || null
    } else if (selectedKnowledgeBase.value) {
      selectedKnowledgeBase.value = knowledgeBases.value.find((item) => item.id === selectedKnowledgeBase.value?.id) || null
    }
  } catch (error) {
    knowledgeBases.value = []
    selectedKnowledgeBase.value = null
    knowledgeBasesLoadError.value = error instanceof Error ? error.message : '知识库加载失败'
  } finally {
    isKnowledgeBasesLoading.value = false
  }
}

async function loadDashboard(): Promise<void> {
  isDashboardLoading.value = true
  dashboardLoadError.value = ''
  try {
    const response = await fetch(`${apiBase}/api/v1/dashboard`)
    if (!response.ok) throw new Error(await responseError(response))
    dashboard.value = await response.json() as DashboardData
  } catch (error) {
    dashboard.value = null
    dashboardLoadError.value = error instanceof Error ? error.message : '工作台数据加载失败'
  } finally {
    isDashboardLoading.value = false
  }
}

function stopTaskPolling(): void {
  // Clearing before every reschedule guarantees at most one outstanding timer,
  // even when refresh, visibilitychange and enqueue complete close together.
  if (taskPollTimer) clearTimeout(taskPollTimer)
  taskPollTimer = null
}

function scheduleTaskPolling(): void {
  /**
   * Schedule the next one-shot read only while the queue has live work.
   * A recursive setTimeout is used instead of setInterval so slow requests can
   * never overlap. The next timeout is installed only after the request settles.
   */
  stopTaskPolling()
  if (!activeTasks.value.length) return
  // Poll quickly while visible, but reduce background traffic when the user
  // switches away. A persisted task never depends on the browser staying open.
  const delay = document.visibilityState === 'visible' ? 2000 : 10000
  taskPollTimer = setTimeout(() => void loadTasks(true), delay)
}

async function loadTasks(background = false): Promise<void> {
  /**
   * Refresh the complete activity timeline and reconcile terminal transitions.
   * Foreground/manual loads own visible loading and error states. Silent polling
   * preserves the last valid list during transient failures, preventing flicker.
   */
  stopTaskPolling()
  if (!background) isTasksLoading.value = true
  if (!background) tasksLoadError.value = ''
  // Capture identity before replacement so a running -> terminal transition can
  // trigger dependent data refreshes without comparing every task field.
  const previouslyActive = new Set(activeTasks.value.map((task) => task.id))
  try {
    const response = await fetch(`${apiBase}/api/v1/processing-tasks?limit=100`)
    if (!response.ok) throw new Error(await responseError(response))
    const nextTasks = await response.json() as ProcessingTask[]
    tasks.value = nextTasks
    const removableIds = new Set(nextTasks.filter(isRemovableTask).map((task) => task.id))
    selectedTaskIds.value = new Set([...selectedTaskIds.value].filter((id) => removableIds.has(id)))
    const taskJustFinished = nextTasks.some((task) => (
      previouslyActive.has(task.id) && ['completed', 'failed', 'cancelled'].includes(task.status)
    ))
    if (taskJustFinished) {
      // Completion changes both the attachment slots and dashboard counts.
      await Promise.all([loadEntries(selectedEntryId.value), loadDashboard(), loadKnowledgeBases()])
    }
  } catch (error) {
    // Background connectivity loss must not erase previously rendered durable
    // history. A manual load, however, needs an explicit retry state.
    if (!background) {
      tasks.value = []
      tasksLoadError.value = error instanceof Error ? error.message : '处理记录加载失败'
    }
  } finally {
    if (!background) isTasksLoading.value = false
    scheduleTaskPolling()
  }
}

async function retryTask(task: ProcessingTask): Promise<void> {
  // The server validates status, remaining attempts, and duplicate active work.
  // Replacing the row locally gives immediate feedback before the next poll.
  try {
    const response = await fetch(`${apiBase}/api/v1/processing-tasks/${task.id}/retry`, { method: 'POST' })
    if (!response.ok) throw new Error(await responseError(response))
    const retried = await response.json() as ProcessingTask
    tasks.value = [retried, ...tasks.value.filter((item) => item.id !== retried.id)]
    scheduleTaskPolling()
    showNotice('success', '失败任务已重新加入队列')
  } catch (error) {
    showNotice('error', error instanceof Error ? error.message : '任务重试失败')
  }
}

function markTasksDeleting(taskIds: string[], deleting: boolean): void {
  const next = new Set(deletingTaskIds.value)
  for (const taskId of taskIds) deleting ? next.add(taskId) : next.delete(taskId)
  deletingTaskIds.value = next
}

function removeTasksFromView(taskIds: string[]): void {
  const removed = new Set(taskIds)
  tasks.value = tasks.value.filter((task) => !removed.has(task.id))
  selectedTaskIds.value = new Set([...selectedTaskIds.value].filter((id) => !removed.has(id)))
}

async function deleteTask(task: ProcessingTask): Promise<void> {
  if (!isRemovableTask(task)) return
  const isHistory = task.id.startsWith('document-version:')
  const action = isHistory ? '隐藏历史记录' : '删除任务记录'
  if (!window.confirm(`确定${action}“${task.name}”吗？Markdown 文件、版本和知识库数据不会被删除。`)) return
  markTasksDeleting([task.id], true)
  try {
    const response = await fetch(`${apiBase}/api/v1/processing-tasks/remove`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ activity_ids: [task.id] }),
    })
    if (!response.ok) throw new Error(await responseError(response))
    removeTasksFromView([task.id])
    showNotice('success', isHistory ? '历史记录已隐藏' : '任务记录已删除')
  } catch (error) {
    showNotice('error', error instanceof Error ? error.message : '记录移除失败')
  } finally {
    markTasksDeleting([task.id], false)
  }
}

async function deleteSelectedTasks(): Promise<void> {
  const taskIds = [...selectedTaskIds.value]
  if (!taskIds.length) return
  if (!window.confirm(`确定移除选中的 ${taskIds.length} 条记录吗？Markdown 文件、版本和知识库数据不会被删除。`)) return
  markTasksDeleting(taskIds, true)
  try {
    const response = await fetch(`${apiBase}/api/v1/processing-tasks/remove`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ activity_ids: taskIds }),
    })
    if (!response.ok) throw new Error(await responseError(response))
    const result = await response.json() as { removed_ids: string[]; removed_count: number }
    removeTasksFromView(result.removed_ids)
    showNotice('success', `已移除 ${result.removed_count} 条记录`)
  } catch (error) {
    showNotice('error', error instanceof Error ? error.message : '批量删除任务失败')
  } finally {
    markTasksDeleting(taskIds, false)
  }
}

async function refreshRealData(): Promise<void> {
  // Top-level datasets do not depend on one another, so load them concurrently.
  // Individual loaders own their own error states and never inject demo data.
  await Promise.all([
    loadEntries(selectedEntryId.value),
    loadDocumentDeletionSummary(),
    loadConfigurations(),
    loadKnowledgeBases(),
    loadDashboard(),
    loadTasks(),
  ])
}

function openCreateDialog(): void {
  isCreateDialogOpen.value = true
  nextTick(() => titleInput.value?.focus())
}

function closeCreateDialog(): void {
  if (!isSavingEntry.value) isCreateDialogOpen.value = false
}

function resetCreateForm(): void {
  createForm.title = ''
  createForm.itemType = 'journalArticle'
  createForm.authors = ''
  createForm.year = undefined
  createForm.doi = ''
  createForm.abstract = ''
}

async function createEntry(): Promise<void> {
  if (!createForm.title.trim()) {
    showNotice('error', '请填写条目标题后再保存')
    titleInput.value?.focus()
    return
  }
  isSavingEntry.value = true
  try {
    const response = await fetch(`${apiBase}/api/v1/documents`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: createForm.title.trim(),
        doi: createForm.doi.trim() || null,
        authors: createForm.authors.split(/[，,]/).map((name) => name.trim()).filter(Boolean),
        abstract: createForm.abstract.trim() || null,
        publication_year: createForm.year || null,
        metadata: { item_type: createForm.itemType, import_mode: 'manual' },
      }),
    })
    if (!response.ok) throw new Error(await responseError(response))
    const entry = mapApiDocument(await response.json() as ApiDocument)
    entries.value = [entry, ...entries.value]
    selectedEntryId.value = entry.id
    isCreateDialogOpen.value = false
    resetCreateForm()
    await loadDashboard()
    showNotice('success', '文献条目已创建，可继续添加附件')
  } catch (error) {
    showNotice('error', error instanceof Error ? error.message : '创建条目失败，请重试')
  } finally {
    isSavingEntry.value = false
  }
}

function attachmentKind(attachment: Attachment): string {
  return attachment.kind === 'markdown' || attachment.name.toLocaleLowerCase().endsWith('.md') ? 'MD' : 'PDF'
}

function attachmentContentUrl(attachment: Attachment, download = false): string {
  return `${apiBase}/api/v1/documents/attachments/${attachment.id}/content${download ? '?download=true' : ''}`
}

function setUploadDestination(destination: 'new' | 'existing'): void {
  uploadDestination.value = destination
  if (destination === 'existing') {
    const preferredId = selectedEntryId.value
    uploadTargetId.value = entries.value.some((entry) => entry.id === preferredId)
      ? preferredId || ''
      : entries.value[0]?.id || ''
  } else {
    uploadTargetId.value = ''
  }
}

function openUploadDialog(destination: 'new' | 'existing' = 'new'): void {
  setUploadDestination(destination)
  selectedUploadFiles.value = []
  isUploadDialogOpen.value = true
}

function closeUploadDialog(): void {
  if (!isUploading.value) isUploadDialogOpen.value = false
}

function chooseUploadFiles(): void {
  uploadInput.value?.click()
}

function onUploadFiles(event: Event): void {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  const accepted = files.filter((file) => /\.(pdf|md)$/i.test(file.name) && file.size <= 100 * 1024 * 1024)
  if (accepted.length !== files.length) showNotice('error', '已忽略非 PDF/MD 或超过 100 MB 的文件')
  selectedUploadFiles.value = accepted
  input.value = ''
}

function removeUploadFile(index: number): void {
  selectedUploadFiles.value = selectedUploadFiles.value.filter((_, itemIndex) => itemIndex !== index)
}

async function submitUpload(): Promise<void> {
  // Existing entries are version slots, not arbitrary file buckets. Detect
  // replacement on the client so the user explicitly confirms data loss.
  if (!selectedUploadFiles.value.length) {
    showNotice('error', '请先选择 PDF 或 Markdown 文件')
    return
  }
  if (uploadDestination.value === 'existing' && !uploadTargetId.value) {
    showNotice('error', '请选择要添加附件的条目')
    return
  }
  let replaceExisting = false
  if (uploadDestination.value === 'existing') {
    const incomingKinds = selectedUploadFiles.value.map((file) => file.name.toLowerCase().endsWith('.md') ? 'MD' : 'PDF')
    if (new Set(incomingKinds).size !== incomingKinds.length) {
      showNotice('error', '同一条目一次最多上传一个 PDF 和一个 MD')
      return
    }
    const target = entries.value.find((entry) => entry.id === uploadTargetId.value)
    const conflicts = incomingKinds.filter((kind) => target?.attachments.some((file) => attachmentKind(file) === kind))
    if (conflicts.length) {
      const labels = [...new Set(conflicts)].join('、')
      if (!window.confirm(`该条目已经有 ${labels} 文件。继续上传会替代原文件，并清理磁盘上的旧文件。是否继续？`)) return
      replaceExisting = true
    }
  }
  const formData = new FormData()
  selectedUploadFiles.value.forEach((file) => formData.append('files', file))
  const endpoint = uploadDestination.value === 'new'
    ? `${apiBase}/api/v1/documents/from-files`
    : `${apiBase}/api/v1/documents/${uploadTargetId.value}/files/batch${replaceExisting ? '?replace_existing=true' : ''}`
  const uploadedFileCount = selectedUploadFiles.value.length
  const destination = uploadDestination.value
  isUploading.value = true
  try {
    const response = await fetch(endpoint, { method: 'POST', body: formData })
    if (!response.ok) throw new Error(await responseError(response))
    const result = await response.json() as ApiDocument | ApiDocument[]
    const preferredId = Array.isArray(result) ? result[0]?.id : result.id
    // The upload request is the transaction boundary. Close the dialog as soon
    // as it succeeds instead of making the user wait for three secondary reads.
    isUploadDialogOpen.value = false
    selectedUploadFiles.value = []
    uploadTargetId.value = ''
    showNotice(
      'success',
      destination === 'new'
        ? `已按文件名创建 ${uploadedFileCount} 个条目，并识别其他元数据`
        : replaceExisting ? '文件已替代并更新 MD 版本' : `已添加 ${uploadedFileCount} 个附件`,
    )
    await nextTick()
    await Promise.all([loadEntries(preferredId), loadDashboard(), loadTasks()])
  } catch (error) {
    showNotice('error', error instanceof Error ? error.message : '文件上传失败，请重试')
  } finally {
    isUploading.value = false
  }
}

async function convertPdfToMarkdown(entry: LibraryEntry): Promise<void> {
  /**
   * Validate the visible document state, then enqueue—never execute—conversion.
   * The returned task is inserted immediately so controls lock without waiting
   * for the next timeline fetch. PostgreSQL/Worker processing then survives tab
   * closure, navigation and frontend refreshes.
   */
  if (activeTaskForDocument(entry.id)) {
    showNotice('info', '该文献已有 PDF 转换任务正在处理中')
    return
  }
  const pdf = entry.attachments.find((file) => attachmentKind(file) === 'PDF')
  if (!pdf) {
    showNotice('error', '该条目没有可转换的 PDF 文件')
    return
  }
  const hasMarkdown = entry.attachments.some((file) => attachmentKind(file) === 'MD')
  if (hasMarkdown && !window.confirm('该条目已经有 MD 文件。MinerU 转换完成后会替代现有 MD，并清理旧文件。是否继续？')) return
  convertingDocumentId.value = entry.id
  try {
    const response = await fetch(
      `${apiBase}/api/v1/documents/${entry.id}/convert-pdf${hasMarkdown ? '?replace_existing=true' : ''}`,
      { method: 'POST' },
    )
    if (!response.ok) throw new Error(await responseError(response))
    const task = await response.json() as ProcessingTask
    // Filtering first also handles the backend returning an already-active task
    // after a duplicate/racing enqueue, without rendering it twice.
    tasks.value = [task, ...tasks.value.filter((item) => item.id !== task.id)]
    scheduleTaskPolling()
    showNotice('success', hasMarkdown ? '重新生成任务已加入队列' : 'PDF 转 MD 任务已加入队列')
  } catch (error) {
    showNotice('error', error instanceof Error ? error.message : 'PDF 转 MD 任务创建失败')
  } finally {
    convertingDocumentId.value = null
  }
}

function stripMarkdownFrontmatter(markdown: string): string {
  return markdown.replace(/^\uFEFF?---\s*\r?\n[\s\S]*?\r?\n---\s*(?:\r?\n|$)/, '')
}

function normalizeMarkdownForDisplay(markdown: string): string {
  // MinerU and many Chinese guidelines emit compact numbered items such as
  // `1.院前急救`. CommonMark requires a space after the marker, so normalize
  // that display-only form without changing the stored chunk or its offsets.
  return stripMarkdownFrontmatter(markdown).replace(/^(\s*)(\d+)\.(?=\S)/gm, '$1$2. ')
}

function renderMarkdownHtml(markdown: string, emptyMessage = '此分块暂无正文。'): string {
  const rendered = marked.parse(normalizeMarkdownForDisplay(markdown), {
    gfm: true,
    breaks: false,
    async: false,
  }) as string
  const sanitized = DOMPurify.sanitize(rendered, {
    USE_PROFILES: { html: true },
    FORBID_ATTR: ['style'],
    ALLOW_DATA_ATTR: true,
  })
  const container = document.createElement('div')
  container.innerHTML = sanitized
  container.querySelectorAll('a').forEach((link) => {
    link.target = '_blank'
    link.rel = 'noopener noreferrer'
  })
  return container.innerHTML || `<p>${emptyMessage}</p>`
}

async function previewFile(attachment: Attachment): Promise<void> {
  markdownPreviewController?.abort()
  previewAttachment.value = attachment
  markdownPreviewHtml.value = ''
  markdownPreviewError.value = ''
  if (attachmentKind(attachment) !== 'MD') return

  const controller = new AbortController()
  markdownPreviewController = controller
  isMarkdownPreviewLoading.value = true
  try {
    const response = await fetch(attachmentContentUrl(attachment), { signal: controller.signal })
    if (!response.ok) throw new Error(await responseError(response))
    if (previewAttachment.value?.id === attachment.id) {
      markdownPreviewHtml.value = renderMarkdownHtml(await response.text(), '此 Markdown 文件暂无正文。')
    }
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') return
    markdownPreviewError.value = error instanceof Error ? error.message : 'Markdown 预览加载失败'
  } finally {
    if (markdownPreviewController === controller) {
      markdownPreviewController = null
      isMarkdownPreviewLoading.value = false
    }
  }
}

function closePreview(): void {
  markdownPreviewController?.abort()
  markdownPreviewController = null
  previewAttachment.value = null
  markdownPreviewHtml.value = ''
  markdownPreviewError.value = ''
  isMarkdownPreviewLoading.value = false
}

function openFileInNewWindow(attachment: Attachment): void {
  window.open(attachmentContentUrl(attachment), '_blank', 'noopener,noreferrer')
}

function downloadFile(attachment: Attachment): void {
  const anchor = document.createElement('a')
  anchor.href = attachmentContentUrl(attachment, true)
  anchor.download = attachment.name
  anchor.click()
}

async function deleteAttachment(attachment: Attachment): Promise<void> {
  if (!window.confirm(`确定删除附件“${attachment.name}”吗？磁盘文件也会被清理。`)) return
  deletingAttachmentId.value = attachment.id
  try {
    const response = await fetch(`${apiBase}/api/v1/documents/attachments/${attachment.id}`, { method: 'DELETE' })
    if (!response.ok) throw new Error(await responseError(response))
    await Promise.all([loadEntries(selectedEntryId.value), loadDashboard(), loadTasks()])
    showNotice('success', '附件已删除')
  } catch (error) {
    showNotice('error', error instanceof Error ? error.message : '附件删除失败')
  } finally {
    deletingAttachmentId.value = null
  }
}

function openMoveDialog(attachment: Attachment): void {
  movingAttachment.value = attachment
  moveTargetId.value = moveTargets.value[0]?.id || ''
}

async function moveFile(): Promise<void> {
  if (!movingAttachment.value || !moveTargetId.value) return
  isMoving.value = true
  try {
    const response = await fetch(`${apiBase}/api/v1/documents/attachments/${movingAttachment.value.id}/move`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_document_id: moveTargetId.value }),
    })
    if (!response.ok) throw new Error(await responseError(response))
    const targetId = moveTargetId.value
    movingAttachment.value = null
    await Promise.all([loadEntries(targetId), loadDashboard(), loadTasks()])
    showNotice('success', '附件已移动到目标条目')
  } catch (error) {
    showNotice('error', error instanceof Error ? error.message : '附件移动失败')
  } finally {
    isMoving.value = false
  }
}

async function deleteSelectedEntry(): Promise<void> {
  if (selectedEntryActiveTask.value) {
    showNotice('error', '该文献正在执行后台任务，完成后才能删除')
    return
  }
  if (selectedEntryKnowledgeBases.value.length) {
    showNotice('error', '该文献已加入知识库，请先从知识库中移除后再删除')
    return
  }
  if (!selectedEntry.value || !window.confirm(`确定删除条目“${selectedEntry.value.title}”吗？其全部附件文件也会被清理。`)) return
  isDeletingEntry.value = true
  try {
    const response = await fetch(`${apiBase}/api/v1/documents/${selectedEntry.value.id}`, { method: 'DELETE' })
    if (!response.ok) throw new Error(await responseError(response))
    await Promise.all([loadEntries(null), loadDashboard(), loadTasks(), loadDocumentDeletionSummary()])
    showNotice('success', '条目及其附件已删除')
  } catch (error) {
    showNotice('error', error instanceof Error ? error.message : '条目删除失败')
  } finally {
    isDeletingEntry.value = false
  }
}

async function deleteUnprotectedDocuments(): Promise<void> {
  const count = documentDeletionSummary.value?.deletable_count ?? 0
  if (!count || isBulkDeletingDocuments.value) return
  const processingCount = documentDeletionSummary.value?.processing_count ?? 0
  const warning = processingCount
    ? `另有 ${processingCount} 条正在处理的文献会暂时保留。`
    : ''
  if (!window.confirm(`确定清空 ${count} 条未加入知识库且未在处理中的文献吗？这些条目的附件也会删除。已加入知识库的文献不会删除。${warning}`)) return
  isBulkDeletingDocuments.value = true
  try {
    const response = await fetch(`${apiBase}/api/v1/documents/delete-unprotected`, { method: 'POST' })
    if (!response.ok) throw new Error(await responseError(response))
    const result = await response.json() as { deleted_count: number; protected_count: number; processing_count: number }
    await Promise.all([loadEntries(null), loadDashboard(), loadTasks(), loadDocumentDeletionSummary()])
    const skipped = result.processing_count ? `，${result.processing_count} 条正在处理的文献保留` : ''
    showNotice('success', `已删除 ${result.deleted_count} 条文献及其附件；${result.protected_count} 条知识库文献已保留${skipped}`)
  } catch (error) {
    showNotice('error', error instanceof Error ? error.message : '一键清理文献失败')
    await loadDocumentDeletionSummary()
  } finally {
    isBulkDeletingDocuments.value = false
  }
}

function chunkingConfigPayload(): Record<string, unknown> {
  // Preview and registration must send exactly the same persisted recipe.
  if (chunkingForm.strategy === 'guideline_semantic') return {
    deepseek: { model: 'deepseek-v4-flash', timeout_seconds: chunkingForm.semanticTimeout, max_attempts: chunkingForm.semanticAttempts, concurrent_requests: chunkingForm.semanticConcurrency },
    batching: { target_chars: chunkingForm.batchTargetChars, max_chars: chunkingForm.batchMaxChars, target_atoms: chunkingForm.batchTargetAtoms, max_atoms: chunkingForm.batchMaxAtoms, context_before_atoms: chunkingForm.contextBefore, context_after_atoms: chunkingForm.contextAfter },
    chunking: { review_max_chars: chunkingForm.reviewMaxChars },
    summary: { enabled: chunkingForm.summaryEnabled, max_chars: chunkingForm.summaryMaxChars },
    cache_enabled: chunkingForm.cacheEnabled,
    require_quality_gate: chunkingForm.requireQualityGate,
    prepend_heading_path: chunkingForm.prependHeadingPath,
    max_chunks: chunkingForm.maxChunks,
  }
  return {
    target_chars: chunkingForm.targetChars,
    max_chars: chunkingForm.maxChars,
    min_chars: chunkingForm.minChars,
    overlap: chunkingForm.overlap,
    max_chunks: chunkingForm.maxChunks,
    heading_levels: [...chunkingForm.headingLevels].sort(),
    merge_short_sections: chunkingForm.mergeShortSections,
    prepend_heading_path: chunkingForm.prependHeadingPath,
    preserve_tables: chunkingForm.preserveTables,
    preserve_code_blocks: chunkingForm.preserveCodeBlocks,
  }
}

function currentChunkingPreviewSignature(): string {
  // Changing either the sample or a parameter invalidates the previous dry run.
  return JSON.stringify({
    documentId: chunkingPreviewDocumentId.value,
    strategy: chunkingForm.strategy,
    config: chunkingConfigPayload(),
  })
}

function openChunkingDialog(): void {
  recipePreviewController?.abort()
  isChunkingPreviewLoading.value = false
  chunkingStep.value = 1
  chunkingPreviewDocumentId.value = markdownReadyEntries.value[0]?.id || ''
  chunkingPreview.value = []
  chunkingPreviewStats.value = null
  chunkingPreviewError.value = ''
  chunkingPreviewSignature.value = ''
  isChunkingDialogOpen.value = true
}

function toggleHeadingLevel(level: number): void {
  chunkingForm.headingLevels = chunkingForm.headingLevels.includes(level)
    ? chunkingForm.headingLevels.filter((item) => item !== level)
    : [...chunkingForm.headingLevels, level].sort()
  chunkingPreviewSignature.value = ''
}

function goToChunkingStep(step: number): void {
  if (step > 1 && !chunkingForm.name.trim()) {
    showNotice('error', '请先填写分块方法名称')
    return
  }
  if (step > 2 && chunkingForm.strategy === 'guideline_semantic') {
    if (chunkingForm.batchTargetChars > chunkingForm.batchMaxChars || chunkingForm.batchTargetAtoms > chunkingForm.batchMaxAtoms) {
      showNotice('error', '语义批次目标不能超过对应的最大值')
      return
    }
  } else if (step > 2) {
    if (chunkingForm.minChars > chunkingForm.targetChars || chunkingForm.targetChars > chunkingForm.maxChars) {
      showNotice('error', '字符范围需要满足：最小字符 ≤ 目标字符 ≤ 最大字符')
      return
    }
    if (chunkingForm.overlap >= chunkingForm.maxChars / 2) {
      showNotice('error', '重叠字符数必须小于最大字符数的一半')
      return
    }
    if (chunkingForm.strategy === 'heading' && !chunkingForm.headingLevels.length) {
      showNotice('error', '请至少选择一个标题层级')
      return
    }
  }
  chunkingStep.value = step
}

async function previewChunkingMethod(): Promise<void> {
  if (!chunkingPreviewDocumentId.value) {
    showNotice('error', '请先选择一篇已经有 MD 版本的样本文献')
    return
  }
  isChunkingPreviewLoading.value = true
  chunkingProgressMessage.value = ''
  chunkingPreviewError.value = ''
  chunkingPreview.value = []
  chunkingPreviewStats.value = null
  recipePreviewController?.abort()
  const controller = new AbortController()
  recipePreviewController = controller
  const signature = currentChunkingPreviewSignature()
  try {
    const response = await fetch(`${apiBase}/api/v1/configurations/chunking-methods/preview`, {
      method: 'POST',
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        document_id: chunkingPreviewDocumentId.value,
        strategy: chunkingForm.strategy,
        config: chunkingConfigPayload(),
      }),
    })
    if (!response.ok) throw new Error(await responseError(response))
    const result = await readChunkingResponse(response, controller.signal, (message) => { chunkingProgressMessage.value = message })
    chunkingPreview.value = result.chunks
    chunkingPreviewStats.value = result.stats
    chunkingPreviewSignature.value = signature
  } catch (error) {
    if (controller.signal.aborted) return
    chunkingPreviewError.value = error instanceof Error ? error.message : '分块预览生成失败'
  } finally {
    if (recipePreviewController === controller) isChunkingPreviewLoading.value = false
  }
}

async function readChunkingResponse(response: Response, signal: AbortSignal, update: (message: string) => void): Promise<ChunkPreviewResponse> {
  if (!response.ok) throw new Error(await responseError(response))
  if (response.status !== 202) return await response.json() as ChunkPreviewResponse
  let task = await response.json() as ProcessingTask
  tasks.value = [task, ...tasks.value.filter((item) => item.id !== task.id)]
  scheduleTaskPolling()
  while (['queued', 'running'].includes(task.status)) {
    update(task.message || '指南语义切块任务正在后台执行，可在解析任务页查看进度')
    await new Promise((resolve) => window.setTimeout(resolve, 1500))
    const statusResponse = await fetch(`${apiBase}/api/v1/processing-tasks/${task.id}`, { signal })
    if (!statusResponse.ok) throw new Error(await responseError(statusResponse))
    task = await statusResponse.json() as ProcessingTask
  }
  if (task.status !== 'completed') throw new Error(task.error_message || '指南语义切块未完成，请在解析任务页重试')
  const resultResponse = await fetch(`${apiBase}/api/v1/processing-tasks/${task.id}/result`, { signal })
  if (!resultResponse.ok) throw new Error(await responseError(resultResponse))
  return await resultResponse.json() as ChunkPreviewResponse
}

async function viewGuidelineTaskResult(task: ProcessingTask): Promise<void> {
  try {
    const response = await fetch(`${apiBase}/api/v1/processing-tasks/${task.id}/result`)
    if (!response.ok) throw new Error(await responseError(response))
    guidelineTaskResult.value = await response.json() as ChunkPreviewResponse
  } catch (error) {
    showNotice('error', error instanceof Error ? error.message : '切块结果加载失败')
  }
}

async function registerChunkingMethod(): Promise<void> {
  if (!chunkingForm.name.trim()) {
    showNotice('error', '请填写分块方法名称')
    return
  }
  // Never save a recipe that has not succeeded with its exact current settings.
  if (chunkingPreviewSignature.value !== currentChunkingPreviewSignature()) {
    chunkingStep.value = 3
    showNotice('error', '请使用当前参数成功生成一次预览后再注册')
    return
  }
  isSavingChunkingMethod.value = true
  try {
    const response = await fetch(`${apiBase}/api/v1/configurations/chunking-methods`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: chunkingForm.name.trim(),
        strategy: chunkingForm.strategy,
        description: chunkingForm.description.trim() || null,
        color: chunkingForm.color,
        config: chunkingConfigPayload(),
      }),
    })
    if (!response.ok) throw new Error(await responseError(response))
    chunkingForm.name = ''
    chunkingForm.description = ''
    await loadConfigurations()
    isChunkingDialogOpen.value = false
    showNotice('success', '分块方法已注册')
  } catch (error) {
    showNotice('error', error instanceof Error ? error.message : '分块方法注册失败')
  } finally {
    isSavingChunkingMethod.value = false
  }
}

async function registerEmbeddingModel(): Promise<void> {
  if (!embeddingForm.name.trim() || !embeddingForm.modelName.trim()) {
    showNotice('error', '请填写显示名称和模型标识')
    return
  }
  isSavingEmbeddingModel.value = true
  try {
    const response = await fetch(`${apiBase}/api/v1/configurations/embedding-models`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: embeddingForm.name.trim(),
        provider: embeddingForm.provider,
        model_name: embeddingForm.modelName.trim(),
        dimension: embeddingForm.dimension || null,
        base_url: embeddingForm.baseUrl.trim() || null,
        config: {},
      }),
    })
    if (!response.ok) throw new Error(await responseError(response))
    embeddingForm.name = 'MedCPT（本地双编码器）'
    embeddingForm.provider = 'medcpt'
    embeddingForm.modelName = 'ncbi/MedCPT-Article-Encoder'
    embeddingForm.dimension = 768
    embeddingForm.baseUrl = ''
    await loadConfigurations()
    isEmbeddingDialogOpen.value = false
    showNotice('success', '嵌入模型已注册')
  } catch (error) {
    showNotice('error', error instanceof Error ? error.message : '嵌入模型注册失败')
  } finally {
    isSavingEmbeddingModel.value = false
  }
}

async function downloadEmbeddingModel(model: EmbeddingModel): Promise<void> {
  downloadingModelId.value = model.id
  try {
    const response = await fetch(`${apiBase}/api/v1/configurations/embedding-models/${model.id}/download`, { method: 'POST' })
    if (!response.ok) throw new Error(await responseError(response))
    const task = await response.json() as ProcessingTask
    tasks.value = [task, ...tasks.value.filter((item) => item.id !== task.id)]
    scheduleTaskPolling()
    showNotice('success', 'MedCPT 查询与文献编码器已加入下载队列')
  } catch (error) {
    showNotice('error', error instanceof Error ? error.message : 'MedCPT 模型下载任务创建失败')
  } finally {
    downloadingModelId.value = null
  }
}

function closeChunkingDialog(): void {
  if (!isSavingChunkingMethod.value) {
    isChunkingDialogOpen.value = false
    recipePreviewController?.abort()
  }
}

function closeEmbeddingDialog(): void {
  if (!isSavingEmbeddingModel.value) isEmbeddingDialogOpen.value = false
}

async function deleteConfiguration(kind: 'chunking-methods' | 'embedding-models', id: string, name: string): Promise<void> {
  if (!window.confirm(`确定删除“${name}”吗？`)) return
  deletingConfigurationId.value = id
  try {
    const response = await fetch(`${apiBase}/api/v1/configurations/${kind}/${id}`, { method: 'DELETE' })
    if (!response.ok) throw new Error(await responseError(response))
    await loadConfigurations()
    showNotice('success', '配置已删除')
  } catch (error) {
    showNotice('error', error instanceof Error ? error.message : '配置删除失败')
  } finally {
    deletingConfigurationId.value = null
  }
}

function openKnowledgeDialog(): void {
  if (!markdownReadyEntries.value.length) {
    showNotice('error', '知识库只能加入 MD 文本，请先上传 MD 或使用 MinerU 将 PDF 转为 MD')
    return
  }
  knowledgeForm.name = ''
  knowledgeForm.description = ''
  knowledgeForm.documentIds = []
  knowledgeForm.chunkingMethodId = chunkingMethods.value[0]?.id || ''
  knowledgeForm.embeddingModelId = embeddingModels.value[0]?.id || ''
  isKnowledgeDialogOpen.value = true
}

function closeKnowledgeDialog(): void {
  if (!isSavingKnowledgeBase.value) isKnowledgeDialogOpen.value = false
}

async function createKnowledgeBase(): Promise<void> {
  if (!knowledgeForm.name.trim()) {
    showNotice('error', '请填写知识库名称')
    return
  }
  if (!knowledgeForm.documentIds.length) {
    showNotice('error', '请至少选择一篇文献')
    return
  }
  if (!knowledgeForm.chunkingMethodId || !knowledgeForm.embeddingModelId) {
    showNotice('error', '请选择分块方法和嵌入模型')
    return
  }
  isSavingKnowledgeBase.value = true
  try {
    const response = await fetch(`${apiBase}/api/v1/knowledge-bases`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: knowledgeForm.name.trim(),
        description: knowledgeForm.description.trim() || null,
        document_ids: knowledgeForm.documentIds,
        chunking_method_id: knowledgeForm.chunkingMethodId,
        embedding_model_id: knowledgeForm.embeddingModelId,
      }),
    })
    if (!response.ok) throw new Error(await responseError(response))
    const created = mapKnowledgeBase(await response.json() as ApiKnowledgeBase)
    await Promise.all([loadKnowledgeBases(created.id), loadDashboard(), loadTasks()])
    isKnowledgeDialogOpen.value = false
    showNotice('success', '知识库已创建并保存构建配置')
  } catch (error) {
    showNotice('error', error instanceof Error ? error.message : '知识库创建失败')
  } finally {
    isSavingKnowledgeBase.value = false
  }
}

function knowledgeBaseIsBusy(library: KnowledgeBase): boolean {
  return library.status === 'building'
    || buildingKnowledgeBaseId.value === library.id
    || activeTasks.value.some((task) => (
      task.resource_type === 'knowledge_base' && task.resource_id === library.id
    ))
}

async function deleteKnowledgeBase(library: KnowledgeBase): Promise<void> {
  if (knowledgeBaseIsBusy(library)) {
    showNotice('info', '知识库正在构建，完成后才能删除')
    return
  }
  if (deletingKnowledgeBaseId.value) return
  const wasSelected = selectedKnowledgeBase.value?.id === library.id
  const documentCount = library.documents.length
  if (!window.confirm(`确定删除知识库“${library.name}”吗？该知识库的向量索引和分块数据会删除；其中 ${documentCount} 篇文献及其附件会保留。`)) return
  deletingKnowledgeBaseId.value = library.id
  try {
    const response = await fetch(`${apiBase}/api/v1/knowledge-bases/${library.id}`, { method: 'DELETE' })
    if (!response.ok) throw new Error(await responseError(response))
    await Promise.all([loadKnowledgeBases(), loadDashboard(), loadTasks()])
    if (wasSelected) closeKnowledgeBase()
    showNotice('success', `知识库“${library.name}”已删除，文献及附件已保留`)
  } catch (error) {
    showNotice('error', error instanceof Error ? error.message : '知识库删除失败')
  } finally {
    deletingKnowledgeBaseId.value = null
  }
}

function openKnowledgeBase(item: KnowledgeBase): void {
  selectedKnowledgeBase.value = item
  selectedKnowledgeDocument.value = null
  chunkPreview.value = []
  chunkPreviewError.value = ''
  knowledgeSearchQuery.value = ''
  knowledgeSearchResults.value = []
  knowledgeSearchError.value = ''
}

function closeKnowledgeBase(): void {
  knowledgePreviewController?.abort()
  selectedKnowledgeBase.value = null
  selectedKnowledgeDocument.value = null
  chunkPreview.value = []
  chunkPreviewError.value = ''
}

async function buildKnowledgeBase(): Promise<void> {
  if (!selectedKnowledgeBase.value) return
  const library = selectedKnowledgeBase.value
  buildingKnowledgeBaseId.value = library.id
  try {
    const response = await fetch(`${apiBase}/api/v1/knowledge-bases/${library.id}/build`, { method: 'POST' })
    if (!response.ok) throw new Error(await responseError(response))
    const task = await response.json() as ProcessingTask
    tasks.value = [task, ...tasks.value.filter((item) => item.id !== task.id)]
    selectedKnowledgeBase.value.status = 'building'
    scheduleTaskPolling()
    showNotice('success', 'MedCPT 向量构建任务已加入队列')
  } catch (error) {
    showNotice('error', error instanceof Error ? error.message : '向量构建任务创建失败')
  } finally {
    buildingKnowledgeBaseId.value = null
  }
}

async function searchKnowledgeBase(): Promise<void> {
  if (!selectedKnowledgeBase.value || !knowledgeSearchQuery.value.trim()) return
  isKnowledgeSearching.value = true
  knowledgeSearchError.value = ''
  knowledgeSearchResults.value = []
  try {
    const response = await fetch(`${apiBase}/api/v1/knowledge-bases/${selectedKnowledgeBase.value.id}/search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: knowledgeSearchQuery.value.trim(), top_k: 8 }),
    })
    if (!response.ok) throw new Error(await responseError(response))
    const result = await response.json() as { items: KnowledgeSearchHit[] }
    knowledgeSearchResults.value = result.items
  } catch (error) {
    knowledgeSearchError.value = error instanceof Error ? error.message : '知识库检索失败'
  } finally {
    isKnowledgeSearching.value = false
  }
}

async function openKnowledgeDocument(document: LibraryEntry): Promise<void> {
  if (!selectedKnowledgeBase.value) return
  selectedKnowledgeDocument.value = document
  chunkPreview.value = []
  chunkPreviewError.value = ''
  isChunkPreviewLoading.value = true
  knowledgeChunkProgressMessage.value = ''
  knowledgePreviewController?.abort()
  const controller = new AbortController()
  knowledgePreviewController = controller
  try {
    const response = await fetch(`${apiBase}/api/v1/knowledge-bases/${selectedKnowledgeBase.value.id}/documents/${document.id}/chunks`, { signal: controller.signal })
    if (!response.ok) throw new Error(await responseError(response))
    const result = await readChunkingResponse(response, controller.signal, (message) => { knowledgeChunkProgressMessage.value = message })
    chunkPreview.value = result.chunks
  } catch (error) {
    if (controller.signal.aborted) return
    chunkPreviewError.value = error instanceof Error ? error.message : '分块预览加载失败'
  } finally {
    if (knowledgePreviewController === controller) isChunkPreviewLoading.value = false
  }
}

onMounted(() => {
  // Initial hydration uses only real APIs. Visibility changes recalculate the
  // next delay so a background tab immediately drops to the lower poll rate.
  void refreshRealData()
  document.addEventListener('visibilitychange', scheduleTaskPolling)
})

onBeforeUnmount(() => {
  // Prevent a detached page from continuing network requests after navigation
  // or hot-module replacement during development.
  stopTaskPolling()
  recipePreviewController?.abort()
  knowledgePreviewController?.abort()
  document.removeEventListener('visibilitychange', scheduleTaskPolling)
})
</script>

<template>
  <div class="shell">
    <PlatformSidebar
      class="knowledge-sidebar"
      active-app="knowledge"
      aria-label="知识空间导航"
      account-accent="primary"
      hide-account-on-mobile
    >
      <p class="sidebar-section-label">知识工作台</p>
      <nav class="knowledge-business-nav" aria-label="知识服务导航">
        <RouterLink v-for="tab in tabs" :key="tab.id" class="nav-item" :class="{ active: activeTab === tab.id }" :aria-current="activeTab === tab.id ? 'page' : undefined" :to="tab.path">
          <span>{{ tab.label }}</span>
        </RouterLink>
      </nav>
    </PlatformSidebar>

    <main>
      <div v-if="notice" class="notice" :class="notice.kind" :role="notice.kind === 'error' ? 'alert' : 'status'"><span>{{ notice.message }}</span><button type="button" aria-label="关闭提示" @click="notice = null">×</button></div>

      <template v-if="activeTab === 'welcome'">
        <header><div><p class="eyebrow">Knowledge workspace</p><h1>从文献到<span>可追溯证据</span></h1><p>集中管理脑出血研究，让每条医学结论都能回到原始页码与版本。</p></div><button type="button" @click="navigateTo('documents')">进入文献管理</button></header>
        <section class="signal" :aria-busy="isDashboardLoading">
          <div><b>{{ dashboard?.documents_this_week ?? '—' }}</b><span>近 7 天新增文献</span></div>
          <div><b>{{ dashboard?.persisted_chunks ?? '—' }}</b><span>持久化知识块</span></div>
          <div><b>{{ dashboard?.markdown_ready ?? '—' }}</b><span>MD 已就绪文献</span></div>
          <div><b>{{ dashboard?.knowledge_bases ?? '—' }}</b><span>已建知识库</span></div>
        </section>
        <section class="content">
          <div class="section-title"><div><p class="eyebrow">Recent library</p><h2>最近文献</h2></div><button class="text-action" type="button" @click="navigateTo('documents')">查看全部 →</button></div>
          <div v-if="isDashboardLoading" class="data-state" role="status"><span class="loading-ring"></span><p>正在读取工作台数据…</p></div>
          <div v-else-if="dashboardLoadError" class="data-state error" role="alert"><p>{{ dashboardLoadError }}</p><button type="button" @click="loadDashboard">重新加载</button></div>
          <div v-else-if="recentDocuments.length" class="doc-list"><article v-for="(doc, i) in recentDocuments" :key="doc.id"><span class="file-no">{{ String(i + 1).padStart(2, '0') }}</span><div><h3>{{ doc.title }}</h3><p>{{ recentDocumentMeta(doc) }}</p></div><span class="tag">{{ recentDocumentState(doc) }}</span></article></div>
          <div v-else class="data-state"><p>还没有真实文献记录。</p><button type="button" @click="openUploadDialog('new')">添加第一份文件</button></div>
        </section>
      </template>

      <template v-else-if="activeTab === 'documents'">
        <header class="page-header document-header">
          <div><p class="eyebrow">Reference library</p><h1>文献管理</h1><p>以条目组织元数据，以附件保留可追溯的原始文件。</p></div>
          <div class="header-actions">
            <button class="cleanup-button" type="button" :disabled="isBulkDeletingDocuments || isLoadingDeletionSummary || !documentDeletionSummary?.deletable_count" :title="documentDeletionSummary ? `共 ${documentDeletionSummary.total_count} 条，${documentDeletionSummary.protected_count} 条在知识库中受保护，${documentDeletionSummary.processing_count} 条正在处理` : '正在读取可清理文献数量'" @click="deleteUnprotectedDocuments">{{ isBulkDeletingDocuments ? '正在清空…' : `清空文献（${documentDeletionSummary?.deletable_count ?? '…'}）` }}</button>
            <button class="soft-button" type="button" :disabled="isUploading" @click="openUploadDialog('new')"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/><path d="M4 19.5V21h16v-1.5"/></svg>添加文件</button>
            <button type="button" @click="openCreateDialog"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M6 3h9l3 3v15H6z"/><path d="M9 12h6M12 9v6"/></svg>新建条目</button>
          </div>
        </header>

        <section class="document-toolbar" aria-label="文献筛选">
          <label class="search-field"><span>搜索条目</span><span class="input-wrap"><svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="11" cy="11" r="6"/><path d="m16 16 4 4"/></svg><input v-model.trim="searchQuery" type="search" placeholder="标题、作者或 DOI" /></span></label>
          <label class="filter-field"><span>条目类型</span><select v-model="itemFilter"><option value="all">全部类型</option><option value="journalArticle">期刊论文</option><option value="guideline">临床指南</option><option value="report">报告</option><option value="document">文档</option></select></label>
          <div class="library-summary"><strong>{{ entries.length }}</strong><span>个条目</span><strong>{{ totalAttachments }}</strong><span>个附件</span></div>
        </section>

        <section v-if="isEntriesLoading" class="data-state page-data-state" role="status"><span class="loading-ring"></span><p>正在读取文献条目…</p></section>
        <section v-else-if="entriesLoadError" class="data-state page-data-state error" role="alert"><p>{{ entriesLoadError }}</p><button type="button" @click="loadEntries()">重新加载</button></section>
        <section v-else class="reference-workspace" aria-label="文献条目工作区">
          <div class="entry-pane">
            <div class="pane-heading"><div><p class="eyebrow">My library</p><h2>我的文献</h2></div><span>{{ filteredEntries.length }} 条</span></div>
            <div v-if="filteredEntries.length" class="entry-tree">
              <article v-for="entry in filteredEntries" :key="entry.id" class="entry-group" :class="{ selected: selectedEntryId === entry.id }">
                <div class="entry-line">
                  <button v-if="entry.attachments.length" class="entry-toggle" type="button" :aria-label="`${expandedEntryIds.has(entry.id) ? '收起' : '展开'} ${entry.title} 的附件`" :aria-expanded="expandedEntryIds.has(entry.id)" @click="toggleEntry(entry.id)"><svg aria-hidden="true" viewBox="0 0 24 24" :class="{ rotated: expandedEntryIds.has(entry.id) }"><path d="m9 6 6 6-6 6"/></svg></button>
                  <span v-else class="entry-toggle-spacer"></span>
                  <button class="entry-select" type="button" @click="selectEntry(entry.id)"><span class="entry-icon"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M6 3h9l3 3v15H6z"/><path d="M15 3v4h4M9 12h6M9 16h5"/></svg></span><span class="entry-copy"><strong>{{ entry.title }}</strong><small>{{ entry.authors.join('、') || '未填写作者' }}<template v-if="entry.year"> · {{ entry.year }}</template></small></span></button>
                </div>
                <ul v-if="expandedEntryIds.has(entry.id) && entry.attachments.length" class="attachment-tree" :aria-label="`${entry.title} 的附件`"><li v-for="attachment in entry.attachments" :key="attachment.id"><span class="tree-guide"></span><button class="attachment-tree-select" type="button" @click="previewFile(attachment)"><span class="pdf-icon" :class="{ markdown: attachmentKind(attachment) === 'MD' }">{{ attachmentKind(attachment) }}</span><span><strong>{{ attachment.name }}</strong><small>{{ formatFileSize(attachment.sizeBytes) }}</small></span></button></li></ul>
              </article>
            </div>
            <div v-else class="empty-state"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M6 3h9l3 3v15H6z"/><path d="M9 12h6M9 16h4"/></svg><h3>没有找到条目</h3><p>调整搜索条件，或创建一个新条目。</p><button type="button" @click="openCreateDialog">新建条目</button></div>
          </div>

          <section class="entry-inspector" aria-label="条目详情">
            <template v-if="selectedEntry">
              <div class="inspector-heading"><span>{{ itemTypeLabel(selectedEntry.itemType) }}</span><button class="danger-link" type="button" :disabled="isDeletingEntry || selectedEntryKnowledgeBases.length > 0 || Boolean(selectedEntryActiveTask)" :title="selectedEntryKnowledgeBases.length ? `已加入知识库：${selectedEntryKnowledgeBases.map((item) => item.name).join('、')}` : selectedEntryActiveTask ? '后台任务处理期间不能删除文献' : '删除条目及其附件'" @click="deleteSelectedEntry">{{ isDeletingEntry ? '正在删除…' : selectedEntryKnowledgeBases.length ? '知识库使用中' : selectedEntryActiveTask ? '后台任务处理中' : '删除条目' }}</button></div>
              <p v-if="selectedEntryKnowledgeBases.length" class="protected-entry-note">该文献已加入 {{ selectedEntryKnowledgeBases.map((item) => `“${item.name}”`).join('、') }}，需先从知识库移除才能删除。</p>
              <p v-else-if="selectedEntryActiveTask" class="protected-entry-note" role="status">{{ selectedEntryActiveTask.message || '后台任务正在处理该文献' }}，完成前不能修改附件或删除条目。</p>
              <h2>{{ selectedEntry.title }}</h2>
              <p class="inspector-authors">{{ selectedEntry.authors.join('、') || '未填写作者' }}</p>
              <dl><div><dt>年份</dt><dd>{{ selectedEntry.year || '—' }}</dd></div><div><dt>DOI</dt><dd>{{ selectedEntry.doi || '—' }}</dd></div><div><dt>附件</dt><dd>{{ selectedEntry.attachments.length }} 个</dd></div></dl>
              <div class="abstract-block"><h3>摘要</h3><p>{{ selectedEntry.abstract || '暂无摘要。' }}</p></div>
              <div class="inspector-attachments"><div><h3>文件版本</h3><span class="attachment-header-actions"><button v-if="selectedEntry.attachments.some((file) => attachmentKind(file) === 'PDF')" class="text-action" type="button" :disabled="convertingDocumentId === selectedEntry.id || Boolean(selectedEntryActiveTask)" @click="convertPdfToMarkdown(selectedEntry)">{{ conversionButtonLabel(selectedEntry) }}</button><button class="text-action" type="button" :disabled="isUploading || Boolean(selectedEntryActiveTask)" @click="openUploadDialog('existing')">添加文件 ＋</button></span></div><p class="attachment-policy">每个条目最多保存一份 PDF 原件和一份 MD 文本；知识库只使用 MD。</p><ul v-if="selectedEntry.attachments.length"><li v-for="attachment in selectedEntry.attachments" :key="attachment.id"><span class="pdf-icon" :class="{ markdown: attachmentKind(attachment) === 'MD' }">{{ attachmentKind(attachment) }}</span><span class="attachment-copy"><strong>{{ attachment.name }}</strong><small>{{ formatFileSize(attachment.sizeBytes) }}<template v-if="attachment.source === 'mineru'"> · MinerU 生成</template><template v-else> · 用户上传</template></small></span><span class="attachment-actions"><button type="button" @click="previewFile(attachment)">预览</button><button type="button" @click="downloadFile(attachment)">下载</button><button type="button" :disabled="!moveTargets.length || Boolean(selectedEntryActiveTask)" @click="openMoveDialog(attachment)">移动</button><button class="danger" type="button" :disabled="deletingAttachmentId === attachment.id || Boolean(selectedEntryActiveTask)" @click="deleteAttachment(attachment)">{{ deletingAttachmentId === attachment.id ? '删除中' : '删除' }}</button></span></li></ul><p v-else>暂无文件，可添加一份 PDF 或 Markdown。</p></div>
            </template>
            <div v-else class="empty-inspector"><h2>选择一个条目</h2><p>在左侧查看文献元数据与附件。</p></div>
          </section>
        </section>
      </template>

      <template v-else-if="activeTab === 'configurations'">
        <header class="page-header configuration-header"><div><p class="eyebrow">Build registry</p><h1>方法与模型</h1><p>集中注册可复用的分块策略与嵌入模型，供知识库构建时选择。</p></div><div class="configuration-actions"><button class="soft-button" type="button" @click="openChunkingDialog"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M5 4h14v5H5zM5 15h6v5H5zM15 15h4v5h-4z"/></svg>注册分块方法</button><button type="button" @click="isEmbeddingDialogOpen = true"><svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><circle cx="5" cy="6" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="5" cy="18" r="2"/><circle cx="19" cy="18" r="2"/><path d="m7 7 3 3m4 0 3-3m-7 7-3 3m7-3 3 3"/></svg>注册嵌入模型</button></div></header>
        <section v-if="isConfigurationsLoading" class="data-state page-data-state" role="status"><span class="loading-ring"></span><p>正在读取方法与模型…</p></section>
        <section v-else-if="configurationsLoadError" class="data-state page-data-state error" role="alert"><p>{{ configurationsLoadError }}</p><button type="button" @click="loadConfigurations">重新加载</button></section>
        <section v-else class="registry-sections" aria-label="知识库构建配置">
          <article class="registry-section">
            <div class="registry-heading"><div><span class="registry-icon chunk-icon"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M5 4h14v5H5zM5 15h6v5H5zM15 15h4v5h-4z"/></svg></span><div><p class="eyebrow">Chunking methods</p><h2>分块方法</h2></div></div><span>{{ chunkingMethods.length }} 个</span></div>
            <div v-if="chunkingMethods.length" class="registry-list">
              <article v-for="method in chunkingMethods" :key="method.id" class="registry-row chunking-row"><div class="registry-row-main"><span class="method-swatch" :style="{ background: method.color }"></span><button class="method-detail-trigger" type="button" :aria-label="`查看分块方法 ${method.name} 的详情`" title="点击查看完整配置" @click="selectedChunkingMethod = method"><strong>{{ method.name }}</strong><span class="method-detail-preview">{{ method.description || '暂无说明' }}</span></button></div><div class="registry-row-value"><small>策略</small><span>{{ strategyLabel(method.strategy) }}</span></div><div class="registry-row-value"><small>{{ method.strategy === 'guideline_semantic' ? '批次上限' : '最大字符' }}</small><span>{{ methodSize(method) }}</span></div><div class="registry-row-value"><small>{{ method.strategy === 'guideline_semantic' ? '前后上下文' : '重叠字符' }}</small><span>{{ methodContext(method) }}</span></div><button class="danger-link" type="button" :disabled="deletingConfigurationId === method.id" @click="deleteConfiguration('chunking-methods', method.id, method.name)">{{ deletingConfigurationId === method.id ? '删除中…' : '删除' }}</button></article>
            </div>
            <div v-else class="registry-empty">还没有分块方法，请先注册一个。</div>
          </article>

          <article class="registry-section">
            <div class="registry-heading"><div><span class="registry-icon model-icon"><svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><circle cx="5" cy="6" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="5" cy="18" r="2"/><circle cx="19" cy="18" r="2"/><path d="m7 7 3 3m4 0 3-3m-7 7-3 3m7-3 3 3"/></svg></span><div><p class="eyebrow">Embedding models</p><h2>嵌入模型</h2></div></div><span>{{ embeddingModels.length }} 个</span></div>
            <div v-if="embeddingModels.length" class="registry-list">
              <article v-for="model in embeddingModels" :key="model.id" class="registry-row model-row"><div class="registry-row-main"><span class="model-monogram">AI</span><div><strong>{{ model.name }}</strong><p>{{ model.model_name }}</p></div></div><div class="registry-row-value"><small>提供方</small><span>{{ providerLabel(model.provider) }}</span></div><div class="registry-row-value"><small>向量维度</small><span>{{ model.dimension ? `${model.dimension} 维` : '—' }}</span></div><div class="registry-row-value"><small>服务地址</small><span class="registry-url" :title="model.base_url || '本地模型缓存'">{{ model.base_url || '本地缓存' }}</span></div><span class="registry-actions"><button v-if="model.provider === 'medcpt'" class="text-action" type="button" :disabled="downloadingModelId === model.id" @click="downloadEmbeddingModel(model)">{{ downloadingModelId === model.id ? '排队中…' : '下载模型' }}</button><button class="danger-link" type="button" :disabled="deletingConfigurationId === model.id" @click="deleteConfiguration('embedding-models', model.id, model.name)">{{ deletingConfigurationId === model.id ? '删除中…' : '删除' }}</button></span></article>
            </div>
            <div v-else class="registry-empty">还没有嵌入模型，请先注册一个。</div>
          </article>
        </section>
      </template>

      <template v-else-if="activeTab === 'knowledge'">
        <section v-if="isKnowledgeBasesLoading" class="data-state page-data-state" role="status"><span class="loading-ring"></span><p>正在读取知识库…</p></section>
        <section v-else-if="knowledgeBasesLoadError" class="data-state page-data-state error" role="alert"><p>{{ knowledgeBasesLoadError }}</p><button type="button" @click="loadKnowledgeBases()">重新加载</button></section>
        <template v-else-if="!selectedKnowledgeBase">
          <header class="page-header"><div><p class="eyebrow">Knowledge collections</p><h1>知识库管理</h1><p>只选择已经具有 MD 文本版本的文献，并绑定分块方法和嵌入模型。</p></div><button type="button" :disabled="!markdownReadyEntries.length || !chunkingMethods.length || !embeddingModels.length" @click="openKnowledgeDialog">＋ 新建知识库</button></header>
          <div v-if="!chunkingMethods.length || !embeddingModels.length" class="configuration-callout"><div><strong>还需要完成构建配置</strong><p>新建知识库前，至少注册一个分块方法和一个嵌入模型。</p></div><button class="soft-button" type="button" @click="navigateTo('configurations')">前往方法与模型</button></div>
          <section v-if="knowledgeBases.length" class="library-grid"><article v-for="library in knowledgeBases" :key="library.id" class="library-card" :style="{ '--method-color': library.chunkingMethod.color }"><span class="library-orb"></span><div class="library-card-top"><span>{{ library.status === 'configured' ? '已配置' : library.status }}</span><small>{{ library.documents.length }} 篇文献</small></div><h2>{{ library.name }}</h2><p>{{ library.description || '暂无知识库说明。' }}</p><dl><div><dt>分块</dt><dd><i :style="{ background: library.chunkingMethod.color }"></i>{{ library.chunkingMethod.name }}</dd></div><div><dt>嵌入</dt><dd>{{ library.embeddingModel.name }}</dd></div></dl><div class="library-card-actions"><button class="text-action" type="button" @click="openKnowledgeBase(library)">打开知识库 →</button><button class="danger-link" type="button" :disabled="knowledgeBaseIsBusy(library) || deletingKnowledgeBaseId !== null" :title="knowledgeBaseIsBusy(library) ? '构建完成后才能删除' : '删除知识库及其向量索引，保留文献与附件'" @click="deleteKnowledgeBase(library)">{{ deletingKnowledgeBaseId === library.id ? '删除中…' : knowledgeBaseIsBusy(library) ? '构建中' : '删除知识库' }}</button></div></article></section>
          <section v-else class="knowledge-empty"><span class="registry-icon chunk-icon"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M4 5h7v14H4zM13 5h7v6h-7zM13 13h7v6h-7z"/></svg></span><h2>还没有知识库</h2><p>先准备 MD 文本，再选择分块方法和嵌入模型创建知识库。</p><button type="button" :disabled="!markdownReadyEntries.length || !chunkingMethods.length || !embeddingModels.length" @click="openKnowledgeDialog">新建知识库</button></section>
        </template>

        <template v-else>
          <button class="back-link" type="button" @click="closeKnowledgeBase">← 返回知识库列表</button>
          <header class="page-header knowledge-detail-header"><div><p class="eyebrow">Knowledge base</p><h1>{{ selectedKnowledgeBase.name }}</h1><p>{{ selectedKnowledgeBase.description || '暂无知识库说明。' }}</p></div><div class="knowledge-header-actions"><div class="knowledge-config-summary"><span><i :style="{ background: selectedKnowledgeBase.chunkingMethod.color }"></i>{{ selectedKnowledgeBase.chunkingMethod.name }}</span><span>{{ selectedKnowledgeBase.embeddingModel.name }}</span><span>{{ taskStatusLabel(selectedKnowledgeBase.status) }}</span></div><div class="knowledge-build-actions"><button type="button" :disabled="buildingKnowledgeBaseId === selectedKnowledgeBase.id || knowledgeBaseIsBusy(selectedKnowledgeBase)" @click="buildKnowledgeBase">{{ selectedKnowledgeBase.status === 'indexed' ? '重建向量索引' : knowledgeBaseIsBusy(selectedKnowledgeBase) ? '向量构建中…' : '构建向量索引' }}</button><button class="danger-link knowledge-delete-action" type="button" :disabled="knowledgeBaseIsBusy(selectedKnowledgeBase) || deletingKnowledgeBaseId !== null" @click="deleteKnowledgeBase(selectedKnowledgeBase)">{{ deletingKnowledgeBaseId === selectedKnowledgeBase.id ? '正在删除…' : '删除知识库' }}</button></div></div></header>
          <form class="knowledge-search" role="search" @submit.prevent="searchKnowledgeBase"><label><span>MedCPT 语义检索</span><input v-model="knowledgeSearchQuery" type="search" placeholder="输入医学问题或检索词" :disabled="selectedKnowledgeBase.status !== 'indexed'" /></label><button type="submit" :disabled="selectedKnowledgeBase.status !== 'indexed' || isKnowledgeSearching || !knowledgeSearchQuery.trim()">{{ isKnowledgeSearching ? '检索中…' : '检索知识块' }}</button></form>
          <section v-if="knowledgeSearchError || knowledgeSearchResults.length" class="knowledge-search-results" aria-live="polite"><p v-if="knowledgeSearchError" class="search-error" role="alert">{{ knowledgeSearchError }}</p><article v-for="hit in knowledgeSearchResults" :key="hit.chunk_id"><header><strong>{{ hit.document_title }}</strong><span>{{ Math.round(hit.score * 100) }}%</span></header><small>{{ hit.heading_path.join(' › ') || '正文' }}</small><ChunkSemanticMetadata :metadata="hit.metadata" /><p>{{ hit.content }}</p></article></section>
          <section class="knowledge-workspace">
            <section class="knowledge-documents" aria-label="知识库文献">
              <div class="pane-heading"><div><p class="eyebrow">Included references</p><h2>加入的文献</h2></div><span>{{ selectedKnowledgeBase.documents.length }} 篇</span></div>
              <button v-for="document in selectedKnowledgeBase.documents" :key="document.id" class="knowledge-document" :class="{ active: selectedKnowledgeDocument?.id === document.id }" type="button" @click="openKnowledgeDocument(document)"><span class="entry-icon"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M6 3h9l3 3v15H6z"/><path d="M15 3v4h4M9 12h6M9 16h5"/></svg></span><span><strong>{{ document.title }}</strong><small>{{ document.authors.join('、') || '未填写作者' }}<template v-if="document.year"> · {{ document.year }}</template></small></span><svg class="row-chevron" aria-hidden="true" viewBox="0 0 24 24"><path d="m9 6 6 6-6 6"/></svg></button>
            </section>
            <section class="chunk-viewer" aria-label="文献分块预览">
              <template v-if="selectedKnowledgeDocument">
                <div class="chunk-viewer-heading"><div><p class="eyebrow">Chunk preview</p><h2>{{ selectedKnowledgeDocument.title }}</h2></div><span><i :style="{ background: selectedKnowledgeBase.chunkingMethod.color }"></i>{{ strategyLabel(selectedKnowledgeBase.chunkingMethod.strategy) }}</span></div>
                <div v-if="isChunkPreviewLoading" class="preview-state" role="status"><span class="loading-ring"></span><p>{{ knowledgeChunkProgressMessage || '正在按所选方法生成分块预览…' }}</p></div>
                <div v-else-if="chunkPreviewError" class="preview-state error" role="alert"><p>{{ chunkPreviewError }}</p><button type="button" @click="openKnowledgeDocument(selectedKnowledgeDocument)">重新加载</button></div>
                <template v-else-if="chunkPreview.length"><ChunkRunArtifacts :metadata="chunkPreview[0]?.metadata" /><div class="chunk-list"><article v-for="chunk in chunkPreview" :key="chunk.index" class="chunk-block" :style="{ '--chunk-color': selectedKnowledgeBase.chunkingMethod.color }"><span>Chunk {{ String(chunk.index).padStart(2, '0') }}</span><ChunkSemanticMetadata :metadata="chunk.metadata" /><div class="chunk-markdown" v-html="renderMarkdownHtml(chunk.content)"></div><small>字符 {{ chunk.char_start }}–{{ chunk.char_end }}</small></article></div></template>
                <div v-else class="preview-state"><p>当前文献没有可用于分块预览的文本。</p></div>
              </template>
              <div v-else class="preview-state"><span class="registry-icon chunk-icon"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M5 4h14v5H5zM5 15h6v5H5zM15 15h4v5h-4z"/></svg></span><p>选择左侧文献，查看带颜色标识的分块结果。</p></div>
            </section>
          </section>
        </template>
      </template>

      <template v-else-if="activeTab === 'tasks'">
        <header class="page-header"><div><p class="eyebrow">Processing activity</p><h1>解析任务</h1><p>实时查看持久化任务的排队、解析、保存、完成和失败状态。</p></div><button type="button" :disabled="isTasksLoading" @click="loadTasks()">{{ isTasksLoading ? '刷新中…' : '刷新状态' }}</button></header>
        <section class="content task-panel">
          <div class="section-title"><div><p class="eyebrow">Persisted jobs</p><h2>真实处理记录</h2></div><div class="task-bulk-actions"><label v-if="removableTasks.length"><input type="checkbox" :checked="allRemovableTasksSelected" @change="toggleAllRemovableTasks" />全选可移除</label><button class="task-bulk-delete" type="button" :disabled="!selectedTaskIds.size || deletingTaskIds.size > 0" @click="deleteSelectedTasks">{{ deletingTaskIds.size ? '处理中…' : `移除所选（${selectedTaskIds.size}）` }}</button><span class="count">{{ tasks.length }} 条记录</span></div></div>
          <div v-if="isTasksLoading" class="data-state" role="status"><span class="loading-ring"></span><p>正在读取处理记录…</p></div>
          <div v-else-if="tasksLoadError" class="data-state error" role="alert"><p>{{ tasksLoadError }}</p><button type="button" @click="loadTasks()">重新加载</button></div>
          <div v-else-if="tasks.length" class="task-list" aria-live="polite" aria-atomic="false"><article v-for="task in tasks" :key="task.id"><label v-if="isRemovableTask(task)" class="task-select"><input type="checkbox" :checked="selectedTaskIds.has(task.id)" :disabled="deletingTaskIds.has(task.id)" @change="toggleTaskSelection(task.id)" /><span class="visually-hidden">选择 {{ task.name }}</span></label><span v-else class="task-select-spacer" aria-hidden="true"></span><div class="task-copy"><h3>{{ task.name }}</h3><p>{{ task.operation }} · {{ task.processor }} · {{ formatDateTime(task.created_at) }}</p><small v-if="task.error_message" class="task-error" role="alert">{{ task.error_message }}</small><small v-else>{{ task.message || '处理记录已持久化' }}</small></div><div class="progress" :class="{ indeterminate: task.progress === null && ['queued', 'running', 'building'].includes(task.status) }" :aria-label="task.progress === null ? `${taskStatusLabel(task.status)}，进度暂不可确定` : `处理进度 ${task.progress}%`"><i :style="task.progress === null ? undefined : { width: `${task.progress}%` }"></i></div><b>{{ task.progress === null ? '处理中' : `${task.progress}%` }}</b><span class="tag" :class="`task-${task.status}`">{{ taskStatusLabel(task.status) }}</span><div class="task-row-actions"><button v-if="task.has_chunking_result" class="text-action" type="button" @click="viewGuidelineTaskResult(task)">查看切块结果</button><button v-if="task.status === 'failed' && task.attempt_count < task.max_attempts" class="task-retry" type="button" :disabled="deletingTaskIds.has(task.id)" @click="retryTask(task)">重试</button><button v-if="isRemovableTask(task)" class="task-delete" type="button" :disabled="deletingTaskIds.has(task.id)" @click="deleteTask(task)">{{ deletingTaskIds.has(task.id) ? '处理中…' : task.id.startsWith('document-version:') ? '隐藏' : '删除' }}</button></div></article></div>
          <div v-else class="data-state"><p>暂无已持久化的处理记录。上传 MD、执行 MinerU 转换或创建知识库后会显示在这里。</p></div>
        </section>
      </template>
    </main>

    <Teleport to="body">
      <ChunkingMethodDetails v-if="selectedChunkingMethod" :method="selectedChunkingMethod" :engine-label="strategyLabel(selectedChunkingMethod.strategy)" @close="selectedChunkingMethod = null" />
    </Teleport>

    <Teleport to="body">
      <div v-if="guidelineTaskResult" class="dialog-backdrop" @click.self="guidelineTaskResult = null" @keydown.esc="guidelineTaskResult = null">
        <section class="entry-dialog guideline-result-dialog" role="dialog" aria-modal="true" aria-labelledby="guideline-result-title">
          <div class="dialog-heading"><div><p class="eyebrow">Guideline Chunker</p><h2 id="guideline-result-title">{{ guidelineTaskResult.document_title || '指南切块结果' }}</h2><p>{{ guidelineTaskResult.chunks.length }} 个原子知识块</p></div><button class="icon-button" type="button" aria-label="关闭指南切块结果" @click="guidelineTaskResult = null">×</button></div>
          <ChunkRunArtifacts :metadata="guidelineTaskResult.chunks[0]?.metadata || guidelineTaskResult.run_metadata" />
          <div class="recipe-preview-list"><article v-for="chunk in guidelineTaskResult.chunks" :key="chunk.index"><header><span>Chunk {{ chunk.index }}</span><small>字符 {{ chunk.char_start }}–{{ chunk.char_end }}</small></header><p v-if="chunk.heading_path.length" class="chunk-breadcrumb">{{ chunk.heading_path.join(' › ') }}</p><ChunkSemanticMetadata :metadata="chunk.metadata" /><div class="chunk-markdown compact" v-html="renderMarkdownHtml(chunk.content)"></div></article></div>
        </section>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="isChunkingDialogOpen" class="dialog-backdrop" @click.self="closeChunkingDialog" @keydown.esc="closeChunkingDialog">
        <section class="entry-dialog chunking-configuration-dialog" role="dialog" aria-modal="true" aria-labelledby="chunking-dialog-title">
          <div class="dialog-heading"><div><p class="eyebrow">Chunking recipe</p><h2 id="chunking-dialog-title">注册分块方法</h2><p>选择后端切块引擎，配置一组可复用参数，并用真实 Markdown 文献验证结果。</p></div><button class="icon-button" type="button" aria-label="关闭注册分块方法窗口" :disabled="isSavingChunkingMethod" @click="closeChunkingDialog">×</button></div>
          <nav class="configuration-steps" aria-label="分块方法注册步骤">
            <button v-for="step in [{ number: 1, label: '基本信息与引擎' }, { number: 2, label: '分块规则' }, { number: 3, label: '样本预览' }]" :key="step.number" type="button" :class="{ active: chunkingStep === step.number, complete: chunkingStep > step.number }" :aria-current="chunkingStep === step.number ? 'step' : undefined" @click="goToChunkingStep(step.number)"><span>{{ chunkingStep > step.number ? '✓' : step.number }}</span>{{ step.label }}</button>
          </nav>
          <form @submit.prevent="registerChunkingMethod">
            <section v-if="chunkingStep === 1" class="configuration-stage">
              <div class="engine-explainer"><strong>什么是切块引擎？</strong><p>它是后端预先实现并验证过的文本切分算法。前端只选择引擎并保存参数，不上传或执行用户代码。</p></div>
              <div class="form-grid"><label><span>显示名称 <b aria-hidden="true">*</b></span><input v-model.trim="chunkingForm.name" required maxlength="255" placeholder="如：指南标题递归分块" /></label><label><span>标识颜色</span><span class="color-field"><input v-model="chunkingForm.color" type="color" aria-label="分块标识颜色" /><code>{{ chunkingForm.color }}</code></span></label></div>
              <fieldset class="engine-fieldset"><legend>选择切块引擎</legend><div class="engine-options">
                <button type="button" :class="{ selected: chunkingForm.strategy === 'heading' }" @click="chunkingForm.strategy = 'heading'"><span class="engine-icon">H</span><span><strong>Markdown 标题</strong><small>按 H1–H6 章节组织，过长章节再递归切分；适合结构化指南。</small></span><i>{{ chunkingForm.strategy === 'heading' ? '已选择' : '推荐' }}</i></button>
                <button type="button" :class="{ selected: chunkingForm.strategy === 'recursive' }" @click="chunkingForm.strategy = 'recursive'"><span class="engine-icon">¶</span><span><strong>递归边界</strong><small>优先在段落、句号和换行处切开；适合标题不规范的正文。</small></span><i>{{ chunkingForm.strategy === 'recursive' ? '已选择' : '通用' }}</i></button>
                <button type="button" :class="{ selected: chunkingForm.strategy === 'fixed' }" @click="chunkingForm.strategy = 'fixed'"><span class="engine-icon">≡</span><span><strong>固定长度</strong><small>按字符窗口稳定切分并保留重叠；适合基准测试和可复现实验。</small></span><i>{{ chunkingForm.strategy === 'fixed' ? '已选择' : '基准' }}</i></button>
                <button type="button" :class="{ selected: chunkingForm.strategy === 'guideline_semantic' }" @click="chunkingForm.strategy = 'guideline_semantic'"><span class="engine-icon">G</span><span><strong>医学指南原子知识</strong><small>规则原子化 + DeepSeek 语义判断，输出 FACT / REC、主题标签、推荐等级和切块质量报告。</small></span><i>{{ chunkingForm.strategy === 'guideline_semantic' ? '已选择' : 'Guideline Chunker' }}</i></button>
              </div></fieldset>
              <label class="full-field"><span>使用说明</span><textarea v-model.trim="chunkingForm.description" rows="3" placeholder="记录适用文献、预期效果或版本说明"></textarea></label>
            </section>

            <section v-else-if="chunkingStep === 2" class="configuration-stage">
              <div class="stage-heading"><div><p class="eyebrow">Rules</p><h3>控制块大小与上下文连续性</h3></div><span>{{ strategyLabel(chunkingForm.strategy) }}</span></div>
              <template v-if="chunkingForm.strategy === 'guideline_semantic'">
                <div class="engine-explainer"><strong>模型：deepseek-v4-flash · {{ guidelineEngine?.topic_count || 102 }} 个受控主题</strong><p>{{ guidelineEngine?.api_key_configured ? 'DeepSeek 已配置。语义切块在后台运行，可从解析任务页查看进度与结果。' : 'DeepSeek 尚未配置。请在知识服务及 Worker 的后端环境中设置 DEEPSEEK_API_KEY。' }}</p></div>
                <div class="parameter-grid">
                  <label><span>批次目标字符</span><input v-model.number="chunkingForm.batchTargetChars" type="number" min="1" required /><small>决定每次语义判断的正文范围，不是单块长度。</small></label>
                  <label><span>批次最大字符</span><input v-model.number="chunkingForm.batchMaxChars" type="number" min="1" required /></label>
                  <label><span>批次目标原子数</span><input v-model.number="chunkingForm.batchTargetAtoms" type="number" min="1" required /></label>
                  <label><span>批次最大原子数</span><input v-model.number="chunkingForm.batchMaxAtoms" type="number" min="1" required /></label>
                  <label><span>前文上下文原子数</span><input v-model.number="chunkingForm.contextBefore" type="number" min="0" max="2" required /></label>
                  <label><span>后文上下文原子数</span><input v-model.number="chunkingForm.contextAfter" type="number" min="0" max="2" required /></label>
                  <label><span>待核查区间最大字符</span><input v-model.number="chunkingForm.reviewMaxChars" type="number" min="1" required /></label>
                  <label><span>摘要最大字符</span><input v-model.number="chunkingForm.summaryMaxChars" type="number" min="1" required /></label>
                  <label><span>单次请求超时（秒）</span><input v-model.number="chunkingForm.semanticTimeout" type="number" min="1" required /></label>
                  <label><span>单批次最多尝试次数</span><input v-model.number="chunkingForm.semanticAttempts" type="number" min="1" max="3" required /></label>
                  <label><span>文献最多知识块数</span><input v-model.number="chunkingForm.maxChunks" type="number" min="1" max="10000" required /></label>
                </div>
                <div class="rule-switches">
                  <label><input v-model="chunkingForm.summaryEnabled" type="checkbox" /><span><strong>生成知识摘要</strong><small>摘要须通过原文忠实度校验。</small></span></label>
                  <label><input v-model="chunkingForm.cacheEnabled" type="checkbox" /><span><strong>复用语义缓存</strong><small>重试或重新构建时复用相同原文和参数的已验证结果。</small></span></label>
                  <label><input v-model="chunkingForm.requireQualityGate" type="checkbox" /><span><strong>发布索引前必须通过质量门</strong><small>覆盖率不足、存在未解决切块问题时阻止索引发布。</small></span></label>
                  <label><input v-model="chunkingForm.prependHeadingPath" type="checkbox" /><span><strong>嵌入文本携带标题路径</strong><small>知识块正文保持原文连续切片。</small></span></label>
                </div>
              </template>
              <template v-else>
              <div class="parameter-grid">
                <label><span>目标字符数</span><input v-model.number="chunkingForm.targetChars" type="number" min="100" max="10000" required /><small>引擎优先生成的块大小。</small></label>
                <label><span>最大字符数</span><input v-model.number="chunkingForm.maxChars" type="number" min="100" max="20000" required /><small>单个块不可超过的硬上限。</small></label>
                <label><span>最小字符数</span><input v-model.number="chunkingForm.minChars" type="number" min="0" max="5000" required /><small>短章节可与前一块合并。</small></label>
                <label><span>重叠字符数</span><input v-model.number="chunkingForm.overlap" type="number" min="0" :max="Math.floor(chunkingForm.maxChars / 2) - 1" required /><small>相邻块之间保留的上下文。</small></label>
                <label><span>最多生成块数</span><input v-model.number="chunkingForm.maxChunks" type="number" min="1" max="10000" required /><small>防止异常文档无限切分。</small></label>
              </div>
              <section v-if="chunkingForm.strategy === 'heading'" class="heading-level-panel"><div><strong>参与切分的标题层级</strong><small>只把选中的 Markdown 标题视为章节边界。</small></div><div class="heading-levels"><button v-for="level in 6" :key="level" type="button" :class="{ selected: chunkingForm.headingLevels.includes(level) }" :aria-pressed="chunkingForm.headingLevels.includes(level)" @click="toggleHeadingLevel(level)">H{{ level }}</button></div></section>
              <div class="rule-switches">
                <label><input v-model="chunkingForm.mergeShortSections" type="checkbox" /><span><strong>合并过短章节</strong><small>短于最小字符数时尝试并入前一块。</small></span></label>
                <label><input v-model="chunkingForm.prependHeadingPath" type="checkbox" /><span><strong>嵌入文本携带标题路径</strong><small>检索向量保留“章 › 节”上下文，展示正文不重复标题。</small></span></label>
                <label><input v-model="chunkingForm.preserveTables" type="checkbox" /><span><strong>尽量保持表格完整</strong><small>避免在 Markdown 表格行中间切断。</small></span></label>
                <label><input v-model="chunkingForm.preserveCodeBlocks" type="checkbox" /><span><strong>尽量保持代码块完整</strong><small>避免在围栏代码块中间切断。</small></span></label>
              </div>
              </template>
            </section>

            <section v-else class="configuration-stage preview-stage">
              <div class="stage-heading"><div><p class="eyebrow">Dry run</p><h3>使用真实 MD 检查分块结果</h3></div><span>注册前必做</span></div>
              <div v-if="markdownReadyEntries.length" class="preview-controls"><label><span>样本文献</span><select v-model="chunkingPreviewDocumentId"><option v-for="entry in markdownReadyEntries" :key="entry.id" :value="entry.id">{{ entry.title }}</option></select></label><button type="button" :disabled="isChunkingPreviewLoading" @click="previewChunkingMethod">{{ isChunkingPreviewLoading ? '生成中…' : chunkingPreview.length ? '重新生成预览' : '生成预览' }}</button></div>
              <div v-else class="preview-empty"><strong>还没有可用的 Markdown 文献</strong><p>请先在文献管理上传 MD，或用 MinerU 将 PDF 转为 MD。</p></div>
              <div v-if="isChunkingPreviewLoading" class="preview-state compact" role="status"><span class="loading-ring"></span><p>{{ chunkingProgressMessage || `正在执行 ${strategyLabel(chunkingForm.strategy)}…` }}</p></div>
              <div v-else-if="chunkingPreviewError" class="preview-state compact error" role="alert"><p>{{ chunkingPreviewError }}</p></div>
              <template v-else-if="chunkingPreviewStats">
                <dl class="preview-stat-grid"><div><dt>分块总数</dt><dd>{{ chunkingPreviewStats.total }}</dd></div><div><dt>平均长度</dt><dd>{{ chunkingPreviewStats.average_chars }}</dd></div><div><dt>最短 / 最长</dt><dd>{{ chunkingPreviewStats.minimum_chars }} / {{ chunkingPreviewStats.maximum_chars }}</dd></div><div><dt>超出范围</dt><dd>{{ chunkingPreviewStats.too_short + chunkingPreviewStats.too_long }}</dd></div></dl>
                <ChunkRunArtifacts :metadata="chunkingPreview[0]?.metadata" />
                <div class="recipe-preview-list"><article v-for="chunk in chunkingPreview" :key="chunk.index" :style="{ '--preview-color': chunkingForm.color }"><header><span>Chunk {{ String(chunk.index).padStart(2, '0') }}</span><small>{{ chunk.char_count }} 字符</small></header><p v-if="chunk.heading_path.length" class="chunk-breadcrumb">{{ chunk.heading_path.join(' › ') }}</p><ChunkSemanticMetadata :metadata="chunk.metadata" /><div class="chunk-markdown compact" v-html="renderMarkdownHtml(chunk.content)"></div></article></div>
              </template>
              <div v-else-if="markdownReadyEntries.length" class="preview-empty subtle"><strong>参数尚未验证</strong><p>点击“生成预览”，确认块大小、标题路径和内容边界符合预期。</p></div>
            </section>

            <div class="dialog-actions configuration-footer"><button class="soft-button" type="button" :disabled="isSavingChunkingMethod" @click="chunkingStep === 1 ? closeChunkingDialog() : goToChunkingStep(chunkingStep - 1)">{{ chunkingStep === 1 ? '取消' : '上一步' }}</button><span>第 {{ chunkingStep }} / 3 步</span><button v-if="chunkingStep < 3" type="button" @click="goToChunkingStep(chunkingStep + 1)">下一步</button><button v-else type="submit" :disabled="isSavingChunkingMethod || !chunkingPreviewStats">{{ isSavingChunkingMethod ? '正在注册…' : '保存分块方法' }}</button></div>
          </form>
        </section>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="isEmbeddingDialogOpen" class="dialog-backdrop" @click.self="closeEmbeddingDialog" @keydown.esc="closeEmbeddingDialog">
        <section class="entry-dialog configuration-dialog" role="dialog" aria-modal="true" aria-labelledby="embedding-dialog-title">
          <div class="dialog-heading"><div><p class="eyebrow">Register model</p><h2 id="embedding-dialog-title">注册嵌入模型</h2><p>填写模型标识与调用地址，供知识库构建时复用。</p></div><button class="icon-button" type="button" aria-label="关闭注册嵌入模型窗口" :disabled="isSavingEmbeddingModel" @click="closeEmbeddingDialog">×</button></div>
          <form @submit.prevent="registerEmbeddingModel">
            <label class="full-field"><span>显示名称 <b aria-hidden="true">*</b></span><input v-model.trim="embeddingForm.name" required maxlength="255" placeholder="如：BGE-M3 本地服务" /></label>
            <div class="form-grid"><label><span>提供方</span><select v-model="embeddingForm.provider"><option value="medcpt">MedCPT 本地双编码器</option><option value="local">本地兼容服务</option><option value="openai">OpenAI 兼容</option></select></label><label><span>向量维度</span><input v-model.number="embeddingForm.dimension" type="number" min="1" placeholder="768" :disabled="embeddingForm.provider === 'medcpt'" /></label></div>
            <label class="full-field"><span>文献编码器 <b aria-hidden="true">*</b></span><input v-model.trim="embeddingForm.modelName" required maxlength="512" placeholder="ncbi/MedCPT-Article-Encoder" :disabled="embeddingForm.provider === 'medcpt'" /></label>
            <label class="full-field"><span>服务地址</span><input v-model.trim="embeddingForm.baseUrl" type="url" placeholder="远程兼容服务可填写" :disabled="embeddingForm.provider === 'medcpt'" /></label>
            <p v-if="embeddingForm.provider === 'medcpt'" class="embedding-note">查询使用 ncbi/MedCPT-Query-Encoder，文献分块使用 ncbi/MedCPT-Article-Encoder；注册后点击“下载模型”。</p>
            <div class="dialog-actions"><button class="soft-button" type="button" :disabled="isSavingEmbeddingModel" @click="closeEmbeddingDialog">取消</button><button type="submit" :disabled="isSavingEmbeddingModel">{{ isSavingEmbeddingModel ? '正在注册…' : '确认注册' }}</button></div>
          </form>
        </section>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="isKnowledgeDialogOpen" class="dialog-backdrop" @click.self="closeKnowledgeDialog" @keydown.esc="closeKnowledgeDialog">
        <section class="entry-dialog knowledge-dialog" role="dialog" aria-modal="true" aria-labelledby="knowledge-dialog-title">
          <div class="dialog-heading"><div><p class="eyebrow">New knowledge base</p><h2 id="knowledge-dialog-title">新建知识库</h2><p>依次选择内容与构建配置，创建后仍可查看每篇文献的分块结果。</p></div><button class="icon-button" type="button" aria-label="关闭新建知识库窗口" :disabled="isSavingKnowledgeBase" @click="closeKnowledgeDialog">×</button></div>
          <form @submit.prevent="createKnowledgeBase">
            <section class="build-step"><div class="build-step-title"><span>1</span><div><strong>基本信息</strong><small>用于识别知识库的主题与范围</small></div></div><label class="full-field"><span>知识库名称 <b aria-hidden="true">*</b></span><input v-model.trim="knowledgeForm.name" required maxlength="255" placeholder="如：脑出血急性期治疗证据库" /></label><label class="full-field"><span>说明</span><textarea v-model.trim="knowledgeForm.description" rows="2" placeholder="简要描述收录范围和用途"></textarea></label></section>
            <section class="build-step"><div class="build-step-title"><span>2</span><div><strong>选择 MD 文献</strong><small>这里只显示已经上传或通过 MinerU 生成 MD 的条目</small></div><em>{{ knowledgeForm.documentIds.length }} 篇已选</em></div><div class="document-choice-list"><label v-for="entry in markdownReadyEntries" :key="entry.id" class="document-choice" :class="{ selected: knowledgeForm.documentIds.includes(entry.id) }"><input v-model="knowledgeForm.documentIds" type="checkbox" :value="entry.id" /><span class="entry-icon"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M6 3h9l3 3v15H6z"/><path d="M15 3v4h4M9 12h6M9 16h5"/></svg></span><span><strong>{{ entry.title }}</strong><small>{{ entry.authors.join('、') || '未填写作者' }} · MD 已就绪</small></span></label></div></section>
            <section class="build-step"><div class="build-step-title"><span>3</span><div><strong>构建配置</strong><small>指定分块方法与向量嵌入模型</small></div></div><div class="build-config-grid"><label><span>分块方法 <b aria-hidden="true">*</b></span><select v-model="knowledgeForm.chunkingMethodId" required><option value="" disabled>请选择分块方法</option><option v-for="method in chunkingMethods" :key="method.id" :value="method.id">{{ method.name }}</option></select></label><label><span>嵌入模型 <b aria-hidden="true">*</b></span><select v-model="knowledgeForm.embeddingModelId" required><option value="" disabled>请选择嵌入模型</option><option v-for="model in embeddingModels" :key="model.id" :value="model.id">{{ model.name }}</option></select></label></div></section>
            <div class="dialog-actions"><button class="soft-button" type="button" :disabled="isSavingKnowledgeBase" @click="closeKnowledgeDialog">取消</button><button type="submit" :disabled="isSavingKnowledgeBase || !knowledgeForm.documentIds.length">{{ isSavingKnowledgeBase ? '正在创建…' : '创建知识库' }}</button></div>
          </form>
        </section>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="isCreateDialogOpen" class="dialog-backdrop" @click.self="closeCreateDialog" @keydown.esc="closeCreateDialog">
        <section class="entry-dialog" role="dialog" aria-modal="true" aria-labelledby="create-entry-title">
          <div class="dialog-heading"><div><p class="eyebrow">New reference</p><h2 id="create-entry-title">新建文献条目</h2><p>先保存基本元数据，之后可继续添加一个或多个附件。</p></div><button class="icon-button" type="button" aria-label="关闭新建条目窗口" @click="closeCreateDialog">×</button></div>
          <form @submit.prevent="createEntry">
            <label class="full-field"><span>标题 <b aria-hidden="true">*</b></span><input ref="titleInput" v-model.trim="createForm.title" required maxlength="1000" placeholder="输入文献标题" /></label>
            <div class="form-grid"><label><span>条目类型</span><select v-model="createForm.itemType"><option value="journalArticle">期刊论文</option><option value="guideline">临床指南</option><option value="report">报告</option><option value="bookSection">书籍章节</option><option value="document">其他文档</option></select></label><label><span>出版年份</span><input v-model.number="createForm.year" type="number" min="1600" max="2200" placeholder="2026" /></label></div>
            <label class="full-field"><span>作者</span><input v-model.trim="createForm.authors" placeholder="多位作者用逗号分隔" /><small>机构作者可直接填写完整机构名称。</small></label>
            <label class="full-field"><span>DOI</span><input v-model.trim="createForm.doi" placeholder="10.xxxx/xxxxx" /></label>
            <label class="full-field"><span>摘要</span><textarea v-model.trim="createForm.abstract" rows="4" placeholder="可选，稍后也可以补充"></textarea></label>
            <div class="dialog-actions"><button class="soft-button" type="button" :disabled="isSavingEntry" @click="closeCreateDialog">取消</button><button type="submit" :disabled="isSavingEntry">{{ isSavingEntry ? '正在保存…' : '创建条目' }}</button></div>
          </form>
        </section>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="isUploadDialogOpen" class="dialog-backdrop" @click.self="closeUploadDialog" @keydown.esc="closeUploadDialog">
        <section class="entry-dialog upload-dialog" role="dialog" aria-modal="true" aria-labelledby="upload-title">
          <div class="dialog-heading"><div><p class="eyebrow">Import attachments</p><h2 id="upload-title">添加文件</h2><p>支持批量选择 PDF 和 Markdown；每个条目只保留一份 PDF 与一份 MD。</p></div><button class="icon-button" type="button" aria-label="关闭添加文件窗口" @click="closeUploadDialog">×</button></div>
          <div class="upload-step-heading"><span>1</span><div><strong>选择文件</strong><small>新建模式下每个文件生成独立条目；已有条目最多选择一份 PDF 和一份 MD</small></div></div>
          <input ref="uploadInput" class="visually-hidden" type="file" accept="application/pdf,text/markdown,.pdf,.md" multiple @change="onUploadFiles" />
          <button class="file-picker" type="button" @click="chooseUploadFiles"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 16V4M7 9l5-5 5 5"/><path d="M5 14v5h14v-5"/></svg><span><strong>{{ selectedUploadFiles.length ? '继续选择文件' : '选择 PDF 或 Markdown' }}</strong><small>单个文件不超过 100 MB</small></span></button>
          <ul v-if="selectedUploadFiles.length" class="selected-files" aria-label="待上传文件"><li v-for="(file, index) in selectedUploadFiles" :key="`${file.name}-${file.size}`"><span class="pdf-icon" :class="{ markdown: file.name.toLowerCase().endsWith('.md') }">{{ file.name.toLowerCase().endsWith('.md') ? 'MD' : 'PDF' }}</span><span><strong>{{ file.name }}</strong><small>{{ formatFileSize(file.size) }}</small></span><button type="button" :aria-label="`移除 ${file.name}`" @click="removeUploadFile(index)">×</button></li></ul>
          <div class="upload-step-heading destination-heading"><span>2</span><div><strong>选择保存位置</strong><small>创建新条目，或添加到现有文献条目</small></div></div>
          <div class="upload-destination" role="group" aria-label="文件保存位置">
            <button class="upload-choice" :class="{ selected: uploadDestination === 'new' }" type="button" :aria-pressed="uploadDestination === 'new'" @click="setUploadDestination('new')"><span class="choice-indicator"><svg v-if="uploadDestination === 'new'" aria-hidden="true" viewBox="0 0 24 24"><path d="m7 12 3 3 7-7"/></svg></span><span><strong>创建新条目</strong><small>每个文件创建一个条目，条目名默认使用文件名。</small></span></button>
            <button class="upload-choice" :class="{ selected: uploadDestination === 'existing' }" type="button" :aria-pressed="uploadDestination === 'existing'" :disabled="!entries.length" @click="setUploadDestination('existing')"><span class="choice-indicator"><svg v-if="uploadDestination === 'existing'" aria-hidden="true" viewBox="0 0 24 24"><path d="m7 12 3 3 7-7"/></svg></span><span><strong>添加到已有条目</strong><small>{{ entries.length ? '同类型文件已存在时，会在上传前询问是否替代。' : '暂无可用条目，请先创建新条目。' }}</small></span></button>
          </div>
          <label v-if="uploadDestination === 'existing'" class="full-field target-field"><span>目标条目</span><select v-model="uploadTargetId"><option value="" disabled>请选择条目</option><option v-for="entry in entries" :key="entry.id" :value="entry.id">{{ entry.title }}</option></select></label>
          <div class="dialog-actions"><button class="soft-button" type="button" :disabled="isUploading" @click="closeUploadDialog">取消</button><button type="button" :disabled="isUploading || !selectedUploadFiles.length" @click="submitUpload">{{ isUploading ? '正在上传并识别…' : selectedUploadFiles.length ? `上传 ${selectedUploadFiles.length} 个文件` : '开始上传' }}</button></div>
        </section>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="previewAttachment" class="dialog-backdrop preview-backdrop" @click.self="closePreview" @keydown.esc="closePreview">
        <section class="preview-dialog" role="dialog" aria-modal="true" aria-labelledby="preview-title">
          <div class="preview-toolbar"><div><span class="pdf-icon" :class="{ markdown: attachmentKind(previewAttachment) === 'MD' }">{{ attachmentKind(previewAttachment) }}</span><h2 id="preview-title">{{ previewAttachment.name }}</h2></div><div><button class="soft-button" type="button" @click="openFileInNewWindow(previewAttachment)">查看原文件</button><button class="soft-button" type="button" @click="downloadFile(previewAttachment)">下载</button><button class="icon-button" type="button" aria-label="关闭预览" @click="closePreview">×</button></div></div>
          <div v-if="attachmentKind(previewAttachment) === 'MD'" class="markdown-preview-shell">
            <div v-if="isMarkdownPreviewLoading" class="preview-state" role="status"><span class="loading-ring"></span><p>正在排版 Markdown…</p></div>
            <div v-else-if="markdownPreviewError" class="preview-state error" role="alert"><p>{{ markdownPreviewError }}</p><button type="button" @click="previewFile(previewAttachment)">重新加载</button></div>
            <article v-else class="markdown-body" v-html="markdownPreviewHtml"></article>
          </div>
          <iframe v-else :src="attachmentContentUrl(previewAttachment)" :title="`${previewAttachment.name} 预览`"></iframe>
        </section>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="movingAttachment" class="dialog-backdrop" @click.self="!isMoving && (movingAttachment = null)" @keydown.esc="!isMoving && (movingAttachment = null)">
        <section class="entry-dialog move-dialog" role="dialog" aria-modal="true" aria-labelledby="move-title">
          <div class="dialog-heading"><div><p class="eyebrow">Move attachment</p><h2 id="move-title">移动附件</h2><p>“{{ movingAttachment.name }}”将从当前条目移动到目标条目。</p></div><button class="icon-button" type="button" aria-label="关闭移动附件窗口" :disabled="isMoving" @click="movingAttachment = null">×</button></div>
          <label class="full-field"><span>目标条目</span><select v-model="moveTargetId"><option v-for="entry in moveTargets" :key="entry.id" :value="entry.id">{{ entry.title }}</option></select></label>
          <div class="dialog-actions"><button class="soft-button" type="button" :disabled="isMoving" @click="movingAttachment = null">取消</button><button type="button" :disabled="isMoving || !moveTargetId" @click="moveFile">{{ isMoving ? '正在移动…' : '确认移动' }}</button></div>
        </section>
      </div>
    </Teleport>
  </div>
</template>
