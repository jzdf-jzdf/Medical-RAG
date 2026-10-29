"""Transparent retrieval, citation and answer metrics without model judging."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from statistics import mean
from typing import Any

CITATION_RE = re.compile(r"(?<![A-Za-z0-9_])\[(\d+)\]")


def _chunk_text(chunk: str | dict[str, Any]) -> str:
    if isinstance(chunk, str):
        return chunk
    for key in ("quote", "content", "text", "context"):
        value = chunk.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _text_bigrams(text: str) -> Counter[str]:
    value = unicodedata.normalize("NFKC", text).casefold()
    value = value.replace("≥", ">=").replace("≤", "<=")
    value = value.translate(str.maketrans({"–": "-", "—": "-", "~": "-"}))
    value = "".join(value.split())
    # Preserve decimal points, percentages and comparisons so 1.5 and 15 differ.
    value = "".join(
        char for index, char in enumerate(value)
        if char.isalnum() or char in "%<>=±+-"
        or (
            char == "." and 0 < index < len(value) - 1
            and value[index - 1].isdigit() and value[index + 1].isdigit()
        )
    )
    if len(value) < 2:
        return Counter([value]) if value else Counter()
    return Counter(value[index:index + 2] for index in range(len(value) - 1))


def evaluate_reference_text(
    retrieved_chunks: list[dict[str, Any]],
    reference_chunks: list[str | dict[str, Any]],
    top_k: int,
) -> dict[str, Any] | None:
    references = [(_chunk_text(chunk), _text_bigrams(_chunk_text(chunk)))
                  for chunk in reference_chunks]
    references = [(text, tokens) for text, tokens in references if tokens]
    if not references:
        return None
    candidates = [
        (rank, chunk, _chunk_text(chunk), _text_bigrams(_chunk_text(chunk)))
        for rank, chunk in enumerate(retrieved_chunks[:top_k], start=1)
    ]
    candidates = [candidate for candidate in candidates if candidate[3]]
    matches = []
    for index, (reference, reference_tokens) in enumerate(references, start=1):
        best: dict[str, Any] = {
            "reference_index": index,
            "reference_text": reference,
            "retrieved_rank": None,
            "retrieved_text": "",
            "document_id": None,
            "chunk_id": None,
            "title": None,
            "recall": 0.0,
            "precision": 0.0,
            "f1": 0.0,
        }
        for rank, chunk, retrieved, retrieved_tokens in candidates:
            overlap = sum((reference_tokens & retrieved_tokens).values())
            recall = overlap / reference_tokens.total()
            precision = overlap / retrieved_tokens.total()
            f1 = 2 * recall * precision / (recall + precision) if recall + precision else 0.0
            if (recall, f1) > (best["recall"], best["f1"]):
                best.update(
                    retrieved_rank=rank, retrieved_text=retrieved,
                    document_id=chunk.get("document_id"), chunk_id=chunk.get("chunk_id"),
                    title=chunk.get("title") or chunk.get("document_title"),
                    recall=recall, precision=precision, f1=f1,
                )
        matches.append(best)
    metrics = {
        "k": top_k,
        "reference_count": len(references),
        "retrieved_count": len(retrieved_chunks[:top_k]),
        "text_recall": mean(match["recall"] for match in matches),
        "text_precision": mean(match["precision"] for match in matches),
        "text_f1": mean(match["f1"] for match in matches),
    }
    return {
        "method": "reference_text_v1",
        "score": metrics["text_recall"],
        "metrics": metrics,
        "matches": matches,
        "issues": [] if candidates else ["no_retrieved_text"],
    }


def _tokens(value: str) -> set[str]:
    lowered = value.lower()
    result = set(re.findall(r"[a-z0-9_]+", lowered))
    for sequence in re.findall(r"[\u3400-\u9fff]+", lowered):
        result.update(
            sequence
            if len(sequence) == 1
            else (sequence[index : index + 2] for index in range(len(sequence) - 1))
        )
    return result


def evaluate_retrieval(
    citations: list[dict[str, Any]], expected_document_ids: list[str], top_k: int,
    reference_chunks: list[str | dict[str, Any]] | None = None,
) -> dict[str, Any]:
    text_score = evaluate_reference_text(citations, reference_chunks or [], top_k)
    if text_score is not None:
        return text_score
    retrieved = [str(item.get("document_id") or "") for item in citations[:top_k]]
    expected = set(expected_document_ids)
    rank = next((index for index, value in enumerate(retrieved, start=1) if value in expected), None)
    if not expected:
        return {
            "method": "document_id",
            "score": None,
            "metrics": {"hit_at_k": None, "recall_at_k": None, "mrr_at_k": None, "k": top_k},
            "issues": ["missing_retrieval_labels"],
        }
    metrics = {
        "hit_at_k": float(rank is not None),
        "recall_at_k": len(set(retrieved) & expected) / len(expected),
        "mrr_at_k": 1 / rank if rank else 0.0,
        "k": top_k,
    }
    return {"method": "document_id", "score": mean(metrics[key] for key in ("hit_at_k", "recall_at_k", "mrr_at_k")), "metrics": metrics, "issues": []}


def evaluate_citations(answer: str, citations: list[dict[str, Any]]) -> dict[str, Any]:
    claims = [
        part.strip()
        for part in re.split(r"(?<=[。！？；;])|\n+", answer)
        if len(part.strip(" #*-:：")) > 8
    ]
    cited_claims = sum(bool(CITATION_RE.search(claim)) for claim in claims)
    source_ids = [int(value) for value in CITATION_RE.findall(answer)]
    valid_ids = [value for value in source_ids if 1 <= value <= len(citations)]
    coverage = cited_claims / len(claims) if claims else 1.0
    validity = len(valid_ids) / len(source_ids) if source_ids else 0.0
    grounded_numbers: list[float] = []
    for claim in claims:
        numbers = re.findall(r"\d+(?:\.\d+)?(?:%|％)?", CITATION_RE.sub("", claim))
        if not numbers:
            continue
        ids = [int(value) for value in CITATION_RE.findall(claim)]
        evidence = " ".join(
            str(citations[index - 1].get("text") or "")
            for index in ids
            if 1 <= index <= len(citations)
        )
        grounded_numbers.append(float(all(number in evidence for number in numbers)))
    numeric_grounding = mean(grounded_numbers) if grounded_numbers else 1.0
    issues = []
    if coverage < 1:
        issues.append("uncited_claims")
    if validity < 1:
        issues.append("invalid_citation_ids")
    if numeric_grounding < 1:
        issues.append("ungrounded_numbers")
    metrics = {
        "claim_coverage": coverage,
        "citation_validity": validity,
        "numeric_grounding": numeric_grounding,
        "claim_count": len(claims),
    }
    return {"score": mean((coverage, validity, numeric_grounding)), "metrics": metrics, "issues": issues}


def evaluate_answer(answer: str, reference_answer: str | None) -> dict[str, Any]:
    if not reference_answer:
        return {
            "score": None,
            "metrics": {"reference_token_recall": None, "reference_token_precision": None, "token_f1": None},
            "issues": ["missing_reference_answer"],
        }
    answer_tokens = _tokens(CITATION_RE.sub("", answer))
    reference_tokens = _tokens(reference_answer)
    overlap = len(answer_tokens & reference_tokens)
    recall = overlap / len(reference_tokens) if reference_tokens else 0.0
    precision = overlap / len(answer_tokens) if answer_tokens else 0.0
    f1 = 2 * recall * precision / (recall + precision) if recall + precision else 0.0
    return {
        "score": f1,
        "metrics": {
            "reference_token_recall": recall,
            "reference_token_precision": precision,
            "token_f1": f1,
        },
        "issues": [],
    }


def evaluate_layers(
    *,
    answer: str,
    citations: list[dict[str, Any]],
    expected_document_ids: list[str],
    reference_answer: str | None,
    top_k: int,
    reference_chunks: list[str | dict[str, Any]] | None = None,
    retrieved_chunks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    retrieval = evaluate_retrieval(
        citations if retrieved_chunks is None else retrieved_chunks,
        expected_document_ids, top_k, reference_chunks,
    )
    citation = evaluate_citations(answer, citations)
    answer_score = evaluate_answer(answer, reference_answer)
    available = [
        layer["score"] for layer in (retrieval, citation, answer_score) if layer["score"] is not None
    ]
    overall = mean(available) if available else None
    return {
        "retrieval": retrieval,
        "citation": citation,
        "answer": answer_score,
        "overall_score": overall,
        "gate_passed": citation["score"] >= 0.8 and all(
            layer["score"] is None or layer["score"] >= 0.5
            for layer in (retrieval, answer_score)
        ),
    }
