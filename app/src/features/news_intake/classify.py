"""후보 메타데이터 1회 분류와 채택 기사 본문 조회."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Collection, Iterable

from src.features.news_intake import constants as c
from src.features.news_intake.models import (
    ClassifiedNewsCandidate,
    FetchedNewsArticle,
    NewsCandidate,
    NewsClassificationResult,
)
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS


Classifier = Callable[[str], str]
TextFetcher = Callable[[str], str | None]


class _DuplicateJsonKey(ValueError):
    """같은 JSON 키가 뒤 값을 덮어쓰는 응답을 거절한다."""


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> object:
    raise ValueError


def build_classification_prompt(candidates: Iterable[NewsCandidate]) -> str:
    """최대 20건의 제목·요약만 담은 strict JSON 분류 프롬프트."""

    limited = tuple(candidates)[: c.MAX_CANDIDATES]
    payload = [
        {
            "id": candidate.id,
            "title": candidate.title,
            "description": candidate.description,
        }
        for candidate in limited
    ]
    section_ids = ", ".join(REQUIRED_EVIDENCE_SECTION_IDS)
    kind_ids = ", ".join(c.ALLOWED_KINDS)
    return (
        "아래 뉴스 후보가 보고서 어느 장의 보조 근거로 쓸 만한지만 분류하세요. "
        "기사 내용을 요약하거나 새로 쓰지 마세요. 억지로 장을 채우지 마세요.\n"
        "응답은 설명이나 마크다운 없이 다음 꼴의 JSON 객체 하나만 반환하세요: "
        '{"items":[{"id":"news-001","sections":["portfolio"],'
        '"kind":"press_release"}]}\n'
        f"sections 허용값: {section_ids}. 후보당 최대 {c.MAX_SECTIONS_PER_CANDIDATE}개.\n"
        f"kind 허용값: {kind_ids}.\n"
        "후보:\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


def _strict_payload(raw: object) -> list[object] | None:
    if not isinstance(raw, str):
        return None
    try:
        payload = json.loads(
            raw,
            object_pairs_hook=_json_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or set(payload) != {"items"}:
        return None
    items = payload.get("items")
    return items if isinstance(items, list) else None


def _validated_classifications(
    *,
    candidates: tuple[NewsCandidate, ...],
    raw_response: object,
    eligible_sections: Collection[str],
) -> tuple[tuple[ClassifiedNewsCandidate, ...], dict[str, int]]:
    excluded: Counter[str] = Counter()
    raw_items = _strict_payload(raw_response)
    if raw_items is None:
        excluded[c.EXCLUDED_INVALID_JSON] += 1
        return (), dict(excluded)

    by_id = {candidate.id: candidate for candidate in candidates}
    seen: set[str] = set()
    classified: list[ClassifiedNewsCandidate] = []
    allowed_sections = set(REQUIRED_EVIDENCE_SECTION_IDS)
    for raw_item in raw_items:
        if not isinstance(raw_item, dict) or set(raw_item) != {"id", "sections", "kind"}:
            excluded[c.EXCLUDED_INVALID_JSON] += 1
            continue
        candidate_id = raw_item.get("id")
        if not isinstance(candidate_id, str) or candidate_id not in by_id:
            excluded[c.EXCLUDED_UNKNOWN_ID] += 1
            continue
        if candidate_id in seen:
            excluded[c.EXCLUDED_DUPLICATE_ID] += 1
            continue
        seen.add(candidate_id)
        raw_sections = raw_item.get("sections")
        if (
            not isinstance(raw_sections, list)
            or not raw_sections
            or any(not isinstance(section_id, str) for section_id in raw_sections)
        ):
            excluded[c.EXCLUDED_INVALID_JSON] += 1
            continue
        if len(raw_sections) > c.MAX_SECTIONS_PER_CANDIDATE or len(raw_sections) != len(
            set(raw_sections)
        ):
            excluded[c.EXCLUDED_TOO_MANY_SECTIONS] += 1
            continue
        valid_sections: list[str] = []
        for section_id in raw_sections:
            if section_id not in allowed_sections:
                excluded[c.EXCLUDED_UNKNOWN_SECTION] += 1
            elif section_id not in eligible_sections:
                # 대상에서 빠지는 장은 결국 공식 근거로 이미 채운 READY 장뿐이라
                # 사유 코드 이름은 그대로 둔다.
                excluded[c.EXCLUDED_READY_SECTION] += 1
            else:
                valid_sections.append(section_id)
        if not valid_sections:
            continue
        kind = raw_item.get("kind")
        if not isinstance(kind, str) or kind not in c.ALLOWED_KINDS:
            excluded[c.EXCLUDED_UNKNOWN_KIND] += 1
            continue
        classified.append(
            ClassifiedNewsCandidate(
                candidate=by_id[candidate_id],
                section_ids=tuple(valid_sections),
                kind=kind,
            )
        )
    return tuple(classified), dict(excluded)


def classify_candidates(
    candidates: Iterable[NewsCandidate],
    *,
    classify: Classifier,
    eligible_sections: Collection[str],
) -> NewsClassificationResult:
    """주입 분류기를 정확히 한 번 호출하고 응답을 닫힌 목록으로 검증한다.

    ``eligible_sections``는 「뉴스 보조 문장을 받을 수 있는 장」이다. 정본은
    ``select.news_eligible_sections``이며, 여기서는 그 결과를 그대로 쓴다.
    """

    candidate_tuple = tuple(candidates)
    excluded: Counter[str] = Counter()
    if len(candidate_tuple) > c.MAX_CANDIDATES:
        excluded[c.EXCLUDED_CANDIDATE_LIMIT] += len(candidate_tuple) - c.MAX_CANDIDATES
        candidate_tuple = candidate_tuple[: c.MAX_CANDIDATES]
    unknown_sections = set(eligible_sections) - set(REQUIRED_EVIDENCE_SECTION_IDS)
    if unknown_sections:
        raise ValueError("뉴스 대상 장에 닫힌 목록 밖 장이 있습니다")
    if not candidate_tuple:
        return NewsClassificationResult(classified=(), articles=(), exclusion_counts={})
    if not eligible_sections:
        return NewsClassificationResult(classified=(), articles=(), exclusion_counts={})
    try:
        raw_response = classify(build_classification_prompt(candidate_tuple))
    except Exception:
        excluded[c.EXCLUDED_INVALID_JSON] += 1
        return NewsClassificationResult(
            classified=(), articles=(), exclusion_counts=dict(excluded)
        )
    classified, validation_counts = _validated_classifications(
        candidates=candidate_tuple,
        raw_response=raw_response,
        eligible_sections=eligible_sections,
    )
    excluded.update(validation_counts)
    return NewsClassificationResult(
        classified=classified,
        articles=(),
        exclusion_counts=dict(excluded),
    )


def classify_and_read(
    candidates: Iterable[NewsCandidate],
    *,
    classify: Classifier,
    fetch_text: TextFetcher,
    eligible_sections: Collection[str],
) -> NewsClassificationResult:
    """분류 채택 후보만 주입 본문 함수로 한 번씩 읽는다."""

    classification = classify_candidates(
        candidates,
        classify=classify,
        eligible_sections=eligible_sections,
    )
    excluded = Counter(classification.exclusion_counts)
    articles: list[FetchedNewsArticle] = []
    for item in classification.classified:
        try:
            text = fetch_text(item.candidate.source_url)
        except Exception:
            text = None
        if not isinstance(text, str) or not text.strip():
            excluded[c.EXCLUDED_FETCH_FAILED] += 1
            continue
        articles.append(FetchedNewsArticle(classified=item, text=text))
    return NewsClassificationResult(
        classified=classification.classified,
        articles=tuple(articles),
        exclusion_counts=dict(excluded),
    )
