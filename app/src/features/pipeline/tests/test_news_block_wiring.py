"""뉴스 조각이 «실제 수집·transport»를 지나 보도표까지 닿는지 본다.

★ 왜 손으로 만든 조각으로 끝내지 않나 — composer 시험은 조각을 손으로 지어
  넣는다. 그러면 상류가 발행처·발행일을 안 실어 주게 바뀌어도 아무 시험도
  안 깨지고, 운영에서만 「매체 칸이 비어서 표가 통째로 안 붙는다」가 된다.
  그래서 여기서는 가짜 검색·분류·본문만 주입하고 나머지는 진짜 경로를 태운다.

★ 그리고 composer가 결과 객체에 값을 실어도 그것을 실행 기록(steps)으로
  옮기는 한 줄이 없으면 운영 진단에는 영영 안 남는다. 그 한 줄도 함께 잰다.

★ AI·네트워크 0회.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core import news_intake_switch
from src.features.composer.constants import SECTION_IDS
from src.features.composer.news_block import (
    NEWS_BLOCK_STEP,
    augment_news_blocks,
    news_block_steps,
)
from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    filing_meta_from_raw,
)
from src.features.pipeline import real
from src.features.pipeline.tests.test_news_intake_wiring import (
    _grounded_runtime_item,
    _grounded_runtime_analysis,
    GROUNDED_RUNTIME_BODY,
    _official_result,
    _result,
)
from src.features.pipeline.tests.test_official_evidence_runtime import (
    _Collector,
    _freeze_runtime,
    _request,
    _wire_runtime,
    FakeEngine,
)
from src.shared.report_evidence.constants import (
    GenerationGateStatus,
    ReleaseMode,
    ReportExecutionOutcome,
    SOURCE_KIND_NEWS,
)


#: 법인·사업 사실·숫자의 정확 원문 검증을 통과하는 현재 수집 fixture.
_ARTICLE_BODY = GROUNDED_RUNTIME_BODY
#: 각 근거 조각은 한 장에서 소유한다.
_SECTIONS = ("portfolio",)


@pytest.fixture(autouse=True)
def _reset_news_switch(monkeypatch: pytest.MonkeyPatch):
    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001
    monkeypatch.delenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, raising=False)
    yield
    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001


def _partial_path_calls(monkeypatch: pytest.MonkeyPatch):
    """부분 보고서 갈래를 진짜 수집·분류·transport로 한 번 돌린다.

    돌려주는 것은 ``(_wire_runtime`` 기록, 실행 결과) 쌍이다 — 이 하네스를
    쓰는 다른 시험이 「작성기 연결부가 실제로 무엇을 받았나」까지 볼 수 있게
    조각이 아니라 «호출 기록»을 그대로 넘긴다.
    """

    monkeypatch.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, "1")
    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    engine = FakeEngine()
    collector = _Collector([_official_result()])
    calls = _wire_runtime(monkeypatch, engine=engine)
    original_assess = real.assess_official_evidence

    def assess_with_portfolio_gap(result):
        # ★ 세 장을 «모두» 미달로 둔다 — 뉴스 경로는 미달인 장만 대상으로
        #   삼으므로, 한 장만 비워 두면 분류기가 세 장을 골라도 두 장은
        #   `ready_section`으로 버려진다(실측: portfolio만 남았다).
        base = original_assess(result)
        decision = replace(
            base.decision,
            status=GenerationGateStatus.STOP_INSUFFICIENT_EVIDENCE,
            outcome=ReportExecutionOutcome.INSUFFICIENT_EVIDENCE,
            ready_section_ids=tuple(
                section_id
                for section_id in base.decision.ready_section_ids
                if section_id not in _SECTIONS
            ),
            insufficient_section_ids=_SECTIONS,
            reason_codes=("fixture_section_gap",),
        )
        return replace(
            base,
            decision=decision,
            dart_partial_fallback=True,
            dart_partial_reason="insufficient_with_ready_sections",
        )

    monkeypatch.setattr(real, "assess_official_evidence", assess_with_portfolio_gap)

    search_calls = 0

    def search_news(_query: str, **kwargs: object) -> SimpleNamespace:
        nonlocal search_calls
        search_calls += 1
        return _result(items=[_grounded_runtime_item()] if search_calls == 1 else [])

    user_input, card = _request()
    result = real.RealPipeline(
        official_evidence_collector=collector,
        news_search=search_news,
        news_analyze=_grounded_runtime_analysis,
        news_fetch_text=lambda _url: _ARTICLE_BODY,
    ).run(user_input, card)
    assert result.outcome is real.Outcome.REPORT, result.message
    return calls, result


def _typed_news_fragments(monkeypatch: pytest.MonkeyPatch):
    """진짜 수집·transport를 태워 typed 뉴스 조각과 장별 소유권 표를 얻는다."""

    calls, _result = _partial_path_calls(monkeypatch)
    composer = calls.composers[0]
    packets = real._full_section_evidence_packets(  # noqa: SLF001
        corp_id=composer["corp_id"],
        source_identity_digest=composer["source_identity_digest"],
        frags=composer["frags"],
        filing_meta=filing_meta_from_raw(composer["filing"]),
    )
    return packets


def _allowed_by_section(packets) -> dict[str, frozenset[str]]:
    return {
        packet.section_id: frozenset(
            fragment.fragment_id for fragment in packet.fragments
        )
        for packet in packets.packets
    }


def _empty_report() -> ComposedReport:
    return ComposedReport(
        sections=tuple(
            ComposedSection(section_id=section_id, sentences=())
            for section_id in SECTION_IDS
        )
    )


def test_실제_수집_조각에_매체와_발행일이_실려_온다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """표의 두 칸(발행일·매체)이 상류에서 실제로 오는지부터 못 박는다."""

    packets = _typed_news_fragments(monkeypatch)
    news = [
        fragment
        for packet in packets.packets
        for fragment in packet.fragments
        if fragment.formal_source_kind == SOURCE_KIND_NEWS
    ]

    assert news, "뉴스 조각이 하나도 transport를 통과하지 못했습니다"
    for fragment in news:
        assert fragment.source_publisher == "newsis.com"
        assert fragment.news_grounded is True
        assert fragment.news_claim_kind == "reported_fact"
        assert fragment.document_date == "2026-09-01"
        assert fragment.counts_toward_document_floor is False
        assert fragment.location.startswith("기사 본문 · news-fragment-")


def test_본문검증_기사의_단일_소유_장에_기사한개로_표시한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packets = _typed_news_fragments(monkeypatch)
    allowed = _allowed_by_section(packets)
    fragments = tuple(
        {
            fragment.fragment_id: fragment
            for packet in packets.packets
            for fragment in packet.fragments
        }.values()
    )

    result = augment_news_blocks(
        _empty_report(),
        fragments,
        allowed_fragment_ids_by_section=allowed,
    )

    by_section = dict(result.row_counts_by_section)
    assert set(by_section) == set(_SECTIONS), by_section
    assert set(by_section.values()) == {1}, by_section

    section = next(
        section
        for section in result.report.sections
        if section.section_id == "portfolio"
    )
    글자 = [row.cells[2] for row in section.news_rows]
    본문_문장 = _ARTICLE_BODY.splitlines()
    assert 글자 == 본문_문장[: len(글자)], 글자
    assert all(row.cells[0] == "2026-09-01" for row in section.news_rows)
    assert all(row.cells[1] == "newsis.com · 가나다전자 새 제품군 공급" for row in section.news_rows)


def test_뉴스가_안_붙는_장에는_표가_없다(monkeypatch: pytest.MonkeyPatch) -> None:
    packets = _typed_news_fragments(monkeypatch)
    fragments = tuple(
        {
            fragment.fragment_id: fragment
            for packet in packets.packets
            for fragment in packet.fragments
        }.values()
    )

    result = augment_news_blocks(
        _empty_report(),
        fragments,
        allowed_fragment_ids_by_section=_allowed_by_section(packets),
    )

    없는_장 = {
        section.section_id
        for section in result.report.sections
        if not section.news_rows
    }
    assert "competitive_position" in 없는_장
    assert set(_SECTIONS) & 없는_장 == set()


def test_조각이_0건이면_아무_표도_안_생긴다() -> None:
    """뉴스를 안 켠 실행에서 빈 표가 생기지 않는지 본다."""

    result = augment_news_blocks(
        _empty_report(),
        (),
        allowed_fragment_ids_by_section={
            section_id: frozenset() for section_id in SECTION_IDS
        },
    )

    assert not result.added
    assert result.blocked_counts_by_reason == ()
    assert all(not section.news_rows for section in result.report.sections)


# ══════════════════════════════════════════════════════════
# 실행 기록 — composer 값이 steps까지 닿는가
# ══════════════════════════════════════════════════════════


class _Output:
    def __init__(self, rows) -> None:
        self.news_block_row_counts_by_section = rows
        self.news_block_blocked_counts_by_reason = ()


def test_실행기록_헬퍼가_보도표_단계를_함께_남긴다() -> None:
    steps = real._unused_name_steps(_Output((("identity", 2),)))  # noqa: SLF001

    assert [step["step"] for step in steps] == [NEWS_BLOCK_STEP]
    assert steps[0]["장별행수"] == {"identity": 2}


def test_실행기록_헬퍼가_composer_정본을_부른다() -> None:
    """단계 이름·필드를 real.py가 손으로 다시 적으면 조용히 어긋난다.

    ★ 소스를 «다시 읽어» 확인한다 — import한 모듈 객체는 옛 바이트코드를
      쓸 수 있어 같은 길이의 수정을 못 본다.
    """

    tree = ast.parse(
        Path(real.__file__).read_text(encoding="utf-8"), filename=real.__file__
    )
    helper = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_unused_name_steps"
    )
    called = {
        node.func.id
        for node in ast.walk(helper)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "news_block_steps" in called


def test_단계_이름은_composer_정본을_그대로_쓴다() -> None:
    assert NEWS_BLOCK_STEP == "뉴스_보도표"
    assert news_block_steps(_Output((("identity", 1),)))[0]["step"] == NEWS_BLOCK_STEP
