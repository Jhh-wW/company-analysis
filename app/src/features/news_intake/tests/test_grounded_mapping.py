"""같은 사건 뉴스 중복 판정 — «순수 부분 인용»만 좁게 인정하는 경계.

이 파일은 이전 구현(WOORI-reviewfix-fix, source SHA bcd80e6d...)이 총괄에게
거절된 뒤 다시 쓴 것이다. 이전 구현은 숫자 부분집합 + 대칭 유사도 0.90 문턱만
보는 «포함 비율» 경로를 썼는데, 부정어 하나만 바꾼 문장("319대 감소했다" →
"319대 감소하지 않았다")과 주체만 바꾼 문장("우리은행" → "기업은행")도
같은 사건으로 잘못 합쳤다(문자 단위 유사도는 부정어·고유명사 한 글자를 대세에
영향 없는 잡음으로 흡수해 버린다). 그 실패는
.local-artifacts/resume-20260909-news-event-dedup-correction/에 원본 그대로
스냅샷 보존했다.

이번 구현은 숫자 배열이 다를 때 «문장 전체 단위 정확 대응» + «발행처·발행일·
주제·claim_kind/temporal_status/event_on 전부 일치»를 모두 요구한다. 허용된
정규화는 문장 끝의 좁은 통계 서술 종결형 하나뿐이라(constants 참고), 부정어나
주체명이 하나라도 다르면 문장 키 자체가 달라져 대응에 실패한다.

cross-feature import 금지 — 이 파일은 news_intake 안의 모듈만 쓴다.
"""

from __future__ import annotations

from src.features.news_intake.grounded_mapping import same_event, select_diverse_excerpts
from src.features.news_intake.models import GroundedNewsExcerpt, NewsCandidate, NewsCollectionPolicy

# ══════════════════════════════════════════════════════════
# 고정 값 — 매직 넘버 대신 이름 붙인 상수로 둔다.
# ══════════════════════════════════════════════════════════

_PUBLISHED_ON = "2026-09-09"
_PUBLISHER_HANKYUNG = "hankyung.com"
_TOPIC_OPERATIONS = "operations"
_TOPIC_BUSINESS = "business"
_CLAIM_KIND_REPORTED = "reported_fact"
_TEMPORAL_COMPLETED = "completed"

#: WOORI 실제 최신 결과의 인용문 — .local-artifacts/resume-20260909-WOORI-reviewfix-news-public/README.md와
#: next-eval-news/WOORI/79f83d7dba5a80ec0708183f95aea79a의 selected-news.json에서 그대로 옮겼다.
_TEXT_327 = (
    "국민·신한·하나·우리은행 등 4대 시중은행과 부산·경남·iM·광주·전북은행 등 5개 지방은행이 "
    "운영하는 ATM은 올 상반기 말 기준 1만7647대로 집계됐다. 지난해 말보다 319대 감소했다. "
    "2024년 말 1만8954대와 비교하면 약 2년 만에 1307대가 사라졌다."
)
_TEXT_330 = (
    "국민·신한·하나·우리은행 등 4대 시중은행과 부산·경남·iM·광주·전북은행 등 5개 지방은행이 "
    "운영하는 ATM은 올 상반기 말 기준 1만7647대였다. 지난해 말보다 319대 감소했다."
)
_TEXT_326 = (
    "우리은행은 산업통상부와 함께 중견기업 금융지원 프로그램인 '라이징 리더 300"
    "(Rising Leaders 300)' 8기 기업 45개 사를 최종 선정하고 본격적인 지원에 나선다."
)
_TEXT_329 = (
    "우리은행이 산업통상자원부와 함께 우수 중견기업을 육성하는 '라이징 리더스 300"
    "(Rising Leaders 300)' 8기 기업 45개사를 선정하고, 총 5480억원 규모의 금융 지원에 나선다."
)


def _candidate(
    article_id: str,
    url: str,
    published_on: str = _PUBLISHED_ON,
    publisher: str = _PUBLISHER_HANKYUNG,
) -> NewsCandidate:
    return NewsCandidate(
        id=article_id, title="t", description="", originallink=url, link=url,
        published_on=published_on, publisher=publisher, priority=1, source_url=url,
        source_category="news_report",
    )


def _excerpt(
    candidate: NewsCandidate,
    text: str,
    *,
    topic: str = _TOPIC_OPERATIONS,
    event_key: str = "event",
    claim_kind: str = _CLAIM_KIND_REPORTED,
    temporal_status: str = _TEMPORAL_COMPLETED,
) -> GroundedNewsExcerpt:
    return GroundedNewsExcerpt(
        candidate=candidate, text=text, section_id="operations_partners",
        claim_slot="operations_partners:sample", claim_kind=claim_kind,
        temporal_status=temporal_status, topic=topic, event_key=event_key,
        event_on=candidate.published_on, span_start=0, span_end=len(text),
    )


def _excerpt_327() -> GroundedNewsExcerpt:
    return _excerpt(_candidate("327", "https://www.hankyung.com/article/202609092486i"), _TEXT_327)


def _excerpt_330() -> GroundedNewsExcerpt:
    return _excerpt(_candidate("330", "https://www.hankyung.com/article/2026090947061"), _TEXT_330)


# ══════════════════════════════════════════════════════════
# ① 최소 재현 — 실제 놓쳤던 중복(#327·#330)을 잡는가
# ══════════════════════════════════════════════════════════

def test_327_and_330_are_recognized_as_the_same_atm_decline_event() -> None:
    """숫자 배열이 달라도(330의 문장이 327 문장들과 순서대로 정확히 대응) 같은 사건으로 본다."""
    assert same_event(_excerpt_327(), _excerpt_330())
    assert same_event(_excerpt_330(), _excerpt_327())  # 순서를 바꿔도 대칭이어야 한다.


def test_select_diverse_excerpts_keeps_the_richer_327_object_unchanged() -> None:
    """중복으로 하나만 남길 때, 추가 문장(2024년 말 수치)이 있는 «기존 327 객체»를
    글자 하나 바꾸지 않고 그대로 대표로 남긴다."""
    original_327 = _excerpt_327()
    chosen, excluded = select_diverse_excerpts([_excerpt_330(), original_327], NewsCollectionPolicy())

    assert len(chosen) == 1
    assert chosen[0] is original_327  # 새로 만든 값이 아니라 기존 객체 그대로.
    assert chosen[0].text == _TEXT_327
    assert excluded.get("duplicate_event") == 1


def test_select_diverse_excerpts_keeps_327_regardless_of_input_order() -> None:
    chosen_forward, _ = select_diverse_excerpts([_excerpt_327(), _excerpt_330()], NewsCollectionPolicy())
    chosen_backward, _ = select_diverse_excerpts([_excerpt_330(), _excerpt_327()], NewsCollectionPolicy())

    assert chosen_forward[0].text == _TEXT_327
    assert chosen_backward[0].text == _TEXT_327


# ══════════════════════════════════════════════════════════
# ② 거절된 공격 두 건 — 이번 구현에서는 반드시 닫혀야 한다
# ══════════════════════════════════════════════════════════

def test_negation_flip_attack_is_rejected() -> None:
    """"319대 감소했다" → "319대 감소하지 않았다" — 이전(거절된) 구현은 이걸
    같은 사건으로 잘못 합쳤다(root-rejection-probe.json 참고). 문장 키가
    통째로 달라지므로 이번 구현은 반드시 별개로 닫는다."""
    negated_text = _TEXT_330.replace("지난해 말보다 319대 감소했다.", "지난해 말보다 319대 감소하지 않았다.")
    negated_excerpt = _excerpt(_excerpt_330().candidate, negated_text)

    assert not same_event(_excerpt_327(), negated_excerpt)


def test_different_company_subject_attack_is_rejected() -> None:
    """"우리은행" → "기업은행" — 이전(거절된) 구현은 이것도 같은 사건으로 잘못
    합쳤다. 대상명이 문장 안에 있으므로 문장 키가 달라져 이번 구현은 닫는다."""
    wrong_company_text = _TEXT_330.replace("우리은행", "기업은행")
    wrong_company_excerpt = _excerpt(_excerpt_330().candidate, wrong_company_text)

    assert not same_event(_excerpt_327(), wrong_company_excerpt)


# ══════════════════════════════════════════════════════════
# ③ 보완 정보 보존 — #326·#329는 절대 합치지 않는다
# ══════════════════════════════════════════════════════════

def test_326_and_329_stay_separate_because_amounts_are_complementary_not_duplicate() -> None:
    """326의 기업당 300억원 한도와 329의 총 5480억원은 서로 다른 사실이다.
    발행처(view.asiae.co.kr vs etnews.com)도 다르므로 새 경로 자체를 타지
    않고 곧바로 별개로 남는다."""
    excerpt_326 = _excerpt(
        _candidate("326", "https://view.asiae.co.kr/article/2026090914300390568", publisher="view.asiae.co.kr"),
        _TEXT_326, topic=_TOPIC_BUSINESS, event_key="rl300",
    )
    excerpt_329 = _excerpt(
        _candidate("329", "https://www.etnews.com/20260909000037", publisher="etnews.com"),
        _TEXT_329, topic=_TOPIC_BUSINESS, event_key="rl300",
    )

    assert not same_event(excerpt_326, excerpt_329)

    chosen, excluded = select_diverse_excerpts([excerpt_326, excerpt_329], NewsCollectionPolicy())
    assert {item.text for item in chosen} == {_TEXT_326, _TEXT_329}
    assert excluded.get("duplicate_event", 0) == 0


# ══════════════════════════════════════════════════════════
# ④ 신원 불일치 공격 — 문장이 아무리 잘 대응해도 신원이 다르면 닫는다
# ══════════════════════════════════════════════════════════

def test_different_publisher_is_not_merged() -> None:
    other_publisher_excerpt = _excerpt(
        _candidate("330-other", "https://www.hankyung.com/article/2026090947061", publisher="other-press.com"),
        _TEXT_330,
    )
    assert not same_event(_excerpt_327(), other_publisher_excerpt)


def test_different_published_date_is_not_merged() -> None:
    """발행일 창(365일)이 아니라 «정확히 같은 날»을 요구한다 — 하루만 달라도 닫는다."""
    other_date_excerpt = _excerpt(
        _candidate("330-other-date", "https://www.hankyung.com/article/2026090947061", published_on="2026-09-08"),
        _TEXT_330,
    )
    assert not same_event(_excerpt_327(), other_date_excerpt)


def test_different_topic_is_not_merged() -> None:
    other_topic_excerpt = _excerpt(_excerpt_330().candidate, _TEXT_330, topic=_TOPIC_BUSINESS)
    assert not same_event(_excerpt_327(), other_topic_excerpt)


def test_different_unit_is_not_merged() -> None:
    """같은 숫자라도 단위가 다르면(대→원) 문장 자체가 달라져 대응에 실패한다."""
    wrong_unit_text = _TEXT_330.replace("1만7647대였다", "1만7647원였다")
    wrong_unit_excerpt = _excerpt(_excerpt_330().candidate, wrong_unit_text)

    assert not same_event(_excerpt_327(), wrong_unit_excerpt)


def test_plan_tense_is_not_merged() -> None:
    """확정 사실("~였다.")과 계획("~일 계획이다.")은 허용 종결형 목록에 없으므로 닫는다."""
    plan_text = _TEXT_330.replace("1만7647대였다.", "1만7647대일 계획이다.")
    plan_excerpt = _excerpt(_excerpt_330().candidate, plan_text)

    assert not same_event(_excerpt_327(), plan_excerpt)


def test_both_sides_having_a_unique_sentence_is_not_merged() -> None:
    """양쪽 다 상대에 없는 고유 문장이 있으면(순수 부분 인용이 아니면) 둘 다 보존한다."""
    both_unique_text = _TEXT_330 + " 이 통계는 하반기에 다시 집계될 예정이다."
    both_unique_excerpt = _excerpt(_excerpt_330().candidate, both_unique_text)

    assert not same_event(_excerpt_327(), both_unique_excerpt)


def test_same_numbers_in_different_order_is_not_merged() -> None:
    """기존(HEAD) 배열 비교는 순서까지 같아야 한다 — 부분 인용 경로도 이를 우회하지 않는다."""
    reordered_text = "319대 감소했다. 지난해 말보다 1만7647대로 집계됐다."
    reordered_excerpt = _excerpt(
        _candidate("reorder", "https://www.hankyung.com/article/reorder"), reordered_text,
    )
    assert not same_event(reordered_excerpt, _excerpt_330())


# ══════════════════════════════════════════════════════════
# ⑤ 기존 동작 회귀 — 숫자 배열이 완전히 같은 경로는 HEAD 그대로다
# ══════════════════════════════════════════════════════════

def test_identical_text_is_still_recognized_as_the_same_event() -> None:
    excerpt_a = _excerpt_327()
    excerpt_b = _excerpt(
        _candidate("327-copy", "https://www.hankyung.com/article/202609092486i-copy"), _TEXT_327,
    )
    assert same_event(excerpt_a, excerpt_b)


def test_different_numbers_with_no_sentence_correspondence_is_not_merged() -> None:
    """숫자 배열이 다르고 문장도 대응하지 않으면 곧바로 닫는다(기존과 같은 결과)."""
    excerpt_a = _excerpt(_candidate("a", "https://example.com/a"), "매출액은 2025년 100억원을 기록했다.")
    excerpt_b = _excerpt(_candidate("b", "https://example.com/b"), "영업이익은 2025년 55억원을 기록했다.")
    assert not same_event(excerpt_a, excerpt_b)


# ══════════════════════════════════════════════════════════
# ⑥ 종결형 정규화의 자리 — «숫자 + 단위» 뒤에서만 쓴다
# ══════════════════════════════════════════════════════════

def test_non_numeric_word_ending_in_the_unit_letter_is_preserved() -> None:
    """단위가 아니라 낱말의 끝글자가 '대'인 문장은 종결형을 바꾸지 않는다.

    '임대로 집계됐다.'·'반대로 집계됐다.'는 숫자 뒤 단위가 아니라 낱말
    '임대'·'반대'의 일부다. 접미사만 보고 '임대였다.'·'반대였다.'로 바꾸면
    서로 다른 두 문장이 같은 키가 되어 다른 사건이 하나로 합쳐진다.
    """
    for word in ("임대", "반대"):
        long_text = f"이번 분기 거래유형은 {word}로 집계됐다. 지난해 말보다 319건 늘었다."
        short_text = f"이번 분기 거래유형은 {word}였다."
        long_excerpt = _excerpt(_candidate(f"{word}-long", f"https://example.com/{word}/long"), long_text)
        short_excerpt = _excerpt(_candidate(f"{word}-short", f"https://example.com/{word}/short"), short_text)

        assert not same_event(long_excerpt, short_excerpt), word


def test_comma_separated_number_before_the_unit_still_normalizes_the_ending() -> None:
    """천단위 구분자가 붙은 숫자 뒤의 '대로 집계됐다.'는 그대로 표준형과 같다.

    실제 두 기사(#327·#330)는 '1만7647대' 모양이라, 숫자 판별 조건이 '17,647대'
    같은 평범한 표기까지 막지 않는지 별도로 확인한다.
    """
    long_text = "보유 대수는 17,647대로 집계됐다. 지난해 말보다 319대 감소했다."
    short_text = "보유 대수는 17,647대였다."
    long_excerpt = _excerpt(_candidate("comma-long", "https://example.com/comma/long"), long_text)
    short_excerpt = _excerpt(_candidate("comma-short", "https://example.com/comma/short"), short_text)

    assert same_event(long_excerpt, short_excerpt)
