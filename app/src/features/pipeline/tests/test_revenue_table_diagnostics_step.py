"""매출 구성표가 «0개»일 때도 왜 없는지가 실행 기록에 남는지 못 박는다.

★★ 왜 이 시험이 있나 (실측 2026-09-07) — 예전에는 표가 «있을 때만»
  `6_수집_매출구성` 단계를 적었다. 그래서 표가 없는 회사는 로그에서
  「아예 안 찾아봤다」와 「찾았지만 못 세웠다」가 똑같이 침묵으로 보였다.
  어느 쪽인지 모르면 고칠 수가 없다.
★ 시험 안에서 단계를 따로 만들지 않는다. 운영 함수 `real._collect`를 그대로
  불러 그 함수가 남긴 기록을 본다 — 배선이 끊기면 여기서 빨간불이 난다.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.core import revenue_table_switch
from src.features.pipeline import real
from src.features.pipeline.port import UserInput

_FILING = {
    "rcept_no": "20260331000123",
    "rcept_dt": "20260331",
    "report_nm": "사업보고서 (2025.12)",
}
#: 표가 나오는 원문 — 가로형 지역표 하나. 회사 이름·지역 이름 모두 일반 어휘다.
_표가_있는_원문 = (
    "가나다전자 6. 영업부문 정보. "
    "지역에 대한 공시 당기 (단위 : 천원) 지역 지역 합계 국내 해외 일본 기타 국가 "
    "매출액 60,000 15,000 5,000 80,000 끝."
)
#: 표가 하나도 안 나오는 원문 — 「금액 합이 합계와 다른」 아깝게 떨어지는
#: 표를 일부러 넣었다. 후보가 아예 없으면 사유 칸이 비어도 초록이라
#: 「사유를 남긴다」를 증명하지 못한다.
_표가_없는_원문 = (
    "가나다전자 가. 매출 실적. "
    "(1) 연결 (단위 : 천원) 사업부문 매출유형 품 목 제32기 "
    "가전사업 제품 냉장고 44,000 통신사업 제품 휴대전화 36,000 합계 90,000"
)


class _Engine:
    RAW_DIR = Path(".")
    SECTION_HEADS: dict[str, str] = {}
    FRAG_CHARS = 0

    def __init__(self, filing_text: str) -> None:
        self.filing_text = filing_text

    def download_document(self, *_args: Any) -> str:
        return "fixture"

    def read_filing_text(self, _path: str) -> str:
        return self.filing_text

    def make_fragments(self, *_args: Any) -> dict[int, dict[str, str]]:
        return {}


def _empty_collection() -> SimpleNamespace:
    return SimpleNamespace(
        state="none",
        detail="시험 자료 없음",
        candidate_scope_complete=True,
        fragments=(),
        attempted_documents=0,
        downloaded_pdf_bytes=0,
    )


def _collect(filing_text: str, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    monkeypatch.setenv(revenue_table_switch.REVENUE_TABLE_V2_ENV_NAME, "1")
    revenue_table_switch._reset_process_revenue_table_switch_for_tests()  # noqa: SLF001
    monkeypatch.setattr(real.filing_clean, "repair", lambda frags, *_a: (frags, 0))
    monkeypatch.setattr(real.filing_extra, "add_to", lambda frags, *_a: (frags, 0))
    monkeypatch.setattr(
        real.filing_relationships, "add_to", lambda frags, *_a: (frags, 0)
    )
    monkeypatch.setattr(
        real, "collect_homepage_fragments", lambda *_a, **_k: _empty_collection()
    )
    monkeypatch.setattr(
        real, "collect_official_ir_fragments", lambda *_a, **_k: _empty_collection()
    )
    steps: list[dict[str, Any]] = []
    try:
        real._collect(  # noqa: SLF001
            _Engine(filing_text),
            object(),
            {"corp_name": "가나다전자", "hm_url": ""},
            UserInput(company="가나다전자", job="", region="", posting_text=""),
            object(),
            steps,
            financials=None,
            fin_years=[],
            filing=_FILING,
        )
    finally:
        revenue_table_switch._reset_process_revenue_table_switch_for_tests()  # noqa: SLF001
    return steps


def _구성표_단계(steps: list[dict[str, Any]]) -> dict[str, Any]:
    return next(step for step in steps if step["step"] == "6_수집_매출구성")


def test_표가_0개여도_단계와_축별_탈락사유를_남긴다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    단계 = _구성표_단계(_collect(_표가_없는_원문, monkeypatch))

    assert 단계["표"] == 0
    assert set(단계["탈락사유"]) == {"product", "region"}
    # 「왜 없나」가 실제로 적혀야 한다 — 빈 목록이면 아무것도 못 고친다.
    assert "sum_mismatch" in 단계["탈락사유"]["product"]


def test_표가_있으면_사유_칸_없이_표_수만_남긴다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 표가 나왔는데 사유를 적으면 읽는 사람이 실패로 오해한다."""

    단계 = _구성표_단계(_collect(_표가_있는_원문, monkeypatch))

    assert 단계 == {"step": "6_수집_매출구성", "표": 1}
