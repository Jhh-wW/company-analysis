"""FULL 5장 작성자가 같은 사건 행의 주체·시점·조치를 함께 보게 한다."""
from collections import deque
from collections.abc import Mapping, Sequence
from datetime import date
import hashlib
import json

from src.features.composer.challenge_event_scope import _event_rows, _days, _surface
from src.features.composer import challenge_event_constants as ec
from src.features.composer import challenge_event_writer_constants as c
from src.features.composer.port import CollectedFragment


def _hint(fragment: CollectedFragment, row, pairs: Mapping) -> dict | None:
    selected = [{'id': key, '의미칸': slot} for key, (slot, fid) in pairs.items()
                if fid == fragment.fragment_id and slot in c.EVENT_WRITER_SLOT_IDS]
    if (not selected or not row.columns_unambiguous or not row.event
            or _surface(row.actor) in ec.UNKNOWN_EVENT_ACTORS | c.EVENT_WRITER_UNKNOWN_ACTORS
            or _surface(row.actor).isdigit()
            or len(row.raw_row) > c.EVENT_WRITER_MAX_ROW_CHARS
            or fragment.text[row.start:row.end] != row.raw_row):
        return None
    days = _days(row.date)
    if len(days) != 1:
        return None
    try:
        date(*next(iter(days)))
    except ValueError:
        return None
    return {'조각번호': fragment.fragment_id, '선택가능지원쌍': selected,
            '주체': row.actor, '원문날짜': row.date, '사건': row.event,
            '문장시작보기': row.actor + '는 ' + row.date + '…',
            '원문단독숫자셀': [cell.strip() for cell in row.raw_row.split('|') if cell.strip().isdigit()],
            '조치와상태원문': row.response, '행원문': row.raw_row,
            '조각내행범위': [row.start, row.end],
            '행원문SHA256': hashlib.sha256(row.raw_row.encode()).hexdigest()}


def render_challenge_event_writer_hints(fragments: Sequence[CollectedFragment], pairs: Mapping,
                                        *, allowed_fragment_ids: frozenset[str] | None = None) -> str:
    """허용된 조각만 읽고 자료별 순환으로 보조 입력 예산을 배분한다. 원문은 변경하지 않는다."""
    buckets = []
    skipped = False
    for fragment in fragments:
        if allowed_fragment_ids is not None and fragment.fragment_id not in allowed_fragment_ids:
            continue
        hints = []
        for row in _event_rows(fragment.text):
            hint = _hint(fragment, row, pairs)
            if hint is None:
                skipped = True
            else:
                hints.append(json.dumps(hint, ensure_ascii=False, separators=(',', ':')) + '\n')
        if hints:
            buckets.append(deque(hints))
    if not buckets:
        return ''
    lines = []
    # 생략 안내가 붙어도 총 문자 상한을 넘지 않게 공간을 먼저 확보한다.
    available = c.EVENT_WRITER_MAX_CHARS - len(c.EVENT_WRITER_HEADER + c.EVENT_WRITER_GUIDE + c.EVENT_WRITER_OMISSION)
    while buckets and len(lines) < c.EVENT_WRITER_MAX_ROWS:
        remaining = []
        for bucket in buckets:
            item = bucket.popleft()
            if len(lines) >= c.EVENT_WRITER_MAX_ROWS or len(item) > available:
                skipped = True
            else:
                lines.append(item)
                available -= len(item)
            if bucket:
                remaining.append(bucket)
        buckets = remaining
    skipped = skipped or bool(buckets)
    if not lines:
        return ''
    return c.EVENT_WRITER_HEADER + c.EVENT_WRITER_GUIDE + ''.join(lines) + (c.EVENT_WRITER_OMISSION if skipped else '')
