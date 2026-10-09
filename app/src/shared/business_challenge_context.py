"""회사 직접 사실과 분리한 산업 과제의 원문·사업 연결 계약.

이 타입의 존재나 지문은 의미 검수 완료를 뜻하지 않는다. 생산자는 기사 또는
공식 자료 검수 요청에서 원문과 공식 사업 근거의 적용 관계를 검수해야 한다.
소비자는 같은 실행의 출처 등록부로 두 원문을 다시 검증한다.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Final, Mapping

from src.shared.report_evidence.constants import (
    FORMAL_DOCUMENT_SOURCE_KINDS, OFFICIAL_WEB_SOURCE_KINDS,
)
from src.shared.report_evidence.source_verification import SourceVerifier
from src.shared.report_evidence.news_business_activity import (
    news_business_anchor_problem, parse_news_business_binding,
)
from src.shared.report_evidence.constants import SOURCE_KIND_NEWS

INDUSTRY_CONTEXT_SECTION: Final[str] = "current_challenges"
INDUSTRY_CONTEXT_CAPTION: Final[str] = "공식 자료에 나온 사업과 관련된 산업 과제"
NEWS_INDUSTRY_CONTEXT_CAPTION: Final[str] = "검증된 보도 사업과 관련된 산업 과제"
INDUSTRY_CONTEXT_LIMITATION: Final[str] = (
    "산업 자료와 공식 사업 자료를 연결한 해석이다. 이 회사의 직접 피해와 "
    "실제 대응은 확인되지 않았다."
)
INDUSTRY_CONTEXT_MAX_ITEMS: Final[int] = 2
OFFICIAL_INDUSTRY_FIELDS: Final[tuple[str, ...]] = (
    "source_kind", "identity_binding", "assessment_quote", "observation_period",
)
OFFICIAL_INDUSTRY_YEAR_RE: Final[re.Pattern[str]] = re.compile(r"(?:19|20)[0-9]{2}")
INDUSTRY_GEOGRAPHIES: Final[frozenset[str]] = frozenset({"domestic", "global", "foreign"})
OFFICIAL_UNSPECIFIED_GEOGRAPHY: Final[str] = "unspecified"
OFFICIAL_INDUSTRY_GEOGRAPHIES: Final[frozenset[str]] = (
    INDUSTRY_GEOGRAPHIES | {OFFICIAL_UNSPECIFIED_GEOGRAPHY}
)
UNSPECIFIED_GEOGRAPHY_LIMITATION: Final[str] = (
    "이 산업 관찰의 지역 범위는 확인되지 않아 국내·세계 산업 문제로 단정하지 않는다."
)
INDUSTRY_GEOGRAPHY_LABELS: Final[Mapping[str, str]] = {
    "domestic": "국내", "global": "세계", "foreign": "해외 특정 지역",
    OFFICIAL_UNSPECIFIED_GEOGRAPHY: "지역 범위 미확인",
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
        if self.source_kind == SOURCE_KIND_NEWS:
            if news_business_anchor_problem(
                self, company_id=self.company_id, reference_date=self.published_on,
            ):
                raise ValueError("뉴스 사업의 자기 회사·원문·행동 결속이 올바르지 않습니다")
        elif self.source_kind not in FORMAL_DOCUMENT_SOURCE_KINDS:
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
    # 빈 기본값은 이미 봉인된 뉴스 산업 자료의 저장 형식을 그대로 보존한다.
    source_kind: str = field(default="", metadata={"canonical_omit_empty_string": True})
    identity_binding: str = field(default="", metadata={"canonical_omit_empty_string": True})
    assessment_quote: str = field(default="", metadata={"canonical_omit_empty_string": True})
    observation_period: str = field(default="", metadata={"canonical_omit_empty_string": True})

    def __post_init__(self) -> None:
        for name in ("evidence_id", "business_anchor_id", "document_id", "source_url", "publisher", "title", "industry", "problem"):
            _text(getattr(self, name), name)
        date.fromisoformat(self.published_on)
        _span(self.exact_text, self.text_sha256, self.location)
        unspecified = self.geography == OFFICIAL_UNSPECIFIED_GEOGRAPHY
        allowed_geographies = OFFICIAL_INDUSTRY_GEOGRAPHIES if self.source_kind else INDUSTRY_GEOGRAPHIES
        if self.geography not in allowed_geographies:
            raise ValueError("산업 문제의 지역 범위가 계약 밖입니다")
        if unspecified:
            if self.geography_detail != "" or self.geography_evidence != "":
                raise ValueError("지역 미확인 공식 관찰에 지역 명칭이나 근거를 만들 수 없습니다")
        else:
            _text(self.geography_detail, "geography_detail")
            _text(self.geography_evidence, "geography_evidence")
        if any(value not in self.exact_text for value in (self.industry, self.problem, self.geography_evidence, self.geography_detail)):
            raise ValueError("산업명·문제·지역 적용 근거는 같은 연속 원문 안에 있어야 합니다")
        if type(self.source_id) is not str:
            raise ValueError("산업 자료의 공개 출처 식별자는 문자열이어야 합니다")
        for digest in (self.document_content_sha256, self.analysis_response_sha256):
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise ValueError("산업 자료에는 전체 원문과 검수 응답의 SHA-256이 필요합니다")
        if not self.applicability_quote or self.applicability_quote not in self.exact_text:
            raise ValueError("사업 적용 근거는 산업 자료의 연속 원문 안에 있어야 합니다")
        for name in OFFICIAL_INDUSTRY_FIELDS:
            if type(getattr(self, name)) is not str:
                raise ValueError("공식 산업 자료의 추가 메타데이터는 문자열이어야 합니다")
        if not self.source_kind:
            if any(getattr(self, name) for name in OFFICIAL_INDUSTRY_FIELDS[1:]):
                raise ValueError("뉴스 산업 자료에 공식 검수 메타데이터를 섞을 수 없습니다")
            return
        if self.source_kind not in FORMAL_DOCUMENT_SOURCE_KINDS:
            raise ValueError("공식 산업 자료의 수집 종류가 계약 밖입니다")
        for name in OFFICIAL_INDUSTRY_FIELDS:
            _text(getattr(self, name), name)
        if self.exact_text.count(self.assessment_quote) != 1:
            raise ValueError("공식 산업 평가 범위는 자기 조각의 유일한 연속 원문이어야 합니다")
        if any(value not in self.assessment_quote for value in (
            self.industry, self.problem, self.geography_detail,
            self.geography_evidence, self.applicability_quote,
        )):
            raise ValueError("공식 산업 판정이 다른 절의 문제나 지역을 빌렸습니다")
        if (self.observation_period not in self.title
                and self.observation_period not in self.assessment_quote):
            raise ValueError("공식 산업 자료의 관찰 시점이 제목 또는 평가 원문에 없습니다")
        years = OFFICIAL_INDUSTRY_YEAR_RE.findall(self.observation_period)
        if not years or any(int(year) > date.fromisoformat(self.published_on).year for year in years):
            raise ValueError("공식 산업 자료의 관찰 연도가 없거나 공표 이후입니다")


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
        if self.anchor.source_kind == SOURCE_KIND_NEWS:
            return f"보도에서 확인한 사업 ‘{self.anchor.business_item}’에 이 산업 문제가 적용되는지 점검할 필요가 있다."
        return f"공식 자료에서 확인한 사업 ‘{self.anchor.business_item}’에 이 산업 문제가 적용되는지 점검할 필요가 있다."

    @property
    def limitation(self) -> str:
        if self.anchor.source_kind == SOURCE_KIND_NEWS:
            role = parse_news_business_binding(self.anchor.identity_binding).get("business_role")
            basis = "인수한 사업의 관련성" if role == "acquired_business" else "보도된 현재 사업의 관련성"
            limitation = (
                f"{basis}과 산업 자료를 연결한 해석이다. 회사의 직접 피해·실제 대응·"
                "주력 순위·최초 사업 개시는 확인되지 않았다."
            )
            if self.problem.geography == OFFICIAL_UNSPECIFIED_GEOGRAPHY:
                limitation += " " + UNSPECIFIED_GEOGRAPHY_LIMITATION
            return limitation
        if self.problem.geography == OFFICIAL_UNSPECIFIED_GEOGRAPHY:
            return INDUSTRY_CONTEXT_LIMITATION + " " + UNSPECIFIED_GEOGRAPHY_LIMITATION
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

    @property
    def caption(self) -> str:
        """기존 공식 표시와 뉴스 사업의 출처 종류를 구분한다."""
        return (NEWS_INDUSTRY_CONTEXT_CAPTION
                if self.context.anchor.source_kind == SOURCE_KIND_NEWS
                else INDUSTRY_CONTEXT_CAPTION)

    def __post_init__(self) -> None:
        if type(self.context) is not IndustryChallengeContext:
            raise ValueError("산업 과제 표시의 근거 계약이 올바르지 않습니다")
        _text(self.context.problem.source_id, "산업 자료의 공개 출처 식별자")
        _text(self.context.anchor.source_id, "공식 사업의 공개 출처 식별자")
        if any(type(value) is not int or value <= 0 for value in (self.business_source_number, self.industry_source_number)):
            raise ValueError("산업 과제 표시에는 두 공개 출처 번호가 필요합니다")
        geography = self.context.geography_label
        if self.context.problem.geography_detail and self.context.problem.geography_detail != geography:
            geography += f"·{self.context.problem.geography_detail}"
        problem_label = (
            "산업 관찰" if self.context.problem.geography == OFFICIAL_UNSPECIFIED_GEOGRAPHY
            else "산업 문제"
        )
        expected = (
            f"{problem_label}({geography}): {self.context.problem.problem} [{self.industry_source_number}]",
            f"{'보도 사업 근거' if self.context.anchor.source_kind == SOURCE_KIND_NEWS else '공식 사업 근거'}: {self.context.anchor.business_item} [{self.business_source_number}]",
            f"{self.context.interpretation} [{self.business_source_number}] [{self.industry_source_number}] — 해석",
            self.context.limitation,
        )
        if self.context.problem.source_kind:
            problem = self.context.problem
            expected = (
                f"회사 공식 자료의 기준: {problem.observation_period} · {problem.published_on} 공표",
                *expected,
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
    payload = asdict(value)
    # 새 필드가 없는 옛 뉴스 보고서도 같은 공개 내용 지문으로 다시 읽는다.
    for name in OFFICIAL_INDUSTRY_FIELDS:
        if not payload["problem"][name]:
            payload["problem"].pop(name)
    return payload


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
            elif official and span.source_kind == SOURCE_KIND_NEWS and (
                not verified.news
                or news_business_anchor_problem(
                    span, company_id=company_id, reference_date=reference_date,
                )
                or str(getattr(source, "identity_binding", "")) != span.identity_binding
                or str(getattr(source, "document_id", "")) != span.document_id
                or str(getattr(source, "location", "")) != span.location
                or str(getattr(source, "published_at", "")) != span.published_on
                or str(getattr(source, "publisher", "")) != span.publisher
                or str(getattr(source, "title", "")) != span.title
            ):
                problems.append("industry_context_unbound_news_anchor")
            elif official and span.source_kind != SOURCE_KIND_NEWS and not verified.official:
                problems.append("industry_context_unofficial_anchor")
            elif not official and not (verified.news or verified.official):
                problems.append("industry_context_unqualified_industry_source")
            elif not official and span.source_kind and (
                not verified.official
                or verified.formal_kind != span.source_kind
                or str(getattr(source, "identity_binding", "")) != span.identity_binding
            ):
                problems.append("industry_context_changed_official_source")
            elif not official and (
                str(getattr(source, (
                    "disclosed_at" if span.source_kind
                    and span.source_kind not in OFFICIAL_WEB_SOURCE_KINDS
                    else "published_at"
                ), "")) != span.published_on
                or str(getattr(source, "publisher", "")) != span.publisher
                or str(
                    (getattr(source, "title", "") or getattr(source, "label", ""))
                    if span.source_kind else getattr(source, "title", "")
                ) != span.title
            ):
                problems.append("industry_context_changed_source_metadata")
    return tuple(dict.fromkeys(problems))
