"""본문 인용 번호 표기 방식 두 가지를 못 박는다.

★ 왜 이 시험이 있나 (사용자 신고) — 본문에 `[1][12][13]`이 문장마다 박혀 읽기가
  끊긴다. 실측: JYP 보고서 48문장 중 42개(87%)에 대괄호 번호가 있었다.
★ 그렇다고 다 지우면 «문장 단위 추적»을 잃는다. 이 엔진은 문장마다 원문 대조·
  의미 검수를 해서 확인/해석을 가르는데, 번호를 다 없애면 독자가 「이 수치가
  공시값인지 우리 해석인지」를 구분할 수 없다.
★ 그래서 절충안은 번호가 «정보를 주는 자리»에만 남긴다:
  ① 해석 문장은 번호를 빼고 « — 해석» 표지만 남긴다.
  ② 같은 출처를 잇달아 인용하는 확인 문장은 묶음의 마지막에만 번호를 단다.
★ 가장 중요한 것: **어느 방식이든 부록과의 1:1이 깨지면 안 된다.**
  출고 검증(validate_v2)이 「본문에 없는 부록 번호」·「부록에 없는 본문 번호」를
  둘 다 차단하므로, 번호를 줄이다가 부록 한 줄이 고아가 되면 보고서가 통째로
  막힌다.
"""

from __future__ import annotations

import re
from typing import Any

from src.features.composer.constants import (
    CITATION_STYLE_INLINE,
    CITATION_STYLE_MERGED,
    DEFAULT_CITATION_STYLE,
    GRADE_CONFIRMED,
    GRADE_INTERPRETED,
    SECTION_IDS,
)
from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)
from src.features.composer.render import INTERPRETATION_MARKER, render_report
from src.features.composer.validate import validate_v2

_MARKER_RE = re.compile(r"\[(\d+)\]")


def _fragments() -> dict[int, dict[str, Any]]:
    return {
        1: {"종류": "사업내용", "원문": "가나다전자는 검사 장비를 만든다."},
        2: {"종류": "재무", "원문": "2025년 매출은 1,200억원이다."},
        3: {
            "종류": "홈페이지",
            "원문": "고객 최우선을 내건다.",
            "출처": "https://example.test/about",
        },
    }


def _s(text: str, citations: tuple[str, ...], grade: str = GRADE_CONFIRMED):
    return ComposedSentence(text=text, citations=citations, grade=grade)


def _report(**by_section) -> ComposedReport:
    return ComposedReport(
        sections=tuple(
            ComposedSection(section_id=sid, sentences=by_section.get(sid, ()))
            for sid in SECTION_IDS
        ),
        summary=by_section.get(
            "summary",
            (
                _s("검사 장비 중심 구조다.", ("1",)),
                _s("매출은 성장 흐름이다.", ("2",)),
                _s("고객 가치를 앞세운다.", ("3",)),
            ),
        ),
    )


def _identity_lines(report) -> list[str]:
    for section in report.sections:
        if section.cell == "identity":
            return [text for text, _cite in section.prose_lines]
    raise AssertionError("identity 장이 없습니다")


def _render(composed, style):
    return render_report(
        "가나다전자(주)", composed, _fragments(), None, citation_style=style
    )


# ══════════════════════════════════════════════════════════
# 기본값은 바뀌지 않는다
# ══════════════════════════════════════════════════════════


def test_기본값은_절충안이다():
    """사용자가 두 방식을 나란히 보고 절충안으로 확정했다."""
    assert DEFAULT_CITATION_STYLE == CITATION_STYLE_MERGED


def test_기존_방식은_문장마다_번호를_붙인다():
    composed = _report(
        identity=(
            _s("검사 장비를 만든다.", ("1",)),
            _s("장비 매출이 크다.", ("1",)),
        )
    )

    lines = _identity_lines(_render(composed, CITATION_STYLE_INLINE))

    assert lines[0].endswith("[1]")
    assert lines[1].endswith("[1]")


# ══════════════════════════════════════════════════════════
# 절충안 규칙 ① 해석 문장은 번호를 뺀다
# ══════════════════════════════════════════════════════════


def test_해석_문장은_번호를_빼고_표지만_남긴다():
    composed = _report(
        identity=(
            _s("검사 장비를 만든다.", ("1",)),
            _s("장비 중심 구조로 읽힌다.", ("1",), GRADE_INTERPRETED),
        )
    )

    lines = _identity_lines(_render(composed, CITATION_STYLE_MERGED))

    해석줄 = lines[1]
    assert 해석줄.endswith(INTERPRETATION_MARKER)
    assert not _MARKER_RE.search(해석줄)


# ══════════════════════════════════════════════════════════
# 절충안 규칙 ② 같은 출처가 이어지면 마지막에만
# ══════════════════════════════════════════════════════════


def test_같은_출처가_이어지면_묶음_마지막에만_번호를_단다():
    composed = _report(
        identity=(
            _s("검사 장비를 만든다.", ("1",)),
            _s("장비는 반도체 공정에 쓰인다.", ("1",)),
            _s("생산 거점은 국내에 있다.", ("1",)),
        )
    )

    lines = _identity_lines(_render(composed, CITATION_STYLE_MERGED))

    assert not _MARKER_RE.search(lines[0])
    assert not _MARKER_RE.search(lines[1])
    assert lines[2].endswith("[1]")


def test_출처가_바뀌면_거기서_번호가_끊긴다():
    composed = _report(
        identity=(
            _s("검사 장비를 만든다.", ("1",)),
            _s("2025년 매출은 1,200억원이다.", ("2",)),
        )
    )

    lines = _identity_lines(_render(composed, CITATION_STYLE_MERGED))

    assert lines[0].endswith("[1]")
    assert lines[1].endswith("[2]")


def test_해석_문장은_묶음을_끊지_않는다():
    """중간에 해석이 끼어도 같은 출처 확인 문장 묶음은 이어진 것으로 본다."""
    composed = _report(
        identity=(
            _s("검사 장비를 만든다.", ("1",)),
            _s("그래서 장비 회사로 읽힌다.", ("1",), GRADE_INTERPRETED),
            _s("장비는 반도체 공정에 쓰인다.", ("1",)),
        )
    )

    lines = _identity_lines(_render(composed, CITATION_STYLE_MERGED))

    assert not _MARKER_RE.search(lines[0])
    assert not _MARKER_RE.search(lines[1])
    assert lines[2].endswith("[1]")


# ══════════════════════════════════════════════════════════
# ★ 가장 중요 — 두 방식 모두 부록 1:1을 지킨다
# ══════════════════════════════════════════════════════════


def _full_report(style):
    composed = _report(
        identity=(
            _s("검사 장비를 만든다.", ("1",)),
            _s("장비는 반도체 공정에 쓰인다.", ("1",)),
            _s("장비 중심으로 읽힌다.", ("1",), GRADE_INTERPRETED),
        ),
        business_model=(
            _s("2025년 매출은 1,200억원이다.", ("2",)),
            _s("성장 흐름으로 읽힌다.", ("2",), GRADE_INTERPRETED),
        ),
        culture=(_s("고객 최우선을 내건다.", ("3",)),),
    )
    return _render(composed, style)


def test_두_방식_모두_출고_검증을_통과한다():
    for style in (CITATION_STYLE_INLINE, CITATION_STYLE_MERGED):
        validate_v2(_full_report(style))  # 예외가 나면 실패다


def test_절충안에서도_모든_부록_번호가_본문에_한_번은_나온다():
    """번호를 줄이다 부록 한 줄이 고아가 되면 보고서가 통째로 막힌다."""
    report = _full_report(CITATION_STYLE_MERGED)

    본문번호: set[int] = set()
    for section in report.sections:
        for text, _cite in section.prose_lines:
            본문번호.update(int(v) for v in _MARKER_RE.findall(text))
    for item in report.summary_items:
        본문번호.update(int(v) for v in _MARKER_RE.findall(item.text))

    부록번호 = {source.number for source in report.citations}
    assert 부록번호 <= 본문번호, f"본문에 안 나오는 부록 번호: {부록번호 - 본문번호}"


def test_절충안이_실제로_번호_수를_줄인다():
    """줄지 않으면 이 방식을 만든 이유가 없다."""

    def 번호수(report) -> int:
        total = 0
        for section in report.sections:
            for text, _cite in section.prose_lines:
                total += len(_MARKER_RE.findall(text))
        return total

    assert 번호수(_full_report(CITATION_STYLE_MERGED)) < 번호수(
        _full_report(CITATION_STYLE_INLINE)
    )


def test_부록_사용_장_기록은_표기_방식과_무관하다():
    """번호를 숨겨도 그 근거를 «쓴 장»은 바뀌지 않는다."""
    inline = {s.number: sorted(s.used_in) for s in _full_report(CITATION_STYLE_INLINE).citations}
    merged = {s.number: sorted(s.used_in) for s in _full_report(CITATION_STYLE_MERGED).citations}

    assert inline == merged


# ══════════════════════════════════════════════════════════
# ★ 고아 방지 — 해석 문장에서만 인용된 조각
# ══════════════════════════════════════════════════════════


def test_해석_문장에서만_인용된_조각도_번호가_살아남는다():
    """★ 골든 fixture가 잡은 실측 결함.

    절충안 규칙 ①은 해석 문장의 번호를 뺀다. 그런데 어떤 조각이 «해석
    문장에서만» 인용되면 그 번호가 본문에 한 번도 안 나온다. 부록은 인용된
    조각으로 만들어지므로 그 줄이 고아가 되고, 출고 검증이 「부록에 있는
    번호를 본문 어디에서도 인용하지 않았습니다」로 보고서를 통째로 막는다.
    그래서 어디에도 안 보이는 번호는 마지막 인용 문장에서 되살린다.
    """
    composed = _report(
        identity=(_s("검사 장비를 만든다.", ("1",)),),
        # 조각 2는 «해석» 문장에서만 인용된다 — 규칙대로면 번호가 사라진다.
        business_model=(_s("성장 흐름으로 읽힌다.", ("2",), GRADE_INTERPRETED),),
        culture=(_s("고객 최우선을 내건다.", ("3",)),),
        summary=(
            _s("검사 장비 중심 구조다.", ("1",)),
            _s("고객 가치를 앞세운다.", ("3",)),
            _s("장비 회사로 읽힌다.", ("1",), GRADE_INTERPRETED),
        ),
    )

    report = _render(composed, CITATION_STYLE_MERGED)

    본문번호: set[int] = set()
    for section in report.sections:
        for text, _cite in section.prose_lines:
            본문번호.update(int(v) for v in _MARKER_RE.findall(text))
    for item in report.summary_items:
        본문번호.update(int(v) for v in _MARKER_RE.findall(item.text))

    assert 2 in 본문번호, "해석 문장에서만 인용된 조각의 번호가 사라졌습니다"
    validate_v2(report)  # 출고 검증도 통과해야 한다


def test_되살린_번호는_꼭_필요한_곳에만_붙는다():
    """고아 방지가 절충안을 무력화하면 안 된다 — 필요한 만큼만 되살린다."""
    composed = _report(
        identity=(
            _s("검사 장비를 만든다.", ("1",)),
            _s("장비는 반도체 공정에 쓰인다.", ("1",)),
            _s("생산 거점은 국내에 있다.", ("1",)),
        ),
        business_model=(_s("2025년 매출은 1,200억원이다.", ("2",)),),
        culture=(_s("고객 최우선을 내건다.", ("3",)),),
    )

    lines = _identity_lines(_render(composed, CITATION_STYLE_MERGED))

    # 조각 1은 확인 문장 묶음이라 마지막 하나에만 붙는다 — 되살리기가 개입할
    # 이유가 없다.
    assert not _MARKER_RE.search(lines[0])
    assert not _MARKER_RE.search(lines[1])
    assert lines[2].endswith("[1]")
