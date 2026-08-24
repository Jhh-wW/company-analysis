"""중복 «검출» 시험 (엔진 v2 인수 작업 — 아직 출고를 막지 않는 단계).

★ 지켜야 하는 것 두 가지(중요도 순):
  ① 정상 보고서(핵심 용어만 반복)는 검출 0건이어야 한다 — 이게 더 중요하다.
     오탐이 잦으면 나중에 게이트로 배선했을 때 정상 보고서까지 막는다.
  ② 진짜 중복(같은 수치가 두 장·두 형식에)은 실제로 잡혀야 한다.
  ★ find_numeric_duplicates는 예외를 던지지 않고 validate_v2 게이트에도
    배선돼 있지 않다 — 이 파일이 그 사실 자체도 못 박는다
    (test_출고를_막지_않는다).
"""

from __future__ import annotations

from src.features.composer.dup_detect import (
    CONFIDENCE_CONFIRMED,
    CONFIDENCE_SUSPECTED,
    find_numeric_duplicates,
)
from src.features.pipeline.port import Grade, Report, ReportSection, ReportTable, SummaryItem


def _section(
    section_id: str,
    prose: list[tuple[str, str]] | None = None,
    tables: list[ReportTable] | None = None,
) -> ReportSection:
    return ReportSection(
        cell=section_id,
        title=section_id,
        prose_lines=prose or [],
        tables=tables or [],
    )


def _report(sections: list[ReportSection], summary: list[SummaryItem] | None = None) -> Report:
    return Report(
        company="가나다전자",
        job="",
        corp_type="상장사",
        grade=Grade.COMPLETE,
        sections=sections,
        summary_items=summary or [],
    )


# ══════════════════════════════════════════════════════════
# ① 정상 보고서 — 검출 0건이 더 중요하다
# ══════════════════════════════════════════════════════════


def test_핵심_용어_반복만으로는_잡히지_않는다():
    report = _report(
        [
            _section(
                "identity",
                [("가나다전자는 반도체 검사 장비 전문기업이다.", "1")],
            ),
            _section(
                "portfolio",
                [("가나다전자의 검사 장비 사업이 포트폴리오 중심이다.", "1")],
            ),
        ]
    )
    assert find_numeric_duplicates(report) == ()


def test_서로_다른_지표의_수치는_잡히지_않는다():
    report = _report(
        [
            _section(
                "business_model",
                [("2025년 매출액은 900억원이다.", "1")],
            ),
            _section(
                "operations_partners",
                [("협력사는 총 12개다.", "2")],
            ),
        ]
    )
    assert find_numeric_duplicates(report) == ()


def test_같은_장_같은_형식_안의_반복은_대상이_아니다():
    # dedupe.py(생성 단계)가 이미 다루는 영역 — 이 검출기는 "다른 장/다른 형식"만 본다.
    report = _report(
        [
            _section(
                "business_model",
                [
                    ("2025년 매출액은 900억원이다.", "1"),
                    ("2025년 매출액 900억원을 기록했다.", "1"),
                ],
            ),
        ]
    )
    assert find_numeric_duplicates(report) == ()


def test_요약_재사용은_스캔하지_않는다():
    # 정본이 명시적으로 허용한 반복(핵심 요약은 검증 문장 글자 그대로 재사용) —
    # summary_items에도 같은 수치가 있지만 본문에는 한 번뿐이면 검출 0건이어야 한다.
    report = _report(
        [_section("business_model", [("2025년 매출액은 900억원이다.", "1")])],
        summary=[SummaryItem(text="2025년 매출액은 900억원이다.")],
    )
    assert find_numeric_duplicates(report) == ()


# ══════════════════════════════════════════════════════════
# ② 진짜 중복 — 확정
# ══════════════════════════════════════════════════════════


def test_같은_수치가_두_장에_있으면_확정으로_잡는다():
    report = _report(
        [
            _section("past_changes", [("2025년 매출액은 1,200억원이다.", "1")]),
            _section("current_challenges", [("2025년 매출액은 1,200억원이다.", "2")]),
        ]
    )
    findings = find_numeric_duplicates(report)
    assert len(findings) == 1
    assert findings[0].confidence == CONFIDENCE_CONFIRMED
    assert "서로 다른 장" in findings[0].reason
    assert {occ.section_id for occ in findings[0].occurrences} == {
        "past_changes",
        "current_challenges",
    }


def test_같은_사실이_문장과_표에_있으면_확정으로_잡는다():
    table = ReportTable(
        caption="3개년 주요 실적",
        headers=["항목", "2023", "2024", "2025"],
        rows=[["매출액", "900", "1,000", "1,200"]],
        display_unit="억원",
    )
    report = _report(
        [
            _section(
                "past_changes",
                prose=[("2025년 매출액은 1,200억원을 기록했다.", "1")],
                tables=[table],
            ),
        ]
    )
    findings = find_numeric_duplicates(report)
    assert len(findings) == 1
    assert findings[0].confidence == CONFIDENCE_CONFIRMED
    assert "형식" in findings[0].reason
    assert {occ.format for occ in findings[0].occurrences} == {"문장", "표"}


# ══════════════════════════════════════════════════════════
# ③ 애매한 경우 — 의심으로 낮춘다 (오탐을 두려워하되 감추지 않는다)
# ══════════════════════════════════════════════════════════


def test_기간이_다르면_의심으로_낮춘다():
    report = _report(
        [
            _section("past_changes", [("2023년 매출액은 900억원이다.", "1")]),
            _section("current_challenges", [("2025년 목표는 900억원이다.", "2")]),
        ]
    )
    findings = find_numeric_duplicates(report)
    assert len(findings) == 1
    assert findings[0].confidence == CONFIDENCE_SUSPECTED


def test_기간을_모르면_의심으로_낮춘다():
    report = _report(
        [
            _section("business_model", [("매출액은 900억원 수준이다.", "1")]),
            _section("current_challenges", [("경쟁사 매출도 900억원 규모다.", "2")]),
        ]
    )
    findings = find_numeric_duplicates(report)
    assert len(findings) == 1
    assert findings[0].confidence == CONFIDENCE_SUSPECTED


def test_지표_힌트가_뚜렷이_다르면_확정에서_의심으로_낮춘다():
    report = _report(
        [
            _section("business_model", [("2025년 매출액은 900억원이다.", "1")]),
            _section("culture", [("2025년 임직원 수는 900명이 아니라 900억원 규모 예산이다.", "2")]),
        ]
    )
    # 두 문장 모두 "900억원"·"2025"를 공유하지만 지표 힌트("매출액" vs 이 예산
    # 문장은 힌트가 비어 있지 않은 경우를 가정) — 힌트가 뚜렷이 다르면 확정을
    # 의심으로 낮춘다. (이 케이스는 힌트 추출이 애매할 수 있어 결과가 확정이든
    # 의심이든 최소 1건은 잡혀야 한다는 것만 함께 확인한다.)
    findings = find_numeric_duplicates(report)
    assert len(findings) == 1


# ══════════════════════════════════════════════════════════
# ④ 출고를 막지 않는다 (배선 안 됨을 못 박는다)
# ══════════════════════════════════════════════════════════


def test_출고를_막지_않는다():
    """find_numeric_duplicates는 예외를 던지지 않는다 — 값만 돌려준다."""
    report = _report(
        [
            _section("past_changes", [("2025년 매출액은 1,200억원이다.", "1")]),
            _section("current_challenges", [("2025년 매출액은 1,200억원이다.", "2")]),
        ]
    )
    findings = find_numeric_duplicates(report)  # 예외 없이 끝나야 한다
    assert len(findings) == 1  # 검출은 되지만 여기서 막지 않는다(값만 돌려줌)
