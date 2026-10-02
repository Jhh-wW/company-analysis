"""회사 직접 사실과 분리한 산업 과제의 원문·사업 연결 계약.

이 타입의 존재나 지문은 의미 검수 완료를 뜻하지 않는다. 생산자는 기존
기사 검수 요청에서 원문과 공식 사업 근거의 적용 관계를 검수해야 한다.
소비자는 같은 실행의 출처 등록부로 두 원문을 다시 검증한다.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import date
from typing import Final, Mapping

from src.shared.report_evidence.constants import FORMAL_DOCUMENT_SOURCE_KINDS
from src.shared.report_evidence.source_verification import SourceVerifier

INDUSTRY_CONTEXT_SECTION: Final[str] = "current_challenges"
INDUSTRY_CONTEXT_CAPTION: Final[str] = "공식 자료에 나온 사업과 관련된 산업 과제"
INDUSTRY_CONTEXT_LIMITATION: Final[str] = (
    "산업 자료와 공식 사업 자료를 연결한 해석이다. 이 회사의 직접 피해와 "
    "실제 대응은 확인되지 않았다."
)
INDUSTRY_CONTEXT_MAX_ITEMS: Final[int] = 2
INDUSTRY_GEOGRAPHIES: Final[frozenset[str]] = frozenset({"domestic", "global", "foreign"})
INDUSTRY_GEOGRAPHY_LABELS: Final[Mapping[str, str]] = {
    "domestic": "국내", "global": "세계", "foreign": "해외 특정 지역",
}


def _text(value: object, label: str) -> str:
    if type(value) is not str or not value.strip() or value != value.strip():
        raise ValueError(f"{label}은 앞뒤 공백 없는 문자열이어야 합니다")
    return value


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _span(text: str, text_sha256: str, location: str) -> None:
    _text(text, "정확 원문")
    _text(location, "원문 위치")
    if text_sha256 != _hash(text):
        raise ValueError("산업 과제의 정확 원문 지문이 일치하지 않습니다")


@dataclass(frozen=True)
class BusinessActivityAnchor:
    """대상 회사의 공식 자료가 직접 밝힌 사업 항목. 주력 순위를 단정하지 않는다."""

    anchor_id: str
    company_id: str
    source_id: str
    document_id: str
    source_kind: str
    source_url: str
    location: str
    exact_text: str
    text_sha256: str
    business_item: str
    publisher: str = ""
    title: str = ""
    published_on: str = ""
    document_content_sha256: str = ""
    identity_binding: str = ""

    def __post_init__(self) -> None:
        for name in ("anchor_id", "company_id", "document_id", "source_url", "business_item"):
            _text(getattr(self, name), name)
        if self.source_kind not in FORMAL_DOCUMENT_SOURCE_KINDS:
            raise ValueError("산업 과제의 회사 사업 근거는 공식 자료여야 합니다")
        _span(self.exact_text, self.text_sha256, self.location)
        if self.business_item not in self.exact_text:
            raise ValueError("사업 항목이 공식 원문 안에 없습니다")


@dataclass(frozen=True)
class IndustryProblemEvidence:
    """사업 앵커와 적용 관계를 함께 검수한 산업 문제의 연속 원문."""

    evidence_id: str
    business_anchor_id: str
    document_id: str
    source_url: str
    publisher: str
    title: str
    published_on: str
    location: str
    exact_text: str
    text_sha256: str
    industry: str
    problem: str
    geography: str
    geography_detail: str
    geography_evidence: str
    source_id: str = ""
    document_content_sha256: str = ""
    analysis_response_sha256: str = ""
    applicability_quote: str = ""

    def __post_init__(self) -> None:
        for name in ("evidence_id", "business_anchor_id", "document_id", "source_url", "publisher", "title", "industry", "problem", "geography_detail", "geography_evidence"):
            _text(getattr(self, name), name)
        date.fromisoformat(self.published_on)
        _span(self.exact_text, self.text_sha256, self.location)
        if self.geography not in INDUSTRY_GEOGRAPHIES:
            raise ValueError("산업 문제의 지역 범위가 계약 밖입니다")
        if any(value not in self.exact_text for value in (self.industry, self.problem, self.geography_evidence, self.geography_detail)):
            raise ValueError("산업명·문제·지역 적용 근거는 같은 연속 원문 안에 있어야 합니다")
        if type(self.source_id) is not str:
            raise ValueError("산업 자료의 공개 출처 식별자는 문자열이어야 합니다")
        for digest in (self.document_content_sha256, self.analysis_response_sha256):
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise ValueError("산업 자료에는 전체 원문과 검수 응답의 SHA-256이 필요합니다")
        if not self.applicability_quote or self.applicability_quote not in self.exact_text:
            raise ValueError("사업 적용 근거는 산업 자료의 연속 원문 안에 있어야 합니다")


@dataclass(frozen=True)
class IndustryChallengeContext:
    """직접 피해나 회사 대응을 주장하지 않는 공개용 산업 과제."""

    anchor: BusinessActivityAnchor
    problem: IndustryProblemEvidence

    def __post_init__(self) -> None:
        if type(self.anchor) is not BusinessActivityAnchor or type(self.problem) is not IndustryProblemEvidence:
            raise ValueError("산업 과제는 정확한 공식 사업·산업 자료 타입이어야 합니다")
        if self.problem.business_anchor_id != self.anchor.anchor_id:
            raise ValueError("산업 자료가 검수한 공식 사업 앵커와 공개 앵커가 다릅니다")

    @property
    def interpretation(self) -> str:
        return f"공식 자료에서 확인한 사업 ‘{self.anchor.business_item}’에 이 산업 문제가 적용되는지 점검할 필요가 있다."

    @property
    def limitation(self) -> str:
        return INDUSTRY_CONTEXT_LIMITATION

    @property
    def geography_label(self) -> str:
        return INDUSTRY_GEOGRAPHY_LABELS[self.problem.geography]


@dataclass(frozen=True)
class IndustryContextDisplay:
    """봉인된 공개 두 출처 번호와 산업 과제의 표시 내용."""

    context: IndustryChallengeContext
    business_source_number: int
    industry_source_number: int
    lines: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.context) is not IndustryChallengeContext:
            raise ValueError("산업 과제 표시의 근거 계약이 올바르지 않습니다")
        _text(self.context.problem.source_id, "산업 자료의 공개 출처 식별자")
        _text(self.context.anchor.source_id, "공식 사업의 공개 출처 식별자")
        if any(type(value) is not int or value <= 0 for value in (self.business_source_number, self.industry_source_number)):
            raise ValueError("산업 과제 표시에는 두 공개 출처 번호가 필요합니다")
        geography = self.context.geography_label
        if self.context.problem.geography_detail != geography:
            geography += f"·{self.context.problem.geography_detail}"
        expected = (
            f"산업 문제({geography}): {self.context.problem.problem} [{self.industry_source_number}]",
            f"공식 사업 근거: {self.context.anchor.business_item} [{self.business_source_number}]",
            f"{self.context.interpretation} [{self.business_source_number}] [{self.industry_source_number}] — 해석",
            self.context.limitation,
        )
        if self.lines and self.lines != expected:
            raise ValueError("산업 과제의 공개 문구가 근거로 만든 고정 해석과 다릅니다")
        object.__setattr__(self, "lines", expected)


def industry_display_from_dict(value: object) -> IndustryContextDisplay:
    if type(value) is not dict or set(value) != {"context", "business_source_number", "industry_source_number", "lines"}:
        raise ValueError("산업 과제 표시의 저장 형식이 계약과 다릅니다")
    return IndustryContextDisplay(
        industry_context_from_dict(value["context"]),
        value["business_source_number"], value["industry_source_number"], tuple(value["lines"]),
    )


def industry_context_displays(contexts: tuple[IndustryChallengeContext, ...], sources: tuple[object, ...]) -> tuple[IndustryContextDisplay, ...]:
    """봉인 전 보고서의 보조 표시에도 두 출처 번호의 같은 정본을 쓴다."""
    numbers = {str(getattr(source, "source_id", "")): getattr(source, "number", 0) for source in sources}
    return tuple(IndustryContextDisplay(value, numbers.get(value.anchor.source_id, 0), numbers.get(value.problem.source_id, 0)) for value in contexts)


def industry_context_to_dict(value: IndustryChallengeContext) -> dict[str, object]:
    if type(value) is not IndustryChallengeContext:
        raise ValueError("정확한 산업 과제 타입이 필요합니다")
    return asdict(value)


def industry_context_from_dict(value: object) -> IndustryChallengeContext:
    if type(value) is not dict or set(value) != {"anchor", "problem"}:
        raise ValueError("산업 과제 저장 형식이 계약과 다릅니다")
    return IndustryChallengeContext(
        BusinessActivityAnchor(**value["anchor"]), IndustryProblemEvidence(**value["problem"]),
    )


def industry_context_problems(
    contexts: tuple[IndustryChallengeContext, ...], *, company_id: str,
    registry: tuple[object, ...], reference_date: str, verifier: SourceVerifier,
) -> tuple[str, ...]:
    """두 원문과 봉인 출처를 대조한다. 사업의 의미 적용 판정을 대체하지 않는다."""
    if type(contexts) is not tuple or len(contexts) > INDUSTRY_CONTEXT_MAX_ITEMS:
        return ("industry_context_invalid_count",)
    if not contexts:
        return ()
    sources = {str(getattr(source, "source_id", "")): source for source in registry}
    if len(sources) != len(registry):
        return ("industry_context_ambiguous_registry",)
    problems: list[str] = []
    seen: set[str] = set()
    for context in contexts:
        if type(context) is not IndustryChallengeContext:
            problems.append("industry_context_invalid_type")
            continue
        if context.anchor.company_id != company_id:
            problems.append("industry_context_wrong_company")
        if context.problem.evidence_id in seen:
            problems.append("industry_context_duplicate")
        seen.add(context.problem.evidence_id)
        if context.problem.published_on > reference_date:
            problems.append("industry_context_future_source")
        for span, official in ((context.anchor, True), (context.problem, False)):
            source = sources.get(span.source_id)
            if source is None:
                problems.append("industry_context_missing_source")
                continue
            verified = verifier(source, registry, reference_date=reference_date, evidence_text=span.exact_text)
            if (verified is None or not verified.evidence_bound
                    or span.text_sha256 not in verified.exact_evidence_hashes
                    or str(getattr(source, "url", "")) != span.source_url
                    or verified.content_sha256 != span.document_content_sha256
                    or verified.source_id != span.source_id):
                problems.append("industry_context_unbound_source")
            elif official and not verified.official:
                problems.append("industry_context_unofficial_anchor")
            elif not official and not (verified.news or verified.official):
                problems.append("industry_context_unqualified_industry_source")
            elif not official and (
                str(getattr(source, "published_at", "")) != span.published_on
                or str(getattr(source, "publisher", "")) != span.publisher
                or str(getattr(source, "title", "")) != span.title
            ):
                problems.append("industry_context_changed_source_metadata")
    return tuple(dict.fromkeys(problems))
