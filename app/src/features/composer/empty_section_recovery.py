"""공식 해당 장 근거에 한정한 묶음 작성 한 번과 동일 검수 한 번."""
from __future__ import annotations

import json
import hashlib
import logging
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace

from src.features.composer.constants import (
    CLAIM_SLOTS_BY_SECTION, GRADE_CONFIRMED, PARSE_RETRY_LIMIT, RESPONSE_CLAIM_SLOT_KEY,
    RESPONSE_SENTENCES_KEY, SECTION_IDS, SECTION_GUIDES, SECTION_TITLES,
)
from src.features.composer.culture_constants import SOURCE_CLAUSE_SPLIT_RE
from src.features.composer.culture_guard import _clause_carries_section_subject
from src.features.composer.dedupe import drop_cross_section_duplicates
from src.features.composer.empty_section_recovery_constants import (
    EMPTY_RECOVERY_GUIDE, EMPTY_RECOVERY_RETRY_GUIDE, EMPTY_RECOVERY_STEP,
    EMPTY_RECOVERY_TARGET_IDS_JOINER, EMPTY_RECOVERY_TARGET_IDS_LINE,
    RESPONSE_SECTIONS_KEY, RESPONSE_SHAPE_CONTRACT, RESPONSE_SHAPE_FLAT_SINGLE,
    RESPONSE_SHAPE_NO_TARGET, RESPONSE_SHAPE_UNREADABLE, RESPONSE_SHAPE_UNWRAPPED,
    REQUEST_ORDER_FIRST_NUMBER,
    SECTION_DISPLAY_FIRST_NUMBER, SECTION_DISPLAY_NOISE_RE, SECTION_DISPLAY_ORDINAL_PREFIX,
    SECTION_DISPLAY_UNIT, SECTION_KEY_SEPARATOR_RE,
    MAX_EMPTY_RECOVERY_EVIDENCE_CHARS, MAX_EMPTY_RECOVERY_FRAGMENTS,
    MAX_EMPTY_RECOVERY_SENTENCES, MAX_EMPTY_RECOVERY_SECTIONS,
)
from src.features.composer.logic import AskFn, extract_json_payload, parse_section_response, _strip_inline_citation_markers
from src.features.composer.port import AskFatalError, CollectedFragment, ComposedReport, ComposedSection
from src.features.composer.verify import verify_report
from src.features.composer.structured_claims import enforce_public_numeric_safety
from src.shared.report_evidence.constants import FORMAL_DOCUMENT_SOURCE_KINDS

logger = logging.getLogger(__name__)


def rejected_sentence_fingerprint(text: str) -> str:
    """공백·표기 정규화·인용 표식만 바꾼 재승인을 막는 내부 지문."""
    normalized = unicodedata.normalize("NFKC", _strip_inline_citation_markers(text))
    return hashlib.sha256("".join(normalized.split()).encode("utf-8")).hexdigest()


def recovery_evidence(fragments: Sequence[CollectedFragment]) -> dict[str, tuple[CollectedFragment, ...]]:
    """이미 신원이 결속된 공식 조각의 해당 장 slot만 사용한다."""
    result = {}
    for section_id in SECTION_IDS:
        selected = []
        chars = 0
        for fragment in fragments:
            if (fragment.formal_source_kind not in FORMAL_DOCUMENT_SOURCE_KINDS
                    or not fragment.document_identity or not fragment.document_content_sha256
                    or not fragment.identity_binding or not fragment.text.strip()
                    or not any(slot.startswith(section_id + ":") for slot in fragment.supported_claim_slots)):
                continue
            if section_id == "culture" and not any(
                _clause_carries_section_subject(clause)
                for clause in SOURCE_CLAUSE_SPLIT_RE.split(fragment.text)
            ):
                continue
            if chars + len(fragment.text) > MAX_EMPTY_RECOVERY_EVIDENCE_CHARS:
                continue
            selected.append(fragment)
            chars += len(fragment.text)
            if len(selected) >= MAX_EMPTY_RECOVERY_FRAGMENTS:
                break
        if selected:
            result[section_id] = tuple(selected)
    return result


def _normalized_section_key(value: str) -> str:
    """대소문자·공백·하이픈/밑줄 차이만 지운 장 ID 비교값."""
    return SECTION_KEY_SEPARATOR_RE.sub("", unicodedata.normalize("NFKC", value)).casefold()


def _display_section_key(value: str) -> str:
    """장 번호·제목 표시형 비교값 — 공백·따옴표·괄호·구두점 차이를 지운다."""
    return SECTION_DISPLAY_NOISE_RE.sub("", unicodedata.normalize("NFKC", value)).casefold()


def _section_display_forms() -> dict[str, frozenset[str]]:
    """장 ID마다 작가가 키로 쓸 수 있는 표시형의 비교값.

    번호는 장 순서(SECTION_IDS, 1부터)이고 제목은 SECTION_TITLES다 — 작가가 보는
    작성범위 문구(SECTION_GUIDES)의 «N장 «제목»» 과 같은 값이다(시험이 둘을 대조한다).
    «N장»·«제N장»·«N장 제목»·«제N장 제목»·«제목»·«N. 제목» 을 받는다.
    """
    forms: dict[str, frozenset[str]] = {}
    for number, section_id in enumerate(SECTION_IDS, start=SECTION_DISPLAY_FIRST_NUMBER):
        title = _display_section_key(SECTION_TITLES[section_id])
        chapter, ordinal = _chapter_forms(number)
        forms[section_id] = frozenset((
            chapter, ordinal, chapter + title, ordinal + title, title, f"{number}{title}",
        ))
    return forms


def _chapter_forms(number: int) -> tuple[str, str]:
    """«N장»·«제N장» 비교값 — 표시형 표와 번호만 있는 표가 같은 글자를 쓰게 한 곳에서 만든다."""
    chapter = f"{number}{SECTION_DISPLAY_UNIT}"
    return chapter, f"{SECTION_DISPLAY_ORDINAL_PREFIX}{chapter}"


def _number_only_display_forms() -> dict[str, int]:
    """제목 없이 번호만 있는 표시형(«N장»·«제N장»)의 비교값 → 정본 장 번호.

    ★ 이 꼴만 «요청 순번» 읽기와 겹친다. 작가가 요청한 장을 순서대로 «1장»·«2장»
      이라 부르면 정본 번호의 장과 다른 장을 뜻할 수 있다(독립 검토 2026-09-23 재현:
      요청 business_model·operations_partners 에 «2장» → 정본 2장으로 풀려 7장 사실이
      2장에 실렸다). 제목이 붙은 꼴은 번호와 제목이 서로 맞아야만 걸리므로 여기 없다.
    """
    forms: dict[str, int] = {}
    end_number = SECTION_DISPLAY_FIRST_NUMBER + len(SECTION_IDS)
    for number in range(SECTION_DISPLAY_FIRST_NUMBER, end_number):
        for form in _chapter_forms(number):
            forms[form] = number
    return forms


_SECTION_DISPLAY_FORMS = _section_display_forms()
_NUMBER_ONLY_DISPLAY_FORMS = _number_only_display_forms()


def _number_only_key_number(key: str) -> int | None:
    """번호만 있는 키(«N장»·«제N장»)면 그 정본 번호, 아니면 None."""
    return _NUMBER_ONLY_DISPLAY_FORMS.get(_display_section_key(key))


def _request_order_position(number: int, targets: Sequence[str]) -> int | None:
    """번호를 «요청 순번»으로 읽은 요청 장 위치. 요청 장 수보다 크면 None."""
    position = number - REQUEST_ORDER_FIRST_NUMBER
    return position if 0 <= position < len(targets) else None


def _writes_canonical_numbers(keys: Iterable[object], targets: Sequence[str]) -> bool:
    """응답이 장 번호를 «정본 번호»로 쓰는가 — 순번으로 못 읽는 요청 장 번호가 있나.

    요청 순번으로는 요청 장 수보다 큰 번호가 나올 수 없다. 그런 번호만 있는 키가
    정본 번호로 요청 장에 걸리면 작가가 정본 번호를 쓰는 것이므로, 그 응답의 번호만
    있는 키는 모두 정본대로 읽는다(2026-09-23 탐침: 요청 2·7장에 «2장»·«7장» 답이
    순번 충돌로 2장을 잃었고, 7장이 풀려 재요청도 없었다). 요청 밖 장의 번호는 세지
    않는다 — 요청 순번으로 쓴 «1장»·«2장»에 덤 «3장»이 얹혀도 판별이 켜지면 «2장»이
    다른 장으로 풀린다. 제목이 붙은 키도 세지 않는다.
    """
    for key in keys:
        if not isinstance(key, str):
            continue
        number = _number_only_key_number(key)
        if (number is not None and _request_order_position(number, targets) is None
                and SECTION_IDS[number - SECTION_DISPLAY_FIRST_NUMBER] in targets):
            return True
    return False


def _conflicts_with_request_order(key: str, targets: Sequence[str]) -> bool:
    """번호만 있는 키를 «요청 순번»으로도 읽을 수 있고, 그 장이 정본 번호의 장과 다른가.

    번호가 요청 장 수보다 크면 순번으로 읽을 수 없으므로 충돌이 아니다(요청 2개에
    «4장»·«8장»). 두 읽기가 같은 장이면(요청 1·2장에 «1장»·«2장») 역시 충돌이 아니다.
    """
    number = _number_only_key_number(key)
    if number is None:
        return False
    position = _request_order_position(number, targets)
    if position is None:
        return False
    return targets[position] != SECTION_IDS[number - SECTION_DISPLAY_FIRST_NUMBER]


def _claim_slot_tail_owners() -> dict[str, str]:
    """장 ID를 뗀 칸 이름(꼬리) → 그 꼬리를 가진 장. 두 장 이상에 있는 꼬리는 넣지 않는다.

    정본 표에서는 꼬리가 장끼리 겹치지 않는다(시험이 지키는 불변식).
    """
    owners: dict[str, set[str]] = {}
    for section_id, slots in CLAIM_SLOTS_BY_SECTION.items():
        for slot in slots:
            owners.setdefault(slot.partition(":")[2], set()).add(section_id)
    return {tail: next(iter(sections)) for tail, sections in owners.items()
            if len(sections) == 1}


_CLAIM_SLOT_TAIL_OWNERS = _claim_slot_tail_owners()


def _claim_slot_section(slot: str) -> str | None:
    """주장슬롯 값이 가리키는 장 — «장 ID:칸»이면 콜론 앞 장, 꼬리만이면 그 꼬리의 장."""
    head, separator, _ = slot.partition(":")
    if separator:
        return head if head in SECTION_IDS else None
    return _CLAIM_SLOT_TAIL_OWNERS.get(slot)


def _body_claim_slot_sections(body: object) -> frozenset[str]:
    """장 본문 문장들의 주장슬롯이 가리키는 장들. 칸 값은 파서와 같은 방식으로 읽는다."""
    if not isinstance(body, Mapping):
        return frozenset()
    items = body.get(RESPONSE_SENTENCES_KEY)
    if not isinstance(items, list):
        return frozenset()
    raw_slots = (str(item.get(RESPONSE_CLAIM_SLOT_KEY) or "").strip()
                 for item in items if isinstance(item, Mapping))
    sections = (_claim_slot_section(slot) for slot in raw_slots)
    return frozenset(section_id for section_id in sections if section_id)


def _body_belongs_to_other_request(
    key: str, body: object, section_id: str, targets: Sequence[str],
) -> bool:
    """정본으로 푼 순번 범위 번호 키의 본문이 다른 요청 장의 칸을 가리키는가.

    요청 순번으로도 읽히는 번호(N ≤ 요청 장 수)는 번호 체계가 섞인 답에서 작가 뜻이
    순번일 수 있다(2026-09-23 독립 검토: 순번 «1장»·«2장»에 정본 «7장»이 얹히자 7장
    글을 담은 «2장»이 정본 2장으로 풀렸다). 본문 칸이 풀린 장이 아닌 다른 요청 장을
    가리키면 그 해석을 받지 않는다. 칸이 없는 본문은 볼 것이 없어 막지 못한다.
    """
    number = _number_only_key_number(key)
    if number is None or _request_order_position(number, targets) is None:
        return False
    return bool((_body_claim_slot_sections(body) & set(targets)) - {section_id})


def _matches_exact(key: str, section_id: str) -> bool:
    return key == section_id


def _matches_normalized(key: str, section_id: str) -> bool:
    return _normalized_section_key(key) == _normalized_section_key(section_id)


def _matches_display(key: str, section_id: str) -> bool:
    return _display_section_key(key) in _SECTION_DISPLAY_FORMS.get(section_id, frozenset())


#: 응답 키 해석 단계 — 앞 단계가 우선한다. 같은 장에 키가 여럿 걸리면 앞 단계로
#: 걸린 키(같은 단계면 먼저 나온 키)를 쓰고 나머지는 요청 밖 장으로 센다.
_KEY_MATCH_STAGES = (_matches_exact, _matches_normalized, _matches_display)
_NORMALIZED_STAGE = _KEY_MATCH_STAGES.index(_matches_normalized)
_DISPLAY_STAGE = _KEY_MATCH_STAGES.index(_matches_display)


def _resolve_section_key(
    key: object, targets: Sequence[str], *, canonical_numbering: bool = False,
) -> tuple[str | None, int | None]:
    """응답 키 하나를 요청 장 ID 하나로 해석한다.

    Args:
        canonical_numbering: 이 응답이 정본 번호를 쓴다고 판별됐으면 참이다. 참이면
            번호만 있는 키의 요청 순번 충돌을 보지 않고 정본대로 푼다.

    Returns:
        (요청 장 ID, 걸린 단계 위치). 어느 단계에도 안 걸리면 (None, None).
        한 단계에서 요청 장 둘 이상에 걸리거나, 번호만 있는 키가 정본 번호와 요청
        순번에서 서로 다른 장을 가리키면(모호) (None, 그 단계 위치).
    """
    if not isinstance(key, str):
        return None, None
    for stage, matches in enumerate(_KEY_MATCH_STAGES):
        hits = [section_id for section_id in targets if matches(key, section_id)]
        if (hits and stage == _DISPLAY_STAGE and not canonical_numbering
                and _conflicts_with_request_order(key, targets)):
            # 정본 번호로도 요청 순번으로도 읽히는데 두 장이 다르다 — 어느 쪽도 고르지 않는다.
            return None, stage
        if len(hits) == 1:
            return hits[0], stage
        if hits:
            return None, stage
    return None, None


def _select_requested(
    entries: Mapping[object, object], targets: Sequence[str], *,
    skip_keys: frozenset[str] = frozenset(),
) -> tuple[dict[str, object], int]:
    """응답 묶음에서 요청 장별 본문을 고르고, 쓰지 않은 키 수를 센다.

    쓰지 않은 키 = 해석 못 한 키 + 모호한 키 + 이미 고른 장에 또 걸린 키.
    해석은 됐지만 본문이 객체가 아닌 키는 포장 여부와 무관하게 고르지도 세지도
    않는다(2026-09-23: 포장 안 문자열 본문이 «쓸 장»으로 잡혀 재요청을 막았다). 번호만
    있는 키를 정본 번호로 읽을지는 키를 풀기 전에 이 묶음의 키 전체로 한 번 정한다 —
    키 순서와 무관하다. 그 근거로는 본문이 객체인 키만 센다(포장 여부와 무관). 정본으로
    푼 순번 범위 번호 키라도 본문 칸이 다른 요청 장을 가리키면 모호로 센다.
    """
    chosen: dict[str, tuple[int, object]] = {}
    unused = 0
    ambiguous = 0
    # 문자열 값(«"7장": "<장 제목>"» 같은 표지)은 포장 여부와 무관하게 장 본문이
    # 아니다 — 판별 근거로 세지 않는다(2026-09-23 독립 검토).
    canonical_numbering = _writes_canonical_numbers(
        (key for key, value in entries.items()
         if key not in skip_keys and isinstance(value, Mapping)),
        targets)
    for key, value in entries.items():
        if key in skip_keys:
            continue
        section_id, stage = _resolve_section_key(
            key, targets, canonical_numbering=canonical_numbering)
        if (section_id is not None and canonical_numbering and isinstance(key, str)
                and _body_belongs_to_other_request(key, value, section_id, targets)):
            section_id = None  # 섞인 번호 답 — 모호로 센다(걸린 단계는 그대로).
        if section_id is None:
            unused += 1
            if stage is not None:
                ambiguous += 1
            continue
        if not isinstance(value, Mapping):
            continue
        previous = chosen.get(section_id)
        if previous is not None:
            unused += 1
            if stage >= previous[0]:
                continue
        chosen[section_id] = (stage, value)
    stages = [stage for stage, _ in chosen.values()]
    normalized, display = stages.count(_NORMALIZED_STAGE), stages.count(_DISPLAY_STAGE)
    if normalized or display or ambiguous:
        # 개수만 남긴다 — 응답 문장·회사명은 로그에 싣지 않는다.
        logger.info("빈 장 복구 응답 장 키 해석: 정규화 %d개, 표시명 %d개, 모호 %d개",
                    normalized, display, ambiguous)
    return ({section_id: chosen[section_id][1] for section_id in targets if section_id in chosen},
            unused)


def requested_sections_from_response(
    raw: object, targets: Sequence[str],
) -> tuple[dict[str, object], int, str]:
    """작가 응답에서 «요청한 장»의 본문 묶음만 꺼낸다.

    계약 형식은 ``{"장들": {"<장 ID>": {"문장들": [...]}}}`` 하나뿐이지만, 작가는
    포장을 빼먹거나(``{"<장 ID>": {...}}``) 요청 장이 하나일 때 장 ID까지
    빼고 ``{"문장들": [...]}`` 만 돌려주기도 한다(2026-09-17 실측: 같은 지침에
    두 번 연속 「작성형식실패」). 셋 다 문장 내용은 같으므로 받아 준다.
    요청 장이 둘 이상인데 평면 꼴이면 어느 장인지 알 수 없어 받지 않는다.

    장 키는 요청 장 ID로 «해석»한다(2026-09-23 실측: 키를 «1장 기업 정체성»·«1장»
    으로 적은 정답이 두 번 모두 «요청장없음»으로 버려졌다). 해석 순서는 정확 일치 →
    정규화 일치(대소문자·공백·하이픈/밑줄) → 표시형(«N장»·«제N장»·«N장 제목»·
    «제목»·«N. 제목», N은 장 순서)이다. 한 단계에서 요청 장 둘 이상에 걸리는 키는
    모호해서 받지 않는다. 요청하지 않은 장의 표시형(예: 요청 밖 «3장»)도 받지 않는다.
    제목 없이 번호만 있는 키(«N장»·«제N장»)는 «요청 순번»으로도 읽힐 수 있어서,
    N번째 요청 장이 정본 N장과 다르면 받지 않고 모호로 센다(요청 2·7장에 «2장»).
    번호가 요청 장 수보다 크면 순번으로 읽을 수 없으므로 정본대로 받는다. 그런
    번호만 있는 키가 정본으로 요청 장에 걸리면 작가가 정본 번호를 쓰는 것이므로,
    그 응답의 번호만 있는 키는 모두 정본대로 받는다(요청 2·7장에 «2장»·«7장»).
    요청 밖 장의 번호(덤 «3장»)는 이 판별에 쓰지 않는다. 다만 번호 체계가 섞인
    답에서 순번으로도 읽히는 번호 키의 본문 주장슬롯이 다른 요청 장을 가리키면
    받지 않는다(순번 «2장»에 7장 글).
    응답 꼴 코드는 닫힌 목록 그대로다 — 키를 해석해 받은 답도 «계약»·«포장없음»이다.

    Returns:
        (요청 장별 본문 묶음, 요청 밖 장 수, 응답 꼴 코드). 요청 밖 장 수는 «쓰지 않은
        키 수»다 — 해석 못 한 키·모호한 키·이미 고른 장에 또 걸린 키.
    """
    if not isinstance(raw, Mapping):
        return {}, 0, RESPONSE_SHAPE_UNREADABLE
    by_section = raw.get(RESPONSE_SECTIONS_KEY)
    if isinstance(by_section, Mapping):
        usable, extra = _select_requested(by_section, targets)
        return usable, extra, (RESPONSE_SHAPE_CONTRACT if usable else RESPONSE_SHAPE_NO_TARGET)
    unwrapped, extra = _select_requested(
        raw, targets, skip_keys=frozenset((RESPONSE_SENTENCES_KEY,)),
    )
    if unwrapped:
        return unwrapped, extra, RESPONSE_SHAPE_UNWRAPPED
    if len(targets) == 1 and isinstance(raw.get(RESPONSE_SENTENCES_KEY), list):
        return {targets[0]: raw}, 0, RESPONSE_SHAPE_FLAT_SINGLE
    return {}, 0, RESPONSE_SHAPE_NO_TARGET


def recover_empty_sections(
    company_name: str, report: ComposedReport, *, targets: tuple[str, ...],
    evidence: Mapping[str, tuple[CollectedFragment, ...]], writer: AskFn, reviewer: AskFn,
    performance_table=None, baseline_date=None, diagnostics=None, protocol_diagnostics=None,
    comparison_fragments: Sequence[CollectedFragment] = (),
    rejected_fingerprints: Mapping[str, frozenset[str]] | None = None,
    require_claim_slot: bool = False,
) -> ComposedReport:
    """비대상 장은 그대로 두고 확인·verified 문장만 빈 본문에 반영한다.

    Args:
        require_claim_slot: 참이면 의미 칸(claim slot)이 붙고 «인용한 조각이 그
            칸을 실제로 지원하는» 문장만 남긴다. 장별 packet 계약(FULL)에서는
            반드시 참이어야 한다 — 의미 칸 없는 문장은 사실 장부에 FactRecord로
            오르지 못해, 그 장이 「fact_id와 결속되지 않은 공개 내용」으로
            판정되고 «보고서 전체»가 공개 차단된다(2026-09-16 재현). 즉 복구가
            빈 장 하나를 채우려다 보고서를 통째로 막는다.
    """
    empty_ids = {section.section_id for section in report.sections if not section.sentences}
    targets = tuple(section_id for section_id in SECTION_IDS
                    if section_id in targets and section_id in empty_ids and evidence.get(section_id))
    targets = targets[:MAX_EMPTY_RECOVERY_SECTIONS]
    if not targets:
        return report
    # 조각마다 «그 장에서 지원하는 의미 칸»을 함께 준다. 이 목록이 없으면 작가가
    # 칸 이름을 지어내고, 지어낸 칸은 파서가 빈 칸으로 떨어뜨린다.
    supported_slots = {
        section_id: {
            fragment.fragment_id: tuple(
                slot for slot in fragment.supported_claim_slots
                if slot.startswith(section_id + ":")
            )
            for fragment in evidence[section_id]
        }
        for section_id in targets
    }
    payload = {
        section_id: {
            "작성범위": SECTION_GUIDES[section_id],
            "공식근거": [{"id": f.fragment_id, "원문": f.text,
                       "의미칸": list(supported_slots[section_id][f.fragment_id])}
                      for f in evidence[section_id]],
        }
        for section_id in targets
    }
    # 지침의 «<요청 장 ID>» 자리에 쓸 실제 문자열을 따로 한 줄로 준다. 이 줄이 없으면
    # 작가가 작성범위 문구의 장 번호·제목을 키로 쓴다(2026-09-23 실측).
    target_ids_line = EMPTY_RECOVERY_TARGET_IDS_LINE.format(
        target_ids=EMPTY_RECOVERY_TARGET_IDS_JOINER.join(targets))
    prompt = (f"분석 회사: {company_name}\n{EMPTY_RECOVERY_GUIDE}{target_ids_line}"
              + json.dumps(payload, ensure_ascii=False))
    # 요청한 장만 골라 쓰고 요청 밖 장은 버린다. 예전에는 키 집합이 정확히
    # 같지 않으면 답 전체를 버려서, 한 장을 덤으로 얹은 답 하나 때문에 빈 장이
    # 둘 다 비어 나갔다(2026-09-14 실측). 쓸 장이 하나도 없을 때만 재요청한다.
    usable: dict[str, object] = {}
    extra_sections = 0
    attempts = 0
    shapes: list[str] = []
    while True:
        attempts += 1
        raw = extract_json_payload(writer(
            prompt if attempts == 1 else prompt + EMPTY_RECOVERY_RETRY_GUIDE
        ))
        usable, extra_sections, shape = requested_sections_from_response(raw, targets)
        shapes.append(shape)
        if usable or attempts > PARSE_RETRY_LIMIT:
            break
    if not usable:
        # 어떤 꼴로 실패했는지 남긴다 — 내용은 담지 않고 응답의 구조만 적는다.
        if protocol_diagnostics is not None:
            protocol_diagnostics.append({"step": EMPTY_RECOVERY_STEP, "상태": "작성형식실패",
                                         "대상장": list(targets), "시도": attempts,
                                         "응답꼴": shapes})
        return report
    targets = tuple(section_id for section_id in targets if section_id in usable)
    sections = []
    for section_id in targets:
        parsed = parse_section_response(json.dumps(usable[section_id], ensure_ascii=False), section_id,
                                        reject_inline_citation_markers=True)
        allowed = {f.fragment_id for f in evidence[section_id]}
        slots_by_fragment = supported_slots[section_id]
        sentences = tuple(
            sentence for sentence in (parsed or ())
            if sentence.grade == GRADE_CONFIRMED and sentence.citations
            and set(sentence.citations) <= allowed
            and (not require_claim_slot or (
                sentence.planned_claim_slot
                and all(sentence.planned_claim_slot in slots_by_fragment.get(citation, ())
                        for citation in sentence.citations)))
            and rejected_sentence_fingerprint(sentence.text)
            not in (rejected_fingerprints or {}).get(section_id, frozenset())
        )[:MAX_EMPTY_RECOVERY_SENTENCES]
        sections.append(ComposedSection(section_id, sentences))
    if not any(section.sentences for section in sections):
        if protocol_diagnostics is not None:
            protocol_diagnostics.append({"step": EMPTY_RECOVERY_STEP, "상태": "확인후보없음", "대상장": list(targets)})
        return report
    if protocol_diagnostics is not None:
        protocol_diagnostics.append({"step": EMPTY_RECOVERY_STEP, "상태": "작성완료",
                                     "대상장": list(targets),
                                     "작성문장수": sum(len(section.sentences) for section in sections),
                                     "요청밖장수": extra_sections, "응답꼴": shapes[-1]})
    called = False

    def review_once(prompt: str) -> str:
        nonlocal called
        if called:
            raise AskFatalError(ValueError("빈 장 복구 검수는 한 번만 허용합니다"), call_limit=True)
        called = True
        return reviewer(prompt)

    fragments = tuple({f.fragment_id: f for section_id in targets for f in evidence[section_id]}.values())
    verified = verify_report(
        ComposedReport(tuple(sections)), fragments, performance_table, review_once,
        diagnostics=diagnostics, protocol_diagnostics=protocol_diagnostics,
        baseline_date=baseline_date, allow_sentence_rewrite=False,
    )
    verified_count = sum(
        1 for section in verified.sections for sentence in section.sentences
        if sentence.grade == GRADE_CONFIRMED and sentence.verification_state == "verified"
    )
    verified, _ = drop_cross_section_duplicates(verified, fragments=fragments)
    # 본문 출고와 같은 수치 안전 검사까지 살아남아야 복구한 장으로 센다.
    verified, _ = enforce_public_numeric_safety(verified)
    replacements = {
        section.section_id: tuple(sentence for sentence in section.sentences
                                  if sentence.grade == GRADE_CONFIRMED and sentence.verification_state == "verified")
        for section in verified.sections
    }
    safe_count = sum(len(sentences) for sentences in replacements.values())
    recovered = []
    merged = []
    for section in report.sections:
        sentences = replacements.get(section.section_id, ())
        # 복구 문장이 기존 장의 사실을 다시 가져오면 복구 쪽만 버린다.
        # 기존 중복 판정과 같은 문서 결속을 사용하되 기존 본문은 수정하지 않는다.
        unique = []
        for sentence in sentences:
            candidate_report = replace(report, sections=tuple(
                replace(original, sentences=(sentence,))
                if original.section_id == section.section_id else original
                for original in report.sections
            ))
            _, duplicate_count = drop_cross_section_duplicates(
                candidate_report, fragments=comparison_fragments or fragments,
            )
            if not duplicate_count:
                unique.append(sentence)
        sentences = tuple(unique)
        if section.section_id in targets and not section.sentences and sentences:
            merged.append(replace(section, sentences=sentences, notice=""))
            recovered.append(section.section_id)
        else:
            merged.append(section)
    if protocol_diagnostics is not None:
        # 어느 관문에서 문장이 사라졌는지 세 숫자로 남긴다(검수 통과 → 수치·중복
        # 검사 뒤 → 기존 장과의 중복 제거 뒤). 「복구장 []」만으로는 원인을 못 가린다.
        protocol_diagnostics.append({"step": EMPTY_RECOVERY_STEP, "상태": "검수완료",
                                     "대상장": list(targets), "복구장": recovered,
                                     "검수통과": verified_count, "안전검사후": safe_count,
                                     "최종반영": sum(len(section.sentences) for section in merged
                                                  if section.section_id in recovered)})
    return replace(report, sections=tuple(merged))
