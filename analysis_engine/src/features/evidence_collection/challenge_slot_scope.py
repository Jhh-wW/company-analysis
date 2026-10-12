"""날짜·주체·사건이 같은 표 행에 결속될 때만 5장 사건 근거로 채점한다."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as calendar_date
import re
import unicodedata

from features.evidence_collection import challenge_slot_constants as c
from features.evidence_collection.challenge_eligibility import challenge_incident_row_problem


def _surface(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


def _index(cells: tuple[str, ...], headers: tuple[str, ...]) -> int | None:
    return next((index for index, cell in enumerate(cells) if cell in headers), None)


def _valid_date(text: str) -> bool:
    if not c.EVENT_DATE_RE.fullmatch(text):
        return False
    try:
        calendar_date(*(int(part) for part in re.findall(r"\d+", text)))
    except (ValueError, TypeError):
        return False
    return True


@dataclass(frozen=True)
class ChallengeTableScope:
    issue_text: str
    has_incident_row: bool
    excluded_rows: int
    has_response_row: bool = False


def challenge_table_scope(text: str) -> ChallengeTableScope:
    """머리말·다른 행의 날짜를 사건 행에 빌리지 않는다. 원문은 수정하지 않는다."""
    kept: list[str] = []
    mapping: tuple[int, int, int, int | None, bool, int, int | None] | None = None
    incident_header_seen = False
    found = False
    response_found = False
    excluded = 0
    for row in c.ROW_SEPARATOR_RE.split(text):
        raw_cells = tuple(cell.strip() for cell in row.split("|"))
        cells = tuple(_surface(cell) for cell in raw_cells)
        actor = _index(cells, c.ACTOR_HEADERS)
        date = _index(cells, c.DATE_HEADERS)
        if actor is not None and date is not None:
            incident_header_seen = True
        elif incident_header_seen and sum(cell in c.OTHER_TABLE_HEADERS for cell in cells) >= c.MIN_OTHER_HEADER_CELLS:
            mapping = None
            incident_header_seen = False
            kept.append(row)
            continue
        if not c.MIN_TABLE_CELLS <= len(cells) <= c.MAX_TABLE_CELLS:
            if incident_header_seen and "|" in row:
                excluded += 1
                continue
            mapping = None
            incident_header_seen = False
            kept.append(row)
            continue
        accident = _index(cells, c.ACCIDENT_HEADERS)
        sanction = _index(cells, c.SANCTION_HEADERS)
        if actor is not None and date is not None:
            event = accident if accident is not None else sanction
            mapping = ((actor, date, event, _index(cells, c.PLACE_HEADERS), accident is not None,
                        len(cells), next((index for index, cell in enumerate(cells)
                                          if cell in c.RESPONSE_HEADERS and index != event), None))
                       if event is not None else None)
            excluded += 1
            continue
        if mapping is None:
            if incident_header_seen:
                excluded += 1
                continue
            kept.append(row)
            continue
        actor, date, event, place, is_accident, column_count, response = mapping
        if len(cells) != column_count:
            excluded += 1
            continue
        required = (actor, date, event) + ((place,) if place is not None else ())
        if max(required) >= len(cells):
            excluded += 1
            continue
        actor_text = cells[actor]
        event_text = cells[event]
        actor_valid = bool(actor_text not in c.UNKNOWN_ACTORS and not actor_text.isdecimal()
                           and actor_text not in c.ACTOR_HEADERS)
        date_valid = _valid_date(cells[date])
        event_valid = bool((c.ACCIDENT_RE if is_accident else c.SANCTION_RE).search(event_text)
                           and not c.HYPOTHETICAL_RE.search(event_text))
        place_valid = not is_accident or bool(place is not None and cells[place] not in ("", "-"))
        if actor_valid and date_valid and event_valid and place_valid:
            if challenge_incident_row_problem(row):
                excluded += 1
                continue
            found = True
            response_found |= bool(response is not None and response < len(cells)
                                   and c.RESPONSE_ACTION_RE.search(cells[response]))
            kept.append(row)
        else:
            excluded += 1
    return ChallengeTableScope(" ; ".join(kept) if excluded else text, found, excluded, response_found)
