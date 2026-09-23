# -*- coding: utf-8 -*-
"""빈 장 안내문이 «실제 화면»과 어긋나지 않게 마지막에 대조한다.

★ 왜 이 시험이 있나 (실측 — 2026-09-22 산출 PDF 2건에 인쇄된 글자)
  · 주식회사 뤼튼테크놀로지스 3쪽 8장(인재상과 일하는 방식):
      「이 장에 담겼던 내용이 다른 장에서 더 자세히 다뤄져, 같은 설명을 두 번
       싣지 않으려고 그쪽으로 모았습니다. 자료가 없어서 비어 있는 것이
       아닙니다.」
    그런데 인재상·일하는 방식에 해당하는 내용이 «다른 어느 장에도 없다».
    중복 제거가 안내문을 붙인 시점에는 참이었지만, 그 뒤 단계가 소유 장의
    같은 문장을 지우면서 거짓이 됐다.
  · (주)메디라인액티브코리아 3쪽 4장(3개년 주요 변화):
      「확인된 자료가 부족해 이 장은 비어 있습니다.」 바로 아래에 2개년 실적
    표가 실렸다. 글과 화면이 어긋난다.

★ 여기서 지키는 것:
  ⓐ 문장이 실린 장에는 「비어 있습니다」가 남지 않는다.
  ⓑ 「그쪽으로 모았습니다」는 가리키는 장이 그 내용을 들고 있을 때만 남는다.
  ⓒ 표가 남는 빈 장은 「비어 있다」가 아니라 「표만 실었다」고 적는다.
  ⓓ 「생성이 끝나지 않았다」는 우리 쪽 실패 안내문은 건드리지 않는다.
  ⓔ 운영 진입점(run_v2)이 이 대조를 «요약·렌더 앞»에서 실제로 부른다.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from src.features.composer.constants import (
    NOTICE_AI_UNAVAILABLE,
    NOTICE_COMPOSE_FAILED,
    NOTICE_DUPLICATE_MOVED,
    NOTICE_DUPLICATE_MOVED_TABLE_KEPT,
    NOTICE_EVIDENCE_NONE,
    NOTICE_EVIDENCE_NOT_COMPOSED,
    NOTICE_INSUFFICIENT_EVIDENCE,
    NOTICE_INSUFFICIENT_EVIDENCE_TABLE_KEPT,
    NOTICE_NUMERIC_BODY_WITHHELD,
    SECTION_IDS,
)
from src.features.composer.dedupe import (
    drop_cross_section_duplicates,
    reconcile_section_notices,
)
from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FlowRow,
    NewsRow,
)

#: 8장(인재상과 일하는 방식) · 1장(기업 정체성) — 실제 보고서의 장 id.
_CULTURE = "culture"
_IDENTITY = "identity"
#: 4장(3개년 주요 변화) — 메디라인 실적 표가 남은 장.
_PAST_CHANGES = "past_changes"

#: 실측 — 뤼튼 보고서 2쪽 3.3에 인쇄된 문장.
_크랙_문장 = (
    "회사는 홈페이지에서 AI 엔터테인먼트 콘텐츠 플랫폼 '크랙(Crack)'을 "
    "운영하고 있으며, 청소년 보호 정책 강화를 추진하고 있다."
)
#: 같은 사실을 어투만 바꿔 다른 장에 옮겨 적은 모양 (중복 제거가 잡는 짝).
_크랙_문장_변형 = (
    "회사는 홈페이지에서 AI 엔터테인먼트 콘텐츠 플랫폼 '크랙(Crack)'을 "
    "운영한다고 밝히며, 청소년 보호 정책의 강화를 추진하고 있다."
)
#: 실측 — 뤼튼 보고서 3쪽 5.2에 인쇄된 문장.
_상장_문장 = (
    "회사는 상장 준비에 본격 착수했다고 밝혔으며, 국내 주요 증권사에 "
    "기업공개(IPO) 주관사 선정을 추진하고 있다."
)


def _sentence(text: str, citations: tuple[str, ...] = ("26",)) -> ComposedSentence:
    return ComposedSentence(text=text, citations=citations, grade="확인")


def _report(sections: dict[str, ComposedSection]) -> ComposedReport:
    """장 id → 장. 안 준 장은 빈 장으로 채운다(장 삭제 금지)."""

    return ComposedReport(
        sections=tuple(
            sections.get(section_id, ComposedSection(section_id, ()))
            for section_id in SECTION_IDS
        )
    )


def _section(report: ComposedReport, section_id: str) -> ComposedSection:
    for section in report.sections:
        if section.section_id == section_id:
            return section
    raise AssertionError(f"{section_id} 장이 없습니다")


def _emptied_by_dedupe() -> ComposedReport:
    """실제 중복 제거로 8장을 비운다 — 안내문과 «간 곳»을 생산자가 만든다.

    1장이 같은 사실을 두 문장으로 다뤄 소유 장이 되고, 8장의 한 문장이 빠져
    8장이 통째로 빈다(뤼튼 8장이 비게 된 바로 그 모양).
    """

    report = _report({
        _IDENTITY: ComposedSection(
            _IDENTITY,
            (_sentence(_크랙_문장), _sentence(_상장_문장)),
        ),
        _CULTURE: ComposedSection(_CULTURE, (_sentence(_크랙_문장_변형),)),
    })
    deduped, dropped = drop_cross_section_duplicates(report)
    assert dropped == 1, "이 픽스처가 중복을 못 잡으면 아무것도 증명하지 못합니다"
    return deduped


# ══════════════════════════════════════════════════════════
# ⓑ 「그쪽으로 모았습니다」 — 그쪽이 정말 들고 있나
# ══════════════════════════════════════════════════════════


def test_중복_제거가_간_곳을_함께_적는다():
    """★ 생산자 단정 — 이 값이 없으면 아래 대조가 통째로 공전한다."""
    비워진_장 = _section(_emptied_by_dedupe(), _CULTURE)

    assert 비워진_장.sentences == ()
    assert 비워진_장.notice == NOTICE_DUPLICATE_MOVED
    assert 비워진_장.moved_to_sections == (_IDENTITY,)


def test_소유_장이_비면_이동_안내문이_자료부족으로_바뀐다():
    """뤼튼 8장 재현 — 「다른 장에 있다」는데 그 장도 비었다면 거짓말이다."""
    deduped = _emptied_by_dedupe()
    # 뒤 단계(본문 검수·수치 안전)가 소유 장의 문장을 걷어낸 모양.
    사라진_소유장 = ComposedReport(
        sections=tuple(
            replace(section, sentences=())
            if section.section_id == _IDENTITY
            else section
            for section in deduped.sections
        )
    )

    대조본 = reconcile_section_notices(사라진_소유장, frozenset())

    assert _section(대조본, _CULTURE).notice == NOTICE_INSUFFICIENT_EVIDENCE


def test_소유_장에_문장이_남아_있으면_이동_안내문을_그대로_둔다():
    """음성 대조 — 참인 안내문까지 바꾸면 「어디로 갔는지」를 잃는다."""
    deduped = _emptied_by_dedupe()

    대조본 = reconcile_section_notices(deduped, frozenset())

    assert _section(대조본, _IDENTITY).sentences, "소유 장이 비면 이 시험은 무의미하다"
    assert _section(대조본, _CULTURE).notice == NOTICE_DUPLICATE_MOVED
    assert 대조본 is deduped, "바꿀 것이 없으면 같은 보고서를 그대로 돌려준다"


def test_소유_장이_비어도_표가_남으면_표_사실까지_적는다():
    """ⓑ와 ⓒ가 함께 걸리는 자리 — 자료 부족으로 내리되 표는 말한다."""
    deduped = _emptied_by_dedupe()
    사라진_소유장 = ComposedReport(
        sections=tuple(
            replace(section, sentences=())
            if section.section_id == _IDENTITY
            else section
            for section in deduped.sections
        )
    )

    대조본 = reconcile_section_notices(사라진_소유장, frozenset({_CULTURE}))

    assert (
        _section(대조본, _CULTURE).notice == NOTICE_INSUFFICIENT_EVIDENCE_TABLE_KEPT
    )


def test_이동_안내문도_표가_남으면_표_사실을_붙인다():
    """표가 뒤 단계에서 붙은 장 — 「모았습니다」에도 표 말이 붙어야 한다."""
    deduped = _emptied_by_dedupe()

    대조본 = reconcile_section_notices(deduped, frozenset({_CULTURE}))

    assert (
        _section(대조본, _CULTURE).notice == NOTICE_DUPLICATE_MOVED_TABLE_KEPT
    )


# ══════════════════════════════════════════════════════════
# ⓒ 표가 남는 빈 장 — 메디라인 4장
# ══════════════════════════════════════════════════════════


def test_메디라인_4장_표가_남으면_표만_실었다고_적는다():
    report = _report({
        _PAST_CHANGES: ComposedSection(
            _PAST_CHANGES, (), notice=NOTICE_INSUFFICIENT_EVIDENCE
        ),
    })

    대조본 = reconcile_section_notices(report, frozenset({_PAST_CHANGES}))

    assert (
        _section(대조본, _PAST_CHANGES).notice
        == NOTICE_INSUFFICIENT_EVIDENCE_TABLE_KEPT
    )


def test_메디라인_4장을_실제_실적표로_재현한다():
    """표 자리를 손으로 짓지 않고 «운영이 쓰는 함수»로 만든다.

    손으로 지은 frozenset은 「4장에 실적표가 실린다」는 진짜 규칙을 재현하지
    못해, 그 매핑이 바뀌어도 시험이 초록이다.
    """
    from src.features.composer.dedupe import sections_with_program_tables
    from src.features.composer.port import PerformanceTable

    실적표 = PerformanceTable(
        caption="전자공시 최근 두 사업연도 별도 주요 실적",
        headers=("구분", "2024", "2025"),
        rows=(("매출액", "397.4", "424.8"),),
    )
    report = _report({
        _PAST_CHANGES: ComposedSection(
            _PAST_CHANGES, (), notice=NOTICE_INSUFFICIENT_EVIDENCE
        ),
    })

    대조본 = reconcile_section_notices(
        report, sections_with_program_tables(실적표)
    )

    assert (
        _section(대조본, _PAST_CHANGES).notice
        == NOTICE_INSUFFICIENT_EVIDENCE_TABLE_KEPT
    )


def test_표가_없으면_자료부족_안내문을_그대로_둔다():
    """음성 대조 — 표가 없는데 「아래 표」라고 적으면 반대로 어긋난다."""
    report = _report({
        _PAST_CHANGES: ComposedSection(
            _PAST_CHANGES, (), notice=NOTICE_INSUFFICIENT_EVIDENCE
        ),
    })

    대조본 = reconcile_section_notices(report, frozenset())

    assert _section(대조본, _PAST_CHANGES).notice == NOTICE_INSUFFICIENT_EVIDENCE
    assert 대조본 is report


@pytest.mark.parametrize(
    "장_바꾸기",
    [
        pytest.param(
            lambda section: replace(
                section, flow_rows=(FlowRow(("원재료", "제조", "고객"), ("30",)),)
            ),
            id="장이 들고 있는 경로표",
        ),
        pytest.param(
            lambda section: replace(
                section,
                news_rows=(
                    NewsRow(("2025-09-04", "매체", "수상 사실"), ("57",)),
                ),
            ),
            id="장이 들고 있는 보도표",
        ),
    ],
)
def test_장이_들고_있는_표도_표로_센다(장_바꾸기):
    """밖에서 들어오는 표만 보면 7장 도식·보도표가 있는 장을 놓친다."""
    report = _report({
        _CULTURE: 장_바꾸기(
            ComposedSection(_CULTURE, (), notice=NOTICE_INSUFFICIENT_EVIDENCE)
        ),
    })

    대조본 = reconcile_section_notices(report, frozenset())

    assert (
        _section(대조본, _CULTURE).notice
        == NOTICE_INSUFFICIENT_EVIDENCE_TABLE_KEPT
    )


def test_표가_사라지면_표_말이_붙은_안내문도_되돌린다():
    """멱등성의 반대 방향 — 대조 결과가 다시 입력이 되어도 거짓이 안 남는다."""
    report = _report({
        _PAST_CHANGES: ComposedSection(
            _PAST_CHANGES, (), notice=NOTICE_INSUFFICIENT_EVIDENCE_TABLE_KEPT
        ),
    })

    대조본 = reconcile_section_notices(report, frozenset())

    assert _section(대조본, _PAST_CHANGES).notice == NOTICE_INSUFFICIENT_EVIDENCE


# ══════════════════════════════════════════════════════════
# ⓐ 문장이 있는 장 / ⓓ 우리 쪽 실패
# ══════════════════════════════════════════════════════════


def test_문장이_있는_장의_남은_안내문은_지운다():
    """뒤 단계가 다시 채운 장 — 「비어 있습니다」가 바로 위에 남으면 거짓이다."""
    report = _report({
        _CULTURE: ComposedSection(
            _CULTURE,
            (_sentence(_상장_문장),),
            notice=NOTICE_INSUFFICIENT_EVIDENCE,
        ),
    })

    대조본 = reconcile_section_notices(report, frozenset())

    assert _section(대조본, _CULTURE).notice == ""
    assert _section(대조본, _CULTURE).sentences, "문장까지 지우면 안 된다"


@pytest.mark.parametrize(
    "안내문", [NOTICE_COMPOSE_FAILED, NOTICE_AI_UNAVAILABLE]
)
def test_우리쪽_실패_안내문은_바꾸지_않는다(안내문):
    """「다시 실행하면 채워질 수 있습니다」는 자료가 아니라 실행 상태를 말한다."""
    report = _report({
        _CULTURE: ComposedSection(_CULTURE, (), notice=안내문),
    })

    대조본 = reconcile_section_notices(report, frozenset({_CULTURE}))

    assert _section(대조본, _CULTURE).notice == 안내문


def test_안내문이_없는_빈_장에는_새로_만들지_않는다():
    """이 대조는 «적힌 말»만 고친다 — 없는 안내문을 지어내지 않는다."""
    report = _report({})

    대조본 = reconcile_section_notices(report, frozenset({_CULTURE}))

    assert _section(대조본, _CULTURE).notice == ""
    assert 대조본 is report


def test_두_번_불러도_같은_결과다():
    """보충 경로가 같은 대조를 다시 건다 — 멱등이 아니면 글자가 표류한다."""
    deduped = _emptied_by_dedupe()
    사라진_소유장 = ComposedReport(
        sections=tuple(
            replace(section, sentences=())
            if section.section_id == _IDENTITY
            else section
            for section in deduped.sections
        )
    )
    tables = frozenset({_PAST_CHANGES})

    한_번 = reconcile_section_notices(사라진_소유장, tables)
    두_번 = reconcile_section_notices(한_번, tables)

    assert [section.notice for section in 두_번.sections] == [
        section.notice for section in 한_번.sections
    ]


def test_대상_장만_고치도록_제한할_수_있다():
    """보충 회차는 승인 장만 고친다 — 비대상 장이 바뀌면 보고서가 통째로 막힌다."""
    deduped = _emptied_by_dedupe()
    사라진_소유장 = ComposedReport(
        sections=tuple(
            replace(section, sentences=())
            if section.section_id == _IDENTITY
            else section
            for section in deduped.sections
        )
    )

    대조본 = reconcile_section_notices(
        사라진_소유장, frozenset(), section_ids=frozenset({_PAST_CHANGES})
    )

    assert _section(대조본, _CULTURE).notice == NOTICE_DUPLICATE_MOVED
    assert 대조본 is 사라진_소유장


def test_문장과_표와_도식은_건드리지_않는다():
    """이 함수가 바꾸는 것은 `notice` 글자뿐이다."""
    원본 = _report({
        _CULTURE: ComposedSection(
            _CULTURE,
            (),
            notice=NOTICE_INSUFFICIENT_EVIDENCE,
            flow_rows=(FlowRow(("원재료", "제조", "고객"), ("30",)),),
            news_rows=(NewsRow(("2025-09-04", "매체", "수상 사실"), ("57",)),),
            news_decisions=(("57", "사유", "설명"),),
        ),
    })

    대조본 = reconcile_section_notices(원본, frozenset())

    바뀐_장 = _section(대조본, _CULTURE)
    원래_장 = _section(원본, _CULTURE)
    assert 바뀐_장.notice != 원래_장.notice
    assert (바뀐_장.sentences, 바뀐_장.flow_rows, 바뀐_장.news_rows) == (
        원래_장.sentences, 원래_장.flow_rows, 원래_장.news_rows
    )
    assert 바뀐_장.news_decisions == 원래_장.news_decisions


# ══════════════════════════════════════════════════════════
# 사유 상수들이 «같은 문장»이라는 전제
# ══════════════════════════════════════════════════════════


def test_자료부족_사유_상수들은_모두_같은_문장이다():
    """★ 대조가 «글자»로 판정하는 근거.

    사유별 상수는 진단·시험이 경로를 가르려고 따로 두지만, 독자에게 보이는
    문장은 하나뿐이다(2026-09-16 결정). 하나라도 글자가 갈라지면 그 경로만
    대조에서 빠져 조용히 옛 안내문이 나간다 — 그때 이 시험이 알려 준다.
    """
    from src.features.composer.verify import NOTICE_ALL_SENTENCES_REJECTED

    assert {
        NOTICE_NUMERIC_BODY_WITHHELD,
        NOTICE_EVIDENCE_NONE,
        NOTICE_EVIDENCE_NOT_COMPOSED,
        NOTICE_ALL_SENTENCES_REJECTED,
    } == {NOTICE_INSUFFICIENT_EVIDENCE}


@pytest.mark.parametrize(
    "안내문",
    [
        NOTICE_INSUFFICIENT_EVIDENCE,
        NOTICE_NUMERIC_BODY_WITHHELD,
        NOTICE_EVIDENCE_NONE,
        NOTICE_EVIDENCE_NOT_COMPOSED,
    ],
)
def test_어느_사유로_비었든_표가_남으면_같은_말로_고친다(안내문):
    report = _report({
        _PAST_CHANGES: ComposedSection(_PAST_CHANGES, (), notice=안내문),
    })

    대조본 = reconcile_section_notices(report, frozenset({_PAST_CHANGES}))

    assert (
        _section(대조본, _PAST_CHANGES).notice
        == NOTICE_INSUFFICIENT_EVIDENCE_TABLE_KEPT
    )


# ══════════════════════════════════════════════════════════
# ⓔ 배선 — 운영 경로가 요약·렌더 «앞»에서 실제로 부른다
# ══════════════════════════════════════════════════════════


def test_운영_경로가_요약_앞에서_안내문을_대조한다(monkeypatch):
    """★ 배선 단정 — 부르지 않으면 이 규칙은 운영에서 통째로 꺼진다.

    시험 안에서 따로 불러 검사하면 배선이 빠져도 초록불이 된다. 운영
    진입점(run_v2)을 실제로 돌리고 «부른 순서»와 «받은 인자»를 단정한다.
    """
    from src.features.composer import dedupe as dedupe_module
    from src.features.composer import pipeline
    from src.features.composer.tests.test_pipeline import (
        _FakeReviewer,
        _FakeWriter,
        _raw_fragments,
    )

    순서: list[str] = []
    받은_보고서: list[ComposedReport] = []

    def reconcile_spy(report, sections_with_tables, **kwargs):
        순서.append("대조")
        받은_보고서.append(report)
        return dedupe_module.reconcile_section_notices(
            report, sections_with_tables, **kwargs
        )

    original_render = pipeline.render_report

    def render_spy(*args, **kwargs):
        순서.append("렌더")
        return original_render(*args, **kwargs)

    monkeypatch.setattr(pipeline, "reconcile_section_notices", reconcile_spy)
    monkeypatch.setattr(pipeline, "render_report", render_spy)

    pipeline.run_v2(
        "가나다전자", _raw_fragments(), None,
        writer_ask=_FakeWriter(), reviewer_ask=_FakeReviewer(),
        corp_type="상장사", as_of_date="2026-08-24",
    )

    assert "대조" in 순서, "운영 경로가 안내문 대조를 아예 부르지 않았습니다"
    assert "렌더" in 순서, (
        "렌더를 안 불렀다면 이 시험의 순서 단정이 아무것도 증명하지 못합니다"
    )
    assert 순서.index("대조") < 순서.index("렌더"), (
        "안내문 대조가 렌더보다 뒤에 있으면 봉인된 글자와 화면이 갈라집니다"
    )
    assert 받은_보고서[0].sections, "빈 보고서를 넘기면 대조가 공전합니다"


def test_안내문_한_글자가_장_봉인_블록_지문을_바꾼다():
    """★ 위 «승인 장만 고친다» 결정의 근거를 값으로 확인한다.

    보충 결속 검사는 비대상 장의 봉인 블록 지문이 1회차와 다르면
    「승인하지 않은 장이 보충 중 바뀌었습니다」로 보고서 전체를 막는다
    (shared/report_recovery.py). 안내문이 그 지문에 들어가는지가 이 결정의
    전제이므로 «가정»으로 두지 않고 직접 잰다.
    """
    from src.features.composer.pipeline import _section_block_sha256s
    from src.features.composer.render import render_report

    def _지문(안내문: str) -> dict[str, str]:
        report = _report({
            _PAST_CHANGES: ComposedSection(_PAST_CHANGES, (), notice=안내문),
            _IDENTITY: ComposedSection(_IDENTITY, (_sentence(_상장_문장),)),
        })
        return dict(_section_block_sha256s(render_report("가나다전자", report, (), None)))

    이전 = _지문(NOTICE_INSUFFICIENT_EVIDENCE)
    이후 = _지문(NOTICE_INSUFFICIENT_EVIDENCE_TABLE_KEPT)

    assert 이전[_PAST_CHANGES] != 이후[_PAST_CHANGES]
    assert 이전[_IDENTITY] == 이후[_IDENTITY], "다른 장까지 바뀌면 이 시험이 무의미하다"


def test_보충_경로는_승인_장만_대조한다(monkeypatch):
    """★ 두 번째 호출부가 죽어 있지 않은지 «실제 보충 실행»으로 확인한다.

    이 경로에는 「승인하지 않은 장이 보충 중 바뀌면 보고서 전체를 막는」 계약이
    걸려 있다(shared/report_recovery.py). 그래서 대조 대상을 승인 장으로
    좁혀 넘기는데, 그 인자가 빠지면 보충이 있는 실행이 통째로 실패한다.
    """
    from src.features.composer import dedupe as dedupe_module
    from src.features.composer import pipeline
    from src.features.composer.tests.test_section_public_manifest import (
        _run_recovering_full,
    )

    받은_인자: list[dict] = []

    def reconcile_spy(report, sections_with_tables, **kwargs):
        받은_인자.append(kwargs)
        return dedupe_module.reconcile_section_notices(
            report, sections_with_tables, **kwargs
        )

    monkeypatch.setattr(pipeline, "reconcile_section_notices", reconcile_spy)

    _run_recovering_full((_IDENTITY,))

    # 본문 검수와 공개 전 도식 필터 뒤 대조를 거친다. 보충도 같은 두 경계를 거치되 승인 장만 대조한다.
    assert [value.get("section_ids") for value in 받은_인자] == [
        None, None, frozenset({_IDENTITY}), frozenset({_IDENTITY}),
    ]
    assert all(isinstance(value["moved_facts"], list) and value["fragments"] for value in 받은_인자)


def test_대조한_안내문이_공개_투영까지_그대로_간다():
    """대조한 안내문은 번호 없는 안내로 봉인되며 사실 문단에 섞이지 않는다."""
    from src.features.composer.render import render_report
    from src.features.report_standard.public_projection import (
        build_public_projection,
    )

    report = _report({
        _PAST_CHANGES: ComposedSection(
            _PAST_CHANGES, (), notice=NOTICE_INSUFFICIENT_EVIDENCE
        ),
        _IDENTITY: ComposedSection(_IDENTITY, (_sentence(_상장_문장),)),
    })

    대조본 = reconcile_section_notices(report, frozenset({_PAST_CHANGES}))
    rendered = render_report("가나다전자", 대조본, (), None)
    projection = build_public_projection(rendered)

    화면 = next(
        block.display
        for block in projection.sections
        if block.display.cell == _PAST_CHANGES
    )
    assert 화면.guidance_lines == (NOTICE_INSUFFICIENT_EVIDENCE_TABLE_KEPT,)
    assert 화면.paragraphs == ()
    assert 화면.sentences == ()


def test_모든_호출부가_같은_표_자리를_넘긴다():
    """보충 경로처럼 위 시험이 못 도는 호출부까지 «구문»으로 전수 확인한다.

    표 자리를 한쪽만 넘기면 그 경로에서만 「비어 있다」가 그대로 나간다.
    """
    import ast
    import inspect

    from src.features.composer import pipeline

    tree = ast.parse(inspect.getsource(pipeline))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "reconcile_section_notices"
    ]
    assert len(calls) >= 2, "호출부가 줄었습니다 — 배선 단정을 다시 맞추세요"
    for call in calls:
        위치인자 = [ast.unparse(argument) for argument in call.args]
        assert 위치인자[1:] == [
            "sections_with_program_tables(performance_table, composition_tables)"
        ], f"{call.lineno}행이 다른 표 자리를 넘깁니다"
