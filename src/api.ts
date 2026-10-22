const API_BASE = import.meta.env.VITE_AGENT_API_BASE || '/api/agent'

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

export interface ChatResult {
  run_id: string
  message_id: string
  content: string
  citations: Array<Record<string, unknown>>
  degraded_without_evidence: boolean
  model: string
  provider: string
  tool_calls: ToolCall[]
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
) {
  return request<ChatResult>('/api/v1/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      conversation_id: conversationId,
      user_id: userId,
      content,
      model_config_id: modelConfigId || null,
    }),
  })
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
