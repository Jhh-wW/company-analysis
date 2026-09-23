"""관계법인 표의 회계범위 각주를 같은 문서의 정확한 원문 구간으로 운반한다.

★ 왜 필요한가(2026-09-23 4차 실측) — 감사보고서 주석 「특수관계자 등 거래」
  표에는 한 해외 법인이 「종속기업」으로 분류돼 대여·영업비용 거래가 실려 있다.
  같은 문서의 다른 주석(매도가능증권 표)에는 그 법인 행에 「(*)」가 붙고, 각주가
  「경과규정에 따라 종속기업에서 제외, 지분법 미적용·취득원가 계상」이라고
  회계 적용 범위를 제한한다. legacy 발췌는 절 표제 첫 출현만 떠서 특수관계자
  주석만 작성 입력에 들어갔고, 제한 각주는 어떤 발췌 종류에도 걸리지 않았다.
  그래서 작가·검수는 거래 사실만 보고 회계 범위 단서를 알 수 없었다.

★ 운반 규칙(모두 만족할 때만):
  1) 같은 문서 원문 안에서, 표 행 이름 바로 뒤의 각주 표식과 같은 표식의 정의가
     «같은 주석 구간»(주석 번호 제목 사이)에 있다.
  2) 각주 정의 문장이 회계 적용 범위를 제한한다(연결·지분법·종속기업 제외 등).
     같은 문장에 예정·계획 표현이 있으면 아직 일어나지 않은 제외로 보고 쓰지 않는다.
  3) 행의 법인 이름 토큰 묶음이 이미 운반된 관계자 조각 원문에 «같은 경계로»
     그대로 나온다. 이름 일부(뒤쪽 공통 꼬리)만 겹치는 다른 법인은 묶지 않는다.

★ 운반 방식 — 각주를 다른 조각 원문에 이어 붙이거나 문장을 합성하지 않는다.
  주석 제목부터 그 각주 정의 끝까지의 연속 원문 구간을 별도 조각으로 만들고,
  원문 위치(평문 문자 시작-끝)를 함께 싣는다. 구간은 원문의 부분 문자열이므로
  해시·위치로 원문과 대조할 수 있다. 거래 사실(관계자 조각)은 지우지 않는다.
  주석 전체가 상한을 넘으면 행이 속한 소표 머리(「(2) 전기말 내역」)부터 싣는다.
  법인 행부터 자르면 기간 표제가 빠져 과거 제외가 현재 범위로 읽히므로, 소표
  구간도 상한을 넘거나 기간 표제가 소표 밖에만 있으면 운반을 보류한다.

이 모듈은 AI·네트워크를 쓰지 않는 순수 함수다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from features.evidence_collection import constants as c

_NAME_CHAR_PATTERN = re.compile(r"[A-Za-z가-힣]")
_TOKEN_PATTERN = re.compile(r"\S+")
_WHITESPACE_PATTERN = re.compile(r"\s+")


@dataclass(frozen=True)
class EntityScopeFootnote:
    """운반할 원문 구간 하나. ``text == filing_text[start:end]``가 항상 참이다."""

    entity_label: str
    start: int
    end: int
    text: str


def _compact(text: str) -> str:
    return _WHITESPACE_PATTERN.sub("", text)


def _normalized_token(token: str) -> str:
    return token.strip(c.ENTITY_FOOTNOTE_TOKEN_PUNCTUATION)


def _is_boundary_token(token: str) -> bool:
    """이름이 아닌 토큰(숫자·기호·표 머리말)인지 본다."""

    core = _normalized_token(token)
    if not core or _NAME_CHAR_PATTERN.search(core) is None:
        return True
    return core in c.ENTITY_FOOTNOTE_TABLE_WORDS


def _definition_limits_scope(definition: str) -> bool:
    """각주 정의에 «이미 적용 중인» 회계 범위 제한 문장이 있는지 본다."""

    for sentence in c.ENTITY_FOOTNOTE_SENTENCE_END_PATTERN.split(definition):
        compact = _compact(sentence)
        if not any(marker in compact for marker in c.ENTITY_SCOPE_LIMIT_MARKERS):
            continue
        if any(marker in compact for marker in c.ENTITY_SCOPE_FUTURE_MARKERS):
            continue
        return True
    return False


def _row_label(text: str, mark_start: int, block_start: int) -> tuple[str, int] | None:
    """행 표식 바로 앞의 이름 토큰 묶음과 그 시작 위치를 돌려준다.

    표 머리말·숫자 토큰을 만날 때까지 뒤에서부터 모은다. 창 안에서 경계를 못
    찾거나 이름이 너무 길고 짧으면 이름 시작을 확정할 수 없으므로 ``None``이다.
    """

    window_start = max(block_start, mark_start - c.ENTITY_FOOTNOTE_LABEL_WINDOW_CHARS)
    tokens = list(_TOKEN_PATTERN.finditer(text, window_start, mark_start))
    name_tokens: list[re.Match[str]] = []
    found_boundary = window_start == block_start
    for token in reversed(tokens):
        if _is_boundary_token(token.group()):
            found_boundary = True
            break
        name_tokens.insert(0, token)
        if len(name_tokens) > c.ENTITY_FOOTNOTE_LABEL_MAX_TOKENS:
            return None
    if not name_tokens or not found_boundary:
        return None
    label = " ".join(token.group() for token in name_tokens)
    if len(_compact(label)) < c.ENTITY_FOOTNOTE_LABEL_MIN_CHARS:
        return None
    return label, name_tokens[0].start()


def _anchor_names_label(label: str, anchor_texts: Sequence[str]) -> bool:
    """관계자 조각에 같은 이름이 같은 경계(앞뒤가 이름 아닌 토큰)로 나오는지 본다."""

    wanted = [_normalized_token(token) for token in label.split()]
    size = len(wanted)
    for anchor in anchor_texts:
        tokens = anchor.split()
        for index in range(len(tokens) - size + 1):
            if [_normalized_token(token) for token in tokens[index:index + size]] != wanted:
                continue
            before_ok = index == 0 or _is_boundary_token(tokens[index - 1])
            after_ok = (
                index + size == len(tokens) or _is_boundary_token(tokens[index + size])
            )
            if before_ok and after_ok:
                return True
    return False


def _block_bounds(position: int, heading_starts: Sequence[int], text_length: int) -> tuple[int, int]:
    start = max((heading for heading in heading_starts if heading <= position), default=0)
    end = min((heading for heading in heading_starts if heading > position), default=text_length)
    return start, end


def _last_match_start(pattern: re.Pattern[str], text: str, start: int, end: int) -> int | None:
    return max((match.start() for match in pattern.finditer(text, start, end)), default=None)


def _context_start(
    text: str, block_start: int, label_start: int, definition_end: int, max_chars: int
) -> int | None:
    """행이 속한 표 문맥의 머리(주석 제목 또는 소표 머리) 위치를 돌려준다.

    주석 전체가 상한 안이면 주석 제목부터 시작한다. 넘으면 행 앞 소표 머리 후보를
    가까운 것부터 거슬러 보며, 행 앞 가장 가까운 기간 표제를 구간 안에 담는 첫
    후보에서 시작한다. 괄호 숫자는 음수 금액 「(12)」일 수도 있어 가장 가까운 후보
    하나로 확정하지 않는다. 후보 구간이 상한을 넘으면 더 앞 후보도 넘으므로 거기서
    멈추고 ``None``(운반 보류)이다. 행 이름부터 시작하는 구간은 만들지 않는다.
    """

    if definition_end - block_start <= max_chars:
        return block_start
    period_start = _last_match_start(
        c.ENTITY_FOOTNOTE_PERIOD_LABEL_PATTERN, text, block_start, label_start
    )
    heading_starts = [
        match.start()
        for match in c.ENTITY_FOOTNOTE_SUBTABLE_HEADING_PATTERN.finditer(
            text, block_start, label_start
        )
    ]
    for heading_start in reversed(heading_starts):
        if definition_end - heading_start > max_chars:
            return None
        if period_start is None or period_start >= heading_start:
            return heading_start
    return None


def find_entity_scope_footnotes(
    filing_text: str,
    anchor_texts: Sequence[str],
    *,
    max_chars: int = c.ENTITY_FOOTNOTE_DEFAULT_MAX_CHARS,
) -> tuple[EntityScopeFootnote, ...]:
    """관계자 조각에 나온 법인의 회계범위 각주 원문 구간을 찾는다.

    Args:
        filing_text: 한 공시 문서의 평문 전체(legacy 발췌가 쓴 같은 문자열).
        anchor_texts: 같은 문서에서 이미 작성 입력으로 운반한 관계자 조각 원문.
        max_chars: 운반 구간 글자 상한. 넘으면 그 각주는 운반하지 않는다.

    Returns:
        원문 순서의 중복 없는 구간 목록. 조건이 하나라도 어긋나면 비어 있다.
    """

    if not filing_text or max_chars <= 0:
        return ()
    anchors = tuple(text for text in anchor_texts if text and text.strip())
    if not anchors:
        return ()
    heading_starts = [
        match.start() for match in c.ENTITY_FOOTNOTE_NOTE_HEADING_PATTERN.finditer(filing_text)
    ]
    row_marks = [
        (match.group(1), match.start())
        for match in c.ENTITY_FOOTNOTE_ROW_MARK_PATTERN.finditer(filing_text)
    ]
    definitions = [
        (match.group(1), match.start())
        for match in c.ENTITY_FOOTNOTE_DEFINITION_PATTERN.finditer(filing_text)
    ]

    found: dict[tuple[int, int], EntityScopeFootnote] = {}
    for position, (mark, definition_start) in enumerate(definitions):
        block_start, block_end = _block_bounds(
            definition_start, heading_starts, len(filing_text)
        )
        next_definition = next(
            (start for _mark, start in definitions[position + 1:] if start > definition_start),
            len(filing_text),
        )
        definition_end = min(next_definition, block_end, definition_start + max_chars)
        if not _definition_limits_scope(filing_text[definition_start:definition_end]):
            continue
        for row_mark, row_start in row_marks:
            if row_mark != mark or not block_start <= row_start < definition_start:
                continue
            # 같은 주석 안에서 같은 표식이 재사용되면(소표·기간별) 행은 가장
            # 가까운 뒤쪽 정의에만 귀속한다. 중간에 같은 표식의 정의가 있으면
            # 이 행은 그 정의 소속이므로 현재 정의로 끌고 오지 않는다.
            if any(
                d_mark == mark and row_start < d_start < definition_start
                for d_mark, d_start in definitions
            ):
                continue
            labelled = _row_label(filing_text, row_start, block_start)
            if labelled is None:
                continue
            label, label_start = labelled
            if not _anchor_names_label(label, anchors):
                continue
            span_start = _context_start(
                filing_text, block_start, label_start, definition_end, max_chars
            )
            if span_start is None:
                continue
            raw = filing_text[span_start:definition_end]
            start = span_start + (len(raw) - len(raw.lstrip()))
            end = definition_end - (len(raw) - len(raw.rstrip()))
            if start >= end:
                continue
            found.setdefault(
                (start, end),
                EntityScopeFootnote(
                    entity_label=label,
                    start=start,
                    end=end,
                    text=filing_text[start:end],
                ),
            )
    return tuple(found[key] for key in sorted(found))


def add_entity_scope_footnotes(
    frags: Mapping[int, Mapping[str, str]],
    filing_text: str,
    *,
    kind: str,
    anchor_kinds: Iterable[str],
    max_chars: int = c.ENTITY_FOOTNOTE_DEFAULT_MAX_CHARS,
) -> tuple[dict[int, dict[str, str]], int]:
    """legacy 조각 묶음에 관계법인 회계범위 각주 조각을 더한다.

    기존 조각은 한 글자도 바꾸지 않고 새 번호로만 더한다. 이미 같은 원문을
    담은 조각이 있으면 더하지 않는다. 종류 이름은 호출자(app 정본)가 넘긴다 —
    엔진은 app 어휘를 import하지 않는다.

    Returns:
        (새 조각 묶음, 더한 조각 수).
    """

    result = {int(number): dict(fragment) for number, fragment in frags.items()}
    if type(kind) is not str or not kind.strip():
        return result, 0
    wanted_kinds = frozenset(anchor_kinds)
    anchors = [
        str(fragment.get("원문") or "")
        for fragment in result.values()
        if fragment.get("종류") in wanted_kinds
    ]
    footnotes = find_entity_scope_footnotes(filing_text, anchors, max_chars=max_chars)
    existing = [_compact(str(fragment.get("원문") or "")) for fragment in result.values()]
    next_number = max(result, default=0) + 1
    added = 0
    for footnote in footnotes:
        if added >= c.ENTITY_SCOPE_FOOTNOTE_MAX_FRAGMENTS:
            break
        compact = _compact(footnote.text)
        if any(compact in text for text in existing):
            continue
        result[next_number] = {
            "종류": kind,
            "원문": footnote.text,
            "원문위치": c.ENTITY_FOOTNOTE_LOCATION_TEMPLATE.format(
                start=footnote.start, end=footnote.end
            ),
        }
        existing.append(compact)
        next_number += 1
        added += 1
    return result, added
