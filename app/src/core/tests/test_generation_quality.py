from __future__ import annotations

import pytest

from src.core.generation_quality import (
    SHADOW_ASSESSMENT_MODE,
    UnboundGenerationSection,
    observe_unbound_generation,
)
from src.features.report_quality.constants import QUALITY_CONTRACT_VERSION


def test_원자_claim이_없는_생성물을_가짜_fact로_통과시키지_않는다() -> None:
    observation = observe_unbound_generation(
        (
            UnboundGenerationSection(
                section_id="identity",
                has_public_content=True,
                notice_only=False,
            ),
        )
    )

    assert observation.mode == SHADOW_ASSESSMENT_MODE
    assert observation.contract_version == QUALITY_CONTRACT_VERSION
    assert observation.quality_grade == "미완성"
    assert observation.safety_decision == "공개 차단"
    assert observation.publication_grade == "미완성"
    assert observation.release_allowed is False
    assert any(
        "fact_id와 결속되지 않은 공개 내용" in problem
        for problem in observation.safety_problems
    )


def test_알수없는_품질계약_버전을_최신으로_몰래_바꾸지_않는다() -> None:
    with pytest.raises(ValueError, match="알 수 없는 보고서 품질 계약 버전"):
        observe_unbound_generation((), contract_version="없는-계약")
