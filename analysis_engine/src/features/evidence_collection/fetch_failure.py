"""DART 수집 포트에서 복구 가능한 외부 실패만 식별한다.

``DartFetcher``의 구현은 실제 DART 어댑터뿐 아니라 app이 조립한 callback도
포함한다. 그래서 모든 ``Exception``을 FAILED로 바꾸면 함수 인자 불일치나
잘못된 반환 자료형 같은 코드 결함까지 "외부 자료를 못 받음"으로 위장된다.

실제 core DART 어댑터는 HTTP·응답 계약 오류를 아래 닫힌 예외 타입으로
정규화한다. 파일 cache I/O와 Python의 표준 timeout/connection 계열은
``OSError``로 남을 수 있다. 이 셋만 개별 수집 실패로 복구하고, 그 밖의
예외(TypeError·AttributeError·KeyError·AssertionError·ValueError 등)는
호출자에게 그대로 올려 전체 실행을 내부 계약 오류로 닫는다.
"""

from __future__ import annotations

from core.dart_client import DartResponseError, DartTransportError


RECOVERABLE_EXTERNAL_FETCH_ERRORS = (
    DartTransportError,
    DartResponseError,
    OSError,
)


def is_recoverable_external_fetch_error(error: Exception) -> bool:
    """예상한 외부 전송·응답·cache I/O 실패인지 닫힌 목록으로 판정한다."""

    return isinstance(error, RECOVERABLE_EXTERNAL_FETCH_ERRORS)
