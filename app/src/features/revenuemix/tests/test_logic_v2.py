"""표 «모양»으로 찾는 v2 파서를 검사판 5곳 실측 원문으로 못 박는다.

★ 무엇을 지키나 — 「제목이 달라도 표를 찾는다」와 「매출표가 아닌 표는
  안 찾는다」 두 가지다. 앞의 것만 지키면 은행 자금조달표가 매출표로 올라오고,
  뒤의 것만 지키면 지금처럼 5곳 중 1곳에서만 표가 나온다.

⚠️ 아래 기대값은 **원문에서 눈으로 옮겨 적은 리터럴**이다. 코드가 만든 값을
  다시 코드로 비교하면 둘이 함께 틀려도 초록이 된다.

픽스처는 ``fixtures/``에 있고, 검사판 사업보고서 원문에서 표 구간 앞뒤 600자만
잘라 온 것이다(원문 전체는 넣지 않는다 — 용량·라이선스).
  · hybe_product_and_region.txt        하이브 「(1) 제품별 매출액」+「(2) 지역별 매출액」
  · samsung_product.txt                삼성전자 「가. 주요 제품 매출」 (설명 문단 340자·△ 음수)
  · jinyoung_product.txt               진영 「가. 주요 제품 등의 현황」 (열 이름 「비율」)
  · jinyoung_region_consolidated.txt   진영 주석 37번 «연결» 지역별 매출액
  · jinyoung_region_separate.txt       진영 주석 37번 «별도» 지역별 매출액
  · kakao_product.txt                  카카오 「가. 매출 실적」 (HTML·&nbsp;)
  · hyundaicard_product.txt            현대카드 「(1) 영업실적」 (열 이름 「구성비」·% 없음)
  · woori_funding_not_revenue.txt      우리은행 「[자금조달실적]」 — 비중 열이 있지만 매출이 아니다

단위 픽스처 두 개는 **하이브 픽스처를 일부러 망가뜨린 인공 변형본**이다(원문이 아니다).
  · unit_conflict_synthetic.txt        머리말 칸에 「(천원)」을 하나씩 더 넣어 단위를 엇갈리게 했다
  · unit_missing_synthetic.txt         「(단위 : 백만원)」 표기를 전부 지웠다
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core import revenue_table_switch as switch
from src.features.revenuemix.logic import build, build_with_diagnostics
from src.shared.revenue_table_provenance import (
    revenue_row_evidence_matches,
    revenue_table_axis_matches,
    revenue_table_headers,
    revenue_table_source_excerpt,
    revenue_units_in,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def 픽스처(이름: str) -> str:
    return (_FIXTURES / f"{이름}.txt").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _fresh_process_revenue_table_switch():
    """시험끼리 프로세스 동결 상태가 새지 않게 격리한다.

    ⚠️ 이 스위치는 프로세스당 한 번 동결된다. 격리하지 않으면 여기서 켠 값이
      뒤따르는 «다른 파일의» 시험까지 v2로 끌고 간다.
    """

    switch._reset_process_revenue_table_switch_for_tests()  # noqa: SLF001
    yield
    switch._reset_process_revenue_table_switch_for_tests()  # noqa: SLF001


@pytest.fixture
def v2_켬(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(switch.REVENUE_TABLE_V2_ENV_NAME, "1")


@pytest.fixture
def v2_끔(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(switch.REVENUE_TABLE_V2_ENV_NAME, raising=False)


def 축별_행(원문: str) -> dict[str, list[list[str]]]:
    return {str(표["axis"]): 표["rows"] for 표 in build(원문)}


# ══════════════════════════════════════════════════════════
# ① 회사별 실측 기대값 (스위치 ON)
# ══════════════════════════════════════════════════════════


def test_하이브는_제품표와_지역표를_그대로_옮긴다(v2_켬: None) -> None:
    표들 = build(픽스처("hybe_product_and_region"), cite="[8]")

    assert [표["axis"] for 표 in 표들] == ["product", "region"]
    assert 표들[0]["caption"] == "무엇을 팔아 번 돈인가 — 제품·서비스별 매출 비중 (2025년)"
    assert 표들[0]["rows"][0] == ["음반/음원 음반, 음원 등", "772,960", "29.17%"]
    assert 표들[0]["rows"][-1] == ["합계", "2,649,870", "100.00%"]
    assert len(표들[0]["rows"]) == 7
    assert 표들[1]["caption"] == "어디서 번 돈인가 — 지역별 매출 비중 (2025년)"
    assert 표들[1]["rows"][0] == ["국내", "722,780", "27.28%"]
    assert 표들[1]["rows"][-1] == ["합계", "2,649,870", "100.00%"]
    assert len(표들[1]["rows"]) == 6


def test_삼성전자는_설명문단과_음수를_넘어_표를_찾는다(v2_켬: None) -> None:
    """★★ 표제와 표 사이에 설명 문단 340자가 끼어 있고, 다섯째 행이 음수다.

    ⚠️ ``△``를 못 읽으면 그 행이 통째로 빠지고 남은 다섯 행의 비중 합이
      108.9%가 된다 — «틀린 표»가 나온다. 0단계 실측에서 잡힌 결함이다.
    """

    표들 = build(픽스처("samsung_product"))

    assert len(표들) == 1
    assert 표들[0]["axis"] == "product"
    assert 표들[0]["rows"] == [
        [
            "DX 부문 TV, 모니터, 냉장고, 세탁기, 에어컨,스마트폰, 네트워크시스템, PC 등",
            "1,879,673",
            "56.3%",
        ],
        ["DS 부문 DRAM, NAND Flash, 모바일AP 등", "1,301,282", "39.0%"],
        ["SDC 스마트폰용 OLED패널 등", "298,417", "8.9%"],
        ["Harman 디지털 콕핏, 카오디오, 포터블 스피커 등", "157,833", "4.7%"],
        ["기타 부문간 내부거래 제거 등", "△301,146", "△8.9%"],
        ["총 계", "3,336,059", "100.00%"],
    ]


def test_삼성전자_음수는_원문_표기_그대로_남는다(v2_켬: None) -> None:
    """★ ``△``를 떼면 화면에서 «빼는 값»이 «더하는 값»으로 보인다."""

    표 = build(픽스처("samsung_product"))[0]

    금액, 비중 = 표["rows"][4][1], 표["rows"][4][2]

    assert 금액 == "△301,146"
    assert 비중 == "△8.9%"
    assert "△301,146 △8.9%" in revenue_table_source_excerpt(표["evidence_rows"])


def test_진영은_열_이름이_비율이어도_읽는다(v2_켬: None) -> None:
    """★ 열 이름이 「비 중」이 아니라 「비율」이다. 이름 하나로 표를 버리지 않는다."""

    표들 = build(픽스처("jinyoung_product"))

    assert len(표들) == 1
    assert 표들[0]["rows"] == [
        ["제품 가구용 Sheet", "23,127", "71.33%"],
        ["산업용 Sheet", "4,191", "12.93%"],
        ["열분해유", "1,226", "3.78%"],
        ["기타", "3,066", "9.46%"],
        ["상품", "229", "0.71%"],
        ["기타", "584", "1.80%"],
        ["합계", "32,423", "100.00%"],
    ]


def test_진영은_같은_이름_기타가_두_번_나와도_둘_다_싣는다(v2_켬: None) -> None:
    """★★ 이름이 같다고 버리면 금액 합이 합계와 어긋나 표 전체가 떨어진다.

    실측 — 제품 「기타」 3,066 과 상품 「기타」 584 는 서로 다른 행이다.
    """

    표 = build(픽스처("jinyoung_product"))[0]

    기타들 = [행 for 행 in 표["rows"] if 행[0] == "기타"]

    assert [행[1] for 행 in 기타들] == ["3,066", "584"]


def test_카카오는_HTML의_nbsp_합계행도_알아본다(v2_켬: None) -> None:
    """★ 태그만 지운 평문에는 ``&nbsp;``가 글자로 남는다(실측: 「합 계」)."""

    표들 = build(픽스처("kakao_product"))

    assert len(표들) == 1
    assert 표들[0]["rows"] == [
        ["플랫폼 부문", "4,318,175", "53.3%"],
        ["콘텐츠 부문", "3,780,973", "46.7%"],
        ["합 계", "8,099,148", "100.0%"],
    ]


def test_현대카드는_구성비_열과_퍼센트_없는_값을_읽는다(v2_켬: None) -> None:
    """★ 열 이름이 「구성비」이고 값에 ``%``가 없다. 합계 행 이름도 「영업수익합계」다."""

    표들 = build(픽스처("hyundaicard_product"))

    assert len(표들) == 1
    assert 표들[0]["rows"] == [
        ["카드수익", "17,936", "44.8%"],
        ["이자수익", "16,676", "41.6%"],
        ["유가증권평가및처분이익", "281", "0.7%"],
        ["배당금수익", "8", "0.0%"],
        ["신용손실충당금환입", "24", "0.0%"],
        ["기타영업수익", "5,153", "12.9%"],
        ["영업수익합계", "40,078", "100.0%"],
    ]


def test_우리은행은_비중_열이_있어도_매출표가_아니면_안_만든다(v2_켬: None) -> None:
    """★★ 은행 「자금조달실적」은 비중 열이 있고 금액도 합계와 맞는다.

    모양만 보면 통과하지만 «매출»이 아니다. 이 표를 실으면 독자는 은행이
    예수금을 팔아 돈을 번다고 읽는다.
    """

    표들, 진단 = build_with_diagnostics(픽스처("woori_funding_not_revenue"))

    assert 표들 == []
    assert 진단["후보_표_수"] >= 1          # 후보를 보긴 «봤다»
    assert 진단["채택_표_수"] == 0
    assert 진단["탈락_사유"]                # 왜 떨어졌는지 남는다


# ══════════════════════════════════════════════════════════
# ② 변형 처리
# ══════════════════════════════════════════════════════════


def test_같은_표가_두_번_실려도_한_번만_싣는다(v2_켬: None) -> None:
    """★ 하이브 제품표는 원문에 세 번 실린다(실측 flat 15498·18189·298048)."""

    한_번 = 픽스처("hybe_product_and_region")

    두_번 = build(f"{한_번} {한_번}")

    assert [표["axis"] for 표 in 두_번] == ["product", "region"]
    assert 두_번[0]["rows"] == build(한_번)[0]["rows"]


def test_연결과_별도가_같이_있으면_연결을_쓰고_섞지_않는다(v2_켬: None) -> None:
    """★★ 진영 주석 37번은 연결·별도가 «같은 번호»로 두 번 나온다.

    숫자가 다르다 — 연결 한국 28,952,091 / 별도 한국 24,521,198.
    섞으면 합계가 맞지 않는 표가 되고, 별도를 고르면 규모가 작아 보인다.
    """

    연결 = 픽스처("jinyoung_region_consolidated")
    별도 = 픽스처("jinyoung_region_separate")

    표들 = build(f"{연결} {별도}")

    assert len(표들) == 1
    assert 표들[0]["rows"] == [
        ["한국", "28,952,091", "89.29%"],
        ["중국", "1,466,903", "4.52%"],
        ["인도", "1,617,307", "4.99%"],
        ["기타", "387,021", "1.19%"],
        ["합계", "32,423,322", "100.00%"],
    ]
    assert "24,521,198" not in json.dumps(표들[0], ensure_ascii=False)


def test_별도만_있으면_별도를_쓴다(v2_켬: None) -> None:
    """★ 연결이 없는데 표를 버리지는 않는다 — 있는 것을 그대로 옮긴다."""

    표들 = build(픽스처("jinyoung_region_separate"))

    assert 표들[0]["rows"][0] == ["한국", "24,521,198", "87.60%"]


def test_비중_열이_없는_표는_만들지_않는다(v2_켬: None) -> None:
    """★★ 비중을 «우리가» 계산하지 않는다 (제품 결정).

    ⚠️ 열 이름이 「구분 · 매출액 · 비중」 세 칸으로 고정돼 있어 비중 칸을 비운
      표를 낼 자리가 없다. 계산해서 채우는 순간 반올림이 공시와 어긋나므로,
      비중 열이 없는 표는 «만들지 않는» 쪽을 택했다.
    """

    금액만 = (
        "가. 매출실적 (단위 : 백만원) 구 분 제57기 제56기 제55기 "
        "제품가 6,000 5,500 5,000 제품나 4,000 4,500 5,000 "
        "합계 10,000 10,000 10,000"
    )

    assert build(금액만) == []


def test_금액_합이_합계와_다르면_버린다(v2_켬: None) -> None:
    """★★ 이 검산이 v2의 진짜 관문이다 — 표제가 아니라 이것이 판정한다."""

    어긋난_표 = (
        "가. 매출 실적 (단위 : 백만원) 구 분 품 목 2025년 매출액 비중 "
        "제품가 6,000 60.00% 제품나 3,000 30.00% 합계 10,000 100.00%"
    )

    표들, 진단 = build_with_diagnostics(어긋난_표)

    assert 표들 == []
    assert 진단["탈락_사유"].get("금액 합 불일치") == 1


def test_매출_표현이_없으면_모양이_맞아도_버린다(v2_켬: None) -> None:
    원가표 = (
        "가. 원재료 매입 (단위 : 백만원) 구 분 품 목 2025년 매입액 비중 "
        "원료가 6,000 60.00% 원료나 4,000 40.00% 합계 10,000 100.00%"
    )

    표들, 진단 = build_with_diagnostics(원가표)

    assert 표들 == []
    assert 진단["탈락_사유"].get("매출 표현 없음") == 1


def test_비중_합이_100에서_멀면_버린다(v2_켬: None) -> None:
    """★ 금액 합만 맞고 비중이 엉뚱하면 표가 잘못 잘린 것이다."""

    이상한_표 = (
        "가. 매출 실적 (단위 : 백만원) 구 분 품 목 2025년 매출액 비중 "
        "제품가 6,000 20.00% 제품나 4,000 15.00% 합계 10,000 100.00%"
    )

    표들, 진단 = build_with_diagnostics(이상한_표)

    assert 표들 == []
    assert 진단["탈락_사유"].get("비중 합 불일치") == 1


# ══════════════════════════════════════════════════════════
# ③ 금액 단위 — 틀리면 독자가 100배로 읽는다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "이름, 축, 열_이름",
    (
        ("hybe_product_and_region", "product", "매출액 (백만원)"),
        ("hybe_product_and_region", "region", "매출액 (백만원)"),
        ("samsung_product", "product", "매출액 (억원)"),
        ("hyundaicard_product", "product", "매출액 (억원)"),
        ("jinyoung_product", "product", "매출액 (백만원)"),
        ("jinyoung_region_consolidated", "region", "매출액 (천원)"),
        ("kakao_product", "product", "매출액 (백만원)"),
    ),
)
def test_금액_열_이름이_원문_단위를_따라간다(
    이름: str, 축: str, 열_이름: str, v2_켬: None
) -> None:
    """★★ 숫자는 원문 그대로여도 열 이름의 단위가 틀리면 100배로 읽힌다.

    실측 — 삼성전자 DX 부문 1,879,673은 «억원»이라 187조원이다. 이것을
    「매출액 (백만원)」 칸에 넣으면 1조 8,796억원으로 읽힌다.
    """

    표 = next(표 for 표 in build(픽스처(이름)) if 표["axis"] == 축)

    assert 표["headers"] == ["구분", 열_이름, "비중"]


def test_캡션과_비중_열은_단위와_상관없이_그대로다(v2_켬: None) -> None:
    """★ 바뀌는 것은 금액 열 이름 하나뿐이다."""

    표 = build(픽스처("samsung_product"))[0]

    assert 표["headers"][0] == "구분"
    assert 표["headers"][2] == "비중"
    assert 표["caption"] == "무엇을 팔아 번 돈인가 — 제품·서비스별 매출 비중"


def test_단위가_엇갈리면_표를_만들지_않는다(v2_켬: None) -> None:
    """★★ 어느 쪽이 맞는지 «우리가» 고르면 그 순간 지어내는 것이다."""

    표들, 진단 = build_with_diagnostics(픽스처("unit_conflict_synthetic"))

    assert 표들 == []
    assert 진단["탈락_사유"].get("단위 충돌") == 2


def test_단위를_못_읽으면_표를_만들지_않는다(v2_켬: None) -> None:
    """★★ 환산하지 않는다. 모르면 뺀다 — 틀린 단위보다 없는 편이 낫다."""

    표들, 진단 = build_with_diagnostics(픽스처("unit_missing_synthetic"))

    assert 표들 == []
    assert 진단["탈락_사유"].get("단위 미확인") == 2


@pytest.mark.parametrize(
    "원문, 기대",
    (
        ("(단위 : 억원, %)", ("억원",)),
        ("( 단위 : 천원 )", ("천원",)),
        ("(단위: 백만원, 연결재무제표 기준)", ("백만원",)),
        ("(단위 : 원)", ("원",)),
        ("구 분 품 목 (천원) 2025년", ("천원",)),
        ("(단위 : 백만원, 천원)", ("백만원", "천원")),
        ("(단위 : Km, 리터)", ()),
        ("원재료 매입 지원 원가", ()),
    ),
)
def test_단위는_단위라고_적힌_자리에서만_읽는다(원문: str, 기대: tuple) -> None:
    """★ 아무 데서나 「원」을 주우면 「원재료」·「지원」이 단위가 된다."""

    assert revenue_units_in(원문) == 기대


def test_닫힌_목록_밖의_단위는_열_이름으로_만들지_않는다() -> None:
    with pytest.raises(ValueError):
        revenue_table_headers("조원")


# ══════════════════════════════════════════════════════════
# ④ 근거 — 새 표도 v1과 «같은 강도»로 원문에 묶인다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "이름",
    (
        "hybe_product_and_region",
        "samsung_product",
        "jinyoung_product",
        "jinyoung_region_consolidated",
        "kakao_product",
        "hyundaicard_product",
    ),
)
def test_모든_새_표가_행별_원문_근거_검증을_통과한다(이름: str, v2_켬: None) -> None:
    """★★ 표가 늘어나도 「원문에서 왔다」는 증명은 그대로여야 한다."""

    원문 = 픽스처(이름)

    for 표 in build(원문, cite="[재현]"):
        assert revenue_table_axis_matches(
            axis=표["axis"],
            caption=표["caption"],
            evidence_rows=표["evidence_rows"],
            cited_source_text=revenue_table_source_excerpt(표["evidence_rows"]),
        )
        assert len(표["evidence_rows"]) == len(표["rows"])
        for 행, 원행, 근거 in zip(표["rows"], 표["raw_rows"], 표["evidence_rows"]):
            assert revenue_row_evidence_matches(
                근거,
                cited_source_text=원문,
                filing_text=원문,
                headers=표["headers"],
                public_row=행,
                raw_row=원행,
            )


def test_근거의_인용_조각은_원문_그대로다(v2_켬: None) -> None:
    원문 = 픽스처("hyundaicard_product")

    표 = build(원문)[0]
    조각 = revenue_table_source_excerpt(표["evidence_rows"])
    첫_근거 = json.loads(표["evidence_rows"][0])

    assert 조각 in 원문
    assert 원문[첫_근거["source"]["start"] : 첫_근거["source"]["end"]] == 조각
    assert 첫_근거["row"]["raw_fields"]["amount"]["value"] == "17,936"


# ══════════════════════════════════════════════════════════
# ⑤ 스위치 — 끄면 지금과 «똑같다»
# ══════════════════════════════════════════════════════════


def test_스위치를_끄면_하이브는_지금과_같다(v2_끔: None) -> None:
    """★ 되돌림의 증거. 옛 경로는 표제 「제품별 매출액」·「지역별 매출액」을 찾는다."""

    표들 = build(픽스처("hybe_product_and_region"), cite="[8]")

    assert [표["axis"] for 표 in 표들] == ["product", "region"]
    assert 표들[0]["rows"][0] == ["음반/음원 음반, 음원 등", "772,960", "29.17%"]
    assert len(표들[0]["rows"]) == 7
    assert len(표들[1]["rows"]) == 6


@pytest.mark.parametrize(
    "이름",
    (
        "samsung_product",
        "jinyoung_product",
        "jinyoung_region_consolidated",
        "kakao_product",
        "hyundaicard_product",
        "woori_funding_not_revenue",
    ),
)
def test_스위치를_끄면_나머지_회사는_예전처럼_표가_없다(이름: str, v2_끔: None) -> None:
    assert build(픽스처(이름)) == []


def test_스위치를_끄면_옛_경로는_단위를_보지_않는다(v2_끔: None) -> None:
    """★ v1은 표제만 보고 단위는 읽지 않는다 — 옛 동작을 그대로 둔다는 증거다.

    단위 표기를 지운 픽스처에서도 옛 경로는 표제 「(1) 제품별 매출액」을 찾아
    예전과 똑같이 두 표를 만든다. 단위 fail-closed는 «새 경로만»의 규칙이다.
    """

    표들 = build(픽스처("unit_missing_synthetic"))

    assert [표["headers"] for 표 in 표들] == [
        ["구분", "매출액 (백만원)", "비중"],
        ["구분", "매출액 (백만원)", "비중"],
    ]


def test_우리은행은_스위치를_켜도_꺼도_표가_없다(monkeypatch: pytest.MonkeyPatch) -> None:
    원문 = 픽스처("woori_funding_not_revenue")

    monkeypatch.delenv(switch.REVENUE_TABLE_V2_ENV_NAME, raising=False)
    assert build(원문) == []

    switch._reset_process_revenue_table_switch_for_tests()  # noqa: SLF001
    monkeypatch.setenv(switch.REVENUE_TABLE_V2_ENV_NAME, "1")
    assert build(원문) == []


def test_진단은_경로_이름을_남긴다(v2_끔: None) -> None:
    """★ 나중에 「이 조사가 어느 파서로 돌았나」를 저장본만 보고 알 수 있어야 한다."""

    _, 진단 = build_with_diagnostics(픽스처("samsung_product"))

    assert 진단["경로"] == "v1"
    assert 진단["채택_표_수"] == 0
