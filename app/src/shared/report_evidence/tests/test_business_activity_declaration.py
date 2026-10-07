"""현재 사업목록 선언과 사업목적·타사·내부업무를 구분한다."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.shared.report_evidence.business_activity import current_business_item
from src.shared.report_evidence.business_activity_declaration import declared_business_item


@pytest.mark.parametrize("text,item", (
    ("당사의 사업은 정밀부품 사업과 산업장비 사업으로 구성되어 있습니다.", "정밀부품 사업"),
    ("우리 회사의 핵심 사업은 법률 자문 사업과 교육 사업으로 구성되어 있다.", "법률 자문 사업"),
    ("당사의 사업은 회계 자문 사업과 기업 교육 사업으로 구성되어 있습니다.", "회계 자문 사업"),
    ("당사의 사업은 고객 대상 회계 처리 사업과 세무 자문 사업으로 구성되어 있습니다.", "고객 대상 회계 처리 사업"),
    ("당사의 사업은 3차원 영상 사업과 장비 임대 사업으로 구성되어 있습니다.", "3차원 영상 사업"),
    ("당사의 사업은 종속기업 대상 회계 자문 사업과 기업 교육 사업으로 구성되어 있습니다.", "종속기업 대상 회계 자문 사업"),
    ("당사의 사업은 2020년에 시작한 데이터 분석 사업과 장비 임대 사업으로 구성되어 있습니다.", "2020년에 시작한 데이터 분석 사업"),
    ("당사의 사업은 크게 이용자의 접근성을 전제로 한 온라인 광고 사업과 데이터 판매 사업으로 구성되어 있습니다.", "온라인 광고 사업"),
))
def test_현재_회사소유_구체목록의_첫사업만_원문선택(text, item):
    assert declared_business_item(text) == item
    assert item in text
    fragment = SimpleNamespace(text=text, location=f"0-{len(text)}", source_context_json="",
                               covered_slot_ids=("identity:business_definition",))
    assert current_business_item(fragment, "가온기업") == ""
    assert current_business_item(fragment, "가온기업", include_declaration=True) == item


@pytest.mark.parametrize("text", (
    "미디어 사업은 광고 사업과 콘텐츠 판매 사업으로 구성되어 있습니다.",
    "당사의 사업 목적은 정밀부품 사업과 장비 사업으로 구성되어 있습니다.",
    "당사의 사업은 정밀부품 사업과 장비 사업으로 구성될 예정입니다.",
    "당사의 사업은 향후 정밀부품 사업과 장비 사업으로 구성되어 있습니다.",
    "당사의 사업은 과거 정밀부품 사업과 장비 사업으로 구성되어 있습니다.",
    "당사의 사업은 고객사의 정밀부품 사업과 장비 사업으로 구성되어 있습니다.",
    "당사의 자회사 사업은 정밀부품 사업과 장비 사업으로 구성되어 있습니다.",
    "당사의 사업은 계열사의 정밀부품 사업과 장비 사업으로 구성되어 있습니다.",
    "당사의 사업은 회계 관리 사업과 법무 관리 사업으로 구성되어 있습니다.",
    "당사의 사업은 내부 관리 사업과 공시 관리 사업으로 구성되어 있습니다.",
    "당사의 사업은 임직원 교육 사업과 복리후생 사업으로 구성되어 있습니다.",
    "당사의 사업은 인수하는 경우 데이터 분석 사업과 장비 임대 사업으로 구성되어 있습니다.",
    "당사의 사업은 2020년 당시 데이터 분석 사업과 장비 임대 사업으로 구성되어 있습니다.",
    "당사의 사업은 종속기업의 데이터 분석 사업과 장비 임대 사업으로 구성되어 있습니다.",
    "당사의 사업은 인수 완료를 전제로 한 데이터 분석 사업과 장비 임대 사업으로 구성되어 있습니다.",
    "당사의 사업은 회계 처리 사업과 세무 자문 사업으로 구성되어 있습니다.",
    "당사의 사업은 고객 대상 회계 처리 사업과 내부 회계 처리 사업으로 구성되어 있습니다.",
    "당사의 사업은 내부고객 대상 회계 처리 사업과 세무 자문 사업으로 구성되어 있습니다.",
    "당사의 사업은 서비스 사업과 제품 사업으로 구성되어 있습니다.",
    "당사의 사업은 기타 부대사업과 지원 사업으로 구성되어 있습니다.",
    "당사의 사업은 정밀부품 사업과 장비 사업으로 구성되어 있다고 과거 보고서에서 설명했다.",
))
def test_정의_계획_타주체_내부관리_목록은_새앵커가_아니다(text):
    assert declared_business_item(text) == ""


def test_운영칸이나_제휴칸은_선언만으로_사업정의가_되지_않는다():
    text = "당사의 사업은 정밀부품 사업과 장비 사업으로 구성되어 있습니다."
    for slot in ("operations_partners:operating_role", "operations_partners:partnership"):
        fragment = SimpleNamespace(text=text, location=f"0-{len(text)}", source_context_json="",
                                   covered_slot_ids=(slot,))
        assert current_business_item(fragment, "가온기업", include_declaration=True) == ""


def test_앱과_엔진_문법은_배포경계만_다르다():
    root = Path(__file__).resolve().parents[5]
    app = root / "app/src/shared/report_evidence"
    engine = root / "analysis_engine/src/features/evidence_collection"
    assert (app / "business_activity_declaration_constants.py").read_text(encoding="utf-8") == (
        engine / "business_activity_declaration_constants.py").read_text(encoding="utf-8")
    assert (app / "business_activity_declaration.py").read_text(encoding="utf-8").replace(
        "src.shared.report_evidence", "features.evidence_collection") == (
        engine / "business_activity_declaration.py").read_text(encoding="utf-8")
