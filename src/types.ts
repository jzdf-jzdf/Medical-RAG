export type QuestionType = 'qa' | 'single_choice'
export type QuestionStyle = 'patient' | 'clinician' | 'professional' | 'public'
export type QuestionSource = 'manual' | 'generated'

export interface DocumentSummary { id: string; title: string }
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
export interface ChoiceOption { key: string; text: string }
export interface EvidenceExcerpt { chunk_index: number; heading: string; quote: string }
export interface GeneratedQuestion {
  question_type: QuestionType
  question: string
  standard_answer: string
  options: ChoiceOption[]
  correct_answer?: string | null
  difficulty: string
  topic: string
  rationale: string
  evidence: EvidenceExcerpt[]
  source_chunks: ChunkItem[]
  agent_run_id: string
  model: string
  provider: string
}
export interface GeneratedQuestionBatch {
  questions: GeneratedQuestion[]
  requested_count: number
  question_type: QuestionType
  question_style: QuestionStyle
}
export interface QuestionBankSummary {
  id: string
  name: string
  description?: string | null
  version: string
  question_count: number
  qa_count: number
  choice_count: number
  created_at: string
  updated_at: string
}
export interface StoredQuestion {
  id: string
  bank_id: string
  bank_name: string
  question_type: QuestionType
  prompt: string
  standard_answer: string
  options: ChoiceOption[]
  correct_answer?: string | null
  source: QuestionSource
  evidence: Record<string, unknown>[]
  metadata: Record<string, unknown>
  created_at: string
}
export interface QuestionBankDetail extends QuestionBankSummary { questions: StoredQuestion[] }
export interface QuestionInput {
  question_type: QuestionType
  prompt: string
  standard_answer: string
  options?: ChoiceOption[]
  correct_answer?: string | null
  source?: QuestionSource
  evidence?: Record<string, unknown>[]
  metadata?: Record<string, unknown>
}
export interface TestRunSummary {
  id: string
  bank_id: string
  bank_name: string
  status: string
  total_questions: number
  completed_questions: number
  started_at: string
  finished_at?: string | null
}
export interface TestQuestionResult {
  id: string
  question_id: string
  prompt: string
  question_type: QuestionType
  options: ChoiceOption[]
  correct_answer?: string | null
  status: string
  answer?: string | null
  agent_run_id?: string | null
  metrics: Record<string, unknown>
  overall_score?: number | null
  error?: string | null
}
export interface TestRunDetail extends TestRunSummary { results: TestQuestionResult[] }
