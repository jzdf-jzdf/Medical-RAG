from fastapi import APIRouter

from evaluation_api.schemas.evaluation import EvaluationRequest, EvaluationResponse
from evaluation_api.services.evaluation import evaluate_layers

router = APIRouter(prefix="/evaluations", tags=["evaluation"])


@router.post("/score", response_model=EvaluationResponse)
async def score(payload: EvaluationRequest) -> dict:
    return evaluate_layers(
        answer=payload.answer,
        citations=payload.citations,
        expected_document_ids=payload.expected_document_ids,
        reference_answer=payload.reference_answer,
        top_k=payload.top_k,
        reference_chunks=payload.reference_chunks,
        retrieved_chunks=payload.retrieved_chunks,
    )
