"""3장 이름 표가 «행마다» 인용한 조각을 부록까지 데려가는지 본다.

★ 왜 필요한가 (2026-09-07 부분 보고서 실측) — 이름 표 한 행에 이름이 여덟 개
  실렸는데 그 이름들은 서로 «다른 조각»에서 왔다. 그런데 표는 캡션 번호 하나만
  들고 있었고, 부록에도 그 조각 한 줄만 올랐다. 독자가 나머지 일곱 이름의
  원문을 찾아갈 길이 없었다. FULL은 공개 봉인이 `source_cites`를 채워 문제가
  없었으므로, 봉인이 없는 실행(부분 보고서 = SHADOW)에서만 조용히 새어 나갔다.

★ 그래서 두 겹을 «따로» 지킨다 —
  ① 표가 행별 번호를 `source_cites`에 싣는가(`_name_report_table`),
  ② 렌더러가 그 번호를 전부 부록 사용 목록에 올리는가(`render_report` 호출부).
  한 겹만 있으면 오히려 출고 검증이 보고서를 통째로 막는다(①만 있으면
  「본문이 인용한 번호가 부록에 없다」, ②만 있으면 「부록에 있는 번호를 본문
  어디에서도 인용하지 않았다」).

★ AI·네트워크 0회. 회사명·이름은 모두 가공이다.
"""

from __future__ import annotations

import pytest

from src.features.composer.constants import PORTFOLIO_TABLE_SECTION_ID
from src.features.composer.pipeline import run_v2
from src.features.composer.port import CollectedFragment
from src.features.composer.portfolio_name_table import (
    NAME_TABLE_CAPTION_PREFIX,
    NAME_TABLE_CAPTION_TEMPLATE,
    NAME_TABLE_HEADERS,
    NAME_TABLE_NAME_SEPARATOR,
    PortfolioNameTable,
)
from src.features.composer.public_manifest import _normalized_source_cites
from src.features.composer.render import (
    _name_report_table,
    citation_numbers_for_fragments,
)
from src.features.composer.tests.test_portfolio_name_table_channels import (
    _fragments,
    _reviewer,
    _writer,
)
from src.features.composer.validate import v2_validation_problems
from src.shared.name_fragments.constants import (
    NAME_KIND_IP,
    NAME_KIND_LABELS,
    NAME_KIND_PRODUCT,
)


# ══════════════════════════════════════════════════════════
# ① 표 하나 — 행별 번호가 전부 실리는가
# ══════════════════════════════════════════════════════════
#
# ★ 기대값을 계산식이 아니라 «글자 그대로» 적는다. 생산 코드에서 다시
#   계산해 오면 그 계산이 통째로 틀려도 시험은 초록으로 남는다.

#: 행이 인용하는 조각 id. 「번호를 그대로 쓴다」는 계약대로 숫자 문자열이다.
_ID_FIRST = "24"
_ID_SECOND = "25"
_ID_THIRD = "33"
#: 두 행이 «같은» 조각을 함께 인용하는 모양. 부록에는 한 줄만 올라야 한다.
_ROW_IDS = (
    (_ID_THIRD, _ID_FIRST, _ID_FIRST),
    (_ID_SECOND, _ID_FIRST),
)
_EXPECTED_SOURCE_CITES = ["[24]", "[25]", "[33]"]
_EXPECTED_CAPTION_CITE = "[24]"

_IP_LABEL = NAME_KIND_LABELS[NAME_KIND_IP]
_PRODUCT_LABEL = NAME_KIND_LABELS[NAME_KIND_PRODUCT]


def _numbers() -> dict[str, int]:
    """조각 id → 부록 표시 번호. 숫자 id는 «그 숫자»가 번호다."""

    return {_ID_FIRST: 24, _ID_SECOND: 25, _ID_THIRD: 33}


def _name_table() -> PortfolioNameTable:
    return PortfolioNameTable(
        caption=NAME_TABLE_CAPTION_TEMPLATE.format(
            prefix=NAME_TABLE_CAPTION_PREFIX, count=3
        ),
        headers=NAME_TABLE_HEADERS,
        rows=(
            (_IP_LABEL, NAME_TABLE_NAME_SEPARATOR.join(("하늘소년단", "바다소녀들"))),
            (_PRODUCT_LABEL, "가나다청소기"),
        ),
        row_fragment_ids=_ROW_IDS,
        name_count=3,
        counts_by_label=((_IP_LABEL, 2), (_PRODUCT_LABEL, 1)),
        table_titles=("나. 주요 아티스트 전속계약",),
    )


def test_이름_표가_행별_조각_번호를_전부_싣는다() -> None:
    table = _name_report_table(_name_table(), _numbers())

    assert table is not None
    # 행마다 다른 조각에서 온 이름이므로 번호가 셋 다 있어야 한다.
    assert table.source_cites == _EXPECTED_SOURCE_CITES
    # 두 행이 함께 쓴 [24]는 «한 번만» 실린다 — 중복은 출고 검증이 막는다.
    assert table.source_cites.count(_EXPECTED_CAPTION_CITE) == 1
    # 캡션은 여전히 대표 번호 하나만 단다(표 제목이 번호로 덮이지 않게).
    assert table.cite == _EXPECTED_CAPTION_CITE


def test_되찾지_못한_조각_번호는_싣지_않는다() -> None:
    """번호를 못 찾은 조각은 «빼고» 나간다 — 헛번호를 인쇄하지 않는다."""

    numbers = _numbers()
    del numbers[_ID_SECOND]

    table = _name_report_table(_name_table(), numbers)

    assert table is not None
    assert table.source_cites == ["[24]", "[33]"]


# ══════════════════════════════════════════════════════════
# ② 공개 봉인과 «같은 값»인가 (FULL이 덮어써도 안 갈리게)
# ══════════════════════════════════════════════════════════


def test_숫자_조각_id는_그대로_부록_번호가_된다() -> None:
    """렌더러와 봉인을 잇는 다리 — 여기가 갈리면 FULL이 통째로 막힌다.

    봉인(`_normalized_source_cites`)은 조각 id «자체»를 번호로 읽고, 렌더러는
    조각 id → 번호 표를 거쳐 읽는다. 숫자 id에서 두 길이 같은 값에 닿는다는
    사실이 두 값이 같아지는 전제다.
    """

    fragments = tuple(
        CollectedFragment(
            fragment_id=fragment_id, kind="회사 공식 자료", text=f"원문 {fragment_id}"
        )
        for fragment_id in (_ID_THIRD, _ID_FIRST, _ID_SECOND)
    )

    assert citation_numbers_for_fragments(fragments) == _numbers()


def test_렌더러_전체출처가_공개봉인_계산과_같다() -> None:
    """FULL은 봉인 값으로 이 칸을 덮어쓴다 — 한 글자만 달라도 봉인이 막는다."""

    name_table = _name_table()
    rendered = _name_report_table(name_table, _numbers())
    # 봉인이 실제로 넣는 입력과 같은 모양(행 순서대로 이어 붙인 조각 id).
    # 중복 제거·정렬은 봉인 함수가 스스로 한다.
    sealed = _normalized_source_cites(
        tuple(
            fragment_id
            for row_ids in name_table.row_fragment_ids
            for fragment_id in row_ids
        )
    )

    assert rendered is not None
    assert tuple(rendered.source_cites) == sealed
    # 두 길이 같은 «틀린 값»에 닿는 경우를 막으려고 기대값도 함께 못 박는다.
    assert list(sealed) == _EXPECTED_SOURCE_CITES


def test_FULL에서_렌더러_값과_봉인_값이_실제로_같다(monkeypatch) -> None:
    """함수 대조로는 못 보는 것 — «실제 FULL 실행»에서 두 값을 나란히 놓는다.

    ★ 봉인은 이 칸을 조용히 덮어쓴다. 그래서 두 값이 달라도 FULL 시험은
      초록으로 남는다(덮어쓴 값이 정본이니까). 그 «조용함»이 위험하다 —
      SHADOW에는 렌더러 값이 그대로 나가므로, 두 값이 갈리는 날 봉인 없는
      실행만 다른 부록을 낸다. 여기서 렌더러가 만든 값을 가로채 최종 값과
      맞춰 그 갈림을 막는다.
    """

    from src.features.composer import render as render_module
    from src.features.composer.tests.test_portfolio_name_table_channels import (
        _full_packets_with_names,
    )
    from src.features.composer.tests.test_section_public_manifest import _run_full

    original = render_module._name_report_table
    captured: list[list[str]] = []

    def spy(name_table, numbers):
        table = original(name_table, numbers)
        if table is not None:
            captured.append(list(table.source_cites))
        return table

    monkeypatch.setattr(render_module, "_name_report_table", spy)
    output, *_ = _run_full(packets=_full_packets_with_names())

    sealed_table = _table(output.report)
    # 봉인이 실제로 붙었다는 뜻 — 이게 없으면 이 시험은 SHADOW를 한 번 더 도는 것.
    assert sealed_table.manifest_ref
    # 가로채기가 «정말 돌았는가»부터 본다. 안 돌았으면 아무것도 안 지킨다.
    assert captured, "렌더러의 이름 표 생성이 한 번도 안 불렸습니다"
    for rendered_cites in captured:
        assert rendered_cites == list(sealed_table.source_cites)


# ══════════════════════════════════════════════════════════
# ③ 봉인 없는 실행(SHADOW = 부분 보고서) 끝까지
# ══════════════════════════════════════════════════════════
#
# ★ 시험 안에서 표를 따로 만들어 검사하지 않는다. 운영 렌더 함수가 실제로
#   내놓은 보고서를 그대로 읽는다 — 배선이 빠지면 여기서 걸린다.


@pytest.fixture(scope="module")
def SHADOW_이름표_보고서():
    """이름 셋이 각각 «다른 조각»에서 온 SHADOW 보고서 하나."""

    output = run_v2(
        "가나다회사",
        _fragments(),
        None,
        writer_ask=_writer,
        reviewer_ask=_reviewer,
        corp_type="상장사",
        as_of_date="2026-09-06",
    )
    assert output.portfolio_name_table_name_count == 3, (
        f"이름 표가 안 붙었습니다 — 사유: "
        f"{output.portfolio_name_table_blocked_reason!r}"
    )
    return output.report


def _table(report):
    section = next(
        section
        for section in report.sections
        if section.cell == PORTFOLIO_TABLE_SECTION_ID
    )
    return next(
        table
        for table in section.tables
        if table.caption.startswith(NAME_TABLE_CAPTION_PREFIX)
    )


def _cited_numbers(report) -> set[int]:
    from src.core.citations import citation_number

    return {
        int(number)
        for raw_cite in _table(report).source_cites
        if (number := citation_number(raw_cite))
    }


def test_SHADOW_부록에_이름_표의_모든_출처_행이_오른다(
    SHADOW_이름표_보고서,
) -> None:
    실린 = _cited_numbers(SHADOW_이름표_보고서)
    부록 = {source.number for source in SHADOW_이름표_보고서.citations}

    # 이름 셋이 조각 셋에서 왔으므로 번호도 셋이다(캡션 하나가 아니라).
    assert len(실린) == 3, sorted(실린)
    assert 실린 <= 부록, (sorted(실린), sorted(부록))


def test_SHADOW_부록의_각_출처_행이_3장을_사용_장으로_적는다(
    SHADOW_이름표_보고서,
) -> None:
    실린 = _cited_numbers(SHADOW_이름표_보고서)
    사용_장 = {
        source.number: list(source.used_in)
        for source in SHADOW_이름표_보고서.citations
        if source.number in 실린
    }

    assert set(사용_장) == 실린, 사용_장
    for number, sections in 사용_장.items():
        assert PORTFOLIO_TABLE_SECTION_ID in sections, (number, sections)


def test_SHADOW_결과가_출고_검증을_통과한다(SHADOW_이름표_보고서) -> None:
    """부록·본문 1:1 — 「부록에만 있는 번호」도 「본문에만 있는 번호」도 없다."""

    assert v2_validation_problems(SHADOW_이름표_보고서) == ()
