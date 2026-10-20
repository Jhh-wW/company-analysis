"""법인 개요가 제품 표제로 잘못 채점되는 것을 막고 현재 서비스 원문은 보존한다."""
import hashlib

import pytest

from features.evidence_collection.relevance import score_fragment_slots_with_signal
from features.evidence_collection.product_role_relation_constants import REASON_CODE, SLOT_ID
from features.evidence_collection.tests.test_product_role_relation import _harvest


@pytest.mark.parametrize("fact", [
    "회사는 전자지급결제대행업을 영위할 목적으로 설립된 회사다.",
    "ANT INTERNATIONAL HOLDING PTE. LTD.는 지분을 보유한 주요 주주이다.",
    "회사는 다른 종속기업과의 합병을 완료했다.",
    "최대주주는 A기업이다.",
    "합병으로 A법인이 회사의 포트폴리오에 편입되었다.",
])
def test_주력제품_표제를_붙여도_법인사실은_제품역할로_채점하지_않는다(fact):
    scores, observed = score_fragment_slots_with_signal("주력 제품 소개: " + fact)
    assert observed
    assert SLOT_ID not in {score.slot_id for score in scores}


def test_법인연혁과_함께_있어도_현재서비스의_제품역할과_원문좌표를_보존한다():
    service = "당사는 온라인 가맹점의 결제 승인과 정산을 연결하는 전자결제 서비스를 제공하고 있습니다."
    text = (
        "(주)가온기업\nII. 사업의 내용\n가. 주요 제품 및 서비스\n\n"
        "당사는 결제대행업을 영위할 목적으로 설립되었습니다. " + service
    )
    result = _harvest(text)
    role, = [fragment for fragment in result.fragments if REASON_CODE in fragment.reason_codes]
    assert role.covered_slot_ids == (SLOT_ID,)
    assert service in role.text
    begin, end = map(int, role.location.split("-"))
    assert text[begin:end] == role.text
    assert hashlib.sha256(role.text.encode()).hexdigest() == role.text_sha256
