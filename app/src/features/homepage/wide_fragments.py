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
"""

from __future__ import annotations

import hashlib

from src.features.homepage.constants import WIDE_SLOT_BODY_KEYWORDS
from src.features.homepage.wide_domain import slot_ids_for_url
from src.features.homepage.wide_types import WideDocumentIdentity, WideFragment

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
