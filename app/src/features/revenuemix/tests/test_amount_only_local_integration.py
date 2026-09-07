"""로컬에 저장된 DART 원문 23건으로 「표가 실제로 몇 건에서 나오나」를 잰다.

★ 왜 필요한가 — 가공 원문 시험은 «내가 만든 모양»만 지킨다. 진짜 공시는
  머리말이 세 겹으로 눌려 있고 앞 표의 숫자가 바로 붙어 온다. 그 원문에서
  실제로 몇 건이 열리는지는 여기서만 알 수 있다.
★ 원문은 용량·저작권 때문에 저장소에 넣지 않는다. 없으면 «건너뛴다» —
  다만 조용히 넘어가지 않고 건너뛴 이유를 남긴다.

⚠️ 이 시험은 개수의 «하한»만 못 박는다. 파서가 좋아져 더 많이 열리는 것은
  막지 않되, 지금 열리는 것이 닫히면 빨간불이 난다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.core import revenue_table_switch as switch
from src.features.revenuemix.logic import build

pytestmark = pytest.mark.local_integration

#: 파일럿 실행이 내려받아 둔 원문. 접수번호 14자리가 파일 이름이다.
_RAW_DIRS = (
    Path(__file__).resolve().parents[5] / "analysis_engine" / "data" / "pilot" / "raw_filings",
    Path(__file__).resolve().parents[6]
    / "기업분석2"
    / "analysis_engine"
    / "data"
    / "pilot"
    / "raw_filings",
)

#: 이 아래로 떨어지면 회귀다. 2026-09-07 실측 — 고치기 «전» 지역표 6건,
#: 「금액만·가로형」을 더한 «뒤» 15건. 제품표는 14건 → 16건.
#: ⚠️ 이 값은 생산 상수가 아니라 «그날 눈으로 센 수»다. 코드에서 가져오면
#:   숫자가 줄어드는 회귀를 못 잡는다.
_REGION_FILES_FLOOR = 15
_PRODUCT_FILES_FLOOR = 16
_EXPECTED_FILINGS = 23

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _read_filing_text(path: Path) -> str:
    """운영 `read_filing_text`와 같은 태그 제거·공백 축약."""

    raw = path.read_bytes()
    for encoding in ("utf-8", "cp949", "euc-kr"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", text))


def _filings() -> tuple[Path, ...]:
    for directory in _RAW_DIRS:
        found = tuple(sorted(directory.glob("*.xml"))) if directory.is_dir() else ()
        if found:
            return found
    return ()


@pytest.fixture
def v2_켬(monkeypatch: pytest.MonkeyPatch):
    switch._reset_process_revenue_table_switch_for_tests()  # noqa: SLF001
    monkeypatch.setenv(switch.REVENUE_TABLE_V2_ENV_NAME, "1")
    yield
    switch._reset_process_revenue_table_switch_for_tests()  # noqa: SLF001


def _axes_by_filing(paths: tuple[Path, ...]) -> dict[str, set[str]]:
    return {
        path.stem: {str(표["axis"]) for 표 in build(_read_filing_text(path))}
        for path in paths
    }


def test_로컬통합_저장공시에서_지역표가_열리는_파일_수(v2_켬: None) -> None:
    paths = _filings()
    if not paths:
        pytest.skip("로컬 저장 공시(raw_filings/*.xml)가 없어 건너뜁니다")
    assert len(paths) >= _EXPECTED_FILINGS

    axes = _axes_by_filing(paths)
    지역 = sorted(name for name, found in axes.items() if "region" in found)
    제품 = sorted(name for name, found in axes.items() if "product" in found)

    assert len(지역) >= _REGION_FILES_FLOOR, f"지역표 파일 {len(지역)}건: {지역}"
    assert len(제품) >= _PRODUCT_FILES_FLOOR, f"제품표 파일 {len(제품)}건: {제품}"


def test_로컬통합_모든_표의_금액_합이_합계와_맞는다(v2_켬: None) -> None:
    """★★ 개수만 늘고 내용이 틀리면 더 나쁘다. 전 건의 검산을 다시 센다."""

    from decimal import Decimal

    from src.shared.revenue_table_provenance import (
        is_revenue_total_name_v2,
        revenue_amounts_sum_to_total,
        revenue_signed_decimal,
    )

    paths = _filings()
    if not paths:
        pytest.skip("로컬 저장 공시(raw_filings/*.xml)가 없어 건너뜁니다")

    검사한_표 = 0
    for path in paths:
        for 표 in build(_read_filing_text(path)):
            헤더 = list(표["headers"])
            행 = [list(row) for row in 표["rows"]]
            if not is_revenue_total_name_v2(행[-1][0]):
                continue
            if len(헤더) == 2:                       # 비중 없는 표 — 금액 합
                assert revenue_amounts_sum_to_total(
                    (r[1] for r in 행[:-1]), 행[-1][1]
                ), f"{path.stem} {표['caption']}"
            else:                                     # 비중 표 — 합계가 100%
                assert revenue_signed_decimal(행[-1][2]) == Decimal(100), (
                    f"{path.stem} {표['caption']}"
                )
            검사한_표 += 1
    assert 검사한_표 >= _REGION_FILES_FLOOR
