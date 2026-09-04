"""매출 구성 비중 뜯기를 못 박는다.

★ 여기서 지키는 것은 하나다 — **공시에 적힌 숫자를 그대로 옮긴다.**
  비중을 «우리가» 계산하는 순간 반올림 규칙이 공시와 달라져 합이 안 맞는다.

⚠️ 아래 원문 조각은 **하이브 2025 사업보고서에서 실제로 뜯어 온 것**이다
  (직접 확인). 지어낸 예시가 아니라 실제 모양이라 시험 가치가 있다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.features.revenuemix.constants import KNOWN_TABLE_HEADS
from src.features.revenuemix.logic import build, clean_name, find_block, parse_rows
from src.shared.revenue_table_provenance import (
    canonical_json,
    revenue_row_evidence_matches,
    revenue_table_axis_matches,
    revenue_table_source_excerpt,
)

#: ★ 저장소에 보관된 하이브 2025 사업보고서 실제 수집 조각을 그대로 쓴다.
_FIXTURE_PATH = (
    Path(__file__).resolve().parents[5]
    / "analysis_engine"
    / "data"
    / "pilot"
    / "fragments"
    / "실캡처-자사홈페이지-03.json"
)
제품별 = str(json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))["8"]["원문"])


# ══════════════════════════════════════════════════════════
# ① 이름 다듬기
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "원문, 기대",
    [
        ("음반/음원 음반, 음원 등", "음반/음원 음반, 음원 등"),
        ("매 출 액 비 중 공연 콘서트", "공연 콘서트"),
        ("광고, 출연료 (주2) 광고 수익", "광고, 출연료 광고 수익"),
        ("2025년 제21기 (당 기) 국내", "국내"),
    ],
)
def test_표_머리말_찌꺼기를_지운다(원문: str, 기대: str):
    """★ 표가 «한 줄로 눌린» 글이라 머리말이 다음 행 이름 앞에 그대로 붙는다."""
    assert clean_name(원문) == 기대


def test_긴_이름을_자르지_않는다():
    """★★ 실측 결함 — 26자에서 말줄임(…)으로 잘라 「MD 및 라이선싱 공식
    상품(MD), IP 라이…」처럼 무엇을 파는지 못 읽는 이름이 나왔다 (하이브 2025
    사업보고서 2장 실측). 제품 결정: 자르지 않고 화면·PDF가
    줄바꿈으로 흘려 받는다 — 코드는 «이름을 온전히 넘기는 것»까지만 책임진다.
    """
    이름 = "MD 및 라이선싱 공식 상품(MD), IP 라이선싱 등"
    assert len(이름) > 26          # 예전 상한(26자)보다 길다는 것부터 확인
    다듬은_이름 = clean_name(이름)

    assert 다듬은_이름 == 이름     # 안 잘리고 그대로 나온다
    assert "…" not in 다듬은_이름  # 말줄임표가 안 붙는다


def test_실제_원문에서도_긴_품목명이_안_잘린다():
    """clean_name 단위 시험을 넘어 build() 전체 경로로도 확인한다."""
    표들 = build(제품별)

    이름들 = [행[0] for 행 in 표들[0]["rows"]]
    assert "MD 및 라이선싱 공식 상품(MD), IP 라이선싱 등" in 이름들
    assert not any("…" in 이름 for 이름 in 이름들)


# ══════════════════════════════════════════════════════════
# ② 행 뜯기 — ★ 여기가 핵심
# ══════════════════════════════════════════════════════════


def test_당기_숫자만_가져온다():
    """★★ 한 행에 3개 연도가 나란히 있다. **맨 앞(당기)만** 써야 한다.

    ⚠️ 여기가 깨지면 **재작년 숫자를 올해 것처럼** 내보낸다 —
    이 기능에서 가장 나쁜 실패다.
    """
    _, block = find_block(제품별, ("제품별 매출액",))

    rows, total = parse_rows(block)

    assert rows[0][1] == "772,960"      # 2025년 값
    assert rows[0][2] == "29.17%"
    assert "860,962" not in [c for r in rows for c in r]   # 2024년 값이 섞이면 안 된다


def test_합계에서_멈춘다():
    """★ 안 멈추면 바로 뒤의 «다음 표»(지역별) 행까지 먹는다 — 실측으로 잡혔다."""
    _, block = find_block(제품별, ("제품별 매출액",))

    rows, total = parse_rows(block)

    이름들 = [r[0] for r in rows]
    assert "국내" not in " ".join(이름들)
    assert "아시아" not in " ".join(이름들)
    assert total is not None and total[1] == "2,649,870"


def test_소계는_버린다():
    """★ 소계까지 넣으면 비중을 다 더했을 때 200%가 된다."""
    _, block = find_block(제품별, ("지역별 매출액",))

    rows, _ = parse_rows(block)

    assert all("소계" not in r[0] for r in rows)


def test_연도는_머리말에서_읽는다():
    """★ 머리말을 그냥 잘라 버렸더니 뒤에 남은 「2023년」을 주워
    **2025년 숫자에 2023년 딱지**가 붙었다 (실측으로 잡힘).
    """
    표들 = build(제품별)

    assert "(2025년)" in 표들[0]["caption"]
    assert "2023" not in 표들[0]["caption"]


# ══════════════════════════════════════════════════════════
# ③ 통째로
# ══════════════════════════════════════════════════════════


def test_두_표를_만든다():
    표들 = build(제품별, cite="조각 8·매출수주")

    assert len(표들) == 2
    assert "제품·서비스별" in 표들[0]["caption"]
    assert "지역별" in 표들[1]["caption"]
    assert 표들[0]["cite"] == "조각 8·매출수주"


def test_비중을_우리가_계산하지_않는다():
    """★★ 공시가 적어 둔 값을 **그대로** 옮긴다.

    우리가 계산하면 반올림 규칙이 공시와 달라져 합이 100%가 안 맞는다.
    """
    표들 = build(제품별)
    비중들 = [r[2] for r in 표들[0]["rows"]]

    assert 비중들 == [
        "29.17%",
        "28.83%",
        "5.55%",
        "21.53%",
        "9.77%",
        "5.15%",
        "100.00%",
    ]


def test_실제_원문의_각_행에_손실없는_범위와_해시를_붙인다():
    표 = build(제품별, cite="[8]")[0]

    assert len(표["evidence_rows"]) == len(표["rows"])
    assert 표["raw_rows"] == 표["rows"]
    assert revenue_table_source_excerpt(표["evidence_rows"]) in 제품별
    first = json.loads(표["evidence_rows"][0])
    source = first["source"]
    assert 제품별[source["start"] : source["end"]] == source["excerpt"]
    assert first["table"]["header"]["text"].startswith("제품별 매출액")
    assert first["extractor"] == {"name": "revenuemix.regex", "version": "3"}
    assert first["table"]["axis"] == "product"
    assert first["row"]["selection"] == "first-current-period-pair"
    assert first["row"]["raw_fields"]["amount"]["value"] == "772,960"
    for row, raw_row, evidence in zip(
        표["rows"], 표["raw_rows"], 표["evidence_rows"]
    ):
        assert revenue_row_evidence_matches(
            evidence,
            cited_source_text=제품별,
            headers=표["headers"],
            public_row=row,
            raw_row=raw_row,
        )


@pytest.mark.parametrize("이름", ("기계장비", "회계 서비스", "설계 용역"))
def test_계가_들어간_보통_이름을_합계로_오인하지_않는다(이름: str):
    원문 = (
        "제품별 매출액 구 분 2025년 제1기 매 출 액 비 중 "
        f"{이름} 4,000 40.00% 일반 서비스 3,000 30.00% "
        "기타 3,000 30.00% 합계 10,000 100.00%"
    )

    표 = build(원문)[0]

    assert 표["rows"][0][0] == 이름
    assert len(표["rows"]) == 4


def test_MAX_ROWS를_넘겨_잘린_표는_완성표로_내보내지_않는다():
    행들 = " ".join(
        f"품목{chr(44032 + index)} 1,000 {100 / 13:.2f}%" for index in range(13)
    )
    원문 = (
        "제품별 매출액 구 분 2025년 제1기 매 출 액 비 중 "
        f"{행들} 합계 13,000 100.00%"
    )

    assert build(원문) == []


def test_합계가_SCAN범위_안에_없으면_부분표를_내보내지_않는다():
    원문 = (
        "제품별 매출액 구 분 2025년 제1기 매 출 액 비 중 "
        "제품가 6,000 60.00% 제품나 4,000 40.00% " + "설명 " * 600
        + "합계 10,000 100.00%"
    )

    assert build(원문) == []


def test_합계행만_100이고_구성행이_빠진_표는_내보내지_않는다():
    원문 = (
        "제품별 매출액 구 분 2025년 제1기 매 출 액 비 중 "
        "제품가 4,000 40.00% 제품나 3,000 30.00% 제품다 2,000 20.00% "
        "합계 10,000 100.00%"
    )

    assert build(원문) == []


@pytest.mark.parametrize("원문", ["", "매출 이야기가 전혀 없는 글", "제품별 매출액 표가 없음"])
def test_못_찾으면_빈_목록(원문: str):
    """★ 억지로 만들지 않는다 — 비중을 우리가 채우면 그 순간 공시와 어긋난다."""
    assert build(원문) == []


def test_한_줄짜리는_구성이_아니다():
    한줄 = ("제품별 매출액 구 분 품 목 2025년 제21기 (당 기) 매 출 액 비 중 "
            "전체 전부 100,000 100.00%")

    assert build(한줄) == []


# ══════════════════════════════════════════════════════════
# ④ 표 경계와 typed 축 — 제품 caption에 지역 행을 붙였던 실측 결함
# ══════════════════════════════════════════════════════════


_COMPACT_PRODUCT = (
    "제품별 매출액 구 분 2025년 제1기 매 출 액 비 중 "
    "제품가 6,000 60.00% 제품나 4,000 40.00% 합계 10,000 100.00%"
)
_COMPACT_REGION = (
    "지역별 매출액 구 분 2025년 제1기 매 출 액 비 중 "
    "국내 7,000 70.00% 해외 3,000 30.00% 합계 10,000 100.00%"
)


def _rows_by_axis(text: str) -> dict[str, list[list[str]]]:
    return {str(table["axis"]): table["rows"] for table in build(text)}


def test_연속_제품지역표의_caption과_exact행을_서로_바꾸지_않는다():
    """★★ 기존 코드는 두 번째 ``비중``을 제품표 머리말 끝으로 골랐다."""

    tables = build(f"{_COMPACT_PRODUCT} {_COMPACT_REGION}")

    assert [table["axis"] for table in tables] == ["product", "region"]
    assert _rows_by_axis(f"{_COMPACT_PRODUCT} {_COMPACT_REGION}") == {
        "product": [
            ["제품가", "6,000", "60.00%"],
            ["제품나", "4,000", "40.00%"],
            ["합계", "10,000", "100.00%"],
        ],
        "region": [
            ["국내", "7,000", "70.00%"],
            ["해외", "3,000", "30.00%"],
            ["합계", "10,000", "100.00%"],
        ],
    }
    for table in tables:
        assert revenue_table_axis_matches(
            axis=table["axis"],
            caption=table["caption"],
            evidence_rows=table["evidence_rows"],
            cited_source_text=revenue_table_source_excerpt(table["evidence_rows"]),
        )


@pytest.mark.parametrize("gap", (1, 319))
def test_다음_표제가_가깝거나_319자_뒤여도_현재표_첫합계에서_닫힌다(gap: int):
    text = f"{_COMPACT_PRODUCT}{' ' * gap}{_COMPACT_REGION}"

    rows = _rows_by_axis(text)

    assert [row[0] for row in rows["product"]] == ["제품가", "제품나", "합계"]
    assert [row[0] for row in rows["region"]] == ["국내", "해외", "합계"]


@pytest.mark.parametrize("next_head", KNOWN_TABLE_HEADS)
def test_모든_알려진_표제를_다음표_경계로_쓴다(next_head: str):
    next_table = (
        f"{next_head} 구 분 2025년 제1기 매 출 액 비 중 "
        "다음가 8,000 80.00% 다음나 2,000 20.00% 합계 10,000 100.00%"
    )

    product = next(
        table
        for table in build(f"{_COMPACT_PRODUCT} {next_table}")
        if table["axis"] == "product"
    )

    assert [row[0] for row in product["rows"]] == ["제품가", "제품나", "합계"]
    assert "다음가" not in revenue_table_source_excerpt(product["evidence_rows"])


def test_다년도_비중이_반복돼도_당기행과_축만_고른다():
    text = (
        "제품별 매출액 구 분 2025년 제1기 2024년 제0기 "
        "매 출 액 비 중 매 출 액 비 중 "
        "제품가 6,000 60.00% 5,500 55.00% "
        "제품나 4,000 40.00% 4,500 45.00% "
        "합계 10,000 100.00% 10,000 100.00% "
        + _COMPACT_REGION
    )

    rows = _rows_by_axis(text)

    assert rows["product"][:2] == [
        ["제품가", "6,000", "60.00%"],
        ["제품나", "4,000", "40.00%"],
    ]
    assert rows["region"][:2] == [
        ["국내", "7,000", "70.00%"],
        ["해외", "3,000", "30.00%"],
    ]


def test_목차의_선출현_표제를_건너뛰고_실제표를_찾는다():
    text = (
        "목차 2. 제품별 매출액 3. 지역별 매출액 본문 설명 "
        f"{_COMPACT_PRODUCT} {_COMPACT_REGION}"
    )

    assert _rows_by_axis(text) == _rows_by_axis(
        f"{_COMPACT_PRODUCT} {_COMPACT_REGION}"
    )


def test_지역표가_먼저_나와도_각축의_exact행을_보존한다():
    rows = _rows_by_axis(f"{_COMPACT_REGION} {_COMPACT_PRODUCT}")

    assert [row[0] for row in rows["product"]] == ["제품가", "제품나", "합계"]
    assert [row[0] for row in rows["region"]] == ["국내", "해외", "합계"]


@pytest.mark.parametrize(
    "corruption", ("axis", "caption", "evidence_axis", "excerpt")
)
def test_typed축_caption_header_excerpt가_하나라도_다르면_거절한다(
    corruption: str,
):
    table = build(f"{_COMPACT_PRODUCT} {_COMPACT_REGION}")[0]
    axis = table["axis"]
    caption = table["caption"]
    evidence_rows = list(table["evidence_rows"])
    if corruption == "axis":
        axis = "region"
    elif corruption == "caption":
        caption = "어디서 번 돈인가 — 지역별 매출 비중 (2025년)"
    elif corruption == "excerpt":
        evidence_rows = build(f"{_COMPACT_PRODUCT} {_COMPACT_REGION}")[1][
            "evidence_rows"
        ]
    else:
        changed: list[str] = []
        for evidence in evidence_rows:
            payload = json.loads(evidence)
            payload["table"]["axis"] = "region"
            changed.append(canonical_json(payload))
        evidence_rows = changed

    assert not revenue_table_axis_matches(
        axis=axis,
        caption=caption,
        evidence_rows=evidence_rows,
    )
