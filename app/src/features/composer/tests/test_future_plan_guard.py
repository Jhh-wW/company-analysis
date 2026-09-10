"""6장 성장 계획 표의 미래 근거 결속을 «실측 원문»과 적대 반례로 검증한다.

★ 여기 쓰인 원문은 실행 d86b56f374403038d958c6f41fcde2fd(SM)·
  8022de861adf3b01f1a27570a74d7a26(WOORI)에서 그 줄이 실제로 인용한 조각의
  문장을 그대로 옮긴 것이다. 요약·정규화하지 않았다.
★ 광고 [60] 도 실측 전문이다. `resume-20260910-SM-future-exact-sources/sources.json`
  에 590자 원문이 그대로 있고 `exact_sha256` 까지 일치한다 — 아래 시험이 그 해시를
  매번 다시 확인한다. (앞선 초안이 「전문 미보존이라 재구성했다」고 적었던 것은
  사실이 아니었고 ROOT 지적으로 정정했다.)
"""

from hashlib import sha256

import pytest

from src.features.composer.future_plan_constants import (
    FUTURE_ACTIVITY_NOT_IN_QUOTE,
    FUTURE_CANDIDATE_STATES_CURRENT,
    FUTURE_EVIDENCE_MISSING,
    FUTURE_FIELD_TYPE_INVALID,
    FUTURE_KEY,
    FUTURE_MODE_INVALID,
    FUTURE_MODE_KEY,
    FUTURE_MODE_MISDECLARED,
    FUTURE_MODE_PLAN,
    FUTURE_OUTLOOK_HARDENED,
    FUTURE_POLARITY_FLIPPED,
    FUTURE_QUOTE_KEY,
    FUTURE_QUOTE_NOT_IN_SOURCE,
    FUTURE_QUOTE_TOO_SHORT,
    FUTURE_REASON_CODES,
    FUTURE_REASON_ITEM,
    FUTURE_SLOTS_DEGENERATE,
    FUTURE_SLOTS_SPLIT_ACROSS_CELLS,
    FUTURE_SOURCE_ID_EMPTY,
    FUTURE_SOURCE_KEY,
    FUTURE_SOURCE_NOT_CITED,
    FUTURE_SOURCE_STATES_CURRENT,
    FUTURE_SUBJECT_MISMATCH,
    FUTURE_ACTIVITY_KEY,
    FUTURE_TARGET_GENERIC,
    FUTURE_TARGET_KEY,
    FUTURE_TARGET_NOT_BOUND,
    FUTURE_TARGET_NOT_IN_CANDIDATE,
    FUTURE_TARGET_NOT_IN_QUOTE,
    FUTURE_TARGET_TOO_SHORT,
)
from src.features.composer.future_plan_guard import future_plan_problem
from src.shared.report_quality.review_diagnostic_constants import REVIEW_SCOPE_ITEMS

# ══════════════════════════════════════════════════════════
# 실측 원문 (그대로)
# ══════════════════════════════════════════════════════════
WOORI_NEW_BUSINESS = (
    "신규사업 등의 내용 및 전망 우리은행은 핵심사업 확장, 미래금융 가속, "
    "고객신뢰 확립을 경영목표로 하여 고객만족을 최우선으로 하는 미래 성장동력을 "
    "발굴할 계획입니다. 특히, 세분화된 고객 세그먼트별 맞춤형 마케팅을 확대하고, "
    "금융 취약층을 지원하는 상생금융을 적극 추진하여 금융의 사회적 책임을 "
    "다하고자 합니다. 또한, 통신업, 부동산 등 이종산업과의 제휴를 통해 新수익원을 "
    "발굴하고, 디지털 금융환경에 대응한 고객 맞춤형 종합금융솔루션을 제공하고자 "
    "합니다. 또한, 당행은 대외시스템과의 연계를 기반으로 기업상품 취급에 필요한 "
    "데이터를 수집, 관리하는 자체개발 B2B 전용 MP(Market Place) 서비스인 "
    "「원비즈 e-MP 서비스」를 2025년 6월 출시하였습니다."
)
SM_TRAVEL = (
    "이러한 Buying Power를 바탕으로 K-Pop 글로벌 팬들이 공연을 관람하고 한국 "
    "문화를 향유하는 'K컬처 여행 상품'을 런칭하여 매출 증대가 이뤄지고 있습니다."
)
SM_GLOBAL = (
    "특히, 북미·동남아 등 해외 시장에서의 현지화 전략과 콘텐츠 유통 파트너십을 "
    "바탕으로 글로벌 영향력을 확대하고 있으며, 기술 융합 콘텐츠, 디지털 플랫폼 "
    "기반의 새로운 소비 경험 제공을 통해 변화하는 산업 환경에 선제적으로 "
    "대응하고 있습니다."
)
SM_WORLDVIEW = (
    "당사는 독창적 제작 시스템을 통해 세계적인 K-Pop 아티스트를 배출해왔으며, "
    "Virtual IP 확장 등 첨단 기술 기반의 엔터테인먼트 세계관을 통해 글로벌 "
    "콘텐츠 산업을 선도하고 있습니다."
)
#: SM 광고 [60] 실측 전문(590자). 원문 그대로이며 해시로 고정한다.
#: ⚠️ 「창출까지가능할」의 붙어 쓴 자리는 «원문 그대로»다 — 고치지 마라.
SM_AD_EXPECTATION = (
    "종속회사 에스엠컬처앤콘텐츠의 광고사업부문은 SK플래닛(주)에서 물적 분할된 "
    "M&C(마케팅앤컴퍼니) 부문을 인수함으로써 2017년 말에 신설된 부문입니다. "
    "광고사업부문은 국내 Top-tier 광고회사이자 SK그룹 인하우스 대행사로서 축적해 온 "
    "다양한 업종의 마케팅 커뮤니케이션 경험과 노하우, 선진화된 업무 프로세스와 "
    "광범위한 네트워크를 바탕으로 안정적인 영업 환경 및 고객 구성을 유지하고 "
    "있습니다.국내 광고시장의 침체 및 디지털시장으로의 급격한 변화 속에서 "
    "종합광고회사의 주력시장인 4대 매체 중심의 전통광고 시장은 최근 역성장 추세를 "
    "보이는 가운데 에스엠컬처앤콘텐츠의 광고사업부문은 당사 및 타 사업부문과의 "
    "시너지를 통해 새로운 성장 전략을 추진하고 있습니다.광고사업부문은 대한민국 "
    "대표 기업들과 함께 성공 캠페인을 만들어 온 광고 제작 능력, 당사의 콘텐츠파워, "
    "그리고 에스엠컬처앤콘텐츠가 쌓아온 빅데이터를 결합하여 파급력 있는 "
    "CreativeContents를 만드는 광고 대행사입니다. 이처럼 에스엠컬처앤콘텐츠의 "
    "차별화된 마케팅 커뮤니케이션 서비스는 침체된 기존 종합광고회사 시장의 틀을 "
    "넘어서 새로운 시장 창출까지가능할 것으로 기대하고 있습니다."
)
SM_AD_EXPECTATION_SHA256 = "2029a47f9ef4ad62abd0845ab75ddd1ad642be937ef1ea1da2c0c5a04a99a516"
#: 그 전문 안의 «전망» 문장. 원문의 연속 부분문자열이다.
SM_AD_QUOTE = (
    "이처럼 에스엠컬처앤콘텐츠의 차별화된 마케팅 커뮤니케이션 서비스는 침체된 "
    "기존 종합광고회사 시장의 틀을 넘어서 새로운 시장 창출까지가능할 것으로 "
    "기대하고 있습니다."
)


def test_ad_source_matches_the_measured_sha256():
    """★ 「실측」이라고 적은 것이 실제로 실측인지 시험이 매번 확인한다."""

    assert sha256(SM_AD_EXPECTATION.encode()).hexdigest() == SM_AD_EXPECTATION_SHA256
    assert SM_AD_QUOTE in SM_AD_EXPECTATION


def evidence(source="f1", target="", activity="", quote="", mode=FUTURE_MODE_PLAN):
    """미래근거 항목 하나를 담은 검증근거 객체.

    ★ 인자 이름은 영문이고, 실제 계약 키(근거·대상·활동·원문·양태)는 상수로 쓴다 —
      계약이 바뀌면 상수 한 곳만 고치면 된다.
    """

    return {FUTURE_KEY: [{
        FUTURE_SOURCE_KEY: source,
        FUTURE_TARGET_KEY: target,
        FUTURE_ACTIVITY_KEY: activity,
        FUTURE_QUOTE_KEY: quote,
        FUTURE_MODE_KEY: mode,
    }]}


# ══════════════════════════════════════════════════════════
# ① 실측 «보존» 사례 — 진짜 미래 계획은 그대로 남는다
# ══════════════════════════════════════════════════════════
def test_measured_bank_tailored_marketing_expansion_is_kept_as_a_plan():
    cells = (
        "고객 세그먼트별 맞춤형 마케팅 확대",
        "",
        "세분화된 고객 세그먼트별 맞춤형 마케팅을 확대하고 상생금융을 적극 추진",
    )
    assert future_plan_problem(
        cells,
        {"f1": WOORI_NEW_BUSINESS},
        evidence(
            target="맞춤형 마케팅",
            activity="확대",
            quote="특히, 세분화된 고객 세그먼트별 맞춤형 마케팅을 확대하고, 금융 "
            "취약층을 지원하는 상생금융을 적극 추진하여 금융의 사회적 책임을 "
            "다하고자 합니다.",
        ),
    ) == ""


def test_measured_bank_cross_industry_partnership_is_kept_as_a_plan():
    cells = (
        "이종산업 제휴를 통한 신수익원 발굴",
        "",
        "통신업, 부동산 등 이종산업과의 제휴를 통해 新수익원을 발굴",
    )
    assert future_plan_problem(
        cells,
        {"f1": WOORI_NEW_BUSINESS},
        evidence(
            target="이종산업",
            activity="제휴",
            quote="또한, 통신업, 부동산 등 이종산업과의 제휴를 통해 新수익원을 "
            "발굴하고, 디지털 금융환경에 대응한 고객 맞춤형 종합금융솔루션을 "
            "제공하고자 합니다.",
        ),
    ) == ""


def test_measured_partnership_is_proven_despite_an_inserted_particle():
    """★ 독립 대조 F2 가 보류로 남긴 자리다 (target_not_in_quote).

    행의 대상은 「이종산업 제휴」인데 원문은 「이종산업**과의** 제휴」다. 조사 하나가
    낀 정상 바꿔쓰기이므로 증명되어야 한다. 원문·행·기대값을 바꿔 통과시킨 것이
    아니라, 낱말 사이의 «닫힌 조사»만 허용하도록 대조 규칙을 좁게 넓혔다.
    ★ 이 행은 대상이 활동의 «수단»으로 붙은 꼴(「제휴를 통한 … 발굴」)이라
      수단 다리도 함께 필요하다.
    """

    cells = (
        "이종산업 제휴를 통한 신수익원 발굴",
        "",
        "통신업, 부동산 등 이종산업과의 제휴를 통해 新수익원을 발굴하고, 디지털 "
        "금융환경에 대응한 고객 맞춤형 종합금융솔루션을 제공하고자 합니다.",
    )
    assert future_plan_problem(
        cells,
        {"8": WOORI_NEW_BUSINESS},
        {FUTURE_KEY: [{
            "근거": "8",
            "대상": "이종산업 제휴",
            "활동": "발굴",
            "원문": "또한, 통신업, 부동산 등 이종산업과의 제휴를 통해 新수익원을 "
            "발굴하고, 디지털 금융환경에 대응한 고객 맞춤형 종합금융솔루션을 "
            "제공하고자 합니다.",
            "양태": "계획",
        }]},
    ) == ""


def test_a_means_target_recast_as_the_object_is_rejected():
    """★ 위 시험의 «오류 짝». 수단 다리를 허용해도 이 둔갑은 막아야 한다.

    원문은 「이종산업과의 제휴를 통해 新수익원을 발굴」이다 — 발굴되는 것은
    이종산업이 아니라 신수익원이다. 행이 「이종산업 발굴」이라고 적으면 거짓이다.
    후보 칸의 다리 종류(인접)가 원문의 다리 종류(수단)와 달라서 걸린다.
    """

    assert future_plan_problem(
        ("이종산업 발굴", "", ""),
        {"8": WOORI_NEW_BUSINESS},
        {FUTURE_KEY: [{
            "근거": "8",
            "대상": "이종산업",
            "활동": "발굴",
            "원문": "또한, 통신업, 부동산 등 이종산업과의 제휴를 통해 新수익원을 "
            "발굴하고, 디지털 금융환경에 대응한 고객 맞춤형 종합금융솔루션을 "
            "제공하고자 합니다.",
            "양태": "계획",
        }]},
    ) == FUTURE_TARGET_NOT_BOUND


# ══════════════════════════════════════════════════════════
# ② 실측 «실패» 사례 — 현재·완료가 미래 계획으로 올라오지 못한다
# ══════════════════════════════════════════════════════════
def test_measured_travel_product_already_launched_is_not_a_plan():
    cells = (
        "여행 상품 확대",
        "",
        "K-Pop 글로벌 팬 대상 여행 상품 런칭을 통한 IP 기반 수익화 채널 개척",
    )
    assert future_plan_problem(
        cells,
        {"f1": SM_TRAVEL},
        evidence(
            target="여행 상품",
            activity="런칭",
            quote="'K컬처 여행 상품'을 런칭하여 매출 증대가 이뤄지고 있습니다.",
        ),
    ) == FUTURE_SOURCE_STATES_CURRENT


def test_measured_global_reach_stated_as_ongoing_is_not_a_plan():
    cells = (
        "글로벌 시장 진출 강화",
        "",
        "북미·동남아 현지화 전략, 중화권 합작법인 STE 설립을 통한 지역별 영향력 확대",
    )
    assert future_plan_problem(
        cells,
        {"f1": SM_GLOBAL},
        evidence(
            target="영향력",
            activity="확대",
            quote="글로벌 영향력을 확대하고 있으며",
        ),
    ) == FUTURE_SOURCE_STATES_CURRENT


def test_measured_worldview_expansion_stated_as_ongoing_is_not_a_plan():
    cells = (
        "첨단 기술 기반 엔터테인먼트 세계관 확장",
        "",
        "Virtual IP 확장, 기술 융합 콘텐츠, 디지털 플랫폼 기반 새로운 소비 경험 제공",
    )
    assert future_plan_problem(
        cells,
        {"f1": SM_WORLDVIEW},
        evidence(
            target="Virtual IP",
            activity="확장",
            quote="Virtual IP 확장 등 첨단 기술 기반의 엔터테인먼트 세계관을 통해 "
            "글로벌 콘텐츠 산업을 선도하고 있습니다.",
        ),
    ) == FUTURE_SOURCE_STATES_CURRENT


# ══════════════════════════════════════════════════════════
# ③ 전망 — 확정 계획으로 굳히면 실패, 한정을 유지하면 보존
# ══════════════════════════════════════════════════════════
def test_measured_ad_outlook_declared_as_a_plan_fails():
    """★ 「모든 원문에 미래 표현이 없다」는 판정은 틀렸다 — 기대는 미래 표현이다.
    틀린 것은 그 기대를 «확정 계획»으로 바꾼 것이다."""

    assert future_plan_problem(
        ("새로운 시장 창출", "", ""),
        {"f1": SM_AD_EXPECTATION},
        evidence(
            target="새로운 시장",
            activity="창출",
            quote=SM_AD_QUOTE,
            mode="계획",
        ),
    ) == FUTURE_MODE_MISDECLARED


def test_measured_ad_outlook_loses_its_hedge_in_the_candidate_and_fails():
    assert future_plan_problem(
        ("새로운 시장 창출", "", ""),
        {"f1": SM_AD_EXPECTATION},
        evidence(
            target="새로운 시장",
            activity="창출",
            quote=SM_AD_QUOTE,
            mode="전망",
        ),
    ) == FUTURE_OUTLOOK_HARDENED


def test_measured_ad_row_keeping_its_outlook_hedge_is_kept():
    assert future_plan_problem(
        ("새로운 시장 창출 기대", "", ""),
        {"f1": SM_AD_EXPECTATION},
        evidence(
            target="새로운 시장",
            activity="창출",
            quote=SM_AD_QUOTE,
            mode="전망",
        ),
    ) == ""


# ══════════════════════════════════════════════════════════
# ④ 현재 A 운영 / 미래 B 확장 — 갈라서 판정한다
# ══════════════════════════════════════════════════════════
MIXED = "당사는 국내 물류센터를 운영하고 있으며, 해외 물류망을 확대할 계획입니다."


def test_current_operation_in_the_same_source_is_not_approved_as_a_plan():
    assert future_plan_problem(
        ("국내 물류센터 운영", "", ""),
        {"f1": MIXED},
        evidence(target="국내 물류센터", activity="운영", quote=MIXED),
    ) == FUTURE_SOURCE_STATES_CURRENT


def test_future_expansion_in_the_same_source_is_kept():
    assert future_plan_problem(
        ("해외 물류망 확대", "", ""),
        {"f1": MIXED},
        evidence(target="해외 물류망", activity="확대", quote=MIXED),
    ) == ""


def test_coordinated_clauses_share_the_trailing_plan_modality():
    """★ 의도적 판정이다. 「A를 정비하고 B를 개설할 계획입니다」는 한국어에서 두
    가지 «모두» 계획이라는 뜻이다. 이 분배를 막으면 실측 보존 사례(우리은행
    「마케팅을 확대하고 … 다하고자 합니다」)가 통째로 사라진다.
    ⚠️ 원문이 앞 활동을 «현재·완료로 표시»하면 위 시험처럼 그때 걸린다."""

    source = "당사는 기존 매장을 정비하고 신규 매장을 개설할 계획입니다."
    assert future_plan_problem(
        ("기존 매장 정비", "", ""),
        {"f1": source},
        evidence(target="기존 매장", activity="정비", quote=source),
    ) == ""


def test_a_completed_first_activity_cannot_borrow_the_later_plan():
    source = "당사는 기존 매장을 정비하였고 신규 매장을 개설할 계획입니다."
    assert future_plan_problem(
        ("기존 매장 정비", "", ""),
        {"f1": source},
        evidence(target="기존 매장", activity="정비", quote=source),
    ) == FUTURE_SOURCE_STATES_CURRENT


# ══════════════════════════════════════════════════════════
# ⑤ 근거 결속 자체 — 짜깁기·차용·형식
# ══════════════════════════════════════════════════════════
def test_a_quote_spliced_from_two_sources_is_rejected():
    sources = {
        "f1": "당사는 반도체 라인을 증설합니다.",
        "f2": "신소재 사업을 추진할 계획입니다.",
    }
    assert future_plan_problem(
        ("반도체 라인 증설", "", ""),
        sources,
        evidence(
            target="반도체 라인",
            activity="증설",
            quote="반도체 라인을 증설합니다. 신소재 사업을 추진할 계획입니다.",
        ),
    ) == FUTURE_QUOTE_NOT_IN_SOURCE


def test_a_source_this_row_did_not_cite_is_rejected():
    assert future_plan_problem(
        ("반도체 라인 증설", "", ""),
        {"f9": "당사는 반도체 라인을 증설할 계획입니다."},
        evidence(
            target="반도체 라인", activity="증설", quote="반도체 라인을 증설할 계획입니다."
        ),
    ) == FUTURE_SOURCE_NOT_CITED


def test_two_claims_each_proven_by_their_own_citation_are_kept():
    """★ 한 줄이 두 주장을 담고 각각 «다른» 자기 인용이 뒷받침하는 것은 정상이다.

    예전에는 「항목의 근거 id 가 모두 같아야 한다」로 이런 줄을 거절했는데, 그건
    요구가 아니었다(ROOT 정정). 짜깁기 금지는 «한 구절이 한 원문 안에 있어야 한다»는
    항목별 규칙이 그대로 지킨다 — 아래 시험이 그 짝이다.
    """

    sources = {
        "f1": "당사는 해외 매장을 확대할 계획입니다.",
        "f2": "당사는 신규 브랜드를 출시할 계획입니다.",
    }
    assert future_plan_problem(
        ("해외 매장 확대 및 신규 브랜드 출시", "", ""),
        sources,
        {FUTURE_KEY: [
            {"근거": "f1", "대상": "해외 매장", "활동": "확대",
             "원문": sources["f1"], "양태": "계획"},
            {"근거": "f2", "대상": "신규 브랜드", "활동": "출시",
             "원문": sources["f2"], "양태": "계획"},
        ]},
    ) == ""


def test_one_quote_spanning_two_sources_is_still_rejected():
    """★ 인용을 여럿 대는 것과 «한 구절을 여러 원문에서 짜깁는 것»은 다르다."""

    sources = {
        "f1": "당사는 해외 매장을 확대할 계획입니다.",
        "f2": "당사는 신규 브랜드를 출시할 계획입니다.",
    }
    assert future_plan_problem(
        ("해외 매장 확대", "", ""),
        sources,
        {FUTURE_KEY: [{"근거": "f1", "대상": "해외 매장", "활동": "확대",
                       "원문": sources["f1"] + " " + sources["f2"], "양태": "계획"}]},
    ) == FUTURE_QUOTE_NOT_IN_SOURCE


def test_another_subject_plan_in_the_same_sentence_cannot_be_borrowed():
    source = "경쟁사는 해외 공장을 증설할 계획이며, 당사는 국내 설비를 점검하고 있습니다."
    assert future_plan_problem(
        ("해외 공장 증설", "", ""),
        {"f1": source},
        evidence(target="해외 공장", activity="증설", quote=source),
    ) == FUTURE_SUBJECT_MISMATCH


def test_target_and_activity_taken_from_different_cells_are_rejected():
    source = "당사는 A 라인의 생산 능력을 확대할 계획입니다."
    assert future_plan_problem(
        ("생산 능력 강화", "", "A 라인의 확대"),
        {"f1": source},
        evidence(target="생산 능력", activity="확대", quote=source),
    ) == FUTURE_SLOTS_SPLIT_ACROSS_CELLS


def test_a_target_absent_from_the_quote_is_rejected():
    """후보 칸에는 있어도 인용 구절에 없으면 그 계획은 이 원문이 말한 것이 아니다."""

    source = "당사는 A 라인의 생산 능력을 확대할 계획입니다."
    assert future_plan_problem(
        ("물류 능력 확대", "", ""),
        {"f1": source},
        evidence(target="물류 능력", activity="확대", quote=source),
    ) == FUTURE_TARGET_NOT_IN_QUOTE


def test_a_target_not_bound_to_the_activity_is_rejected():
    source = "당사는 창고 자동화를 마무리하였고 물류망 신설을 검토할 계획입니다."
    assert future_plan_problem(
        ("창고 자동화 신설", "", ""),
        {"f1": source},
        evidence(target="창고 자동화", activity="신설", quote=source),
    ) in (FUTURE_TARGET_NOT_BOUND, FUTURE_SOURCE_STATES_CURRENT)


# ══════════════════════════════════════════════════════════
# ⑥ 좁은 표면 규칙 — 한 글자 대상·일반명사·겹친 슬롯을 막는다
# ══════════════════════════════════════════════════════════
GROWTH = "당사는 A 라인의 생산 능력을 확대할 계획입니다."


@pytest.mark.parametrize(
    "target, activity, expected",
    [
        ("A", "확대", FUTURE_TARGET_TOO_SHORT),
        ("사업", "확대", FUTURE_TARGET_GENERIC),
        ("생산 능력 확대", "확대", FUTURE_SLOTS_DEGENERATE),
        ("생산 능력", "확대", ""),
    ],
)
def test_minimum_requirements_for_target_and_activity_slots(target, activity, expected):
    assert future_plan_problem(
        ("생산 능력 확대", "", ""),
        {"f1": GROWTH},
        evidence(target=target, activity=activity, quote=GROWTH),
    ) == expected


def test_a_partial_match_ignoring_word_boundaries_is_not_accepted():
    """「능력」이 「생산 능력」의 일부로만 들어 있으면 대상으로 세지 않는다."""

    assert future_plan_problem(
        ("생산능력 확대", "", ""),
        {"f1": GROWTH},
        evidence(target="능력", activity="확대", quote=GROWTH),
    ) == FUTURE_TARGET_NOT_IN_CANDIDATE


def test_hada_verb_forms_count_as_the_same_activity():
    """★ 「확대」와 「확대하고」는 같은 활동이다. 조사만 벗기면 정상 줄이 전부
    증명 실패로 빠진다."""

    source = "당사는 해외 매장을 확대하고자 합니다."
    assert future_plan_problem(
        ("해외 매장 확대", "", ""),
        {"f1": source},
        evidence(target="해외 매장", activity="확대", quote=source),
    ) == ""


def test_an_activity_that_only_prefixes_another_word_is_not_the_same():
    source = "당사는 규정을 확대해석하지 않기로 하였습니다."
    assert future_plan_problem(
        ("규정 확대", "", ""),
        {"f1": source},
        evidence(target="규정", activity="확대", quote=source),
    ) == FUTURE_ACTIVITY_NOT_IN_QUOTE


# ══════════════════════════════════════════════════════════
# ⑦ 극성 — 부정·축소 계획을 뒤집지 못한다
# ══════════════════════════════════════════════════════════
NEGATIVE_PLAN = "당사는 오프라인 매장을 확대하지 않을 계획입니다."


def test_flipping_a_negated_plan_into_a_positive_one_is_rejected():
    assert future_plan_problem(
        ("오프라인 매장 확대", "", ""),
        {"f1": NEGATIVE_PLAN},
        evidence(target="오프라인 매장", activity="확대", quote=NEGATIVE_PLAN),
    ) == FUTURE_POLARITY_FLIPPED


def test_a_negated_plan_written_with_the_same_polarity_is_kept():
    assert future_plan_problem(
        ("오프라인 매장 확대 중단", "", ""),
        {"f1": NEGATIVE_PLAN},
        evidence(target="오프라인 매장", activity="확대", quote=NEGATIVE_PLAN),
    ) == ""


def test_a_negation_inside_a_modifier_is_not_read_as_plan_negation():
    """「수수료 없는 …」은 계획을 뒤집는 말이 아니다 — 정상 줄을 지운다."""

    source = "당사는 수수료 없는 비대면 서비스를 확대할 계획입니다."
    assert future_plan_problem(
        ("수수료 없는 비대면 서비스 확대", "", ""),
        {"f1": source},
        evidence(target="비대면 서비스", activity="확대", quote=source),
    ) == ""


# ══════════════════════════════════════════════════════════
# ⑧ 후보 쪽 완료 표기 — 성장 계획 표에 들어올 수 없다
# ══════════════════════════════════════════════════════════
def test_a_candidate_cell_stating_completion_is_rejected():
    source = "당사는 신공장 건설을 추진할 계획입니다."
    assert future_plan_problem(
        ("신공장 건설 추진 완료", "", ""),
        {"f1": source},
        evidence(target="신공장", activity="건설", quote=source),
    ) == FUTURE_CANDIDATE_STATES_CURRENT


# ══════════════════════════════════════════════════════════
# ⑨ 형식 오류·누락은 모두 근거 결속 실패로 닫힌다
# ══════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "evidence_payload, expected",
    [
        (None, FUTURE_EVIDENCE_MISSING),
        ({}, FUTURE_EVIDENCE_MISSING),
        ({FUTURE_KEY: []}, FUTURE_EVIDENCE_MISSING),
        ({FUTURE_KEY: "확대할 계획"}, FUTURE_FIELD_TYPE_INVALID),
        ({FUTURE_KEY: [{"근거": "f1", "대상": ["생산 능력"], "활동": "확대",
                        "원문": GROWTH, "양태": "계획"}]}, FUTURE_FIELD_TYPE_INVALID),
        ({FUTURE_KEY: ["문자열"]}, FUTURE_FIELD_TYPE_INVALID),
    ],
)
def test_missing_evidence_and_malformed_shapes_close_as_binding_failures(evidence_payload, expected):
    assert future_plan_problem(
        ("생산 능력 확대", "", ""), {"f1": GROWTH}, evidence_payload
    ) == expected


def test_a_mode_outside_the_contract_is_rejected():
    assert future_plan_problem(
        ("생산 능력 확대", "", ""), {"f1": GROWTH},
        evidence(target="생산 능력", activity="확대", quote=GROWTH, mode="추정"),
    ) == FUTURE_MODE_INVALID


def test_an_empty_source_id_leaves_no_source_to_compare():
    assert future_plan_problem(
        ("생산 능력 확대", "", ""), {"f1": GROWTH},
        evidence(source="  ", target="생산 능력", activity="확대", quote=GROWTH),
    ) == FUTURE_SOURCE_ID_EMPTY


def test_a_too_short_quote_cannot_serve_as_binding_evidence():
    assert future_plan_problem(
        ("생산 능력 확대", "", ""), {"f1": GROWTH},
        evidence(target="생산 능력", activity="확대", quote="확대"),
    ) == FUTURE_QUOTE_TOO_SHORT


def test_a_row_with_no_values_is_not_judged():
    assert future_plan_problem(("", "", ""), {"f1": GROWTH}, None) == ""


# ══════════════════════════════════════════════════════════
# ⑩ 사유 코드가 진단 전송 계약과 어긋나지 않는다
# ══════════════════════════════════════════════════════════
def test_every_reason_code_is_registered_in_the_diagnostic_contract():
    """★ 등록이 빠지면 그 사유로 제외된 줄이 진단에서 조용히 사라진다."""

    unregistered = [
        code for code in FUTURE_REASON_CODES
        if REVIEW_SCOPE_ITEMS.get(code) != FUTURE_REASON_ITEM
    ]
    assert unregistered == []


def test_reason_codes_have_no_duplicates():
    assert len(set(FUTURE_REASON_CODES)) == len(FUTURE_REASON_CODES)
