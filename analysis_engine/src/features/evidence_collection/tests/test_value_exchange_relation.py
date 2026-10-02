"""2장 가치교환의 약한 단어를 실제 고객 거래와 구별한다."""

from __future__ import annotations

import pytest

from features.evidence_collection.relevance import score_fragment_slots_with_signal


def _result(text: str) -> tuple[bool, bool]:
    scores, observed = score_fragment_slots_with_signal(text)
    return (any(item.slot_id == "business_model:value_exchange" for item in scores), observed)


@pytest.mark.parametrize("text", (
    "금융부채를 양도하면서 수취한 대가는 상각후원가로 측정합니다.",
    "취득 자산의 대가와 공정가치를 비교해 손상 여부를 평가합니다.",
    "감사인은 회사가 제공한다는 자료의 가치를 검토했습니다.",
    "이사회는 기업 가치를 제고하는 방안을 논의했습니다.",
    "기술의 가치를 무형자산 손상검사에 반영했습니다.",
    "서비스를 제공한다. 별도 수수료를 받습니다.",
    "회사는 고객에게 보고서를 제공한다. 다른 자산의 대가를 평가합니다.",
    "당사의 판매경로는 정해졌습니다. 금융부채를 현금으로 회수했습니다.",
    "당사는 판매경로와 내수 매출을 관리합니다. 어음은 별도 회계로 회수합니다.",
    "회사는 내수 판매경로를 검토하고 현금성자산을 보유합니다.",
    "당사는 내수 판매경로를 검토하고 현금성자산을 회수합니다.",
    "은행은 고객 대출 금융자산의 공정가치와 이자수익을 측정합니다.",
    "대출 실행 대가의 이자수익을 유효이자율로 측정합니다.",
    "고객에게 재화를 제공할 때 대가를 수익으로 인식한다.",
    "회사는 고객에게 서비스를 제공할 때 수익을 인식합니다.",
    "회사는 고객가치 창출을 비전이자 경영목표로 삼습니다.",
    "고객에게 제공한 서비스의 가치를 손상평가에 반영합니다.",
    "고객에게 제공한 대출상품의 대가를 공정가치로 측정합니다.",
))
def test_거래관계_없는_약신호는_필수가치교환을_채우지_않는다(text: str) -> None:
    supported, _observed = _result(text)
    assert supported is False


@pytest.mark.parametrize("text", (
    "회사는 고객에게 서비스를 제공한다.",
    "회사는 고객에게 예약 서비스를 제공해 대기시간 단축 편익을 제공한다.",
    "회사는 사용자에게 새로운 가치를 전달하는 서비스를 운영합니다.",
    "은행은 고객에게 대출상품을 제공한다.",
    "회사는 고객에게 감사서비스를 제공한다.",
    "회사는 제품 판매대가를 수취합니다.",
    "회사는 매출을 얻으며 거래처에 부품을 제공한다.",
    "판매경로는 당사에서 협력 법인으로 이어지며 판매방법 및 조건에 따라 내수 대금은 현금·어음으로 회수합니다.",
    "회사는 고객에게 서비스를 제공했고 대가 15억원을 수익으로 인식했습니다.",
    "고객이 서비스를 이용할 때 월 구독료를 결제하고 이 대가를 매출로 인식합니다.",
    "은행은 대출을 실행하고 서비스 대가로 이자를 수취했습니다.",
    "회사는 고객에게 온라인 서비스를 제공하고 월 구독료를 받습니다.",
    "고객이 서비스를 이용하고 이용료를 결제합니다.",
    "당사는 제품 판매대금을 현금과 어음으로 회수합니다.",
    "회사는 고객에게 예약 서비스를 제공해 대기시간을 절감합니다.",
    "은행은 고객에게 대출상품을 제공하고 이자를 수취하며 금융자산의 공정가치를 측정합니다.",
    "고객 | 구독서비스 이용 | 이용료 결제",
    "기업 고객은 회사가 제공하는 소프트웨어 개발 용역을 이용하고 개발 대금을 지급합니다",
))
def test_같은_의미단위의_실제_거래와_가격없는_혜택은_보존한다(text: str) -> None:
    assert _result(text)[0] is True


def test_혼합문단의_상용구가_아닌_고객거래_문장은_보존한다() -> None:
    text = (
        "금융부채 지급대가는 상각후원가로 측정합니다. "
        "회사는 고객에게 구독 서비스를 제공한다."
    )
    assert _result(text)[0] is True


def test_다른_회계절의_약단어는_고객거래_점수에_보태지_않는다() -> None:
    text = (
        "금융부채의 대가와 자산의 가치를 측정합니다. "
        "회사는 고객에게 서비스를 제공한다."
    )
    scores, observed = score_fragment_slots_with_signal(text)
    exchange = next(item for item in scores if item.slot_id == "business_model:value_exchange")
    assert exchange.score_millis == 250
    assert observed is True


def test_판매대금_직접패턴은_원래_약단어가_없어도_근거로_남는다() -> None:
    text = (
        "판매경로는 당사→협력법인이며 판매방법 및 조건에 따라 "
        "내수 현금·어음 회수, 수출 송금으로 거래합니다."
    )
    scores, observed = score_fragment_slots_with_signal(text)
    exchange = next(item for item in scores if item.slot_id == "business_model:value_exchange")
    assert "direct_pattern:payment_route" in exchange.reason_codes
    assert observed is True


def test_수익인식_정책은_가치교환이_아니며_무신호_재판정도_막는다() -> None:
    assert _result("고객에게 재화를 제공할 때 대가를 수익으로 인식한다.") == (False, True)
    assert _result("회사는 고객에게 서비스를 제공할 때 수익을 인식합니다.") == (False, True)


@pytest.mark.parametrize("text", (
    "고객이 서비스를 이용합니다. 별도 자산의 이용료를 지급합니다.",
    "고객이 서비스를 이용합니다.\n별도 자산의 이용료를 지급합니다.",
    "고객이 서비스를 이용합니다.;이용료를 결제합니다.",
    "당사는 판매대금을 확인합니다. 금융부채를 현금으로 회수합니다.",
    "회사는 고객에게 서비스를 제공할 때 이용료를 수익으로 인식합니다.",
    "고객 | 구독서비스 이용;다른 거래 | 이용료 결제",
))
def test_이용료와_회수의_직접패턴도_다른_문장_행과_결합하지_않는다(text: str) -> None:
    assert _result(text)[0] is False
