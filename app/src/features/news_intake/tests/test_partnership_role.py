"""수집 전 역할 변경으로 원문·좌표·허용쌍의 의미를 함께 보존한다."""
from src.features.news_intake.tests.test_claim_role_slots import _collect, _excerpt
from src.features.news_intake.partnership_role import partnership_role_parts
from src.features.news_intake.tests.test_collection import COMPANY
import pytest
from src.shared.report_generation.models import exact_text_sha256
from src.shared.report_evidence.partnership_scope_constants import (
    PARTNERSHIP_SLOT, OPERATING_ROLE_SLOT, PARTNERSHIP_REROUTED, PARTNERSHIP_EXCLUDED,
)

SOLO = "가나다전자가 기업 80곳과 금융사 20곳의 사업보고서를 전수 조사해 결과를 공개했다."
JOINT = "가나다전자는 새봄대학과 함께 기업 80곳의 사업보고서를 전수 조사해 결과를 공개했다."


def _run(text):
    return _collect("가나다전자는 산업설비 제조 사업을 운영한다. " + text,
                    _excerpt(text, section="operations_partners", slot=PARTNERSHIP_SLOT,
                             topic="operations"))


def test_단독조사를_작성전에_운영역할로_옮기며_원문_지문과_좌표를_보존한다():
    result = _run(SOLO)
    (fragment,) = result.fragments
    assert fragment.supported_claim_slots == (OPERATING_ROLE_SLOT,)
    assert fragment.section_ids == ("operations_partners",)
    assert fragment.text == SOLO and fragment.text_sha256 == exact_text_sha256(SOLO)
    assert fragment.span_end - fragment.span_start == len(SOLO)
    assert result.diagnostics["주장역할조정"] == {PARTNERSHIP_REROUTED: 1}


def test_정상공동조사를_제휴칸에_보존한다():
    (fragment,) = _run(JOINT).fragments
    assert fragment.supported_claim_slots == (PARTNERSHIP_SLOT,)
    assert fragment.text == JOINT


def test_다른회사의_조사나_주어없는_조사를_대상회사_운영으로_바꾸지_않는다():
    for text in ("새봄대학은 기업 80곳의 사업보고서를 전수 조사했다.",
                 "기업 80곳의 사업보고서를 전수 조사해 결과를 공개했다."):
        result = _run(text)
        assert result.fragments == ()


def test_관계없는_협약이_섞인_범위는_통째로_운영칸으로_추정하지_않는다():
    result = _run(SOLO + " 가나다전자는 새봄대학과 협약을 맺었다.")
    assert result.fragments == ()
    assert result.diagnostics["주장역할조정"] == {PARTNERSHIP_EXCLUDED: 1}


def test_조사대상_통계가_이어져도_원문전체를_그대로_보존한다():
    text = SOLO + " 조사 대상 기업의 이용률 중앙값은 12%였다."
    (fragment,) = _run(text).fragments
    assert fragment.text == text and fragment.supported_claim_slots == (OPERATING_ROLE_SLOT,)


@pytest.mark.parametrize("tail", ["실시할 예정이다", "실시할 계획이다", "실시하지 않았다",
                                  "실시하지 못했다", "진행할 경우에만 공개한다",
                                  "하지 않았다", "할 예정이다"])
def test_미수행_조사를_운영의_새_허용쌍으로_승격하지_않는다(tail):
    text = "가나다전자는 기업 80곳의 사업보고서 전수 조사를 " + tail + "."
    assert partnership_role_parts(text, claim_slot=PARTNERSHIP_SLOT, company=COMPANY,
                                  temporal_status="completed") == ()


def test_명시_계획_상태는_원문_동작이_있어도_단독운영_칸으로_옮기지_않는다():
    assert partnership_role_parts(SOLO, claim_slot=PARTNERSHIP_SLOT, company=COMPANY,
                                  temporal_status="planned") == ()
    assert partnership_role_parts(JOINT, claim_slot=PARTNERSHIP_SLOT, company=COMPANY,
                                  temporal_status="planned") is None


def test_수집입구에서도_계획과_부정의_단독조사는_운영칸을_만들지_않는다():
    planned = "가나다전자는 기업 80곳의 사업보고서 전수 조사를 실시할 예정이라고 밝혔다."
    result = _collect("가나다전자는 산업설비 제조 사업을 운영한다. " + planned,
        _excerpt(planned, section="operations_partners", slot=PARTNERSHIP_SLOT,
                 kind="company_plan", temporal="planned", topic="operations"))
    assert not result.fragments
    assert result.diagnostics["주장역할조정"] == {PARTNERSHIP_EXCLUDED: 1}
    negative = "가나다전자는 기업 80곳의 사업보고서 전수 조사를 실시하지 않았다."
    assert not _run(negative).fragments


def test_현재조사와_별도활동의_계획_부정_혼합은_보존한다():
    for extra in ("가나다전자는 내년에 새 설비를 설치할 예정이다.",
                  "가나다전자는 새 설비를 설치하지 않았다."):
        text = SOLO + " " + extra
        parts = partnership_role_parts(text, claim_slot=PARTNERSHIP_SLOT, company=COMPANY,
                                       temporal_status="completed")
        assert len(parts) == 1 and parts[0].claim_slot == OPERATING_ROLE_SLOT
