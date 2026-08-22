"""운영 SQLite에 저장하는 JSON 문서의 공통 자원 상한."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from typing import Final


MAX_FIELD_BYTES: Final[int] = 8 * 1024 * 1024
MAX_DOCUMENT_NODES: Final[int] = 20_000
MAX_DOCUMENT_CONTAINER_ITEMS: Final[int] = 20_000
MAX_CONTAINER_ITEMS: Final[int] = 4_096
MAX_DOCUMENT_DEPTH: Final[int] = 64
CHECK_INTERVAL: Final[int] = 128


class PersistedJsonContractError(ValueError):
    """저장 JSON이 공통 형식·자원 상한을 벗어남."""


def validate_persisted_json_text(
    payload: str,
    *,
    deadline_check: Callable[[], None] | None = None,
) -> object:
    """JSON을 한 번 해석해 바이트·노드·항목·깊이 상한을 전수 검사한다."""

    check = deadline_check or (lambda: None)
    check()
    if type(payload) is not str:
        raise PersistedJsonContractError("저장 JSON은 문자열이어야 합니다")
    try:
        byte_count = len(payload.encode("utf-8"))
    except UnicodeError as exc:
        raise PersistedJsonContractError("저장 JSON을 UTF-8로 해석할 수 없습니다") from exc
    if byte_count > MAX_FIELD_BYTES:
        raise PersistedJsonContractError("저장 JSON이 바이트 상한을 넘었습니다")
    try:
        root = json.loads(payload)
    except (ValueError, RecursionError, MemoryError) as exc:
        raise PersistedJsonContractError("저장 JSON을 안전하게 해석할 수 없습니다") from exc
    check()

    stack: list[tuple[object, int]] = [(root, 0)]
    nodes = 0
    container_items = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > MAX_DOCUMENT_NODES:
            raise PersistedJsonContractError("저장 JSON이 노드 상한을 넘었습니다")
        if depth > MAX_DOCUMENT_DEPTH:
            raise PersistedJsonContractError("저장 JSON이 깊이 상한을 넘었습니다")
        if nodes % CHECK_INTERVAL == 0:
            check()

        if type(value) is dict:
            item_count = len(value)
            if item_count > MAX_CONTAINER_ITEMS:
                raise PersistedJsonContractError("저장 JSON container가 항목 상한을 넘었습니다")
            container_items += item_count
            if container_items > MAX_DOCUMENT_CONTAINER_ITEMS:
                raise PersistedJsonContractError("저장 JSON이 전체 항목 상한을 넘었습니다")
            stack.extend((item, depth + 1) for item in value.values())
        elif type(value) is list:
            item_count = len(value)
            if item_count > MAX_CONTAINER_ITEMS:
                raise PersistedJsonContractError("저장 JSON container가 항목 상한을 넘었습니다")
            container_items += item_count
            if container_items > MAX_DOCUMENT_CONTAINER_ITEMS:
                raise PersistedJsonContractError("저장 JSON이 전체 항목 상한을 넘었습니다")
            stack.extend((item, depth + 1) for item in value)
        elif type(value) is float:
            if not math.isfinite(value):
                raise PersistedJsonContractError("저장 JSON에 유한하지 않은 수가 있습니다")
        elif type(value) not in (str, int, bool, type(None)):
            raise PersistedJsonContractError("저장 JSON 값 타입을 해석할 수 없습니다")
    check()
    return root
