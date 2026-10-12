"""FULL 작성자가 고를 수 있는 주장슬롯·근거조각 쌍을 봉인한다."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from src.features.composer.constants import CLAIM_SLOTS_BY_SECTION, SECTION_IDS
from src.features.composer.port import CollectedFragment
from src.shared.report_evidence.policy import injected_slots_for
from src.features.composer.education_practice_scope import education_practice_scope_problem


EvidencePairMap = Mapping[str, tuple[str, str]]


def build_evidence_pair_map(
    section_id: str,
    fragments: Sequence[CollectedFragment],
) -> dict[str, tuple[str, str]]:
    """장별 봉인 조각에서만 고유한 선택 ID를 만든다.

    번호는 자료의 원래 ID가 아니라 선택지 ID다. 장 번호를 넣어 다른 장의
    선택지를 섞어 쓰지 못하게 하고, 프로그램 전용 의미칸은 제외한다.
    """
    if section_id not in SECTION_IDS:
        raise ValueError("알 수 없는 장의 근거 선택지입니다")
    injected = frozenset(injected_slots_for(section_id))
    choices: dict[str, tuple[str, str]] = {}
    seen: set[tuple[str, str]] = set()
    for slot in CLAIM_SLOTS_BY_SECTION[section_id]:
        if slot in injected:
            continue
        for fragment in fragments:
            pair = (slot, fragment.fragment_id)
            if slot not in fragment.supported_claim_slots or pair in seen:
                continue
            if education_practice_scope_problem(
                fragment.text, {fragment.fragment_id: fragment.text},
                section_id=section_id, claim_slot=slot,
                practice_context_by_source_id={fragment.fragment_id: fragment.practice_context_json},
            ):
                continue
            seen.add(pair)
            choice_id = f"p{SECTION_IDS.index(section_id) + 1}-{len(choices) + 1:03d}"
            choices[choice_id] = pair
    return choices


def writer_visible_fragments(
    section_id: str,
    fragments: Sequence[CollectedFragment],
) -> tuple[CollectedFragment, ...]:
    """FULL 작성 프롬프트에는 본문 지원쌍이 있는 원문만 원순서로 보여 준다.

    준비된 전체 근거와 검수 입력은 바꾸지 않는다. 흐름표 칸의 의미칸은
    본문 지원쌍과 같은 슬롯 집합에 속하고, 프로그램 삽입 전용칸은 제외된다.
    """

    choices = build_evidence_pair_map(section_id, fragments)
    visible_ids = {fragment_id for _slot, fragment_id in choices.values()}
    return tuple(fragment for fragment in fragments if fragment.fragment_id in visible_ids)


def render_evidence_pair_index(
    section_id: str,
    fragments: Sequence[CollectedFragment],
) -> str:
    """기존 슬롯별 번호표 안에 유효한 쌍 ID만 짧게 보여 준다."""
    choices = build_evidence_pair_map(section_id, fragments)
    by_slot: dict[str, list[str]] = {}
    for choice_id, (slot, fragment_id) in choices.items():
        by_slot.setdefault(slot, []).append(f"{fragment_id}[{choice_id}]")
    return "".join(
        f"- {slot}: {', '.join(by_slot[slot])}\n"
        for slot in CLAIM_SLOTS_BY_SECTION[section_id]
        if slot in by_slot
    )
