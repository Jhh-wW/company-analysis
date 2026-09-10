"""뉴스 보조근거가 «그 뉴스를 인용한 장»에만 붙는 경계만 검증한다.

모델의 의미 판단 품질을 증명하지 않는다. 검수 프롬프트에 «어떤 원문이
실리는가»라는 입력 범위만 본다.
"""

import json

import pytest

from src.features.composer.port import CollectedFragment, ComposedSentence
from src.features.composer.verify import (
    REVIEW_SUMMARY_GROUP, _GroupedReviewItem, _ReviewItem,
    _build_grouped_review_prompt, _build_review_prompt,
)


NEWS_KIND = "news"
OFFICIAL_KIND = "사업내용"


def _official(fragment_id: str, text: str, *slots: str) -> CollectedFragment:
    return CollectedFragment(
        fragment_id, OFFICIAL_KIND, text, supported_claim_slots=tuple(slots)
    )


def _news(fragment_id: str, text: str, *slots: str) -> CollectedFragment:
    return CollectedFragment(
        fragment_id, NEWS_KIND, text, supported_claim_slots=tuple(slots),
        formal_source_kind=NEWS_KIND,
    )


def _carries(prompt: str, fragment: CollectedFragment) -> bool:
    return json.dumps(fragment.text, ensure_ascii=False) in prompt


#: 뉴스는 사업운영 장에만 있고, 문화 장 후보는 공식 조각만 인용한다.
NEWS_SECTION = "operations_partners"
OTHER_SECTION = "culture"


def _mixed_batch():
    news = _news("n1", "보도: 지점 수가 줄었다는 기사 본문.", NEWS_SECTION + ":operating_role")
    cited_official = _official(
        "o1", "공식: 회사는 비대면 채널을 운영한다.", NEWS_SECTION + ":operating_role"
    )
    same_section_uncited = _official(
        "o2", "공식: 같은 장의 인용하지 않은 지점 운영 자료.",
        NEWS_SECTION + ":operating_role",
    )
    other_section_uncited = _official(
        "o3", "공식: 다른 장의 인용하지 않은 인사 절차 자료.",
        OTHER_SECTION + ":work_principle",
    )
    other_section_cited = _official(
        "o4", "공식: 문화 장이 실제로 인용한 윤리강령 자료.",
        OTHER_SECTION + ":work_principle",
    )
    items = (
        _ReviewItem(
            1,
            ComposedSentence(
                "회사는 비대면 채널을 운영한다.", ("n1", "o1"), "확인",
                planned_claim_slot=NEWS_SECTION + ":operating_role",
            ),
            NEWS_SECTION,
        ),
        _ReviewItem(
            2,
            ComposedSentence(
                "회사는 윤리강령을 제정한다.", ("o4",), "확인",
                planned_claim_slot=OTHER_SECTION + ":work_principle",
            ),
            OTHER_SECTION,
        ),
    )
    fragments = {
        fragment.fragment_id: fragment
        for fragment in (
            news, cited_official, same_section_uncited,
            other_section_uncited, other_section_cited,
        )
    }
    return items, fragments, {
        "news": news, "cited_official": cited_official,
        "same_section_uncited": same_section_uncited,
        "other_section_uncited": other_section_uncited,
        "other_section_cited": other_section_cited,
    }


def test_uncited_official_sources_from_sections_without_news_are_excluded():
    items, fragments, named = _mixed_batch()
    prompt = _build_review_prompt(items, fragments, "")
    assert not _carries(prompt, named["other_section_uncited"])


def test_uncited_official_sources_in_news_owner_section_are_preserved():
    items, fragments, named = _mixed_batch()
    prompt = _build_review_prompt(items, fragments, "")
    # 보도와 공식자료의 모순·시점 대조가 이 자료에 기대므로 약화하면 안 된다.
    assert _carries(prompt, named["same_section_uncited"])


def test_directly_cited_sources_are_preserved_regardless_of_section():
    items, fragments, named = _mixed_batch()
    prompt = _build_review_prompt(items, fragments, "")
    for key in ("news", "cited_official", "other_section_cited"):
        assert _carries(prompt, named[key]), key


def test_no_uncited_official_sources_are_added_without_news():
    _items, fragments, named = _mixed_batch()
    only_official = (
        _ReviewItem(
            1,
            ComposedSentence(
                "회사는 윤리강령을 제정한다.", ("o4",), "확인",
                planned_claim_slot=OTHER_SECTION + ":work_principle",
            ),
            OTHER_SECTION,
        ),
    )
    prompt = _build_review_prompt(only_official, fragments, "")
    assert _carries(prompt, named["other_section_cited"])
    for key in ("same_section_uncited", "other_section_uncited", "news"):
        assert not _carries(prompt, named[key]), key


def test_conflicting_official_source_in_news_owner_section_is_preserved():
    """뉴스 주장과 어긋나는 같은 장 원문을 빼면 모순 검수가 죽는다."""

    news = _news("n1", "보도: 지점을 늘렸다는 기사 본문.", NEWS_SECTION + ":operating_role")
    conflicting = _official(
        "o2", "공식: 같은 기간 지점 수는 오히려 줄었다.",
        NEWS_SECTION + ":operating_role",
    )
    items = (
        _ReviewItem(
            1,
            ComposedSentence(
                "회사는 지점을 늘렸다.", ("n1",), "확인",
                planned_claim_slot=NEWS_SECTION + ":operating_role",
            ),
            NEWS_SECTION,
        ),
    )
    prompt = _build_review_prompt(
        items, {"n1": news, "o2": conflicting}, ""
    )
    assert _carries(prompt, conflicting)


@pytest.mark.parametrize("section_id", (REVIEW_SUMMARY_GROUP, "3", ""))
def test_unknown_owner_falls_back_to_single_planned_claim_section(section_id):
    """요약 묶음·그룹 번호처럼 section_id 를 장으로 쓸 수 없는 경우의 경계.

    장별 안내가 고르는 장과 보조근거 범위가 갈라지면 안 된다.
    """

    news = _news("n1", "보도: 신규 서비스 출시 기사 본문.", NEWS_SECTION + ":operating_role")
    same_section_uncited = _official(
        "o2", "공식: 같은 장의 인용하지 않은 채널 운영 자료.",
        NEWS_SECTION + ":operating_role",
    )
    other_section_uncited = _official(
        "o3", "공식: 다른 장의 인용하지 않은 인사 절차 자료.",
        OTHER_SECTION + ":work_principle",
    )
    items = (
        _ReviewItem(
            1,
            ComposedSentence(
                "회사는 신규 서비스를 출시했다.", ("n1",), "확인",
                planned_claim_slot=NEWS_SECTION + ":operating_role",
            ),
            section_id,
        ),
    )
    prompt = _build_review_prompt(
        items,
        {"n1": news, "o2": same_section_uncited, "o3": other_section_uncited},
        "",
    )
    assert _carries(prompt, same_section_uncited)
    assert not _carries(prompt, other_section_uncited)


def test_actual_owner_takes_precedence_over_planned_claim_section():
    """slot 앞부분이 아니라 실제 소유 장의 자료만 보조로 붙어야 한다."""

    news = _news("n1", "보도: 조직 개편 기사 본문.", OTHER_SECTION + ":work_principle")
    owning_section_uncited = _official(
        "o2", "공식: 소유 장의 인용하지 않은 인사 절차 자료.",
        OTHER_SECTION + ":work_principle",
    )
    slot_section_uncited = _official(
        "o3", "공식: 주장 범주 앞부분 장의 인용하지 않은 채널 자료.",
        NEWS_SECTION + ":operating_role",
    )
    items = (
        _ReviewItem(
            1,
            ComposedSentence(
                "회사는 조직을 개편했다.", ("n1",), "확인",
                # 소유 장은 culture 인데 계획된 주장 범주만 operations_partners 다.
                planned_claim_slot=NEWS_SECTION + ":operating_role",
            ),
            OTHER_SECTION,
        ),
    )
    prompt = _build_review_prompt(
        items,
        {"n1": news, "o2": owning_section_uncited, "o3": slot_section_uncited},
        "",
    )
    assert _carries(prompt, owning_section_uncited)
    assert not _carries(prompt, slot_section_uncited)


def test_grouped_review_section_isolation_is_preserved():
    """grouped 경로는 이번 수정 대상이 아니며 동작이 바뀌면 안 된다."""

    news = _news("n1", "보도: 지점 수가 줄었다는 기사 본문.", NEWS_SECTION + ":operating_role")
    same_section_uncited = _official(
        "o2", "공식: 같은 장의 인용하지 않은 지점 운영 자료.",
        NEWS_SECTION + ":operating_role",
    )
    other_section_uncited = _official(
        "o3", "공식: 다른 장의 인용하지 않은 인사 절차 자료.",
        OTHER_SECTION + ":work_principle",
    )
    prompt = _build_grouped_review_prompt(
        (
            _GroupedReviewItem(
                1, NEWS_SECTION, "문장", ("n1",),
                sentence=ComposedSentence(
                    "회사는 지점을 줄였다.", ("n1",), "확인",
                    planned_claim_slot=NEWS_SECTION + ":operating_role",
                ),
            ),
        ),
        {"n1": news, "o2": same_section_uncited, "o3": other_section_uncited},
        None,
    )
    assert _carries(prompt, same_section_uncited)
    assert not _carries(prompt, other_section_uncited)
