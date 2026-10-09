"""검증된 같은 실행의 공식 자료만 마지막 선택 호출에 연결한다."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from typing import Any

from src.core.official_industry_context_adapter import collect_official_industry_context
from src.features.pipeline.business_activity_anchors import (
    _matches_location, build_business_activity_anchors,
)
from src.features.pipeline.official_news_aliases import _document_matches_profile, _validate_collection
from src.features.pipeline import official_industry_context_constants as c
from src.shared.business_challenge_context import BusinessActivityAnchor
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult
from src.shared.report_evidence.industry_candidates import OfficialIndustryCandidateEvidence, OfficialIndustrySupplement
from src.shared.report_evidence.source_kind_policy import formal_document_is_writer_eligible
from src.shared.report_evidence.constants import SOURCE_KIND_NEWS
from src.shared.report_evidence.practice_context import parse_practice_context
from src.shared.report_evidence.news_business_activity import (
    news_business_anchor_problem, news_business_fragment_matches,
)


def _matches_selected(fragment: object, document: object, selected: object) -> bool:
    return (
        not any(getattr(selected, field, "") for field in c.SUBITEM_FIELDS)
        and all(getattr(selected, left, None) == getattr(document, right)
                for left, right in (*c.SELECTED_DOCUMENT_FIELDS, *c.SELECTED_PROVENANCE_FIELDS))
        and all(getattr(selected, field, None) == getattr(fragment, field)
                for field in c.SELECTED_FRAGMENT_FIELDS)
    )


def prepare_official_industry_fallback(
    *, collection: OfficialEvidenceCollectionResult | None,
    profile: Mapping[str, Any] | None, company_id: str, reference_date: str,
    anchors: tuple[BusinessActivityAnchor, ...], analyze: Callable,
    available_calls: Callable[[], int], diagnostics: list[dict],
    budget_exceptions: tuple[type[Exception], ...] = (),
    collect: Callable = collect_official_industry_context,
    news_fragments: tuple = (),
) -> tuple[tuple[BusinessActivityAnchor, ...], Callable | None]:
    """누락 신원은 호출하지 않으며, 선택 후 exact 원문에 한 번만 분석을 허용한다."""
    if (type(collection) is not OfficialEvidenceCollectionResult
            or not isinstance(profile, Mapping)
            or profile.get("corp_code") != company_id
            or not profile.get("corp_name") or profile.get("status", "000") != "000"):
        return anchors, None
    try:
        _validate_collection(collection)
        if collection.company_id != company_id:
            return anchors, None
        validated_anchors = build_business_activity_anchors(collection, profile=profile)
        # 공식 앵커는 공식 원문, 뉴스 앵커는 선택된 검증 뉴스 원문으로 각각 재확인한다.
        def matching_news_fragment(anchor, fragment):
            return news_business_fragment_matches(
                fragment, anchor, company_id=company_id, reference_date=reference_date,
            )

        usable_anchors = tuple(value for value in (anchors or validated_anchors)
                               if value in validated_anchors or (
                                   value.source_kind == SOURCE_KIND_NEWS
                                   and not news_business_anchor_problem(
                                       value, company_id=company_id, reference_date=reference_date,
                                   )
                                   and any(matching_news_fragment(value, fragment)
                                           for fragment in news_fragments)
                               ))
        originals = []
        industry_originals = []
        seen = set()
        for group in collection.candidates:
            documents = {value.document_id: value for value in group.documents}
            for fragment in group.fragments:
                document = documents.get(fragment.document_id)
                key = (fragment.document_id, fragment.location, fragment.text_sha256)
                if (document is None or key in seen
                        or fragment.company_id != company_id or document.company_id != company_id
                        or fragment.range_index >= 0
                        or any(getattr(fragment, field) for field in c.SUBITEM_FIELDS)
                        or not formal_document_is_writer_eligible(document)
                        or not _document_matches_profile(document, profile)
                        or not _matches_location(document, fragment)
                        or fragment.text_sha256 not in document.exact_evidence_hashes
                        or hashlib.sha256(fragment.text.encode("utf-8")).hexdigest() != fragment.text_sha256):
                    continue
                if parse_practice_context(
                    fragment.practice_context_json, document_id=fragment.document_id,
                    document_sha256=document.content_sha256,
                    fragment_location=fragment.location, fragment_sha256=fragment.text_sha256,
                    fragment_text=fragment.text,
                ):
                    continue
                originals.append((fragment, document))
                seen.add(key)
        for fragment in collection.industry_candidates:
            if type(fragment) is not OfficialIndustryCandidateEvidence:
                raise ValueError("공식 산업 후보 자료형이 다릅니다")
            fragment.__post_init__()
            document = fragment.document
            if parse_practice_context(fragment.practice_context_json,
                                      document_id=fragment.document_id,
                                      document_sha256=document.content_sha256,
                                      fragment_location=fragment.location,
                                      fragment_sha256=fragment.text_sha256,
                                      fragment_text=fragment.text):
                continue
            key = (fragment.document_id, fragment.location, fragment.text_sha256)
            if (key not in seen and fragment.company_id == company_id
                    and _document_matches_profile(document, profile)
                    and formal_document_is_writer_eligible(document)):
                industry_originals.append((fragment, document))
                seen.add(key)
    except (ValueError, TypeError, KeyError, AttributeError):
        return anchors, None
    if not usable_anchors or not originals:
        return anchors, None
    attempted = False

    def fallback(selected: tuple) -> tuple:
        nonlocal attempted
        if attempted:
            return ()
        attempted = True
        observation = {"step": c.OFFICIAL_INDUSTRY_DIAGNOSTIC_STEP, "상태": "후보부족"}
        diagnostics.append(observation)
        selected_originals = tuple((fragment, document) for fragment, document in originals
                           if any(_matches_selected(fragment, document, value) for value in selected))
        selected_anchors = tuple(anchor for anchor in usable_anchors if any(
            fragment.text == anchor.exact_text and fragment.location == anchor.location
            and document.document_id == anchor.document_id
            for fragment, document in selected_originals
        ) or (anchor.source_kind == SOURCE_KIND_NEWS
              and any(matching_news_fragment(anchor, fragment) for fragment in selected)))
        if not selected_originals or not selected_anchors:
            return ()
        candidates = selected_originals + tuple(industry_originals)
        if available_calls() <= 0:
            observation["상태"] = "호출여유없음"
            return ()
        calls = 0

        def analyze_once(*args):
            nonlocal calls
            if calls:
                raise ValueError("공식 산업 보조 분석은 한 번만 호출할 수 있습니다")
            calls += 1
            return analyze(*args)

        try:
            result = collect(candidates=candidates, anchors=selected_anchors,
                             company_id=company_id, reference_date=reference_date,
                             analyze=analyze_once, diagnostics=observation)
        except budget_exceptions:
            observation["상태"] = "요청예산부족"
            return ()
        observation["호출수"] = calls
        result = tuple(result)
        # 작성 이후 채택된 별도 원문만 출처 등록용으로 반환한다. 탐색 후보 전체를
        # 본문 근거 목록에 추가하거나 필수 칸의 점수로 바꾸지 않는다.
        used = tuple(fragment for fragment, document in industry_originals if any(
            problem.document_id == fragment.document_id
            and problem.location == fragment.location
            and problem.exact_text == fragment.text
            and problem.text_sha256 == fragment.text_sha256
            and problem.document_content_sha256 == document.content_sha256
            for problem in result
        ))
        return OfficialIndustrySupplement(problems=result, candidates=used) if used else result

    return usable_anchors, fallback
