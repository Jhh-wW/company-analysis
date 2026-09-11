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
    DOCUMENT_LIST_GUIDE,
    DOCUMENT_LIST_HEAD,
    EVIDENCE_LABEL_DOCUMENT_HEADER,
    EVIDENCE_LABEL_OMIT_NUMERIC_LOCATION,
    EVIDENCE_LABEL_SHORT_KIND,
    FORBIDDEN_TOPICS_GUIDE,
    GRADE_CONFIRMED,
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
    PROMPT_FRAGMENT_LOCATION_LABEL,
    PROMPT_HEADER,
    PROMPT_TABLE_HEAD,
    SOURCE_KIND_DISPLAY_NAMES,
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
    COMPETITIVE_POSITION_PARAGRAPH_PLAN,
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
    VerifiedProgramEvidence,
    fragments_from_raw,
)
from src.shared.report_evidence.policy import required_slots_for
from src.features.composer.news_constants import NEWS_WRITER_GUIDE
from src.features.composer.news_usage import news_metadata, parse_news_decisions
from src.features.composer.news_block import _is_news_fragment
from src.shared.report_quality.composition_diagnostic_constants import (
    DIAGRAM_ROW_COUNT_STEP,
    DIAGRAM_STAGE_PARSED,
    DIAGRAM_STAGE_SECTION_EVIDENCE,
    EXTRACT_DIRECT,
    EXTRACT_SLICED,
)
from src.shared.report_quality.output_constants import SUMMARY_MIN_SENTENCES, SUMMARY_MAX_SENTENCES

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
    supported_claim_slots_by_fragment_id: Mapping[str, frozenset[str]]
    flat_union: tuple[CollectedFragment, ...]
    program_evidence_by_section: Mapping[str, VerifiedProgramEvidence]
    program_facts: tuple[object, ...] = ()
    program_sources: tuple[object, ...] = ()
    program_sentences: tuple[ComposedSentence, ...] = ()
    enforce_claim_slot_support: bool = False
    company_id: str = ""
    evidence_generation_sha256: str = ""
    packet_sha256s: tuple[tuple[str, str], ...] = ()


_NOTICE_OUTSIDE_PACKET_CITATIONS: Final[str] = (
    "이 장에 배정되지 않은 근거를 사용한 문장을 제외했습니다."
)
_NOTICE_UNSUPPORTED_CLAIM_SLOTS: Final[str] = (
    "이 장의 근거가 지원하지 않는 주장을 제외했습니다."
)

#: FULL AI 표의 각 칸이 주장하는 의미와 typed 근거 slot의 닫힌 결속.
#:
#: FlowRow에는 문장처럼 작가가 고르는 claim slot 필드가 없다. 따라서 헤더가
#: 이미 고정한 칸의 뜻을 코드가 소유한다. 각 비어 있지 않은 칸은 같은 행이
#: 인용한 조각들 가운데 아래 slot 중 하나의 지원을 받아야 한다. 이 표 없이
#: 장 안 citation만 검사하면 5장의 «과제» 원문으로 가짜 «대응»을 쓰거나,
#: 7장의 관계 원문으로 회사가 하지 않는 운영 역할을 만들 수 있다.
_FLOW_CELL_SUPPORTED_SLOTS_BY_SECTION: Final[
    dict[str, tuple[frozenset[str], ...]]
] = {
    "identity": (
        frozenset({"identity:corporate_identity"}),
        frozenset({"identity:business_definition", "identity:legal_scope"}),
        frozenset(
            {
                "identity:corporate_identity",
                "identity:business_definition",
                "identity:self_positioning",
            }
        ),
    ),
    "business_model": (
        frozenset({"business_model:value_exchange", "business_model:revenue_model"}),
        frozenset({"business_model:value_exchange", "business_model:revenue_model"}),
        frozenset(
            {
                "business_model:customer_type",
                "business_model:sales_channel",
                "business_model:value_exchange",
            }
        ),
        frozenset({"business_model:revenue_model", "business_model:value_exchange"}),
    ),
    "portfolio": (
        frozenset({"portfolio:product_role"}),
        frozenset({"portfolio:product_role", "portfolio:customer_fit"}),
        frozenset(
            {
                "portfolio:portfolio_priority",
                "portfolio:product_role",
                "portfolio:revenue_link",
            }
        ),
        frozenset({"portfolio:product_role", "portfolio:revenue_link"}),
    ),
    "current_challenges": (
        frozenset({"current_challenges:issue"}),
        frozenset({"current_challenges:response"}),
    ),
    "future_strategy": (
        frozenset({"future_strategy:stated_plan"}),
        frozenset(
            {
                "future_strategy:plan_timing",
                "future_strategy:plan_status",
            }
        ),
        frozenset(
            {
                "future_strategy:stated_plan",
                "future_strategy:plan_status",
                "future_strategy:plan_condition",
                "future_strategy:execution_signal",
            }
        ),
    ),
    "operations_partners": (
        frozenset(
            {
                "operations_partners:value_chain",
                "operations_partners:supply_relation",
            }
        ),
        frozenset({"operations_partners:operating_role"}),
        frozenset(
            {
                "operations_partners:value_chain",
                "operations_partners:distribution_relation",
                "operations_partners:partnership",
            }
        ),
    ),
    "culture": (
        frozenset({"culture:work_principle", "culture:leadership"}),
        frozenset({"culture:work_principle", "culture:decision_process"}),
        frozenset({"culture:verified_case", "culture:organization_change"}),
    ),
}

if set(_FLOW_CELL_SUPPORTED_SLOTS_BY_SECTION) != set(FLOW_HEADERS_BY_SECTION):
    raise RuntimeError("FULL 표 칸과 claim-slot 결속 정책의 장 목록이 다릅니다")
if any(
    len(_FLOW_CELL_SUPPORTED_SLOTS_BY_SECTION[section_id]) != len(headers)
    for section_id, headers in FLOW_HEADERS_BY_SECTION.items()
):
    raise RuntimeError("FULL 표 칸과 claim-slot 결속 정책의 칸 수가 다릅니다")
if any(
    not allowed_slots
    or not allowed_slots <= set(CLAIM_SLOTS_BY_SECTION[section_id])
    for section_id, cell_requirements in _FLOW_CELL_SUPPORTED_SLOTS_BY_SECTION.items()
    for allowed_slots in cell_requirements
):
    raise RuntimeError("FULL 표 칸이 현재 장 밖의 claim slot을 허용합니다")


# ══════════════════════════════════════════════════════════
# 프롬프트 만들기
# ══════════════════════════════════════════════════════════


#: 원문위치가 typed DART 수집기의 글자 오프셋(``f"{start}-{end}"``, 예
#: ``"18656-19172"``)인지 판정한다. analysis_engine의
#: ``evidence_collection/collect.py``가 만드는 이 모양만 숫자로만 이뤄진다 —
#: 사람이 쓴 제목·문단 경로(``"II. 사업의 내용"``·``"PDF p.1 1문단"`` 등)는
#: 전부 글자를 섞어 쓰므로 걸리지 않는다.
_NUMERIC_OFFSET_LOCATION_RE = re.compile(r"\d+(-\d+)?")


def _is_numeric_offset_location(location: str) -> bool:
    """작가가 못 읽는 숫자 오프셋 원문위치인가 (글자로 된 위치는 False)."""
    return bool(_NUMERIC_OFFSET_LOCATION_RE.fullmatch(location))


def _document_symbol(index: int) -> str:
    """1부터 시작하는 순번을 엑셀 열 이름 방식 기호로 바꾼다.

    1→A, 2→B, …, 26→Z, 27→AA, 28→AB … 같은 (kind, document_title) 조합은
    같은 기호를 받으므로, 여러 조각이 같은 문서에서 나왔으면 문서 목록에는
    한 번만, 조각 줄에는 기호만 남는다.
    """
    letters: list[str] = []
    remaining = index
    while remaining > 0:
        remaining, remainder = divmod(remaining - 1, 26)
        letters.append(chr(ord("A") + remainder))
    return "".join(reversed(letters))


def _render_fragments(
    fragments: Sequence[CollectedFragment],
    *,
    show_supported_claim_slots: bool = False,
) -> str:
    """조각 전체를 id와 함께 나열한다 — 작가가 이 id로 인용한다.

    라벨 다이어트 세 가지(②③④, `EVIDENCE_LABEL_*` 상수 참고)는 각각
    독립적으로 켜고 끌 수 있다 — 유료 비교 실행에서 품질이 떨어지면 하나씩
    되돌려 원인을 가르기 위해서다(2026-09-10 팀장 지시). ③이 꺼지면
    ②(짧은 표시명)는 문서 목록이 아니라 조각 줄에 «인라인»으로 적용된다.
    셋 다 꺼지면 이 함수는 2026-09-10 이전과 글자 그대로 같은 문자열을
    낸다(숫자 원문위치 제거만 별개 — ④ 자체를 끄면 그마저 없다).

    ★ 종류 판정 — 어느 다이어트든 «원래» 종류 문자열(``raw_kind`` =
      ``formal_source_kind or kind``)을 기준으로 한다. typed 조각의
      ``kind``는 운반 지문(``typed-evidence-v3:<hex>``)이라 작가에게 아무
      뜻이 없다 — 닫힌 출처 종류는 ``formal_source_kind``에만 봉인돼 있다.
      raw 조각은 그 필드가 비어 있고 ``kind``가 곧 「종류」다. flat 렌더
      경로(``fragments_from_raw``)와 packet 렌더 경로
      (``evidence_transport._collected_fragment_from_raw``)는 서로 다른
      필드에 종류를 담을 뿐(전자는 항상 ``formal_source_kind`` 비움, typed
      raw dict면 후자만 채움) 최종 ``raw_kind`` 값은 두 경로에서 같다 —
      `test_evidence_label_compact.py`의 실제 변환 함수 왕복 시험으로 확인.
    """
    use_header = EVIDENCE_LABEL_DOCUMENT_HEADER
    use_short_kind = EVIDENCE_LABEL_SHORT_KIND
    omit_numeric_location = EVIDENCE_LABEL_OMIT_NUMERIC_LOCATION

    def _kind_label(raw_kind: str) -> str:
        return SOURCE_KIND_DISPLAY_NAMES.get(raw_kind, raw_kind) if use_short_kind else raw_kind

    symbol_by_key: dict[tuple[str, str], str] = {}
    order: list[tuple[str, str]] = []
    if use_header:
        for fragment in fragments:
            raw_kind = fragment.formal_source_kind or fragment.kind or "자료"
            # ★ 기호 배정 키는 표시용으로 줄인 이름이 아니라 «원래» 종류
            #   문자열이다 — 여러 DART 세부 종류가 전부 「공시」로 겹쳐
            #   보여도, 실제로 다른 종류의 문서를 같은 기호로 잘못 합치지
            #   않기 위함이다.
            key = (raw_kind, fragment.document_title)
            if key not in symbol_by_key:
                order.append(key)
                symbol_by_key[key] = _document_symbol(len(order))

    lines: list[str] = [PROMPT_FRAGMENTS_HEAD]
    if use_header and order:
        lines.append(DOCUMENT_LIST_HEAD)
        entry_by_key: dict[tuple[str, str], str] = {}
        entry_counts: dict[str, int] = {}
        for key in order:
            raw_kind, title = key
            kind_label = _kind_label(raw_kind)
            entry = f"{title} · {kind_label}" if title else kind_label
            entry_by_key[key] = entry
            entry_counts[entry] = entry_counts.get(entry, 0) + 1
        for key in order:
            raw_kind, title = key
            entry = entry_by_key[key]
            if entry_counts[entry] > 1:
                # ★ 표시명이 같아져 두 줄의 글자가 겹치는 경우(DART 다섯
                #   종류가 전부 「공시」로 겹치는 게 대표 사례) — 원래 종류
                #   문자열을 괄호로 덧붙여 구분한다(2026-09-10 독립 검토 P2).
                #   기호 배정 키가 이미 raw_kind 기준이라 이 표시만 바뀌고
                #   결정성은 그대로다.
                entry = f"{entry}({raw_kind})"
            lines.append(f"{symbol_by_key[key]}: {entry}\n")
        lines.append(DOCUMENT_LIST_GUIDE)

    for fragment in fragments:
        raw_kind = fragment.formal_source_kind or fragment.kind or "자료"
        if use_header:
            label = f"문서 {symbol_by_key[(raw_kind, fragment.document_title)]}"
        else:
            label = _kind_label(raw_kind)
        if _is_news_fragment(fragment):
            label += " · 메타데이터 " + news_metadata(fragment)
        if not use_header and fragment.document_title:
            label = f"{label}·{fragment.document_title}"
        show_location = fragment.location and (
            not omit_numeric_location
            or not _is_numeric_offset_location(fragment.location)
        )
        if show_location:
            # 안내문이 「원문위치에 … 표기가 있는 조각」을 고르라고 지시하므로
            # 그 값을 실제로 보여 준다. 숫자 오프셋은 작가가 읽을 수 없는
            # typed DART 내부 좌표라 대신 뺀다(위 정규식 주석 참고). 없는
            # 조각(홈페이지 일부)은 그대로 둔다.
            label = (
                f"{label} · {PROMPT_FRAGMENT_LOCATION_LABEL}: {fragment.location}"
            )
        if show_supported_claim_slots:
            supported = ", ".join(fragment.supported_claim_slots) or "없음"
            label = f"{label} · 지원 주장슬롯: {supported}"
        evidence_text = json.dumps(fragment.text, ensure_ascii=False) if _is_news_fragment(fragment) else fragment.text
        lines.append(f"[조각 {fragment.fragment_id}] ({label}) {evidence_text}\n")
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


class CacheablePrompt(str):
    """앞부분이 여러 호출에서 «바이트 동일»한 프롬프트 — 캐시 경계를 실어 나른다.

    ★ 왜 str 하위형인가: 기존 경로(`ask(prompt: str)`·`exact_text_sha256(prompt)`·
      `str(prompt)`·슬라이싱)를 한 줄도 안 고치고 그대로 쓰기 위해서다. 캐시를
      쓸 수 있다는 «표식»만 얹는다.
    ★ 왜 앞부분 길이를 실어 나르나: 프롬프트 캐시는 앞부분이 바이트 단위로
      완전히 같을 때만 맞는다. 어디까지가 공유 앞부분인지는 프롬프트를 «만든»
      쪽만 알고, 실제로 caching 블록을 나눠 보내는 쪽(provider 호출부)은
      모른다. 그래서 글자 수로 경계를 넘긴다 — `prompt[:cache_prefix_chars]`가
      공유 앞부분, `prompt[cache_prefix_chars:]`가 호출마다 달라지는 뒷부분이며
      둘을 이어 붙이면 원래 프롬프트와 같다.
    ★ 알아 둘 것: `prompt + RETRY_REMINDER`처럼 이어 붙이면 결과는 평범한 str이
      되어 표식이 사라진다. 의도된 동작이다 — 재시도는 드물고, 그때는 캐시를
      포기하고 통짜로 보내는 편이 경계를 잘못 잡는 것보다 안전하다.
    """

    #: 공유 앞부분의 «글자» 수. 바이트가 아니라 파이썬 문자열 인덱스다.
    cache_prefix_chars: int

    def __new__(cls, text: str, *, cache_prefix_chars: int) -> "CacheablePrompt":
        if cache_prefix_chars < 0 or cache_prefix_chars > len(text):
            raise ValueError("캐시 앞부분 길이는 0 이상 프롬프트 길이 이하여야 합니다")
        prompt = super().__new__(cls, text)
        prompt.cache_prefix_chars = cache_prefix_chars
        return prompt


def build_section_prompt(
    company_name: str,
    section_id: str,
    fragments: Sequence[CollectedFragment],
    performance_table: Optional[PerformanceTable],
    already_written: Sequence[str] = (),
    *,
    show_supported_claim_slots: bool = False,
    shared_evidence_prefix: bool = False,
) -> str:
    """장 하나를 쓰게 하는 지시문 — 지침 + 조각 전체 + 실적표 + JSON 강제.

    Args:
        already_written: 앞 장들이 이미 쓴 문장. 같은 사실을 다시 쓰지 않도록
            보여 준다. 비어 있으면 블록을 넣지 않는다 (첫 장).
        shared_evidence_prefix: 조각 블록을 «맨 앞»으로 옮겨 아홉 장이 같은
            앞부분을 공유하게 한다. 글자는 한 자도 바뀌지 않고 블록 순서만
            바뀌며, 반환값은 앞부분 길이를 실은 `CacheablePrompt`다.
            기본값 False는 기존 문자열을 바이트 그대로 돌려준다.

    Returns:
        `shared_evidence_prefix`가 False면 평범한 str, True면 `CacheablePrompt`.
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
    if show_supported_claim_slots:
        required_claim_slots = required_slots_for(section_id)
        claim_slot_guide += (
            "\nFULL 근거 결속 규칙 — 모든 산문 문장은 «확인»·«해석» 등급과 "
            "관계없이 이 장에 허용된 주장슬롯을 정확히 하나 선택한다. 빈 문자열이나 "
            "목록 밖 id는 허용되지 않는다. 또한 «인용»에 넣은 조각 중 적어도 하나의 "
            "«지원 주장슬롯» 목록에 선택한 id가 있어야 한다. 지원하지 않는 조각으로 "
            "빈자리를 채우지 말고, 맞는 근거가 없으면 그 문장을 내지 않는다.\n"
            "FULL 필수 의미칸 — 아래 칸은 자료 패킷에 존재하는 데서 끝나지 않고, "
            "각 칸을 뒷받침하는 근거를 인용한 공개 문장으로 모두 다뤄야 한다:\n- "
            + "\n- ".join(required_claim_slots)
            + "\n"
        )
        flow_requirements = _FLOW_CELL_SUPPORTED_SLOTS_BY_SECTION.get(section_id)
        if flow_requirements is not None:
            claim_slot_guide += (
                "FULL 표 근거 결속 규칙 — 표의 각 비어 있지 않은 칸도 같은 행의 "
                "인용 조각이 아래 의미 중 하나를 지원해야 한다:\n"
                + "".join(
                    f"- {header}: {', '.join(sorted(slots))}\n"
                    for header, slots in zip(
                        FLOW_HEADERS_BY_SECTION[section_id],
                        flow_requirements,
                        strict=True,
                    )
                )
            )
    header = PROMPT_HEADER.format(company=company_name)
    # 조각 블록은 프롬프트에서 가장 큰 덩어리다. 아홉 장이 같은 조각을 보는
    # flat 모드에서는 이 블록만 앞으로 옮겨도 앞부분이 장마다 같아진다.
    # ★ 회사 이름과 조각으로만 정해진다 — section_id·already_written·실적표에
    #   의존하면 앞부분이 장마다 달라져 캐시가 영영 안 맞는다.
    fragments_block = _render_fragments(
        fragments,
        show_supported_claim_slots=show_supported_claim_slots,
    )
    section_parts = [
        SECTION_GUIDES[section_id],
        (
            COMPETITIVE_POSITION_PARAGRAPH_PLAN
            if section_id == "competitive_position"
            else ""
        ),
        "\n\n",
        CITATION_RULES_GUIDE,
        NEWS_WRITER_GUIDE if any(_is_news_fragment(f) for f in fragments) else "",
        FORBIDDEN_TOPICS_GUIDE,
        SENTENCE_RANGE_GUIDE.format(
            minimum=minimum,
            maximum=maximum,
            interpretation_cap=MAX_INTERPRETED_SENTENCES_PER_SECTION,
        ),
        claim_slot_guide,
        # 7장은 «경로표»를 함께 내야 해서 스키마 안내를 통째로 바꾼다.
        # 덧붙이면 기본 안내의 「이 JSON만 출력한다」와 충돌해 작가가 경로표를
        # 빼먹는다 (소재 제조사 실측).
        # 흐름표를 내는 장(5장 대응표·7장 경로표)은 스키마 안내를 통째로 «바꾼다».
        # 덧붙이면 기본 안내의 「이 JSON만 출력한다」와 충돌해 작가가 표를
        # 빼먹는다 (소재 제조사 실측).
        FLOW_PROMPT_BY_SECTION.get(section_id, JSON_SCHEMA_GUIDE),
        _render_table(performance_table),
        _render_already_written(already_written),
    ]
    if not shared_evidence_prefix:
        # 기존 순서 그대로 — 이 경로의 결과는 바이트가 예전과 같아야 한다.
        return "".join([header, "\n", *section_parts, fragments_block])
    # 구분자는 원래 쓰던 것만 쓴다: 머리말 뒤의 "\n"은 그 자리에 두고, 조각
    # 블록 뒤에 "\n"을 하나 넣어 장별 지시와 한 줄 띄운다.
    shared_prefix = f"{header}\n{fragments_block}\n"
    return CacheablePrompt(
        shared_prefix + "".join(section_parts),
        cache_prefix_chars=len(shared_prefix),
    )


# ══════════════════════════════════════════════════════════
# 응답 파싱
# ══════════════════════════════════════════════════════════


#: 문장 «글» 안에 흉내낸 인용 표기 — [숫자]·[인용: …]·[조각 …]·(문서 X).
#: ★ 정식 인용은 citations 배열이 유일한 정본이고, 부록(render.py)도 그
#:   배열에서만 만들어진다. 작가 프롬프트가 자료를 「[조각 n]」·「[인용: 1, 2]」
#:   모양으로 보여주므로 모델이 산문 속에도 같은 모양을 흉내 내는 사고가
#:   실재한다(critical 결함 — validate.py의 인용-부록 1:1 검사가 이 숫자를
#:   진짜 인용으로 오인해 유료 실행 전체를 GATE_STOPPED로 죽인다). 이건 내용
#:   검열 게이트가 아니라 출력 «형식» 정리다 — 값을 판단하지 않고 모양만 본다.
#: ★ ``(문서 X)``도 같은 위험이다(2026-09-10 독립 검토 P2) — 조각 줄이
#:   ``[조각 N] (문서 A)`` 모양을 보여주므로, 괄호 안 기호를 산문에 그대로
#:   베낄 수 있다. 실제 렌더는 항상 ASCII 소괄호 ``(문서 {기호})``만 쓰므로
#:   (``_render_fragments``) 이 모양만 좁게 잡는다.
_INLINE_CITATION_MARKER_RE: Final[re.Pattern[str]] = re.compile(
    r"\[(?:\d+|인용\s*:[^\]]*|조각[^\]]*)\]"
    r"|\(문서\s*[A-Z]+\)"
)

#: packet 모드의 구조 citations 우회를 찾는 더 넓은 규칙. legacy flat 응답은
#: 위 정리 규칙을 그대로 써야 하므로 별도 정규식으로 둔다.
_PACKET_INLINE_CITATION_MARKER_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:\[\s*(?:\d{1,3}(?:\s*[,，、]\s*\d{1,3})*|인용\s*[:：][^\]]*|조각[^\]]*)\s*\]"
    r"|[〔【〖〚]\s*(?:\d{1,3}(?:\s*[,，、]\s*\d{1,3})*|인용\s*[:：][^〕】〗〛]*|조각[^〕】〗〛]*)"
    r"\s*[〕】〗〛])"
)


#: 조각 줄의 문서 기호 흉내 — ``(문서 A)``·``(문서 AA)`` 등. 숫자 인용 흉내와
#: 같은 구멍이라 ``contains_inline_citation_marker``의 일반 괄호 스캐너가
#: 같은 자리에서 함께 찾는다(2026-09-10 독립 검토 P2).
_DOCUMENT_SYMBOL_MARKER_RE: Final[re.Pattern[str]] = re.compile(r"문서\s*[A-Z]+")


def _match_citation_group(text: str, start: int) -> Optional[int]:
    """``start`` 위치에서 인용 흉내 «한 조각»을 읽고 끝 위치를 돌려준다.

    두 모양 중 하나만 인정한다 — 1~3자리 숫자(``contains_inline_citation_marker``
    원래 규칙) 또는 「문서」+대문자 기호(``(문서 A)`` 흉내). 어느 쪽도 아니면
    ``None``.
    """
    cursor = start
    while cursor < len(text) and text[cursor].isdecimal():
        cursor += 1
    if 1 <= cursor - start <= 3:
        return cursor
    symbol_match = _DOCUMENT_SYMBOL_MARKER_RE.match(text, start)
    if symbol_match is not None:
        return symbol_match.end()
    return None


def contains_inline_citation_marker(text: str) -> bool:
    """NFKC 뒤 모든 Ps/Pe 혼합 괄호의 인용 흉내(숫자 또는 문서 기호)를 찾는다.

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
            matched_end = _match_citation_group(normalized, start)
            if matched_end is None:
                break
            cursor = matched_end
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


def extract_json_payload(
    raw: str, *, observe: Optional[dict] = None
) -> Optional[Any]:
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
        observe: 주면 «어떻게 읽었는지»만 적는다(추출방식·자른 offset).
            내용은 담지 않으며 반환값과 동작에 영향이 없다.

    Returns:
        JSON으로 읽힌 값(보통 dict). 못 읽으면 None.
        ★ ``null`` 은 «적법하게 읽혀 None» 이다. 못 읽은 것과 구분하려면
          ``observe['추출방식']`` 을 함께 보아야 한다.
    """
    text = (raw or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    else:
        if observe is not None:
            observe["추출방식"] = EXTRACT_DIRECT
        return payload
    # 코드 펜스(```json … ```)나 머리말이 붙은 경우: 첫 «{»부터 마지막 «}»까지만 다시 시도
    start, end = text.find("{"), text.rfind("}")
    if observe is not None:
        observe["json시작offset"] = start
        observe["json끝offset"] = end
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    if observe is not None:
        observe["추출방식"] = EXTRACT_SLICED
    return payload


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
    # ★ 빈 칸을 허용한다 (제품 결정). 예전에는 한 칸이라도
    #   비면 줄을 버렸는데, 8장 「확인된 사례」처럼 «없을 수 있는» 칸 때문에
    #   쓸 만한 줄이 통째로 사라졌다. 다만 «전부» 빈 줄은 아무 말도 하지
    #   않으므로 그때만 버린다.
    if not any(cell for cell in cells):
        return None
    # ★ 칸 글자 수 상한을 없앴다 (제품 결정). 24자는 너무 빡빡했다 —
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
            news_decisions=parse_news_decisions(extract_json_payload(raw)),
        )
    return ComposedSection(
        section_id=section_id,
        sentences=sentences,
        notice="",
        flow_rows=flow_rows,
        news_decisions=parse_news_decisions(extract_json_payload(raw)),
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
            financial_api_disclosed_at=fragment.financial_api_disclosed_at,
            document_identity=fragment.document_identity,
            document_content_sha256=fragment.document_content_sha256,
            supported_claim_slots=fragment.supported_claim_slots,
            formal_source_kind=fragment.formal_source_kind,
            source_document_id=fragment.source_document_id,
            source_publisher=fragment.source_publisher,
            identity_binding=fragment.identity_binding,
            source_collected_on=fragment.source_collected_on,
            domain_attestation_source_id=fragment.domain_attestation_source_id,
            domain_attestation_evidence=fragment.domain_attestation_evidence,
            reporting_period=fragment.reporting_period,
            attachment_url=fragment.attachment_url,
            ir_metadata_verification=fragment.ir_metadata_verification,
            domain_redirect_verification=fragment.domain_redirect_verification,
            domain_redirect_from_host=fragment.domain_redirect_from_host,
            domain_redirect_to_host=fragment.domain_redirect_to_host,
            bound_source=fragment.bound_source,
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
    program_evidence_by_section = {
        packet.section_id: packet.program_evidence
        for packet in typed_packets.packets
        if packet.program_evidence is not None
    } if typed_packets is not None else {}
    program_facts = tuple(
        fact
        for section_id in SECTION_IDS
        for fact in (
            program_evidence_by_section[section_id].facts
            if section_id in program_evidence_by_section
            else ()
        )
    )
    program_sources_by_id: dict[str, object] = {}
    for section_id in SECTION_IDS:
        evidence = program_evidence_by_section.get(section_id)
        if evidence is None:
            continue
        for source in evidence.registry_sources:
            source_id = str(getattr(source, "source_id", "") or "")
            previous = program_sources_by_id.get(source_id)
            if previous is not None and previous != source:
                raise ValueError("프로그램 Source ID가 서로 다른 출처를 가리킵니다")
            program_sources_by_id[source_id] = source
    program_sentences = tuple(
        sentence
        for section_id in SECTION_IDS
        for sentence in (
            program_evidence_by_section[section_id].sentences
            if section_id in program_evidence_by_section
            else ()
        )
    )
    return _PreparedSectionEvidencePackets(
        packets=normalized_packets,
        allowed_fragment_ids_by_section=allowed_fragment_ids_by_section,
        supported_claim_slots_by_fragment_id={
            fragment.fragment_id: frozenset(fragment.supported_claim_slots)
            for fragment in flat_union
        },
        flat_union=flat_union,
        program_evidence_by_section=program_evidence_by_section,
        program_facts=program_facts,
        program_sources=tuple(program_sources_by_id.values()),
        program_sentences=program_sentences,
        # Mapping packet과 slot 메타데이터가 생기기 전의 legacy PacketSet은
        # exact 의미 칸을 알 수 없다. 빈 집합을 종류 이름으로 추측하지 않고
        # 호환 동작을 유지한다. formal typed slot이 실제로 하나라도 들어온 새
        # FULL 계약에서만 전체 산문·표 결속을 강제한다.
        enforce_claim_slot_support=(
            typed_packets is not None
            and any(fragment.supported_claim_slots for fragment in flat_union)
        ),
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


def record_flow_row_counts(
    diagnostics: Optional[list[dict]],
    report: ComposedReport,
    *,
    stage: str,
) -> None:
    """장별 도식 행 수를 «개수»만 단계 기록으로 남긴다.

    ★ 왜 두 번 부르나 — 도식 0줄의 원인은 둘 중 하나다. ① 작가가 애초에 빈
      배열을 냈다 ② 우리가 걸렀다. 「작성」 직후와 「장근거정리」 직후를 함께
      남기면 그 둘이 구분된다. 예전에는 «검증» 제외 기록만 있었고 그 기록은
      정리 필터 «뒤»에 있어서, 여기서 사라진 줄은 어디에도 남지 않았다.
    ⚠️ 칸 내용·인용 id·회사 원문은 남기지 않는다 — 장 이름과 개수만.
    """

    if diagnostics is None:
        return
    diagnostics.append({
        "step": DIAGRAM_ROW_COUNT_STEP,
        "단계": stage,
        "장별행수": {
            section.section_id: len(section.flow_rows)
            for section in report.sections
        },
    })


def _sanitize_report_to_section_evidence(
    report: ComposedReport,
    allowed_fragment_ids_by_section: Mapping[str, frozenset[str]],
    *,
    supported_claim_slots_by_fragment_id: Mapping[str, frozenset[str]] | None = None,
    enforce_claim_slot_support: bool = False,
) -> ComposedReport:
    """장 밖 조각을 인용한 본문·도식 줄을 검수 AI 전에 제외한다.

    해석 문장의 빈 인용은 빈 집합이므로 허용한다. 요약은 장별 경계가 아니라
    전체 보고서의 합집합을 쓰므로 이 함수에서 건드리지 않는다.
    """

    supported_by_id = supported_claim_slots_by_fragment_id or {}

    def claim_slot_is_supported(
        sentence: ComposedSentence, *, section_id: str
    ) -> bool:
        # 프로그램이 만든 structured claim은 별도 raw evidence 계약으로 이미
        # 결속된다. 여기서는 AI가 계획한 산문 claim만 조각의 typed slot과 맞춘다.
        if sentence.structured_claim is not None:
            return True
        # FULL에서는 «확인»뿐 아니라 공식 근거에서 의미를 읽는 «해석»도 어느
        # 의미 칸의 어느 조각에 기대는지 밝혀야 한다. 파서가 누락·미등록 id를
        # 빈칸으로 정규화하므로, 빈칸을 허용하면 두 경우 모두 이 검사를 우회한다.
        if sentence.planned_claim_slot not in CLAIM_SLOTS_BY_SECTION.get(
            section_id, ()
        ):
            return False
        return any(
            sentence.planned_claim_slot in supported_by_id.get(citation, frozenset())
            for citation in sentence.citations
        )

    def flow_row_is_supported(*, section_id: str, row: FlowRow) -> bool:
        requirements = _FLOW_CELL_SUPPORTED_SLOTS_BY_SECTION.get(section_id)
        if requirements is None:
            # 새 AI 표를 정책에 등록하지 않고 FULL로 공개하지 않는다.
            return False
        cited_slots = frozenset().union(
            *(supported_by_id.get(citation, frozenset()) for citation in row.citations)
        )
        return all(
            not cell or bool(required_slots & cited_slots)
            for cell, required_slots in zip(row.cells, requirements, strict=True)
        )

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
            and (
                not enforce_claim_slot_support
                or claim_slot_is_supported(sentence, section_id=section.section_id)
            )
        )
        flow_rows = tuple(
            row
            for row in section.flow_rows
            if set(row.citations).issubset(allowed)
            and not any(contains_inline_citation_marker(cell) for cell in row.cells)
            and (
                not enforce_claim_slot_support
                or flow_row_is_supported(section_id=section.section_id, row=row)
            )
        )
        notice = section.notice
        if section.sentences and not sentences and not notice:
            slot_rejected = enforce_claim_slot_support and any(
                set(sentence.citations).issubset(allowed)
                and not contains_inline_citation_marker(sentence.text)
                and not claim_slot_is_supported(
                    sentence, section_id=section.section_id
                )
                for sentence in section.sentences
            )
            notice = (
                _NOTICE_UNSUPPORTED_CLAIM_SLOTS
                if slot_rejected
                else _NOTICE_OUTSIDE_PACKET_CITATIONS
            )
        sections.append(
            ComposedSection(
                section_id=section.section_id,
                sentences=sentences,
                notice=notice,
                flow_rows=flow_rows,
                news_decisions=tuple(decision for decision in section.news_decisions
                    if decision[0] in allowed_fragment_ids_by_section[section.section_id]),
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
    composition_diagnostics: Optional[list[dict]] = None,
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
        composition_diagnostics: 주어지면 장별 도식 «행 수»를 작성 직후와 장
            근거 정리 직후에 각각 남긴다(원문·칸 내용은 남기지 않는다).

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
                show_supported_claim_slots=(
                    prepared is not None
                    and prepared.enforce_claim_slot_support
                ),
                # flat 모드만 아홉 장이 «같은» 조각 전체를 본다 — 조각 블록을
                # 앞으로 옮기면 앞부분이 장마다 같아져 캐시가 맞는다.
                # packet 모드는 장마다 조각이 달라 공유 앞부분이 아예 없다.
                # 켜 봐야 캐시 «쓰기» 할증만 물고 읽기가 없어 손해다.
                shared_evidence_prefix=prepared is None,
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
    # 작가 응답을 읽은 «직후» 줄 수 — 아래 정리에서 사라진 줄과 구분하기 위해
    # 반드시 정리 «전»에 남긴다.
    record_flow_row_counts(
        composition_diagnostics, report, stage=DIAGRAM_STAGE_PARSED
    )
    if prepared is None:
        return report
    sanitized = _sanitize_report_to_section_evidence(
        report,
        prepared.allowed_fragment_ids_by_section,
        supported_claim_slots_by_fragment_id=(
            prepared.supported_claim_slots_by_fragment_id
        ),
        enforce_claim_slot_support=prepared.enforce_claim_slot_support,
    )
    record_flow_row_counts(
        composition_diagnostics, sanitized, stage=DIAGRAM_STAGE_SECTION_EVIDENCE
    )
    return sanitized


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
                show_supported_claim_slots=prepared.enforce_claim_slot_support,
                # packet 모드라 장마다 조각이 다르다 — 공유 앞부분이 없으므로
                # 캐시 표식을 켜지 않는다(기본값 False 유지).
            ),
            ask,
            reject_inline_citation_markers=True,
            parse_retry_limit=0,
        )
        sections.append(section)
    return _sanitize_report_to_section_evidence(
        ComposedReport(sections=tuple(sections), summary=()),
        prepared.allowed_fragment_ids_by_section,
        supported_claim_slots_by_fragment_id=(
            prepared.supported_claim_slots_by_fragment_id
        ),
        enforce_claim_slot_support=prepared.enforce_claim_slot_support,
    )


# ══════════════════════════════════════════════════════════
# 핵심 요약 (소단계 3-3) — 검증된 본문 문장 중에서 «고른다»
# ══════════════════════════════════════════════════════════
#
# ★ 왜 «쓰기»가 아니라 «고르기»인가 (2026-09-11 실측) — 예전에는 본문을
#   재료로 요약을 새로 쓰게 했다. 그러면 새로 쓴 문장은 어느 본문 사실에도
#   축자로 맞지 않아 결속(bound_summary_fact_id)이 붙지 않는다. 그래서
#   ① 결속을 요구하는 실행(4차 멀티캠퍼스 run e193846f)에서는 초안 4문장이
#      검수·수치 안전 검사에 전부 지워져(수치검사후수 0) 본문 재활용으로
#      되돌아갔고, 살려 둔 작성·검수 두 호출 40.39원이 최종 요약에 0문장
#      기여했다.
#   ② 결속을 요구하지 않는 실행(2차 인텍에프에이)에서는 결속 없는 AI 요약
#      2건이 그대로 출고됐다.
#   두 결말은 같은 모순의 양면이다 — 작성기는 축자 재사용을 «재탕»으로 버리고
#   결속기는 축자만 인정한다. 그래서 «고른다»로 바꾼다. 고른 문장은 본문
#   문장 그 자체이므로 요약은 «그 본문 문장의 결속과 같은 수준»으로
#   결속되고, 새 문장이 없으니 요약을 다시 검수할 이유도 없다.
#   ⚠️ 「구성상 보장」은 과한 말이다 (2026-09-11 독립 검토) — 본문 문장이
#     FactRecord를 못 만든 실행에서는 요약도 결속 0건이다. 나빠지지는
#     않지만(요약은 늘 본문과 같은 수준), 결속을 새로 만들어 주지도 않는다.

SUMMARY_PROMPT_HEADER: Final[str] = (
    "당신은 «공식 근거 기반 기업분석 보고서»의 본문을 모두 읽고, "
    "보고서 맨 앞에 실릴 «핵심 요약»에 넣을 문장을 «고른다».\n"
    "문장을 새로 쓰지 않는다 — 아래 후보의 «번호»만 고른다.\n"
    "지원 직무·채용공고·지원자 정보는 주어지지 않았다. 개인이나 직무에 맞춘 "
    "문장을 고르지 마라.\n"
)

#: 응답이 객체 모양으로 올 때 번호 배열을 담는 키.
SUMMARY_SELECTION_NUMBERS_KEY: Final[str] = "번호"

#: 고르는 규칙 — 무엇을 우선하는지만 말하고, 글자를 만들라고 하지 않는다.
#:
#: ★ 4항이 뒤집힌 근거 (2026-09-11 독립 검토 P2-1) — 예전에는 「수치나 고유명사가
#:   들어 있는 문장을 먼저」였다. 그 지시는 출고 계약
#:   (`docs/출력물 기준/00_핵심_요약/README.md` 조사 절차 3·제외 기준)과 정반대다.
#:   계약은 「같은 장에서는 숫자 없는 문장을 먼저 고르되, 숫자 문장뿐이면 글자
#:   변경 없이 허용」이고 「구체적인 날짜·실행 사례·출처 번호의 재기재」를 막는다.
#:   게다가 골든 fixture 실측에서 본문 52문장 중 숫자 문장 15건이 «전부» 후보에서
#:   빠져(결속 없는 숫자 문장은 요약 잣대를 못 넘는다) 그 지시는 공회전했다.
SUMMARY_SELECTION_RULES_GUIDE: Final[str] = (
    "고르는 규칙:\n"
    f"1. 아래 후보 문장 중 {SUMMARY_MIN_SENTENCES}~"
    f"{SUMMARY_MAX_SENTENCES}개의 번호를 고른다.\n"
    "2. 회사를 처음 보는 취업준비생에게 가장 중요한 순서로 고른다.\n"
    "3. 한 장에서 하나씩만 고른다 — 같은 장에서 둘 이상 고르지 않는다.\n"
    "4. 숫자가 없는 문장을 먼저 고른다. 그 장에 숫자 없는 문장이 없을 때만 "
    "숫자가 든 문장을 고른다. 무엇을 하는 회사인지·수익 구조·최근 변화·"
    "과제와 대응·성장 방향·동종업계와 비교해 확인된 차이처럼 회사를 처음 "
    "보는 독자에게 핵심인 사실을 장별로 하나씩 고른다.\n"
    "5. 문장을 고치거나 새로 쓰지 않는다. 번호만 답한다.\n"
)

#: 출력 형식 — 번호 배열 하나뿐이라 출력 토큰이 아주 작다.
#:
#: ★ 객체 모양으로 바꾼 근거 (2026-09-11 독립 검토 P1) — 예전에는 «맨 배열»
#:   `[1, 4, 7]`을 요구했다. 그런데 공용 회수기(`extract_json_payload`)는 코드
#:   펜스·머리말이 붙으면 «첫 { ~ 마지막 }»만 잘라 내므로 중괄호가 없는 배열은
#:   한 번도 회수되지 않는다(실측: "```json\n[1, 4, 7]\n```" → 번호 0개).
#:   그러면 유료 호출 1회가 통째로 버려지고 규칙 보충으로 되돌아간다.
#:   안내문을 객체형으로 바꾸고, 파서는 두 모양을 «둘 다» 읽는다.
SUMMARY_SELECTION_JSON_GUIDE: Final[str] = (
    "출력 형식 — 설명·머리말 없이 고른 번호만 담은 JSON 하나만 출력한다:\n"
    f'{{"{SUMMARY_SELECTION_NUMBERS_KEY}": [1, 4, 7]}}\n'
)

SUMMARY_CANDIDATE_HEAD: Final[str] = (
    "\n고를 수 있는 본문 문장 (번호. [장 이름] 문장):\n"
)


def _normalized_text(text: str) -> str:
    """공백 차이만 지운 비교용 형태를 만든다.

    ★ «글자 그대로 재탕»만 잡기 위한 정규화다 — 어휘·마커·어미를 보는
      내용 검사가 아니다(닫힌 게이트 금지 원칙 준수).
    """
    return " ".join(text.split())


@dataclass(frozen=True)
class SummaryCandidate:
    """요약에 실어도 된다고 판정된 본문 문장 하나와 그 문장이 실린 장."""

    section_id: str
    sentence: ComposedSentence


def summary_candidates(
    report: ComposedReport,
    *,
    accept: Optional[Callable[[ComposedSentence], bool]] = None,
) -> tuple[SummaryCandidate, ...]:
    """요약 후보를 본문 순서대로 모은다 — 같은 문장은 한 번만 담는다.

    ``accept``: «요약 잣대»를 통과하는지 보는 술어. 부르는 쪽이 수치 안전
    검사와 «같은» 술어를 넘겨, 통과하지 못할 문장이 애초에 후보로 나가지
    않게 한다. 예전에는 요약을 다 만든 «뒤»에 이 검사를 걸어서, 걸러진
    자리를 규칙 보충이 메우고 그 보충분이 또 걸리는 일이 반복됐다.
    """

    candidates: list[SummaryCandidate] = []
    seen: set[str] = set()
    for section in report.sections:
        for sentence in section.sentences:
            key = _normalized_text(sentence.text)
            if not key or key in seen:
                continue
            if accept is not None and not accept(sentence):
                continue
            seen.add(key)
            candidates.append(SummaryCandidate(section.section_id, sentence))
    return tuple(candidates)


def build_summary_selection_prompt(
    candidates: Sequence[SummaryCandidate],
) -> str:
    """고를 후보를 «번호 + 장 이름 + 문장»으로 나열한 지시문을 만든다."""

    parts = [
        SUMMARY_PROMPT_HEADER,
        "\n",
        SUMMARY_SELECTION_RULES_GUIDE,
        FORBIDDEN_TOPICS_GUIDE,
        SUMMARY_SELECTION_JSON_GUIDE,
        SUMMARY_CANDIDATE_HEAD,
    ]
    for number, candidate in enumerate(candidates, start=1):
        title = SECTION_TITLES.get(candidate.section_id, candidate.section_id)
        parts.append(f"{number}. [{title}] {candidate.sentence.text}\n")
    return "".join(parts)


#: 번호로 읽어 줄 문자열의 최대 자릿수. 후보는 많아야 수백 개라 이보다 긴
#: 숫자는 번호가 아니며, 아주 긴 숫자 문자열을 int()에 넣으면 파이썬 자체가
#: 자릿수 상한으로 예외를 던진다.
_SUMMARY_NUMBER_MAX_DIGITS: Final[int] = 6


def _selection_number(item: Any) -> Optional[int]:
    """응답 항목 하나를 후보 번호로 읽는다. 번호가 아니면 None."""

    if isinstance(item, bool):
        # bool은 int의 하위형이라 True가 1번으로 읽힌다 — 먼저 막는다.
        return None
    if isinstance(item, int):
        return item
    if isinstance(item, str):
        text = item.strip()
        if text.isdigit() and len(text) <= _SUMMARY_NUMBER_MAX_DIGITS:
            return int(text)
    return None


def _fence_stripped(raw: str) -> str:
    """코드 펜스(```json … ```)만 걷어낸 본문을 돌려준다.

    펜스가 없으면 공백만 다듬어 그대로 돌려준다. 글자를 해석하지 않는다.
    """

    text = (raw or "").strip()
    if not text.startswith("```"):
        return text
    body = text[3:]
    newline = body.find("\n")
    if newline >= 0:
        # 첫 줄의 언어 표시(```json 등)를 버린다.
        body = body[newline + 1 :]
    body = body.rstrip()
    if body.endswith("```"):
        body = body[:-3]
    return body.strip()


def _bracketed_array_payload(raw: str) -> Optional[Any]:
    """응답 «전체»가 맨 배열일 때만 그 배열을 회수한다.

    ★ 왜 여기서 따로 회수하나 (2026-09-11 독립 검토 P1) — 공용 회수기
      `extract_json_payload`는 펜스가 붙으면 «첫 { ~ 마지막 }»만 자른다. 배열에는
      중괄호가 없어 한 번도 회수되지 않았다. 공용 함수에 «[ ~ ]» 자르기를 더하면
      장별 응답·검수 응답까지 영향을 받으므로, 번호만 읽는 이 자리에만 둔다.
    ★ 왜 «전체»만 인정하나 (2026-09-11 재검토 P3-3) — 처음에는 첫 «[»부터 마지막
      «]»까지 잘랐다. 그러면 산문 속 대괄호가 번호가 된다 — "본문 [3] 문단을
      참고했습니다" 가 번호 (3,)으로 읽혔다. 고를 수 있는 것이 검증된 본문
      문장뿐이라 안전 문제는 아니지만, 「못 읽었다」가 「하나 골랐다」로 기록돼
      단계 진단의 초안수가 0 대신 1이 되고 원인 판별이 흐려진다.
    ★ 자른 뒤에도 «JSON으로 읽히는가»만 본다. 글자를 해석하지 않는다.
    """

    text = _fence_stripped(raw)
    if not text.startswith("[") or not text.endswith("]"):
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _selection_numbers_payload(raw: str) -> Optional[Any]:
    """응답에서 번호 배열이 될 수 있는 값을 꺼낸다 — 객체형·맨 배열 둘 다."""

    payload = extract_json_payload(raw)
    if isinstance(payload, Mapping):
        keyed = payload.get(SUMMARY_SELECTION_NUMBERS_KEY)
        if keyed is not None:
            return keyed
        # 객체는 왔는데 번호 칸이 없다 — 펜스 안의 맨 배열을 한 번 더 본다.
        payload = None
    if payload is None:
        return _bracketed_array_payload(raw)
    return payload


def parse_summary_selection(raw: str, candidate_count: int) -> tuple[int, ...]:
    """응답에서 «후보 번호»만 읽는다 — 그 밖의 것은 전부 버린다.

    객체형 ``{"번호": [1, 4, 7]}``와 맨 배열 ``[1, 4, 7]``을 모두 읽으며,
    코드 펜스나 머리말이 붙어 있어도 읽는다. 안내문이 요구하는 모양은
    객체형이지만, 모델이 둘 중 어느 쪽으로 답해도 호출이 버려지지 않는다.

    ★ 글자를 읽지 않는다. 범위 밖 번호·중복·JSON 아님은 모두 «그만큼 못
      골랐다»로 처리하고, 모자란 자리는 부르는 쪽의 규칙 보충이 메운다.
      응답이 문장을 담아 와도 그 글자는 보고서에 실리지 않는다.
    """

    payload = _selection_numbers_payload(raw)
    if isinstance(payload, (str, bytes)) or not isinstance(payload, Sequence):
        return ()
    numbers: list[int] = []
    for item in payload:
        number = _selection_number(item)
        if number is None or not 1 <= number <= candidate_count:
            continue
        if number in numbers:
            continue
        numbers.append(number)
        if len(numbers) >= SUMMARY_MAX_SENTENCES:
            break
    return tuple(numbers)


def _by_section_rounds(
    pools: Sequence[Sequence[ComposedSentence]],
) -> tuple[ComposedSentence, ...]:
    """장을 번갈아 도는 순서를 만들되 «각 장의 첫 문장»을 마지막으로 돌린다.

    ★ 왜 첫 문장을 뒤로 미루나 (실측) — 예전에는 한 바퀴에 장마다 «첫» 문장을
      집었다. 그래서 이 순서가 요약 보충에 쓰일 때마다 요약이 「1·2·3장 첫
      문장」이라는 똑같은 서명으로 나왔다. 실측 실행의 요약 3건이 정확히
      본문 2장·3장·1장의 첫 문장과 축자 동일했다. 장마다 쓸 만한 다른 문장이
      있으면 그것부터 쓰고, 한 문장뿐인 장에서만 그 첫 문장을 쓴다.
    ★ 순서만 바뀌고 «쓸 수 있는 문장 집합»은 그대로다 — 요약이 빌 위험은 0이다.
    ★ 장을 «번갈아» 도는 성질은 그대로다. 첫 문장을 통째로 뒤로 몰지 않고
      장마다 «시작점만» 한 칸 옮긴다(둘째 문장부터 돌고 첫 문장이 그 장의
      마지막 차례). 통째로 뒤로 몰면 문장이 많은 한 장에서 세 개를 연달아
      집어 「서로 다른 장 우선」이 깨진다 — 실측으로 확인한 자리다.
    ★ 이 순서를 만드는 곳은 여기 한 곳이다. 예전에는 pipeline 쪽에 같은 뜻의
      정렬이 한 벌 더 있었는데, 그 벌은 앞선 위임이 이미 최소 문장 수를
      채워 버려 «한 번도 실행되지 않는» 죽은 코드였다.
    """

    rotated = [list(pool[1:]) + list(pool[:1]) for pool in pools]
    deepest = max((len(pool) for pool in rotated), default=0)
    ordered: list[ComposedSentence] = []
    for round_index in range(deepest):
        for pool in rotated:
            if round_index < len(pool):
                ordered.append(pool[round_index])
    return tuple(ordered)


def _confirmed_by_section_rounds(
    report: ComposedReport,
) -> tuple[ComposedSentence, ...]:
    """본문의 «확인» 문장을 «서로 다른 장 우선» 순서로 편다."""
    return _by_section_rounds([
        [s for s in section.sentences if s.grade == GRADE_CONFIRMED]
        for section in report.sections
    ])


def _any_grade_by_section_rounds(
    report: ComposedReport,
) -> tuple[ComposedSentence, ...]:
    """등급을 가리지 않은 본문 문장을 같은 «서로 다른 장 우선» 순서로 편다.

    검수가 전역 실패하면 모든 문장이 «해석»으로 강등돼 «확인» 문장이 0개일 수
    있다. 그때도 요약을 비우지 않기 위한 마지막 재료이며, 순서 규칙은 확인
    문장 경로와 «같은 함수»를 쓴다.
    """
    return _by_section_rounds([
        list(section.sentences) for section in report.sections
    ])


def _fill_summary(
    summary: Sequence[ComposedSentence],
    ordered: Sequence[ComposedSentence],
    *,
    excluded_keys: frozenset[str],
    accept: Optional[Callable[[ComposedSentence], bool]] = None,
    section_by_key: Optional[Mapping[str, str]] = None,
) -> tuple[ComposedSentence, ...]:
    """이미 고른 요약에 후보를 순서대로 채워 최소 문장 수를 맞춘다.

    ``accept``: 후보가 «요약 잣대»를 통과하는지 보는 술어. 통과하지 못한
    후보는 건너뛰고 다음 후보로 간다.

    ``section_by_key``: 정규화 본문 → 그 문장을 소유한 장. 주면 «장당 최대
    1개» 계약(`docs/출력물 기준/00_핵심_요약/README.md` 조사 절차 4)을
    여기서도 지킨다. 이미 쓴 장의 문장은 건너뛴다. 안 주면 예전 동작 그대로다.

    ★ 왜 필요한가 (실측) — 본문 잣대와 요약 잣대가 다르다. 본문에 남아 있는
      문장이라고 요약에 실을 수 있는 것은 아니다. 술어 없이 채우면 방금
      수치 안전 검사가 뺀 «그» 문장만 피하고, 같은 이유로 빠졌어야 할 다른
      본문 문장이 그대로 들어온다(운영 진입점에서 최종 3건 전부가 그런
      문장이 되는 경우를 재현했다).
    ★ 후보가 바닥나면 기존 안전선 그대로 — 최소 문장 수에 못 미쳐도 억지로
      채우지 않는다. 빈 요약 차단 방지는 등급 무관 보충이 계속 맡는다.
    """

    chosen: list[ComposedSentence] = list(summary)
    seen = {_normalized_text(sentence.text) for sentence in chosen}
    used_sections: set[str] = set()
    if section_by_key is not None:
        used_sections = {
            section_by_key[key] for key in seen if key in section_by_key
        }
    for candidate in ordered:
        if len(chosen) >= SUMMARY_MIN_SENTENCES:
            break
        key = _normalized_text(candidate.text)
        if not key or key in seen or key in excluded_keys:
            continue
        if accept is not None and not accept(candidate):
            continue
        section_id = ""
        if section_by_key is not None:
            section_id = section_by_key.get(key, "")
            if section_id and section_id in used_sections:
                continue
        chosen.append(candidate)
        seen.add(key)
        if section_id:
            used_sections.add(section_id)
    return tuple(chosen)


def _supplement_summary(
    summary: Sequence[ComposedSentence],
    report: ComposedReport,
    *,
    excluded_keys: frozenset[str] = frozenset(),
    accept: Optional[Callable[[ComposedSentence], bool]] = None,
    section_by_key: Optional[Mapping[str, str]] = None,
) -> tuple[ComposedSentence, ...]:
    """요약이 최소 문장 수에 못 미치면 본문 «확인» 문장으로 보충한다.

    ★ 본문 재사용이 허용되는 유일한 경로다(정본 기준) —
      빈 요약으로 인한 차단을 만들지 않기 위해서다.

    ``excluded_keys``: 되돌려 넣으면 «안 되는» 문장의 정규화 본문. 앞 단계가
    안전 검사로 뺀 문장이 이 보충으로 되살아나는 자리를 막는다.
    """
    return _fill_summary(
        summary, _confirmed_by_section_rounds(report),
        excluded_keys=excluded_keys, accept=accept,
        section_by_key=section_by_key,
    )


def _supplement_summary_any_grade(
    summary: Sequence[ComposedSentence],
    report: ComposedReport,
    *,
    excluded_keys: frozenset[str] = frozenset(),
    accept: Optional[Callable[[ComposedSentence], bool]] = None,
    section_by_key: Optional[Mapping[str, str]] = None,
) -> tuple[ComposedSentence, ...]:
    """«확인» 문장이 모자랄 때 등급을 가리지 않고 같은 순서로 보충한다."""
    return _fill_summary(
        summary, _any_grade_by_section_rounds(report),
        excluded_keys=excluded_keys, accept=accept,
        section_by_key=section_by_key,
    )


def select_summary_sentences(
    candidates: Sequence[SummaryCandidate],
    ask: AskFn,
) -> tuple[ComposedSentence, ...]:
    """후보 중에서 AI가 고른 문장을 «글자 그대로» 돌려준다 (AI 호출 1회).

    돌려주는 것은 후보로 받은 그 ``ComposedSentence`` 객체다 — 글자·인용·
    등급·구조화 사실이 본문과 완전히 같으므로, 요약은 «그 본문 문장의 결속과
    같은 수준»으로 결속된다. 본문 문장이 결속되지 않은 실행에서는 요약도
    결속되지 않는다(그때도 본문보다 느슨해지지는 않는다).

    ★ «장당 최대 1개»는 프롬프트가 아니라 여기서 지킨다 (계약
      `docs/출력물 기준/00_핵심_요약/README.md` 조사 절차 4). AI가 한 장에서
      둘 이상 고르면 그 장의 첫 번호만 남기고 나머지는 버린다 — 빈자리는
      부르는 쪽의 규칙 보충이 «다른 장»에서 채운다. 지시만으로 두면 실측처럼
      한 장에서 세 개가 그대로 실린다(1장 후보 3개에 [1,2,3] 응답 재현).
    ★ 재요청하지 않는다. 응답이 번호가 아니면 «그만큼 못 골랐다»로 두고
      부르는 쪽의 규칙 보충이 메운다 — 번호 하나 받자고 호출을 한 번 더
      쓰는 것보다, 검증된 본문 문장으로 채우는 편이 결과가 같고 싸다.

    Args:
        candidates: 요약 잣대를 이미 통과한 본문 문장들.
        ask: 프롬프트 문자열 → 응답 문자열 주입 함수 (시험은 가짜 함수 사용).

    Returns:
        AI가 고른 본문 문장들(장마다 최대 하나). 재료가 없거나 못 골랐으면
        빈 튜플.

    Raises:
        AskFatalError: 요청 전역 장애(예산 소진·한도)는 삼키지 않고 그대로
            올린다 — 부르는 쪽이 «한도 도달»로 기록하고 규칙 보충으로 간다.
    """

    if not candidates:
        # 재료가 없으면 헛호출하지 않는다.
        return ()
    prompt = build_summary_selection_prompt(candidates)
    try:
        raw = ask(prompt)
    except AskFatalError:
        raise
    except Exception as error:  # noqa: BLE001 - 요약 실패가 보고서를 멈추면 안 된다
        # ⚠️ 예외 «메시지»는 남기지 않는다 — provider 응답 본문이 섞일 수 있다.
        logger.warning("요약 선택 실패(삼킴) kind=%s", type(error).__name__)
        return ()
    numbers = parse_summary_selection(str(raw), len(candidates))
    if not numbers:
        logger.warning(
            "요약 선택 응답에서 후보 번호를 하나도 읽지 못했다 — "
            "검증된 본문 문장으로 채운다"
        )
    chosen: list[ComposedSentence] = []
    used_sections: set[str] = set()
    dropped = 0
    for number in numbers:
        candidate = candidates[number - 1]
        if candidate.section_id in used_sections:
            dropped += 1
            continue
        used_sections.add(candidate.section_id)
        chosen.append(candidate.sentence)
    if dropped:
        logger.warning(
            "요약 선택이 한 장에서 %d개를 더 골라 버렸다 — 장당 하나만 싣고 "
            "나머지는 다른 장의 검증된 본문 문장으로 채운다",
            dropped,
        )
    return tuple(chosen)
