from __future__ import annotations

import pytest

from src.shared.comparison_candidate_basis import (
    comparison_source_candidate_support_terms,
)


@pytest.mark.parametrize(
    ("sentence", "candidate", "expected"),
    (
        (
            "주식회사 알파는 주식회사 베타와 경쟁한다.",
            "주식회사 베타",
            ("주식회사 베타", "와 경쟁한다"),
        ),
        (
            "Beta competes directly with us.",
            "Beta",
            ("beta", "competes directly with"),
        ),
    ),
)
def test_닫힌_후보판별기가_확인한_별칭과_관계표현만_운반한다(
    sentence: str,
    candidate: str,
    expected: tuple[str, str],
) -> None:
    assert comparison_source_candidate_support_terms(sentence, candidate) == expected


@pytest.mark.parametrize(
    ("sentence", "candidate"),
    (
        ("알파는 베타와 협력한다.", "베타"),
        ("알파는 베타와 경쟁하지 않는다.", "베타"),
        ("알파는 감마와 경쟁한다.", "베타"),
    ),
)
def test_경쟁관계나_후보별칭을_추측해서_근거어를_만들지않는다(
    sentence: str,
    candidate: str,
) -> None:
    assert comparison_source_candidate_support_terms(sentence, candidate) == ()
