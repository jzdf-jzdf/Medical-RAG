export interface DocumentSummary {
  id: string
  title: string
}

export interface KnowledgeBaseSummary {
  id: string
  name: string
  description?: string | null
  status: string
  documents: DocumentSummary[]
}

export interface ChunkItem {
  index: number
  content: string
  embedding_text?: string | null
  char_start: number
  char_end: number
  char_count: number
  heading_path: string[]
}

export interface ChunkStats {
  total: number
  average_chars: number
  minimum_chars: number
  maximum_chars: number
  too_short: number
  too_long: number
}

export interface DocumentChunks {
  document_id: string
  document_title: string
  method_name: string
  method_strategy: string
  chunks: ChunkItem[]
  stats: ChunkStats
}

export interface EvidenceExcerpt {
  chunk_index: number
  heading: string
  quote: string
}

export interface GeneratedQuestion {
  question: string
  standard_answer: string
  difficulty: string
  topic: string
  rationale: string
  evidence: EvidenceExcerpt[]
  source_chunks: ChunkItem[]
  agent_run_id: string
  model: string
  provider: string
}

export interface AgentTestResult {
  answer: string
  expected_answer?: string | null
  run_id: string
  model: string
  provider: string
  degraded_without_evidence: boolean
  citations: Record<string, unknown>[]
  tool_calls: Record<string, unknown>[]
}
