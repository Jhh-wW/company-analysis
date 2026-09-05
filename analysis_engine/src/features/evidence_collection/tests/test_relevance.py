"""장별 적합도 결정론 채점 시험 — 서로 다른 장에 서로 다른 조각을 주는지."""

from __future__ import annotations

import pytest

from features.evidence_collection import constants as c
from features.evidence_collection.relevance import (
    score_fragment_slots,
    score_fragment_slots_with_signal,
    score_fragment_text,
)
from features.evidence_collection.segment import segment_document
from features.evidence_collection.tests.fixtures.synthetic_documents import (
    BANK_BUSINESS_REPORT_TEXT,
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
    """수집기 슬롯 우선 규칙 — 순수 점수가 같으면 COLLECTOR_SLOT_IDS가 우선한다.

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


def test_당사는_정체성_근거가_아니며_복수_사업신호가_선언순서에_납치되지_않는다() -> None:
    scores = score_fragment_slots(
        "당사는 주요 고객사에 서비스를 제공한다. 구독 수수료를 받습니다.",
        section_heading="II. 사업의 내용",
    )

    assert {score.section_id for score in scores} == {"business_model"}
    assert {
        "business_model:revenue_model",
        "business_model:customer_type",
        "business_model:value_exchange",
    } <= {score.slot_id for score in scores}
    assert "identity:corporate_identity" not in {
        score.slot_id for score in scores
    }


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


def test_소유범위밖_신호는_근거가_아니지만_분류실패로도_세지_않는다() -> None:
    scores, has_any_direct_signal = score_fragment_slots_with_signal(
        "대표 제품은 정밀 센서이며 핵심 제품의 매출 비중을 관리한다.",
        section_heading="II. 주요 제품",
        allowed_slot_ids=frozenset(c.COLLECTOR_SLOTS_BY_SECTION["past_changes"]),
    )

    assert scores == ()
    assert has_any_direct_signal is True


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
    """historical_performance·비교 4종은 구조화 검증기가 채운다 — 텍스트 채점이
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


def _slot_ids_by_section(text: str) -> dict[str, set[str]]:
    """전문을 분할·채점해 장별로 실제 채워진 슬롯 id를 모은다."""

    filled: dict[str, set[str]] = {}
    for candidate in segment_document(text):
        for score in score_fragment_slots(candidate.text, candidate.section_heading):
            filled.setdefault(score.section_id, set()).add(score.slot_id)
    return filled


def test_은행형_공시도_portfolio_필수칸을_채운다() -> None:
    """「제품」이라는 말이 없어도 「주요 상품」·「부문별 영업실적」으로 채워야 한다.

    실측 결함 회귀 — 제조업 어휘만 있던 시절 우리은행 2025 사업보고서는
    portfolio 두 칸이 모두 0건이라 9장 게이트가 여기서 막혔다.
    """
    assert "제품" not in BANK_BUSINESS_REPORT_TEXT

    filled = _slot_ids_by_section(BANK_BUSINESS_REPORT_TEXT)

    assert set(c.COLLECTOR_SLOTS_BY_SECTION["portfolio"]) <= filled.get("portfolio", set())


def test_은행형_공시도_culture_필수칸을_채운다() -> None:
    """「핵심가치」·「행동강령」이 없어도 「윤리강령」·「조직문화」로 채워야 한다."""

    assert "핵심가치" not in BANK_BUSINESS_REPORT_TEXT
    assert "행동강령" not in BANK_BUSINESS_REPORT_TEXT

    filled = _slot_ids_by_section(BANK_BUSINESS_REPORT_TEXT)

    assert set(c.COLLECTOR_SLOTS_BY_SECTION["culture"]) <= filled.get("culture", set())


def test_금융업_어휘를_넣어도_제조업_공시의_기존_배정은_그대로다() -> None:
    """새 어휘가 기존 장을 빼앗지 않는지 — 제조업 원문의 필수 칸 전수 확인.

    portfolio·culture만 보지 않고 수집기 필수 슬롯을 모두 세는 이유: 금융업
    표현이 다른 장의 문단을 납치하면 그쪽 칸이 조용히 비기 때문이다.
    """
    filled = _slot_ids_by_section(LISTED_BUSINESS_REPORT_TEXT)

    for section_id, required_slot_ids in c.COLLECTOR_SLOTS_BY_SECTION.items():
        if section_id == "past_changes":
            # historical_performance는 구조화 실적기 몫이라 원문 채점 대상이 아니다.
            continue
        assert set(required_slot_ids) <= filled.get(section_id, set()), section_id


def test_제조업_제품_문단은_여전히_portfolio_제품역할로_간다() -> None:
    scores = score_fragment_slots(
        "핵심 제품은 메모리 모듈이며, 매출 기여가 가장 큰 주력 제품이다.",
        section_heading="2. 주요 제품 및 서비스",
    )

    slot_ids = {score.slot_id for score in scores}
    assert "portfolio:product_role" in slot_ids
    assert "portfolio:revenue_link" in slot_ids


def test_은행_위험관리_문단은_금융업_어휘로_portfolio에_새지_않는다() -> None:
    """「내부통제」·「준법」·「여신」은 일부러 안 넣은 어휘다 — 그 사실을 고정한다.

    이 세 단어는 은행 공시의 위험관리·약관 문단 어디에나 나와서, 키워드로
    넣으면 current_challenges 문단 수십 건을 portfolio·culture로 끌고 간다(실측).
    """
    scores = score_fragment_slots(
        (
            "내부통제 기준에 따라 준법감시 조직을 두고 여신 심사 과정의 위험에 "
            "대응하는 대책을 시행했다."
        ),
        section_heading="IV. 이사의 경영진단 및 분석의견",
    )

    assert scores
    assert {score.section_id for score in scores} == {"current_challenges"}


def test_은행형_공시의_회사개요_문단은_portfolio에_납치되지_않는다() -> None:
    """「은행업」을 portfolio 어휘로 넣지 않았다는 사실을 고정한다(정체성 문단 보호)."""

    scores = score_fragment_slots(
        "당사는 은행법에 따라 설립된 주식회사이며, 정관상 목적사업은 은행업으로 등록되어 있다.",
        section_heading="I. 회사의 개요",
    )

    assert scores
    assert {score.section_id for score in scores} == {"identity"}


def test_은행형_전문도_한_문단은_한_장_경계_안에서만_배정된다() -> None:
    for candidate in segment_document(BANK_BUSINESS_REPORT_TEXT):
        scores = score_fragment_slots(candidate.text, candidate.section_heading)
        assert len({score.section_id for score in scores}) <= 1


#: 금융업 어휘 하나하나가 «혼자서» 지정한 칸을 채우는지 — 어휘별 회귀 고정.
#: 전문 fixture 시험은 한 문단에 신호가 여러 개라, 어휘 하나를 빼도 옆
#: 어휘가 대신 걸려 초록불이 유지된다(음성 대조에서 실제로 확인). 그래서
#: 어휘마다 신호가 하나뿐인 최소 문장을 따로 둔다.
_금융업_어휘_최소문장 = (
    ("주요 상품은 예금과 기업 대출이다.", "portfolio:product_role"),
    ("기업금융을 핵심 사업 부문으로 삼고 있다.", "portfolio:product_role"),
    ("부문별 영업실적은 이자부문이 가장 컸다.", "portfolio:revenue_link"),
    ("이자이익은 전년보다 늘었다.", "portfolio:revenue_link"),
    ("임직원 윤리강령을 제정했다.", "culture:work_principle"),
    ("준법을 중시하는 조직문화를 정착시켰다.", "culture:work_principle"),
)


@pytest.mark.parametrize(("text", "expected_slot_id"), _금융업_어휘_최소문장)
def test_금융업_어휘는_하나만_있어도_지정한_칸을_채운다(
    text: str, expected_slot_id: str,
) -> None:
    scores = score_fragment_slots(text)

    assert {score.slot_id for score in scores} == {expected_slot_id}


def test_금융업_표제_상품_및_서비스도_portfolio_가산점을_받는다() -> None:
    """DART 은행 서식은 「제품 및 서비스」 자리에 「상품 및 서비스」를 쓴다.

    표제 힌트는 새 신호를 만들지 않고 이미 맞은 슬롯의 점수만 올리므로,
    같은 문장을 표제 유무로만 비교해 가산점이 실제로 붙는지 확인한다.
    """
    text = "주요 상품은 예금과 기업 대출이다."
    without_hint = score_fragment_text(text)
    with_hint = score_fragment_text(text, section_heading="다. 주요 상품 및 서비스의 내용")

    assert without_hint is not None and with_hint is not None
    assert with_hint.score_millis > without_hint.score_millis
    assert "heading_match:portfolio" in with_hint.reason_codes
    assert not any(code.startswith("heading_match:") for code in without_hint.reason_codes)
