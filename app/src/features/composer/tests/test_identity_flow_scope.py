"""공식 자기정의 칸의 자기 인용과 일반 도식·법적 업종 보존을 검증한다."""
import hashlib
import json

import pytest

from src.features.composer.diagram_check import check_diagrams
from src.features.composer.identity_flow_scope import identity_flow_scope_problem
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, FlowRow
from src.features.composer.verify import verify_report


SOURCE = "회사는 포털 및 기타 인터넷정보 매개 서비스업을 영위한다."
GOOD = ("포털 및 기타 인터넷정보 매개 서비스업", "인터넷정보 매개 서비스업", "정보 중개 사업자로 읽을 수 있다")
BAD = (GOOD[0], "경제 뉴스 및 분석 콘텐츠 제공", GOOD[2])


@pytest.mark.parametrize("cells", (BAD, ("경제 전문 미디어", GOOD[1], GOOD[2]),
                                  (GOOD[0], "금융 데이터 제공", GOOD[2])))
def test_공식칸의미인용표현은_해석칸이나다른후보에서빌리지않는다(cells):
    assert identity_flow_scope_problem(cells, {"1": SOURCE}) == "scope_condition_unbound"


@pytest.mark.parametrize("cells,source", (
    (GOOD, SOURCE),
    (("포털·인터넷정보 매개 서비스업", "포털", "법적 업종의 해석"), SOURCE),
    (("인터넷정보매개서비스업이다", "포털을", "뉴스 매체라는 해석"), SOURCE),
    (("공식 자기정의: 포털", "사업 범위: 인터넷정보 매개 서비스업", "이 보고서의 해석: 뉴스 전달 매체"), SOURCE),
    (("공식 자기정의 : 포털", "사업 범위 : 인터넷정보 매개 서비스업", "해석"), SOURCE),
    (("공식자기정의： 포털", "사업범위： 인터넷정보 매개 서비스업", "해석"), SOURCE),
    (("디지털 콘텐츠 사업", "콘텐츠(기사·사진·분석자료), 구독, IR", "여러 판매 창구"),
     "회사는 디지털 콘텐츠 사업을 한다. 콘텐츠로 기사, 사진, 분석자료를 판매하며 구독 및 IR 사업을 한다."),
    (("온라인정보매개서비스업", "", "원문의 법적 자기정의"), "회사는 온라인 정보매개 서비스업을 영위한다."),
    (("IR 서비스", "IR", "기업 고객과의 관계"), "회사는 IR 서비스를 제공한다."),
    (("센서 제조업", "지능형 센서 제조", "공급 구조의 해석"),
     "공식 업종은 센서 제조업이다. 회사는 지능형 센서를 제조하여 공급한다."),
    (("콘텐츠 제공업", "분석 콘텐츠 제공", "정보 판매 구조"),
     "공식 업종은 콘텐츠 제공업이다. 회사는 분석 콘텐츠를 제공한다."),
    (("전자장비업", "장비의 개발", "개발 역량의 해석"),
     "회사는 전자장비업을 영위하고 장비의 개발을 수행한다."),
))
def test_법적자기정의_공백_조사_목록_빈사업범위는원문그대로보존한다(cells, source):
    digest = hashlib.sha256(source.encode()).hexdigest()
    assert not identity_flow_scope_problem(cells, {"1": source})
    assert hashlib.sha256(source.encode()).hexdigest() == digest


def test_다른영문단어의일부를약어증명으로쓰지않는다():
    assert identity_flow_scope_problem(("정보매개업", "IR", "해석"), {"1": "정보매개업이다. first라는 단어가 있다."})


def test_두조각의반쪽표현을이어붙이지않는다():
    assert identity_flow_scope_problem(("정보매개업", "광고 매출", "해석"), {"1": "정보매개업과 광고", "2": "매출"})


@pytest.mark.parametrize("candidate,source", (
    ("요가", "회사는 정보서비스업을 영위하며 고객에게 요금을 청구한다."),
    ("요가", "회사는 정보서비스업을 영위하며 고객의 필요를 확인한다."),
    ("센서는", "회사는 정보서비스업을 영위하며 센서망을 관리한다."),
))
def test_조사처럼보이는명사끝을떼어다른단어의일부를빌리지않는다(candidate, source):
    assert identity_flow_scope_problem(("정보서비스업", candidate, "해석"), {"1": source})


def test_줄인어근이원문전체단어또는문법꼬리면보존한다():
    source = "회사는 정보서비스업을 영위하며 포털은 주요 서비스로 제공한다."
    assert not identity_flow_scope_problem(("정보서비스업이다", "포털을", "해석"), {"1": source})


def test_명사구축약이역할동의어나대상을추가하지않는다():
    source = "공식 업종은 센서 유통업이다. 회사는 센서를 유통하여 공급한다."
    assert identity_flow_scope_problem(("센서 유통업", "지능형 센서 제조", "공급 구조"), {"1": source})
    assert identity_flow_scope_problem(("센서 유통업", "센서 제조", "공급 구조"), {"1": source})


@pytest.mark.parametrize("path", ("legacy", "grouped"))
@pytest.mark.parametrize("supported", (False, True))
def test_두실제검수경로에서참판정도공식칸결속을거친다(path, supported):
    source = CollectedFragment("1", "사업내용", SOURCE)
    uncited = CollectedFragment("2", "사업내용", "회사는 경제 뉴스 및 분석 콘텐츠를 제공한다.")
    row = FlowRow(GOOD if supported else BAD, ("1",))
    original = ComposedReport((ComposedSection("identity", (), flow_rows=(row,)),))
    diagnostics = []
    def reviewer(_):
        return json.dumps({"판정": [{"번호": 1, "장": "identity", "근거": ["1"], "결과": "참", "검증근거": {}}]}, ensure_ascii=False)
    if path == "legacy":
        checked, _ = check_diagrams(original, (source, uncited), reviewer, diagnostics=diagnostics)
    else:
        checked = verify_report(original, (source, uncited), None, reviewer, diagnostics=diagnostics,
                                allowed_fragment_ids_by_section={"identity": frozenset({"1", "2"})})
    assert len(checked.sections[0].flow_rows) == int(supported)
    if supported:
        assert checked.sections[0].flow_rows[0].cells == row.cells
        assert checked.sections[0].flow_rows[0].citations == ("1",)
        assert checked.sections[0].flow_rows[0].review_binding is not None
    else:
        assert any(item.get("reason_code") == "scope_condition_unbound" for item in diagnostics)
    assert original.sections[0].flow_rows == (row,)
    assert source.text == SOURCE and uncited.text.endswith("제공한다.")


def test_도식외법적정의산문과다른장에는공식칸검사를강제하지않는다():
    from src.features.composer.verify import _apply_grounding
    text = "회사는 인터넷정보 매개 서비스업을 영위한다."
    values = _apply_grounding("{}", {1: "참"}, {1: (text, {"1": SOURCE})},
                             diagnostic_contexts={1: ("identity", "본문", text)})
    assert values[1] == "참"
