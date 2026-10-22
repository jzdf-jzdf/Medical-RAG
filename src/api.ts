import type {
  AgentTestResult,
  DocumentChunks,
  GeneratedQuestion,
  KnowledgeBaseSummary,
} from './types'

const API_BASE = '/api/evaluation/api/v1'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...options?.headers,
      },
    })
  } catch {
    throw new Error('无法连接测试服务，请确认 evaluation-api 已启动')
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: unknown } | null
    const detail = typeof body?.detail === 'string' ? body.detail : `请求失败（${response.status}）`
    throw new Error(detail)
  }
  return response.json() as Promise<T>
}

export function fetchKnowledgeBases() {
  return request<KnowledgeBaseSummary[]>('/knowledge-bases')
}

export function fetchDocumentChunks(collectionId: string, documentId: string) {
  return request<DocumentChunks>(
    `/knowledge-bases/${encodeURIComponent(collectionId)}/documents/${encodeURIComponent(documentId)}/chunks`,
  )
}

export function generateQuestion(payload: {
  collection_id: string
  document_id: string
  chunk_indexes: number[]
  instructions?: string
}) {
  return request<GeneratedQuestion>('/questions/generate', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function runAgentTest(payload: { question: string; expected_answer?: string }) {
  return request<AgentTestResult>('/agent-tests', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}
