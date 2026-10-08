"""설립 목적·현재 사업·다른 사업의 현재 실행을 독립적으로 대조한다."""

import pytest

from src.features.composer.founding_purpose_scope import founding_purpose_scope_problem
from src.features.composer.scope_guard import flow_scope_problem, scope_problem
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND

PURPOSE = "회사는 장비 생산판매 및 설비공사업, 모듈 제작업 등을 영위할 목적으로 설립되었다. 제조부문을 인적분할하였다."


@pytest.mark.parametrize("claim", [
    "회사는 장비 생산판매, 설비공사업, 모듈 제작업을 주요 사업으로 영위하고 있다.",
    "회사는 설립 이후 모듈 제작업을 영위하고 있으며 조직을 정리했다.",
    "당사는 이 사업을 영위하고 있다.",
])
def test_설립목적만으로_현재사업을_단정하지_않는다(claim):
    assert founding_purpose_scope_problem(claim, {"a": PURPOSE}) == SCOPE_CONDITION_UNBOUND


@pytest.mark.parametrize("claim", [
    "회사는 모듈 제작업을 영위할 목적으로 설립되었다.",
    "회사는 과거 모듈 제작업을 영위하였다.",
    "회사는 모듈 제작업을 영위하고 있었다.",
    "제조부문을 인적분할하였다.",
])
def test_설립목적과_과거사실을_현재주장으로_취급하지_않는다(claim):
    assert founding_purpose_scope_problem(claim, {"a": PURPOSE}) == ""


@pytest.mark.parametrize("activity", [
    "회사는 현재 모듈 제작업을 영위하고 있다.",
    "당사는 모듈 제작업 서비스를 제공하고 있습니다.",
])
def test_같은사업의_명시현재근거를_보존한다(activity):
    assert founding_purpose_scope_problem("회사는 모듈 제작업을 영위하고 있다.",
                                          {"a": PURPOSE + " " + activity}) == ""


@pytest.mark.parametrize("extra", [
    "회사는 운송 서비스를 제공하고 있다.",
    "회사는 모듈 제작업을 영위할 계획이다.",
    "회사는 모듈 제작업을 중단했으며 운송 서비스를 제공하고 있다.",
])
def test_다른사업이나_계획을_현재사업의_근거로_빌리지_않는다(extra):
    assert founding_purpose_scope_problem("회사는 모듈 제작업을 영위하고 있다.",
                                          {"a": PURPOSE, "b": extra}) == SCOPE_CONDITION_UNBOUND


def test_여러설립사업중_현재하나만_입증해도_나머지는_현재로_넓히지_않는다():
    source = {"a": PURPOSE, "b": "회사는 설비공사업을 영위하고 있다."}
    assert founding_purpose_scope_problem("회사는 설비공사업을 영위하고 있다.", source) == ""
    assert founding_purpose_scope_problem("회사는 설비공사업과 모듈 제작업을 영위하고 있다.", source) == SCOPE_CONDITION_UNBOUND


def test_설립목적_문맥없는_일반현재주장은_기존검수에_맡긴다():
    assert founding_purpose_scope_problem("회사는 모듈 제작업을 영위하고 있다.", {"a": "별도 자료"}) == ""


def test_본문과_도식_공통검수배선():
    claim = "회사는 모듈 제작업을 영위하고 있다."
    assert scope_problem(claim, {"a": PURPOSE}) == SCOPE_CONDITION_UNBOUND
    assert flow_scope_problem(("회사의 사업", claim), {"a": PURPOSE}) == SCOPE_CONDITION_UNBOUND


def test_한문장에_설립목적을_먼저써도_뒤의_현재사업확대는_검사한다():
    claim = "회사는 모듈 제작업을 영위할 목적으로 설립되어 현재 모듈 제작업을 영위하고 있다."
    assert founding_purpose_scope_problem(claim, {"a": PURPOSE}) == SCOPE_CONDITION_UNBOUND


def test_원문의_설립목적뒤에_별도로_명시한_현재실행은_남긴다():
    source = "회사는 모듈 제작업을 영위할 목적으로 설립되었으며 현재 모듈 제작업을 영위하고 있다."
    assert founding_purpose_scope_problem("회사는 모듈 제작업을 영위하고 있다.", {"a": source}) == ""


def test_설립목적뒤_다른사업의_현재실행은_빌리지_않는다():
    source = "회사는 모듈 제작업을 영위할 목적으로 설립되었으며 현재 운송 서비스를 제공하고 있다."
    assert founding_purpose_scope_problem("회사는 모듈 제작업을 영위하고 있다.", {"a": source}) == SCOPE_CONDITION_UNBOUND


def test_사업을_영위하는_회사로_설립되었다는_역사서술을_현재로_보지_않는다():
    claim = "회사는 모듈 제작업을 영위하는 회사로 1986년 설립되었으며 이후 제조부문을 분할하였다."
    assert founding_purpose_scope_problem(claim, {"a": PURPOSE}) == ""


def test_역사서술_뒤의_명시현재주장은_검사한다():
    claim = "회사는 모듈 제작업을 영위하는 회사로 설립되었으며 현재 모듈 제작업을 영위하고 있다."
    assert founding_purpose_scope_problem(claim, {"a": PURPOSE}) == SCOPE_CONDITION_UNBOUND


@pytest.mark.parametrize("source", [
    "회사는 모듈 제작업을 영위하는 것으로 가정한다.",
    "고객은 모듈 제작업을 영위하고 있다.",
    "종속기업은 모듈 제작업을 영위하고 있다.",
    "2019년 당시 회사는 모듈 제작업을 영위하며 제품을 생산했다.",
    "고객은 운송업을 영위하며 모듈 제작업을 영위하고 있다.",
])
def test_다른주체_과거_가정은_현재실행의_근거로_쓰지_않는다(source):
    assert founding_purpose_scope_problem("회사는 모듈 제작업을 영위하고 있다.",
                                          {"a": PURPOSE, "b": source}) == SCOPE_CONDITION_UNBOUND


@pytest.mark.parametrize("source", [
    "회사는 현재 모듈 제작업을 영위하고 있으며 운송업을 영위할 계획이다.",
    "회사는 과거 운송업을 영위하였으며 현재 모듈 제작업을 영위하고 있다.",
    "고객은 운송업을 영위하며 당사는 모듈 제작업을 영위하고 있다.",
])
def test_같은문장의_다른계획_주체_과거와_구별한_현재사업은_남긴다(source):
    assert founding_purpose_scope_problem("회사는 모듈 제작업을 영위하고 있다.",
                                          {"a": PURPOSE, "b": source}) == ""


@pytest.mark.parametrize("claim", [
    "회사는 설립 이후 모듈 제작업을 영위해 왔으며 제조부문을 분할하였다.",
    "회사는 모듈 제작업을 영위해 왔다.",
    "회사는 모듈 제작업을 영위해 왔습니다.",
    "회사는 모듈 제작업을 영위해 온 기업이다.",
    "회사는 모듈 제작업을 경영해 오고 있으며 설비를 갖추고 있다.",
])
def test_설립목적만으로_계속사업의_실행이력을_만들지_않는다(claim):
    assert scope_problem(claim, {"a": PURPOSE}) == SCOPE_CONDITION_UNBOUND


@pytest.mark.parametrize("activity", [
    "회사는 설립 이후 모듈 제작업을 영위해 왔다.",
    "회사는 모듈 제작업을 영위해 왔으며 설비를 갖추고 있다.",
    "회사는 모듈 제작업 서비스를 제공해 왔습니다.",
    "회사는 모듈 제작업을 영위해 오고 있으며 설비를 갖추고 있다.",
])
def test_원문이_실제계속사업을_명시한_경우_보존한다(activity):
    claim = "회사는 모듈 제작업을 영위해 왔다."
    assert scope_problem(claim, {"a": PURPOSE, "b": activity}) == ""


@pytest.mark.parametrize("activity", [
    "고객은 모듈 제작업을 영위해 왔다.",
    "회사는 운송업을 영위해 왔다.",
    "회사는 모듈 제작업을 영위해 왔지만 현재 중단하였다.",
    "회사는 모듈 제작업을 영위해 왔다고 가정한다.",
])
def test_다른주체_사업_중단_가정을_계속사업의_근거로_빌리지_않는다(activity):
    claim = "회사는 모듈 제작업을 영위해 왔다."
    assert scope_problem(claim, {"a": PURPOSE, "b": activity}) == SCOPE_CONDITION_UNBOUND


def test_계속이력_뒤_종료된_사업을_현재실행으로_승격하지_않는다():
    source = PURPOSE + " 회사는 모듈 제작업을 영위해 왔다. 현재 해당 사업을 중단하였다."
    claim = "회사는 현재 모듈 제작업을 영위해 오고 있다."
    assert scope_problem(claim, {"a": source}) == SCOPE_CONDITION_UNBOUND


def test_명시과거의_계속이력은_현재주장이_아니다():
    claim = "회사는 과거 모듈 제작업을 영위해 왔다."
    source = PURPOSE + " " + claim + " 현재 해당 사업을 중단하였다."
    assert scope_problem(claim, {"a": source}) == ""


def test_과거이력과_같은문장의_명시현재주장을_구분한다():
    claim = "회사는 과거 모듈 제작업을 영위해 왔으며 현재 모듈 제작업을 영위하고 있다."
    assert scope_problem(claim, {"a": PURPOSE}) == SCOPE_CONDITION_UNBOUND


@pytest.mark.parametrize("join", ["왔고 현재", "왔으나 지금", "왔다고 하며 현재도"])
def test_과거이력_뒤_명시현재절이_이력표시에_가려지지_않는다(join):
    claim = f"회사는 과거 모듈 제작업을 영위해 {join} 모듈 제작업을 영위하고 있다."
    assert scope_problem(claim, {"a": PURPOSE}) == SCOPE_CONDITION_UNBOUND


def test_과거사업과_다른_현재사업의_지원근거를_구분한다():
    claim = "회사는 과거 모듈 제작업을 영위해 왔고 현재 설비공사업을 영위하고 있다."
    source = {"a": PURPOSE, "b": "회사는 현재 설비공사업을 영위하고 있다."}
    assert scope_problem(claim, source) == ""
