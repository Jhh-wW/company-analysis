"""3장 카드에 대표 이름을 «결정적으로» 올리는 보강 — 업종을 가리지 않는다.

★ 이 시험이 지키는 것 (2026-09-06 실측 2회):
  ① 작가가 이름을 하나도 안 쓰면 조각 글자만 투영한 카드 하나가 «반드시» 붙는다.
  ② 작가가 이미 썼으면 아무것도 안 붙는다(중복 카드를 만들지 않는다).
  ③ 붙인 카드의 이름은 3장 이름 «접지 검사»를 그대로 통과한다 — 검사를
     다시 돌려도 제목 칸이 안 비워진다.
  ④ 인용은 그 이름이 나온 조각뿐이고, 장별 근거 소유권 밖 조각은 안 쓴다.
  ⑤ 작가 카드 3개 + 결정적 카드 1개 = 4개를 넘지 않는다.
  ⑥ 웹·PDF·노션 세 채널이 이 카드를 실제로 그린다.

★ 픽스처의 이름은 «가공 이름»이다. 실존 회사·그룹·상품 이름을 시험에 박으면
  지워도 되돌아온다.
"""

from __future__ import annotations

import pytest

from src.features.composer.constants import (
    PORTFOLIO_TABLE_HEADERS,
    PORTFOLIO_TABLE_SECTION_ID,
)
from src.features.composer.diagram_check import (
    PORTFOLIO_NAME_NOT_IN_SOURCE_CODE,
    check_diagram_numbers,
)
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FlowRow,
)
from src.features.composer.portfolio_name_card import (
    BLOCKED_ALREADY_USED,
    BLOCKED_NAME_NOT_IN_SOURCE,
    BLOCKED_NO_SECTION,
    BLOCKED_TOO_FEW_NAMES,
    NAME_CARD_MAX_NAMES,
    NAME_CARD_REASON,
    NAME_CARD_ROLE,
    PORTFOLIO_NAME_CARD_STEP,
    augment_portfolio_name_card,
    portfolio_name_card_steps,
)
from src.features.composer.portfolio_names import (
    MIN_REPRESENTATIVE_NAMES_FOR_CARD,
    portfolio_name_usage,
)
from src.shared.name_fragments.constants import (
    NAME_KIND_LABELS,
    NAME_KIND_SEGMENT,
    REPRESENTATIVE_NAME_LABELS,
    compose_name_location,
)


IP_LABEL, PRODUCT_LABEL, BRAND_LABEL = REPRESENTATIVE_NAME_LABELS
SEGMENT_LABEL = NAME_KIND_LABELS[NAME_KIND_SEGMENT]
_KIND_OF_LABEL = {label: kind for kind, label in NAME_KIND_LABELS.items()}

#: 업종 세 가지의 «가공» 이름 표. (표 제목, 종류 라벨, 행 원문, 이름)
_INDUSTRIES: dict[str, tuple[str, str, tuple[str, ...], tuple[str, ...]]] = {
    "엔터": (
        "나. 주요 아티스트 전속계약",
        IP_LABEL,
        (
            "㈜가나다뮤직 | 하늘소년단",
            "㈜가나다뮤직 | 바다소녀들",
            "㈜라마바뮤직 | 별무리",
        ),
        ("하늘소년단", "바다소녀들", "별무리"),
    ),
    "제조": (
        "가. 주요 제품 및 서비스의 현황",
        PRODUCT_LABEL,
        (
            "메모리 | 가람메모리 D9 | 1,204 | 41.2%",
            "메모리 | 나래메모리 D7 | 902 | 30.9%",
            "파운드리 | 다솜공정 4나노 | 511 | 17.5%",
        ),
        ("가람메모리 D9", "나래메모리 D7", "다솜공정 4나노"),
    ),
    "카드사": (
        "가. 주요 상품의 현황",
        PRODUCT_LABEL,
        (
            "신용카드 | 라온카드 포인트형 | 320 | 18.4%",
            "신용카드 | 마루카드 캐시백형 | 285 | 16.1%",
            "체크카드 | 바람체크카드 | 141 | 8.0%",
        ),
        ("라온카드 포인트형", "마루카드 캐시백형", "바람체크카드"),
    ),
}


def _name_fragment(
    fragment_id: str,
    title: str,
    label: str,
    text: str,
    name: str,
    *,
    row: int,
) -> CollectedFragment:
    return CollectedFragment(
        fragment_id=fragment_id,
        kind="dart_business_report",
        text=text,
        location=compose_name_location(
            f"{title} · {row}행", _KIND_OF_LABEL[label], name
        ),
        supported_claim_slots=("portfolio:product_role",),
    )


def _industry_fragments(
    industry: str, count: int | None = None
) -> tuple[CollectedFragment, ...]:
    title, label, texts, names = _INDUSTRIES[industry]
    limit = len(names) if count is None else count
    return tuple(
        _name_fragment(
            str(index + 1),
            title,
            label,
            texts[index],
            names[index],
            row=index * 3 + 2,
        )
        for index in range(limit)
    )


def _report(*rows: FlowRow) -> ComposedReport:
    return ComposedReport(
        sections=(
            ComposedSection(
                section_id=PORTFOLIO_TABLE_SECTION_ID,
                sentences=(
                    ComposedSentence(
                        text="회사는 공식 제품을 운영한다.",
                        citations=("1",),
                        grade="확인",
                    ),
                ),
                flow_rows=rows,
            ),
        )
    )


def _segment_row(citation: str = "1") -> FlowRow:
    """이름이 하나도 없는 «부문 카드» — 실측에서 작가가 실제로 낸 모양."""

    return FlowRow(
        cells=("사업부문 하나", "부문 설명", "이름 표에 실린 대상이다", "주력"),
        citations=(citation,),
    )


def _portfolio_rows(report: ComposedReport) -> tuple[FlowRow, ...]:
    return next(
        section.flow_rows
        for section in report.sections
        if section.section_id == PORTFOLIO_TABLE_SECTION_ID
    )


# ══════════════════════════════════════════════════════════
# ① 이름을 안 쓴 카드에는 «반드시» 이름 카드가 붙는다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("industry", sorted(_INDUSTRIES))
def test_이름을_안_쓴_카드에_이름_카드가_붙는다(industry: str) -> None:
    _title, label, _texts, names = _INDUSTRIES[industry]
    fragments = _industry_fragments(industry)

    result = augment_portfolio_name_card(_report(_segment_row()), fragments)

    assert result.added is True
    assert result.blocked_reason == ""
    assert result.name_count == len(names)
    assert result.counts_by_label == {label: len(names)}
    rows = _portfolio_rows(result.report)
    assert len(rows) == 2
    card = rows[-1]
    # 제목 칸 = 첫 번째 이름 «그대로». 종류 라벨이 아니다.
    assert card.cells[0] == names[0]
    # 범위 칸 = 이름 전부를 원문 순서로.
    assert card.cells[1] == "·".join(names)
    assert card.cells[2] == NAME_CARD_REASON
    assert card.cells[3] == NAME_CARD_ROLE
    assert len(card.cells) == len(PORTFOLIO_TABLE_HEADERS)
    # 인용 = 그 이름들이 나온 조각 전부.
    assert card.citations == tuple(
        fragment.fragment_id for fragment in fragments
    )


def test_이름_카드가_붙으면_미사용_판정이_풀린다() -> None:
    """두 표식이 «동시에 켜질 수 없다» — 켜지면 어딘가 어긋난 것이다."""

    fragments = _industry_fragments("엔터")
    before = _report(_segment_row())
    assert portfolio_name_usage(before, fragments).unused is True

    result = augment_portfolio_name_card(before, fragments)

    assert portfolio_name_usage(result.report, fragments).unused is False


def test_작가_카드가_없어도_이름_카드는_붙는다() -> None:
    """표가 통째로 비었을 때가 이 보강이 가장 필요한 경우다."""

    result = augment_portfolio_name_card(_report(), _industry_fragments("엔터"))

    assert result.added is True
    assert len(_portfolio_rows(result.report)) == 1


# ══════════════════════════════════════════════════════════
# ② 작가가 이미 썼으면 아무것도 안 붙는다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("industry", sorted(_INDUSTRIES))
def test_작가가_이름을_썼으면_안_붙는다(industry: str) -> None:
    _title, _label, _texts, names = _INDUSTRIES[industry]
    fragments = _industry_fragments(industry)
    written = FlowRow(
        cells=(names[0], "·".join(names), "출시했다", "주력"),
        citations=tuple(fragment.fragment_id for fragment in fragments),
    )
    report = _report(written)

    result = augment_portfolio_name_card(report, fragments)

    assert result.added is False
    assert result.blocked_reason == BLOCKED_ALREADY_USED
    # 보고서를 «건드리지도» 않는다.
    assert result.report is report


def test_이름을_한_칸에서만_써도_안_붙는다() -> None:
    """제목이 아니라 범위 칸에만 이름이 있어도 「썼다」로 본다."""

    fragments = _industry_fragments("엔터")
    _title, _label, _texts, names = _INDUSTRIES["엔터"]
    row = FlowRow(cells=("음악 부문", names[1], "출시했다", ""), citations=("2",))

    result = augment_portfolio_name_card(_report(row), fragments)

    assert result.added is False
    assert result.blocked_reason == BLOCKED_ALREADY_USED


def test_이름이_하한보다_적으면_안_붙는다() -> None:
    fragments = _industry_fragments("엔터", MIN_REPRESENTATIVE_NAMES_FOR_CARD - 1)

    result = augment_portfolio_name_card(_report(_segment_row()), fragments)

    assert result.added is False
    assert result.blocked_reason == BLOCKED_TOO_FEW_NAMES


def test_하한_경계에서는_붙는다() -> None:
    """N-1은 안 붙고 N은 붙는다 — 하한이 실제로 그 수인지 본다."""

    fragments = _industry_fragments("엔터", MIN_REPRESENTATIVE_NAMES_FOR_CARD)

    result = augment_portfolio_name_card(_report(_segment_row()), fragments)

    assert result.added is True
    assert result.name_count == MIN_REPRESENTATIVE_NAMES_FOR_CARD


def test_부문명만_있는_조각으로는_안_붙는다() -> None:
    """사업부문은 «회사를 대표하는 이름»이 아니다 — 판정 정본과 같은 규칙."""

    fragments = tuple(
        _name_fragment(
            str(index + 1),
            "가. 사업부문 현황",
            SEGMENT_LABEL,
            f"부문 {index} | 매출",
            f"부문 {index}",
            row=index + 2,
        )
        for index in range(3)
    )

    result = augment_portfolio_name_card(_report(_segment_row()), fragments)

    assert result.added is False
    assert result.blocked_reason == BLOCKED_TOO_FEW_NAMES


def test_조각_id가_없는_이름은_후보에서_빠진다() -> None:
    """인용할 수 없는 이름은 카드에 못 싣는다 — 빈 인용은 봉인을 깬다."""

    title, label, texts, names = _INDUSTRIES["엔터"]
    fragments = tuple(
        _name_fragment("", title, label, texts[index], names[index], row=index + 2)
        for index in range(3)
    )

    result = augment_portfolio_name_card(_report(_segment_row()), fragments)

    assert result.added is False
    assert result.blocked_reason == BLOCKED_TOO_FEW_NAMES


def test_3장이_없으면_안_붙는다() -> None:
    report = ComposedReport(
        sections=(ComposedSection(section_id="identity", sentences=()),)
    )

    result = augment_portfolio_name_card(report, _industry_fragments("엔터"))

    assert result.added is False
    assert result.blocked_reason == BLOCKED_NO_SECTION
    assert result.report is report


# ══════════════════════════════════════════════════════════
# ③ 접지 — 이름이 인용 원문에 «글자 그대로» 있어야 한다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("industry", sorted(_INDUSTRIES))
def test_붙인_카드는_이름_접지_검사를_그대로_통과한다(industry: str) -> None:
    """검사를 다시 돌려도 제목 칸이 안 비워진다(멱등).

    ★ 이 시험이 없으면 「붙였는데 화면에서는 제목이 빈 카드」가 조용히 나간다.
    """

    fragments = _industry_fragments(industry)
    result = augment_portfolio_name_card(_report(), fragments)
    assert result.added is True

    checked, problems = check_diagram_numbers(result.report, fragments)

    assert problems == ()
    assert _portfolio_rows(checked) == _portfolio_rows(result.report)


@pytest.mark.parametrize("industry", sorted(_INDUSTRIES))
def test_작가_카드가_비워져도_이름_카드는_남는다(industry: str) -> None:
    """작가 부문 카드는 근거가 없어 제목이 비워지는데 우리 카드는 그대로다.

    ★ 이 대비가 시험의 «음성 대조»다 — 검사가 실제로 돌고 있으면서도 우리
      카드만 살아남는다는 뜻이다. 검사가 안 돌면 두 줄 다 그대로라 이 시험이
      아무것도 안 지킨다.
    """

    fragments = _industry_fragments(industry)
    result = augment_portfolio_name_card(_report(_segment_row()), fragments)
    assert result.added is True

    checked, problems = check_diagram_numbers(result.report, fragments)
    rows = _portfolio_rows(checked)

    assert any(PORTFOLIO_NAME_NOT_IN_SOURCE_CODE in problem for problem in problems)
    assert rows[0].cells[0] == ""  # 작가 부문 카드의 제목은 비워졌다
    assert rows[-1] == _portfolio_rows(result.report)[-1]  # 우리 카드는 그대로


def test_이름이_원문에_없으면_카드를_안_만든다() -> None:
    """상류(파서)가 그 보장을 잃으면 여기서 fail-closed로 막는다."""

    fragments = tuple(
        _name_fragment(
            str(index + 1),
            "나. 주요 아티스트 전속계약",
            IP_LABEL,
            "㈜가나다뮤직 | 소속 아티스트",  # 이름이 원문에 없다
            f"없는이름{index}",
            row=index + 2,
        )
        for index in range(3)
    )

    result = augment_portfolio_name_card(_report(_segment_row()), fragments)

    assert result.added is False
    assert result.blocked_reason == BLOCKED_NAME_NOT_IN_SOURCE


def test_이름_안의_수는_그대로_남는다() -> None:
    """모델명·공정명의 수를 빼거나 고치지 않는다 — 도식 수치 검사도 통과한다."""

    fragments = _industry_fragments("제조")
    _title, _label, _texts, names = _INDUSTRIES["제조"]

    result = augment_portfolio_name_card(_report(), fragments)
    _checked, problems = check_diagram_numbers(result.report, fragments)

    assert _portfolio_rows(result.report)[0].cells[0] == names[0]
    assert "D9" in _portfolio_rows(result.report)[0].cells[1]
    assert problems == ()


# ══════════════════════════════════════════════════════════
# ④ 인용 — 장별 근거 소유권 밖 조각은 안 쓴다
# ══════════════════════════════════════════════════════════


def test_허용_밖_조각은_후보에서_빠진다() -> None:
    fragments = _industry_fragments("엔터")
    allowed = frozenset({"1", "2"})

    result = augment_portfolio_name_card(
        _report(_segment_row()), fragments, allowed_fragment_ids=allowed
    )

    assert result.added is True
    assert set(_portfolio_rows(result.report)[-1].citations) <= allowed
    assert result.name_count == 2


def test_허용_밖만_남으면_카드를_안_만든다() -> None:
    fragments = _industry_fragments("엔터")

    result = augment_portfolio_name_card(
        _report(_segment_row()), fragments, allowed_fragment_ids=frozenset({"1"})
    )

    assert result.added is False
    assert result.blocked_reason == BLOCKED_TOO_FEW_NAMES


# ══════════════════════════════════════════════════════════
# ⑤ 개수 — 이름 상한과 카드 상한
# ══════════════════════════════════════════════════════════


def test_이름은_상한까지만_싣는다() -> None:
    title = "가. 주요 제품 및 서비스의 현황"
    fragments = tuple(
        _name_fragment(
            str(index + 1),
            title,
            PRODUCT_LABEL,
            f"부문 | 가나제품 {index}호 | 매출",
            f"가나제품 {index}호",
            row=index + 2,
        )
        for index in range(NAME_CARD_MAX_NAMES + 4)
    )

    result = augment_portfolio_name_card(_report(), fragments)

    assert result.name_count == NAME_CARD_MAX_NAMES
    assert len(_portfolio_rows(result.report)[0].citations) == NAME_CARD_MAX_NAMES


def test_작가_카드_3개면_전체가_4개다() -> None:
    """작가 상한 3 + 결정적 카드 1. 안내문이 정한 상한을 넘기지 않는다."""

    fragments = _industry_fragments("엔터")
    report = _report(*(_segment_row(str(index + 1)) for index in range(3)))

    result = augment_portfolio_name_card(report, fragments)

    assert len(_portfolio_rows(result.report)) == 4


def test_종류가_섞이면_종류별로_묶는다() -> None:
    fragments = (
        _name_fragment(
            "1", "나. 주요 아티스트 전속계약", IP_LABEL, "㈜가나다뮤직 | 하늘소년단", "하늘소년단", row=2
        ),
        _name_fragment(
            "2", "나. 주요 아티스트 전속계약", IP_LABEL, "㈜가나다뮤직 | 바다소녀들", "바다소녀들", row=3
        ),
        _name_fragment(
            "3", "가. 주요 상표", BRAND_LABEL, "브랜드 | 하늘상점 | 운영", "하늘상점", row=4
        ),
    )

    result = augment_portfolio_name_card(_report(), fragments)

    assert _portfolio_rows(result.report)[0].cells[1] == (
        f"{IP_LABEL}: 하늘소년단·바다소녀들 / {BRAND_LABEL}: 하늘상점"
    )


# ══════════════════════════════════════════════════════════
# ⑥ 실행 기록 단계
# ══════════════════════════════════════════════════════════


class _Output:
    def __init__(self, **fields: object) -> None:
        self.__dict__.update(fields)


def test_카드를_붙인_실행은_단계로_남는다() -> None:
    output = _Output(
        portfolio_name_card_count=5,
        portfolio_name_card_counts_by_label=((IP_LABEL, 3), (PRODUCT_LABEL, 2)),
    )

    assert portfolio_name_card_steps(output) == [
        {
            "step": PORTFOLIO_NAME_CARD_STEP,
            "이름수": 5,
            "종류별": {IP_LABEL: 3, PRODUCT_LABEL: 2},
        }
    ]


def test_안_붙인_실행은_단계를_안_남긴다() -> None:
    assert portfolio_name_card_steps(_Output(portfolio_name_card_count=0)) == []


def test_이_필드를_모르는_옛_결과도_그대로_지나간다() -> None:
    assert portfolio_name_card_steps(_Output()) == []
    assert portfolio_name_card_steps(_Output(portfolio_name_card_count=None)) == []
    # 개수는 있는데 종류별이 깨져 있어도 단계는 남는다(진단이 사라지면 안 된다).
    assert portfolio_name_card_steps(
        _Output(
            portfolio_name_card_count=4,
            portfolio_name_card_counts_by_label="깨진 값",
        )
    ) == [{"step": PORTFOLIO_NAME_CARD_STEP, "이름수": 4, "종류별": {}}]
