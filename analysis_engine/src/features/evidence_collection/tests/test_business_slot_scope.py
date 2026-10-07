"""신용관리·보험·조사가 고객·운영 역할을 거짓 준비시키지 않는다."""
import hashlib

import pytest

from features.evidence_collection.business_slot_scope import business_slot_scope, business_slot_scope_problem
from features.evidence_collection.relevance import score_fragment_slots_with_signal
from features.evidence_collection.tests.test_streaming_collection import _collect

CUSTOMER = "business_model:customer_type"
ROLE = "operations_partners:operating_role"
POLICIES = (
    (CUSTOMER, "회사는 거래처의 신용등급을 결정할 목적으로 재무정보와 거래실적을 사용하고 있습니다."),
    (ROLE, "회사는 제조물의 결함으로 인한 생명, 신체 또는 재산의 피해에 대비해 보험계약을 체결한다."),
    (CUSTOMER, "회사는 신용도가 일정 수준 이상인 거래처와 거래하고 담보를 수취하는 정책을 채택한다."),
    (CUSTOMER, "신용위험 관리\n매출채권의 거래처 신용등급을 검토하고 신용보증보험으로 손실을 관리한다."),
    (CUSTOMER, "회사는 매출채권에 신용평가를 수행하며, 거래처의 신용등급을 지속적으로 검토한다."),
    (ROLE, "제조물책임법에 따라 제조업자에게 손해배상책임을 부과하므로 회사는 보험계약을 체결한다."),
    (ROLE, "회사는 공정거래위원회로부터 금형 제조위탁 거래 조사를 받았으며 조사결과에 이의를 제기했다."),
    (ROLE, "제조물책임보험\n회사는 제조물책임보험계약을 체결한다."),
)


@pytest.mark.parametrize("slot,text", POLICIES)
def test_policy_or_investigation_cannot_fill_business_slot_or_become_no_signal(slot, text):
    scores, observed = score_fragment_slots_with_signal(text)
    assert observed
    assert slot not in {score.slot_id for score in scores}
    assert business_slot_scope_problem(text, slot) == "business_slot_scope_unsupported"
    assert business_slot_scope_problem(text, "current_challenges:issue") == ""


@pytest.mark.parametrize("slot,text", [
    (CUSTOMER, "회사의 주요 고객사는 산업장비 업체와 의료기관이다."),
    (CUSTOMER, "회사는 금융 거래처에게 신용위험 관리 서비스를 제공한다."),
    (CUSTOMER, "회사는 기업 고객사에 대출 서비스를 제공하며 신용등급을 평가한다."),
    (ROLE, "회사는 산업장비를 제조하고 고객사에 공급한다."),
    (ROLE, "회사는 제조물책임보험 상품을 고객사에 제공한다."),
    (ROLE, "회사는 공정위 조사를 받았지만 제품을 생산하고 고객사에 납품한다."),
    (ROLE, "회사는 자동차 부품 제조를 외주 업체에 위탁하고 공급망을 운영한다."),
    (CUSTOMER, "회사는 신용등급을 평가하며, 은행의 고객은 기업과 개인 차주이다."),
    (CUSTOMER, "은행의 고객은 기업과 개인 차주이며 신용위험 관리정책을 운영한다."),
    (ROLE, "보험사는 제조물책임보험을 설계하고 판매한다."),
    (ROLE, "보험사는 제조물책임보험 계약을 인수한다."),
])
def test_actual_customers_financial_services_and_operating_role_are_preserved(slot, text):
    assert not business_slot_scope_problem(text, slot)
    assert business_slot_scope(text, slot).score_text.strip()


@pytest.mark.parametrize("policy_slot,policy", POLICIES[:2])
@pytest.mark.parametrize("separator", ["\n", " ", ", ", "; "])
def test_mixed_paragraph_masks_only_policy_clause_and_preserves_exact_source(policy_slot, policy, separator):
    fact = ("회사의 주요 고객사는 의료기관이다." if policy_slot == CUSTOMER
            else "회사는 산업장비를 제조하고 생산한다.")
    text = policy + separator + fact
    scoped = business_slot_scope(text, policy_slot)
    assert fact.rstrip(".") in scoped.score_text
    assert not business_slot_scope_problem(text, policy_slot)
    # 제한은 점수 입력뿐이다. 수집 원문·좌표·지문은 변경되지 않는다.
    original = policy + " " + fact
    harvest = _collect(original)
    assert any(fact in fragment.text for fragment in harvest.fragments)
    for document in (*harvest.documents, *harvest.unclassified_documents):
        assert document.content_sha256 == hashlib.sha256(original.encode()).hexdigest()
    for fragment in (*harvest.fragments, *harvest.unclassified_fragments):
        start, end = map(int, fragment.location.split("-"))
        assert original[start:end] == fragment.text
        assert fragment.text_sha256 == hashlib.sha256(fragment.text.encode()).hexdigest()


def test_pure_excluded_policy_is_not_retained_for_ai_reclassification():
    text = POLICIES[0][1]
    harvest = _collect(text)
    assert not harvest.fragments
    assert not harvest.unclassified_fragments


@pytest.mark.parametrize("text", [
    "한빛대학교(학사) 가람제조 제조기술팀 팀장",
    "학력: 공학 석사 / 주요 경력: 생산기술본부 본부장",
    "주요경력 | 제조사업부 담당 임원 | 공학 박사",
    "한빛대학교(학사) 가람산업 동부공장 제조담당",
    "다솔대학교(학사) 가람산업 해외공장 생산담당",
    "공학 학사 주요경력 가람산업 동부공장에서 제품 제조를 담당",
    "공학 학사 가람산업 생산기술본부 제조부문 담당",
    "주요 경력 가람산업 동부공장 제조 수행 담당자",
    "공학 학사 출신으로 이전 직장에서 공장 생산을 담당했다.",
    "주요 경력: 가람산업 동부공장 생산을 담당하였다.",
    "주요 경력: 공학 박사, 가람산업 동부공장 생산을 담당하였다.",
    "한빛대학교(학사/석사) 가람제조 생산기획담당",
    "학력 및 주요 경력: 공학 박사 가람제조 제조기술기획담당",
    "학력 및 주요 경력: 공학 학사 가람산업 공정개선담당",
])
def test_학위와_개인경력의_조직명은_직접운영역할이_아니다(text):
    original = text
    scores, observed = score_fragment_slots_with_signal(text)
    assert observed
    assert ROLE not in {score.slot_id for score in scores}
    assert business_slot_scope_problem(text, ROLE) == "business_slot_scope_unsupported"
    assert business_slot_scope(text, "culture:leadership").score_text == original
    harvest = _collect(text)
    assert not harvest.fragments
    assert not harvest.unclassified_fragments
    assert text == original


@pytest.mark.parametrize("text", [
    "공학 학사 출신 제조 담당 임원은 현재 제품 생산을 총괄한다.",
    "석사 출신 공장장은 현재 부품 제조를 담당한다.",
    "회사는 제조기술팀을 운영하며 제품을 생산한다.",
    "회사는 생산팀에서 산업장비를 제조하고 고객사에 공급한다.",
    "학교의 팀장은 공학 학사 교육 서비스를 고객에게 제공한다.",
    "공학 학사 출신 제조담당은 현재 공장에서 제품을 제조한다.",
    "공학 학사 출신 제조담당은 현재 제조를 담당한다.",
    "공학 학사 출신 생산담당은 현재 생산을 수행하고 있다.",
    "학사 출신 제조담당은 현재 회사의 제품 제조를 담당하며 생산을 수행한다.",
    "공학 학사 경력을 가진 임원이 있으며, 회사는 공장 생산을 담당했다.",
    "주요 경력: 공학 학사 임원이 있으며, 회사는 공장에서 제품을 제조했다.",
    "학사 출신 생산기획담당은 현재 공장에서 제품 생산을 담당한다.",
    "학사 출신 생산기획담당은 설비를 개발하고 있다.",
])
def test_현재_임원의_실제운영책임과_현행조직업무는_보존한다(text):
    assert not business_slot_scope_problem(text, ROLE)
    scores, _ = score_fragment_slots_with_signal(text, allowed_slot_ids=frozenset({ROLE}))
    if "생산" in text or "제조" in text:
        assert ROLE in {score.slot_id for score in scores}


def test_경력절만_가리고_현재제조절과_리더십소개_원문을_보존한다():
    career = "주요 경력은 공학 학사 출신 제조기술팀 팀장이다."
    current = "현재 회사는 산업장비를 제조하고 생산한다."
    text = career + " " + current
    scoped = business_slot_scope(text, ROLE)
    assert scoped.excluded_clauses
    assert current.rstrip(".") in scoped.score_text
    assert not business_slot_scope_problem(text, ROLE)
    harvest = _collect(text)
    assert any(fragment.text == text for fragment in harvest.fragments)
    for fragment in harvest.fragments:
        start, end = map(int, fragment.location.split("-"))
        assert text[start:end] == fragment.text
        assert fragment.text_sha256 == hashlib.sha256(fragment.text.encode()).hexdigest()
    leadership = "대표이사 주요 경력: 제조기술팀 팀장, 공학 학사"
    scores, observed = score_fragment_slots_with_signal(leadership)
    assert observed
    assert {score.slot_id for score in scores} == {"culture:leadership"}


@pytest.mark.parametrize("separator", [", ", "\n", "; ", ". "])
@pytest.mark.parametrize("current", [
    "회사는 현재 공장에서 산업장비를 제조한다.",
    "제조담당은 현재 제품 제조를 담당하고 있다.",
])
def test_제조담당_경력과_실제업무가_섞여도_원문과_현재업무를_보존한다(separator, current):
    career = "한빛대학교(학사) 가람산업 동부공장 제조담당"
    text = career + separator + current
    scoped = business_slot_scope(text, ROLE)
    assert scoped.excluded_clauses
    assert career not in scoped.score_text
    assert current.rstrip(".") in scoped.score_text
    assert not business_slot_scope_problem(text, ROLE)
    scores, observed = score_fragment_slots_with_signal(text)
    assert observed
    assert ROLE in {score.slot_id for score in scores}
    harvest = _collect(text)
    for fragment in harvest.fragments:
        start, end = map(int, fragment.location.split("-"))
        assert text[start:end] == fragment.text
        assert fragment.text_sha256 == hashlib.sha256(fragment.text.encode()).hexdigest()


def test_새분류의_캐시는_갱신하고_원문파서버전은_유지한다():
    from features.evidence_collection import constants as c
    # 운영 역할의 의미칸 변경은 새 수집 결과로 구분하며 추출 원문은 같은 계약이다.
    assert c.COLLECTOR_VERSION == "evidence_collection/3.8"
    assert c.PARSER_VERSION == "evidence_collection_segment/2.2"


@pytest.mark.parametrize("activity", [
    "회사는 공장에서 제품을 생산할 계획이다.",
    "회사는 제조 설비를 개발할 예정이다.",
])
def test_학위복합직함과_섞인_회사의_생산계획은_보존한다(activity):
    text = "공학 학사 가람제조 생산기획담당, " + activity
    scoped = business_slot_scope(text, ROLE)
    assert scoped.excluded_clauses
    assert activity.rstrip(".") in scoped.score_text
    assert not business_slot_scope_problem(text, ROLE)
