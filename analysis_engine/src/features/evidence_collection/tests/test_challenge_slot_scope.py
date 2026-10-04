"""표의 같은 행 관계만 5장 지원칸으로 사용한다."""
import pytest

from features.evidence_collection.challenge_slot_scope import challenge_table_scope
from features.evidence_collection.relevance import score_fragment_slots_with_signal

HEADER = "재해발생회사 | 중대재해발생일자 | 발생장소 | 재해내용 | 사망자수 | 조치 및 전망"
ROW = "가온제조 | 2024.07.12 | 가온제조 공장 | 성형공정 끼임사고 | 1 | 안전센서 설치 예정"


def slots(text):
    scores, observed = score_fragment_slots_with_signal(text)
    return {score.slot_id for score in scores if score.slot_id.startswith("current_challenges:")}, observed


def test_actual_row_adds_issue_and_response_without_changing_raw():
    text = HEADER + "; " + ROW
    result = challenge_table_scope(text)
    assert result.has_incident_row and result.has_response_row
    assert ROW in result.issue_text
    supported, observed = slots(text)
    assert {"current_challenges:issue", "current_challenges:response"} <= supported
    assert observed
    assert text == HEADER + "; " + ROW


@pytest.mark.parametrize("row", [
    ROW.replace("2024.07.12", "2024.99.99"),
    ROW.replace("2024.07.12", "2024.02.30"),
    ROW.replace("2024.07.12", "-"),
    ROW.replace("가온제조 |", "- |", 1),
    ROW.replace("가온제조 |", "해당없음 |", 1),
    ROW.replace("가온제조 공장", "-"),
    ROW.replace("성형공정 끼임사고", "끼임사고 발생할 수 있음"),
])
def test_unbound_or_hypothetical_row_never_adds_five_slots(row):
    text = HEADER + "; " + row
    result = challenge_table_scope(text)
    assert not result.has_incident_row and not result.has_response_row
    supported, observed = slots(text)
    assert not supported
    assert observed


def test_header_only_does_not_support_issue_or_response():
    supported, observed = slots(HEADER)
    assert not supported and observed


@pytest.mark.parametrize("row", [
    "가온제조 | - | 끼임사고 | 안전센서 설치 조치",
    "- | 안전센서 설치 조치",
    " | ".join(["안전센서 설치 조치"] * 25),
])
def test_malformed_incident_pipe_row_does_not_return_through_keyword_scoring(row):
    supported, observed = slots(HEADER + "; " + row)
    assert not supported and observed


def test_parent_header_without_event_column_does_not_release_unbound_rows():
    header = "재해발생회사 | 중대재해발생일자 | 중대재해 내용 | 조치 및 전망"
    row = "가온제조 | - | 끼임사고 | 안전센서 설치 조치"
    assert slots(header + "; " + row) == (set(), True)


def test_complete_row_after_malformed_row_still_uses_original_incident_header():
    text = HEADER + "; 가온제조 | - | 끼임사고 | 안전센서 설치 조치; " + ROW
    result = challenge_table_scope(text)
    assert result.has_incident_row and result.has_response_row


def test_other_table_resets_mapping_and_does_not_borrow_date():
    text = HEADER + "; 구분 | 일자 | 회사명 | 내용 | 수량 | 금액; " + ROW
    assert not challenge_table_scope(text).has_incident_row


def test_date_and_response_from_another_row_do_not_complete_incident():
    text = HEADER + "; " + ROW.replace("2024.07.12", "-") + "; " + ROW.replace("성형공정 끼임사고", "안전 관리")
    result = challenge_table_scope(text)
    assert not result.has_incident_row and not result.has_response_row


def test_missing_response_is_not_supplied_from_header_or_other_row():
    text = HEADER + "; " + ROW.replace("안전센서 설치 예정", "-") + "; " + ROW.replace("성형공정 끼임사고", "안전 관리")
    result = challenge_table_scope(text)
    assert result.has_incident_row and not result.has_response_row
    supported, _ = slots(text)
    assert "current_challenges:issue" in supported
    assert "current_challenges:response" not in supported


def test_sanction_is_supported_by_actual_row_not_incidental_law_name():
    header = "제재조치일 | 처벌 또는 조치대상자 | 처벌 또는 조치내용 | 사유 및 근거법령 | 이행 및 재발방지대책"
    actual = "2024.04.10 | 가온제조 | 과태료 100만원 | 위험물안전관리법 | 과태료 납부 완료, 고객 납품 지연이 지속되고 있다"
    supported, _ = slots(header + "; " + actual)
    assert {"current_challenges:issue", "current_challenges:response"} <= supported
    noise = actual.replace("과태료 100만원", "해당없음")
    assert not slots(header + "; " + noise)[0]


def test_completed_sanction_history_keeps_raw_and_observation_without_current_slots():
    header = "제재조치일 | 처벌 또는 조치대상자 | 처벌 또는 조치내용 | 사유 및 근거법령 | 이행 및 재발방지대책"
    row = "2024.04.10 | 가온제조 | 과태료 100만원 | 위험물안전관리법 | 과태료 납부 완료"
    raw = header + "; " + row
    assert slots(raw) == (set(), True)
    assert not challenge_table_scope(raw).has_incident_row
    assert raw == header + "; " + row


def test_contractors_identity_is_preserved_in_score_text():
    row = ROW.replace("가온제조 |", "협력제조(가온제조 도급사) |", 1)
    result = challenge_table_scope(HEADER + "; " + row)
    assert result.has_incident_row
    assert "협력제조(가온제조 도급사)" in result.issue_text


@pytest.mark.parametrize("event", ["추락사고 발생", "충돌사고 발생"])
def test_named_accident_with_same_row_date_actor_and_place_is_valid(event):
    header = HEADER.replace("조치 및 전망", "개선대책")
    result = challenge_table_scope(header + "; " + ROW.replace("성형공정 끼임사고", event))
    assert result.has_incident_row and result.has_response_row
    hypothetical = challenge_table_scope(header + "; " + ROW.replace("성형공정 끼임사고", event + "할 수 있음"))
    assert not hypothetical.has_incident_row and not hypothetical.has_response_row


@pytest.mark.parametrize("response,expected", [
    ("안전장치 설치 완료", False),
    ("안전장치 설치 완료, 고객 납품 지연이 지속되고 있다", True),
    ("다음 분기 안전장치 도입 예정", True),
    ("납부 완료, 사고 후 안전장치 설치 중", True),
])
def test_improvement_response_column_requires_unresolved_or_planned_operations(response, expected):
    text = HEADER.replace("조치 및 전망", "개선대책") + "; " + ROW.replace("안전센서 설치 예정", response)
    assert challenge_table_scope(text).has_response_row is expected


def test_completed_history_and_current_incident_keep_separate_rows():
    historical = ROW.replace("안전센서 설치 예정", "안전장치 설치 완료")
    current = ROW.replace("2024.07.12", "2026.10.04").replace("성형공정 끼임사고", "화재로 생산 중단")
    raw = HEADER + "; " + historical + "; " + current
    scoped = challenge_table_scope(raw)
    assert scoped.has_incident_row and scoped.has_response_row
    assert current in scoped.issue_text and historical not in scoped.issue_text
    assert slots(raw)[1] is True


@pytest.mark.parametrize("text", [
    "연결회사는 지분상품에서 발생하는 가격변동위험에 노출되어 있습니다.",
    "부정 및 부정위험과 관련된 감사 절차 수행결과 보고",
    "감사 진행상황 보고 (독립성, 핵심감사사항, 내부회계관리제도감사 및 자금 관련 부정위험 통제 등)",
    "금융상품과 같이 자산이나 부채에 대한 관측할 수 없는 투입변수의 위험을 평가한다.",
    "금융 위험을 식별, 모니터링 및 평가하여 관리한다.",
])
def test_audit_and_financial_management_signals_remain_observed_without_five_slots(text):
    assert slots(text) == (set(), True)


@pytest.mark.parametrize("text", [
    "회사는 고객에게 공정가치 평가 서비스를 제공한다. 고객의 서비스 제공 지연이 발생하여 대응했다.",
    "은행의 차주 연체율이 급증하여 신용위험에 대응했다.",
    "감사 진행상황을 보고하며, 공장 생산이 중단되어 고객 납품 지연이 발생하여 대응했다.",
])
def test_real_services_and_mixed_operations_survive(text):
    supported, _ = slots(text)
    assert "current_challenges:response" in supported or "current_challenges:issue" in supported


@pytest.mark.parametrize("text", [
    "기업은 지연공시로 공시위반 제재금을 납부하고 공시교육으로 대응했다.",
    "우수기업으로 선정돼 성과를 거뒀으며 대응했다.",
])
def test_raw_challenge_signal_remains_observed_but_ineligible_slots_are_removed(text):
    assert slots(text) == (set(), True)


def test_original_scope_before_accounting_keeps_current_row_header_and_binding():
    header = "제재조치일 | 처벌 또는 조치대상자 | 처벌 또는 조치내용 | 사유 및 근거법령 | 이행 및 재발방지대책"
    closed = "2024.04.10 | 가온제조 | 과태료 100만원 | 안전관리 위반 | 과태료 납부 완료"
    current = "2026.10.04 | 가온제조 | 과태료 200만원 | 공장 화재로 생산이 중단됐다. | 과태료 납부 완료, 복구 진행 중"
    raw = header + "; " + closed + "; " + current
    supported, observed = slots(raw)
    assert {"current_challenges:issue", "current_challenges:response"} <= supported
    assert observed and raw == header + "; " + closed + "; " + current


@pytest.mark.parametrize("text", [
    "회계정책과 공시의 변경에 따라 기업회계기준서를 적용했다. 해당 개정이 재무제표의 유동성위험 정보에 미치는 중요한 영향은 없다.",
    "회사의 재무부문은 금융위험을 감시하고 관리한다. 이러한 시장위험과 신용위험 정책은 전기말 이후 변동이 없다.",
])
def test_policy_context_is_checked_before_older_accounting_split_removes_context(text):
    assert slots(text) == (set(), True)
