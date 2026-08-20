"""파이프라인 5번 — 대상 판정 사다리 (전부 코드, AI 호출 없음, 0원).

정본: 확정/기획서/02_판정/01_흐름.md
  [조건 1] 법인구분이 유가(Y)·코스닥(K)·코넥스(N)인가 — 종목코드 유무가 아니다(한진해운 실측)
    └ [조건 0] 사업자번호가 공공기관 명단에 있나 → 거부 A  (상장 경로에서만)
  [조건 2] 공시 목록에 감사보고서가 있나 → 비상장 외감 / 없으면 거부 B
공공기관 대조는 함수 주입으로 받는다 — feature 간 직접 import 금지 규칙.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

LISTED_CLS = frozenset({"Y", "K", "N"})  # 유가·코스닥·코넥스 (E=기타)

STATUS_ACCEPT = "대상"
STATUS_REJECT_A = "거부A_공공기관"
STATUS_REJECT_B = "거부B_외감아님"
TYPE_LISTED = "상장사"
TYPE_AUDITED = "비상장외감"


@dataclass(frozen=True)
class Judgment:
    status: str                  # 대상 / 거부A_공공기관 / 거부B_외감아님
    corp_type: Optional[str]     # 상장사 / 비상장외감 / None
    reason: str                  # 사람이 읽는 판정 근거 한 줄


def decide(
    corp_cls: str,
    has_audit_report: bool,
    bizno: Optional[str],
    public_org_lookup: Callable[[object], Optional[str]],
) -> Judgment:
    """판정 사다리 — 위에서부터 먼저 걸리는 곳으로 확정한다.

    Args:
        corp_cls: 기업개황 법인구분 (Y/K/N/E) — 층3 [6] 응답에서 넘어온다.
        has_audit_report: 공시 목록에 감사보고서가 있는가 (조건 2 조회 결과).
        bizno: 사업자등록번호 — 조건 0 대조 키.
        public_org_lookup: 사업자번호 → 기관명 또는 None (features/public_org 주입).
    """
    if corp_cls in LISTED_CLS:
        org_name = public_org_lookup(bizno)
        if org_name is not None:
            return Judgment(STATUS_REJECT_A, None, f"공공기관 명단 일치: {org_name}")
        return Judgment(STATUS_ACCEPT, TYPE_LISTED, f"법인구분 {corp_cls} = 상장")
    if has_audit_report:
        return Judgment(STATUS_ACCEPT, TYPE_AUDITED, "감사보고서 공시 존재")
    return Judgment(STATUS_REJECT_B, None, "상장 아님 + 감사보고서 없음")
