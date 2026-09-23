"""보도표의 «산문 중복 생존 확정 → 기사 예약» 순서를 못 박는다 (P17).

★ 왜 이 시험이 있나 (실측 — 저장 실측) — 진단은 「보도표 6행 생성」인데 PDF엔
  0행이었다. 행을 만들 때 기사를 예약(listed_articles)해 놓고, 산문 중복
  걸러내기는 렌더 시점(`nonredundant_news_rows`)에만 있었기 때문이다 —
  나중에 사라질 행이 다른 장의 유효한 행까지 막았다.
★ 여기서 지키는 것:
  ① 산문에 이미 실린 기사는 행도 예약도 만들지 않는다(사유 기록).
  ② 그래서 같은 기사가 «산문에 없는 다른 장»에는 실릴 수 있다.
  ③ 후보 행수와 최종 행수를 갈라 기록하고, 후보가 있으면 0행도 정직하게
     남긴다.
  ④ 같은 입력에 다시 걸어도 결과가 같다(멱등).
"""

from __future__ import annotations

from src.features.composer.constants import SECTION_IDS
from src.features.composer.news_block import (
    BLOCKED_REDUNDANT_WITH_PROSE,
    NEWS_BLOCK_STEP,
    augment_news_blocks,
    news_block_steps,
    nonredundant_news_rows,
)
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)

_보도_원문 = "가나다전자는 물류 자동화 제품군을 현장에 적용한다."


def _news(fragment_id: str) -> CollectedFragment:
    return CollectedFragment(
        fragment_id=fragment_id,
        kind="news",
        text=_보도_원문,
        source_url=f"https://media.example/news/{fragment_id}",
        document_title="가나다전자 물류 자동화",
        document_date="2026-09-01",
        formal_source_kind="news",
        source_publisher="가나다경제",
        counts_toward_document_floor=False,
    )


def _sentence(text: str) -> ComposedSentence:
    return ComposedSentence(text=text, citations=("9",), grade="확인")


def _report(**sentences_by_section: tuple[ComposedSentence, ...]) -> ComposedReport:
    return ComposedReport(
        sections=tuple(
            ComposedSection(
                section_id=section_id,
                sentences=sentences_by_section.get(section_id, ()),
            )
            for section_id in SECTION_IDS
        )
    )


def _owned(**by_section: tuple[str, ...]) -> dict[str, frozenset[str]]:
    return {
        section_id: frozenset(by_section.get(section_id, ()))
        for section_id in SECTION_IDS
    }


def _rows(result, section_id: str) -> list[tuple[str, ...]]:
    section = next(
        section for section in result.report.sections
        if section.section_id == section_id
    )
    return [tuple(row.cells) for row in section.news_rows]


def test_산문에_실린_기사는_예약하지_않아_다른_장에_실린다() -> None:
    # 2장 산문이 보도 문장을 그대로 담고 있다 → 2장 행은 만들지 않고,
    # 같은 기사가 4장(산문에 없음)에는 실린다.
    result = augment_news_blocks(
        _report(
            business_model=(
                _sentence(_보도_원문 + " 회사는 이를 주력 사업으로 소개했다."),
            ),
        ),
        (_news("40"),),
        allowed_fragment_ids_by_section=_owned(
            business_model=("40",), past_changes=("40",)
        ),
    )
    assert _rows(result, "business_model") == []
    assert len(_rows(result, "past_changes")) == 1
    assert dict(result.blocked_counts_by_reason)[BLOCKED_REDUNDANT_WITH_PROSE] == 1
    assert dict(result.candidate_row_counts_by_section) == {
        "business_model": 1, "past_changes": 1,
    }
    assert dict(result.row_counts_by_section) == {"past_changes": 1}


def test_모든_후보가_산문_중복이면_0행을_정직하게_남긴다() -> None:
    result = augment_news_blocks(
        _report(
            business_model=(
                _sentence(_보도_원문 + " 회사는 이를 주력 사업으로 소개했다."),
            ),
        ),
        (_news("40"),),
        allowed_fragment_ids_by_section=_owned(business_model=("40",)),
    )
    assert result.row_counts_by_section == ()
    assert dict(result.candidate_row_counts_by_section) == {"business_model": 1}
    assert dict(result.blocked_counts_by_reason)[BLOCKED_REDUNDANT_WITH_PROSE] == 1
    # 붙인 행이 없으므로 보고서 객체는 입력 그대로다.
    assert not result.added


def test_보도표_단계는_후보가_있으면_0행도_기록한다() -> None:
    class _Output:
        news_block_row_counts_by_section = ()
        news_block_blocked_counts_by_reason = (("redundant_with_prose", 1),)
        news_block_candidate_row_counts_by_section = (("business_model", 1),)

    steps = news_block_steps(_Output())
    table_step = next(step for step in steps if step["step"] == NEWS_BLOCK_STEP)
    assert table_step["행수"] == 0
    assert table_step["후보행수"] == 1
    assert table_step["장별후보행수"] == {"business_model": 1}


def test_후보_칸을_모르는_옛_결과는_종전처럼_기록하지_않는다() -> None:
    class _Output:
        news_block_row_counts_by_section = ()
        news_block_blocked_counts_by_reason = ()

    assert news_block_steps(_Output()) == []


def test_살아남은_행은_렌더_걸러내기와_어긋나지_않는다() -> None:
    # augment가 남긴 행을 렌더의 같은 함수에 다시 걸어도 그대로다(멱등).
    result = augment_news_blocks(
        _report(),
        (_news("40"),),
        allowed_fragment_ids_by_section=_owned(past_changes=("40",)),
    )
    section = next(
        section for section in result.report.sections
        if section.section_id == "past_changes"
    )
    assert nonredundant_news_rows(section) == section.news_rows
    again = augment_news_blocks(
        result.report,
        (_news("40"),),
        allowed_fragment_ids_by_section=_owned(past_changes=("40",)),
    )
    assert _rows(again, "past_changes") == _rows(result, "past_changes")
