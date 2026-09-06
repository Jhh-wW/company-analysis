"""뉴스 선별·읽기·조각 매핑 사이의 불변 자료형."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field

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

    def __post_init__(self) -> None:
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

    def __post_init__(self) -> None:
        for value, label in (
            (self.text, "기사 본문"),
            (self.reason_code, "본문 실패 사유"),
            (self.stage, "본문 추출 단계"),
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
        if "culture" in sections and self.statement_on != self.published_on:
            raise ValueError("8장 뉴스 조각에는 기사 날짜와 같은 발언 시점이 필요합니다")
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
