# -*- coding: utf-8 -*-
"""머리말 고지가 «한 줄»로 모이는지 못 박는다.

★ 왜 (2026-08-29 눈가림 독립 평가)
  ─────────────────────────────────────────────────────────
  제외 고지가 장마다 한 줄씩 붙어 머리말이 최대 아홉 줄이 됐다.
  평가자가 「제외 사유 나열문이 완결성·서술품질을 깎는다」고 지적했다.
  읽는 사람에게 필요한 것은 «무엇이 몇 개 빠졌나»이지, 거의 같은 문장을
  아홉 번 읽는 것이 아니다.

★ 다만 «정보를 빼서» 좋아 보이게 만드는 것은 금지다.
  그래서 이 시험은 두 가지를 «동시에» 지킨다:
    ① 줄이 하나로 모였는가
    ② 총 개수와 «장 이름»이 그대로 남았는가   ← 정직성 안전선
"""

from __future__ import annotations

from src.features.composer.constants import SECTION_TITLES
from src.features.composer.pipeline import _apply_generation_quality_label
from src.features.composer.structured_claims import NumericSafetyFiltering
from src.features.pipeline.port import Grade, Report
from src.shared.report_quality.generation import GenerationQualityObservation

_빠진_장 = (("identity", 1), ("portfolio", 2), ("culture", 1))


def _관측() -> GenerationQualityObservation:
    """고지 문구만 보려는 시험이라 나머지는 «문제 없음»으로 채운다."""
    return GenerationQualityObservation(
        mode="shadow",
        contract_version="report-quality-v1",
        quality_grade="완성",
        safety_decision="공개 허용",
        publication_grade="완성",
        release_allowed=True,
        quality_shortfalls=(),
        safety_problems=(),
        substantive_claims=40,
        verified_claims=40,
        verified_ratio="1",
        document_sources=8,
    )


def _보고서() -> Report:
    return Report(
        company="가나다전자",
        job="",
        corp_type="상장사",
        grade=Grade.COMPLETE,
        sections=[],
        shortfall_reasons=[],
    )


def _고지들() -> list[str]:
    labelled = _apply_generation_quality_label(
        _보고서(),
        _관측(),
        NumericSafetyFiltering(removed_section_counts=_빠진_장),
    )
    return [reason for reason in labelled.shortfall_reasons if "수치·날짜 문장" in reason]


def test_제외_고지는_장마다가_아니라_한_줄이다() -> None:
    """★ 세 장에서 빠졌어도 고지는 한 줄이다."""
    assert len(_고지들()) == 1, "★ 장마다 한 줄씩 다시 늘어났다"


def test_한_줄로_모아도_총_개수를_숨기지_않는다() -> None:
    """★ 정직성 안전선 — 합치는 것과 «가리는» 것은 다르다."""
    한줄 = _고지들()[0]
    총합 = sum(count for _section, count in _빠진_장)

    assert f"{총합}개" in 한줄, f"★ 총 개수가 사라졌다: {한줄}"


def test_한_줄로_모아도_어느_장인지_숨기지_않는다() -> None:
    """★ 정직성 안전선 — 어느 장이 깎였는지 읽는 사람이 알아야 한다."""
    한줄 = _고지들()[0]

    for section_id, _count in _빠진_장:
        title = SECTION_TITLES[section_id]
        assert title in 한줄, f"★ 「{title}」 장이 고지에서 빠졌다: {한줄}"


def test_빠진_문장이_없으면_고지도_없다() -> None:
    """★ 아무것도 안 빠졌는데 고지를 만들지 않는다."""
    labelled = _apply_generation_quality_label(
        _보고서(), _관측(), NumericSafetyFiltering()
    )

    assert not [r for r in labelled.shortfall_reasons if "수치·날짜 문장" in r]
