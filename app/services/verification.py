"""Deterministic, fail-closed checks for numbered medical answer citations."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

CITATION_RE = re.compile(r"(?<![A-Za-z0-9_])\[(\d+)\]")


def normalize_citations(answer: str) -> str:
    answer = re.sub(
        r"([。！？；;])\s*((?:\[\d+\][ \t]*)+)",
        lambda match: " " + match[2].strip() + match[1],
        answer,
    )
    return answer


def compact_citations(answer: str, citations: list[dict[str, Any]]):
    used = list(
        dict.fromkeys(
            int(value)
            for value in CITATION_RE.findall(answer)
            if 1 <= int(value) <= len(citations)
        )
    )
    mapping = {old: new for new, old in enumerate(used, start=1)}
    answer = CITATION_RE.sub(
        lambda match: f"[{mapping[int(match.group(1))]}]"
        if int(match.group(1)) in mapping
        else "",
        answer,
    )
    return answer, [citations[index - 1] for index in used]


def verify_answer(answer: str, citations: list[dict[str, Any]]) -> dict[str, Any]:
    answer = normalize_citations(answer)
    issues: list[str] = []
    supported_claims = 0
    claims = extract_claims(answer)
    for claim in claims:
        ids = claim["citation_ids"]
        plain = claim["text"]
        if not ids:
            if len(plain) > 12:
                issues.append("uncited_claim")
            continue
        valid = [source_id for source_id in ids if 1 <= source_id <= len(citations)]
        if len(valid) != len(ids):
            issues.append("invalid_source")
        evidence = " ".join(str(citations[source_id - 1].get("text") or "") for source_id in valid)

        def normalize(value: str) -> str:
            return unicodedata.normalize("NFKC", value).replace(",", "").replace("％", "%")

        expected = {
            normalize(value)
            for value in re.findall(r"\d+(?:[,.]\d+)*(?:%|％)?", evidence)
        }
        numbers = re.findall(r"\d+(?:[,.]\d+)*(?:%|％)?", plain)
        if any(normalize(number) not in expected for number in numbers):
            issues.append("numeric_mismatch")
        else:
            supported_claims += 1
    unique_issues = sorted(set(issues))
    return {
        "passed": bool(citations) and not unique_issues,
        "mode": "deterministic",
        "issues": unique_issues or ([] if citations else ["missing_sources"]),
        "claim_count": len(claims),
        "supported_claims": supported_claims,
        "citation_count": len(citations),
    }


def extract_claims(answer: str) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for part in re.split(r"(?<=[。！？；;])|\n+", normalize_citations(answer)):
        raw = part.strip()
        if not raw:
            continue
        plain = CITATION_RE.sub("", raw).strip(" #*-:：")
        claims.append(
            {
                "claim_id": len(claims) + 1,
                "text": plain,
                "citation_ids": [int(value) for value in CITATION_RE.findall(raw)],
                "requires_support": len(plain) > 12,
            }
        )
    return claims


def validate_semantic_verification(
    answer: str,
    citations: list[dict[str, Any]],
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    claims = [
        claim
        for claim in extract_claims(answer)
        if claim["requires_support"] or claim["citation_ids"]
    ]
    raw_reports = (payload or {}).get("claims")
    reports = raw_reports if isinstance(raw_reports, list) else []
    reported = {
        item.get("claim_id"): item
        for item in reports
        if isinstance(item, dict) and isinstance(item.get("claim_id"), int)
    }
    results: list[dict[str, Any]] = []
    issues: list[str] = []
    for claim in claims:
        report = reported.get(claim["claim_id"], {})
        evidence: list[dict[str, Any]] = []
        for item in report.get("evidence", []) if isinstance(report, dict) else []:
            if not isinstance(item, dict):
                continue
            citation_id = item.get("citation_id")
            quote = str(item.get("quote") or "").strip()
            if (
                isinstance(citation_id, int)
                and citation_id in claim["citation_ids"]
                and 1 <= citation_id <= len(citations)
                and quote
                and quote in str(citations[citation_id - 1].get("text") or "")
            ):
                evidence.append({"citation_id": citation_id, "quote": quote})

        supported = bool(report.get("supported")) and bool(evidence)
        reason = str(report.get("reason") or "").strip()
        if not claim["citation_ids"]:
            supported = False
            reason = reason or "陈述没有引用来源"
        elif not evidence:
            reason = reason or "未找到可在来源原文中定位的证据"
        if not supported:
            issues.append("semantic_mismatch")
        results.append(
            {
                "claim_id": claim["claim_id"],
                "text": claim["text"],
                "citation_ids": claim["citation_ids"],
                "supported": supported,
                "evidence": evidence,
                "reason": reason,
            }
        )

    if payload is None:
        issues.append("semantic_verification_failed")
    unique_issues = sorted(set(issues))
    return {
        "passed": bool(results) and not unique_issues,
        "issues": unique_issues,
        "claims": results,
        "supported_claims": sum(1 for item in results if item["supported"]),
    }
