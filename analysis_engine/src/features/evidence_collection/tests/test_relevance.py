"""장별 적합도 결정론 채점 시험 — 서로 다른 장에 서로 다른 조각을 주는지."""

from __future__ import annotations

from features.evidence_collection import constants as c
from features.evidence_collection.relevance import score_fragment_slots, score_fragment_text
from features.evidence_collection.segment import segment_document
from features.evidence_collection.tests.fixtures.synthetic_documents import (
    LISTED_BUSINESS_REPORT_TEXT,
)


def test_신호가_없으면_None을_돌려준다() -> None:
    assert score_fragment_text("아무 신호도 없는 평범한 문장입니다.") is None


def test_매출_키워드가_있으면_business_model로_채점된다() -> None:
    score = score_fragment_text("당사의 매출은 주요 고객사에 대한 판매에서 발생한다.")
    assert score is not None
    assert score.section_id == "business_model"
    assert score.slot_id in c.CLAIM_SLOTS_BY_SECTION["business_model"]


def test_score_millis는_0_1000_범위다() -> None:
    score = score_fragment_text("매출 수익 판매 과금 수수료가 모두 걸리는 문장이다.")
    assert score is not None
    assert 0 <= score.score_millis <= 1000


def test_reason_codes는_형식_규정을_지킨다() -> None:
    score = score_fragment_text("경영진은 리더십을 발휘한다.", section_heading="I. 임원 및 직원")
    assert score is not None
    for code in score.reason_codes:
        assert c.REASON_CODE_PATTERN.fullmatch(code)


def test_제목_힌트가_맞으면_가산점을_받는다() -> None:
    without_hint = score_fragment_text("당사의 매출은 판매에서 발생한다.")
    with_hint = score_fragment_text(
        "당사의 매출은 판매에서 발생한다.", section_heading="II. 사업의 내용",
    )
    assert without_hint is not None and with_hint is not None
    assert with_hint.score_millis >= without_hint.score_millis
    assert any(code.startswith("heading_match:") for code in with_hint.reason_codes)


def test_동점이면_수집기_1차_표적_슬롯이_이긴다() -> None:
    """team-lead 규칙 1 — 순수 점수가 같으면 COLLECTOR_SLOT_IDS가 우선한다.

    「해외」(business_model:regional_mix, 보조)와 「제공한다」
    (business_model:value_exchange, 수집기 1차 표적)가 똑같이 1건씩 걸려
    동점(250)이 되는 문장. SLOT_KEYWORDS 선언 순서는 regional_mix가
    value_exchange보다 앞서므로, 순서만으로 고르면 regional_mix가 이겨야
    하지만 수집기 우선 규칙이 이를 뒤집어야 한다.
    """
    score = score_fragment_text("매출은 해외에서 발생하며 서비스를 제공한다.")
    assert score is not None
    assert score.slot_id == "business_model:value_exchange"
    assert score.slot_id in c.COLLECTOR_SLOT_IDS


def test_한_문단의_서로_다른_직접주장은_각_슬롯에_배정된다() -> None:
    """한 근거를 모든 장에 복사하지 않고, 글자상 직접 확인되는 세 칸만 낸다."""

    scores = score_fragment_slots(
        "주요 매출은 제품 판매에서 발생하며 고객사에 서비스를 제공한다.",
        section_heading="II. 사업의 내용",
    )

    slot_ids = {score.slot_id for score in scores}
    assert {
        "business_model:revenue_model",
        "business_model:customer_type",
        "business_model:value_exchange",
    } <= slot_ids
    assert all(score.section_id == "business_model" for score in scores)
    assert "culture:verified_case" not in slot_ids


def test_한_문단은_약한_우연일치로_여러_장에_복제되지_않는다() -> None:
    scores = score_fragment_slots(
        (
            "최근 원자재 가격 상승이라는 위험에 대응해 공급업체 다변화 대책을 "
            "시행했으며, 다음 점검은 2025년 하반기로 계획했다."
        ),
        section_heading="IV. 이사의 경영진단 및 분석의견",
    )

    assert scores
    assert {score.section_id for score in scores} == {"current_challenges"}


def test_실제형_공시의_각_문단도_한_장_경계_안에서만_배정된다() -> None:
    for candidate in segment_document(LISTED_BUSINESS_REPORT_TEXT):
        scores = score_fragment_slots(candidate.text, candidate.section_heading)
        assert len({score.section_id for score in scores}) <= 1


def test_강한_보조_신호는_약한_수집기_신호에_밀리지_않는다() -> None:
    """수집기 슬롯이 «항상» 이기면 안 된다 — 순수 점수가 더 센 보조 슬롯이
    이겨야 한다(실측 회귀 — 이전 버전은 실적 문단이 우연히 스친 「고객사」
    한 단어 때문에 business_model:customer_type으로 잘못 배정됐었다).
    """
    text = (
        "최근 3개년 매출액은 2023년 1,000억원, 2024년 1,200억원, "
        "2025년 1,500억원으로 매년 증가했다.\n"
        "이 증가는 신규 고객사 확보가 배경이다."
    )
    score = score_fragment_text(text, section_heading="1. 요약재무정보")
    assert score is not None
    assert score.slot_id == "past_changes:cumulative_change"
    assert score.slot_id not in c.COLLECTOR_SLOT_IDS


def test_다른_엔진_소유_슬롯은_어떤_입력에도_생성되지_않는다() -> None:
    """historical_performance·비교 4종은 Codex가 채운다 — 텍스트 채점이
    옛 키워드(매출액·영업이익·동종업계·점유율 등)를 봐도 이 슬롯들을
    돌려주면 안 된다(SLOT_KEYWORDS에서 아예 뺐는지 확인하는 회귀 시험).
    """
    excluded_slot_ids = {
        "past_changes:historical_performance",
        "competitive_position:comparison_target",
        "competitive_position:comparison_metric",
        "competitive_position:comparison_basis",
        "competitive_position:comparison_judgment",
        "competitive_position:limitation",
    }
    probe_texts = (
        "최근 3개년 매출액과 영업이익, 당기순이익은 아래와 같다.",
        "동종업계 경쟁사 대비 업계는 과점 구조다.",
        "점유율과 순위, 규모는 공시 기준 근거로 확인된다.",
        "이 경쟁력과 강점, 우위는 확인되지 않는다, 한계와 제약이 있다.",
    )
    for text in probe_texts:
        score = score_fragment_text(text)
        if score is not None:
            assert score.slot_id not in excluded_slot_ids


def test_실제_문서를_분할해서_채점하면_서로_다른_장에_서로_다른_조각이_배정된다() -> None:
    """전체 원문을 통째로 복사하는 게 아니라 장마다 다른 문단이 배정되는지."""
    candidates = segment_document(LISTED_BUSINESS_REPORT_TEXT)
    scored = [
        (candidate.text, score_fragment_text(candidate.text, candidate.section_heading))
        for candidate in candidates
    ]
    assigned = [(text, score.section_id) for text, score in scored if score is not None]

    section_ids = {section_id for _text, section_id in assigned}
    # 적어도 서로 다른 장 3개 이상에 조각이 갈렸는지 — «전체 원문 복사» 금지 요구사항.
    assert len(section_ids) >= 3

    texts_by_section: dict[str, set[str]] = {}
    for text, section_id in assigned:
        texts_by_section.setdefault(section_id, set()).add(text)
    # 어떤 두 장도 완전히 같은 조각 집합을 받지 않는다(원문 전체 복사가 아니라는 증거).
    seen: list[set[str]] = []
    for texts in texts_by_section.values():
        assert texts not in seen
        seen.append(texts)
