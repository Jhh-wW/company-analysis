"""검수·도식 응답의 «번호» 필드를 좁게 보정한다 (composer 전용).

★ 왜 필요한가 — 검수·도식 검증 AI가 JSON 정수 대신 순수 숫자 문자열
  ("3")을 «번호»에 낼 때가 있다. 그 행 하나만 탈락하는 게 아니라, 응답의
  모든 행이 이렇게 나오면 판정 전체가 빈 사전이 되어 통째로 버려지고
  같은 호출을 다시 보냈다(PARSE_RETRY_LIMIT=1, 회사당 약 20원 낭비).
  이 모듈은 판정 계약을 넓히지 않는다 — «참»인 숫자의 표현형만 넓힌다.
  음수·소수·빈 문자열·"3번" 같은 비숫자 문자열은 지금처럼 그대로 거부한다.
★ verify.py(문장 검수)와 diagram_check.py(도식 검수)가 같은 규칙을 쓰도록
  한 곳에 둔다 — 두 벌로 베끼면 한쪽만 고쳐져 갈라진다.
"""

from __future__ import annotations

import re
from typing import Final, Optional

#: 앞뒤 공백만 허용하고 부호·소수점 없는 순수 숫자 문자열만 통과시킨다.
_PURE_DIGITS_RE = re.compile(r"^[0-9]+$")

#: 검수·도식 응답의 «번호»가 실제로 가질 수 있는 자릿수의 넉넉한 상한.
#: ★ 왜 필요한가 (적대 검토 실측) — 파이썬 3.13은 문자열→정수 변환에 기본
#:   4,300자리 상한(``sys.get_int_max_str_digits``)이 있다. 정규식만 믿고
#:   ``int(stripped)``를 바로 부르면 "9"*5000 같은 입력에서 ``ValueError``가
#:   그대로 터진다 — 수정 전에는 ``int()`` 호출 자체가 없어 이런 입력도 조용히
#:   한 행 탈락으로 끝났는데, 보정을 넣으며 «안전 거부»가 «예외»로 바뀔 뻔했다.
#:   실제 검수 응답의 번호는 그 응답이 다루는 문장·경로 개수를 넘지 않으므로
#:   9자리(최대 9억9999만9999)면 어떤 실제 보고서보다 넉넉한 여유다. int()를
#:   시도하기 전에 걸러 그 한도 자체에 닿지 않게 한다.
_MAX_VERDICT_NUMBER_DIGITS: Final[int] = 9


def coerce_verdict_number(value: object) -> Optional[int]:
    """검수·도식 응답의 «번호» 필드를 정수로 좁게 보정한다.

    Args:
        value: JSON에서 그대로 꺼낸 «번호» 필드 값 (타입 무관).

    Returns:
        보정된 정수. 계약 밖 값이면 ``None``.

    허용:
        - ``bool`` 이 아닌 ``int`` — 원래부터 유효했던 값 그대로 통과.
        - 앞뒤 공백만 제거하면 숫자만 남고 길이가
          ``_MAX_VERDICT_NUMBER_DIGITS`` 이하인 문자열(``"3"``, ``" 3 "``).

    거부(예전과 동일한 기준 + 자릿수 상한 1개 추가):
        - ``bool`` — 파이썬에서 ``True``/``False`` 는 ``int`` 의 하위
          타입이라 막지 않으면 ``"번호": true`` 가 1번 줄로 읽힌다.
        - 부호·소수점이 있거나 숫자가 아닌 문자열
          (``"-1"``, ``"1.0"``, ``"3번"``, ``""``).
        - ``_MAX_VERDICT_NUMBER_DIGITS`` 자리를 넘는 순수 숫자 문자열 —
          진짜 번호가 아니라 형식 오류·적대적 입력으로 보고 ``int()`` 변환을
          아예 시도하지 않는다.
        - 그 외 모든 타입(``None``, 리스트 등).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if (
            _PURE_DIGITS_RE.fullmatch(stripped)
            and len(stripped) <= _MAX_VERDICT_NUMBER_DIGITS
        ):
            return int(stripped)
    return None
