"""3장 「회사가 공시한 대표 이름」 표를 만드는 규칙과 «자체 검사»를 잠근다.

★ 이름은 전부 가공이다 — 실존 회사·그룹·상품 이름을 시험에 박지 않는다.
★ AI·네트워크 0회.
"""

from __future__ import annotations

import pytest

from src.features.composer.port import CollectedFragment
from src.features.composer.portfolio_name_table import (
    BLOCKED_CITE_NOT_RESOLVABLE,
    BLOCKED_LABEL_NOT_IN_LOCATION,
    BLOCKED_NAME_NOT_IN_SOURCE,
    BLOCKED_TOO_FEW_NAMES,
    NAME_TABLE_CAPTION_PREFIX,
    NAME_TABLE_HEADERS,
    NAME_TABLE_MAX_NAMES_PER_KIND,
    NAME_TABLE_NAME_SEPARATOR,
    NAME_TABLE_ROW_LABELS,
    PORTFOLIO_NAME_TABLE_BLOCKED_STEP,
    PORTFOLIO_NAME_TABLE_STEP,
    build_portfolio_name_table,
    portfolio_name_table_steps,
)
from src.shared.name_fragments.constants import (
    NAME_KIND_BRAND,
    NAME_KIND_IP,
    NAME_KIND_LABELS,
    NAME_KIND_PRODUCT,
    NAME_KIND_SEGMENT,
    compose_name_location,
)


_TABLE_TITLE = "나. 주요 제품 및 서비스 현황"


def _fragment(
    fragment_id: str,
    kind: str,
    name: str,
    *,
    text: str | None = None,
    row: int = 2,
    title: str = _TABLE_TITLE,
) -> CollectedFragment:
    """이름 표 파서가 만드는 모양 그대로의 조각 하나."""

    return CollectedFragment(
        fragment_id=fragment_id,
        kind="사업내용",
        text=text if text is not None else f"㈜가나다 | {name} | 국내",
        location=compose_name_location(f"{title} · {row}행", kind, name),
    )


# ══════════════════════════════════════════════════════════
# ① 표의 모양 — 행은 «종류», 칸은 이름
# ══════════════════════════════════════════════════════════


def test_행은_종류_라벨이고_칸은_그_종류의_이름들이다() -> None:
    result = build_portfolio_name_table(
        (
            _fragment("3", NAME_KIND_PRODUCT, "가나전자레인지"),
            _fragment("4", NAME_KIND_PRODUCT, "가나냉장고"),
        )
    )

    assert result.table is not None
    assert result.table.headers == NAME_TABLE_HEADERS == ("구분", "이름")
    assert result.table.rows == (("제품", "가나전자레인지·가나냉장고"),)
    # 「제목 = 첫 이름」이던 옛 카드로 돌아가지 않는다.
    assert result.table.rows[0][0] != "가나전자레인지"


def test_종류가_섞이면_대표IP_제품_브랜드_순으로_행이_선다() -> None:
    result = build_portfolio_name_table(
        (
            # 일부러 뒤섞어 넣는다 — 조각 순서가 아니라 «종류 순서»를 따른다.
            _fragment("3", NAME_KIND_BRAND, "가나프리미엄"),
            _fragment("4", NAME_KIND_PRODUCT, "가나냉장고"),
            _fragment("5", NAME_KIND_IP, "하늘소년단"),
        )
    )

    assert result.table is not None
    assert [row[0] for row in result.table.rows] == ["대표 IP", "제품", "브랜드"]
    assert list(NAME_TABLE_ROW_LABELS) == ["대표 IP", "제품", "브랜드"]


def test_사업부문은_대표_이름이_아니라_표에_안_들어간다() -> None:
    result = build_portfolio_name_table(
        (
            _fragment("3", NAME_KIND_SEGMENT, "가전부문"),
            _fragment("4", NAME_KIND_SEGMENT, "반도체부문"),
        )
    )

    assert result.table is None
    # 대표 이름이 한 건도 아니므로 «사유도 없다» — 흔한 정상 상태다.
    assert result.blocked_reason == ""
    assert result.candidate_count == 0


def test_캡션에_실린_이름_수가_붙는다() -> None:
    result = build_portfolio_name_table(
        (
            _fragment("3", NAME_KIND_IP, "하늘소년단"),
            _fragment("4", NAME_KIND_IP, "바다소녀들"),
            _fragment("5", NAME_KIND_PRODUCT, "가나냉장고"),
        )
    )

    assert result.table is not None
    assert result.table.caption == f"{NAME_TABLE_CAPTION_PREFIX} (3개)"
    assert result.table.name_count == 3
    assert result.table.counts_by_label == (("대표 IP", 2), ("제품", 1))


# ══════════════════════════════════════════════════════════
# ② 상한과 「외 N」
# ══════════════════════════════════════════════════════════


def test_상한을_넘기면_외_N으로_남기고_그_조각도_인용한다() -> None:
    over = NAME_TABLE_MAX_NAMES_PER_KIND + 3
    fragments = tuple(
        _fragment(str(10 + index), NAME_KIND_PRODUCT, f"가나제품{index}")
        for index in range(over)
    )

    result = build_portfolio_name_table(fragments)

    assert result.table is not None
    cell = result.table.rows[0][1]
    shown = cell.split(" 외 ")[0].split(NAME_TABLE_NAME_SEPARATOR)
    assert len(shown) == NAME_TABLE_MAX_NAMES_PER_KIND
    assert cell.endswith("외 3")
    # 캡션은 «실린» 이름 수만 센다.
    assert result.table.caption == (
        f"{NAME_TABLE_CAPTION_PREFIX} ({NAME_TABLE_MAX_NAMES_PER_KIND}개)"
    )
    # 「외 3」의 3도 근거가 있어야 한다 — 접힌 이름의 조각까지 인용한다.
    assert len(result.table.row_fragment_ids[0]) == over
    assert result.table.fragment_ids == tuple(str(10 + i) for i in range(over))


def test_상한_경계에서는_외_N을_안_붙인다() -> None:
    fragments = tuple(
        _fragment(str(10 + index), NAME_KIND_PRODUCT, f"가나제품{index}")
        for index in range(NAME_TABLE_MAX_NAMES_PER_KIND)
    )

    result = build_portfolio_name_table(fragments)

    assert result.table is not None
    assert "외 " not in result.table.rows[0][1]


# ══════════════════════════════════════════════════════════
# ③ 자체 검사 — 하나라도 어긋나면 «표를 안 만든다»
# ══════════════════════════════════════════════════════════


def test_이름이_하나뿐이면_표를_안_만든다() -> None:
    result = build_portfolio_name_table(
        (_fragment("3", NAME_KIND_PRODUCT, "가나냉장고"),)
    )

    assert result.table is None
    assert result.blocked_reason == BLOCKED_TOO_FEW_NAMES
    assert result.candidate_count == 1


def test_이름이_조각_원문에_없으면_표를_안_만든다() -> None:
    """(a) 접지 — 원문위치에는 이름이 있는데 원문에는 없는 어긋난 조각."""

    result = build_portfolio_name_table(
        (
            _fragment("3", NAME_KIND_PRODUCT, "가나냉장고"),
            _fragment(
                "4",
                NAME_KIND_PRODUCT,
                "가나세탁기",
                text="㈜가나다 | 전혀 다른 값 | 국내",
            ),
        )
    )

    assert result.table is None
    assert result.blocked_reason == BLOCKED_NAME_NOT_IN_SOURCE


def test_라벨이_조각_원문위치와_다르면_표를_안_만든다() -> None:
    """(b) 라벨 — 인용할 조각의 원문위치가 그 라벨을 안 담고 있다.

    ★ 왜 공개 함수가 아니라 자체 검사 함수를 직접 부르나 — 지금의 상류
      계약에서는 라벨을 «원문위치에서 읽어» 만들기 때문에 둘이 어긋난
      후보를 공개 입구로는 만들 수 없다. 그래도 이 검사를 두는 이유는
      상류가 언젠가 라벨을 다른 곳에서 가져오게 바뀔 때 «조용히» 틀린
      구분이 인쇄되는 것을 막기 위해서다. 그 방어가 실제로 도는지를
      여기서 잠근다.
    """

    from src.features.composer.portfolio_name_table import _verified_names
    from src.features.composer.portfolio_names import RepresentativeName

    # 원문위치는 「사업부문」이라고 적혀 있는데 후보는 「제품」이라고 말한다.
    mismatched = CollectedFragment(
        fragment_id="6",
        kind="사업내용",
        text="㈜가나다 | 가나건조기 | 국내",
        location=compose_name_location(
            f"{_TABLE_TITLE} · 4행", NAME_KIND_SEGMENT, "가나건조기"
        ),
    )
    candidate = RepresentativeName(
        label=NAME_KIND_LABELS[NAME_KIND_PRODUCT],
        name="가나건조기",
        fragment_id="6",
        text=mismatched.text,
    )

    verified, reason = _verified_names((candidate,), {"6": mismatched})

    assert verified == ()
    assert reason == BLOCKED_LABEL_NOT_IN_LOCATION
    # 대조군 — 라벨이 맞으면 같은 조각으로 통과한다.
    matched = CollectedFragment(
        fragment_id="6",
        kind="사업내용",
        text=mismatched.text,
        location=compose_name_location(
            f"{_TABLE_TITLE} · 4행", NAME_KIND_PRODUCT, "가나건조기"
        ),
    )
    assert _verified_names((candidate,), {"6": matched}) == ((candidate,), "")


def test_같은_번호를_두_조각이_쓰면_표를_안_만든다() -> None:
    """(c) 인용 — 번호 하나가 두 원문을 가리키면 근거를 못 정한다."""

    result = build_portfolio_name_table(
        (
            _fragment("3", NAME_KIND_PRODUCT, "가나냉장고"),
            _fragment("3", NAME_KIND_PRODUCT, "가나세탁기", row=5),
        )
    )

    assert result.table is None
    assert result.blocked_reason == BLOCKED_CITE_NOT_RESOLVABLE


def test_조각_id가_없는_이름은_후보에서_뺀다() -> None:
    """인용할 수 없는 이름은 애초에 안 싣는다 — 빈 인용은 봉인을 막는다."""

    result = build_portfolio_name_table(
        (
            _fragment("", NAME_KIND_PRODUCT, "가나냉장고"),
            _fragment("4", NAME_KIND_PRODUCT, "가나세탁기"),
        )
    )

    assert result.table is None
    assert result.blocked_reason == BLOCKED_TOO_FEW_NAMES


# ══════════════════════════════════════════════════════════
# ④ 장별 근거 소유권
# ══════════════════════════════════════════════════════════


def test_3장_소유_밖_조각은_후보에서_뺀다() -> None:
    fragments = (
        _fragment("3", NAME_KIND_PRODUCT, "가나냉장고"),
        _fragment("4", NAME_KIND_PRODUCT, "가나세탁기"),
        _fragment("9", NAME_KIND_PRODUCT, "남의장제품"),
    )

    result = build_portfolio_name_table(
        fragments, allowed_fragment_ids=frozenset({"3", "4"})
    )

    assert result.table is not None
    assert result.table.fragment_ids == ("3", "4")
    assert "남의장제품" not in result.table.rows[0][1]


def test_소유_밖으로_이름이_하나만_남으면_표를_안_만든다() -> None:
    fragments = (
        _fragment("3", NAME_KIND_PRODUCT, "가나냉장고"),
        _fragment("9", NAME_KIND_PRODUCT, "남의장제품"),
    )

    result = build_portfolio_name_table(
        fragments, allowed_fragment_ids=frozenset({"3"})
    )

    assert result.table is None
    assert result.blocked_reason == BLOCKED_TOO_FEW_NAMES


# ══════════════════════════════════════════════════════════
# ⑤ 실행 기록
# ══════════════════════════════════════════════════════════


class _Output:
    def __init__(self, **fields: object) -> None:
        for key, value in fields.items():
            setattr(self, key, value)


def test_표를_만들면_이름수_종류별_표제목을_남긴다() -> None:
    steps = portfolio_name_table_steps(
        _Output(
            portfolio_name_table_name_count=3,
            portfolio_name_table_counts_by_label=(("대표 IP", 2), ("제품", 1)),
            portfolio_name_table_titles=(_TABLE_TITLE,),
            portfolio_name_table_blocked_reason="",
        )
    )

    assert steps == [
        {
            "step": PORTFOLIO_NAME_TABLE_STEP,
            "이름수": 3,
            "종류별": {"대표 IP": 2, "제품": 1},
            "표제목": [_TABLE_TITLE],
        }
    ]


def test_못_만들면_사유를_남긴다() -> None:
    steps = portfolio_name_table_steps(
        _Output(
            portfolio_name_table_name_count=0,
            portfolio_name_table_blocked_reason=BLOCKED_NAME_NOT_IN_SOURCE,
        )
    )

    assert steps == [
        {
            "step": PORTFOLIO_NAME_TABLE_BLOCKED_STEP,
            "사유": BLOCKED_NAME_NOT_IN_SOURCE,
        }
    ]


def test_이름이_애초에_없으면_아무_줄도_안_남긴다() -> None:
    assert portfolio_name_table_steps(_Output()) == []
    assert (
        portfolio_name_table_steps(
            _Output(
                portfolio_name_table_name_count=0,
                portfolio_name_table_blocked_reason="",
            )
        )
        == []
    )


# ══════════════════════════════════════════════════════════
# ⑥ 정본 순서와 어긋나지 않는다
# ══════════════════════════════════════════════════════════


def test_행_순서는_이름_표_파서의_종류_순서와_같다() -> None:
    """파서의 배분 순서가 바뀌면 표의 행 순서도 같이 바뀌어야 한다.

    ★ 생산 코드끼리는 feature 경계 때문에 서로 import 하지 못한다. 그
      어긋남을 «시험»이 두 상수를 함께 읽어 잡는다.
    """

    from src.features.product_names.constants import NAME_FRAGMENT_KIND_ORDER
    from src.shared.name_fragments.constants import REPRESENTATIVE_NAME_KINDS

    expected = tuple(
        NAME_KIND_LABELS[kind]
        for kind in NAME_FRAGMENT_KIND_ORDER
        if kind in REPRESENTATIVE_NAME_KINDS
    )

    assert NAME_TABLE_ROW_LABELS == expected


@pytest.mark.parametrize("label", NAME_TABLE_ROW_LABELS)
def test_구분_칸은_정본_라벨_글자_그대로다(label: str) -> None:
    assert label in set(NAME_KIND_LABELS.values())
