"""파이프라인 5번 — 대상 판정 사다리 (전부 코드, AI 호출 없음, 0원).

  [조건 0] 사업자번호가 공공기관 명단에 있나 → 거부 A  («모든» 경로)
  [조건 1] 법인구분이 유가(Y)·코스닥(K)·코넥스(N)인가 — 종목코드 유무가 아니다(한진해운 실측)
  [조건 2] 공개된 재무 자료가 있나 → 비상장 외감 / 없으면 거부 B
    ├ 2-a 공시 목록에 「감사보고서」가 있나 (작은 외감 대상 회사가 내는 별도 공시)
    └ 2-b 전자공시에 재무제표가 공개돼 있나 (나중에 더한 갈래)

★ 왜 2-b 가 필요했나 (실측 · 현대카드)
  「감사보고서」라는 «이름의» 공시는 사업보고서를 내지 «않는» 작은 외감 회사가 낸다.
  현대카드처럼 사업보고서를 내는 회사는 감사보고서를 그 «안에» 첨부하므로 별도
  공시가 없다. 그래서 2-a 만 보면 «공시를 가장 많이 하는 대형 비상장사»가 거부됐다.
  실측: 현대카드는 3년간 331건을 공시하고 재무 API 로 38개 계정이 나오는데도 거부B.
  같은 증상 — 현대캐피탈 · 현대커머셜.
  → 물어야 할 것은 「감사보고서라는 이름의 공시가 있나」가 아니라
    **「분석할 재무 자료가 실제로 있나」**다. 이 제품이 거부하는 이유가 그것이다.
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
#: ★ 띄어쓰기가 «있다» — 앱 이력 정본과 한 글자도 달라선 안 된다
#:   (`app/src/features/observability/constants.py` 의 CORP_TYPE_UNLISTED_AUDITED).
#:   실측(2026-09-05 운영): 여기가 「비상장외감」이라 비상장 회사(현대카드·우리은행)
#:   조사의 이력 1행이 허용값 검사에 걸려 전부 거부됐다. 기록 실패는 조용히
#:   삼켜지므로 보고서는 나갔지만, 실행 상태가 「진행 중」으로 남고 대시보드·
#:   하루 집계·게이트 진단이 통째로 빠졌다. 상장사는 글자가 같아 멀쩡했다.
TYPE_AUDITED = "비상장 외감"


@dataclass(frozen=True)
class Judgment:
    status: str                  # 대상 / 거부A_공공기관 / 거부B_외감아님
    corp_type: Optional[str]     # 상장사 / 비상장 외감 / None
    reason: str                  # 사람이 읽는 판정 근거 한 줄


def decide(
    corp_cls: str,
    has_audit_report: bool,
    bizno: Optional[str],
    public_org_lookup: Callable[[object], Optional[str]],
    has_financial_statements: bool = False,
) -> Judgment:
    """판정 사다리 — 위에서부터 먼저 걸리는 곳으로 확정한다.

    Args:
        corp_cls: 기업개황 법인구분 (Y/K/N/E) — 층3 [6] 응답에서 넘어온다.
        has_audit_report: 공시 목록에 감사보고서가 있는가 (조건 2-a 조회 결과).
        bizno: 사업자등록번호 — 조건 0 대조 키.
        public_org_lookup: 사업자번호 → 기관명 또는 None (features/public_org 주입).
        has_financial_statements: 전자공시에 재무제표가 공개돼 있는가 (조건 2-b).
            기본값 False — 이 인자를 안 넘기는 옛 호출부는 예전과 «똑같이» 동작한다.

    ★ 조건 0(공공기관 대조)을 «맨 위»로 옮겼다 (제품 결정)
      예전에는 상장 경로에서만 돌았다. 그래서 «비상장» 공공기관은 그 검사를
      건너뛰고 조건 2 로 떨어졌고, 자료가 없으니 거부B —
      화면에 「공개된 재무 자료가 없습니다」가 떴다. **거짓말이다.**
      실측: 한국철도공사·한국토지주택공사·한국관광공사·인천국제공항공사·
      한국산업은행 5곳이 공공기관 명단(355개)에 «정확히» 있는데도 그 화면을 봤다.
      거부되는 것은 맞다(정본: 공공기관·공기업은 다루지 않는다). 틀린 것은 «이유»다.
      → 이제 상장이든 아니든 공공기관이면 거부A 로 간다.
    """
    org_name = public_org_lookup(bizno)
    if org_name is not None:
        return Judgment(STATUS_REJECT_A, None, f"공공기관 명단 일치: {org_name}")
    if corp_cls in LISTED_CLS:
        return Judgment(STATUS_ACCEPT, TYPE_LISTED, f"법인구분 {corp_cls} = 상장")
    if has_audit_report:
        return Judgment(STATUS_ACCEPT, TYPE_AUDITED, "감사보고서 공시 존재")
    if has_financial_statements:
        return Judgment(STATUS_ACCEPT, TYPE_AUDITED, "공개된 재무제표 존재")
    return Judgment(STATUS_REJECT_B, None, "상장 아님 + 공개된 재무 자료 없음")
