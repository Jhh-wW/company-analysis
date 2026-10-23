"""공급자 경계에서 키만 줄이고 기존 검수기에 넘기기 전에 그대로 복원한다."""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

from src.features.composer.constants import MISSING_VERDICTS_REMINDER, RETRY_REMINDER
from src.features.composer.prompt_metadata import PromptMetadata
from src.features.composer.review_wire_constants import REVIEW_WIRE_GUIDE, REVIEW_WIRE_KEYS

_ORIGINAL_KEYS = {alias: original for original, alias in REVIEW_WIRE_KEYS.items()}


@dataclass
class _ObjectKeys:
    start: int
    parent: _ObjectKeys | None
    keys: dict[str, bool] = field(default_factory=dict)
    end: int | None = None
    conflict: bool = False


class ReviewWireResponse(str):
    """기존 파서는 복원된 문자열을, 진단은 실제 전송 글자 수를 읽는다."""

    def __new__(cls, value: str, *, wire_response_chars: int):
        result = super().__new__(cls, value)
        result.wire_response_chars = wire_response_chars
        return result


def restore_review_wire(raw: str) -> str:
    """키 토큰만 복원한다. 값·공백·구문 오류는 보존해 기존 행 구제를 유지한다.

    같은 객체의 별칭 중복·원래 키 충돌은 해당 판정 행만 닫는다. 참조를 해석하거나
    판정·행·증명을 생성하지 않는다. 원래 키만 쓰는 구형 응답은 바이트가 같다.
    """
    replacements: list[tuple[int, int, str]] = []
    objects: list[_ObjectKeys] = []
    conflicts: list[_ObjectKeys] = []
    cursor = 0
    while cursor < len(raw):
        char = raw[cursor]
        if char == '{':
            objects.append(_ObjectKeys(cursor, objects[-1] if objects else None))
        elif char == '}' and objects:
            objects.pop().end = cursor + 1
        elif char == '"':
            end = cursor + 1
            while end < len(raw):
                if raw[end] == '\\':
                    end += 2
                    continue
                if raw[end] == '"':
                    break
                end += 1
            if end >= len(raw):
                break
            following = end + 1
            while following < len(raw) and raw[following].isspace():
                following += 1
            if following < len(raw) and raw[following] == ':':
                try:
                    key = json.loads(raw[cursor:end + 1])
                except ValueError:
                    cursor = end + 1
                    continue
                canonical = _ORIGINAL_KEYS.get(key, key)
                aliased = key in _ORIGINAL_KEYS
                if objects:
                    prior = objects[-1].keys.get(canonical)
                    if prior is not None and (prior or aliased):
                        if not objects[-1].conflict:
                            objects[-1].conflict = True
                            conflicts.append(objects[-1])
                    objects[-1].keys[canonical] = aliased
                if aliased:
                    replacements.append((cursor, end + 1, json.dumps(canonical, ensure_ascii=False)))
            cursor = end + 1
            continue
        cursor += 1
    rejected_rows: set[tuple[int, int]] = set()
    for node in conflicts:
        while node is not None and '번호' not in node.keys:
            node = node.parent
        if node is None or node.end is None:
            # 판정 번호가 있는 행 경계를 입증할 수 없으면 봉투를 닫는다.
            return '{}'
        rejected_rows.add((node.start, node.end))
    if rejected_rows:
        replacements = [item for item in replacements
                        if not any(start <= item[0] < end for start, end in rejected_rows)]
        replacements.extend((start, end, 'null') for start, end in rejected_rows)
        replacements.sort()
    if not replacements:
        return raw
    parts = []
    cursor = 0
    for start, end, key in replacements:
        parts.extend((raw[cursor:start], key))
        cursor = end
    parts.append(raw[cursor:])
    return ''.join(parts)


def review_wire_prompt(prompt: str) -> str:
    """렌더 원문·캐시 접두부·스키마를 보존하고 실제 전송에만 형식을 붙인다."""
    suffix = next((tail for tail in (RETRY_REMINDER, MISSING_VERDICTS_REMINDER)
                   if prompt.endswith(tail)), '')
    base = prompt[:-len(suffix)] if suffix else prompt
    value = str(base) + REVIEW_WIRE_GUIDE + suffix
    if isinstance(prompt, PromptMetadata):
        return prompt._with_text(value, cache_prefix_chars=prompt.cache_prefix_chars)
    return value


def with_review_wire(ask: Callable[[str], str]) -> Callable[[str], str]:
    """최초·재요청·누락 후속·재검수에 같은 복원 경계를 적용한다."""
    if getattr(ask, '_review_wire_wrapped', False):
        return ask

    def wrapped(prompt: str) -> str:
        raw = str(ask(review_wire_prompt(prompt)))
        return ReviewWireResponse(restore_review_wire(raw), wire_response_chars=len(raw))

    wrapped._review_wire_wrapped = True
    return wrapped
