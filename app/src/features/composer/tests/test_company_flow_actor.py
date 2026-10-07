"""회사행위 칸은 고객행동을 빌리지 않고 정상 구매·제공·생략 표현을 보존한다."""
import json

import pytest

from src.features.composer.role_binding import company_flow_actor_problem
from src.features.composer.role_binding_constants import ROLE_BINDING_ACTOR_BOUNDARY
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, FlowRow
from src.features.composer.verify import _apply_grounding, verify_report
from src.features.composer.diagram_check import check_diagrams


@pytest.mark.parametrize("source,action", [
    ("회사는 고객이 자료를 구독할 수 있도록 시스템을 구축하였다.", "자료 구독"),
    ("당사는 사용자가 장비를 구매하도록 온라인 창구를 운영한다.", "장비 구매"),
    ("회사는 서비스를 중개하고 고객은 서비스를 구독한다.", "서비스 구독"),
    ("고객은 서비스를 구독한다.", "서비스 구독"),
    ("회사는 서비스를 구독하지 않는다.", "서비스 구독"),
    ("당사는 고객에게 장비를 판매하며 고객이 장비를 구매한다.", "장비 구매"),
])
def test_명시고객행위와목적능력을회사행위로올리지않는다(source, action):
    assert company_flow_actor_problem(("수요자", action, "온라인 채널"), {"1": source}) == ROLE_BINDING_ACTOR_BOUNDARY


@pytest.mark.parametrize("source,action", [
    ("회사는 외부 소프트웨어를 구독한다.", "외부 소프트웨어 구독"),
    ("당사는 원재료를 구매한다.", "원재료 구매"),
    ("회사는 고객에게 구독 서비스를 제공한다.", "구독 서비스 제공"),
    ("당사는 고객에게서 구독료를 수취한다.", "구독료 수취"),
    ("회사는 서비스를 중개하고 고객은 서비스를 구독한다.", "서비스 중개"),
    ("회사는 고객이 자료를 구독할 수 있도록 구독 시스템을 구축하였다.", "구독 시스템 구축"),
    ("회사는 외부 서비스를 구독하고 고객은 별도 서비스를 구매한다.", "외부 서비스 구독"),
    ("회사는 자료를 판매한다. 외부 소프트웨어를 구독한다.", "외부 소프트웨어 구독"),
    ("외부 소프트웨어 구독 업무를 수행한다.", "외부 소프트웨어 구독"),
    ("가람상사는 외부 소프트웨어를 구독한다.", "외부 소프트웨어 구독"),
    ("회사는 구매하는 고객에게 장비를 공급한다.", "장비 공급"),
    ("회사는 고객이 요청한 장비를 공급한다.", "장비 공급"),
    ("회사는 고객이 외부 서비스를 구독할 수 있게 돕는다. 회사는 외부 서비스를 구독한다.", "외부 서비스 구독"),
    ("회사는 서비스 구독 가능성을 검토한다.", "서비스 구독 가능성 검토"),
    ("회사에 문의하면 서비스를 구독할 수 있다.", "문의 대응"),
])
def test_정상회사행위와닫힌구문밖의표현은의미검수에남긴다(source, action):
    assert not company_flow_actor_problem(("고객", action, "서비스 채널"), {"1": source})


def test_앞뒤칸과다른행의미인용원문은회사행위의증명이아니다():
    bad = "회사는 장비를 판매하고 고객은 장비를 구매한다."
    cells = ("회사 장비 구매", "장비 구매", "회사 구매창구")
    assert company_flow_actor_problem(cells, {"1": bad}) == ROLE_BINDING_ACTOR_BOUNDARY
    assert not company_flow_actor_problem(cells, {"1": bad, "2": "회사는 장비를 구매한다."})


@pytest.mark.parametrize("path", ("flat", "grouped", "legacy"))
@pytest.mark.parametrize("good", (False, True))
def test_모델참이같은회사행위가드를거친뒤에만봉인된다(path, good):
    text = "회사는 고객이 외부 서비스를 구독할 수 있도록 접속 시스템을 운영한다."
    text += " 회사는 외부 서비스를 구독한다." if good else ""
    source = CollectedFragment("1", "사업내용", text)
    uncited = CollectedFragment("2", "사업내용", "회사는 외부 서비스를 구독한다.")
    row = FlowRow(("개인 사용자", "외부 서비스 구독", "접속 시스템"), ("1",))
    original = ComposedReport((ComposedSection("operations_partners", (), flow_rows=(row,)),))
    diagnostics = []
    def reviewer(_):
        return json.dumps({"판정": [{"번호": 1, "장": "operations_partners", "근거": ["1"],
                                     "결과": "참", "검증근거": {}}]}, ensure_ascii=False)
    if path == "flat":
        raw = reviewer("")
        verdict = _apply_grounding(raw, {1: "참"}, {1: (" / ".join(row.cells), {"1": text})},
                                  diagnostic_contexts={1: ("operations_partners", "도식", "")},
                                  flow_cells_by_number={1: row.cells}, diagnostics=diagnostics)
        assert (verdict[1] == "참") == good
    elif path == "legacy":
        checked, _ = check_diagrams(original, (source, uncited), reviewer, diagnostics=diagnostics)
        assert len(checked.sections[0].flow_rows) == int(good)
        if good:
            assert checked.sections[0].flow_rows[0].review_binding is not None
    else:
        checked = verify_report(original, (source, uncited), None, reviewer, diagnostics=diagnostics,
                                allowed_fragment_ids_by_section={"operations_partners": frozenset({"1", "2"})})
        assert len(checked.sections[0].flow_rows) == int(good)
        if good:
            assert checked.sections[0].flow_rows[0].review_binding is not None
    assert original.sections[0].flow_rows == (row,) and source.text == text


def test_회사행위검사는다른장이나산문으로확대하지않는다():
    text = "고객은 서비스를 구독한다."
    cells = ("고객", "서비스 구독", "공급 경로")
    assert _apply_grounding("{}", {1: "참"}, {1: (text, {"1": text})},
                            diagnostic_contexts={1: ("operations_partners", "본문", text)})[1] == "참"
    assert _apply_grounding("{}", {1: "참"}, {1: (" / ".join(cells), {"1": text})},
                            diagnostic_contexts={1: ("past_changes", "도식", "")},
                            flow_cells_by_number={1: cells})[1] == "참"
