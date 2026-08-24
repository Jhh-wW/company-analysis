"""구성표가 «도식이 그려지는 모양»으로 나가는지 못 박는다.

★ 왜 이 시험이 있나 (진영 실측) — 2장에 표는 붙었는데 «도식이 안 그려졌다».
  원인: `revenuemix`가 만드는 표는 「구분 · 금액 · 비중」 3열이고 «합계» 행이
  붙는데, 도식 판정기(`report_standard/visualization._composition`)는
  「정확히 2열 · 합계 행 없음 · 3~5행」일 때만 100% 누적 막대를 그린다.
★ v1이 쓰는 `revenuemix`는 고치지 않는다(v1 무변). composer가 «도식용 모양»만
  만든다. 값을 바꾸거나 만들지 않고 «줄이기»만 한다.
★ 억지로 도식을 만들지 않는다 — 조건에 못 맞추면 원표 그대로 두고 표로 나간다.
"""

from __future__ import annotations

from src.features.composer.port import composition_table_from_raw
from src.features.pipeline.port import ReportTable
from src.features.report_standard.visualization import table_visualization

#: 진영 실측에서 실제로 나온 표 (지역별 매출 비중).
_실측표 = [
    {
        "caption": "어디서 번 돈인가 — 지역별 매출 비중",
        "headers": ["구분", "매출액 (백만원)", "비중"],
        "rows": [
            ["한국", "28,952,091", "89.29%"],
            ["중국", "1,466,903", "4.52%"],
            ["인도", "1,617,307", "4.99%"],
            ["기타", "387,021", "1.19%"],
            ["합계", "32,423,322", "100.00%"],
        ],
        "cite": "조각 8·매출수주",
    }
]


def _as_report_table(table) -> ReportTable:
    return ReportTable(
        caption=table.caption,
        headers=list(table.headers),
        rows=[list(row) for row in table.rows],
        presentation="composition",
    )


def test_실측표가_도식이_그려지는_모양으로_바뀐다():
    표 = composition_table_from_raw(_실측표)

    assert 표 is not None
    assert 표.headers == ("구분", "비중")
    assert len(표.rows) == 4  # 합계 행이 빠졌다
    assert table_visualization(_as_report_table(표)).kind == "composition"


def test_합계_행을_뺀다():
    """합계가 섞이면 「부분의 합이 전체」라는 그림이 깨진다."""
    표 = composition_table_from_raw(_실측표)

    assert all("합계" not in row[0] for row in 표.rows)


def test_값을_바꾸지_않는다():
    """줄이기만 한다 — 비중을 다시 계산하거나 반올림하지 않는다."""
    표 = composition_table_from_raw(_실측표)

    assert 표.rows == (
        ("한국", "89.29%"),
        ("중국", "4.52%"),
        ("인도", "4.99%"),
        ("기타", "1.19%"),
    )


def test_비중_열이_없으면_원표를_그대로_둔다():
    """무엇이 비중인지 모르면 손대지 않는다 — 잘못 줄이는 쪽이 더 나쁘다."""
    원표 = [
        {
            "caption": "부문별 매출",
            "headers": ["구분", "2024", "2025"],
            "rows": [["가구용", "100", "120"], ["산업용", "50", "60"], ["기타", "10", "12"]],
        }
    ]

    표 = composition_table_from_raw(원표)

    assert 표.headers == ("구분", "2024", "2025")
    assert len(표.rows) == 3


def test_항목이_셋_미만이면_원표를_그대로_둔다():
    """도식 판정기의 하한이 3행이다 — 못 맞추면 표로 나가는 게 낫다."""
    원표 = [
        {
            "caption": "부문별",
            "headers": ["구분", "매출액", "비중"],
            "rows": [["가구용", "100", "70%"], ["기타", "40", "30%"], ["합계", "140", "100%"]],
        }
    ]

    표 = composition_table_from_raw(원표)

    assert 표.headers == ("구분", "매출액", "비중")
    assert len(표.rows) == 3


def test_이미_두_열이면_손대지_않는다():
    원표 = [
        {
            "headers": ["부문", "비중"],
            "rows": [["가구용", "70"], ["산업용", "9"], ["열분해유", "6"], ["기타", "15"]],
        }
    ]

    표 = composition_table_from_raw(원표)

    assert 표.headers == ("부문", "비중")
    assert len(표.rows) == 4
