from __future__ import annotations

from typing import Any

import pytest

from src.features.company_performance.logic import build_three_year_table
from src.features.pipeline.canonical_report import _table_facts
from src.features.pipeline.port import ReportSection
from src.features.provenance.sources import Source, SourceKind, evidence_text_hash
from src.features.report_standard.publish import _numeric_problems


_DECEMBER_PERIODS = (
    "2025.01.01 ~ 2025.12.31",
    "2024.01.01 ~ 2024.12.31",
    "2023.01.01 ~ 2023.12.31",
)
_MARCH_PERIODS = (
    "2024.04.01 ~ 2025.03.31",
    "2023.04.01 ~ 2024.03.31",
    "2022.04.01 ~ 2023.03.31",
)


def _row(
    account_id: str,
    account_nm: str,
    current: str,
    previous: str,
    before: str,
    *,
    fs_div: str = "CFS",
    periods: tuple[str, str, str] = _DECEMBER_PERIODS,
    bsns_year: str = "2025",
    reprt_code: str = "11011",
    currency: str = "KRW",
    sj_div: str = "IS",
) -> dict[str, Any]:
    return {
        "fs_div": fs_div,
        "sj_div": sj_div,
        "account_id": account_id,
        "account_nm": account_nm,
        "bsns_year": bsns_year,
        "reprt_code": reprt_code,
        "currency": currency,
        "thstrm_dt": periods[0],
        "thstrm_amount": current,
        "frmtrm_dt": periods[1],
        "frmtrm_amount": previous,
        "bfefrmtrm_dt": periods[2],
        "bfefrmtrm_amount": before,
    }


def _financials(
    *,
    periods: tuple[str, str, str] = _DECEMBER_PERIODS,
    scope: str = "CFS",
    revenue: tuple[str, str, str] = (
        "821850000000",
        "601790000000",
        "566500000000",
    ),
    operating_income: tuple[str, str, str] = (
        "155250000000",
        "128260000000",
        "169440000000",
    ),
) -> dict[str, Any]:
    return {
        "status": "000",
        "list": [
            _row(
                "ifrs-full_Revenue",
                "매출액",
                *revenue,
                fs_div=scope,
                periods=periods,
            ),
            _row(
                "dart_OperatingIncomeLoss",
                "영업이익",
                *operating_income,
                fs_div=scope,
                periods=periods,
            ),
            _row(
                "ifrs-full_ProfitLoss",
                "당기순이익",
                "160560000000",
                "97710000000",
                "105020000000",
                fs_div=scope,
                periods=periods,
            ),
        ],
    }


def test_DART_연결_원값을_행은_사업연도_열은_지표인_표로_옮긴다() -> None:
    payload = _financials()
    payload["list"].append(
        _row("ifrs-full_Revenue", "매출액", "1", "2", "3", fs_div="OFS")
    )

    table = build_three_year_table(payload, cite="조각 9·재무")

    assert table is not None
    assert table.headers == ["사업연도", "매출액", "영업이익", "당기순이익"]
    assert [row[0] for row in table.rows] == ["2025", "2024", "2023"]
    # 공개 표는 억원 표시만, 원 단위는 내부 검산 행에만 둔다.
    assert table.rows[0] == ["2025", "8,219", "1,553", "1,606"]
    assert table.raw_rows[0] == [
        "2025",
        "821,850,000,000",
        "155,250,000,000",
        "160,560,000,000",
    ]
    assert table.scale_divisor == "100000000"
    assert table.scale_places == 0
    assert table.display_unit == "억원"
    assert not any("821,850,000,000" in cell for row in table.rows for cell in row)
    assert table.cite == "조각 9·재무"
    assert table.numeric is True
    assert "연결" in table.caption
    assert "결산월: 십이월" in table.caption
    assert "단위: 억원" in table.caption


def test_비십이월_결산은_기간_종료연도를_FY로_쓰고_실제_결산월을_보존한다() -> None:
    table = build_three_year_table(
        _financials(periods=_MARCH_PERIODS), cite="조각 9·재무"
    )

    assert table is not None
    # 첫 기간의 시작연도 2024가 아니라 종료연도인 FY2025여야 한다.
    assert [row[0] for row in table.rows] == ["2025", "2024", "2023"]
    assert "결산월: 삼월" in table.caption
    assert "십이월" not in table.caption


def test_억원_표시는_양수와_음수의_절반에서_ROUND_HALF_UP한다() -> None:
    table = build_three_year_table(
        _financials(
            revenue=("50000000", "-50000000", "49999999"),
            operating_income=("149999999", "-149999999", "0"),
        ),
        cite="조각 9·재무",
    )

    assert table is not None
    assert [row[1] for row in table.rows] == ["1", "-1", "0"]
    assert [row[2] for row in table.rows] == ["1", "-1", "0"]
    assert [row[1] for row in table.raw_rows] == [
        "50,000,000",
        "-50,000,000",
        "49,999,999",
    ]


def test_원값과_억원_표시값이_FactRecord_numeric_checks까지_결속된다() -> None:
    table = build_three_year_table(_financials(), cite="조각 9·재무")
    assert table is not None
    first_claim = f"{table.caption}: " + " | ".join(table.rows[0])
    actual_payload = table.evidence_rows[0]
    source = Source(
        number=9,
        kind=SourceKind.FILING,
        label="주요계정 전자공시",
        disclosed_at="2026-03-20",
        source_id="dart-financial-2025",
        title="2025 사업보고서 주요계정",
        publisher="테스트 주식회사",
        host="dart.fss.or.kr",
        url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260320000001",
        document_id="20260320000001",
        location="단일회사 주요계정 API",
        source_type="공식 공시",
        fact_status="actual",
        evidence_hashes=[evidence_text_hash(actual_payload)],
    )
    section = ReportSection(
        cell="past_changes",
        title="3개년 주요 변화와 실행",
        tables=[table],
    )

    _tables, facts = _table_facts("테스트 주식회사", section, {9: source})

    assert len(facts) == 3
    assert evidence_text_hash(first_claim) not in source.evidence_hashes
    first = facts[0]
    assert first.fiscal_year == 2025
    assert first.raw_value == (
        "821,850,000,000 | 155,250,000,000 | 160,560,000,000"
    )
    assert first.display_value == "8,219 | 1,553 | 1,606"
    assert first.numeric_checks == [
        "821,850,000,000|100000000|0|8,219",
        "155,250,000,000|100000000|0|1,553",
        "160,560,000,000|100000000|0|1,606",
    ]
    assert first.state_evidence == actual_payload
    assert _numeric_problems(first) == []


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda payload: payload["list"].__setitem__(
                0,
                {
                    **payload["list"][0],
                    "bfefrmtrm_amount": "",
                },
            ),
            id="missing-amount",
        ),
        pytest.param(
            lambda payload: payload["list"].__setitem__(
                0,
                {
                    **payload["list"][0],
                    "frmtrm_dt": "2023.01.01 ~ 2023.12.31",
                },
            ),
            id="non-consecutive-period",
        ),
        pytest.param(
            lambda payload: payload["list"].__setitem__(
                1,
                {
                    **payload["list"][1],
                    "thstrm_dt": "2024.04.01 ~ 2025.03.31",
                    "frmtrm_dt": "2023.04.01 ~ 2024.03.31",
                    "bfefrmtrm_dt": "2022.04.01 ~ 2023.03.31",
                },
            ),
            id="mixed-closing-month",
        ),
        pytest.param(
            lambda payload: payload["list"].__setitem__(
                0,
                {
                    **payload["list"][0],
                    "bsns_year": "2024",
                },
            ),
            id="business-year-mismatch",
        ),
        pytest.param(
            lambda payload: payload["list"].__setitem__(
                1,
                {
                    **payload["list"][1],
                    "currency": "USD",
                },
            ),
            id="non-won-currency",
        ),
        pytest.param(
            lambda payload: payload["list"].__setitem__(
                0,
                {
                    **payload["list"][0],
                    "thstrm_amount": "82,1850,000000",
                },
            ),
            id="malformed-original-amount",
        ),
    ],
)
def test_불완전하거나_비교불가능한_기간과_원값은_표를_만들지_않는다(
    mutate: Any,
) -> None:
    payload = _financials()
    mutate(payload)

    assert build_three_year_table(payload, cite="조각 9·재무") is None


def test_분기와_반기_기간은_완료_FY로_승격하지_않는다() -> None:
    quarterly_periods = (
        "2025.01.01 ~ 2025.03.31",
        "2024.01.01 ~ 2024.03.31",
        "2023.01.01 ~ 2023.03.31",
    )
    payload = _financials(periods=quarterly_periods)
    for row in payload["list"]:
        row["reprt_code"] = "11013"

    assert build_three_year_table(payload, cite="조각 9·재무") is None


def test_연결행이_있으면_불완전한_연결을_별도값으로_메우지_않는다() -> None:
    payload = {
        "list": [
            _row("ifrs-full_Revenue", "매출액", "100", "90", "80"),
            _row(
                "ifrs-full_Revenue",
                "매출액",
                "200",
                "190",
                "180",
                fs_div="OFS",
            ),
            _row(
                "dart_OperatingIncomeLoss",
                "영업이익",
                "20",
                "19",
                "18",
                fs_div="OFS",
            ),
        ]
    }

    assert build_three_year_table(payload, cite="조각 9·재무") is None


def test_연결행이_전혀_없을_때만_별도표를_만든다() -> None:
    table = build_three_year_table(
        _financials(scope="OFS"), cite="조각 9·재무"
    )

    assert table is not None
    assert "별도" in table.caption


def _계정_뺀_자료(*빼는_이름: str) -> dict:
    """정상 자료에서 특정 계정 «행 자체»를 뺀다 — 업종 차이를 흉내 낸다.

    은행 손익계산서에는 「매출액」에 해당하는 계정이 «아예 없다»(실측: 우리은행
    17개 행에서 영업이익·당기순이익만 나왔다). 값이 깨진 것과는 다른 상황이다.
    """
    자료 = _financials()
    자료["list"] = [
        row
        for row in 자료["list"]
        if not any(이름 in str(row.get("account_nm") or "") for 이름 in 빼는_이름)
    ]
    return 자료


def test_매출액이_없는_업종도_남은_지표로_표를_만든다() -> None:
    """★ 제품 결정 ① — 없는 지표를 지어내지 말고 있는 것으로 만든다.

    실측: 우리은행은 매출액 계정이 없어 3개년 실적표가 통째로 사라졌고,
    그 여파로 표지 실적 박스·4장 표·변화 요약 띠가 «전부» 빠졌다.
    ⚠️ 이 시험이 깨지면 은행·보험 같은 업종에서 다시 표가 사라진다.
    """
    표 = build_three_year_table(_계정_뺀_자료("매출액"), cite="조각 1·재무")

    assert 표 is not None, "★ 매출액이 없다는 이유로 표를 통째로 버렸다"
    assert 표.headers[0] == "사업연도"
    assert "매출액" not in 표.headers, "★ 없는 지표를 표에 지어냈다"
    assert 표.headers[1:] == ["영업이익", "당기순이익"], (
        f"★ 남은 지표가 그대로 실려야 한다: {표.headers}"
    )


def test_지표가_하나뿐이면_표를_만들지_않는다(caplog) -> None:
    """★ 안전선 — 열이 하나면 «비교표»가 아니라 숫자 나열이다.

    ⚠️ 이 시험이 깨지면 관문이 사실상 사라진 것이다.
    """
    import logging

    한개만 = _계정_뺀_자료("매출액", "영업이익")

    with caplog.at_level(logging.WARNING):
        assert build_three_year_table(한개만, cite="조각 1·재무") is None

    경고 = [r.getMessage() for r in caplog.records if "3개년 실적표" in r.getMessage()]
    assert 경고, "★ 표를 못 만든 이유가 로그에 없다"
    assert "1개뿐" in 경고[0], f"★ 몇 개였는지 안 적혔다: {경고[0]}"


def test_계정은_있는데_값이_깨졌으면_여전히_표를_막는다(caplog) -> None:
    """★ 안전선 — 「업종이라 없다」와 「있는데 깨졌다」는 다르다.

    앞은 남은 지표로 표를 만들어도 되지만, 뒤는 자료 결함이라 예전처럼
    표 전체를 막아야 한다. 둘을 뭉치면 «깨진 값을 조용히 빼고» 표를 그린다.
    """
    import logging

    깨진_매출액 = _financials()
    for row in 깨진_매출액["list"]:
        if "매출액" in str(row.get("account_nm") or ""):
            row["thstrm_amount"] = "숫자아님"

    with caplog.at_level(logging.WARNING):
        assert build_three_year_table(깨진_매출액, cite="조각 1·재무") is None

    경고 = [r.getMessage() for r in caplog.records if "3개년 실적표" in r.getMessage()]
    assert 경고, "★ 막은 이유가 로그에 없다"
    assert "읽을 수 없는" in 경고[0], f"★ 사유가 «자료 결함»으로 안 적혔다: {경고[0]}"


def test_필수_두_지표나_공식_범위가_없으면_표를_만들지_않는다() -> None:
    one_metric = {
        "list": [_row("ifrs-full_Revenue", "매출액", "100", "90", "80")]
    }
    unknown_scope = _financials()
    for row in unknown_scope["list"]:
        row["fs_div"] = ""

    assert build_three_year_table(one_metric, cite="조각 1·재무") is None
    assert build_three_year_table(unknown_scope, cite="조각 1·재무") is None
