"""실제 문화 추론 반례와 공식 문화·절차 보존의 양성 대조."""

import pytest

from src.features.composer.culture_constants import (
    CULTURE_EVIDENCE_SCOPE_MISMATCH,
    CULTURE_SECTION_EVIDENCE_OFFCONTRACT,
)
from src.features.composer.culture_guard import (
    culture_problem,
    culture_section_evidence_problem,
)


@pytest.mark.parametrize("claim,sources", [
    (
        "2025년 4월 우리WON모바일 출시, 6월 원비즈 e-MP 서비스 출시, "
        "10월 삼성월렛머니 서비스 출시 등 신규 사업을 연속적으로 추진한 것은 "
        "고객 맞춤형 종합금융솔루션 제공이라는 경영목표를 실행하는 조직의 의사결정 방식을 보여준다.",
        {
            "85": "2025년 4월 우리은행은 우리WON모바일을 오픈하여 MVNO사업에 진출하였습니다. "
                  "2025년 10월 삼성전자와의 제휴를 통한 삼성월렛 머니 서비스를 출시하였습니다.",
            "219": "우리은행은 핵심사업 확장, 미래금융 가속, 고객신뢰 확립을 경영목표로 하여 "
                   "고객 맞춤형 종합금융솔루션을 제공하고자 합니다. "
                   "원비즈 e-MP 서비스를 2025년 6월 출시하였습니다.",
        },
    ),
    (
        "지속적인 리스크관리시스템 고도화를 통해 리스크관리 역량을 축적하고, "
        "유니버셜 뱅킹을 구축하여 그룹사 간 시너지를 활성화하는 것을 조직의 핵심 운영 원칙으로 삼고 있다.",
        {"9": "우리은행은 지속적인 리스크관리시스템 고도화를 통해 리스크관리 역량을 축적하여 "
              "국내외 시장 불확실성에 철저히 대비하고 있습니다. "
              "또한 그룹 통합마케팅을 지원하는 유니버셜 뱅킹을 구축하였습니다."},
    ),
    (
        "차주별 상환능력 점검 강화, 업종별 리스크 관리, 조기경보체계 고도화 등을 통해 "
        "추가적인 건전성 저하를 방지하는 것은 리스크관리를 조직의 일상적 운영 원칙으로 "
        "내재화하는 방식을 보여준다.",
        {"83": "당행은 차주별 상환능력 점검 강화, 업종별 리스크 관리, 조기경보체계 고도화 등을 "
               "통해 추가적인 건전성 저하를 방지하고 있으며, 부실채권 매각 및 선제적 관리 강화를 지속하고 있습니다."},
    ),
])
def test_actual_woori_business_evidence_does_not_establish_culture(claim, sources):
    assert culture_problem(claim, sources) == CULTURE_EVIDENCE_SCOPE_MISMATCH


@pytest.mark.parametrize("claim,source", [
    ("신규 공장 가동은 회사의 의사결정 방식을 보여준다.", "신규 공장을 가동했다."),
    ("매출 확대가 조직문화를 드러낸다.", "해외 매출이 확대됐다."),
    ("조직문화 행사 지원은 일하는 방식을 보여준다.", "조직문화 행사에 물품을 지원했다."),
    ("제품 개선을 핵심 운영 원칙으로 삼는다.", "제품을 개선했다. 조직문화는 공개하지 않았다."),
])
def test_business_event_and_incidental_culture_words_are_not_culture_evidence(claim, source):
    assert culture_problem(claim, {"공시": source}) == CULTURE_EVIDENCE_SCOPE_MISMATCH


@pytest.mark.parametrize("claim,source", [
    ("안전 검토를 조직의 핵심 운영 원칙으로 삼는다.", "당사의 운영 원칙은 안전 검토다."),
    ("공동 검토가 회사의 의사결정 방식을 보여준다.", "당사의 의사결정 절차는 공동 검토 후 승인이다."),
    ("담당자 자율 판단이 일하는 방식을 보여준다.", "회사는 일상 업무의 결정 권한을 담당자에게 위임한다."),
    ("위원회 심의는 조직의 의사결정 방식을 보여준다.", "위원회 정기 회의에서 예산을 심의한다."),
    ("승인권한의 분담은 회사의 의사결정 방식을 보여준다.", "승인 권한은 이사회와 부서장에게 구분하여 부여한다."),
    ("사업부 통합은 새로운 일하는 방식을 보여준다.", "당사는 조직 개편으로 두 사업부를 통합했다."),
    ("협력 중심의 조직문화를 반영한다.", "회사는 협력 중심의 조직문화를 지향한다고 밝혔다."),
    ("도전하는 인재를 중시하는 조직문화를 보여준다.", "공식 인재상은 도전하는 인재다."),
])
def test_explicit_official_culture_and_procedure_paraphrases_stay_for_semantic_review(claim, source):
    assert culture_problem(claim, {"공식자료": source}) == ""


@pytest.mark.parametrize("claim", [
    "이사회가 투자 안건을 승인하며 담당 부서장이 집행한다.",
    "정기회의는 매월 개최한다.",
    "담당자는 위임된 범위 안에서 자율적으로 결정한다.",
    "조직개편으로 심사팀을 분리했다.",
    "신제품을 출시했다.",
    "조직문화 프로그램을 제공하는 회사다.",
])
def test_normal_business_and_procedure_statements_are_not_blanket_removed(claim):
    assert culture_problem(claim, {"원문": claim}) == ""


def test_explicit_attribution_in_source_is_not_removed():
    claim = "상호 검토를 조직의 핵심 운영 원칙으로 삼고 있다."
    assert culture_problem(claim, {"원문": "회사는 " + claim}) == ""


def test_scope_does_not_depend_on_company_product_or_date():
    claim = "새빛산업의 2031년 물류망 확장은 조직의 의사결정 방식을 보여준다."
    assert culture_problem(claim, {"공시": "새빛산업은 2031년 물류망을 확장했다."}) == CULTURE_EVIDENCE_SCOPE_MISMATCH


def test_empty_source_cannot_establish_inferred_culture():
    assert culture_problem("실행은 조직의 의사결정 방식을 보여준다.", {}) == CULTURE_EVIDENCE_SCOPE_MISMATCH


def test_guard_does_not_claim_to_verify_official_status_or_other_culture_topics():
    # 다른 문화 구절의 의미 관련성과 기사/공시의 공식성은 문자열 API의
    # 검증 범위 밖이다. 빈 결과를 verified 판정으로 사용하면 안 된다.
    claim = "매출 확대는 조직의 의사결정 방식을 보여준다."
    source = "매출이 확대됐다. 승인권한은 이사회가 갖는다."
    assert culture_problem(claim, {"뉴스": source}) == ""


# ══════════════════════════════════════════════════════════
# 8장 «원문 절» 긍정 계약 — 판정 재료가 후보 표현이 아니라 인용 원문이다
#
# ★ 왜 필요한가 (실측) — 기존 재무위험 가드는 후보 «표현»을 본다. 실행 두 판을
#   비교하니 그 가드가 2건을 새로 잡는 동안, 같은 내용을 꼬리만 바꿔 적어
#   안 걸리는 재무 문장 4개가 그 자리에 새로 들어왔다(순증 0). 아래 시험은
#   «표현을 바꿔도 판정이 안 바뀐다»를 값으로 못 박는다.
# ══════════════════════════════════════════════════════════

#: 실제 실행의 8장 4문장이 각각 옮겨 적은 사업보고서 원문 절.
_재무위험_주관_원문 = (
    "재무위험관리는 주로 당사의 경영지원팀에서 주관하고 있으며 당사 내 "
    "사업부와의 긴밀한 협조 하에 재무위험 관리정책 수립 및 재무위험의 측정, "
    "평가, 헷지 등을 실행하고 있습니다"
)
_유동성_원문 = (
    "당사는 적정 유동성의 유지를 위하여 주기적인 자금수지 예측, 필요 현금수준 "
    "추정, 자금수지 관리 및 계획대비 실적 관리를 통하여 유동성 위험을 "
    "최소화하고 있습니다"
)
_자본관리_원문 = (
    "당사의 자본관리 목적은 계속기업으로서 주주 및 이해당사자들에게 이익을 "
    "지속적으로 제공할 수 있는 능력을 보호하고 자본비용을 절감하기 위해 "
    "최적의 자본구조를 유지하는 것입니다"
)
_이자율위험_원문 = (
    "당사는 이자율위험관리의 목표를 이자율변동으로 인한 불확실성의 최소화를 "
    "추구함으로써 기업의 가치를 극대화하는 데 두고 있습니다"
)


@pytest.mark.parametrize("원문", [_유동성_원문, _자본관리_원문, _이자율위험_원문],
                         ids=("유동성", "자본관리", "이자율위험"))
@pytest.mark.parametrize("후보", [
    # 같은 내용을 «규정 꼬리»가 있는 표현과 없는 표현으로 각각 적는다.
    # 예전 가드는 이 둘을 다르게 판정했다 — 이 계약은 같게 판정한다.
    "회사는 위험을 최소화하는 선제적 재무관리 원칙을 실행하고 있다.",
    "회사는 위험을 최소화하고 있다.",
    "회사의 일하는 방식은 재무 건전성을 지키는 데 초점을 맞춘다.",
], ids=("규정꼬리있음", "규정꼬리없음", "문화어휘로_바꿔씀"))
def test_재무_원문만_인용한_8장_문장은_표현을_바꿔도_제외된다(후보, 원문):
    assert culture_section_evidence_problem(
        후보, {"34": 원문}
    ) == CULTURE_SECTION_EVIDENCE_OFFCONTRACT


def test_재무_위험을_누가_맡는지_말한_원문은_보존된다():
    """8장 안내문의 예외 — 재무 위험을 «누가» 맡는지 조직으로 설명한 자료."""

    assert culture_section_evidence_problem(
        "회사는 경영지원팀을 중심으로 재무위험관리를 주관한다.",
        {"34": _재무위험_주관_원문},
    ) == ""
    # 후보 표현을 다르게 적어도 판정은 그대로다 — 재료가 원문이기 때문이다.
    assert culture_section_evidence_problem(
        "재무위험 관리의 주관 조직이 정해져 있다.",
        {"34": _재무위험_주관_원문},
    ) == ""


@pytest.mark.parametrize("원문", [
    "당사는 임직원 사내대출 관리규정에 따라 신용검증절차를 거쳐 주택자금을 "
    "지원하고 있으며, 인사위원회가 승진 기준을 심의합니다",
    "당사의 인재상은 도전과 협업을 실천하는 인재입니다",
    "사.직원 등 현황 직원수 902명, 평균근속연수 8.5년입니다",
    "당사는 전결규정에 따라 의사결정 권한을 위임하고 있으며 승인권한을 "
    "부서장에게 부여합니다",
], ids=("인사제도", "인재상", "직원현황", "의사결정절차"))
def test_인사제도_인재상_직원현황_원문은_보존된다(원문):
    """음성 대조 — 이 장이 실제로 다루는 소재는 그대로 남는다."""

    assert culture_section_evidence_problem("회사의 제도를 설명한다.", {"7": 원문}) == ""


def test_그_소재가_없다고_적은_원문은_근거로_세지_않는다():
    """「인재상을 공시하지 않는다」는 절은 인재상 자료가 아니다."""

    assert culture_section_evidence_problem(
        "회사의 인재상을 설명한다.",
        {"7": "당사는 인재상을 별도로 공시하지 않습니다"},
    ) == CULTURE_SECTION_EVIDENCE_OFFCONTRACT


def test_인용_원문이_없으면_이_장의_소재를_확인할_수_없다():
    """fail-closed — 빈 결과는 «확인됐다»가 아니라 «확인할 수 없다»이다."""

    assert culture_section_evidence_problem(
        "회사의 조직문화를 설명한다.", {}
    ) == CULTURE_SECTION_EVIDENCE_OFFCONTRACT


def test_이_계약은_회사나_업종을_보지_않는다():
    """회사명·업종·연도가 달라도 같은 원문 모양이면 같은 판정이다."""

    사람_원문 = "{회사}는 임직원 교육훈련 제도를 운영한다"
    재무_원문 = "{회사}는 유동성 위험을 최소화하고 있습니다"
    for 회사 in ("새빛산업", "가나다전자", "한밭바이오"):
        assert culture_section_evidence_problem(
            "후보", {"1": 사람_원문.format(회사=회사)}
        ) == ""
        assert culture_section_evidence_problem(
            "후보", {"1": 재무_원문.format(회사=회사)}
        ) == CULTURE_SECTION_EVIDENCE_OFFCONTRACT


def test_명사_안에_묻힌_어간으로는_면제되지_않는다():
    """★ 실측으로 찾은 우회 구멍 — 「스마트교육사업부」의 «교육».

    면제(②)는 조직 주체 «와» 절차 행위를 함께 요구한다. 행위 어간을 낱말
    안에서만 찾으면, 부서 이름 하나와 그런 명사 하나만 있으면 계약이 뚫린다.
    그래서 어간이 «서술어나 조사와 함께» 쓰였을 때만 인정한다.
    """

    우회_시도 = "경영지원실은 스마트교육사업부의 2025년 매출이 늘었다고 밝혔습니다."
    assert culture_section_evidence_problem(
        "후보", {"1": 우회_시도}
    ) == CULTURE_SECTION_EVIDENCE_OFFCONTRACT

    # 같은 조직 주체라도 «실제로 무엇을 한다»고 적히면 그대로 보존된다.
    실제_절차 = "경영지원실은 임직원 교육훈련 과정을 운영하고 있습니다."
    assert culture_section_evidence_problem("후보", {"1": 실제_절차}) == ""
