const API_BASE = import.meta.env.VITE_AGENT_API_BASE || '/api/agent'
const KNOWLEDGE_API_BASE = import.meta.env.VITE_KNOWLEDGE_API_BASE || '/api/knowledge'

export interface KnowledgeBaseOption {
  id: string
  name: string
  status: string
}

export interface ConversationSummary {
  id: string
  title: string
  user_id: string
  created_at: string
  updated_at: string
}

export interface StoredMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  created_at: string
  result?: ChatResult | null
}

export interface ConversationDetail extends ConversationSummary {
  messages: StoredMessage[]
}

export interface ToolCall {
  server: string
  tool: string
  arguments: Record<string, unknown>
  result: unknown
  result_hash: string
  latency_ms: number
  status: 'completed' | 'failed'
  is_error: boolean
}

export interface Citation {
  document_id?: string
  chunk_id?: string
  index_version?: string
  title?: string
  heading?: string
  page_start?: number | null
  page_end?: number | null
  text?: string
  score?: number | null
  rank?: number | null
}

export interface Verification {
  passed: boolean
  mode: string
  issues: string[]
  claim_count?: number
  supported_claims?: number
  citation_count: number
  semantic_passed?: boolean
  claims?: SemanticClaim[]
}

export interface SemanticClaim {
  claim_id: number
  text: string
  citation_ids: number[]
  verified: boolean
  supported: boolean
  evidence: Array<{ citation_id: number; quote: string }>
  reason?: string
}

export interface ChatResult {
  run_id: string
  message_id: string
  content: string
  status?: 'completed' | 'failed'
  citations: Citation[]
  degraded_without_evidence: boolean
  model: string
  provider: string
  tool_calls: ToolCall[]
  verification: Verification
  trace: Array<Record<string, unknown>>
}

export interface ModelConfig {
  id: string
  name: string
  provider: string
  model: string
  base_url: string
  max_tokens: number
  temperature: number
  is_default: boolean
  is_active: boolean
  has_api_key: boolean
}

export interface ModelConfigInput {
  name: string
  provider: string
  model: string
  base_url: string
  api_key?: string
  max_tokens: number
  temperature: number
  is_default: boolean
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init)
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    const detail = payload && typeof payload.detail === 'string' ? payload.detail : '请求失败'
    throw new Error(detail)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export function getHealth() {
  return request<{ status: string }>('/health')
}

export async function listConversations(userId: string) {
  const payload = await request<{ items: ConversationSummary[] }>(
    '/api/v1/conversations?page=1&page_size=100',
    { headers: { 'X-User-ID': userId } },
  )
  return payload.items
}

export function getConversation(conversationId: string, userId: string) {
  return request<ConversationDetail>(`/api/v1/conversations/${conversationId}`, {
    headers: { 'X-User-ID': userId },
  })
}

export function createConversation(userId: string, title: string) {
  return request<ConversationSummary>('/api/v1/conversations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, title }),
  })
}

export function deleteConversation(conversationId: string, userId: string) {
  return request<void>(`/api/v1/conversations/${conversationId}`, {
    method: 'DELETE',
    headers: { 'X-User-ID': userId },
  })
}

export function sendChat(
  conversationId: string,
  userId: string,
  content: string,
  modelConfigId?: string,
  collectionId?: string,
) {
  return request<ChatResult>('/api/v1/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      conversation_id: conversationId,
      user_id: userId,
      content,
      model_config_id: modelConfigId || null,
      collection_id: collectionId || null,
    }),
  })
}

export async function listKnowledgeBases() {
  const response = await fetch(`${KNOWLEDGE_API_BASE}/api/v1/knowledge-bases`)
  if (!response.ok) throw new Error('读取知识库列表失败')
  const items = await response.json() as KnowledgeBaseOption[]
  return items.filter((item) => item.status === 'indexed')
}

export function listModels() {
  return request<ModelConfig[]>('/api/v1/models')
}

export function createModel(payload: ModelConfigInput) {
  return request<ModelConfig>('/api/v1/models', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function updateModel(id: string, payload: Partial<ModelConfigInput>) {
  return request<ModelConfig>(`/api/v1/models/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function deleteModel(id: string) {
  return request<void>(`/api/v1/models/${id}`, { method: 'DELETE' })
}
