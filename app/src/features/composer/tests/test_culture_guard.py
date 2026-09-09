"""실제 문화 추론 반례와 공식 문화·절차 보존의 양성 대조."""

import pytest

from src.features.composer.culture_constants import CULTURE_EVIDENCE_SCOPE_MISMATCH
from src.features.composer.culture_guard import culture_problem


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
