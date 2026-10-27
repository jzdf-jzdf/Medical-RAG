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
            int(value) for value in CITATION_RE.findall(answer) if 1 <= int(value) <= len(citations)
        )
    )
    mapping = {old: new for new, old in enumerate(used, start=1)}
    answer = CITATION_RE.sub(
        lambda match: f"[{mapping[int(match.group(1))]}]" if int(match.group(1)) in mapping else "",
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
            if claim["requires_support"]:
                issues.append("uncited_claim")
            continue
        valid = [source_id for source_id in ids if 1 <= source_id <= len(citations)]
        if len(valid) != len(ids):
            issues.append("invalid_source")
        evidence = " ".join(str(citations[source_id - 1].get("text") or "") for source_id in valid)

        def normalize(value: str) -> str:
            return unicodedata.normalize("NFKC", value).replace(",", "").replace("％", "%")

        expected = {normalize(value) for value in re.findall(r"\d+(?:[,.]\d+)*(?:%|％)?", evidence)}
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
        plain = re.sub(r"^\s*(?:#{1,6}\s*|[-*+]\s+|\d+[.)、]\s*)", "", raw)
        plain = CITATION_RE.sub("", plain).strip(" #*-:：")
        claims.append(
            {
                "claim_id": len(claims) + 1,
                "text": plain,
                "citation_ids": [int(value) for value in CITATION_RE.findall(raw)],
                "requires_support": _requires_support(plain),
            }
        )
    return claims


def _requires_support(text: str) -> bool:
    if len(text) <= 12:
        return False
    boilerplate = (
        r"^以上(?:内容|为).{0,50}(?:供参考|医生判断|专业判断|临床决策)",
        r"^本回答(?:仅|主要).{0,50}(?:供参考|依据|参考来源)",
        r"^请结合患者具体情况.{0,30}(?:医生|专业人员|临床判断)",
        r"^具体(?:治疗|用药|方案|决策).{0,30}(?:医生|专业人员).{0,12}(?:判断|决定|调整)",
    )
    return not any(re.search(pattern, text) for pattern in boilerplate)
