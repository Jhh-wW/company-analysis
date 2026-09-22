"""투자계획 표를 매출 구성표로 오인하지 않는지 «실측 원문»으로 못 박는다.

★ 무엇이 잘못됐나 (실측 2026-09-22, 대형 물류사 사업보고서·DART 접수번호
  20260318001205) — 「나. 투자계획」의 DART 표준 표

      구 분 | 기투자액(2024년·2025년) | 향후투자액(2026년) | 투자기대효과
      국내법인 공장 신ㆍ증설,보완투자 등  1,024,275  683,133  942,422  매출증대 및업무효율 증가
      해외법인                            116,556  189,274  285,135
      합 계                             1,140,831  872,407 1,227,557

  가 보고서에 「지역별 매출액 (2024년)」으로 실렸다. 세 가지가 겹친 결과다.
    ① 태그를 지운 평문에서 기대효과 칸 「매출증대」가 다음 행 이름 앞에 붙어
       금액 전용 경로의 매출 관문(「매출」)을 통과했다.
    ② 「국내법인·해외법인」이 지역 축 힌트(「국내」)가 됐다.
    ③ 기투자액 2024년 열의 행 합(1,024,275 + 116,556)이 합계 1,140,831과 맞았다.
  같은 회사의 전년도 공시(20250317001025)에서도 같은 표가 같은 모양으로 올라왔다.

★ 고친 것 — 머리말·행 이름에 투자·설비투자(CAPEX) 문맥 어휘
  (``constants.NON_REVENUE_CONTEXT_WORDS``)가 있으면 「매출」이 있어도 매출표가
  아니다. 비중 있는 v2·다개년·금액 전용 세 경로가 같은 관문을 쓴다.

★ 픽스처 ``fixtures/glovis_investment_plan_not_revenue.txt`` 는 운영과 같은
  변환(태그→공백, 공백 접기 — ``analysis_engine/tools/survey_audit_reports.read_filing_text``)
  으로 만든 평문에서 표 앞 600자부터 뒤 문장 끝까지 잘라 온 «원문 바이트 그대로»다
  (평문 오프셋 33,056~33,967 · 911자). 아래 sha256 으로 바뀌지 않았음을 확인한다.

⚠️ 「가공」이라고 적은 원문은 실제 공시가 아니라 모양만 옮긴 것이다. 회사 이름은
  「가나다전자·가나다증권」이고 숫자는 손으로 적은 리터럴이다.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.core import revenue_table_switch as switch
from src.features.revenuemix.constants import (
    NON_REVENUE_CONTEXT_WORDS,
    REJECT_NOT_REVENUE,
)
from src.features.revenuemix.logic import (
    build,
    build_multi_year,
    build_multi_year_with_diagnostics,
    build_with_diagnostics,
)
from src.shared.revenue_table_provenance import (
    REVENUE_AXIS_PRODUCT,
    REVENUE_AXIS_REGION,
    revenue_row_evidence_matches,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_FIXTURE_NAME = "glovis_investment_plan_not_revenue"
#: 잘라 온 평문 조각의 지문. 픽스처를 «손으로 고치면» 이 시험이 먼저 깨진다 —
#: 재구성본으로 통과한 시험은 실제 조각에서 미발동한 전례가 있다.
_FIXTURE_SHA256 = "c5e6de1da3db53ffac2076d81c20ccb80d2783853279ffeaff54038c4164587d"


def 픽스처() -> str:
    return (_FIXTURES / f"{_FIXTURE_NAME}.txt").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _fresh_process_revenue_table_switch():
    switch._reset_process_revenue_table_switch_for_tests()  # noqa: SLF001
    yield
    switch._reset_process_revenue_table_switch_for_tests()  # noqa: SLF001


@pytest.fixture
def v2_켬(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(switch.REVENUE_TABLE_V2_ENV_NAME, "1")


@pytest.fixture
def v2_끔(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(switch.REVENUE_TABLE_V2_ENV_NAME, raising=False)


# ══════════════════════════════════════════════════════════
# ① 실측 원문 — 수정 전에는 지역별 매출표가 «만들어졌다»
# ══════════════════════════════════════════════════════════


def test_픽스처는_잘라_온_평문_그대로다() -> None:
    원문 = 픽스처()

    assert hashlib.sha256(원문.encode("utf-8")).hexdigest() == _FIXTURE_SHA256
    # 표의 정체를 밝히는 열 이름과, 오인의 원인이 된 기대효과 칸이 둘 다 있다.
    assert "구 분 기투자액 향후투자액 투자기대효과" in 원문
    assert "매출증대 및업무효율 증가 해외법인" in 원문


def test_투자계획표는_지역별_매출로_올라오지_않는다(v2_켬: None) -> None:
    """★★ 수정 전 실측 — 아래 표가 «지역별 매출액 (2024년)»으로 나갔다.

        국내법인 공장 신ㆍ증설,보완투자 등  1,024,275
        매출증대 및업무효율 증가 해외법인     116,556
        합 계                             1,140,831

    독자는 물류사가 공장 증설로 1조 원을 «벌었다»고 읽는다. 없는 표가 낫다.
    """

    표들, 진단 = build_with_diagnostics(픽스처(), cite="[1]")

    assert 표들 == []
    assert 진단["채택_표_수"] == 0
    # 후보를 «보긴 봤고», 지역 축에서 「매출이 아니다」로 떨어뜨렸다.
    assert 진단["단위표시_수"] >= 1
    assert REJECT_NOT_REVENUE in 진단["축별_탈락사유"][REVENUE_AXIS_REGION]


def test_투자계획표는_다개년_변화표로도_올라오지_않는다(v2_켬: None) -> None:
    """머리말에 2024·2025·2026년이 나란히 있어도 «구성 변화»가 아니다."""

    assert build_multi_year(픽스처()) == []


def test_투자계획표는_스위치를_꺼도_표가_없다(v2_끔: None) -> None:
    assert build(픽스처()) == []


# ══════════════════════════════════════════════════════════
# ② 가공 변형 — 같은 표가 «다른 모양»으로 와도 막는다
# ══════════════════════════════════════════════════════════

#: 가공 — 비중 열이 붙은 투자계획 표. 기대효과 칸 「매출증대」가 행 이름에 섞이고
#: 금액 합·비중 합이 모두 맞아, 관문이 없으면 v2가 지역표로 세운다.
비중있는_투자표 = (
    "가나다전자 나. 투자계획 (단위 : 백만원) 구 분 기투자액 비 중 향후투자액 투자기대효과 "
    "국내법인 공장 증설 6,000 60.0% 매출증대 해외법인 4,000 40.0% 합 계 10,000 100.0%"
)

#: 가공 — 연도별 비중이 붙은 투자계획 표(다개년 경로용).
다개년_투자표 = (
    "가나다전자 나. 투자계획 (단위 : 백만원) 구 분 2024년 기투자액 비중 2025년 기투자액 비중 투자기대효과 "
    "국내법인 6,000 60.0% 5,000 50.0% 매출증대 해외법인 4,000 40.0% 5,000 50.0% "
    "합 계 10,000 100.0% 10,000 100.0%"
)

#: 가공 — 지역이 열 머리말에 있는 가로형인데 표제가 투자표라고 말한다.
가로형_투자표 = (
    "가나다전자 투자계획 (단위 : 천원) 지역 지역 합계 국내 아시아 북미 투자기대효과 "
    "매출액 6,000 1,200 400 7,600 끝."
)


def test_비중_열이_있는_투자표도_v2가_세우지_않는다(v2_켬: None) -> None:
    표들, 진단 = build_with_diagnostics(비중있는_투자표)

    assert 표들 == []
    assert 진단["탈락_사유"] == {"매출 표현 없음": 1}     # 다른 이유가 아니라 «이» 관문이다


def test_연도별_비중이_있는_투자표도_다개년_경로가_세우지_않는다(v2_켬: None) -> None:
    표들, 진단 = build_multi_year_with_diagnostics(다개년_투자표)

    assert 표들 == []
    assert 진단["탈락_사유"].get("매출 표현 없음", 0) >= 1


def test_가로형_표제가_투자표면_이름표가_매출액이어도_세우지_않는다(v2_켬: None) -> None:
    표들, 진단 = build_with_diagnostics(가로형_투자표)

    assert 표들 == []
    assert REJECT_NOT_REVENUE in 진단["축별_탈락사유"][REVENUE_AXIS_REGION]


@pytest.mark.parametrize("표기", ("CAPEX", "Capex", "capex"))
def test_영문_CAPEX_표기는_대소문자를_가리지_않는다(표기: str, v2_켬: None) -> None:
    가공 = (
        f"가나다전자 {표기} 집행 현황 (단위 : 억원) 구 분 제57기 "
        "국내 매출 설비 3,000 해외 매출 설비 1,000 합계 4,000"
    )

    표들, 진단 = build_with_diagnostics(가공)

    assert 표들 == []
    assert REJECT_NOT_REVENUE in 진단["축별_탈락사유"][REVENUE_AXIS_REGION]


# ══════════════════════════════════════════════════════════
# ③ 정당한 매출표는 «그대로» 산다 — 관문이 넓어지지 않았다는 증거
# ══════════════════════════════════════════════════════════

#: 가공 — 사업부문 이름에 「투자」가 들어가는 증권사형 매출표. 「투자」 한 글자로
#: 막으면 이 표가 사라진다.
투자가_이름인_매출표 = (
    "가나다증권 가. 영업실적 (단위 : 백만원) 구 분 매출액 비 중 "
    "투자중개 부문 6,000 60.0% 투자은행 부문 3,000 30.0% 자산관리 부문 1,000 10.0% "
    "합계 10,000 100.0%"
)

#: 가공 — 표 «앞 문장»에만 투자계획이 언급되는 금액 전용 지역표. 표제 되짚기는
#: 마침표에서 멈추므로 앞 문장의 말이 표를 죽이지 않는다.
투자_문장_뒤의_지역표 = (
    "가나다전자 향후 투자계획은 다음과 같습니다. 주요 지역별 매출 현황 (단위 : 억원) "
    "구 분 제57기 제56기 내수 국내 30,000 28,000 수출 일본 12,000 11,000 계 42,000 39,000"
)


def test_사업부문_이름에_투자가_들어간_매출표는_그대로_세운다(v2_켬: None) -> None:
    원문 = 투자가_이름인_매출표

    표들 = build(원문, cite="[2]")

    assert [표["axis"] for 표 in 표들] == [REVENUE_AXIS_PRODUCT]
    assert 표들[0]["rows"] == [
        ["투자중개 부문", "6,000", "60.0%"],
        ["투자은행 부문", "3,000", "30.0%"],
        ["자산관리 부문", "1,000", "10.0%"],
        ["합계", "10,000", "100.0%"],
    ]
    # 행마다 원문 좌표가 붙고, 검증기가 그 좌표로 다시 잘라 인정한다(provenance 유지).
    assert len(표들[0]["evidence_rows"]) == len(표들[0]["rows"])
    for 행, 원행, 근거 in zip(표들[0]["rows"], 표들[0]["raw_rows"], 표들[0]["evidence_rows"]):
        assert revenue_row_evidence_matches(
            근거,
            cited_source_text=원문,
            filing_text=원문,
            headers=표들[0]["headers"],
            public_row=행,
            raw_row=원행,
        )


def test_표_앞_문장의_투자계획_언급은_지역표를_죽이지_않는다(v2_켬: None) -> None:
    표들 = build(투자_문장_뒤의_지역표)

    assert [표["axis"] for 표 in 표들] == [REVENUE_AXIS_REGION]
    assert 표들[0]["rows"] == [
        ["내수 국내", "30,000"],
        ["수출 일본", "12,000"],
        ["계", "42,000"],
    ]


def test_문맥_어휘_목록은_닫혀_있고_투자_한_글자는_없다() -> None:
    """★ 「투자」 한 글자를 넣는 순간 증권사 매출표가 사라진다 — 목록의 경계다."""

    assert "투자" not in NON_REVENUE_CONTEXT_WORDS
    assert all(word and word == word.strip() for word in NON_REVENUE_CONTEXT_WORDS)
    assert len(set(NON_REVENUE_CONTEXT_WORDS)) == len(NON_REVENUE_CONTEXT_WORDS)
    # 실측 열 이름 세 개는 반드시 걸린다.
    assert any(word in "기투자액" for word in NON_REVENUE_CONTEXT_WORDS)
    assert any(word in "향후투자액" for word in NON_REVENUE_CONTEXT_WORDS)
    assert "투자기대효과" in NON_REVENUE_CONTEXT_WORDS
