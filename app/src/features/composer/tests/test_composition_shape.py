"""구성표가 «도식이 그려지는 모양»으로 나가는지 못 박는다.

★ 왜 이 시험이 있나 (진영 실측) — 2장에 표는 붙었는데 «도식이 안 그려졌다».
  원인: `revenuemix`가 만드는 표는 「구분 · 금액 · 비중」 3열이고 «합계» 행이
  붙는데, 도식 판정기(`report_standard/visualization._composition`)는
  「정확히 2열 · 합계 행 없음 · 3~5행」일 때만 100% 누적 막대를 그린다.
★ v1이 쓰는 `revenuemix`는 고치지 않는다(v1 무변). composer가 «도식용 모양»만
  만든다. 값을 바꾸거나 만들지 않고 «줄이기»만 한다.
★ 억지로 도식을 만들지 않는다 — 조건에 못 맞추면 원표 그대로 두고 표로 나간다.

★ 설계 변경 — `composition_table_from_raw`(«첫 표만»)를
  `composition_tables_from_raw`(«표 전부»)로 바꿨다. 제품별·지역별 두 표를
  다 2장에 붙이기 위해서다(과제 2). 시험도 tuple 반환에 맞춰 고쳤다 — 값
  검증 내용(줄이기만 한다·합계를 뺀다 등)은 그대로 지킨다.
"""

from __future__ import annotations

from src.features.composer.port import composition_tables_from_raw
from src.features.pipeline.port import ReportTable
from src.features.revenuemix.logic import build as build_revenue_mix
from src.features.report_standard.visualization import table_visualization
from src.shared.revenue_table_provenance import revenue_row_evidence_matches

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
    표들 = composition_tables_from_raw(_실측표)

    assert len(표들) == 1
    표 = 표들[0]
    assert 표.headers == ("구분", "비중")
    assert len(표.rows) == 4  # 합계 행이 빠졌다
    assert table_visualization(_as_report_table(표)).kind == "composition"


def test_연도열은_순서와_무관하게_최신연도를_고르고_캡션에_밝힌다():
    표 = composition_tables_from_raw(
        [
            {
                "caption": "제품·서비스별 매출 비중 변화 (2023~2025)",
                "headers": ["구분", "2025 비중", "2023 비중", "2024 비중"],
                "rows": [
                    ["제품가", "60%", "40%", "50%"],
                    ["제품나", "30%", "40%", "35%"],
                    ["제품다", "10%", "20%", "15%"],
                    ["합계", "100%", "100%", "100%"],
                ],
            }
        ]
    )[0]

    assert 표.headers == ("구분", "2025 비중")
    assert 표.rows == (("제품가", "60%"), ("제품나", "30%"), ("제품다", "10%"))
    assert 표.caption == "제품·서비스별 매출 비중 변화 (2023~2025) (2025년 비중)"


def test_연도없는_기존표의_캡션과_오른쪽_비중열은_그대로다():
    원표 = [
        {
            "caption": "기존 구성표",
            "headers": ["구분", "비중", "매출 비중"],
            "rows": [["가", "1%", "60%"], ["나", "2%", "30%"], ["다", "3%", "10%"]],
        }
    ]

    표 = composition_tables_from_raw(원표)[0]

    assert 표.caption == "기존 구성표"
    assert 표.headers == ("구분", "매출 비중")
    assert 표.rows == (("가", "60%"), ("나", "30%"), ("다", "10%"))


def test_합계_행을_뺀다():
    """합계가 섞이면 「부분의 합이 전체」라는 그림이 깨진다."""
    표들 = composition_tables_from_raw(_실측표)

    assert all("합계" not in row[0] for row in 표들[0].rows)


def test_값을_바꾸지_않는다():
    """줄이기만 한다 — 비중을 다시 계산하거나 반올림하지 않는다."""
    표들 = composition_tables_from_raw(_실측표)

    assert 표들[0].rows == (
        ("한국", "89.29%"),
        ("중국", "4.52%"),
        ("인도", "4.99%"),
        ("기타", "1.19%"),
    )


def test_3열을_2열로_줄일_때_같은_행의_원문근거도_함께_보존한다():
    원문 = (
        "제품별 매출액 구 분 2025년 제1기 매 출 액 비 중 "
        "제품가 5,000 50.00% 제품나 3,000 30.00% 제품다 2,000 20.00% "
        "합계 10,000 100.00%"
    )
    생산표 = build_revenue_mix(원문, cite="[2]")

    표 = composition_tables_from_raw(생산표)[0]

    assert 표.headers == ("구분", "비중")
    assert 표.rows == (("제품가", "50.00%"), ("제품나", "30.00%"), ("제품다", "20.00%"))
    assert 표.raw_rows == 표.rows
    assert len(표.evidence_rows) == 3
    assert all(
        revenue_row_evidence_matches(
            evidence,
            cited_source_text=원문,
            headers=표.headers,
            public_row=row,
            raw_row=raw_row,
        )
        for row, raw_row, evidence in zip(표.rows, 표.raw_rows, 표.evidence_rows)
    )


def test_기계_회계_설계_행을_합계로_오인해_도식에서_빼지_않는다():
    원표 = [
        {
            "headers": ["구분", "매출액", "비중"],
            "rows": [
                ["기계", "6,000", "60.00%"],
                ["회계", "2,000", "20.00%"],
                ["설계", "2,000", "20.00%"],
                ["합계", "10,000", "100.00%"],
            ],
        }
    ]

    표 = composition_tables_from_raw(원표)[0]

    assert [row[0] for row in 표.rows] == ["기계", "회계", "설계"]


def test_비중_열이_없으면_원표를_그대로_둔다():
    """무엇이 비중인지 모르면 손대지 않는다 — 잘못 줄이는 쪽이 더 나쁘다."""
    원표 = [
        {
            "caption": "부문별 매출",
            "headers": ["구분", "2024", "2025"],
            "rows": [["가구용", "100", "120"], ["산업용", "50", "60"], ["기타", "10", "12"]],
        }
    ]

    표들 = composition_tables_from_raw(원표)

    assert 표들[0].headers == ("구분", "2024", "2025")
    assert len(표들[0].rows) == 3


def test_항목이_셋_미만이면_원표를_그대로_둔다():
    """도식 판정기의 하한이 3행이다 — 못 맞추면 표로 나가는 게 낫다."""
    원표 = [
        {
            "caption": "부문별",
            "headers": ["구분", "매출액", "비중"],
            "rows": [["가구용", "100", "70%"], ["기타", "40", "30%"], ["합계", "140", "100%"]],
        }
    ]

    표들 = composition_tables_from_raw(원표)

    assert 표들[0].headers == ("구분", "매출액", "비중")
    assert len(표들[0].rows) == 3


def test_이미_두_열이면_손대지_않는다():
    원표 = [
        {
            "headers": ["부문", "비중"],
            "rows": [["가구용", "70"], ["산업용", "9"], ["열분해유", "6"], ["기타", "15"]],
        }
    ]

    표들 = composition_tables_from_raw(원표)

    assert 표들[0].headers == ("부문", "비중")
    assert len(표들[0].rows) == 4


def test_표가_여럿이면_전부_바꾼다():
    """★★ 과제 2 — 제품별·지역별 두 표를 «둘 다» 구성표로 바꾼다.

    예전에는 첫 표만 썼다(«같은 매출을 두 번 보여 준다»는 우려 때문). 하지만
    제품별·지역별은 같은 매출을 «다른 축»으로 나눈 것이라 중복이 아니고,
    소유권 표(2장 = 「고객·지역·채널 우선순위」)에도 지역 우선순위가
    명시돼 있다 — 첫 표만 쓰는 건 v2만의 축소였다.
    """
    표들 = composition_tables_from_raw(
        [
            {"caption": "제품별", "headers": ["부문", "비중"], "rows": [["A", "60"], ["B", "30"], ["C", "10"]]},
            {"caption": "지역별", "headers": ["지역", "비중"], "rows": [["국내", "70"], ["해외", "30"], ["기타", "0"]]},
        ]
    )

    assert len(표들) == 2
    assert 표들[0].caption == "제품별"
    assert 표들[1].caption == "지역별"


def test_표_하나가_도식_하한에_못_미쳐도_다른_표는_영향받지_않는다():
    """표마다 따로 줄인다 — 한 표의 실패가 다른 표를 건드리지 않는다."""
    표들 = composition_tables_from_raw(
        [
            {
                "caption": "항목이 둘뿐",
                "headers": ["구분", "비중"],
                "rows": [["가", "60"], ["나", "40"]],  # 3행 하한 미달 → 그대로
            },
            {
                "caption": "정상 표",
                "headers": ["구분", "매출액", "비중"],
                "rows": [["가", "1", "60%"], ["나", "1", "30%"], ["다", "1", "10%"]],
            },
        ]
    )

    assert len(표들) == 2
    assert 표들[0].headers == ("구분", "비중")  # 하한 미달 — 손대지 않음
    assert 표들[1].headers == ("구분", "비중")  # 정상 — 구성 모양으로 줄어듦


def test_빈_표_목록이면_빈_튜플이다():
    assert composition_tables_from_raw([]) == ()
    assert composition_tables_from_raw(None) == ()


def test_행이_비면_그_표만_빠진다():
    표들 = composition_tables_from_raw(
        [{"caption": "빈 표", "headers": ["a"], "rows": []}]
    )
    assert 표들 == ()
