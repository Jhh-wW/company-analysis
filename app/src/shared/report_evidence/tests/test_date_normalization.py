from __future__ import annotations

import pytest

from src.shared.report_evidence.date_normalization import (
    normalize_official_source_date,
)


@pytest.mark.parametrize("raw", ("2024-02-29", "20240229"))
def test_실제_ISO와_옛_compact는_같은_canonical_날짜가_된다(raw: str) -> None:
    assert normalize_official_source_date(raw) == "2024-02-29"


@pytest.mark.parametrize(
    "raw",
    ("2025-02-30", "20250230", "2025/02/28", "임의 문자열", ""),
)
def test_없는날짜와_계약밖_문자열은_닫힌다(raw: str) -> None:
    with pytest.raises(ValueError, match="공식 자료 날짜"):
        normalize_official_source_date(raw)
