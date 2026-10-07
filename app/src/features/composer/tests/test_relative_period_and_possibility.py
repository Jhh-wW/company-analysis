"""상대 과거 실적·미래 약속·명사 가능성의 시제와 정상 혼합 문맥을 대조한다."""
import pytest

from src.features.composer.grounding import grounding_problem, grounding_requirements
from src.features.composer.grounding_constants import GROUNDING_MISSING, TIME_KEY
from src.features.composer.modality_guard import modality_problem
from src.features.composer.modality_constants import MODALITY_PLAN_ASSERTED, MODALITY_POSSIBILITY_ASSERTED


@pytest.mark.parametrize("period", ["지난해", "작년", "전년도", "전년", "전기", "지난 분기", "지난 사업연도"])
@pytest.mark.parametrize("separator", [" ", ""])
def test_상대과거_종결실적_뒤_별개현재문장이_원활동을_현재화하지_않는다(period, separator):
    source = (f"가람기업은 {period} 물류네트워크 확장과 지역별 판매전략 실행으로 실적을 거두었습니다."
              f"{separator}현재 가람기업은 고객상담센터를 운영하고 있습니다.")
    candidate = "가람기업은 물류네트워크 확장과 지역별 판매전략 실행을 추진하고 있습니다."
    assert TIME_KEY in grounding_requirements(candidate, (source,))
    assert grounding_problem(candidate, {"공시": source}, {}) == GROUNDING_MISSING
    assert TIME_KEY not in grounding_requirements("가람기업은 고객상담센터를 운영하고 있다.", (source,))


@pytest.mark.parametrize("source, candidate", [
    ("가람기업은 지난해 물류네트워크 확장을 완료했다.", "가람기업은 지난해 물류네트워크 확장을 완료했다."),
    ("가람기업은 지난해부터 물류네트워크 확장을 추진하고 있다.", "가람기업은 물류네트워크 확장을 추진하고 있다."),
    ("가람기업은 지난해 물류네트워크 확장을 완료했다.현재 가람기업은 물류네트워크 확장을 추진하고 있다.",
     "현재 가람기업은 물류네트워크 확장을 추진하고 있다."),
    ("나래기업은 지난해 물류네트워크 확장을 완료했다.현재 가람기업은 물류네트워크 확장을 추진하고 있다.",
     "가람기업은 물류네트워크 확장을 추진하고 있다."),
    ("가람기업은 지난해 물류네트워크 확장을 완료했다.", "가람기업은 물류네트워크 확장을 추진할 계획이다."),
    ("가람기업은 지난해 물류네트워크 확장을 검토했다.", "가람기업은 고객상담센터를 운영하고 있다."),
])
def test_과거형_실제현재_다른주어_계획_무관활동을_보존한다(source, candidate):
    assert TIME_KEY not in grounding_requirements(candidate, (source,))


def test_일반사업_관계서술이_부문명하나를_공유한_과거실적에_흡수되지_않는다():
    source = (
        "일반기계 및 금형제작 부문의 매출 비중은 크지 않으나 타이어 부문 투자와 밀접한 연관성이 있습니다."
        "일반기계제작부문은 전년 대비 매출액 증가와 실적을 달성했으며, 이익을 기록했습니다."
    )
    candidate = "일반기계 및 금형제작 부문의 매출 비중은 크지 않으나 타이어 부문 투자와 밀접한 연관성을 갖고 있다."
    assert TIME_KEY not in grounding_requirements(candidate, (source,))


@pytest.mark.parametrize("assertion", ["추진한다", "추진하고 있다", "추진했습니다", "추진했다고 밝혔다"])
def test_하겠습니다_약속을_동일활동의_현재나완료로_바꾸면_거절한다(assertion):
    source = "가람기업은 해외성장전략을 추진하겠습니다. 고객상담센터를 운영하고 있습니다."
    candidate = f"가람기업은 해외성장전략을 {assertion}."
    assert modality_problem(candidate, {"공시": source}) == MODALITY_PLAN_ASSERTED
    assert grounding_problem(candidate, {"공시": source}, {}) == MODALITY_PLAN_ASSERTED


@pytest.mark.parametrize("candidate, source", [
    ("가람기업은 해외성장전략을 추진하겠습니다.", "가람기업은 해외성장전략을 추진하겠습니다."),
    ("가람기업은 지난해 '해외성장전략을 추진하겠습니다'라고 말했다.", "가람기업은 지난해 '해외성장전략을 추진하겠습니다'라고 말했다."),
    ("가람기업은 국내성장전략을 추진한다.", "가람기업은 해외성장전략을 추진하겠습니다. 국내성장전략을 추진한다."),
    ("가람기업은 제품을 판매한다.", "가람기업은 '성장하겠습니다'라는 이름의 제품을 판매한다."),
    ("가람기업은 해외성장전략을 추진한다고 밝혔다.", "가람기업은 해외성장전략을 추진한다고 밝혔다."),
])
def test_약속인용_명사고유명_다른활동과_실제행위를_보존한다(candidate, source):
    assert modality_problem(candidate, {"공시": source}) == ""


def test_다른회사_실적은_약속의_현재화_직접근거가_되지_않는다():
    candidate = "가람기업은 해외성장전략을 추진한다."
    source = "가람기업은 해외성장전략을 추진하겠습니다."
    assert modality_problem(candidate, {"계획": source, "실적": "나래기업은 해외성장전략을 추진한다."}) == MODALITY_PLAN_ASSERTED
    assert modality_problem(candidate, {"계획": source, "실적": candidate}) == ""


@pytest.mark.parametrize("object_", ["부품 통합화/모듈화", "부품 통합화 · 모듈화", "문서 자동화"])
@pytest.mark.parametrize("tail", ["추진한다", "추진하고 있습니다", "실현하고 있다", "실현했다"])
def test_명사가능성만으로_동일대상의_실제행위를_추가하지_않는다(object_, tail):
    source = f"새로운 시스템을 구성하여 {object_} 가능"
    candidate = f"가람기업은 {object_}를 {tail}."
    assert modality_problem(candidate, {"공시": source}) == MODALITY_POSSIBILITY_ASSERTED
    assert grounding_problem(candidate, {"공시": source}, {}) == MODALITY_POSSIBILITY_ASSERTED


@pytest.mark.parametrize("candidate, source", [
    ("가람기업은 부품 통합화/모듈화가 가능하다.", "부품 통합화/모듈화 가능"),
    ("가람기업은 부품 통합화/모듈화를 추진할 계획이다.", "부품 통합화/모듈화 가능"),
    ("가람기업은 부품 통합화/모듈화를 추진하겠습니다.", "부품 통합화/모듈화 가능"),
    ("가람기업은 문서 자동화를 추진한다.", "부품 통합화/모듈화 가능. 가람기업은 문서 자동화를 추진한다."),
    ("가람기업은 냉각부품 모듈화를 추진한다.", "전력부품 모듈화 가능. 가람기업은 냉각부품 모듈화를 추진한다."),
    ("가람기업은 부품 통합화/모듈화를 추진한다.", "부품 통합화/모듈화 가능. 가람기업은 부품 통합화/모듈화를 추진한다."),
    ("가람기업은 고객상담센터를 운영한다.", "자동화 가능성이 높다. 가람기업은 고객상담센터를 운영한다."),
])
def test_가능성_계획_다른대상_현재직접근거를_보존한다(candidate, source):
    assert modality_problem(candidate, {"공시": source}) == ""


def test_다른주어의_현재행위로_가능성을_실제화하지_않는다():
    candidate = "가람기업은 문서 자동화를 추진한다."
    possible = "가람기업은 문서 자동화가 가능합니다."
    assert modality_problem(candidate, {"가능": possible, "다른회사": "나래기업은 문서 자동화를 추진한다."}) == MODALITY_POSSIBILITY_ASSERTED
    assert modality_problem(candidate, {"가능": possible, "직접": candidate}) == ""


def test_기존모델참도_가능행위모순을_공개할_수_없다():
    from src.features.composer.grounding import constrain_verdicts
    candidate = "가람기업은 부품 통합화/모듈화를 추진하고 있다."
    result, problems = constrain_verdicts(
        '{"판정":[{"번호":1,"결과":"참","검증근거":{}}]}', {1: "참"},
        {1: (candidate, {"공시": "부품 통합화/모듈화 가능"})},
    )
    assert problems[1] == MODALITY_POSSIBILITY_ASSERTED
    assert result[1] != "참"
