import type {
  DocumentChunks, GeneratedQuestionBatch, KnowledgeBaseSummary, QuestionBankDetail,
  QuestionBankSummary, QuestionInput, QuestionStyle, QuestionType, StoredQuestion,
} from './types'

const API_BASE = '/api/evaluation/api/v1'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: { 'Content-Type': 'application/json', ...options?.headers },
    })
  } catch {
    throw new Error('无法连接评测服务，请确认 evaluation-api 已启动')
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: unknown } | null
    const detail = typeof body?.detail === 'string' ? body.detail : `请求失败（${response.status}）`
    throw new Error(detail)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const fetchQuestionBanks = () => request<QuestionBankSummary[]>('/question-banks')
export const fetchQuestionBank = (id: string) =>
  request<QuestionBankDetail>(`/question-banks/${encodeURIComponent(id)}`)
export const createQuestionBank = (payload: { name: string; description?: string }) =>
  request<QuestionBankSummary>('/question-banks', { method: 'POST', body: JSON.stringify(payload) })

export function fetchQuestions(filters?: { bankId?: string; type?: QuestionType; query?: string }) {
  const params = new URLSearchParams()
  if (filters?.bankId) params.set('bank_id', filters.bankId)
  if (filters?.type) params.set('question_type', filters.type)
  if (filters?.query) params.set('query', filters.query)
  return request<StoredQuestion[]>(`/questions${params.size ? `?${params.toString()}` : ''}`)
}

export const createQuestion = (bankId: string, payload: QuestionInput) =>
  request<StoredQuestion>(`/question-banks/${encodeURIComponent(bankId)}/questions`, {
    method: 'POST', body: JSON.stringify(payload),
  })
export const createQuestionsBulk = (bankId: string, questions: QuestionInput[]) =>
  request<StoredQuestion[]>(`/question-banks/${encodeURIComponent(bankId)}/questions/bulk`, {
    method: 'POST', body: JSON.stringify({ questions }),
  })
export const fetchKnowledgeBases = () => request<KnowledgeBaseSummary[]>('/knowledge-bases')
export const fetchDocumentChunks = (collectionId: string, documentId: string) =>
  request<DocumentChunks>(`/knowledge-bases/${encodeURIComponent(collectionId)}/documents/${encodeURIComponent(documentId)}/chunks`)
export const generateQuestions = (payload: {
  collection_id: string
  document_id: string
  chunk_indexes: number[]
  question_count: number
  question_type: QuestionType
  question_style: QuestionStyle
  instructions?: string
}) => request<GeneratedQuestionBatch>('/questions/generate', {
  method: 'POST', body: JSON.stringify(payload),
})
