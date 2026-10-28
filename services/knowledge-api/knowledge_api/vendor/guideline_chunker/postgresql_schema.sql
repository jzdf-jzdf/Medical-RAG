CREATE TABLE IF NOT EXISTS guideline_chunker_documents (
    id BIGSERIAL PRIMARY KEY,
    input_sha256 CHAR(64) NOT NULL UNIQUE,
    request_id UUID NOT NULL,
    pipeline_run_id TEXT NOT NULL,
    source_doc TEXT NOT NULL,
    input_filename TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('completed', 'completed_with_review')
    ),
    content TEXT NOT NULL,
    source_path TEXT NOT NULL,
    output_dir TEXT NOT NULL,
    manifest JSONB NOT NULL,
    processing_report JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS guideline_chunker_chunks (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL
        REFERENCES guideline_chunker_documents(id) ON DELETE CASCADE,
    chunk_id TEXT NOT NULL,
    source_doc TEXT NOT NULL,
    section_id TEXT NOT NULL,
    section_path JSONB NOT NULL,
    topic_tags JSONB NOT NULL,
    summary TEXT NOT NULL,
    content TEXT NOT NULL,
    chunk_type TEXT NOT NULL CHECK (
        chunk_type IN ('fact', 'recommendation')
    ),
    recommendation_levels JSONB NOT NULL DEFAULT '[]'::jsonb,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (document_id, chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_guideline_chunker_chunks_document
    ON guideline_chunker_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_guideline_chunker_chunks_type
    ON guideline_chunker_chunks(chunk_type);
CREATE INDEX IF NOT EXISTS idx_guideline_chunker_chunks_topics
    ON guideline_chunker_chunks USING GIN(topic_tags);
