"""현대글로비스 수정본 실측: 이사회 자체의 구성·운영 규정은 문화 근거가 아니다.

실제 실행 e5f7dcb26977d772eb8730e70f435172
(20260922_201111_09bd76b5b6605f66767d76fc, glovis_fixed) report.json 8장의
두 문장과, 그 문장들이 기댄 원문이다. 원문은 이번 실행 폴더의
raw_filings/20260318001205.xml(2025.12 사업보고서)·20260814002854.xml(2026.06
반기보고서)에서 태그만 벗긴 연속 구간이다 — 보고서 문장 자체가 아니라 실제
공시 원문을 근거로 놓았다.

첫 문장(정관 제31조)은 감사위원회·채무보증·자산거래 규정을 막은 뒤에도 남았다.
둘째 문장(통합 준법경영시스템·교육·부서별 리스크 평가)은 총괄이 «넓은 일하는
방식»으로 보존 판정했으므로, 실제 원문과 함께 보존을 못 박는다.
이 시험은 8장 인재상이 풍부해졌다는 주장이 아니다 — 잘못 실린 문장이 빠지고
보존 판정을 받은 문장이 남는다는 «경계»만 고정한다.
"""

from dataclasses import asdict
from hashlib import sha256
import json
import re

import pytest

from src.features.composer.culture_constants import CULTURE_SECTION_EVIDENCE_OFFCONTRACT
from src.features.composer.culture_guard import (
    _clause_carries_section_subject,
    _is_board_charter_clause,
    _surface,
    culture_section_evidence_problem,
)
from src.features.composer.empty_section_recovery import recovery_evidence
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence,
)
from src.features.composer.render import render_report
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import NOTICE_ALL_SENTENCES_REJECTED, verify_report


#: 실제 8장 1번 문장 — report.json sections[7].prose_lines[0], 출처 [174].
BOARD_CHARTER_SENTENCE = (
    "회사의 이사회는 이사로 구성되며, 이사회 규정에 따라 권한 위임 및 운영에 관한 "
    "필요한 사항을 별도로 정하고 있다."
)
#: 실제 8장 2번 문장 — report.json sections[7].prose_lines[1], 출처 [200][242].
COMPLIANCE_TRAINING_SENTENCE = (
    "회사는 통합 준법경영시스템을 운영하여 리스크 평가 및 통제·관리방법 교육을 "
    "실시하고, 부서별 리스크 평가를 진행하며 잠재리스크 통제방안을 수립하는 "
    "방식으로 준법경영을 강화하고 있다."
)
#: 2025.12 사업보고서 정관 제31조 — 첫 문장이 기댄 원문(연속 구간 816자).
#: 부록 [174]의 원문 위치 길이(798자)와 거의 같은 조각 모양이다.
ARTICLES_31 = (
    "제31조(이사회의 구성과 권한)\n① 이사회는 이사로 구성하고, 법령과 본 정관에서 "
    "정한 사항 및 다음 각호의 사항을포함하여 회사의 업무 진행에 관한 중요한 사항을 "
    "결의하며, 이사 및 경영진의직무집행을 감독한다.1. 연간 예산, 자본투자계획, "
    "사업계획에 대한 승인 및 당초의 예산, 자본투자계획,사업계획의 중요한 변경에 "
    "대한 동의2. 미화 5 백만달러를 초과하는 차입, 미화 1 백만달러를 초과하는 자금의 "
    "대여 또는보증의 제공3. 회사의 주주 또는 그 계열사와의 계약 또는 거래의 체결, "
    "변경 또는 해지(회사의 일상적인 업무 범위 내에 속하거나, 사후승인을 전제로 "
    "긴급을 요하는사항은 제외함)4. 미화 1 백만달러를 초과하는 소송의 개시, 합의 "
    "또는 취하5. 미화 5 십만달러를 초과하는 고용계약 또는 자문계약의 체결, 변경 "
    "또는 해지6. 외부감사의 선임 및 해임7. 회계방침의 변경8. 이사회 또는 기타 중요한 "
    "회사의 조직 및 운영 관련 규정의 제정 및 변경9. 현대자동차 주식회사 및 그 "
    "계열사에 대한 투자10. 연간 이익 배당안의 작성11. 대표이사의 선임 및 해임12. "
    "합병13. 미화 1 천만달러를 초과하는 합작투자 또는 중요 협력 계약14. 정관의 "
    "변경15. 관련법에 따라 주주총회의 특별결의를 요하는 사항본 제31조 제①항에 "
    "명시된 각 금전적 기준은 회사의 전 회계연도에 대한 주주지분(즉, 순자산)의 "
    "증가율에 따라서 매년 9 월 30 일에 자동적으로 조정된다. 다만, 회사의기업공개로 "
    "인한 주주지분의 증가는 그러한 조정에 반영되지 아니한다.② 권한의 위임, 기타 "
    "이사회의 운영에 관한 필요한 사항을 정하기 위해서 별도의이사회 규정을 둘 수 있다."
)
#: 정관 제31조 중 이사회 조항 ①·② — 첫 문장이 실제로 기댄 절(②)을 담은 구간.
ARTICLES_31_BOARD_CLAUSES = (
    "① 이사회는 이사로 구성하고, 법령과 본 정관에서 정한 사항 및 다음 각호의 사항을"
    "포함하여 회사의 업무 진행에 관한 중요한 사항을 결의하며, 이사 및 경영진의"
    "직무집행을 감독한다. ② 권한의 위임, 기타 이사회의 운영에 관한 필요한 사항을 "
    "정하기 위해서 별도의이사회 규정을 둘 수 있다."
)
#: 2026.06 반기보고서 준법지원인 활동 — 둘째 문장이 기댄 원문 [242].
HALF_YEAR_COMPLIANCE = (
    "법적위험 관리 통합경영시스템 운영 - '26년 통합 준법경영시스템 운영계획 수립 "
    "(3/9)- 리스크 평가 및 통제·관리방법 교육 실시 (4/23, 4/24)- 부서별 리스크 평가 "
    "진행 및 잠재리스크 통제방안 수립 (4/27~5/13) 아. 준법지원인 등 지원조직 현황 "
    "보고서 작성기준일 현재 준법지원인 지원조직 현황은 아래와 같습니다."
)
#: 2025.12 사업보고서 준법지원인 활동 — 둘째 문장이 함께 인용한 원문 [200].
ANNUAL_COMPLIANCE = (
    "법적위험 관리 통합경영시스템 운영 - 리스크 평가 기법 및 통합준법경영시스템 "
    "운영체계 교육 실시(4.16/4.17.)- 리스크 통제방안 이행 모니터링 실시(7.16.~8.22)"
    "- 리스크 통제방안 이행 모니터링(2차) 실시 (9.24. - 10/17)- 내부심사 진행을 통한 "
    "리스크 관리 적격성 자체 점검 (10.20. ~ 10.31.)- 외부심사 수검완료 및 인증자격 "
    "유지(11.20. ~ 11.21.)"
)


@pytest.mark.parametrize("sources", (
    {"174": ARTICLES_31},
    {"174": ARTICLES_31_BOARD_CLAUSES},
    {"self": BOARD_CHARTER_SENTENCE},
), ids=("정관제31조_전체", "정관제31조_이사회조항", "자기인용"))
def test_real_board_charter_sentence_is_rejected(sources):
    """실제 첫 문장은 실제 정관 원문에 기대도, 문장 자체를 원문으로 놓아도 빠진다."""

    assert culture_section_evidence_problem(
        BOARD_CHARTER_SENTENCE, sources
    ) == CULTURE_SECTION_EVIDENCE_OFFCONTRACT


@pytest.mark.parametrize("sources", (
    {"242": HALF_YEAR_COMPLIANCE},
    {"200": ANNUAL_COMPLIANCE},
    {"200": ANNUAL_COMPLIANCE, "242": HALF_YEAR_COMPLIANCE},
    {"self": COMPLIANCE_TRAINING_SENTENCE},
), ids=("반기보고서", "사업보고서", "두_원문", "자기인용"))
def test_real_compliance_training_sentence_is_preserved(sources):
    """총괄 판정 — 교육·부서별 리스크 평가 활동은 넓은 일하는 방식이라 보존한다."""

    assert culture_section_evidence_problem(COMPLIANCE_TRAINING_SENTENCE, sources) == ""


@pytest.mark.parametrize("clause", (
    "① 이사회는 이사로 구성하고, 법령과 본 정관에서 정한 사항 및 다음 각호의 사항을"
    "포함하여 회사의 업무 진행에 관한 중요한 사항을 결의하며, 이사 및 경영진의"
    "직무집행을 감독한다",
    "② 권한의 위임, 기타 이사회의 운영에 관한 필요한 사항을 정하기 위해서 별도의"
    "이사회 규정을 둘 수 있다",
    "이 회사의 이사회는 3명 이상 10명 이하로 하고, 사외이사는 이사총수의 과반수가 "
    "되도록 한다",
    "이사회 헌장에 따라 이사회를 운영한다",
    "이사회 규정에 따라 이사회 운영에 관한 사항을 정한다",
    "정관 제31조에 따라 이사회의 구성과 권한을 정한다",
    "당사 이사회 구성원 중 5명의 사외이사는 사외이사 후보 추천위원회의 추천으로 "
    "선임됐습니다",
), ids=(
    "정관①_구성과권한", "정관②_이사회규정", "정관_이사수", "이사회헌장",
    "이사회규정_운영사항", "정관조문_구성권한", "이사회구성원_선임",
))
def test_board_self_constitution_and_charter_clauses_are_not_section_subject(clause):
    """이사회가 스스로를 어떻게 구성·운영하는지는 인재상도 일하는 방식도 아니다."""

    assert _is_board_charter_clause(_surface(clause)) is True
    assert _clause_carries_section_subject(clause) is False


@pytest.mark.parametrize("clause", (
    "이사회 규정에 따라 임직원 보상위원회를 운영한다",
    "이사회 규정과 전결규정에 따라 의사결정 권한을 부서장에게 위임한다",
    "이사회 규정에 따라 대표이사에게 일상 업무의 결정 권한을 위임한다",
    "이사회는 사외이사 교육을 매년 실시한다",
    "이사회가 투자 안건을 승인하며 담당 부서장이 집행한다",
    "임직원 주택자금 대출의 지급보증은 이사회가 승인한다",
    "이사회가 운영하는 보상위원회가 임원 성과급을 심의한다",
    "사외이사 수를 전체 이사수의 과반으로 정관에 규정합니다",
    "인사위원회가 직원 채용과 승진 기준을 심의한다",
), ids=(
    "이사회규정_임직원보상", "이사회규정_부서장위임", "이사회규정_대표이사위임",
    "이사회_사외이사교육", "이사회승인_부서장집행", "임직원대출_이사회승인",
    "이사회가운영하는_보상위원회", "정관_사외이사수(기존)", "인사위원회(기존)",
))
def test_people_procedures_bound_to_the_board_are_preserved(clause):
    """「이사회」 한 낱말로 막지 않는다 — 사람·업무 절차가 같은 절에 있으면 남긴다."""

    assert _is_board_charter_clause(_surface(clause)) is False
    assert _clause_carries_section_subject(clause) is True
    assert culture_section_evidence_problem(clause + ".", {"self": clause + "."}) == ""


@pytest.mark.parametrize("text", (
    "이사회 헌장에 따라 이사회를 운영한다.",
    "이사회는 3명 이상 10명 이하의 이사로 구성한다.",
    "정관 제31조에 따라 이사회 운영 규정을 둔다.",
))
def test_board_charter_paraphrases_are_rejected_even_with_identical_evidence(text):
    """사실 결속이 완벽해도 장 배치는 거절한다 — 법정 규정 시험과 같은 잣대."""

    assert culture_section_evidence_problem(text, {"self": text}) == CULTURE_SECTION_EVIDENCE_OFFCONTRACT


def _approver(calls):
    def ask(prompt):
        calls.append(prompt)
        items = review_items(re.sub(r"(?m)^  등급: [^\n]+\n", "", prompt))
        assert items, "검수 후보가 없습니다"
        return json.dumps({"판정": [
            {"번호": item.number, "장": item.section, "결과": "참", "근거": [
                citation.strip().removeprefix("조각 ").strip() for citation in item.citations
            ]}
            for item in items
        ]}, ensure_ascii=False)
    return ask


@pytest.mark.parametrize("grouped", (False, True))
def test_prose_review_removes_board_charter_and_keeps_compliance_training(grouped):
    """실제 verify 경로 — 두 문장이 각자 실제 원문을 인용한 8장 초안.

    모델이 둘 다 «참»으로 답해도 정관 문장만 빠지고, 추가 모델 호출은 없다.
    """

    fragments = (
        CollectedFragment("174", "공시", ARTICLES_31),
        CollectedFragment("242", "공시", HALF_YEAR_COMPLIANCE),
    )
    sentences = (
        ComposedSentence(BOARD_CHARTER_SENTENCE, ("174",), "확인"),
        ComposedSentence(COMPLIANCE_TRAINING_SENTENCE, ("242",), "확인"),
    )
    draft = ComposedReport((ComposedSection("culture", sentences),))
    allowed = {"culture": frozenset(("174", "242"))} if grouped else None
    calls = []
    checked = verify_report(draft, fragments, None, _approver(calls),
                            allowed_fragment_ids_by_section=allowed)
    assert tuple(sentence.text for sentence in checked.sections[0].sentences) == (
        COMPLIANCE_TRAINING_SENTENCE,
    )
    assert len(calls) == 1, "장 배치 검사가 추가 모델 호출을 만들었습니다"
    assert checked.sections[0].notice == ""

    rendered = render_report("현대글로비스", checked, fragments, None)
    public = asdict(rendered)
    culture = public["sections"][0]
    assert culture["lines"] == culture["prose_lines"]
    assert BOARD_CHARTER_SENTENCE not in json.dumps(public, ensure_ascii=False)
    # 조각 id가 숫자면 그 번호가 부록 번호가 된다 — 정관 조각 174는 부록에서도 사라진다.
    assert {source.number for source in rendered.citations} == {242}


def test_prose_review_leaves_a_notice_when_only_the_board_charter_sentence_exists():
    """정관 문장 하나뿐인 8장은 지배구조 상용구 대신 빈 장 안내가 된다."""

    fragments = (CollectedFragment("174", "공시", ARTICLES_31),)
    draft = ComposedReport((ComposedSection(
        "culture", (ComposedSentence(BOARD_CHARTER_SENTENCE, ("174",), "확인"),),
    ),))
    checked = verify_report(draft, fragments, None, _approver([]))
    assert checked.sections[0].sentences == ()
    assert checked.sections[0].notice == NOTICE_ALL_SENTENCES_REJECTED


def _formal_fragment(fragment_id, text):
    return CollectedFragment(
        fragment_id, "공시", text,
        formal_source_kind="dart_business_report",
        document_identity="검증된 시험 문서",
        document_content_sha256=sha256(text.encode("utf-8")).hexdigest(),
        identity_binding="시험 문서의 회사 결속",
        supported_claim_slots=("culture:decision_process",),
    )


def test_empty_section_recovery_does_not_reselect_board_charter_clauses():
    """빈 장 복구도 이사회 조항 ①·②만 담은 조각은 다시 뽑지 않는다.

    ⚠️ 알려진 한계 — 정관 제31조 «전체» 조각은 결의 사항 목록의 「고용계약」
      항목(사람 어휘 «고용») 때문에 여전히 복구 근거로 남는다. 그 조각에서 만든
      문장이 ①·②에 기대면 위 verify 경로가 막지만, 「고용계약」 절에 기댄
      문장은 이 수정의 범위 밖이다(어휘 «고용»을 좁히지 않았다).
    """

    board = _formal_fragment("174", ARTICLES_31_BOARD_CLAUSES)
    compliance = _formal_fragment("242", HALF_YEAR_COMPLIANCE)
    assert recovery_evidence((board, compliance)) == {"culture": (compliance,)}
    assert recovery_evidence((board,)) == {}


def test_other_sections_are_not_subject_to_the_board_charter_contract():
    """1장·5장 등 다른 장의 같은 문장은 이 계약의 대상이 아니다."""

    fragment = CollectedFragment("174", "공시", ARTICLES_31)
    for section_id in ("identity", "current_challenges"):
        sentence = ComposedSentence(BOARD_CHARTER_SENTENCE, ("174",), "확인")
        draft = ComposedReport((ComposedSection(section_id, (sentence,)),))
        checked = verify_report(draft, (fragment,), None, _approver([]))
        assert tuple(item.text for item in checked.sections[0].sentences) == (
            BOARD_CHARTER_SENTENCE,
        )
