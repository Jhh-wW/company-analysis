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
    return [reason for reason in labelled.shortfall_reasons if "숫자·날짜 문장" in reason]


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

    assert not [r for r in labelled.shortfall_reasons if "숫자·날짜 문장" in r]


# ══════════════════════════════════════════════════════════
# 개발자 말투가 독자에게 새지 않는다 (2026-08-29 눈가림 평가 감점 1위)
# ══════════════════════════════════════════════════════════

#: 독자가 읽는 문장에 있으면 안 되는 말.
#:
#: ★ 왜 (2026-08-29) — 눈가림 독립 평가에서 «세 평가자 모두» 표지·머리말의
#:   내부 문구 노출을 감점 1위로 꼽았다. 실제로 이런 문장이 인쇄됐다:
#:     「핵심 요약에서 검증된 본문 수치 claim과 결속되지 않은 …」
#:     「근거와 의미가 구조로 확인된 실질 내용이 3개라 완성 기준 40개에 못 미칩니다.」
#:   「claim」은 한국어 문장 안의 영어 식별자, 「결속」은 이 저장소 내부 용어,
#:   「완성 기준 40개」는 화면 어디에도 설명 없는 내부 임계값이다
#:   (그래서 「40점 만점에 3점」으로 오독된다).
#: ⚠️ 이건 «숨기라»는 뜻이 아니다 — 개수·장 이름·비율은 그대로 남긴다.
#:   위 시험들이 그 경계를 따로 지킨다.
_개발자_말투 = (
    "claim",
    "결속",
    "완성 기준",
    "구조화 근거",
    "실질 내용",
    "공개본",
    "새 안전 검사",
    "fact",
)


def _모든_사유() -> list[str]:
    """머리말에 나올 수 있는 문장을 «가능한 갈래마다» 한 번씩 만든다."""
    from dataclasses import replace

    관측 = replace(
        _관측(),
        quality_grade="부분 완성",
        release_allowed=False,
        substantive_claims=3,
        verified_claims=1,
        verified_ratio="0.33",
        document_sources=1,
        section_public_sentence_counts=(("identity", 1),),
        underfilled_sections=("identity",),
        notice_only_sections=("portfolio", "culture"),
    )
    labelled = _apply_generation_quality_label(
        _보고서(),
        관측,
        NumericSafetyFiltering(
            removed_section_counts=_빠진_장, removed_summary_count=2
        ),
    )
    return list(labelled.shortfall_reasons)


def test_머리말에_개발자_말투가_없다() -> None:
    """★ 독자는 우리 내부 용어를 모른다. 알 필요도 없다."""
    사유들 = _모든_사유()
    assert len(사유들) >= 6, f"시험 전제 — 갈래가 충분히 켜져야 한다: {len(사유들)}줄"

    for 사유 in 사유들:
        for 말 in _개발자_말투:
            assert 말 not in 사유, f"★ 개발자 말투가 새어 나왔다: 「{말}」 in 「{사유}」"


def test_쉬운_말로_바꿔도_숫자는_그대로_남는다() -> None:
    """★ 정직성 안전선 — 쉬운 말로 바꾸는 것과 «가리는» 것은 다르다."""
    한줄로 = " / ".join(_모든_사유())

    assert "3건" in 한줄로, "★ 확인된 사실 개수가 사라졌다"
    assert "1개" in 한줄로, "★ 참고한 원문 문서 개수가 사라졌다"
    assert "33%" in 한줄로, "★ 검증을 마친 비율이 사라졌다"
    assert "2개" in 한줄로, "★ 요약에서 뺀 문장 수가 사라졌다"


def test_임계값은_독자에게_보이지_않는다() -> None:
    """★ 40·8·50%는 내부 계약값이고 화면 어디에도 설명이 없다."""
    한줄로 = " / ".join(_모든_사유())

    for 임계값 in ("40개", "8개에", "50%에"):
        assert 임계값 not in 한줄로, f"★ 내부 임계값이 다시 새어 나왔다: 「{임계값}」"
