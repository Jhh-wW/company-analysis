"""외주 공시의 범위 확대와 정상 음성 사실을 분리한다."""

import hashlib

import pytest

from src.features.composer.outsourcing_scope import outsourcing_scope_problem
from src.features.composer.scope_guard import flow_scope_problem, scope_problem


SOURCE = "당사는 장비 조립과 관련하여 외주가공을 맡기고 있는 관계로 생산능력에 대해 기재할 사항은 없습니다."
BAD = "장비 조립과 관련하여 외주가공을 맡기고 있으므로, 회사는 조립·검사 등의 생산 공정을 직접 수행하지 않는다."


@pytest.mark.parametrize("guard", [outsourcing_scope_problem, scope_problem])
def test_외주와_기재생략만으로_직접수행부재를_추가하지_않는다(guard):
    sources = {"원문": SOURCE}
    before = hashlib.sha256(SOURCE.encode()).hexdigest()
    assert guard(BAD, sources) == "scope_condition_unbound"
    assert sources == {"원문": SOURCE}
    assert hashlib.sha256(sources["원문"].encode()).hexdigest() == before


def test_도식의_한칸도_같은_경계를_사용한다():
    assert flow_scope_problem(("장비", BAD, "고객"), {"원문": SOURCE}) == "scope_condition_unbound"


@pytest.mark.parametrize("verb", ["수행", "생산", "운영", "제조", "처리"])
@pytest.mark.parametrize("direct", ["직접", "자체적으로"])
def test_공정명에_의존하지_않는_명시부정_경계(verb, direct):
    candidate = f"외주가공을 맡기므로, 회사는 대상 업무를 {direct} {verb}하지 않는다."
    assert scope_problem(candidate, {"원문": SOURCE}) == "scope_condition_unbound"


@pytest.mark.parametrize("target", ["생산 공정", "시설 운영"])
def test_같은_대상_전량외주는_음성사실을_보존한다(target):
    source = SOURCE + f" 당사는 {target}을 전량 외주업체에 맡긴다."
    candidate = f"외주가공을 맡기므로, 회사는 {target}을 직접 수행하지 않는다."
    assert scope_problem(candidate, {"원문": source}) == ""


def test_다른_대상의_전량외주는_부재주장에_빌려쓰지_않는다():
    source = SOURCE + " 당사는 포장 작업을 전량 외주업체에 맡긴다."
    candidate = "외주가공을 맡기므로, 회사는 생산 공정을 직접 수행하지 않는다."
    assert scope_problem(candidate, {"원문": source}) == "scope_condition_unbound"


def test_명시_직접생산부재는_동일대상에서_보존한다():
    source = SOURCE + " 당사는 장비를 직접 생산하지 않는다."
    candidate = "외주가공을 맡기므로, 회사는 장비를 직접 생산하지 않는다."
    assert scope_problem(candidate, {"원문": source}) == ""


def test_다른_주체의_부정은_회사에_빌려쓰지_않는다():
    source = SOURCE + " 고객사는 장비를 직접 생산하지 않는다."
    candidate = "외주가공을 맡기므로, 회사는 장비를 직접 생산하지 않는다."
    assert scope_problem(candidate, {"원문": source}) == "scope_condition_unbound"


@pytest.mark.parametrize("candidate", [
    "회사는 일부 가공을 외주에 맡기고 장비를 자체 생산한다.",
    "외주를 활용하므로 설비 부담이 상대적으로 낮을 수 있다는 해석이다.",
    "외주가공을 활용하지만 회사는 인건비를 매출로 인식하지 않는다.",
    "회사는 고객의 장비를 직접 운영하지 않는다.",
])
def test_이_좁은_관계에_해당하지않는_문장은_의미검수에_남긴다(candidate):
    assert scope_problem(candidate, {"원문": SOURCE}) == ""


def test_명시부정_정상사실을_외주와_무관한_문단에서_보존한다():
    source = SOURCE + " 회사는 고객의 장비를 직접 운영하지 않는다."
    candidate = "외주를 활용하지만 회사는 고객의 장비를 직접 운영하지 않는다."
    assert scope_problem(candidate, {"원문": source}) == ""


def test_기재생략_없는_외주원문은_이_닫힌_가드로_거절하지_않는다():
    assert scope_problem(BAD, {"원문": "회사는 외주업체와 자체 생산시설을 함께 이용한다."}) == ""


@pytest.mark.parametrize("tail", [
    "생산 공정을 전량 외주업체에 맡긴다고 계획했다.",
    "생산 공정을 전량 위탁할 예정이다.",
    "장비를 직접 생산하지 않는다고 가정한다.",
])
def test_계획이나_가정의_부정은_현재부재근거로_빌리지_않는다(tail):
    target = "장비" if "장비" in tail else "생산 공정"
    candidate = f"외주가공을 맡기므로, 회사는 {target}을 직접 생산하지 않는다."
    assert scope_problem(candidate, {"원문": SOURCE + " 회사는 " + tail}) == "scope_condition_unbound"


@pytest.mark.parametrize("prefix", [
    "전기에는", "향후", "주문이 없는 경우에만", "협력사인", "종속기업인", "과거에",
])
@pytest.mark.parametrize("support", [
    "생산 공정을 전량 외주업체에 맡긴다.", "생산 공정을 직접 수행하지 않는다.",
])
def test_예외근거의_주어앞_시점조건주체를_버리지_않는다(prefix, support):
    source = SOURCE + f" {prefix} 회사는 {support}"
    candidate = "외주가공을 맡기므로, 회사는 생산 공정을 직접 수행하지 않는다."
    assert scope_problem(candidate, {"원문": source}) == "scope_condition_unbound"


@pytest.mark.parametrize("prefix", ["", "현재", "지금", "당기", "보고 기간"])
def test_명시현재의_동일회사대상_전량외주는_보존한다(prefix):
    source = SOURCE + f" {prefix} 회사는 생산 공정을 전량 외주업체에 맡긴다."
    candidate = "외주가공을 맡기므로, 회사는 생산 공정을 직접 수행하지 않는다."
    assert scope_problem(candidate, {"원문": source}) == ""
