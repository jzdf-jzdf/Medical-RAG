from fastapi import APIRouter

from app.schemas.evaluation import EvaluationRequest, EvaluationResponse
from app.services.evaluation import evaluate_layers

router = APIRouter(prefix="/evaluations", tags=["evaluation"])


@router.post("/score", response_model=EvaluationResponse)
async def score(payload: EvaluationRequest) -> dict:
    return evaluate_layers(
        answer=payload.answer,
        citations=payload.citations,
        expected_document_ids=payload.expected_document_ids,
        reference_answer=payload.reference_answer,
        top_k=payload.top_k,
    )
