<script setup lang="ts">
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { computed, nextTick, onMounted, reactive, ref } from 'vue'

type Tab = 'welcome' | 'documents' | 'knowledge' | 'tasks'
type NavigationTab = Exclude<Tab, 'welcome'>
type NoticeKind = 'success' | 'error' | 'info'

interface Attachment {
  id: string
  name: string
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
    media_type: string
    size_bytes: number
    created_at: string
  }>
  created_at: string
}

const apiBase = import.meta.env.VITE_API_BASE_URL || '/api/knowledge'
const activeTab = ref<Tab>('welcome')
const serviceState = ref<'checking' | 'online' | 'offline'>('checking')
const tabs: Array<{ id: NavigationTab; label: string; hint: string }> = [
  { id: 'documents', label: '文献管理', hint: '24' },
  { id: 'knowledge', label: '知识库管理', hint: '06' },
  { id: 'tasks', label: '解析任务', hint: '03' },
]

const recentDocuments = [
  { title: '中国脑出血诊治指南（2024）', meta: '指南 · 142 个知识块', state: '已审核' },
  { title: '急性脑出血血压管理循证更新', meta: '系统综述 · 86 个知识块', state: '解析完成' },
  { title: '自发性脑出血外科治疗进展', meta: '综述 · 64 个知识块', state: '待审核' },
]

const sampleEntries: LibraryEntry[] = [
  {
    id: 'sample-1',
    title: '中国脑出血诊治指南（2024）',
    itemType: 'guideline',
    authors: ['中华医学会神经病学分会', '中华医学会神经外科学分会'],
    year: 2024,
    doi: '10.3760/cma.j.cn113694-20240118-00042',
    abstract: '围绕自发性脑出血的院前处理、急诊评估、内外科治疗及康复管理形成循证推荐。',
    attachments: [
      { id: 'file-1', name: '中国脑出血诊治指南_2024.pdf', mediaType: 'application/pdf', sizeBytes: 8_934_112 },
      { id: 'file-2', name: '指南补充材料.pdf', mediaType: 'application/pdf', sizeBytes: 1_642_005 },
    ],
  },
  {
    id: 'sample-2',
    title: '急性脑出血血压管理循证更新',
    itemType: 'journalArticle',
    authors: ['王宁', '李睿', '周峰'],
    year: 2025,
    attachments: [
      { id: 'file-3', name: 'ICH_血压管理循证更新.pdf', mediaType: 'application/pdf', sizeBytes: 4_268_314 },
    ],
  },
  {
    id: 'sample-3',
    title: '自发性脑出血外科治疗进展',
    itemType: 'journalArticle',
    authors: ['陈宇', '许博'],
    year: 2025,
    attachments: [],
  },
]

const entries = ref<LibraryEntry[]>(sampleEntries)
const selectedEntryId = ref<string | null>(sampleEntries[0].id)
const expandedEntryIds = ref<Set<string>>(new Set([sampleEntries[0].id]))
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
const isDeletingEntry = ref(false)
const titleInput = ref<HTMLInputElement | null>(null)
const notice = ref<{ kind: NoticeKind; message: string } | null>(null)

const createForm = reactive({
  title: '',
  itemType: 'journalArticle',
  authors: '',
  year: undefined as number | undefined,
  doi: '',
  abstract: '',
})

const libraries = [
  { name: '脑出血临床指南库', detail: '12 篇文献 · 486 个知识块', tone: 'sage' },
  { name: '急性期治疗证据库', detail: '8 篇文献 · 392 个知识块', tone: 'blue' },
  { name: '外科与康复研究库', detail: '17 篇文献 · 406 个知识块', tone: 'peach' },
]
const tasks = [
  { name: 'AHA/ASA 脑出血管理指南.pdf', parser: 'MinerU', progress: '82%', state: '解析中' },
  { name: '微创手术随机对照研究.pdf', parser: 'Docling', progress: '100%', state: '待审核' },
  { name: '院内病例资料扫描件.pdf', parser: 'OCR + MinerU', progress: '36%', state: '识别中' },
]

const filteredEntries = computed(() => {
  const query = searchQuery.value.trim().toLocaleLowerCase()
  return entries.value.filter((entry) => {
    const matchesType = itemFilter.value === 'all' || entry.itemType === itemFilter.value
    const haystack = [entry.title, entry.doi, entry.authors.join(' ')].filter(Boolean).join(' ').toLocaleLowerCase()
    return matchesType && (!query || haystack.includes(query))
  })
})

const selectedEntry = computed(() => entries.value.find((entry) => entry.id === selectedEntryId.value) ?? null)
const totalAttachments = computed(() => entries.value.reduce((total, entry) => total + entry.attachments.length, 0))
const moveTargets = computed(() => entries.value.filter((entry) => entry.id !== selectedEntryId.value))

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
      mediaType: file.media_type,
      sizeBytes: file.size_bytes,
      createdAt: file.created_at,
    })),
    createdAt: document.created_at,
  }
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
  try {
    const body = await response.json()
    return typeof body.detail === 'string' ? body.detail : '请求未完成，请重试'
  } catch {
    return '请求未完成，请检查服务连接后重试'
  }
}

async function loadEntries(preferredId?: string | null): Promise<void> {
  try {
    const response = await fetch(`${apiBase}/api/v1/documents?page_size=100`)
    if (!response.ok) return
    const body = await response.json() as { items: ApiDocument[] }
    entries.value = body.items.map(mapApiDocument)
    const nextId = preferredId && entries.value.some((entry) => entry.id === preferredId)
      ? preferredId
      : entries.value[0]?.id ?? null
    selectedEntryId.value = nextId
    if (nextId) expandedEntryIds.value = new Set([...expandedEntryIds.value, nextId])
  } catch {
    // Keep preview data available while the API is offline.
  }
}

async function checkService(): Promise<void> {
  try {
    const response = await fetch(`${apiBase}/health`)
    serviceState.value = response.ok ? 'online' : 'offline'
  } catch {
    serviceState.value = 'offline'
  }
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
    showNotice('success', '文献条目已创建，可继续添加附件')
  } catch (error) {
    showNotice('error', error instanceof Error ? error.message : '创建条目失败，请重试')
  } finally {
    isSavingEntry.value = false
  }
}

function attachmentKind(attachment: Attachment): string {
  return attachment.name.toLocaleLowerCase().endsWith('.md') ? 'MD' : 'PDF'
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
  if (!selectedUploadFiles.value.length) {
    showNotice('error', '请先选择 PDF 或 Markdown 文件')
    return
  }
  if (uploadDestination.value === 'existing' && !uploadTargetId.value) {
    showNotice('error', '请选择要添加附件的条目')
    return
  }
  const formData = new FormData()
  selectedUploadFiles.value.forEach((file) => formData.append('files', file))
  const endpoint = uploadDestination.value === 'new'
    ? `${apiBase}/api/v1/documents/from-files`
    : `${apiBase}/api/v1/documents/${uploadTargetId.value}/files/batch`
  isUploading.value = true
  try {
    const response = await fetch(endpoint, { method: 'POST', body: formData })
    if (!response.ok) throw new Error(await responseError(response))
    const result = await response.json() as ApiDocument | ApiDocument[]
    const preferredId = Array.isArray(result) ? result[0]?.id : result.id
    await loadEntries(preferredId)
    isUploadDialogOpen.value = false
    showNotice(
      'success',
      uploadDestination.value === 'new'
        ? `已创建 ${selectedUploadFiles.value.length} 个条目并识别元数据`
        : `已添加 ${selectedUploadFiles.value.length} 个附件`,
    )
  } catch (error) {
    showNotice('error', error instanceof Error ? error.message : '文件上传失败，请重试')
  } finally {
    isUploading.value = false
  }
}

function stripMarkdownFrontmatter(markdown: string): string {
  return markdown.replace(/^\uFEFF?---\s*\r?\n[\s\S]*?\r?\n---\s*(?:\r?\n|$)/, '')
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
    const markdown = stripMarkdownFrontmatter(await response.text())
    const rendered = await marked.parse(markdown, { gfm: true, breaks: false })
    const sanitized = DOMPurify.sanitize(rendered)
    const container = document.createElement('div')
    container.innerHTML = sanitized
    container.querySelectorAll('a').forEach((link) => {
      link.target = '_blank'
      link.rel = 'noopener noreferrer'
    })
    if (previewAttachment.value?.id === attachment.id) {
      markdownPreviewHtml.value = container.innerHTML || '<p>此 Markdown 文件暂无正文。</p>'
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
    await loadEntries(selectedEntryId.value)
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
    await loadEntries(targetId)
    showNotice('success', '附件已移动到目标条目')
  } catch (error) {
    showNotice('error', error instanceof Error ? error.message : '附件移动失败')
  } finally {
    isMoving.value = false
  }
}

async function deleteSelectedEntry(): Promise<void> {
  if (!selectedEntry.value || !window.confirm(`确定删除条目“${selectedEntry.value.title}”吗？其全部附件文件也会被清理。`)) return
  isDeletingEntry.value = true
  try {
    const response = await fetch(`${apiBase}/api/v1/documents/${selectedEntry.value.id}`, { method: 'DELETE' })
    if (!response.ok) throw new Error(await responseError(response))
    await loadEntries(null)
    showNotice('success', '条目及其附件已删除')
  } catch (error) {
    showNotice('error', error instanceof Error ? error.message : '条目删除失败')
  } finally {
    isDeletingEntry.value = false
  }
}

onMounted(() => {
  void Promise.all([checkService(), loadEntries()])
})
</script>

<template>
  <div class="shell">
    <aside>
      <button class="brand" type="button" aria-label="返回欢迎页" @click="activeTab = 'welcome'">
        <span class="brand-mark">ICH</span>
        <span class="brand-copy"><strong>循证库</strong><small>脑出血专病知识服务</small></span>
      </button>
      <nav aria-label="知识服务导航">
        <button v-for="tab in tabs" :key="tab.id" class="nav-item" :class="{ active: activeTab === tab.id }" :aria-current="activeTab === tab.id ? 'page' : undefined" type="button" @click="activeTab = tab.id">
          <span>{{ tab.label }}</span><small>{{ tab.hint }}</small>
        </button>
      </nav>
      <div class="aside-foot"><i :class="serviceState"></i>知识服务 {{ serviceState === 'online' ? '在线' : serviceState === 'offline' ? '未连接' : '检测中' }}</div>
    </aside>

    <main>
      <template v-if="activeTab === 'welcome'">
        <header><div><p class="eyebrow">Knowledge workspace</p><h1>从文献到<span>可追溯证据</span></h1><p>集中管理脑出血研究，让每条医学结论都能回到原始页码与版本。</p></div><button type="button" @click="activeTab = 'documents'">进入文献管理</button></header>
        <section class="signal"><div><b>24</b><span>本周新增文献</span></div><div><b>1,284</b><span>可检索知识块</span></div><div><b>93%</b><span>已审核证据</span></div><div class="flow"><span>PDF</span><em></em><span>章节</span><em></em><span>Chunk</span><em></em><span>证据</span></div></section>
        <section class="content"><div class="section-title"><div><p class="eyebrow">Recent library</p><h2>最近处理</h2></div><button class="text-action" type="button" @click="activeTab = 'documents'">查看全部 →</button></div><div class="doc-list"><article v-for="(doc, i) in recentDocuments" :key="doc.title"><span class="file-no">0{{ i + 1 }}</span><div><h3>{{ doc.title }}</h3><p>{{ doc.meta }}</p></div><span class="tag">{{ doc.state }}</span></article></div></section>
      </template>

      <template v-else-if="activeTab === 'documents'">
        <header class="page-header document-header">
          <div><p class="eyebrow">Reference library</p><h1>文献管理</h1><p>以条目组织元数据，以附件保留可追溯的原始文件。</p></div>
          <div class="header-actions">
            <button class="soft-button" type="button" :disabled="isUploading" @click="openUploadDialog('new')"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/><path d="M4 19.5V21h16v-1.5"/></svg>添加文件</button>
            <button type="button" @click="openCreateDialog"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M6 3h9l3 3v15H6z"/><path d="M9 12h6M12 9v6"/></svg>新建条目</button>
          </div>
        </header>

        <div v-if="notice" class="notice" :class="notice.kind" :role="notice.kind === 'error' ? 'alert' : 'status'"><span>{{ notice.message }}</span><button type="button" aria-label="关闭提示" @click="notice = null">×</button></div>

        <section class="document-toolbar" aria-label="文献筛选">
          <label class="search-field"><span>搜索条目</span><span class="input-wrap"><svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="11" cy="11" r="6"/><path d="m16 16 4 4"/></svg><input v-model.trim="searchQuery" type="search" placeholder="标题、作者或 DOI" /></span></label>
          <label class="filter-field"><span>条目类型</span><select v-model="itemFilter"><option value="all">全部类型</option><option value="journalArticle">期刊论文</option><option value="guideline">临床指南</option><option value="report">报告</option><option value="document">文档</option></select></label>
          <div class="library-summary"><strong>{{ entries.length }}</strong><span>个条目</span><strong>{{ totalAttachments }}</strong><span>个附件</span></div>
        </section>

        <section class="reference-workspace" aria-label="文献条目工作区">
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
              <div class="inspector-heading"><span>{{ itemTypeLabel(selectedEntry.itemType) }}</span><button class="danger-link" type="button" :disabled="isDeletingEntry" @click="deleteSelectedEntry">{{ isDeletingEntry ? '正在删除…' : '删除条目' }}</button></div>
              <h2>{{ selectedEntry.title }}</h2>
              <p class="inspector-authors">{{ selectedEntry.authors.join('、') || '未填写作者' }}</p>
              <dl><div><dt>年份</dt><dd>{{ selectedEntry.year || '—' }}</dd></div><div><dt>DOI</dt><dd>{{ selectedEntry.doi || '—' }}</dd></div><div><dt>附件</dt><dd>{{ selectedEntry.attachments.length }} 个</dd></div></dl>
              <div class="abstract-block"><h3>摘要</h3><p>{{ selectedEntry.abstract || '暂无摘要。' }}</p></div>
              <div class="inspector-attachments"><div><h3>附件</h3><button class="text-action" type="button" :disabled="isUploading" @click="openUploadDialog('existing')">＋ 添加附件</button></div><ul v-if="selectedEntry.attachments.length"><li v-for="attachment in selectedEntry.attachments" :key="attachment.id"><span class="pdf-icon" :class="{ markdown: attachmentKind(attachment) === 'MD' }">{{ attachmentKind(attachment) }}</span><span class="attachment-copy"><strong>{{ attachment.name }}</strong><small>{{ formatFileSize(attachment.sizeBytes) }}</small></span><span class="attachment-actions"><button type="button" @click="previewFile(attachment)">预览</button><button type="button" @click="downloadFile(attachment)">下载</button><button type="button" :disabled="!moveTargets.length" @click="openMoveDialog(attachment)">移动</button><button class="danger" type="button" :disabled="deletingAttachmentId === attachment.id" @click="deleteAttachment(attachment)">{{ deletingAttachmentId === attachment.id ? '删除中' : '删除' }}</button></span></li></ul><p v-else>暂无附件，可添加 PDF 或 Markdown 文件。</p></div>
            </template>
            <div v-else class="empty-inspector"><h2>选择一个条目</h2><p>在左侧查看文献元数据与附件。</p></div>
          </section>
        </section>
      </template>

      <template v-else-if="activeTab === 'knowledge'">
        <header class="page-header"><div><p class="eyebrow">Knowledge collections</p><h1>知识库管理</h1><p>按临床主题组织文献、知识块和经过审核的证据。</p></div><button type="button">＋ 新建知识库</button></header>
        <section class="library-grid"><article v-for="library in libraries" :key="library.name" class="library-card" :class="library.tone"><span class="library-orb"></span><h2>{{ library.name }}</h2><p>{{ library.detail }}</p><button class="text-action" type="button">打开知识库 →</button></article></section>
      </template>

      <template v-else>
        <header class="page-header"><div><p class="eyebrow">Parsing queue</p><h1>解析任务</h1><p>跟踪 PDF 转换、章节恢复、分块与结构化抽取进度。</p></div><button type="button">刷新状态</button></header>
        <section class="content task-panel"><div class="section-title"><div><p class="eyebrow">Active jobs</p><h2>当前任务</h2></div><span class="count">3 个任务</span></div><div class="task-list"><article v-for="task in tasks" :key="task.name"><div><h3>{{ task.name }}</h3><p>{{ task.parser }}</p></div><div class="progress"><i :style="{ width: task.progress }"></i></div><b>{{ task.progress }}</b><span class="tag">{{ task.state }}</span></article></div></section>
      </template>
    </main>

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
          <div class="dialog-heading"><div><p class="eyebrow">Import attachments</p><h2 id="upload-title">添加文件</h2><p>支持 PDF 和 Markdown，可一次选择多个文件。</p></div><button class="icon-button" type="button" aria-label="关闭添加文件窗口" @click="closeUploadDialog">×</button></div>
          <div class="upload-step-heading"><span>1</span><div><strong>选择文件</strong><small>可一次添加多个 PDF 或 Markdown 文件</small></div></div>
          <input ref="uploadInput" class="visually-hidden" type="file" accept="application/pdf,text/markdown,.pdf,.md" multiple @change="onUploadFiles" />
          <button class="file-picker" type="button" @click="chooseUploadFiles"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 16V4M7 9l5-5 5 5"/><path d="M5 14v5h14v-5"/></svg><span><strong>{{ selectedUploadFiles.length ? '继续选择文件' : '选择 PDF 或 Markdown' }}</strong><small>单个文件不超过 100 MB</small></span></button>
          <ul v-if="selectedUploadFiles.length" class="selected-files" aria-label="待上传文件"><li v-for="(file, index) in selectedUploadFiles" :key="`${file.name}-${file.size}`"><span class="pdf-icon" :class="{ markdown: file.name.toLowerCase().endsWith('.md') }">{{ file.name.toLowerCase().endsWith('.md') ? 'MD' : 'PDF' }}</span><span><strong>{{ file.name }}</strong><small>{{ formatFileSize(file.size) }}</small></span><button type="button" :aria-label="`移除 ${file.name}`" @click="removeUploadFile(index)">×</button></li></ul>
          <div class="upload-step-heading destination-heading"><span>2</span><div><strong>选择保存位置</strong><small>创建新条目，或添加到现有文献条目</small></div></div>
          <div class="upload-destination" role="group" aria-label="文件保存位置">
            <button class="upload-choice" :class="{ selected: uploadDestination === 'new' }" type="button" :aria-pressed="uploadDestination === 'new'" @click="setUploadDestination('new')"><span class="choice-indicator"><svg v-if="uploadDestination === 'new'" aria-hidden="true" viewBox="0 0 24 24"><path d="m7 12 3 3 7-7"/></svg></span><span><strong>创建新条目</strong><small>每个文件创建一个条目，PDF 自动识别元数据。</small></span></button>
            <button class="upload-choice" :class="{ selected: uploadDestination === 'existing' }" type="button" :aria-pressed="uploadDestination === 'existing'" :disabled="!entries.length" @click="setUploadDestination('existing')"><span class="choice-indicator"><svg v-if="uploadDestination === 'existing'" aria-hidden="true" viewBox="0 0 24 24"><path d="m7 12 3 3 7-7"/></svg></span><span><strong>添加到已有条目</strong><small>{{ entries.length ? '全部文件作为附件添加到同一个条目。' : '暂无可用条目，请先创建新条目。' }}</small></span></button>
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
