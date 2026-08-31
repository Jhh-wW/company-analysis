"""문서의 usable_ranges 텍스트 구간을 슬롯 태그된 조각(fragment)으로 바꾼다.

★ «후보 슬롯»은 페이지 유형(URL 키워드, ``wide_domain.slot_ids_for_url`` —
  ``wide_collect.py``의 attempt.slot_ids와 같은 표 하나를 재사용한다)으로
  먼저 정한다.
★ 구간 본문에 그 후보 슬롯의 «가벼운» 신호 키워드가 있으면 그 슬롯(들)만,
  하나도 없으면 후보 전체를 매긴다. 본격적인 분류기가 아니다 —
  ``constants.WIDE_SLOT_BODY_KEYWORDS``에 없는 슬롯은 항상 후보 전체로
  매겨진다.
★ 페이지 유형을 못 알아낸 문서(빈 후보)는 조각을 만들지 않는다 — 근거
  없이 슬롯을 지어내지 않는다.
★ ``slot_id``는 ``WideFragment``가 강제하는 허용 어휘(정본은
  `app/src/shared/report_evidence/policy.py`)만 나온다. comparison_*·
  limitation·historical_performance는 이 어휘에 없으므로 이 모듈에서
  «절대» 만들어지지 않는다(시험으로 0건을 고정한다).
★ 계약 generation=8: ``build_fragments``는 ``company_id``를 **필수
  키워드 인자**로 받는다 — ``document.company_id``에서 자동으로 채워
  넣지 않는다. 호출자(문서마다 이 함수를 부르는 다음 담당자)가 «지금
  이 조회가 실제로 어느 회사를 대상으로 했는지»를 매번 직접 넘겨야
  한다. document의 company_id와 자동으로 같다고 가정하면 다른 회사
  조회 결과가 섞여도 조용히 통과할 수 있어, 그 가정 자체를 없앴다.
  ``build_fragments``는 이 명시 인자를 **공격 시험 전용**으로 유지한다
  — 「호출자가 일부러 다른 값을 넘기는 정당한 운영 시나리오」는 없다
  (팀 리드 2026-08-31). **운영 호출부는 대신 아래
  ``build_fragments_for_collection``을 쓴다** — 수집 결과 자신에서
  company_id를 한 번만 꺼내 모든 문서에 그대로 실으므로, 호출 지점에서
  값을 잘못 넘길 방법이 구조적으로 없다(``build_fragments``를 직접
  호출하며 손으로 company_id를 옮겨 적지 않는다).
"""

from __future__ import annotations

import hashlib

from src.features.homepage.constants import WIDE_SLOT_BODY_KEYWORDS
from src.features.homepage.wide_domain import slot_ids_for_url
from src.features.homepage.wide_types import (
    WideCollectionResult,
    WideDocumentIdentity,
    WideFragment,
)

#: 구간 본문에 후보 슬롯의 신호 키워드가 «있을 때»의 점수.
_SCORE_BODY_KEYWORD_MATCH = 700

#: 신호 키워드가 «없어» 페이지 유형 후보 전체로 매길 때의 점수.
_SCORE_PAGE_TYPE_ONLY = 400

_REASON_PAGE_TYPE_SIGNAL = "page_type_signal"
_REASON_BODY_KEYWORD_MATCH = "body_keyword_match"


def build_fragments(document: WideDocumentIdentity, *, company_id: str) -> tuple[WideFragment, ...]:
    """문서 하나의 usable_ranges 전부에서 슬롯 태그된 fragment를 만든다.

    Args:
        document: 이미 검증을 통과한 ``WideDocumentIdentity``.
        company_id: 이 조회가 실제로 대상으로 한 회사 식별자(필수 — 계약
            generation=8). ``document.company_id``와 다르게 넘겨도 이
            함수는 막지 않는다(그 판정은 앱 계약의 몫이다) — 호출자가
            «생각 없이 document 값을 그대로 베끼는» 습관을 없애기 위해
            의도적으로 독립된 인자로 뒀다.

    Returns:
        슬롯이 태그된 ``WideFragment`` 튜플. 페이지 유형을 못 알아낸
        문서라면 빈 튜플(조각을 지어내지 않는다).
    """
    page_slots = slot_ids_for_url(document.canonical_url)
    if not page_slots:
        return ()

    fragments: list[WideFragment] = []
    for index, text in enumerate(document.usable_ranges):
        matched_slots = _matched_body_slots(text, page_slots)
        if matched_slots:
            score = _SCORE_BODY_KEYWORD_MATCH
            reason_codes = (_REASON_PAGE_TYPE_SIGNAL, _REASON_BODY_KEYWORD_MATCH)
            slots_for_range = matched_slots
        else:
            score = _SCORE_PAGE_TYPE_ONLY
            reason_codes = (_REASON_PAGE_TYPE_SIGNAL,)
            slots_for_range = page_slots

        text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        location = f"{document.canonical_url}#{index}"
        for slot_id in slots_for_range:
            section_id = slot_id.split(":", 1)[0]
            fragments.append(
                WideFragment(
                    company_id=company_id,
                    fragment_id=_fragment_id(document.document_id, index, slot_id),
                    document_id=document.document_id,
                    location=location,
                    text_sha256=text_sha256,
                    text=text,
                    section_id=section_id,
                    slot_id=slot_id,
                    score_millis=score,
                    reason_codes=reason_codes,
                )
            )
    return tuple(fragments)


def build_fragments_for_collection(result: WideCollectionResult) -> tuple[WideFragment, ...]:
    """수집 결과의 모든 문서에서 fragment를 한 번에 만드는 얇은 편의 함수.

    ★ 계약 generation=8 마지막 고리(팀 리드 2026-08-31) — **운영 호출부는
      이 함수를 쓴다**(``build_fragments``에 지역 거부를 추가하는 대신
      구조로 막는다). ``result.company_id``를 정본으로 모든 문서에 그대로
      싣는다 — **documents에서 역산하지 않는다.** documents에서 역산하면
      문서 생성부에 버그가 생겨 문서들이 일제히 엉뚱한 회사 값을 갖게 될
      때 서로는 일치하므로 대조할 정본이 없어 아무도 못 잡는다.
      ``result.company_id``는 ``collect_official_web_documents(company_id=...)``
      가 호출 인자로 받은 값이라 documents와 독립된 정본이고,
      ``WideCollectionResult.__post_init__``이 이미 모든 document·attempt의
      company_id가 이 값과 같은지 생성 시점에 확인해 뒀다(다르면 그
      시점에 ValueError로 걸린다) — 그래서 여기서는 다시 검증하지 않고
      곧바로 신뢰해 쓴다.

    Args:
        result: ``collect_official_web_documents``가 돌려준 수집 결과.

    Returns:
        모든 문서의 fragment를 이어붙인 튜플(결정론 순서 — documents
        순서·문서 내 구간 순서를 그대로 따른다). 문서가 0건이면 빈 튜플.
    """
    return tuple(
        fragment
        for document in result.documents
        for fragment in build_fragments(document, company_id=result.company_id)
    )


def _matched_body_slots(text: str, candidate_slots: tuple[str, ...]) -> tuple[str, ...]:
    """후보 슬롯 중 구간 본문에 신호 키워드가 있는 것만 고른다."""
    lowered = text.lower()
    return tuple(
        slot_id
        for slot_id in candidate_slots
        if any(
            keyword.lower() in lowered
            for keyword in WIDE_SLOT_BODY_KEYWORDS.get(slot_id, ())
        )
    )


def _fragment_id(document_id: str, index: int, slot_id: str) -> str:
    """문서ID·구간 위치·슬롯으로 결정론적 fragment_id를 만든다."""
    digest = hashlib.sha256(f"{document_id}:{index}:{slot_id}".encode("utf-8")).hexdigest()
    return f"frag-{digest[:24]}"
