"""도식이 «아무 말도 아닌 칸»을 내거나 매출원을 빠뜨리지 못하게 막는다.

★ 왜 이 시험이 있나 (2026-09-22 산출 PDF 2건 실측)
  ① 의료기기 회사 7장 도식이 「상품 → 판매 → 고객」이었다. 세 칸 모두 어느
     회사에나 들어맞는 말이라 독자가 얻는 정보가 0이다. 게다가 같은 장 산문은
     「제품 매출이 전체 수익의 대부분」이라, 한 쪽 안에서 제품과 상품이
     서로 어긋났다(공시 손익계산서: 제품매출 387.7억·상품매출 37.2억).
  ② AI 회사 2장 도식은 매출원 둘 중 «콘텐츠»만 그리고 «용역»을 빼먹었다.
     같은 회사 7장 도식의 끝 칸 「개인 사용자」는 인용 원문(주석의 「고객」)에
     없는 말이었다.

★ 그래서 여기서 지키는 것:
  ① 일반어 한 낱말 칸은 «빈 칸»이다 — 전부 그러면 줄이 통째로 빠진다.
  ② 수식어가 붙은 칸(「AI 콘텐츠 서비스」)은 그대로 통과한다. 낱말 «포함»으로
     막으면 정상 도식이 영원히 안 나온다.
  ③ 원문이 밝힌 매출원이 도식에 없으면 «기록»을 남긴다. 줄을 지어내지 않는다.
  ④ 같은 장 산문과 제품/상품을 뒤집어 말하는 줄은 뺀다.
"""

from __future__ import annotations

from src.features.composer.constants import (
    BUSINESS_FLOW_GUIDE,
    BUSINESS_FLOW_HEADERS,
    BUSINESS_FLOW_SECTION_ID,
    OPERATIONS_FLOW_GUIDE,
    OPERATIONS_FLOW_HEADERS,
    OPERATIONS_FLOW_SECTION_ID,
)
from src.features.composer.diagram_check import (
    check_diagram_numbers,
    missing_revenue_streams,
    revenue_stream_names,
)
from src.features.composer.diagram_review_constants import (
    BUSINESS_FLOW_PRODUCT_HEADER,
    FLOW_PRODUCT_GOODS_CONFLICT_CODE,
    FLOW_REVENUE_STREAM_MISSING_CODE,
    OPERATIONS_FLOW_ORIGIN_HEADER,
)
from src.features.composer.logic import is_generic_flow_cell, parse_flow_rows
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FlowRow,
)

# ══════════════════════════════════════════════════════════
# 실제 산출물의 글자 (재구성 금지 — 인쇄된 그대로)
# ══════════════════════════════════════════════════════════

#: 의료기기 회사 3쪽 7장 도식에 인쇄된 세 칸.
_인쇄된_일반어_경로 = ["상품", "판매", "고객"]

#: 같은 회사 7장 산문이 말한 주력 매출. 도식과 어긋났던 문장이다.
_제품_주력_문장 = "제품 매출이 전체 수익의 대부분이다."

#: AI 회사 2쪽 2장 도식에 인쇄된 네 칸(끝 칸은 「미확인」으로 비어 있었다).
_인쇄된_콘텐츠_경로 = [
    "AI 콘텐츠 생성 플랫폼",
    "AI 콘텐츠 서비스",
    "사용자가 콘텐츠 제공받는 시점에 수익 인식",
    "",
]

#: 같은 회사 3쪽 7장 도식에 인쇄된 세 칸. 끝 칸이 원문에 없는 말이었다.
_인쇄된_개인사용자_경로 = ["사용자의 AI 콘텐츠 이용", "콘텐츠 제공", "개인 사용자"]

#: 매출원 둘을 밝힌 원문 절.
_매출_구성_원문 = "영업수익은 용역 매출과 인공지능 콘텐츠 매출로 구성된다."


def _응답(칸들: list[list[str]], 인용: list[str] | None = None) -> str:
    import json

    return json.dumps(
        {
            "문장들": [],
            "경로표": [
                {"칸": 칸, "인용": list(인용 or ["1"])} for 칸 in 칸들
            ],
        },
        ensure_ascii=False,
    )


def _장(
    section_id: str,
    rows: tuple[FlowRow, ...],
    문장들: tuple[str, ...] = (),
    인용: tuple[str, ...] = ("1",),
) -> ComposedSection:
    return ComposedSection(
        section_id=section_id,
        sentences=tuple(
            ComposedSentence(text=글, citations=인용, grade="확인") for 글 in 문장들
        ),
        flow_rows=rows,
    )


def _조각(text: str, fragment_id: str = "1") -> CollectedFragment:
    return CollectedFragment(fragment_id=fragment_id, kind="사업내용", text=text)


# ══════════════════════════════════════════════════════════
# ① 일반어 칸 거부 (파서)
# ══════════════════════════════════════════════════════════


def test_인쇄된_일반어_세칸은_줄이_통째로_빠진다():
    """★ 의료기기 회사 7장 실측 — 「상품 → 판매 → 고객」은 정보가 0이다."""

    rows = parse_flow_rows(
        _응답([_인쇄된_일반어_경로]), OPERATIONS_FLOW_SECTION_ID
    )

    assert rows == (), f"정보가 없는 일반어 경로가 살아남았습니다: {rows}"


def test_회사를_가리키는_칸은_그대로_통과한다():
    """★ 음성 대조 — 낱말 «포함»으로 막으면 정상 도식이 영원히 안 나온다."""

    경로 = ["폴리우레탄 수액세트", "의료기관에 공급", "병원·의료기관"]
    rows = parse_flow_rows(_응답([경로]), OPERATIONS_FLOW_SECTION_ID)

    assert len(rows) == 1
    assert list(rows[0].cells) == 경로


def test_개인_사용자_단독_칸만_비고_나머지_칸은_남는다():
    """★ AI 회사 7장 실측 — 끝 칸만 원문에 없는 일반어였다."""

    rows = parse_flow_rows(
        _응답([_인쇄된_개인사용자_경로]), OPERATIONS_FLOW_SECTION_ID
    )

    assert len(rows) == 1, "일부만 일반어인 줄을 통째로 버리면 안 됩니다"
    assert rows[0].cells == ("사용자의 AI 콘텐츠 이용", "콘텐츠 제공", "")


def test_조사가_붙어도_일반어로_본다():
    """「고객이」·「서비스로」처럼 조사만 붙은 칸도 아무 말을 하지 않는다."""

    assert is_generic_flow_cell("고객이")
    assert is_generic_flow_cell("서비스로")
    assert is_generic_flow_cell("개인 사용자")


def test_낱말을_이루는_글자는_떼지_않는다():
    """★ 「고객사」를 「고객」으로 읽으면 뜻이 있는 칸이 지워진다."""

    assert not is_generic_flow_cell("고객사")
    assert not is_generic_flow_cell("제품군")
    assert not is_generic_flow_cell("AI 콘텐츠 서비스")


def test_빈_칸은_일반어_검사의_대상이_아니다():
    """빈 칸 처분은 파서·렌더러가 이미 정해 뒀다 — 여기서 뒤집지 않는다."""

    assert not is_generic_flow_cell("")
    assert not is_generic_flow_cell("   ")


# ══════════════════════════════════════════════════════════
# ② 매출원 커버리지 (2장)
# ══════════════════════════════════════════════════════════


def test_구성_절에서_매출원_이름을_핵심_명사로_뽑는다():
    assert revenue_stream_names((_매출_구성_원문,)) == ("용역", "콘텐츠")


def test_합계와_지표는_매출원이_아니다():
    """「매출액」·「매출원가」·「수익률」·「영업수익」은 매출«원»이 아니다."""

    원문 = (
        "매출액은 42,483,833,889원이고 매출원가와 수익률은 별도로 구성된다. "
        "연결 매출과 당기 매출도 같은 기준으로 구성된다."
    )

    assert revenue_stream_names((원문,)) == ()


def test_구성_절_밖의_산문에서는_이름을_뽑지_않는다():
    """★ 「매출이 늘었다」에서 이름을 뽑으면 회사마다 엉뚱한 누락이 잡힌다."""

    assert revenue_stream_names(("올해 제품 매출이 늘었다.",)) == ()


def test_손익계산서처럼_붙여_쓴_항목은_구성_절_밖에서도_읽는다():
    """★ 의료기기 회사 실측 — 표 항목은 「제품매출」처럼 붙여 쓴다."""

    assert revenue_stream_names(("제품매출 38,770 상품매출 3,723",)) == (
        "제품",
        "상품",
    )


def test_도식이_매출원_하나를_빠뜨리면_기록이_남고_줄은_그대로다():
    """★ AI 회사 2장 실측 — 용역 매출을 빼고 콘텐츠만 그렸다."""

    rows = (FlowRow(cells=tuple(_인쇄된_콘텐츠_경로), citations=("1",)),)
    report = ComposedReport(
        sections=(_장(BUSINESS_FLOW_SECTION_ID, rows, (_매출_구성_원문,)),)
    )

    검사본, problems = check_diagram_numbers(report, (_조각(_매출_구성_원문),))

    해당 = [p for p in problems if FLOW_REVENUE_STREAM_MISSING_CODE in p]
    assert len(해당) == 1, f"매출원 누락이 기록되지 않았습니다: {problems}"
    assert "1개" in 해당[0] and "용역" in 해당[0]
    assert "콘텐츠" not in 해당[0], "도식에 있는 매출원까지 누락으로 셌습니다"
    assert 검사본.sections[0].flow_rows == rows, "기록만 남겨야 하는데 줄을 뺐습니다"


def test_매출원_두_줄을_다_그리면_기록이_없다():
    """★ 음성 대조 — 둘 다 그린 도식까지 잡으면 이 검사는 못 쓴다."""

    rows = (
        FlowRow(cells=tuple(_인쇄된_콘텐츠_경로), citations=("1",)),
        FlowRow(
            cells=(
                "AI 소프트웨어 개발 인력",
                "AI 소프트웨어 개발 용역",
                "용역 완료일에 수익 인식",
                "",
            ),
            citations=("1",),
        ),
    )
    report = ComposedReport(
        sections=(_장(BUSINESS_FLOW_SECTION_ID, rows, (_매출_구성_원문,)),)
    )

    _검사본, problems = check_diagram_numbers(report, (_조각(_매출_구성_원문),))

    assert [p for p in problems if FLOW_REVENUE_STREAM_MISSING_CODE in p] == []


def test_매출원_커버리지는_도식_줄이_인용하지_않은_장_조각도_본다():
    """★ 매출 구성 절을 산문만 인용하는 일이 흔하다 — 그때도 봐야 한다."""

    rows = (FlowRow(cells=tuple(_인쇄된_콘텐츠_경로), citations=("2",)),)
    section = ComposedSection(
        section_id=BUSINESS_FLOW_SECTION_ID,
        sentences=(
            ComposedSentence(
                text="회사는 두 가지로 수익을 낸다.",
                citations=("1",),
                grade="확인",
            ),
        ),
        flow_rows=rows,
    )
    fragments = (_조각(_매출_구성_원문, "1"), _조각("AI 콘텐츠 서비스를 낸다.", "2"))

    _검사본, problems = check_diagram_numbers(
        ComposedReport(sections=(section,)), fragments
    )

    assert [p for p in problems if FLOW_REVENUE_STREAM_MISSING_CODE in p]


def test_다른_장의_도식은_매출원_커버리지를_보지_않는다():
    """7장은 매출원 표가 아니다 — 장을 넓히면 엉뚱한 기록이 쌓인다."""

    rows = (FlowRow(cells=("AI 콘텐츠", "콘텐츠 제공", "병원"), citations=("1",)),)
    report = ComposedReport(
        sections=(_장(OPERATIONS_FLOW_SECTION_ID, rows, (_매출_구성_원문,)),)
    )

    _검사본, problems = check_diagram_numbers(report, (_조각(_매출_구성_원문),))

    assert [p for p in problems if FLOW_REVENUE_STREAM_MISSING_CODE in p] == []


def test_매출원_판정은_제품서비스_칸을_이름으로_찾는다():
    """칸 번호를 코드에 박으면 머리말 순서가 바뀔 때 조용히 어긋난다."""

    칸 = BUSINESS_FLOW_HEADERS.index(BUSINESS_FLOW_PRODUCT_HEADER)
    cells = ["", "", "", ""]
    cells[칸] = "AI 콘텐츠 서비스"
    rows = (FlowRow(cells=tuple(cells), citations=("1",)),)

    assert missing_revenue_streams(
        rows, BUSINESS_FLOW_SECTION_ID, (_매출_구성_원문,)
    ) == ("용역",)


# ══════════════════════════════════════════════════════════
# ③ 제품/상품 모순 (7장)
# ══════════════════════════════════════════════════════════


def test_산문이_제품_주력이라고_하면_상품_경로를_뺀다():
    """★ 의료기기 회사 실측 — 한 쪽 안에서 두 말을 하면 둘 다 못 믿는다."""

    rows = (
        FlowRow(cells=("상품 매입", "유통", "병원·의료기관"), citations=("1",)),
        FlowRow(
            cells=("폴리우레탄 수액세트", "의료기관에 공급", "병원·의료기관"),
            citations=("1",),
        ),
    )
    report = ComposedReport(
        sections=(_장(OPERATIONS_FLOW_SECTION_ID, rows, (_제품_주력_문장,)),)
    )

    검사본, problems = check_diagram_numbers(
        report, (_조각("의료기기 제조 및 판매업"),)
    )

    assert 검사본.sections[0].flow_rows == (rows[1],)
    assert [p for p in problems if FLOW_PRODUCT_GOODS_CONFLICT_CODE in p]


def test_산문이_주력을_안_밝히면_아무_줄도_빼지_않는다():
    """★ 음성 대조 — 근거 없이 빼면 정상 도식이 사라진다."""

    rows = (FlowRow(cells=("상품 매입", "유통", "병원·의료기관"), citations=("1",)),)
    report = ComposedReport(
        sections=(
            _장(OPERATIONS_FLOW_SECTION_ID, rows, ("의료기기를 만들어 판다.",)),
        )
    )

    검사본, problems = check_diagram_numbers(report, (_조각("의료기기 제조 및 판매업"),))

    assert 검사본.sections[0].flow_rows == rows
    assert [p for p in problems if FLOW_PRODUCT_GOODS_CONFLICT_CODE in p] == []


def test_주력_경로가_이미_있으면_상품_경로도_남긴다():
    """★ 음성 대조 — 둘 다 파는 회사는 둘 다 그리는 것이 맞는 도식이다.

    실측 회사도 상품매출 37.2억이 실재한다. 주력 줄이 함께 있으면 산문과
    어긋나지 않으므로 빼면 «있는 경로»를 지우는 것이 된다.
    """

    rows = (
        FlowRow(cells=("상품 매입", "유통", "병원·의료기관"), citations=("1",)),
        FlowRow(cells=("제품 생산", "의료기관에 공급", "병원"), citations=("1",)),
    )
    report = ComposedReport(
        sections=(_장(OPERATIONS_FLOW_SECTION_ID, rows, (_제품_주력_문장,)),)
    )

    검사본, problems = check_diagram_numbers(
        report, (_조각("의료기기 제조 및 판매업"),)
    )

    assert 검사본.sections[0].flow_rows == rows
    assert [p for p in problems if FLOW_PRODUCT_GOODS_CONFLICT_CODE in p] == []


def test_두_낱말이_다_있는_칸은_어느_쪽도_부정하지_않는다():
    rows = (
        FlowRow(cells=("제품·상품 매입", "유통", "병원·의료기관"), citations=("1",)),
    )
    report = ComposedReport(
        sections=(_장(OPERATIONS_FLOW_SECTION_ID, rows, (_제품_주력_문장,)),)
    )

    검사본, _problems = check_diagram_numbers(report, (_조각("의료기기 제조 및 판매업"),))

    assert 검사본.sections[0].flow_rows == rows


def test_모순_판정은_시작_칸을_이름으로_찾는다():
    assert OPERATIONS_FLOW_ORIGIN_HEADER in OPERATIONS_FLOW_HEADERS
    assert BUSINESS_FLOW_PRODUCT_HEADER in BUSINESS_FLOW_HEADERS


# ══════════════════════════════════════════════════════════
# ④ 작가 프롬프트
# ══════════════════════════════════════════════════════════


def test_두_도식_안내가_일반어_한_낱말을_금지한다():
    for guide in (BUSINESS_FLOW_GUIDE, OPERATIONS_FLOW_GUIDE):
        assert "일반어" in guide
        assert "「고객」" in guide or "「상품」" in guide


def test_2장_안내가_매출원을_빠뜨리지_말라고_말한다():
    assert "매출원이 둘 이상이면" in BUSINESS_FLOW_GUIDE
    assert "빠뜨리지 않는다" in BUSINESS_FLOW_GUIDE
