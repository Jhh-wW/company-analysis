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
from collections.abc import Mapping, Sequence
from typing import Any, Callable, Final, Optional, Union

from src.features.composer.constants import (
    CITATION_RULES_GUIDE,
    FORBIDDEN_TOPICS_GUIDE,
    GRADE_CONFIRMED,
    GRADE_INTERPRETED,
    JSON_SCHEMA_GUIDE,
    NOTICE_COMPOSE_FAILED,
    NOTICE_INSUFFICIENT_EVIDENCE,
    PARSE_RETRY_LIMIT,
    PROMPT_FRAGMENTS_HEAD,
    PROMPT_HEADER,
    PROMPT_TABLE_HEAD,
    RESPONSE_CITATIONS_KEY,
    RESPONSE_GRADE_KEY,
    RESPONSE_SENTENCES_KEY,
    RESPONSE_TEXT_KEY,
    RETRY_REMINDER,
    SECTION_GUIDES,
    SECTION_IDS,
    SECTION_TITLES,
    SECTION_SENTENCE_RANGES,
    SENTENCE_RANGE_GUIDE,
    VALID_GRADES,
)
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    PerformanceTable,
    fragments_from_raw,
)

#: 프롬프트 문자열을 받아 AI 응답 문자열을 돌려주는 주입 함수
AskFn = Callable[[str], str]

#: compose_sections가 받는 조각 입력 — 파이프라인 원시 dict 또는 어댑터 튜플
FragmentsInput = Union[
    Mapping[int, Mapping[str, Any]], Sequence[CollectedFragment]
]


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


def build_section_prompt(
    company_name: str,
    section_id: str,
    fragments: Sequence[CollectedFragment],
    performance_table: Optional[PerformanceTable],
) -> str:
    """장 하나를 쓰게 하는 지시문 — 지침 + 조각 전체 + 실적표 + JSON 강제."""
    minimum, maximum = SECTION_SENTENCE_RANGES[section_id]
    parts = [
        PROMPT_HEADER.format(company=company_name),
        "\n",
        SECTION_GUIDES[section_id],
        "\n\n",
        CITATION_RULES_GUIDE,
        FORBIDDEN_TOPICS_GUIDE,
        SENTENCE_RANGE_GUIDE.format(minimum=minimum, maximum=maximum),
        JSON_SCHEMA_GUIDE,
        _render_table(performance_table),
        _render_fragments(fragments),
    ]
    return "".join(parts)


# ══════════════════════════════════════════════════════════
# 응답 파싱
# ══════════════════════════════════════════════════════════


def _extract_payload(raw: str) -> Optional[Any]:
    """응답 문자열에서 JSON을 꺼낸다. 코드 펜스·앞뒤 설명이 붙어도 살린다.

    ★ 내용 검사가 아니다 — 「JSON으로 읽히는가」만 본다.
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


def _sentence_from_item(item: Any) -> Optional[ComposedSentence]:
    """항목 하나를 문장으로 바꾼다. 계약(글·인용·등급)이 안 맞으면 None.

    ★ 여기서 보는 것은 «형식»뿐이다 — 글이 비었는가, 등급이 계약된 두 값인가,
      인용이 배열인가. 문장 내용은 일절 검사하지 않는다(닫힌 게이트 금지).
    """
    if not isinstance(item, Mapping):
        return None
    text = str(item.get(RESPONSE_TEXT_KEY) or "").strip()
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
    return ComposedSentence(text=text, citations=citations, grade=grade)


def parse_section_response(raw: str) -> Optional[tuple[ComposedSentence, ...]]:
    """작가 응답을 문장 튜플로 바꾼다.

    Returns:
        - 문장 튜플: 정상 (형식이 깨진 개별 항목은 건너뛴다)
        - 빈 튜플: 작가가 «쓸 문장이 없다»고 정상적으로 답한 경우
        - None: 응답 자체를 읽지 못한 경우 → 호출한 쪽이 1회 재요청한다

    ★ 인용 id는 여기서 «보존만» 한다. 실존하지 않는 id의 처분(문장 제거)은
      소단계 3-2 검증기의 몫이다.
    """
    payload = _extract_payload(raw)
    if not isinstance(payload, Mapping):
        return None
    items = payload.get(RESPONSE_SENTENCES_KEY)
    if not isinstance(items, list):
        return None
    if not items:
        return ()
    sentences = tuple(
        sentence
        for sentence in (_sentence_from_item(item) for item in items)
        if sentence is not None
    )
    # 항목이 있었는데 하나도 못 살렸다면 응답 형식이 통째로 어긋난 것 — 재요청 대상
    if not sentences:
        return None
    return sentences


# ══════════════════════════════════════════════════════════
# 장 단위 생성
# ══════════════════════════════════════════════════════════


def _ask_and_parse(
    ask: AskFn, prompt: str
) -> Optional[tuple[ComposedSentence, ...]]:
    """AI를 부르고 파싱까지. 호출 자체가 죽어도 None으로 삼킨다(전체 중단 금지)."""
    try:
        raw = ask(prompt)
    except Exception:  # noqa: BLE001 - 한 장의 실패가 보고서 전체를 멈추면 안 된다
        return None
    return parse_section_response(str(raw))


def _compose_one_section(
    section_id: str, prompt: str, ask: AskFn
) -> ComposedSection:
    """장 하나를 쓴다. 실패 시 재요청 1회, 그래도 실패면 정직한 안내문으로 남긴다."""
    sentences = _ask_and_parse(ask, prompt)
    retries = 0
    while sentences is None and retries < PARSE_RETRY_LIMIT:
        retries += 1
        sentences = _ask_and_parse(ask, prompt + RETRY_REMINDER)
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
        )
    return ComposedSection(section_id=section_id, sentences=sentences, notice="")


def _normalize_fragments(
    fragments: FragmentsInput,
) -> tuple[CollectedFragment, ...]:
    """원시 dict든 어댑터 튜플이든 같은 모양으로 맞춘다."""
    if isinstance(fragments, Mapping):
        return fragments_from_raw(fragments)
    return tuple(fragments)


def compose_sections(
    company_name: str,
    fragments: FragmentsInput,
    performance_table: Optional[PerformanceTable],
    ask: AskFn,
) -> ComposedReport:
    """9개 장 전부를 작가 AI로 쓴다 — 장마다 1회 호출(파싱 실패 시 +1회).

    Args:
        company_name: 분석 대상 법인 이름.
        fragments: 수집 조각 — real.py의 `dict[int, dict[str, str]]` 그대로,
            또는 `CollectedFragment` 시퀀스.
        performance_table: 프로그램이 검증해 만든 3개년 실적표. 없으면 None.
        ask: 프롬프트 문자열 → 응답 문자열 주입 함수 (시험은 가짜 함수 사용).

    Returns:
        9개 장이 «전부» 들어 있는 ComposedReport. 실패한 장도 삭제하지 않고
        빈 문장 + 안내문으로 남는다. summary는 빈 튜플(소단계 3-3이 채운다).
    """
    normalized = _normalize_fragments(fragments)
    sections = tuple(
        _compose_one_section(
            section_id,
            build_section_prompt(
                company_name, section_id, normalized, performance_table
            ),
            ask,
        )
        for section_id in SECTION_IDS
    )
    return ComposedReport(sections=sections, summary=())


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


def compose_summary(report: ComposedReport, ask: AskFn) -> ComposedReport:
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
    sentences = _ask_and_parse(ask, prompt)
    retries = 0
    while sentences is None and retries < PARSE_RETRY_LIMIT:
        retries += 1
        sentences = _ask_and_parse(ask, prompt + RETRY_REMINDER)
    if sentences is None:
        sentences = ()
    kept, had_duplicate = _split_out_duplicates(sentences, body_keys)
    if had_duplicate:
        # 재탕 검출 → 1회 재요청. 실패하거나 또 전부 재탕이면 1차 생존 문장 유지.
        retry_sentences = _ask_and_parse(
            ask, prompt + SUMMARY_DUPLICATE_REMINDER
        )
        if retry_sentences:
            retry_kept, _ = _split_out_duplicates(retry_sentences, body_keys)
            if retry_kept:
                kept = retry_kept
    summary = kept[:SUMMARY_MAX_SENTENCES]
    if len(summary) < SUMMARY_MIN_SENTENCES:
        summary = _supplement_summary(summary, report)
    return ComposedReport(sections=report.sections, summary=summary)
