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
    claims = [
        part.strip()
        for part in re.split(r"(?<=[。！？；;])|\n+", answer)
        if part.strip()
    ]
    for claim in claims:
        ids = [int(value) for value in CITATION_RE.findall(claim)]
        plain = CITATION_RE.sub("", claim).strip(" #*-:：")
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
