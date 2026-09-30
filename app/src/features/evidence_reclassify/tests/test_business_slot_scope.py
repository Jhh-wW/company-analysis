"""AI 재판정은 정책 원문을 사업의 고객·운영 역할로 되살릴 수 없다."""
import pytest

from src.features.evidence_reclassify.logic import parse_and_verify
from src.features.evidence_reclassify.tests.test_logic import _candidate, _assignment, _response


@pytest.mark.parametrize("slot,quote", [
    ("business_model:customer_type", "회사는 신용등급이 높은 거래처와만 거래하고 담보를 수취한다."),
    ("operations_partners:operating_role", "회사는 제조물책임법에 따라 보험계약을 체결한다."),
    ("operations_partners:operating_role", "회사는 공정거래위원회의 제조위탁 조사결과에 이의를 제기했다."),
])
def test_정책인용은_유효사업이_다른절에_있어도_해당칸을_얻지_못한다(slot, quote):
    text = quote + " 회사는 고객사에 산업장비를 공급한다."
    result = parse_and_verify(_response(_assignment(section_id=slot.split(":")[0], slot_id=slot, quote=quote)),
                              [_candidate(text=text)])
    assert result.assignments == ()
    assert result.rejected[0].reason_code == "business_slot_scope_unsupported"


def test_같은문단의_유효사업_정확인용은_재분류할_수_있다():
    quote = "회사는 금융 고객에게 신용등급 평가 서비스를 제공합니다."
    text = "회사는 신용등급이 높은 거래처와만 거래하고 담보를 수취한다. " + quote
    result = parse_and_verify(_response(_assignment(section_id="business_model", slot_id="business_model:customer_type", quote=quote)),
                              [_candidate(text=text)])
    assert len(result.assignments) == 1
    assert result.assignments[0].exact_quote == quote
