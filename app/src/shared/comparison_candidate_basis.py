"""경쟁사 후보 근거 v1의 feature 중립 순수 계약.

9장 조립기와 출고 게이트가 서로 직접 import하지 않고도 같은 닫힌 JSON,
법인 별칭 경계, 경쟁 표지, 문장 SHA-256을 재계산하도록 한다. 원문 문장 자체는
v1에 복제하지 않으며 ``candidate_fact_id``가 가리키는 1~8장 사실에서 읽는다.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Mapping


COMPARISON_BASIS_VERSION = "company-comparison-basis-v1"
COMPARISON_OVERLAP_DIMENSIONS = frozenset(
    {"고객 겹침", "제품·서비스 겹침", "시장 겹침"}
)

_COMPETITION_MARKERS = (
    "경쟁사",
    "경쟁업체",
    "경쟁 관계",
    "경쟁관계",
    "동종업체",
    "동종 업체",
    "경합",
    "시장점유율",
    "시장 점유율",
)
_PRODUCT_MARKERS = ("제품", "서비스", "품목", "브랜드", "기술")
_CUSTOMER_MARKERS = ("고객", "수요처", "납품처", "발주처")
_LEGAL_MARKERS = (
    "주식회사",
    "유한회사",
    "(주)",
    "㈜",
    "corp.",
    "co.,ltd.",
    "co., ltd.",
)
_ALIAS_TRAILING_PARTICLES = (
    "에서",
    "보다",
    "와",
    "과",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "의",
    "에",
    "등",
)
_ALIAS_BOUNDARY = r"[0-9a-z가-힣]"
_ALIAS_SEPARATOR = r"[\s,.;:!?()\[\]{}\"'·/]"
_EVIDENCE_SENTENCE = re.compile(r"[^.!?\n]+(?:[.!?]+|$)")
_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z._:-]{0,199}")
_EXPECTED_KEYS = frozenset(
    {
        "version",
        "candidate_fact_id",
        "candidate_source_id",
        "candidate_corp_code",
        "candidate_name",
        "filing_document_id",
        "evidence_sha256",
        "overlap_dimension",
    }
)


def _normalized(value: object) -> str:
    return " ".join(
        unicodedata.normalize("NFKC", str(value or "")).casefold().split()
    )


def comparison_evidence_sha256(text: str) -> str:
    normalized = " ".join(str(text or "").split()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""


def comparison_evidence_sentences(text: str) -> tuple[str, ...]:
    return tuple(
        sentence
        for match in _EVIDENCE_SENTENCE.finditer(str(text or ""))
        if (sentence := match.group(0).strip())
    )


def comparison_corporate_core(value: str) -> str:
    core = _normalized(value)
    for marker in _LEGAL_MARKERS:
        core = core.replace(_normalized(marker), " ")
    return re.sub(r"[^0-9a-z가-힣]", "", core)


def comparison_candidate_aliases(name: str) -> tuple[str, ...]:
    raw = _normalized(name)
    stripped = raw
    for marker in _LEGAL_MARKERS:
        stripped = stripped.replace(_normalized(marker), " ")
    stripped = " ".join(stripped.split()).strip(" ,.-")
    return tuple(
        dict.fromkeys(
            alias
            for alias in (raw, stripped)
            if alias and (alias == raw or len(alias) >= 2)
        )
    )


def comparison_alias_is_in_sentence(alias: str, sentence: str) -> bool:
    normalized_alias = _normalized(alias)
    normalized_sentence = _normalized(sentence)
    if not normalized_alias:
        return False
    particle = "|".join(
        re.escape(value)
        for value in sorted(_ALIAS_TRAILING_PARTICLES, key=len, reverse=True)
    )
    boundary = rf"(?:$|{_ALIAS_SEPARATOR}|(?:{particle})(?=$|{_ALIAS_SEPARATOR}))"
    return re.search(
        rf"(?<!{_ALIAS_BOUNDARY}){re.escape(normalized_alias)}(?={boundary})",
        normalized_sentence,
    ) is not None


def comparison_sentence_has_marker(sentence: str) -> bool:
    normalized = _normalized(sentence)
    return any(_normalized(marker) in normalized for marker in _COMPETITION_MARKERS)


def comparison_overlap_dimension(sentence: str) -> str:
    if any(marker in sentence for marker in _CUSTOMER_MARKERS):
        return "고객 겹침"
    if any(marker in sentence for marker in _PRODUCT_MARKERS):
        return "제품·서비스 겹침"
    return "시장 겹침"


def encode_comparison_basis_v1(payload: Mapping[str, object]) -> str:
    """닫힌 필드가 모두 유효할 때만 결정론적 v1 JSON을 만든다."""

    encoded = json.dumps(
        {key: payload.get(key, "") for key in _EXPECTED_KEYS},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return encoded if parse_comparison_basis_v1(encoded) is not None else ""


def parse_comparison_basis_v1(value: object) -> dict[str, str] | None:
    try:
        payload = json.loads(str(value or ""))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or set(payload) != _EXPECTED_KEYS:
        return None
    clean = {key: str(payload.get(key) or "").strip() for key in _EXPECTED_KEYS}
    if (
        clean["version"] != COMPARISON_BASIS_VERSION
        or not _ID.fullmatch(clean["candidate_fact_id"])
        or not _ID.fullmatch(clean["candidate_source_id"])
        or not re.fullmatch(r"\d{8}", clean["candidate_corp_code"])
        or not re.fullmatch(r"[0-9a-f]{64}", clean["evidence_sha256"])
        or clean["overlap_dimension"] not in COMPARISON_OVERLAP_DIMENSIONS
        or not all(clean.values())
    ):
        return None
    return clean


def comparison_candidate_sentence_matches(
    basis: Mapping[str, str],
    *,
    comparison_target: str,
    evidence_text: str,
) -> bool:
    """역참조한 한 문장에 후보 실명·경쟁 표지·해시가 함께 있는지 본다."""

    if comparison_corporate_core(str(basis.get("candidate_name") or "")) != (
        comparison_corporate_core(comparison_target)
    ):
        return False
    sentences = comparison_evidence_sentences(evidence_text)
    aliases = comparison_candidate_aliases(comparison_target)
    if len(sentences) != 1 or not aliases:
        return False
    sentence = sentences[0]
    return (
        comparison_sentence_has_marker(sentence)
        and any(comparison_alias_is_in_sentence(alias, sentence) for alias in aliases)
        and comparison_evidence_sha256(sentence)
        == str(basis.get("evidence_sha256") or "")
    )


def comparison_comparator_source_id(
    *, corp_code: str, comparison_period: str, comparison_scope: str
) -> str:
    """후보 DART 고유번호와 실제 비교사 재무 Source ID의 결정론적 결속."""

    scope = str(comparison_scope or "").upper()
    scope_code = "CFS" if "CFS" in scope else "OFS" if "OFS" in scope else ""
    period = str(comparison_period or "")
    end = period.split("~", 1)[-1]
    year = end[:4] if re.match(r"^20\d{2}-", end) else ""
    code = str(corp_code or "").strip()
    if not re.fullmatch(r"\d{8}", code) or not year or not scope_code:
        return ""
    digest = hashlib.sha256(
        _normalized(code + year + scope_code).encode("utf-8")
    ).hexdigest()[:16]
    return f"comparison-comparator-{digest}"
