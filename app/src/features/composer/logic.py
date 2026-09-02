"""composer 본체 — 작가 AI가 장 전체를 산문으로 쓴다 (엔진 v2 소단계 3-1).

★ v1과의 근본 차이(기준문서 3절): 원문 문장을 추출해 나열하지 않고,
  수집 조각 «전체» + 실적표를 주고 장 하나를 통째로 쓰게 한다.
  모든 문장은 인용(조각 id)과 등급(확인/해석)을 달고 나온다.
★ 이 파일은 «쓰기»만 한다. 거짓을 막는 검증(출처 실존·수치·의미 검수)은
  소단계 3-2의 verify.py 몫이다.
★ AI 호출은 `ask` 주입 함수로만 한다 (writer/logic.py와 같은 패턴) —
  시험에서 가짜 함수를 끼우기 위해서다. 여기서 직접 provider를 부르지 않는다.
★ 어떤 장이 실패해도 예외를 밖으로 던지지 않는다 — 장 삭제·전체 중단 금지.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Callable, Final, Optional, Union

from src.core.citations import citation_number
from src.features.composer.constants import (
    ALREADY_WRITTEN_GUIDE,
    ALREADY_WRITTEN_HEAD,
    ALREADY_WRITTEN_MAX_SENTENCES,
    CITATION_RULES_GUIDE,
    CLAIM_SLOTS_BY_SECTION,
    FORBIDDEN_TOPICS_GUIDE,
    GRADE_CONFIRMED,
    GRADE_INTERPRETED,
    JSON_SCHEMA_GUIDE,
    NOTICE_COMPOSE_FAILED,
    NOTICE_INSUFFICIENT_EVIDENCE,
    FLOW_HEADERS_BY_SECTION,
    FLOW_PROMPT_BY_SECTION,
    OPERATIONS_FLOW_GUIDE,
    OPERATIONS_FLOW_HEADERS,
    OPERATIONS_FLOW_MAX_CELL_CHARS,
    OPERATIONS_FLOW_MAX_ROWS,
    OPERATIONS_FLOW_SCHEMA_GUIDE,
    OPERATIONS_FLOW_SECTION_ID,
    PARSE_RETRY_LIMIT,
    PROMPT_FRAGMENTS_HEAD,
    PROMPT_HEADER,
    PROMPT_TABLE_HEAD,
    RESPONSE_CITATIONS_KEY,
    RESPONSE_FLOW_KEY,
    RESPONSE_FLOW_ROW_CELLS_KEY,
    RESPONSE_FLOW_ROW_CITATIONS_KEY,
    RESPONSE_GRADE_KEY,
    RESPONSE_CLAIM_SLOT_KEY,
    RESPONSE_SENTENCES_KEY,
    RESPONSE_TEXT_KEY,
    RETRY_REMINDER,
    SECTION_GUIDES,
    SECTION_IDS,
    SECTION_TITLES,
    SECTION_SENTENCE_RANGES,
    MAX_INTERPRETED_SENTENCES_PER_SECTION,
    SENTENCE_RANGE_GUIDE,
    VALID_GRADES,
)
from src.features.composer.port import (
    AskFatalError,
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FlowRow,
    PerformanceTable,
    SectionEvidencePacketSet,
    fragments_from_raw,
)

logger = logging.getLogger(__name__)

#: 프롬프트 문자열을 받아 AI 응답 문자열을 돌려주는 주입 함수
AskFn = Callable[[str], str]

#: compose_sections가 받는 조각 입력 — 파이프라인 원시 dict 또는 어댑터 튜플
FragmentsInput = Union[
    Mapping[int, Mapping[str, Any]], Sequence[CollectedFragment]
]

#: 장별 근거 묶음 입력. 키는 SECTION_IDS 아홉 개와 정확히 같아야 한다.
#: 값은 기존 flat fragments와 같은 두 입력 모양을 그대로 재사용한다.
SectionEvidencePackets = Union[
    Mapping[str, FragmentsInput], SectionEvidencePacketSet
]

#: 실서비스 raw fragments의 dict[int, ...] 계약과 같은 공개 조각 번호.
_CANONICAL_FRAGMENT_ID_RE: Final[re.Pattern[str]] = re.compile(r"[1-9][0-9]*")


@dataclass(frozen=True)
class _PreparedSectionEvidencePackets:
    """검증을 끝낸 장별 근거 계약과 기존 검증기용 합집합."""

    packets: Mapping[str, tuple[CollectedFragment, ...]]
    allowed_fragment_ids_by_section: Mapping[str, frozenset[str]]
    flat_union: tuple[CollectedFragment, ...]
    company_id: str = ""
    evidence_generation_sha256: str = ""
    packet_sha256s: tuple[tuple[str, str], ...] = ()


_NOTICE_OUTSIDE_PACKET_CITATIONS: Final[str] = (
    "이 장에 배정되지 않은 근거를 사용한 문장을 제외했습니다."
)


# ══════════════════════════════════════════════════════════
# 프롬프트 만들기
# ══════════════════════════════════════════════════════════


def _render_fragments(fragments: Sequence[CollectedFragment]) -> str:
    """조각 전체를 id·종류와 함께 나열한다 — 작가가 이 id로 인용한다."""
    lines: list[str] = [PROMPT_FRAGMENTS_HEAD]
    for fragment in fragments:
        label = fragment.kind or "자료"
        if fragment.document_title:
            label = f"{label}·{fragment.document_title}"
        lines.append(f"[조각 {fragment.fragment_id}] ({label}) {fragment.text}\n")
    return "".join(lines)


def _render_table(table: Optional[PerformanceTable]) -> str:
    """실적표를 글자 표로 편다. 없으면 빈 문자열 — 표 없는 회사도 있다."""
    if table is None or not table.rows:
        return ""
    caption = table.caption
    if table.unit:
        caption = f"{caption} (단위: {table.unit})"
    lines = [PROMPT_TABLE_HEAD, f"{caption}\n"]
    if table.headers:
        lines.append(" | ".join(table.headers) + "\n")
    for row in table.rows:
        lines.append(" | ".join(row) + "\n")
    return "".join(lines)


def _render_already_written(already_written: Sequence[str]) -> str:
    """앞 장이 이미 쓴 문장 목록 — 같은 사실 재탕을 막기 위한 지침 블록.

    ★ 게이트가 아니다. 문장을 지우지 않고 «보여 주고 알려 줄» 뿐이다.
      비어 있으면(첫 장) 블록 자체를 넣지 않아 프롬프트를 늘리지 않는다.
    """
    kept = [text.strip() for text in already_written if text and text.strip()]
    if not kept:
        return ""
    kept = kept[:ALREADY_WRITTEN_MAX_SENTENCES]
    lines = "".join(f"- {text}\n" for text in kept)
    return f"{ALREADY_WRITTEN_HEAD}{lines}{ALREADY_WRITTEN_GUIDE}"


def build_section_prompt(
    company_name: str,
    section_id: str,
    fragments: Sequence[CollectedFragment],
    performance_table: Optional[PerformanceTable],
    already_written: Sequence[str] = (),
) -> str:
    """장 하나를 쓰게 하는 지시문 — 지침 + 조각 전체 + 실적표 + JSON 강제.

    Args:
        already_written: 앞 장들이 이미 쓴 문장. 같은 사실을 다시 쓰지 않도록
            보여 준다. 비어 있으면 블록을 넣지 않는다 (첫 장).
    """
    minimum, maximum = SECTION_SENTENCE_RANGES[section_id]
    claim_slots = CLAIM_SLOTS_BY_SECTION.get(section_id, ())
    claim_slot_guide = (
        "\n원자 주장 계획 — 각 문장은 가장 알맞은 id를 «주장슬롯»에 넣고, "
        "id는 고유 번호가 아니라 사실의 종류다. 같은 종류의 서로 다른 원자 "
        "사실에는 같은 id를 다시 써도 되지만, 같은 사실을 말만 바꿔 반복하지 "
        "않는다. 어느 자리에도 맞지 않으면 빈 문자열로 두며 새 id를 만들지 "
        "않는다:\n- " + "\n- ".join(claim_slots) + "\n"
        if claim_slots
        else ""
    )
    parts = [
        PROMPT_HEADER.format(company=company_name),
        "\n",
        SECTION_GUIDES[section_id],
        "\n\n",
        CITATION_RULES_GUIDE,
        FORBIDDEN_TOPICS_GUIDE,
        SENTENCE_RANGE_GUIDE.format(
            minimum=minimum,
            maximum=maximum,
            interpretation_cap=MAX_INTERPRETED_SENTENCES_PER_SECTION,
        ),
        claim_slot_guide,
        # 7장은 «경로표»를 함께 내야 해서 스키마 안내를 통째로 바꾼다.
        # 덧붙이면 기본 안내의 「이 JSON만 출력한다」와 충돌해 작가가 경로표를
        # 빼먹는다 (진영 실측).
        # 흐름표를 내는 장(5장 대응표·7장 경로표)은 스키마 안내를 통째로 «바꾼다».
        # 덧붙이면 기본 안내의 「이 JSON만 출력한다」와 충돌해 작가가 표를
        # 빼먹는다 (진영 실측).
        FLOW_PROMPT_BY_SECTION.get(section_id, JSON_SCHEMA_GUIDE),
        _render_table(performance_table),
        _render_already_written(already_written),
        _render_fragments(fragments),
    ]
    return "".join(parts)


# ══════════════════════════════════════════════════════════
# 응답 파싱
# ══════════════════════════════════════════════════════════


#: 문장 «글» 안에 흉내낸 인용 표기 — [숫자]·[인용: …]·[조각 …].
#: ★ 정식 인용은 citations 배열이 유일한 정본이고, 부록(render.py)도 그
#:   배열에서만 만들어진다. 작가 프롬프트가 자료를 「[조각 n]」·「[인용: 1, 2]」
#:   모양으로 보여주므로 모델이 산문 속에도 같은 모양을 흉내 내는 사고가
#:   실재한다(critical 결함 — validate.py의 인용-부록 1:1 검사가 이 숫자를
#:   진짜 인용으로 오인해 유료 실행 전체를 GATE_STOPPED로 죽인다). 이건 내용
#:   검열 게이트가 아니라 출력 «형식» 정리다 — 값을 판단하지 않고 모양만 본다.
_INLINE_CITATION_MARKER_RE: Final[re.Pattern[str]] = re.compile(
    r"\[(?:\d+|인용\s*:[^\]]*|조각[^\]]*)\]"
)

#: packet 모드의 구조 citations 우회를 찾는 더 넓은 규칙. legacy flat 응답은
#: 위 정리 규칙을 그대로 써야 하므로 별도 정규식으로 둔다.
_PACKET_INLINE_CITATION_MARKER_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:\[\s*(?:\d{1,3}(?:\s*[,，、]\s*\d{1,3})*|인용\s*[:：][^\]]*|조각[^\]]*)\s*\]"
    r"|[〔【〖〚]\s*(?:\d{1,3}(?:\s*[,，、]\s*\d{1,3})*|인용\s*[:：][^〕】〗〛]*|조각[^〕】〗〛]*)"
    r"\s*[〕】〗〛])"
)


def contains_inline_citation_marker(text: str) -> bool:
    """NFKC 뒤 모든 Ps/Pe 혼합 괄호의 1~3자리 인용 흉내를 찾는다.

    괄호 쌍의 종류를 닫힌 목록으로 두지 않는다. 여는 문자가 Unicode Ps이고
    닫는 문자가 Pe이면 서로 다른 괄호를 섞은 경우도 막는다. 네 자리 숫자는
    연도일 수 있으므로 이 일반 규칙에서는 의도적으로 제외한다.
    """

    normalized = unicodedata.normalize("NFKC", str(text or ""))
    if _PACKET_INLINE_CITATION_MARKER_RE.search(normalized) is not None:
        return True
    for index, char in enumerate(normalized):
        if unicodedata.category(char) != "Ps":
            continue
        cursor = index + 1
        parsed_group = False
        while cursor < len(normalized):
            while cursor < len(normalized) and normalized[cursor].isspace():
                cursor += 1
            start = cursor
            while cursor < len(normalized) and normalized[cursor].isdecimal():
                cursor += 1
            if not 1 <= cursor - start <= 3:
                break
            parsed_group = True
            while cursor < len(normalized) and normalized[cursor].isspace():
                cursor += 1
            if (
                cursor < len(normalized)
                and unicodedata.category(normalized[cursor]) == "Pe"
            ):
                return parsed_group
            if cursor >= len(normalized) or normalized[cursor] not in ",，、":
                break
            cursor += 1
    return False


def _strip_inline_citation_markers(text: str) -> str:
    """legacy 문장에서 흉내낸 인용 표기를 걷어내고 공백을 정리한다."""
    cleaned = _INLINE_CITATION_MARKER_RE.sub(" ", text)
    return " ".join(cleaned.split())


def extract_json_payload(raw: str) -> Optional[Any]:
    """응답 문자열에서 JSON을 꺼낸다. 코드 펜스·앞뒤 설명이 붙어도 살린다.

    ★ 내용 검사가 아니다 — 「JSON으로 읽히는가」만 본다.

    ★ 왜 «공개» 함수인가 (3-strikes) — 같은 규칙이 composer 안에
      **세 벌** 있었다: 여기, `verify.py`, `diagram_check.py`. 앞의 둘은 글자까지
      같았고, `diagram_check` 쪽만 «첫 `{`부터 마지막 `}`까지 자르기»만 하고
      맨 앞의 `json.loads` 시도를 빼먹은 채였다. 그래서 한 곳을 고쳐도 나머지
      둘은 그대로였다.
      전에는 「logic의 «비공개» 함수에 묶이지 않으려고」 일부러 복사했는데,
      `verify.py`가 이미 `_strip_inline_citation_markers`(역시 비공개)를
      import 하고 있어 그 이유가 실제로는 지켜지지 않고 있었다.
      → 숨기는 대신 «계약이 있는 공개 함수»로 올려 한 벌로 만든다.

    ★ 맨 앞의 `json.loads(text)` 시도를 빼면 안 되는 이유 —
      응답이 최상위 «배열»(`[{...}]`)일 때, 자르기만 하면 배열 «안»의 객체
      하나가 잘려 나와 Mapping으로 읽힌다. 부르는 쪽은 그것을 정상 응답으로
      착각한다. 통째로 먼저 읽어 보면 배열은 배열로 나오고, 부르는 쪽의
      `isinstance(payload, Mapping)` 검사가 정상적으로 걸러 낸다.

    Args:
        raw: AI 응답 원문. None·빈 문자열도 받는다.

    Returns:
        JSON으로 읽힌 값(보통 dict). 못 읽으면 None.
    """
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    # 코드 펜스(```json … ```)나 머리말이 붙은 경우: 첫 «{»부터 마지막 «}»까지만 다시 시도
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None


def _sentence_from_item(
    item: Any,
    section_id: str = "",
    *,
    reject_inline_citation_markers: bool = False,
) -> Optional[ComposedSentence]:
    """항목 하나를 문장으로 바꾼다. 계약(글·인용·등급)이 안 맞으면 None.

    ★ 여기서 보는 것은 «형식»뿐이다 — 글이 비었는가, 등급이 계약된 두 값인가,
      인용이 배열인가. 문장 내용은 일절 검사하지 않는다(닫힌 게이트 금지).
      단, 글 속에 흉내낸 인용 대괄호([n]·[인용: …]·[조각 …])는 형식 정리로
      걷어낸다 — 정식 인용은 citations 배열로만 표시된다.
    """
    if not isinstance(item, Mapping):
        return None
    text = str(item.get(RESPONSE_TEXT_KEY) or "").strip()
    if not text:
        return None
    if reject_inline_citation_markers and contains_inline_citation_marker(text):
        return None
    text = _strip_inline_citation_markers(text)
    if not text:
        return None
    grade = str(item.get(RESPONSE_GRADE_KEY) or "").strip()
    if grade not in VALID_GRADES:
        return None
    raw_citations = item.get(RESPONSE_CITATIONS_KEY)
    if raw_citations is None:
        raw_citations = []
    if not isinstance(raw_citations, (list, tuple)):
        return None
    citations = tuple(
        str(value).strip() for value in raw_citations if str(value).strip()
    )
    raw_claim_slot = str(item.get(RESPONSE_CLAIM_SLOT_KEY) or "").strip()
    allowed_claim_slots = CLAIM_SLOTS_BY_SECTION.get(section_id, ())
    planned_claim_slot = (
        raw_claim_slot if raw_claim_slot in allowed_claim_slots else ""
    )
    return ComposedSentence(
        text=text,
        citations=citations,
        grade=grade,
        planned_claim_slot=planned_claim_slot,
        verification_state="unverified",
    )


def parse_section_response(
    raw: str,
    section_id: str = "",
    *,
    reject_inline_citation_markers: bool = False,
) -> Optional[tuple[ComposedSentence, ...]]:
    """작가 응답을 문장 튜플로 바꾼다.

    Returns:
        - 문장 튜플: 정상 (형식이 깨진 개별 항목은 건너뛴다)
        - 빈 튜플: 작가가 «쓸 문장이 없다»고 정상적으로 답한 경우
        - None: 응답 자체를 읽지 못한 경우 → 호출한 쪽이 1회 재요청한다

    ★ 인용 id는 여기서 «보존만» 한다. 실존하지 않는 id의 처분(문장 제거)은
      소단계 3-2 검증기의 몫이다.
    """
    payload = extract_json_payload(raw)
    if not isinstance(payload, Mapping):
        return None
    items = payload.get(RESPONSE_SENTENCES_KEY)
    if not isinstance(items, list):
        return None
    if not items:
        return ()
    inline_rejected = reject_inline_citation_markers and any(
        isinstance(item, Mapping)
        and contains_inline_citation_marker(
            str(item.get(RESPONSE_TEXT_KEY) or "")
        )
        for item in items
    )
    sentences = tuple(
        sentence
        for sentence in (
            _sentence_from_item(
                item,
                section_id,
                reject_inline_citation_markers=reject_inline_citation_markers,
            )
            for item in items
        )
        if sentence is not None
    )
    # 항목이 있었는데 하나도 못 살렸다면 응답 형식이 통째로 어긋난 것 — 재요청 대상
    if not sentences:
        # packet 모드의 inline 표기는 형식이 아니라 해당 항목만의 위반이다.
        # 모두 위반이어도 재호출하지 않고 빈 결과로 확정한다.
        return () if inline_rejected else None
    return sentences


# ══════════════════════════════════════════════════════════
# 장 단위 생성
# ══════════════════════════════════════════════════════════


def _flow_row_from_item(
    item: Any,
    cell_count: int,
    *,
    reject_inline_citation_markers: bool = False,
) -> Optional[FlowRow]:
    """경로표 한 줄을 계약대로 읽는다. 모양이 어긋나면 그 줄만 버린다.

    ★ «모양»만 본다 — 칸 개수, 빈 칸 여부, 길이, 근거 유무. 내용이 좋은지
      나쁜지는 판단하지 않는다(닫힌 목록 게이트 금지).
    ★ 칸 길이를 제한하는 이유: 칸은 이름·짧은 구를 담는 자리다. 길어지면
      주장이 표 안으로 숨어 문장 검증을 피해 간다.
    """
    if not isinstance(item, Mapping):
        return None
    cells_raw = item.get(RESPONSE_FLOW_ROW_CELLS_KEY)
    if not isinstance(cells_raw, list):
        return None
    cells = tuple(" ".join(str(cell).split()) for cell in cells_raw)
    if reject_inline_citation_markers and any(
        contains_inline_citation_marker(cell) for cell in cells
    ):
        return None
    if len(cells) != cell_count:
        return None
    # ★ 빈 칸을 허용한다 (사용자 결정). 예전에는 한 칸이라도
    #   비면 줄을 버렸는데, 8장 「확인된 사례」처럼 «없을 수 있는» 칸 때문에
    #   쓸 만한 줄이 통째로 사라졌다. 다만 «전부» 빈 줄은 아무 말도 하지
    #   않으므로 그때만 버린다.
    if not any(cell for cell in cells):
        return None
    # ★ 칸 글자 수 상한을 없앴다 (사용자 결정). 24자는 너무 빡빡했다 —
    #   「글로벌 사업 확대에 따른 환율변동위험」이 이미 19자다.
    #   상한을 두었던 본래 이유는 「긴 주장이 표 안으로 숨어 문장 검증을
    #   피해 간다」였는데, 그 사이 도식 검증(diagram_check)이 생겨 표의
    #   칸도 숫자 근거·의미 검수를 받는다. 그래서 지금은 상한이 없어도
    #   검증을 피해 갈 수 없다. 길이는 프롬프트로만 «부탁»한다.
    citations_raw = item.get(RESPONSE_FLOW_ROW_CITATIONS_KEY)
    citations = tuple(
        str(value).strip()
        for value in (citations_raw if isinstance(citations_raw, list) else ())
        if str(value).strip()
    )
    if not citations:
        # 근거 없는 경로는 싣지 않는다 — 도식은 본문보다 눈에 먼저 들어온다.
        return None
    return FlowRow(cells=cells, citations=citations)


def parse_flow_rows(
    raw: str,
    section_id: str = OPERATIONS_FLOW_SECTION_ID,
    *,
    reject_inline_citation_markers: bool = False,
) -> tuple[FlowRow, ...]:
    """작가 응답에서 흐름표를 읽는다. 없거나 못 읽으면 빈 튜플(도식 없음).

    ★ 장마다 칸 수가 다르다 — 7장은 3칸(투입→하는 일→도달), 5장은 2칸
      (과제→대응). 칸 수는 FLOW_HEADERS_BY_SECTION 한 곳에서만 정한다.
    """
    headers = FLOW_HEADERS_BY_SECTION.get(section_id)
    if headers is None:
        return ()
    payload = extract_json_payload(raw)
    if not isinstance(payload, Mapping):
        return ()
    items = payload.get(RESPONSE_FLOW_KEY)
    if not isinstance(items, list):
        return ()
    rows = tuple(
        row
        for row in (
            _flow_row_from_item(
                item,
                len(headers),
                reject_inline_citation_markers=reject_inline_citation_markers,
            )
            for item in items
        )
        if row is not None
    )
    return rows[:OPERATIONS_FLOW_MAX_ROWS]


def _ask_and_parse(
    ask: AskFn,
    prompt: str,
    section_id: str = "",
    *,
    reject_inline_citation_markers: bool = False,
) -> tuple[Optional[tuple[ComposedSentence, ...]], str]:
    """AI를 부르고 파싱까지. 호출 자체가 죽어도 None으로 삼킨다(전체 중단 금지).

    Returns:
        (문장 튜플 또는 None, 응답 원문). 원문을 함께 돌려주는 이유는 7장
        경로표가 «같은 응답»에 실려 오기 때문이다 — 표를 따로 받으려고 AI를
        한 번 더 부르지 않는다.

    ★ 예외다: AskFatalError(예산 소진·billing-uncertain 같은 «요청 전역»
      장애)는 삼키지 않고 그대로 재전파한다 — 문장 하나의 실패로 위장하면
      real.py의 FAILED 처리 대신 v2 출고 검증 실패로 오표기된다.
    """
    try:
        raw = ask(prompt)
    except AskFatalError:
        raise
    except Exception as error:  # noqa: BLE001 - 한 장의 실패가 보고서 전체를 멈추면 안 된다
        # ★ 여기가 «1차 원인»을 통째로 삼켰다. 서버 로그에는
        #   2차 증상(ProviderBudgetUnavailable)만 남아 원인을 못 찾았다.
        #   ⚠️ 예외 «메시지»는 남기지 않는다 — provider 응답 본문이 섞일 수 있다.
        #     클래스 이름과 어느 장인지만 남긴다.
        logger.warning(
            "장 작성 실패(삼킴) section=%s kind=%s",
            section_id,
            type(error).__name__,
        )
        return None, ""
    text = str(raw)
    return (
        parse_section_response(
            text,
            section_id,
            reject_inline_citation_markers=reject_inline_citation_markers,
        ),
        text,
    )


def _compose_one_section(
    section_id: str,
    prompt: str,
    ask: AskFn,
    *,
    reject_inline_citation_markers: bool = False,
    parse_retry_limit: int = PARSE_RETRY_LIMIT,
) -> ComposedSection:
    """장 하나를 쓴다. 실패 시 재요청 1회, 그래도 실패면 정직한 안내문으로 남긴다."""
    sentences, raw = _ask_and_parse(
        ask,
        prompt,
        section_id,
        reject_inline_citation_markers=reject_inline_citation_markers,
    )
    retries = 0
    while sentences is None and retries < parse_retry_limit:
        retries += 1
        sentences, raw = _ask_and_parse(
            ask,
            prompt + RETRY_REMINDER,
            section_id,
            reject_inline_citation_markers=reject_inline_citation_markers,
        )
    # 흐름표는 정해진 장(5장 대응표·7장 경로표)에서만 읽는다.
    # 같은 응답에서 꺼내므로 추가 AI 호출이 «0회»다.
    wants_flow = section_id in FLOW_HEADERS_BY_SECTION
    flow_rows = (
        parse_flow_rows(
            raw,
            section_id,
            reject_inline_citation_markers=reject_inline_citation_markers,
        )
        if wants_flow and raw
        else ()
    )
    if wants_flow and not flow_rows:
        # ★ 진단 — 도식이 안 나올 때 «작가가 안 냈는지» «우리가 걸렀는지»를
        #   구분하지 못하면 엉뚱한 데를 고치게 된다(실측에서 두 번 헛짚었다).
        #   원문은 남기지 않는다 — 어느 쪽인지만 기록한다.
        logger.warning(
            "%s 흐름표 없음 — 응답에 «경로표» 키 %s / 응답 길이 %d자",
            section_id,
            "있었으나 쓸 줄이 없음" if RESPONSE_FLOW_KEY in (raw or "") else "아예 없음",
            len(raw or ""),
        )
    if sentences is None:
        # 생성 실패 — 자료 부재로 위장하지 않는다 («없다»와 «못 만들었다»는 다르다)
        return ComposedSection(
            section_id=section_id, sentences=(), notice=NOTICE_COMPOSE_FAILED
        )
    if not sentences:
        return ComposedSection(
            section_id=section_id,
            sentences=(),
            notice=NOTICE_INSUFFICIENT_EVIDENCE,
            flow_rows=flow_rows,
        )
    return ComposedSection(
        section_id=section_id,
        sentences=sentences,
        notice="",
        flow_rows=flow_rows,
    )


def _normalize_fragments(
    fragments: FragmentsInput,
) -> tuple[CollectedFragment, ...]:
    """원시 dict든 어댑터 튜플이든 같은 모양으로 맞춘다."""
    if isinstance(fragments, Mapping):
        return fragments_from_raw(fragments)
    return tuple(fragments)


def _fragment_id_sort_key(fragment_id: str) -> tuple[int, int, str]:
    """flat union의 조각 순서를 id 기준으로 언제나 같게 만든다."""

    normalized = str(fragment_id)
    if normalized.isdecimal():
        # ``1``과 ``01``처럼 숫자값은 같아도 id 문자열은 다른 경우까지
        # 순서를 고정해야 입력 시퀀스의 우연한 순서에 흔들리지 않는다.
        return (0, int(normalized), normalized)
    return (1, 0, normalized)


def _assert_public_number_mapping_injective(
    fragments: Sequence[CollectedFragment],
) -> None:
    """서로 다른 조각 id가 같은 공개 번호가 되는 매핑을 선차단한다."""

    fragment_by_public_number: dict[str, str] = {}
    for fragment in fragments:
        fragment_id = str(fragment.fragment_id)
        public_number = citation_number(fragment_id)
        if (
            not isinstance(public_number, str)
            or _CANONICAL_FRAGMENT_ID_RE.fullmatch(public_number) is None
        ):
            raise ValueError(
                f"fragment_id를 공개 번호로 바꿀 수 없습니다: {fragment_id!r}"
            )
        previous = fragment_by_public_number.get(public_number)
        if previous is not None and previous != fragment_id:
            raise ValueError(
                "서로 다른 fragment_id가 같은 공개 번호로 충돌합니다: "
                f"{previous!r}, {fragment_id!r} -> {public_number}"
            )
        fragment_by_public_number[public_number] = fragment_id


def _normalize_packet_fragments(
    raw_packet: FragmentsInput,
) -> tuple[CollectedFragment, ...]:
    """packet raw Mapping의 문서일만 추가 보존하고 legacy 변환은 바꾸지 않는다."""

    normalized = _normalize_fragments(raw_packet)
    if not isinstance(raw_packet, Mapping):
        return normalized
    document_dates = {
        str(fragment_id): str(fragment.get("문서일") or "").strip()
        for fragment_id, fragment in raw_packet.items()
        if isinstance(fragment, Mapping)
    }
    return tuple(
        CollectedFragment(
            fragment_id=fragment.fragment_id,
            kind=fragment.kind,
            text=fragment.text,
            source_url=fragment.source_url,
            document_title=fragment.document_title,
            location=fragment.location,
            document_date=document_dates.get(fragment.fragment_id, ""),
            document_identity=fragment.document_identity,
        )
        for fragment in normalized
    )


def _prepare_section_evidence_packets(
    packets: SectionEvidencePackets,
) -> _PreparedSectionEvidencePackets:
    """장별 묶음을 검증하고 검증기용 flat union을 한 번에 만든다.

    키·조각 충돌 검사는 작가를 부르기 전에 끝나야 한다. 같은 조각이 여러
    장에 정당하게 배정될 수 있으므로 내용까지 같은 중복은 허용하되, 장별
    프롬프트와 flat union에서는 각각 한 번만 남긴다.
    """

    typed_packets = (
        packets if isinstance(packets, SectionEvidencePacketSet) else None
    )
    packet_mapping: Mapping[str, FragmentsInput] = (
        {
            packet.section_id: packet.fragments
            for packet in typed_packets.packets
        }
        if typed_packets is not None
        else packets
    )
    expected = set(SECTION_IDS)
    actual = set(packet_mapping)
    if actual != expected:
        missing = [section_id for section_id in SECTION_IDS if section_id not in actual]
        extra = sorted(str(section_id) for section_id in actual - expected)
        details: list[str] = []
        if missing:
            details.append("누락=" + ",".join(missing))
        if extra:
            details.append("여분=" + ",".join(extra))
        raise ValueError(
            "장별 evidence packet 키는 SECTION_IDS와 정확히 같아야 합니다"
            + (": " + " · ".join(details) if details else "")
        )

    by_id: dict[str, CollectedFragment] = {}
    normalized_packets: dict[str, tuple[CollectedFragment, ...]] = {}
    allowed_fragment_ids_by_section: dict[str, frozenset[str]] = {}
    empty_sections: list[str] = []
    for section_id in SECTION_IDS:
        raw_packet = packet_mapping[section_id]
        if isinstance(raw_packet, Mapping):
            # ``fragments_from_raw``는 legacy 호환 때문에 빈 원문을 조용히
            # 제외한다. packet 모드는 그 전에 원시 항목을 검사해, 유효 조각
            # 옆에 섞인 빈 자료도 숨지 못하게 한다.
            has_nonempty_raw_text = any(
                isinstance(raw_fragment, Mapping)
                and isinstance(raw_fragment.get("원문"), str)
                and bool(raw_fragment.get("원문").strip())
                for raw_fragment in raw_packet.values()
            )
            for raw_fragment_id, raw_fragment in raw_packet.items():
                fragment_id = str(raw_fragment_id)
                if _CANONICAL_FRAGMENT_ID_RE.fullmatch(fragment_id) is None:
                    raise ValueError(
                        "packet fragment_id는 앞자리 0 없는 양의 ASCII "
                        f"십진수여야 합니다: {fragment_id!r}"
                    )
                if not isinstance(raw_fragment, Mapping):
                    raise ValueError(
                        f"packet fragment {fragment_id}의 원시 자료 형식이 "
                        "Mapping이 아닙니다"
                    )
                raw_text = raw_fragment.get("원문")
                if not isinstance(raw_text, str) or (
                    not raw_text.strip() and has_nonempty_raw_text
                ):
                    raise ValueError(
                        f"packet fragment {fragment_id}의 text가 비어 있습니다"
                    )
        unique_by_id: dict[str, CollectedFragment] = {}
        for fragment in _normalize_packet_fragments(raw_packet):
            fragment_id = str(fragment.fragment_id)
            if _CANONICAL_FRAGMENT_ID_RE.fullmatch(fragment_id) is None:
                raise ValueError(
                    "packet fragment_id는 앞자리 0 없는 양의 ASCII 십진수여야 "
                    f"합니다: {fragment_id!r}"
                )
            if not isinstance(fragment.text, str) or not fragment.text.strip():
                raise ValueError(
                    f"packet fragment {fragment_id}의 text가 비어 있습니다"
                )
            previous = by_id.get(fragment_id)
            if previous is not None and previous != fragment:
                raise ValueError(
                    "장별 evidence packet에서 같은 fragment_id의 내용이 "
                    f"충돌합니다: {fragment_id}"
                )
            if previous is None:
                by_id[fragment_id] = fragment
            unique_by_id.setdefault(fragment_id, fragment)
        if not unique_by_id:
            empty_sections.append(section_id)
        ordered_ids = sorted(unique_by_id, key=_fragment_id_sort_key)
        normalized_packets[section_id] = tuple(
            unique_by_id[fragment_id] for fragment_id in ordered_ids
        )
        allowed_fragment_ids_by_section[section_id] = frozenset(ordered_ids)

    if empty_sections:
        raise ValueError(
            "장별 evidence packet은 비어 있을 수 없습니다: "
            + ",".join(empty_sections)
        )

    flat_union = tuple(
        by_id[fragment_id]
        for fragment_id in sorted(by_id, key=_fragment_id_sort_key)
    )
    _assert_public_number_mapping_injective(flat_union)
    return _PreparedSectionEvidencePackets(
        packets=normalized_packets,
        allowed_fragment_ids_by_section=allowed_fragment_ids_by_section,
        flat_union=flat_union,
        company_id=typed_packets.company_id if typed_packets is not None else "",
        evidence_generation_sha256=(
            typed_packets.evidence_generation_sha256
            if typed_packets is not None
            else ""
        ),
        packet_sha256s=(
            typed_packets.packet_sha256s if typed_packets is not None else ()
        ),
    )


def _sanitize_report_to_section_evidence(
    report: ComposedReport,
    allowed_fragment_ids_by_section: Mapping[str, frozenset[str]],
) -> ComposedReport:
    """장 밖 조각을 인용한 본문·도식 줄을 검수 AI 전에 제외한다.

    해석 문장의 빈 인용은 빈 집합이므로 허용한다. 요약은 장별 경계가 아니라
    전체 보고서의 합집합을 쓰므로 이 함수에서 건드리지 않는다.
    """

    sections: list[ComposedSection] = []
    for section in report.sections:
        allowed = allowed_fragment_ids_by_section.get(
            section.section_id, frozenset()
        )
        sentences = tuple(
            sentence
            for sentence in section.sentences
            if set(sentence.citations).issubset(allowed)
            and not contains_inline_citation_marker(sentence.text)
        )
        flow_rows = tuple(
            row
            for row in section.flow_rows
            if set(row.citations).issubset(allowed)
            and not any(contains_inline_citation_marker(cell) for cell in row.cells)
        )
        notice = section.notice
        if section.sentences and not sentences and not notice:
            notice = _NOTICE_OUTSIDE_PACKET_CITATIONS
        sections.append(
            ComposedSection(
                section_id=section.section_id,
                sentences=sentences,
                notice=notice,
                flow_rows=flow_rows,
            )
        )
    return ComposedReport(sections=tuple(sections), summary=report.summary)


def _validate_table_citations_for_section(
    tables: Sequence[PerformanceTable],
    *,
    section_id: str,
    allowed_fragment_ids: frozenset[str],
    table_label: str,
    require_cite: bool = False,
) -> None:
    """프로그램 표의 표현형 cite를 정본 번호로 읽어 장 소유권을 선검사한다."""

    for index, table in enumerate(tables, start=1):
        raw_cite = str(table.cite or "").strip()
        # legacy flat에서는 cite가 없는 기존 표를 그대로 허용한다. 반면 packet
        # 엄격 모드는 표도 공개 주장이라 장 소유 근거 없이는 내보내지 않는다.
        if not raw_cite:
            if require_cite and table.rows:
                raise ValueError(
                    f"{table_label} {index}번 표의 cite가 비어 있습니다"
                )
            continue
        fragment_id = citation_number(raw_cite)
        if not fragment_id:
            raise ValueError(
                f"{table_label} {index}번 표의 cite를 조각 번호로 해석할 수 없습니다"
            )
        if fragment_id not in allowed_fragment_ids:
            raise ValueError(
                f"{table_label} {index}번 표의 cite {fragment_id}는 "
                f"{section_id} evidence packet에 없습니다"
            )


def _assert_composed_report_evidence_invariant(
    report: ComposedReport,
    allowed_fragment_ids_by_section: Mapping[str, frozenset[str]],
    union_fragment_ids: frozenset[str],
    *,
    stage: str,
) -> None:
    """프로그램 단계가 장별 근거 소유권을 다시 깨지 않았는지 강제한다."""

    problems: list[str] = []
    section_ids = tuple(section.section_id for section in report.sections)
    if section_ids != SECTION_IDS:
        problems.append(f"장 id·순서={section_ids!r} (SECTION_IDS와 다름)")
    for section in report.sections:
        allowed = allowed_fragment_ids_by_section.get(section.section_id)
        if allowed is None:
            problems.append(f"알 수 없는 장 {section.section_id}")
            continue
        for index, sentence in enumerate(section.sentences, start=1):
            if contains_inline_citation_marker(sentence.text):
                problems.append(f"{section.section_id} 본문 {index}번 inline cite")
            outside = sorted(
                {
                    str(citation).strip()
                    for citation in sentence.citations
                    if str(citation).strip() not in allowed
                },
                key=_fragment_id_sort_key,
            )
            if outside:
                problems.append(
                    f"{section.section_id} 본문 {index}번=" + ",".join(outside)
                )
        for index, row in enumerate(section.flow_rows, start=1):
            if any(contains_inline_citation_marker(cell) for cell in row.cells):
                problems.append(f"{section.section_id} 도식 {index}번 inline cite")
            outside = sorted(
                {
                    str(citation).strip()
                    for citation in row.citations
                    if str(citation).strip() not in allowed
                },
                key=_fragment_id_sort_key,
            )
            if outside:
                problems.append(
                    f"{section.section_id} 도식 {index}번=" + ",".join(outside)
                )
    for index, sentence in enumerate(report.summary, start=1):
        if contains_inline_citation_marker(sentence.text):
            problems.append(f"summary {index}번 inline cite")
        outside = sorted(
            {
                str(citation).strip()
                for citation in sentence.citations
                if str(citation).strip() not in union_fragment_ids
            },
            key=_fragment_id_sort_key,
        )
        if outside:
            problems.append(f"summary {index}번=" + ",".join(outside))
    if problems:
        raise ValueError(
            f"장별 evidence invariant 위반({stage}): " + " · ".join(problems)
        )


def compose_sections(
    company_name: str,
    fragments: FragmentsInput,
    performance_table: Optional[PerformanceTable],
    ask: AskFn,
    *,
    section_evidence_packets: Optional[SectionEvidencePackets] = None,
) -> ComposedReport:
    """9개 장 전부를 작가 AI로 쓴다 — 장마다 1회 호출(파싱 실패 시 +1회).

    Args:
        company_name: 분석 대상 법인 이름.
        fragments: 수집 조각 — real.py의 `dict[int, dict[str, str]]` 그대로,
            또는 `CollectedFragment` 시퀀스.
        performance_table: 프로그램이 검증해 만든 3개년 실적표. 없으면 None.
        ask: 프롬프트 문자열 → 응답 문자열 주입 함수 (시험은 가짜 함수 사용).
        section_evidence_packets: 장마다 허용할 근거 조각 묶음. 지정하면 아홉
            장 키를 먼저 검증하고, 각 작가에게 자기 장 묶음만 보여 준다.
            미지정이면 기존 flat fragments 프롬프트를 그대로 유지한다.

    Returns:
        9개 장이 «전부» 들어 있는 ComposedReport. 실패한 장도 삭제하지 않고
        빈 문장 + 안내문으로 남는다. summary는 빈 튜플(소단계 3-3이 채운다).

    Raises:
        ValueError: packet 키·내용·비어 있음 또는 실적표 cite의 장 소유권이
            계약과 다를 때. 작가를 호출하기 전에 발생한다.

    ★ legacy flat 모드는 장을 «순서대로» 쓰며 앞 장 문장을 뒤 장에 보여 준다.
      packet 모드는 생성문 자체가 다른 장의 근거 경계를 넘지 않도록 이 블록을
      전달하지 않고, 중복은 뒤의 single-owner/dedupe 단계에 맡긴다.
    """
    prepared: Optional[_PreparedSectionEvidencePackets] = None
    if section_evidence_packets is None:
        normalized = _normalize_fragments(fragments)
    else:
        prepared = _prepare_section_evidence_packets(
            section_evidence_packets
        )
        _validate_table_citations_for_section(
            (performance_table,) if performance_table is not None else (),
            section_id="past_changes",
            allowed_fragment_ids=prepared.allowed_fragment_ids_by_section[
                "past_changes"
            ],
            table_label="실적",
            require_cite=True,
        )
        # packet 모드에서는 flat 입력을 작가 프롬프트에 섞지 않는다.
        normalized = ()
    sections: list[ComposedSection] = []
    already_written: list[str] = []
    for section_id in SECTION_IDS:
        section_fragments = (
            normalized if prepared is None else prepared.packets[section_id]
        )
        section_table = (
            performance_table
            if prepared is None or section_id == "past_changes"
            else None
        )
        prompt_already_written = already_written if prepared is None else ()
        section = _compose_one_section(
            section_id,
            build_section_prompt(
                company_name,
                section_id,
                section_fragments,
                section_table,
                prompt_already_written,
            ),
            ask,
            reject_inline_citation_markers=prepared is not None,
            # packet/FULL 호출 계약은 장마다 정확히 한 번이다. 형식 오류를
            # 재호출로 감추지 않고 해당 장을 fail-closed 안내문으로 남긴다.
            parse_retry_limit=(0 if prepared is not None else PARSE_RETRY_LIMIT),
        )
        sections.append(section)
        if prepared is None:
            already_written.extend(sentence.text for sentence in section.sentences)
    report = ComposedReport(sections=tuple(sections), summary=())
    if prepared is None:
        return report
    return _sanitize_report_to_section_evidence(
        report, prepared.allowed_fragment_ids_by_section
    )


def compose_selected_sections(
    company_name: str,
    performance_table: Optional[PerformanceTable],
    ask: AskFn,
    *,
    section_evidence_packets: SectionEvidencePacketSet,
    section_ids: tuple[str, ...],
) -> ComposedReport:
    """승인된 FULL 장만 각자의 기존 typed packet으로 한 번씩 다시 쓴다.

    전체 packet set은 회사·generation·아홉 장 결속을 다시 검증하는 데만 쓰고,
    실제 작가 prompt에는 해당 장 packet 외의 조각이나 기존 보고서 본문을 넣지
    않는다. 반환값에도 승인 장만 들어 있으므로 호출자가 기존 보고서에 명시적으로
    병합해야 하며, 비대상 장을 우연히 재생성할 통로가 없다.
    """

    if type(section_evidence_packets) is not SectionEvidencePacketSet:
        raise TypeError("보충 작성에는 정확한 typed section packet set이 필요합니다")
    if type(section_ids) is not tuple or any(
        type(section_id) is not str for section_id in section_ids
    ):
        raise TypeError("보충 장은 정책 순서의 문자열 tuple이어야 합니다")
    if not section_ids or len(section_ids) != len(set(section_ids)):
        raise ValueError("보충 장은 중복 없는 한 개 이상의 장이어야 합니다")
    selected = set(section_ids)
    if tuple(section_id for section_id in SECTION_IDS if section_id in selected) != (
        section_ids
    ):
        raise ValueError("보충 장은 SECTION_IDS의 부분집합을 정책 순서로 담아야 합니다")

    prepared = _prepare_section_evidence_packets(section_evidence_packets)
    _validate_table_citations_for_section(
        (performance_table,) if performance_table is not None else (),
        section_id="past_changes",
        allowed_fragment_ids=prepared.allowed_fragment_ids_by_section[
            "past_changes"
        ],
        table_label="실적",
        require_cite=True,
    )
    sections: list[ComposedSection] = []
    for section_id in section_ids:
        section = _compose_one_section(
            section_id,
            build_section_prompt(
                company_name,
                section_id,
                prepared.packets[section_id],
                performance_table if section_id == "past_changes" else None,
                (),
            ),
            ask,
            reject_inline_citation_markers=True,
            parse_retry_limit=0,
        )
        sections.append(section)
    return _sanitize_report_to_section_evidence(
        ComposedReport(sections=tuple(sections), summary=()),
        prepared.allowed_fragment_ids_by_section,
    )


# ══════════════════════════════════════════════════════════
# 핵심 요약 (소단계 3-3) — 본문 완성 «후» 새로 쓴다 (기준문서 3절)
# ══════════════════════════════════════════════════════════
# ★ 아래 상수는 3-3 소유 범위(logic.py) 안에 둔다 — constants.py는 다른
#   소단계와의 병행 수정 충돌을 피하기 위해 여기서 건드리지 않는다.

#: 핵심 요약 목표 문장 수 — 기준문서 3절: 3~5문장
SUMMARY_MIN_SENTENCES: Final[int] = 3
SUMMARY_MAX_SENTENCES: Final[int] = 5

SUMMARY_PROMPT_HEADER: Final[str] = (
    "당신은 «공식 근거 기반 기업분석 보고서»의 본문을 모두 읽고, "
    "보고서 맨 앞에 실릴 «핵심 요약»을 산문으로 작성한다.\n"
    "지원 직무·채용공고·지원자 정보는 주어지지 않았다. 개인이나 직무에 맞춘 "
    "내용을 만들지 마라.\n"
)

#: 요약 작성 규칙 — 본문 재탕 금지 + 장별 인용·등급 규칙과 동일한 규칙 적용.
SUMMARY_RULES_GUIDE: Final[str] = (
    "작성 규칙:\n"
    f"1. 아래 본문 전체를 근거로 핵심 요약 {SUMMARY_MIN_SENTENCES}~"
    f"{SUMMARY_MAX_SENTENCES}문장을 «새로» 쓴다.\n"
    "2. 본문 문장을 글자 그대로 옮겨 적지 않는다 — 여러 장을 종합한 "
    "새 문장으로 쓴다.\n"
    "3. 모든 문장에 인용(조각 id 배열)과 등급을 붙인다. 인용 id는 본문 문장 뒤 "
    "[인용: …]에 표시된 조각 번호를 그대로 쓴다.\n"
    f"   - 등급 «{GRADE_CONFIRMED}»: 인용한 조각 원문에 직접 근거가 있는 "
    "사실 서술.\n"
    f"   - 등급 «{GRADE_INTERPRETED}»: 공식 자료에 기반한 분석·의미 부여. "
    "종합적 해석이면 빈 배열도 허용된다.\n"
    "4. 본문에 없는 사실·숫자를 지어내지 않는다.\n"
    "5. «글» 문장 본문 안에 [숫자]·[인용: …] 같은 대괄호 표기를 직접 쓰지 "
    "않는다. 인용은 반드시 위 «인용» 배열로만 표시한다.\n"
)

#: 요약 출력 JSON 안내 — 장별 JSON_SCHEMA_GUIDE와 같은 모양(키 상수 공유).
#: (장별 안내문은 «아래 자료 목록» 문구가 있어 요약 프롬프트에는 안 맞아 따로 둔다.)
SUMMARY_JSON_GUIDE: Final[str] = (
    "출력 형식 — 설명·머리말 없이 아래 모양의 JSON만 출력한다:\n"
    f'{{"{RESPONSE_SENTENCES_KEY}": [{{"{RESPONSE_TEXT_KEY}": "<문장>", '
    f'"{RESPONSE_CITATIONS_KEY}": ["<조각id>", "..."], '
    f'"{RESPONSE_GRADE_KEY}": "{GRADE_CONFIRMED}" 또는 '
    f'"{GRADE_INTERPRETED}"}}]}}\n'
)

SUMMARY_BODY_HEAD: Final[str] = "\n방금 완성된 보고서 본문 (전체):\n"

#: 재탕 검출 후 재요청에 덧붙이는 안내 — 계획(04장 3-3): 재요청은 1회.
SUMMARY_DUPLICATE_REMINDER: Final[str] = (
    "\n(직전 응답에 본문 문장을 글자 그대로 옮겨 적은 문장이 있었다. "
    "본문 재탕 없이, 종합한 새 문장으로만 다시 요약하라.)\n"
)


def _normalized_text(text: str) -> str:
    """공백 차이만 지운 비교용 형태를 만든다.

    ★ «글자 그대로 재탕»만 잡기 위한 정규화다 — 어휘·마커·어미를 보는
      내용 검사가 아니다(닫힌 게이트 금지 원칙 준수).
    """
    return " ".join(text.split())


def _render_report_body(report: ComposedReport) -> str:
    """본문 전체를 장 제목·등급·인용 번호와 함께 편다 — 작가의 요약 재료."""
    lines: list[str] = [SUMMARY_BODY_HEAD]
    for section in report.sections:
        if not section.sentences:
            continue  # 빈 장은 요약할 재료가 없다
        title = SECTION_TITLES.get(section.section_id, section.section_id)
        lines.append(f"\n[{title}]\n")
        for sentence in section.sentences:
            line = f"- ({sentence.grade}) {sentence.text}"
            if sentence.citations:
                line += " [인용: " + ", ".join(sentence.citations) + "]"
            lines.append(line + "\n")
    return "".join(lines)


def build_summary_prompt(report: ComposedReport) -> str:
    """핵심 요약을 쓰게 하는 지시문 — 규칙 + 본문 전체 + JSON 강제."""
    parts = [
        SUMMARY_PROMPT_HEADER,
        "\n",
        SUMMARY_RULES_GUIDE,
        FORBIDDEN_TOPICS_GUIDE,
        SUMMARY_JSON_GUIDE,
        _render_report_body(report),
    ]
    return "".join(parts)


def _body_sentence_keys(report: ComposedReport) -> frozenset[str]:
    """본문 전 문장의 비교용 형태 집합 — 재탕 검출에 쓴다."""
    return frozenset(
        _normalized_text(sentence.text)
        for section in report.sections
        for sentence in section.sentences
    )


def _split_out_duplicates(
    sentences: Sequence[ComposedSentence], body_keys: frozenset[str]
) -> tuple[tuple[ComposedSentence, ...], bool]:
    """본문을 글자 그대로 옮겨 적은 문장을 골라낸다.

    Returns:
        (재탕이 아닌 문장들, 재탕이 하나라도 있었는가)
    """
    kept = tuple(
        sentence
        for sentence in sentences
        if _normalized_text(sentence.text) not in body_keys
    )
    return kept, len(kept) != len(sentences)


def _confirmed_by_section_rounds(
    report: ComposedReport,
) -> tuple[ComposedSentence, ...]:
    """본문의 «확인» 문장을 «서로 다른 장 우선» 순서로 편다.

    한 바퀴에 장마다 한 문장씩(각 장의 첫 확인 문장부터) 뽑고,
    부족하면 다음 바퀴로 — 같은 장에서 연달아 뽑지 않기 위한 순서다.
    """
    pools = [
        [s for s in section.sentences if s.grade == GRADE_CONFIRMED]
        for section in report.sections
    ]
    deepest = max((len(pool) for pool in pools), default=0)
    ordered: list[ComposedSentence] = []
    for round_index in range(deepest):
        for pool in pools:
            if round_index < len(pool):
                ordered.append(pool[round_index])
    return tuple(ordered)


def _supplement_summary(
    summary: Sequence[ComposedSentence], report: ComposedReport
) -> tuple[ComposedSentence, ...]:
    """요약이 최소 문장 수에 못 미치면 본문 «확인» 문장으로 보충한다.

    ★ 본문 재사용이 허용되는 유일한 경로다(계획 04장 3-3) —
      빈 요약으로 인한 차단을 만들지 않기 위해서다.
    """
    chosen: list[ComposedSentence] = list(summary)
    seen = {_normalized_text(sentence.text) for sentence in chosen}
    for candidate in _confirmed_by_section_rounds(report):
        if len(chosen) >= SUMMARY_MIN_SENTENCES:
            break
        key = _normalized_text(candidate.text)
        if key in seen:
            continue
        chosen.append(candidate)
        seen.add(key)
    return tuple(chosen)


def compose_summary(
    report: ComposedReport,
    ask: AskFn,
    *,
    reject_inline_citation_markers: bool = False,
) -> ComposedReport:
    """본문 완성 후 핵심 요약 3~5문장을 새로 써서 채운 보고서를 돌려준다.

    흐름(계획 04장 3-3):
        ① 본문 전체를 주고 요약을 새로 쓰게 한다 (파싱 실패 시 1회 재요청).
        ② 본문 문장을 글자 그대로 옮긴 재탕을 코드로 검출 → 1회 재요청.
           재요청 결과에서도 재탕은 버리고, 쓸 것이 없으면 1차 생존 문장을 쓴다.
        ③ 그래도 최소 문장 수 미만이면 본문 «확인» 문장으로 보충한다
           (이때만 재사용 허용, 서로 다른 장 우선). 빈 요약이어도 차단하지 않는다.

    Args:
        report: compose_sections가 만든 본문 (summary는 무시하고 새로 채운다).
        ask: 프롬프트 문자열 → 응답 문자열 주입 함수 (시험은 가짜 함수 사용).

    Returns:
        sections는 그대로 두고 summary만 채운 새 ComposedReport.
    """
    body_keys = _body_sentence_keys(report)
    if not body_keys:
        # 본문이 통째로 비면 요약할 재료가 없다 — 헛호출도, 차단도 하지 않는다
        return ComposedReport(sections=report.sections, summary=())
    prompt = build_summary_prompt(report)
    # 요약에는 경로표가 없다 — 응답 원문은 버린다.
    sentences, _raw = _ask_and_parse(
        ask,
        prompt,
        reject_inline_citation_markers=reject_inline_citation_markers,
    )
    retries = 0
    while sentences is None and retries < PARSE_RETRY_LIMIT:
        retries += 1
        sentences, _raw = _ask_and_parse(
            ask,
            prompt + RETRY_REMINDER,
            reject_inline_citation_markers=reject_inline_citation_markers,
        )
    if sentences is None:
        sentences = ()
    kept, had_duplicate = _split_out_duplicates(sentences, body_keys)
    if had_duplicate:
        # 재탕 검출 → 1회 재요청. 실패하거나 또 전부 재탕이면 1차 생존 문장 유지.
        retry_sentences, _retry_raw = _ask_and_parse(
            ask,
            prompt + SUMMARY_DUPLICATE_REMINDER,
            reject_inline_citation_markers=reject_inline_citation_markers,
        )
        if retry_sentences:
            retry_kept, _ = _split_out_duplicates(retry_sentences, body_keys)
            if retry_kept:
                kept = retry_kept
    summary = kept[:SUMMARY_MAX_SENTENCES]
    if len(summary) < SUMMARY_MIN_SENTENCES:
        summary = _supplement_summary(summary, report)
    return ComposedReport(sections=report.sections, summary=summary)
