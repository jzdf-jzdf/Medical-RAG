from pydantic import BaseModel, Field, model_validator


class KnowledgeEvidence(BaseModel):
    document_id: str
    document_version_id: str
    chunk_id: str
    evidence_span_id: str
    index_version: str
    title: str
    page: int | None = None
    text: str
    score: float | None = None
    review_status: str


class ExternalEvidence(BaseModel):
    source_server: str
    external_source_id: str
    title: str
    text: str
    url: str | None = None
    retrieved_at: str


class ChatRequest(BaseModel):
    conversation_id: str
    user_id: str
    content: str = Field(min_length=1)
    model_profile: str | None = None


class ChatResponse(BaseModel):
    run_id: str
    message_id: str
    content: str
    citations: list[dict]
    degraded_without_evidence: bool


class CitationCandidate(BaseModel):
    source_server: str
    document_id: str | None = None
    document_version_id: str | None = None
    chunk_id: str | None = None
    evidence_span_id: str | None = None
    index_version: str | None = None
    external_source_id: str | None = None

    @model_validator(mode="after")
    def validate_identity(self):
        internal = [
            self.document_id,
            self.document_version_id,
            self.chunk_id,
            self.evidence_span_id,
            self.index_version,
        ]
        if any(internal) and not all(internal):
            raise ValueError("Knowledge citation must contain all five stable IDs")
        if not all(internal) and not self.external_source_id:
            raise ValueError("External citation requires external_source_id")
        return self
