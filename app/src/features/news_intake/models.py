"""뉴스 선별·읽기·조각 매핑 사이의 불변 자료형."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from src.features.news_intake import constants as c
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS
from src.shared.report_generation.models import exact_text_sha256


def _text(value: str, *, label: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label}은 문자열이어야 합니다")
    clean = value.strip()
    if not allow_empty and not clean:
        raise ValueError(f"{label}은 비워 둘 수 없습니다")
    return clean


def _unique(values: tuple[str, ...], *, label: str) -> tuple[str, ...]:
    if not isinstance(values, tuple) or any(not isinstance(item, str) for item in values):
        raise TypeError(f"{label}은 문자열 tuple이어야 합니다")
    clean = tuple(_text(item, label=label) for item in values)
    if len(clean) != len(set(clean)):
        raise ValueError(f"{label}에 중복 값을 넣을 수 없습니다")
    return clean


def _counts(values: Mapping[str, int], *, label: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for key, value in values.items():
        clean_key = _text(key, label=label)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{label} 개수는 0 이상의 정수여야 합니다")
        if value:
            result[clean_key] = value
    return result


@dataclass(frozen=True)
class NewsCandidate:
    """기계 필터와 중복 제거를 통과한 기사 후보."""

    id: str
    title: str
    description: str
    originallink: str
    link: str
    published_on: str
    publisher: str
    priority: int
    source_url: str
    topics: tuple[str, ...] = ()
    source_category: str = ""
    published_on_source: str = "search_index"
    # 검색 메타의 이름 관측은 읽기 순위에만 쓴다. 본문 법인 검증 결과가 아니다.
    metadata_name_match: bool = False

    def __post_init__(self) -> None:
        if type(self.metadata_name_match) is not bool:
            raise TypeError("검색 메타 이름 일치는 불리언이어야 합니다")
        for value, label in (
            (self.id, "후보 식별자"),
            (self.title, "기사 제목"),
            (self.published_on, "기사 날짜"),
            (self.publisher, "언론사명"),
            (self.source_url, "기사 URL"),
        ):
            _text(value, label=label)
        for value, label in (
            (self.description, "기사 요약"),
            (self.originallink, "원문 링크"),
            (self.link, "검색 서비스 링크"),
        ):
            _text(value, label=label, allow_empty=True)
        if self.priority not in {
            c.PRIORITY_NEWSROOM,
            c.PRIORITY_PRESS_RELEASE,
            c.PRIORITY_INTERVIEW,
            c.PRIORITY_OTHER,
        }:
            raise ValueError("기사 우선순위가 닫힌 목록 밖입니다")
        try:
            dt.date.fromisoformat(self.published_on)
        except ValueError as error:
            raise ValueError("기사 날짜는 YYYY-MM-DD 형식이어야 합니다") from error


@dataclass(frozen=True)
class NewsSelectionResult:
    """기계 선별 결과와 제외 사유별 개수."""

    candidates: tuple[NewsCandidate, ...]
    searched_count: int
    exclusion_counts: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (
            isinstance(self.searched_count, bool)
            or not isinstance(self.searched_count, int)
            or self.searched_count < 0
        ):
            raise ValueError("검색 기사 수는 0 이상의 정수여야 합니다")
        if len({item.id for item in self.candidates}) != len(self.candidates):
            raise ValueError("선별 후보 식별자가 중복됐습니다")
        object.__setattr__(
            self, "exclusion_counts", _counts(self.exclusion_counts, label="선별 제외 사유")
        )

    @property
    def selected_count(self) -> int:
        return len(self.candidates)

    @property
    def filtered_count(self) -> int:
        """브리프의 '필터 후 M'과 같은 값."""

        return self.selected_count


@dataclass(frozen=True)
class ClassifiedNewsCandidate:
    """닫힌 분류 응답을 통과하고 READY 장을 뺀 후보."""

    candidate: NewsCandidate
    section_ids: tuple[str, ...]
    kind: str

    def __post_init__(self) -> None:
        sections = _unique(self.section_ids, label="분류 장")
        if not sections or len(sections) > c.MAX_SECTIONS_PER_CANDIDATE:
            raise ValueError("후보당 분류 장은 1개 이상 3개 이하여야 합니다")
        if set(sections) - set(REQUIRED_EVIDENCE_SECTION_IDS):
            raise ValueError("분류 장이 닫힌 목록 밖입니다")
        if self.kind not in c.ALLOWED_KINDS:
            raise ValueError("기사 종류가 닫힌 목록 밖입니다")
        object.__setattr__(self, "section_ids", sections)


@dataclass(frozen=True)
class FetchedNewsArticle:
    """분류를 통과해 주입 함수로 본문을 읽은 기사."""

    classified: ClassifiedNewsCandidate
    text: str

    def __post_init__(self) -> None:
        _text(self.text, label="기사 본문")

    @property
    def candidate(self) -> NewsCandidate:
        return self.classified.candidate

    @property
    def section_ids(self) -> tuple[str, ...]:
        return self.classified.section_ids

    @property
    def kind(self) -> str:
        return self.classified.kind


@dataclass(frozen=True)
class NewsBodyFetchResult:
    """기사 본문 한 번 읽기의 결과 — 성공이면 글자와 «어느 겹에서 얻었나»,
    실패면 사유 코드 하나를 담는다.

    예전에는 본문 함수가 ``str | None``만 돌려줘서 실패가 전부
    ``fetch_failed`` 하나로 뭉개졌다. 그 상태로는 운영 로그만 보고
    「robots가 막았나」·「403인가」·「200인데 본문이 0자인가」를 가를 수
    없어서 고칠 곳을 찾지 못한다.
    """

    text: str = ""
    reason_code: str = ""
    stage: str = ""
    effective_url: str = ""
    published_on: str = ""

    def __post_init__(self) -> None:
        for value, label in (
            (self.text, "기사 본문"),
            (self.reason_code, "본문 실패 사유"),
            (self.stage, "본문 추출 단계"),
            (self.effective_url, "실제 본문 URL"),
            (self.published_on, "본문 발행일"),
        ):
            if not isinstance(value, str):
                raise TypeError(f"{label}은 문자열이어야 합니다")
        text = self.text.strip()
        reason_code = self.reason_code.strip()
        stage = self.stage.strip()
        if bool(text) == bool(reason_code):
            raise ValueError("본문 결과는 글자와 실패 사유 중 정확히 하나만 담습니다")
        if text and not stage:
            raise ValueError("본문을 얻었으면 어느 단계에서 얻었는지 남겨야 합니다")
        if reason_code and stage:
            raise ValueError("실패한 본문 결과에는 추출 단계를 담을 수 없습니다")
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "reason_code", reason_code)
        object.__setattr__(self, "stage", stage)

    @property
    def succeeded(self) -> bool:
        return bool(self.text)


@dataclass(frozen=True)
class NewsClassificationResult:
    """분류 채택과 실제 본문 조회 결과."""

    classified: tuple[ClassifiedNewsCandidate, ...]
    articles: tuple[FetchedNewsArticle, ...]
    exclusion_counts: Mapping[str, int] = field(default_factory=dict)
    #: 본문을 어느 겹에서 얻었는지의 단계별 기사 수. 「메타 설명 한 문장으로
    #: 겨우 건진 기사」와 「본문을 통째로 읽은 기사」를 운영에서 가르는 값이다.
    body_stage_counts: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        classified_ids = tuple(item.candidate.id for item in self.classified)
        if len(classified_ids) != len(set(classified_ids)):
            raise ValueError("분류 채택 후보가 중복됐습니다")
        article_ids = tuple(item.candidate.id for item in self.articles)
        if len(article_ids) != len(set(article_ids)):
            raise ValueError("본문 조회 기사가 중복됐습니다")
        if set(article_ids) - set(classified_ids):
            raise ValueError("분류를 통과하지 않은 기사의 본문을 실을 수 없습니다")
        object.__setattr__(
            self,
            "exclusion_counts",
            _counts(self.exclusion_counts, label="분류 제외 사유"),
        )
        object.__setattr__(
            self,
            "body_stage_counts",
            _counts(self.body_stage_counts, label="본문 추출 단계"),
        )

    @property
    def classified_count(self) -> int:
        return len(self.classified)

    @property
    def fetched_count(self) -> int:
        return len(self.articles)


@dataclass(frozen=True)
class NewsEvidenceFragment:
    """기사 원문·장·출처 메타데이터에 결속한 뉴스 근거 조각."""

    fragment_id: str
    text: str
    text_sha256: str
    section_ids: tuple[str, ...]
    supported_claim_slots: tuple[str, ...]
    document_id: str
    published_on: str
    source_kind: str
    origin: str
    publisher: str
    title: str
    url: str
    statement_on: str = ""
    claim_kind: str = ""
    temporal_status: str = ""
    event_on: str = ""
    topic: str = ""
    event_key: str = ""
    source_category: str = ""
    span_start: int = -1
    span_end: int = -1

    def __post_init__(self) -> None:
        for value, label in (
            (self.fragment_id, "조각 식별자"),
            (self.text, "조각 원문"),
            (self.document_id, "기사 문서 식별자"),
            (self.published_on, "기사 날짜"),
            (self.source_kind, "출처 종류"),
            (self.origin, "수집 출처"),
            (self.publisher, "언론사명"),
            (self.title, "기사 제목"),
            (self.url, "기사 URL"),
        ):
            _text(value, label=label)
        sections = _unique(self.section_ids, label="조각 장")
        slots = _unique(self.supported_claim_slots, label="지원 claim slot")
        if not sections or set(sections) - set(REQUIRED_EVIDENCE_SECTION_IDS):
            raise ValueError("조각 장이 닫힌 목록 밖입니다")
        if not slots:
            raise ValueError("뉴스 조각에는 지원 claim slot이 필요합니다")
        if self.source_kind != c.SOURCE_KIND_NEWS:
            raise ValueError("뉴스 조각의 source_kind는 news여야 합니다")
        if self.origin != c.ORIGIN_NEWS_INTAKE:
            raise ValueError("뉴스 조각의 origin은 news_intake여야 합니다")
        if exact_text_sha256(self.text) != self.text_sha256:
            raise ValueError("뉴스 조각 원문과 SHA-256이 일치하지 않습니다")
        if self.statement_on:
            try:
                dt.date.fromisoformat(self.statement_on)
            except ValueError as error:
                raise ValueError("발언 시점은 확인한 YYYY-MM-DD 날짜여야 합니다") from error
        object.__setattr__(self, "section_ids", sections)
        object.__setattr__(self, "supported_claim_slots", slots)


@dataclass(frozen=True)
class NewsMappingResult:
    """장별 허용 규칙을 통과한 조각과 제외 사유."""

    fragments: tuple[NewsEvidenceFragment, ...]
    exclusion_counts: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len({item.fragment_id for item in self.fragments}) != len(self.fragments):
            raise ValueError("뉴스 조각 식별자가 중복됐습니다")
        object.__setattr__(
            self, "exclusion_counts", _counts(self.exclusion_counts, label="매핑 제외 사유")
        )

    @property
    def fragment_counts_by_section(self) -> dict[str, int]:
        counts = {section_id: 0 for section_id in REQUIRED_EVIDENCE_SECTION_IDS}
        for fragment in self.fragments:
            for section_id in fragment.section_ids:
                counts[section_id] += 1
        return counts


@dataclass(frozen=True)
class NewsIntakeDiagnostics:
    """검색부터 조각 생성까지의 단계별 진단 숫자."""

    searched_count: int
    selected_count: int
    classified_count: int
    fetched_count: int
    fragment_counts_by_section: Mapping[str, int]
    exclusion_counts: Mapping[str, int]

    def __post_init__(self) -> None:
        for value, label in (
            (self.searched_count, "검색 기사 수"),
            (self.selected_count, "필터 후 기사 수"),
            (self.classified_count, "분류 채택 기사 수"),
            (self.fetched_count, "본문 조회 기사 수"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{label}는 0 이상의 정수여야 합니다")
        if not (
            self.searched_count >= self.selected_count >= self.classified_count
            >= self.fetched_count
        ):
            raise ValueError("뉴스 단계별 기사 수가 검색→선별→분류→본문 순서를 어겼습니다")
        if set(self.fragment_counts_by_section) - set(REQUIRED_EVIDENCE_SECTION_IDS):
            raise ValueError("장별 뉴스 조각 진단에 닫힌 목록 밖 장이 있습니다")
        fragment_counts: dict[str, int] = {}
        for section_id in REQUIRED_EVIDENCE_SECTION_IDS:
            value = self.fragment_counts_by_section.get(section_id, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("장별 뉴스 조각 개수는 0 이상의 정수여야 합니다")
            fragment_counts[section_id] = value
        object.__setattr__(self, "fragment_counts_by_section", fragment_counts)
        object.__setattr__(
            self, "exclusion_counts", _counts(self.exclusion_counts, label="뉴스 제외 사유")
        )

    @property
    def search_count(self) -> int:
        return self.searched_count

    @property
    def filtered_count(self) -> int:
        return self.selected_count

    @property
    def body_fetched_count(self) -> int:
        return self.fetched_count


def build_diagnostics(
    selection: NewsSelectionResult,
    classification: NewsClassificationResult,
    mapping: NewsMappingResult,
) -> NewsIntakeDiagnostics:
    """세 단계 결과를 중복 없이 한 진단 객체로 합친다."""

    exclusions: dict[str, int] = {}
    for source in (
        selection.exclusion_counts,
        classification.exclusion_counts,
        mapping.exclusion_counts,
    ):
        for reason, count in source.items():
            exclusions[reason] = exclusions.get(reason, 0) + count
    return NewsIntakeDiagnostics(
        searched_count=selection.searched_count,
        selected_count=selection.selected_count,
        classified_count=classification.classified_count,
        fetched_count=classification.fetched_count,
        fragment_counts_by_section=mapping.fragment_counts_by_section,
        exclusion_counts=exclusions,
    )


@dataclass(frozen=True)
class NewsCompanyContext:
    """호출자가 확인한 대상 법인 신원.

    aliases는 DART 공식 영문명·종목명 또는 동일 법인임을 공식 원문에서 확인한
    이름만 받는다. 사용자 입력·검색 제목의 추측 이름은 넣지 않는다.
    domain도 확인된 공식 도메인만 받는다.
    """

    company_name: str
    aliases: tuple[str, ...] = ()
    domain: str = ""
    executive_names: tuple[str, ...] = ()
    identity_context: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "company_name", _text(self.company_name, label="회사명"))
        object.__setattr__(self, "aliases", _unique(self.aliases, label="회사 별칭"))
        object.__setattr__(self, "executive_names", _unique(self.executive_names, label="임원명"))
        _text(self.domain, label="공식 도메인", allow_empty=True)
        _text(self.identity_context, label="법인 정체성 문맥", allow_empty=True)
        if len(self.identity_context) > c.COMPANY_CONTEXT_CHARS:
            raise ValueError("법인 정체성 문맥이 글자 상한을 넘었습니다")


@dataclass(frozen=True)
class NewsCollectionPolicy:
    """검색과 유료 본문 검수가 함께 결속하는 비용·출처 정책."""

    max_search_calls: int = c.SEARCH_CALL_BUDGET
    search_page_size: int = c.SEARCH_PAGE_SIZE
    max_candidates: int = c.SEARCH_CANDIDATE_BUDGET
    max_search_seconds: int = c.SEARCH_SECONDS_BUDGET
    max_body_articles: int = c.BODY_ARTICLE_BUDGET
    max_body_calls: int = c.BODY_CALL_BUDGET
    max_body_chars: int = c.BODY_CHARS_PER_ARTICLE
    max_total_body_chars: int = c.BODY_TOTAL_CHARS_BUDGET
    max_collection_seconds: int = c.COLLECTION_SECONDS_BUDGET
    batch_size: int = c.GROUNDED_BATCH_SIZE
    max_analysis_calls: int = c.GROUNDED_CALL_BUDGET
    analysis_max_tokens: int = c.GROUNDED_MAX_TOKENS
    max_prompt_chars: int = c.GROUNDED_PROMPT_CHARS_BUDGET
    max_articles: int = c.FINAL_ARTICLE_BUDGET
    max_fragments: int = c.FINAL_FRAGMENT_BUDGET
    max_fragment_chars: int = c.FINAL_FRAGMENT_CHARS_BUDGET
    sufficient_events: int = c.SUFFICIENT_DISTINCT_EVENTS
    sufficient_topics: int = c.SUFFICIENT_DISTINCT_TOPICS
    trusted_publisher_domains: tuple[str, ...] = c.TRUSTED_PUBLISHER_DOMAINS
    max_search_transport_attempts: int = c.SEARCH_TRANSPORT_ATTEMPT_BUDGET

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if name == "trusted_publisher_domains":
                _unique(value, label="확인한 언론 도메인")
            elif name == "max_analysis_calls":
                if type(value) is not int or value < 0:
                    raise ValueError("뉴스 분석 호출 상한은 0 이상의 정수여야 합니다")
            elif type(value) is not int or value <= 0:
                raise ValueError(f"{name} 상한은 양의 정수여야 합니다")
        # 사용자가 조절하더라도 외부 요청·모델입력의 절대 경계는 남긴다.
        ceilings = {
            "max_search_calls": c.SEARCH_CALL_BUDGET,
            "max_search_transport_attempts": c.SEARCH_TRANSPORT_ATTEMPT_BUDGET,
            "search_page_size": c.SEARCH_PAGE_SIZE,
            "max_candidates": c.SEARCH_CANDIDATE_BUDGET,
            "max_search_seconds": c.SEARCH_SECONDS_BUDGET,
            "max_body_articles": c.BODY_ARTICLE_BUDGET,
            "max_body_calls": c.BODY_CALL_BUDGET,
            "max_body_chars": c.BODY_CHARS_PER_ARTICLE,
            "max_total_body_chars": c.BODY_TOTAL_CHARS_BUDGET,
            "max_collection_seconds": c.COLLECTION_SECONDS_BUDGET,
            "batch_size": c.GROUNDED_BATCH_SIZE,
            "max_analysis_calls": c.GROUNDED_CALL_BUDGET,
            "analysis_max_tokens": c.GROUNDED_MAX_TOKENS,
            "max_prompt_chars": c.GROUNDED_PROMPT_CHARS_BUDGET,
            "max_articles": c.FINAL_ARTICLE_BUDGET,
            "max_fragments": c.FINAL_FRAGMENT_BUDGET,
            "max_fragment_chars": c.FINAL_FRAGMENT_CHARS_BUDGET,
        }
        if any(getattr(self, name) > ceiling for name, ceiling in ceilings.items()):
            raise ValueError("뉴스 정책이 절대 비용 상한을 넘었습니다")


@dataclass(frozen=True)
class NewsQueryAttempt:
    """논리 검색의 옵션·응답 지문·전송 관측을 분리한다.

    None은 관측 불가이며 전송 0회와 다르다. 구형 callback은 내부 상한을
    강제할 수 없어 남은 예산 전체를 보수 차감하고 추가 검색을 중단한다.
    """

    query: str
    sort: str
    start: int
    display: int
    topic: str
    window_months: int
    state: str
    reason_code: str
    returned_count: int
    item_fingerprints: tuple[str, ...] = ()
    transport_attempts: int | None = None
    retry_recovered: bool | None = None
    attempt_reason_codes: tuple[str, ...] = ()
    transport_budget_supported: bool = False
    transport_budget_charged: int = 0
    transport_diagnostic_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.transport_attempts is not None and (
            type(self.transport_attempts) is not int or self.transport_attempts < 0
        ):
            raise ValueError("뉴스 전송 관측값은 0 이상의 정수 또는 미관측이어야 합니다")
        if self.retry_recovered is not None and type(self.retry_recovered) is not bool:
            raise ValueError("뉴스 재시도 복구 관측값이 올바르지 않습니다")
        if type(self.transport_budget_charged) is not int or self.transport_budget_charged < 0:
            raise ValueError("뉴스 전송 예산 차감은 0 이상의 정수여야 합니다")
        if type(self.transport_budget_supported) is not bool:
            raise ValueError("뉴스 전송 예산 지원 표시가 올바르지 않습니다")
        if (not isinstance(self.attempt_reason_codes, tuple)
                or any(code not in c.SEARCH_TRANSPORT_ATTEMPT_REASON_CODES for code in self.attempt_reason_codes)):
            raise ValueError("뉴스 전송 시도 사유가 닫힌 목록 밖입니다")
        if self.transport_attempts is not None and len(self.attempt_reason_codes) != self.transport_attempts:
            raise ValueError("뉴스 실제 전송 수와 시도 사유 수가 다릅니다")


@dataclass(frozen=True)
class NewsSearchSnapshot:
    """AI 호출 전에 고정한 후보 예비집합. 본문 단계에서는 검색하지 않는다."""

    candidates: tuple[NewsCandidate, ...]
    query_attempts: tuple[NewsQueryAttempt, ...]
    company_digest: str
    policy_digest: str
    as_of: str
    digest: str
    status: str
    reason_codes: tuple[str, ...]
    cache_eligible: bool
    exclusion_counts: Mapping[str, int] = field(default_factory=dict)
    window_months: tuple[int, ...] = c.WINDOW_MONTHS
    unverified_publishers: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "exclusion_counts", MappingProxyType(dict(self.exclusion_counts)))
        object.__setattr__(self, "unverified_publishers", MappingProxyType(dict(self.unverified_publishers)))

    @property
    def transport_diagnostics(self) -> dict[str, object]:
        """검색 직후와 본문 실패 시에도 같은 관측 의미를 전달한다."""
        attempts = self.query_attempts
        observed = all(attempt.transport_attempts is not None for attempt in attempts)
        known_count = sum(attempt.transport_attempts or 0 for attempt in attempts)
        recovered = sum(attempt.retry_recovered is True for attempt in attempts)
        codes = tuple(dict.fromkeys(code for attempt in attempts for code in attempt.transport_diagnostic_codes))
        return {
            "검색호출": len(attempts), "검색논리호출": len(attempts),
            "검색실제전송": known_count if observed else None,
            "검색관측전송": known_count, "검색전송관측완료": observed,
            "검색전송예산차감": sum(attempt.transport_budget_charged for attempt in attempts),
            "검색전송상한보장": observed and all(attempt.transport_budget_supported for attempt in attempts) and not codes,
            "검색재시도복구": recovered if all(attempt.retry_recovered is not None for attempt in attempts) else None,
            "검색전송진단": codes,
            "검색전송시도": tuple({
                "논리순번": index, "전송횟수": attempt.transport_attempts,
                "재시도복구": attempt.retry_recovered, "시도사유": attempt.attempt_reason_codes,
                "예산지원": attempt.transport_budget_supported,
                "예산차감": attempt.transport_budget_charged,
                "진단": attempt.transport_diagnostic_codes,
            } for index, attempt in enumerate(attempts, start=1)),
        }


@dataclass(frozen=True)
class GroundedNewsExcerpt:
    """본문 분석과 원문/법인 검사를 통과한 연속 인용 범위."""

    candidate: NewsCandidate
    text: str
    section_id: str
    claim_slot: str
    claim_kind: str
    temporal_status: str
    topic: str
    event_key: str
    event_on: str
    span_start: int
    span_end: int


@dataclass(frozen=True)
class GroundedNewsArticle:
    """목록과 본문이 공동으로 참조하는 검증된 기사 정본."""

    candidate: NewsCandidate
    document_content_sha256: str
    excerpts: tuple[GroundedNewsExcerpt, ...]


@dataclass(frozen=True)
class NewsCollectionResult:
    """실질 자료와 부족/장애 진단을 함께 반환한다."""

    fragments: tuple[NewsEvidenceFragment, ...]
    document_hashes: Mapping[str, str]
    diagnostics: Mapping[str, object]
    snapshot_digest: str
    articles: tuple[GroundedNewsArticle, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "document_hashes", MappingProxyType(dict(self.document_hashes)))
        object.__setattr__(self, "diagnostics", MappingProxyType(dict(self.diagnostics)))
