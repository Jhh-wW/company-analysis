# -*- coding: utf-8 -*-
"""«확인» 산문의 자기 원문 근거 검사에 쓰는 사유 코드와 표지.

★ 사유 코드 글자는 `shared/report_quality/review_diagnostic_constants.py` 의
  REVIEW_SCOPE_ITEMS 와 «반드시 같은 값»이어야 진단이 표에서 새지 않는다.
  두 곳이 어긋나면 co-located 동기화 시험이 빨간불이 된다.
"""

from typing import Final
import re

#: 감사 기록에 남길 근거어 수 상한. 판정 문턱은 기존 공유 계약을 따른다.
SUPPORT_TERM_LOG_LIMIT: Final[int] = 12

PROSE_OWN_SOURCE_UNSUPPORTED: Final[str] = "prose_own_source_unsupported"
PROSE_REVENUE_PRIMACY_FROM_RECOGNITION_ONLY: Final[str] = (
    "prose_revenue_primacy_from_recognition_only"
)
PROSE_OWN_SOURCE_REASON_CODES: Final[tuple[str, ...]] = (
    PROSE_OWN_SOURCE_UNSUPPORTED,
    PROSE_REVENUE_PRIMACY_FROM_RECOGNITION_ONLY,
)

#: 문장을 절로 나눈다. 마침표·줄바꿈만 쓰고 쉼표는 절을 나누지 않는다.
CLAUSE_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"[.!?。\n]+")

#: 근거어를 셀 때 쓰는 낱말. `prose_facts` 와 «같은» 규칙을 써야 한다 —
#: 결속과 공개가 다른 낱말을 세면 이번 결함이 다시 난다.
SUPPORT_WORD_RE: Final[re.Pattern[str]] = re.compile(r"[0-9A-Za-z가-힣]{2,}")

# ── 규칙 ②: «주요 수익원» 단정을 순수 회계 인식 설명으로만 세운 경우 ──
#: 후보가 순위 낱말과 수익원 자리를 «붙여» 단정했는가. 「주요 통화」·
#: 「핵심 성공요소」처럼 순위가 아닌 관용 표현은 이 모양이 아니다.
REVENUE_PRIMACY_CLAIM_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:주요|주된|핵심)(?:한|적인)?(?:수익원|매출원|수입원)"
)

#: 원문 절이 «언제·어떻게 수익으로 인식하는가»만 말하는 회계 인식 설명인가.
RECOGNITION_CLAUSE_RE: Final[re.Pattern[str]] = re.compile(
    r"수익으로인식|수익인식|인식합니다|인식한다|인식하며|"
    r"기업회계기준서|수행의무|이행할때|진행기준|인도기준"
)

#: 원문 절이 «얼마나 큰가»를 조금이라도 말하는가. 이 표지가 하나라도 있으면
#: 그 원문은 순수 인식 설명이 아니므로 이 검사는 판정하지 않고 물러난다.
#: 1%인지 70%인지, 그 대상이 맞는지는 **이 검사가 판정하지 않는다**.
REVENUE_MAGNITUDE_MARKER_RE: Final[re.Pattern[str]] = re.compile(
    r"주요|주된|핵심|대부분|절대적|최대|가장|비중|차지|퍼센트|%"
)
REVENUE_WORD_RE: Final[re.Pattern[str]] = re.compile(r"수익|매출|수입")

__all__ = [
    "CLAUSE_SPLIT_RE",
    "PROSE_OWN_SOURCE_REASON_CODES",
    "PROSE_OWN_SOURCE_UNSUPPORTED",
    "PROSE_REVENUE_PRIMACY_FROM_RECOGNITION_ONLY",
    "RECOGNITION_CLAUSE_RE",
    "REVENUE_MAGNITUDE_MARKER_RE",
    "REVENUE_PRIMACY_CLAIM_RE",
    "REVENUE_WORD_RE",
    "SUPPORT_WORD_RE",
    "SUPPORT_TERM_LOG_LIMIT",
]
