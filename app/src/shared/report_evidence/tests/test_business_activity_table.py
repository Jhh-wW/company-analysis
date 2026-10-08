"""현재 자기 영업표와 프로젝트·과거·미실행·회계 문맥을 분리한다."""

from pathlib import Path

import pytest

from src.shared.report_evidence.business_activity_table import activity_table_item, activity_table_ranges


OWNER = "20. 공사의 개요\n당기와 전기 중 당사가 시공한 주요 도급공사의 내역은 다음과 같습니다."
CURRENT = "(3) 당기말과 전기말 현재 공사 내역입니다. ① 당 기"
TABLE = "구 분 | 도급금액 | 누적공사수익(*1) ; 정밀설비공사 현장 | 100 | 60 ; 기타 | 20 | 10"
TEXT = OWNER + "\n\n" + CURRENT + "\n\n(단위: 천원)\n\n" + TABLE


def test_종류별_당기_자기실적의_정확항목과_연속창():
    assert activity_table_item(TEXT) == "정밀설비공사"
    start, end = tuple(activity_table_ranges(TEXT))[0]
    assert TEXT[start:end] == TEXT
    assert "정밀설비공사" in TEXT[start:end]


@pytest.mark.parametrize("altered", (
    TEXT.replace("당사가", "고객사가"),
    TEXT.replace("당사가", "당사의 자회사가"),
    TEXT.replace("당기가", "전기가").replace("① 당 기", "② 전 기"),
    TEXT.replace("시공한", "시공할 예정인"),
    TEXT.replace("시공한", "시공한다고 설명한"),
    TEXT.replace("당기와 전기 중", "과거 당기와 전기 중"),
    TEXT.replace("주요 도급공사", "설립 목적의 주요 도급공사"),
    TEXT.replace("주요 도급공사", "분할 전 주요 도급공사"),
    TEXT.replace("주요 도급공사", "완료된 공사"),
    TEXT.replace("누적공사수익(*1)", "계약예정금액"),
    TEXT.replace("| 60", "| 0"),
    TEXT.replace("| 60", "| (60)"),
    TEXT.replace("정밀설비공사 현장", "완료 공사 이력"),
    TEXT.replace("정밀설비공사 현장", "내부 관리 공사"),
    TEXT.replace("정밀설비공사 현장", "고객사 정밀설비공사"),
    TEXT.replace("구 분 |", "공 사 명 |"),
    TEXT.replace("구 분 |", "고객명 |"),
    TEXT.replace("구 분 |", "회사명 |"),
    TEXT.replace("정밀설비공사 현장", "제품"),
    TEXT.replace(CURRENT, "21. 다른 사업\n" + CURRENT),
    TEXT.replace(CURRENT, CURRENT + " 계획"),
    TEXT.replace(CURRENT, CURRENT + " 자회사 실적"),
))
def test_주어_기간_실행_제공물_근거가_없으면_항목0(altered):
    assert activity_table_item(altered) == ""
    assert not tuple(activity_table_ranges(altered))


def test_현재_외부서비스_종류도_같은_명시관계를_요구한다():
    text = ("당기 중 당사가 제공한 서비스 내역입니다. ① 당기\n\n"
            "서비스종류 | 당기매출액 ; 회계 자문 서비스 | 30 ; 교육 서비스 | 20")
    assert activity_table_item(text) == "회계 자문 서비스"
    assert not activity_table_item(text.replace("회계 자문", "회계 관리").replace("교육 서비스", "내부 업무"))


def test_이전표의_당기표지는_다음_기간없는표에_대여되지_않는다():
    text = OWNER + "\n\n① 당기\n\n공 사 명 | 도급금액 ; 고객프로젝트 | 10\n\n" + TABLE
    assert not activity_table_item(text)


def test_모호한_복수_금액열_깨진행은_넘겨짚지_않는다():
    assert not activity_table_item(TEXT.replace("도급금액", "매출액"))
    assert not activity_table_item(TEXT.replace("| 60", "| 60 | 30"))


@pytest.mark.parametrize("text", (
    TEXT.replace("주요 도급공사의 내역은 다음과 같습니다.", "공사는 없다."),
    TEXT.replace("주요 도급공사의 내역은 다음과 같습니다.", "것이 아니다."),
    TEXT.replace(CURRENT, "다음은 고객의 당기 실적이다."),
    TEXT.replace(CURRENT, "다음은 발주사의 당기 실적이다."),
    TEXT.replace(CURRENT, "당기 중 새봄건설이 시공한 실적이다."),
    TEXT.replace(CURRENT, "(2) 다른 기업의 당기 사업 실적"),
    "다른기업의 사업 개요: " + TEXT,
))
def test_앞당사_설명의_명시부정과_뒤새소유_표제는_대여되지_않는다(text):
    assert not activity_table_item(text, "가온기업")


def test_같은회사_명시소유_표제는_다른회사로_바꾸지_않는다():
    assert activity_table_item("가온기업의 사업 개요: " + TEXT, "(주)가온기업") == "정밀설비공사"
    assert activity_table_item(TEXT.replace(CURRENT, "가온기업의 당기 사업 실적"), "가온기업") == "정밀설비공사"
    for lead in ("다음은", "아래는"):
        assert activity_table_item(TEXT.replace(CURRENT, f"{lead} 가온기업의 당기 사업 실적"), "가온기업") == "정밀설비공사"
        assert not activity_table_item(TEXT.replace(CURRENT, f"{lead} 다른기업의 당기 사업 실적"), "가온기업")


@pytest.mark.parametrize("status", ("완료", "예정", "미착공", "중단", "취소"))
def test_당기라고_쓴_완료_미실행_행도_양성으로_승격하지_않는다(status):
    table = f"구분 | 누적공사수익 | 상태 ; 정밀설비공사 현장 | 60 | {status}"
    assert not activity_table_item(TEXT.replace(TABLE, table))


def test_문맥_상한을_넘으면_과거사업_대체없이_빈값():
    text = OWNER + "\n\n" + "설명 " * 1500 + "\n\n" + CURRENT + "\n\n" + TABLE
    assert not activity_table_item(text)
    long_table = TABLE + " ; 기타 | 20 | 10" * 600
    assert not activity_table_item(TEXT.replace(TABLE, long_table))


def test_앱과_엔진_동일문법_배포경계만_다르다():
    root = Path(__file__).resolve().parents[5]
    app = root / "app/src/shared/report_evidence"
    engine = root / "analysis_engine/src/features/evidence_collection"
    assert (app / "business_activity_table_constants.py").read_text(encoding="utf-8") == (
        engine / "business_activity_table_constants.py").read_text(encoding="utf-8")
    assert (app / "business_activity_table.py").read_text(encoding="utf-8").replace(
        "src.shared.report_evidence", "features.evidence_collection") == (
        engine / "business_activity_table.py").read_text(encoding="utf-8")
