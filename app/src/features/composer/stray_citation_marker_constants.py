# -*- coding: utf-8 -*-
"""본문 «글자»에 남은 인용 아닌 대괄호 숫자의 닫힌 사유 코드와 치환 글자.

★ 이 파일의 사유 코드 문자열은 `src.shared.report_quality`의
  `review_diagnostic_constants.REVIEW_SCOPE_ITEMS` 열쇠와 «반드시 같은 값»이어야
  한다. 어긋나면 진단이 전송 계약에서 통째로 버려진다(그쪽 동기화 시험이 깬다).
"""

from typing import Final

#: 문장 글자 속 ``[숫자]``가 그 문장의 «자기 인용 번호»가 아닐 때의 사유 코드.
CITATION_MARKER_NOT_IN_CITATIONS: Final[str] = "citation_marker_not_in_citations"

#: 위 사유가 진단 표에서 묶이는 검증 항목 이름.
CITATION_MARKER_ITEM: Final[str] = "인용 표기"

#: 이 모듈이 내보내는 사유 코드 전부(전송 계약 동기화 시험이 읽는 정본).
STRAY_CITATION_MARKER_REASON_CODES: Final[tuple[str, ...]] = (
    CITATION_MARKER_NOT_IN_CITATIONS,
)

#: 글자로 남길 때 쓰는 전각 대괄호 — U+FF3B / U+FF3D.
#: ★ 왜 전각인가 — 출고 검사(`output_validation.CITATION_MARKER_RE`)는 ASCII
#:   ``[`` ``]``만 인용 표기로 읽는다. 전각으로 바꾸면 뜻(눈에 보이는 숫자)은
#:   그대로 남고 인용으로는 더 이상 읽히지 않는다. 숫자는 건드리지 않으므로
#:   근거어 대조(`own_source_support_terms`)에서 세던 낱말도 그대로 세어진다.
FULLWIDTH_LEFT_SQUARE_BRACKET: Final[str] = "［"
FULLWIDTH_RIGHT_SQUARE_BRACKET: Final[str] = "］"

#: ``int()``로 바꿔 볼 자릿수 상한. CPython은 4,300자리를 넘는 문자열→정수
#: 변환을 ValueError로 막으므로(sys.set_int_max_str_digits), 그 앞에서 «인용
#: 번호가 아니다»로 닫는다. 부록 번호가 이 자릿수가 될 일은 없다.
MAX_CITATION_NUMBER_DIGITS: Final[int] = 9
