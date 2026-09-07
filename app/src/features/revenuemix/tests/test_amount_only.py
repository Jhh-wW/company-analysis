"""비중 열이 «없는» 매출 구성표(금액만 세로형·가로형)를 못 박는다.

★ 무엇을 지키나 — 세 가지다.
  ① 「비중이 없으니 표를 통째로 버린다」를 그만둔다. 공시가 금액을 적어 뒀는데
    안 싣는 것도 사실을 빠뜨리는 것이다.
  ② 그래도 **비중을 우리가 계산해 채우지 않는다.** 열 자체를 만들지 않는다.
  ③ 가로형 전치에서 이름과 금액이 **한 칸도 밀리지 않는다.** 한 칸 밀리면
    국내 매출이 「해외」 이름을 달고 나간다 — 표가 없는 것보다 나쁘다.

⚠️ 아래 자료는 실제 공시가 아니라 **모양만 옮긴 가공 원문**이다. 회사 이름은
  「가나다전자」이고 지역 이름은 어느 나라 공시에나 나오는 일반 어휘다.
  기대값은 자료를 보고 손으로 적은 리터럴이다 — 코드가 만든 값을 코드로
  다시 비교하면 둘이 함께 틀려도 초록이 된다.
"""

from __future__ import annotations

import json

import pytest

from src.core import revenue_table_switch as switch
from src.features.revenuemix.constants import (
    REJECT_AXIS_UNKNOWN,
    REJECT_HORIZONTAL_NAMES_UNMATCHED,
    REJECT_HORIZONTAL_NO_TOTAL,
    REJECT_NOT_REVENUE,
    REJECT_REASON_CODES,
    REJECT_SUM_MISMATCH,
)
from src.features.revenuemix.logic import build, build_with_diagnostics
from src.shared.revenue_table_provenance import (
    REVENUE_AXIS_PRODUCT,
    REVENUE_AXIS_REGION,
    canonical_json,
    revenue_names_are_region_only,
    revenue_region_names_in,
    revenue_row_evidence_matches,
    revenue_table_axis_matches,
    revenue_table_section_id_from_caption,
    revenue_table_source_excerpt,
    revenue_text_axis,
    sha256_text,
)


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


# ── 가공 원문 ────────────────────────────────────────────────────────
#: ① 금액만 «세로형» 3개년. 비중 열이 없고 기간이 「제57기」식이라 연도가 없다.
세로형_3개년 = (
    "가나다전자 사업보고서 4. 매출 및 수주상황. "
    "(3) 주요 지역별 매출 현황 (단위 : 억원) 구 분 제57기 제56기 제55기 "
    "내수 국내 30,000 28,000 26,000 "
    "수출 일본 12,000 11,000 10,000 "
    "기타 국가 8,000 7,000 6,000 "
    "계 50,000 46,000 42,000 "
    "※ 별도 기준입니다."
)
#: ② 금액만 «세로형» 단년 제품표.
세로형_단년 = (
    "가나다전자 가. 매출 실적. "
    "(1) 연결 (단위 : 천원) 사업부문 매출유형 품 목 제32기 "
    "가전사업 제품 냉장고 44,000 "
    "통신사업 제품 휴대전화 36,000 "
    "합계 80,000"
)
#: ③ «가로형» — 지역이 열 머리말에 있고 금액이 아래 한 줄에 이어진다.
#:   「해외」는 네 칸을 덮는 묶음 이름이라 열이 아니다.
가로형 = (
    "가나다전자 6. 영업부문 정보. "
    "지역에 대한 공시 당기 (단위 : 천원) 지역 지역 합계 "
    "국내(주1) 해외 아시아 북미 유럽 기타 "
    "매출액 6,000 1,200 400 300 100 8,000 "
    "전기 (단위 : 천원) 지역 지역 합계 국내 해외 아시아 북미 유럽 기타 "
    "매출액 5,000 1,000 300 200 100 6,600 끝."
)


# ══════════════════════════════════════════════════════════
# ① 금액만 세로형
# ══════════════════════════════════════════════════════════


def test_금액만_세로형_3개년은_당기_열로_단년_표를_만든다(v2_켬: None) -> None:
    """★ 기간이 셋이어도 «맨 앞(당기)» 한 열만 싣는다 — 옛 단년 규칙과 같다."""

    표들 = build(세로형_3개년, cite="[8]")

    assert [표["axis"] for 표 in 표들] == ["region"]
    assert 표들[0]["headers"] == ["구분", "매출액 (억원)"]
    assert 표들[0]["rows"] == [
        ["내수 국내", "30,000"],
        ["수출 일본", "12,000"],
        ["기타 국가", "8,000"],
        ["계", "50,000"],
    ]
    assert 표들[0]["cite"] == "[8]"


def test_첫_행_이름에서_기간_열의_꼬리를_떼어_낸다(v2_켬: None) -> None:
    """★★ 이름 캡처는 숫자를 못 물어서 「제57기」의 「기」만 남는다.

    떼지 않으면 첫 행 이름이 「기 내수 국내」가 된다 — 있지도 않은 구분이다.
    """

    첫_행 = build(세로형_3개년)[0]["rows"][0]

    assert 첫_행[0] == "내수 국내"
    assert not 첫_행[0].startswith("기 ")


def test_금액만_세로형_단년_제품표를_만든다(v2_켬: None) -> None:
    표들 = build(세로형_단년)

    assert [표["axis"] for 표 in 표들] == ["product"]
    assert 표들[0]["headers"] == ["구분", "매출액 (천원)"]
    assert 표들[0]["rows"] == [
        ["가전사업 제품 냉장고", "44,000"],
        ["통신사업 제품 휴대전화", "36,000"],
        ["합계", "80,000"],
    ]


def test_금액_합이_합계와_다르면_금액만_표도_버린다(v2_켬: None) -> None:
    """★★ 비중이 없어도 검산은 그대로다 — 행 합 == 합계."""

    어긋난_표 = (
        "가나다전자 가. 매출 실적. "
        "(1) 연결 (단위 : 천원) 사업부문 매출유형 품 목 제32기 "
        "가전사업 제품 냉장고 44,000 통신사업 제품 휴대전화 36,000 합계 90,000"
    )

    표들, 진단 = build_with_diagnostics(어긋난_표)

    assert 표들 == []
    assert REJECT_SUM_MISMATCH in 진단["축별_탈락사유"][REVENUE_AXIS_PRODUCT]


def test_합계_행이_없으면_금액만_표를_만들지_않는다(v2_켬: None) -> None:
    합계없음 = (
        "가나다전자 가. 매출 실적. "
        "(1) 연결 (단위 : 천원) 사업부문 매출유형 품 목 제32기 "
        "가전사업 제품 냉장고 44,000 통신사업 제품 휴대전화 36,000 끝."
    )

    assert build(합계없음) == []


@pytest.mark.parametrize("표제", ("제품별 매출채권 현황", "제품별 매출원가 현황"))
def test_매출액이_아닌_일반_2열표를_금액전용_매출표로_오인하지_않는다(
    표제: str,
    v2_켬: None,
) -> None:
    일반표 = (
        f"가나다전자 {표제} (단위 : 천원) 구 분 제32기 "
        "제품가 6,000 제품나 4,000 합계 10,000"
    )

    표들, 진단 = build_with_diagnostics(일반표)

    assert 표들 == []
    assert REJECT_NOT_REVENUE in 진단["축별_탈락사유"][REVENUE_AXIS_PRODUCT]


# ══════════════════════════════════════════════════════════
# ② 가로형 — 전치
# ══════════════════════════════════════════════════════════


def test_가로형은_열_머리말을_행으로_전치한다(v2_켬: None) -> None:
    """★★ 묶음 이름 「해외」를 세면 금액이 한 칸씩 밀린다. 국내 6,000이
    「해외」 이름을 달고 나가는 것이 이 시험이 막는 사고다."""

    표들 = build(가로형)

    assert [표["axis"] for 표 in 표들] == ["region"]
    assert 표들[0]["headers"] == ["구분", "매출액 (천원)"]
    # 「(주1)」은 주석 표시라 공개 이름에서 지워진다(정규화 계약). 봉인된
    # 원문 칸은 「국내(주1)」 그대로다 — 아래 결속 시험이 그것을 확인한다.
    assert 표들[0]["rows"] == [
        ["국내", "6,000"],
        ["아시아", "1,200"],
        ["북미", "400"],
        ["유럽", "300"],
        ["기타", "100"],
        ["합계", "8,000"],
    ]


def test_가로형은_당기_표를_고르고_전기_표를_고르지_않는다(v2_켬: None) -> None:
    """★ 같은 축 후보가 둘이면 «먼저 나온» 것 — DART는 당기를 먼저 싣는다."""

    표들 = build(가로형)

    assert 표들[0]["rows"][-1] == ["합계", "8,000"]     # 당기 합계
    assert 표들[0]["rows"][0] == ["국내", "6,000"]


def test_합계_열이_없는_가로형은_싣지_않는다(v2_켬: None) -> None:
    """★★ 검산할 수 없는 표는 만들지 않는다."""

    합계없는_가로형 = (
        "가나다전자 6. 영업부문 정보. "
        "지역에 대한 공시 당기 (단위 : 천원) 지역 국내 해외 아시아 북미 "
        "매출액 6,000 1,200 400 끝."
    )

    표들, 진단 = build_with_diagnostics(합계없는_가로형)

    assert 표들 == []
    assert REJECT_HORIZONTAL_NO_TOTAL in 진단["축별_탈락사유"][REVENUE_AXIS_REGION]


def test_이름_수와_금액_수가_다르면_맞춰_보지_않는다(v2_켬: None) -> None:
    """★★ 이름 없는 열(내부거래조정)이 섞이면 전치를 «포기»한다.

    억지로 맞추면 한 칸 밀린 표가 나간다 — 틀린 표는 없는 표보다 나쁘다.
    """

    이름이_모자란_가로형 = (
        "가나다전자 6. 영업부문 정보. "
        "지역에 대한 공시 당기 (단위 : 백만원) "
        "연결실체 내 내부거래조정 지역 지역 합계 국내 외국 미주 유럽 중국 "
        "순매출액 10 20 30 40 0 100 끝."
    )

    표들, 진단 = build_with_diagnostics(이름이_모자란_가로형)

    assert 표들 == []
    assert (
        REJECT_HORIZONTAL_NAMES_UNMATCHED
        in 진단["축별_탈락사유"][REVENUE_AXIS_REGION]
    )


# ══════════════════════════════════════════════════════════
# ③ 축 판정 — 지역 어휘·새 표제
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "머리말, 기대",
    (
        (
            "지역 지역 합계 국내(주1) 해외 아시아 북미 유럽 기타",
            ("국내(주1)", "아시아", "북미", "유럽", "기타"),
        ),
        # 잎이 뒤에 없으면 「외국」이 곧 한 열이다.
        ("본사 소재지 국가 외국 지역 합계", ("본사 소재지 국가", "외국")),
        ("지역 지역 합계 국내 해외 아시아 북미 유럽 기타", ("국내", "아시아", "북미", "유럽", "기타")),
    ),
)
def test_묶음_이름은_열로_세지_않는다(머리말: str, 기대: tuple[str, ...]) -> None:
    assert tuple(이름.text for 이름 in revenue_region_names_in(머리말)) == 기대


@pytest.mark.parametrize(
    "이름들, 기대",
    (
        (("국내", "일본", "기타 국가"), True),
        (("내수 국내", "수출 미주"), True),
        (("국내", "냉장고"), False),
        ((), False),
    ),
)
def test_이름이_전부_지역_말이면_지역_축이다(
    이름들: tuple[str, ...], 기대: bool
) -> None:
    assert revenue_names_are_region_only(이름들) is 기대


@pytest.mark.parametrize(
    "표제",
    (
        "지역에 대한 공시",
        "지역별 정보",
        "주요 지역별 매출 현황",
        "지역별 매출 현황",
        "지역별 매출",
    ),
)
def test_새로_더한_지역_표제에서_지역_축을_읽는다(표제: str) -> None:
    """★ 넓힌 표제 목록은 v1의 표 «경계»를 건드리지 않는 별도 목록이다."""

    assert revenue_text_axis(f"{표제} 당기 (단위 : 천원)") == REVENUE_AXIS_REGION


@pytest.mark.parametrize(
    "표제", ("주요 지역별 매출 현황", "지역별 매출 현황", "지역별 매출")
)
def test_새_지역_표제_아래_금액만_표를_싣는다(표제: str, v2_켬: None) -> None:
    원문 = (
        f"가나다전자 4. 판매 현황. {표제} (단위 : 천원) 구 분 제32기 "
        "국내 6,000 일본 3,000 기타 국가 1,000 합계 10,000"
    )

    표들 = build(원문)

    assert [표["axis"] for 표 in 표들] == ["region"]
    assert 표들[0]["rows"][0] == ["국내", "6,000"]


def test_축을_못_읽으면_금액만_표를_만들지_않는다(v2_켬: None) -> None:
    축없음 = (
        "가나다전자 가. 매출 실적. "
        "(1) 연결 (단위 : 천원) 구 분 제32기 "
        "가나 44,000 다라 36,000 합계 80,000"
    )

    표들, 진단 = build_with_diagnostics(축없음)

    assert 표들 == []
    assert REJECT_AXIS_UNKNOWN in 진단["축별_탈락사유"][REVENUE_AXIS_REGION]


# ══════════════════════════════════════════════════════════
# ④ 캡션·각주·소유 장
# ══════════════════════════════════════════════════════════


def test_캡션에_비중을_적지_않았다는_말이_붙는다(v2_켬: None) -> None:
    """★ 독자가 「비중 칸이 왜 없나」를 화면에서 바로 알 수 있어야 한다."""

    지역표 = build(세로형_3개년)[0]
    제품표 = build(세로형_단년)[0]

    assert 지역표["caption"] == (
        "어디서 번 돈인가 — 지역별 매출액 · 비중은 공시에 없어 적지 않았습니다"
    )
    assert 제품표["caption"] == (
        "무엇을 팔아 번 돈인가 — 제품·서비스별 매출액 · 비중은 공시에 없어 적지 않았습니다"
    )


def test_금액만_표도_축이_소유한_장으로_간다(v2_켬: None) -> None:
    assert revenue_table_section_id_from_caption(
        build(세로형_3개년)[0]["caption"]
    ) == "business_model"
    assert revenue_table_section_id_from_caption(
        build(세로형_단년)[0]["caption"]
    ) == "portfolio"


# ══════════════════════════════════════════════════════════
# ⑤ 행마다 원문 결속 — 봉인이 다시 잘려야 한다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("원문", (세로형_3개년, 세로형_단년, 가로형))
def test_금액만_표의_모든_행이_원문으로_다시_검산된다(
    원문: str, v2_켬: None
) -> None:
    표 = build(원문, cite="조각 1·매출수주")[0]
    조각 = revenue_table_source_excerpt(표["evidence_rows"])

    assert 조각 and 조각 in 원문
    assert revenue_table_axis_matches(
        axis=표["axis"],
        caption=표["caption"],
        evidence_rows=표["evidence_rows"],
        cited_source_text=조각,
    )
    for index, 근거 in enumerate(표["evidence_rows"]):
        assert revenue_row_evidence_matches(
            근거,
            cited_source_text=조각,
            filing_text=원문,
            headers=표["headers"],
            public_row=표["rows"][index],
            raw_row=표["raw_rows"][index],
            expected_selected_index=index,
            expected_row_count=len(표["rows"]) - 1,
        ), f"{index}번 행"


def test_봉인된_이름_칸이_원문_그대로다(v2_켬: None) -> None:
    """★ 「기 내수 국내」에서 꼬리를 뗀 뒤에도 봉인 좌표가 원문과 같아야 한다."""

    표 = build(세로형_3개년)[0]
    첫_근거 = json.loads(표["evidence_rows"][0])
    이름 = 첫_근거["row"]["name"]

    assert 이름["value"] == "내수 국내"
    assert 세로형_3개년[이름["start"]:이름["end"]] == "내수 국내"
    assert 첫_근거["table"]["shape"] == "amount-only-vertical"
    assert json.loads(build(가로형)[0]["evidence_rows"][0])["table"]["shape"] == (
        "amount-only-horizontal"
    )


def test_금액만_표에는_비중_칸_자체가_없다(v2_켬: None) -> None:
    """★★ 계약은 그대로다 — 우리가 비중을 계산해 채우지 않는다."""

    for 원문 in (세로형_3개년, 세로형_단년, 가로형):
        표 = build(원문)[0]
        근거 = json.loads(표["evidence_rows"][0])
        assert len(표["headers"]) == 2
        assert "ratio" not in 근거["row"]
        assert not any("비중" in 이름 for 이름 in 근거["public_fields"])


@pytest.mark.parametrize("원문", (세로형_3개년, 가로형))
def test_다른_행의_금액_좌표를_바꿔_끼우면_검산이_막는다(
    원문: str, v2_켬: None
) -> None:
    """이름과 금액이 둘 다 원문에 있어도 같은 행·열의 칸이어야 한다."""

    표 = build(원문)[0]
    첫_근거 = json.loads(표["evidence_rows"][0])
    둘째_근거 = json.loads(표["evidence_rows"][1])
    바꾼_금액 = 둘째_근거["row"]["amount"]
    첫_근거["row"]["amount"] = 바꾼_금액
    첫_근거["public_fields"][표["headers"][1]] = 바꾼_금액["value"]
    위조_행 = [표["rows"][0][0], 바꾼_금액["value"]]

    assert not revenue_row_evidence_matches(
        canonical_json(첫_근거),
        cited_source_text=첫_근거["source"]["excerpt"],
        filing_text=원문,
        headers=표["headers"],
        public_row=위조_행,
        raw_row=위조_행,
        expected_selected_index=0,
        expected_row_count=len(표["rows"]) - 1,
    )


def test_세로형에서_다른_전체_행을_첫째_행으로_속이면_검산이_막는다(
    v2_켬: None,
) -> None:
    """이름·금액을 함께 옮겨도 원문 행 번호까지 바꿀 수는 없다."""

    표 = build(세로형_3개년)[0]
    첫_근거 = json.loads(표["evidence_rows"][0])
    둘째_근거 = json.loads(표["evidence_rows"][1])
    첫_근거["row"]["name"] = 둘째_근거["row"]["name"]
    첫_근거["row"]["amount"] = 둘째_근거["row"]["amount"]
    첫_근거["row"]["source_index"] = 둘째_근거["row"]["source_index"]
    위조_행 = list(표["rows"][1])
    첫_근거["public_fields"] = dict(zip(표["headers"], 위조_행))

    assert not revenue_row_evidence_matches(
        canonical_json(첫_근거),
        cited_source_text=첫_근거["source"]["excerpt"],
        filing_text=세로형_3개년,
        headers=표["headers"],
        public_row=위조_행,
        raw_row=위조_행,
        expected_selected_index=0,
        expected_row_count=len(표["rows"]) - 1,
    )


def test_세로형_이름_칸의_앞부분을_잘라내면_검산이_막는다(v2_켬: None) -> None:
    """「내수 국내」를 원문의 부분문자열인 「국내」로 축소할 수 없다."""

    표 = build(세로형_3개년)[0]
    근거 = json.loads(표["evidence_rows"][0])
    이름 = 근거["row"]["name"]
    잘라낼_길이 = 이름["value"].index(" ") + 1
    이름["value"] = 이름["value"][잘라낼_길이:]
    이름["start"] += 잘라낼_길이
    이름["excerpt_start"] += 잘라낼_길이
    이름["sha256"] = sha256_text(이름["value"])
    위조_행 = [이름["value"], 표["rows"][0][1]]
    근거["public_fields"] = dict(zip(표["headers"], 위조_행))

    assert not revenue_row_evidence_matches(
        canonical_json(근거),
        cited_source_text=근거["source"]["excerpt"],
        filing_text=세로형_3개년,
        headers=표["headers"],
        public_row=위조_행,
        raw_row=위조_행,
        expected_selected_index=0,
        expected_row_count=len(표["rows"]) - 1,
    )


def test_세로형_확장합계명의_앞부분을_잘라내면_검산이_막는다(
    v2_켬: None,
) -> None:
    """원문의 「영업수익합계」를 부분문자열 「합계」로 바꿔 부를 수 없다."""

    원문 = 세로형_단년.replace("합계 80,000", "영업수익합계 80,000")
    표 = build(원문)[0]
    근거 = json.loads(표["evidence_rows"][-1])
    이름 = 근거["row"]["name"]
    잘라낼_길이 = 이름["value"].index("합계")
    이름["value"] = 이름["value"][잘라낼_길이:]
    이름["start"] += 잘라낼_길이
    이름["excerpt_start"] += 잘라낼_길이
    이름["sha256"] = sha256_text(이름["value"])
    근거["table"]["total"]["name"] = dict(이름)
    위조_행 = ["합계", 표["rows"][-1][1]]
    근거["public_fields"] = dict(zip(표["headers"], 위조_행))

    assert not revenue_row_evidence_matches(
        canonical_json(근거),
        cited_source_text=근거["source"]["excerpt"],
        filing_text=원문,
        headers=표["headers"],
        public_row=위조_행,
        raw_row=위조_행,
        expected_selected_index=len(표["rows"]) - 1,
        expected_row_count=len(표["rows"]) - 1,
    )


# ══════════════════════════════════════════════════════════
# ⑥ 진단 — 표가 0개일 때 «왜»가 남는가
# ══════════════════════════════════════════════════════════


def test_진단은_축별로_닫힌_사유_코드만_남긴다(v2_켬: None) -> None:
    _표들, 진단 = build_with_diagnostics("표가 하나도 없는 글입니다.")

    assert set(진단["축별_탈락사유"]) == {REVENUE_AXIS_PRODUCT, REVENUE_AXIS_REGION}
    for 코드들 in 진단["축별_탈락사유"].values():
        assert 코드들 == sorted(코드들)
        assert set(코드들) <= set(REJECT_REASON_CODES)


def test_v1_진단에도_축별_칸이_있다(v2_끔: None) -> None:
    """★ 스위치를 꺼도 호출자가 같은 열쇠를 읽을 수 있어야 한다."""

    _표들, 진단 = build_with_diagnostics(세로형_3개년)

    assert 진단["경로"] == "v1"
    assert 진단["축별_탈락사유"] == {
        REVENUE_AXIS_PRODUCT: [],
        REVENUE_AXIS_REGION: [],
    }


def test_스위치를_끄면_금액만_표는_하나도_나오지_않는다(v2_끔: None) -> None:
    """★ 되돌림의 증거 — 새 모양은 «전부» 스위치 안쪽에 있다."""

    for 원문 in (세로형_3개년, 세로형_단년, 가로형):
        assert build(원문) == []
