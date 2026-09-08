"""공유 검수 관측 경계의 독립 안전성 회귀 시험."""

from __future__ import annotations

from hashlib import sha256

import pytest

from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence
from src.features.composer.review_outcomes import final_review_outcomes
from src.shared.report_quality.review_diagnostics import observed_review_outcomes


def _event(candidate: str = "제외된 후보") -> dict[str, object]:
    return {
        "section_id": "identity",
        "kind": "본문",
        "reason_code": "semantic_grounding_missing",
        "candidate_sha256": sha256(candidate.encode()).hexdigest(),
        "verification_items": ("수치",),
    }


@pytest.mark.parametrize("malformed", (None, {}, [None, {}, []]))
def test_관측_경계는_최상위_비목록_및_오염_목록을_안전하게_버린다(
    malformed: object,
) -> None:
    assert observed_review_outcomes(malformed) == ()  # type: ignore[arg-type]


def test_중간_관측은_남기되_성공_최종_출력에_남은_후보는_제외한다() -> None:
    candidate = "성공한 후보"
    event = _event(candidate)
    report = ComposedReport(
        (ComposedSection("identity", (ComposedSentence(candidate, ("1",), "확인"),)),)
    )

    # 최종 조립 전에는 실패 중간 관측 자체를 원문 없이 전달할 수 있다.
    assert observed_review_outcomes([event]) == (event,)
    # 후보가 최종 결과에 남았다면 실패/제외 기록으로 바꾸지 않는다.
    assert final_review_outcomes(report, [event]) == ()
