"""FULL 산문 claim과 typed 근거 의미 칸의 끝단 결속을 검증한다."""

from __future__ import annotations

import json
import re

import pytest

from src.features.composer.constants import (
    GRADE_CONFIRMED,
    GRADE_INTERPRETED,
    SECTION_IDS,
)
from src.features.composer.logic import compose_sections, compose_selected_sections
from src.features.composer.pipeline import run_v2
from src.features.composer.port import (
    CollectedFragment,
    SectionEvidencePacket,
    SectionEvidencePacketSet,
)
from src.features.composer.validate import V2ValidationError
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_evidence.policy import required_slots_for


_CHALLENGE_SECTION = "current_challenges"
_ISSUE_SLOT = "current_challenges:issue"
_RESPONSE_SLOT = "current_challenges:response"


def _packet_set(
    *,
    challenge_slots: tuple[str, ...],
    slot_overrides: dict[str, tuple[str, ...]] | None = None,
    challenge_extra_slots: tuple[str, ...] = (),
) -> SectionEvidencePacketSet:
    generation = "a" * 64
    packets: list[SectionEvidencePacket] = []
    for index, section_id in enumerate(SECTION_IDS, start=1):
        slots = (slot_overrides or {}).get(
            section_id,
            (
                challenge_slots
                if section_id == _CHALLENGE_SECTION
                else (CLAIM_SLOTS_BY_SECTION[section_id][0],)
            ),
        )
        fragments = [
            CollectedFragment(
                fragment_id=str(index),
                kind="typed-evidence-v1:test",
                text=f"테스트 회사의 {section_id} 공식 원문이다.",
                source_url=f"https://example.com/documents/{index}",
                document_identity=f"document:example.com:doc-{index}",
                document_content_sha256=f"{index:064x}",
                supported_claim_slots=slots,
            )
        ]
        if section_id == _CHALLENGE_SECTION and challenge_extra_slots:
            fragments.append(
                CollectedFragment(
                    fragment_id="50",
                    kind="typed-evidence-v1:test",
                    text="테스트 회사는 당면 과제 대응 방침도 공식 발표했다.",
                    source_url="https://example.com/documents/50",
                    document_identity="document:example.com:doc-50",
                    document_content_sha256=f"{50:064x}",
                    supported_claim_slots=challenge_extra_slots,
                )
            )
        packets.append(
            SectionEvidencePacket(
                company_id="00123456",
                evidence_generation_sha256=generation,
                section_id=section_id,
                fragments=tuple(fragments),
            )
        )
    return SectionEvidencePacketSet(
        company_id="00123456",
        evidence_generation_sha256=generation,
        packets=tuple(packets),
    )


def _response(
    *,
    claim_slot: str | None,
    citations: tuple[str, ...],
    grade: str,
    flow_cells: tuple[str, ...] = (),
) -> str:
    item: dict[str, object] = {
        "글": "회사는 당면 과제에 대응 방안을 공식적으로 밝혔다.",
        "인용": list(citations),
        "등급": grade,
    }
    if claim_slot is not None:
        item["주장슬롯"] = claim_slot
    return json.dumps(
        {
            "문장들": [item],
            "경로표": (
                [{"칸": list(flow_cells), "인용": list(citations)}]
                if flow_cells
                else []
            ),
        },
        ensure_ascii=False,
    )


def _compose_challenge(
    *,
    challenge_slots: tuple[str, ...],
    claim_slot: str | None,
    citations: tuple[str, ...] = ("5",),
    grade: str = GRADE_CONFIRMED,
    flow_cells: tuple[str, ...] = (),
) -> tuple[object, str]:
    prompts: list[str] = []

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        return _response(
            claim_slot=claim_slot,
            citations=citations,
            grade=grade,
            flow_cells=flow_cells,
        )

    report = compose_selected_sections(
        "테스트 회사",
        None,
        ask,
        section_evidence_packets=_packet_set(
            challenge_slots=challenge_slots
        ),
        section_ids=(_CHALLENGE_SECTION,),
    )
    assert len(prompts) == 1
    return report, prompts[0]


def test_과제근거를_대응주장의_증거로_바꿔쓸수없다() -> None:
    report, prompt = _compose_challenge(
        challenge_slots=(_ISSUE_SLOT,),
        claim_slot=_RESPONSE_SLOT,
    )

    assert report.sections[0].sentences == ()
    assert "지원 주장슬롯: current_challenges:issue" in prompt
    assert "빈 문자열이나 목록 밖 id는 허용되지 않는다" in prompt


def test_한조각이_과제와대응을_모두지원하면_대응주장이_남는다() -> None:
    report, prompt = _compose_challenge(
        challenge_slots=(_ISSUE_SLOT, _RESPONSE_SLOT),
        claim_slot=_RESPONSE_SLOT,
    )

    assert len(report.sections[0].sentences) == 1
    assert report.sections[0].sentences[0].planned_claim_slot == _RESPONSE_SLOT
    assert _ISSUE_SLOT in prompt
    assert _RESPONSE_SLOT in prompt


def test_지원주장슬롯이_바뀌면_packet_hash도_바뀐다() -> None:
    issue_only = _packet_set(challenge_slots=(_ISSUE_SLOT,))
    issue_and_response = _packet_set(
        challenge_slots=(_ISSUE_SLOT, _RESPONSE_SLOT)
    )

    issue_hash = dict(issue_only.packet_sha256s)[_CHALLENGE_SECTION]
    response_hash = dict(issue_and_response.packet_sha256s)[_CHALLENGE_SECTION]
    assert issue_hash != response_hash


def test_과제만지원하는조각으로_가짜대응표를_만들수없다() -> None:
    report, prompt = _compose_challenge(
        challenge_slots=(_ISSUE_SLOT,),
        claim_slot=_ISSUE_SLOT,
        flow_cells=("원재료 공급 불안", "대체 공급망 확보"),
    )

    assert report.sections[0].flow_rows == ()
    assert "회사가 밝힌 대응: current_challenges:response" in prompt


def test_과제와대응근거가_모두있으면_대응표가_남는다() -> None:
    report, _prompt = _compose_challenge(
        challenge_slots=(_ISSUE_SLOT, _RESPONSE_SLOT),
        claim_slot=_ISSUE_SLOT,
        flow_cells=("원재료 공급 불안", "대체 공급망 확보"),
    )

    assert len(report.sections[0].flow_rows) == 1


def test_운영관계만으로_회사가하는일을_지어낸_7장표를_만들수없다() -> None:
    prompts: list[str] = []

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        return _response(
            claim_slot="operations_partners:value_chain",
            citations=("7",),
            grade=GRADE_CONFIRMED,
            flow_cells=("공급사", "회사의 품질 검사", "고객사"),
        )

    report = compose_selected_sections(
        "테스트 회사",
        None,
        ask,
        section_evidence_packets=_packet_set(
            challenge_slots=(_ISSUE_SLOT,),
            slot_overrides={
                "operations_partners": ("operations_partners:value_chain",)
            },
        ),
        section_ids=("operations_partners",),
    )

    assert report.sections[0].flow_rows == ()
    assert "회사가 하는 일: operations_partners:operating_role" in prompts[0]


@pytest.mark.parametrize(
    "claim_slot",
    [None, "current_challenges:not_registered"],
)
def test_FULL은_누락되거나_등록되지않은_주장슬롯을_빈칸우회로_받지않는다(
    claim_slot: str | None,
) -> None:
    report, _prompt = _compose_challenge(
        challenge_slots=(_RESPONSE_SLOT,),
        claim_slot=claim_slot,
    )

    assert report.sections[0].sentences == ()


@pytest.mark.parametrize(
    ("citations", "expected_count"),
    [(("5",), 1), ((), 0)],
)
def test_FULL_해석문장도_정확한슬롯과_인용근거에_결속된다(
    citations: tuple[str, ...], expected_count: int
) -> None:
    report, _prompt = _compose_challenge(
        challenge_slots=(_RESPONSE_SLOT,),
        claim_slot=_RESPONSE_SLOT,
        citations=citations,
        grade=GRADE_INTERPRETED,
    )

    assert len(report.sections[0].sentences) == expected_count


def test_legacy_mapping_packet은_없는_slot을_추측하지않고_기존동작을_유지한다(
) -> None:
    packets = {
        section_id: {
            index: {
                "종류": "사업내용",
                "원문": f"테스트 회사의 {section_id} 공식 원문이다.",
            }
        }
        for index, section_id in enumerate(SECTION_IDS, start=1)
    }
    calls = 0

    def ask(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        return _response(
            claim_slot=None,
            citations=(str(calls),),
            grade=GRADE_CONFIRMED,
        )

    report = compose_sections(
        "테스트 회사",
        {},
        None,
        ask,
        section_evidence_packets=packets,
    )

    assert calls == len(SECTION_IDS)
    assert all(len(section.sentences) == 1 for section in report.sections)


def test_run_v2_FULL도_issue근거를_response산문과표로_바꿔쓰지못한다() -> None:
    attack_sentence = "공급 불안에 대응해 회사가 대체 공급망을 이미 확보했다."
    attack_response_cell = "대체 공급망 확보 완료"
    writer_calls = 0
    reviewer_prompts: list[str] = []

    def writer(_prompt: str) -> str:
        nonlocal writer_calls
        section_id = SECTION_IDS[writer_calls]
        fragment_id = str(writer_calls + 1)
        writer_calls += 1
        claim_slot = CLAIM_SLOTS_BY_SECTION[section_id][0]
        text = f"테스트 회사의 {section_id} 공식 사실을 확인했다."
        flow_cells: tuple[str, ...] = ()
        if section_id == _CHALLENGE_SECTION:
            # 조각은 issue만 지원하지만 작가는 response로 라벨을 바꾸고,
            # 같은 조각으로 표의 대응 칸까지 지어내려 한다.
            claim_slot = _RESPONSE_SLOT
            text = attack_sentence
            flow_cells = ("원재료 공급 불안", attack_response_cell)
        return _response(
            claim_slot=claim_slot,
            citations=(fragment_id,),
            grade=GRADE_CONFIRMED,
            flow_cells=flow_cells,
        )

    def reviewer(prompt: str) -> str:
        reviewer_prompts.append(prompt)
        grouped = re.findall(
            r"\[(\d+)\] \(장: ([^,]+), 종류: ([^,]+), 인용: ([^)]+)\)",
            prompt,
        )
        return json.dumps(
            {
                "판정": [
                    {
                        "번호": int(number),
                        "장": section_id,
                        "근거": re.findall(r"조각 (\d+)", citations),
                        "결과": "참",
                    }
                    for number, section_id, _kind, citations in grouped
                ]
            },
            ensure_ascii=False,
        )

    with pytest.raises(V2ValidationError):
        all_required_slots = {
            section_id: required_slots_for(section_id)
            for section_id in SECTION_IDS
        }
        # 패킷 전체에는 issue와 response가 모두 있어 사전 충분성은 통과한다.
        # 하지만 작가가 실제로 인용한 5번 조각은 issue만 지원한다. 시험에서
        # response 근거를 같은 조각에 손으로 주입하지 않고, 별도 50번 원문에
        # 둬서 «패킷 도달성»과 «문장별 근거 결속»을 끝까지 갈라 검증한다.
        all_required_slots[_CHALLENGE_SECTION] = (_ISSUE_SLOT,)
        run_v2(
            "테스트 회사",
            {},
            None,
            writer_ask=writer,
            reviewer_ask=reviewer,
            release_mode=ReleaseMode.FULL,
            section_evidence_packets=_packet_set(
                challenge_slots=(_ISSUE_SLOT,),
                slot_overrides=all_required_slots,
                challenge_extra_slots=(_RESPONSE_SLOT,),
            ),
            company_id="00123456",
            build_identity_sha256="b" * 64,
        )

    assert writer_calls == len(SECTION_IDS)
    assert reviewer_prompts
    reviewer_input = "\n".join(reviewer_prompts)
    assert attack_sentence not in reviewer_input
    assert attack_response_cell not in reviewer_input


def test_run_v2_FULL은_도달불가능한_slot구성을_AI호출전에_막는다() -> None:
    writer_calls = 0

    def forbidden_writer(_prompt: str) -> str:
        nonlocal writer_calls
        writer_calls += 1
        raise AssertionError("의미 칸 하한 미달이면 작가를 부르면 안 됩니다")

    slots = {
        section_id: required_slots_for(section_id)
        for section_id in SECTION_IDS
    }
    slots[_CHALLENGE_SECTION] = (_ISSUE_SLOT,)

    with pytest.raises(V2ValidationError) as caught:
        run_v2(
            "테스트 회사",
            {},
            None,
            writer_ask=forbidden_writer,
            reviewer_ask=lambda _prompt: "{}",
            release_mode=ReleaseMode.FULL,
            section_evidence_packets=_packet_set(
                challenge_slots=(_ISSUE_SLOT,),
                slot_overrides=slots,
            ),
            company_id="00123456",
            build_identity_sha256="b" * 64,
        )

    assert writer_calls == 0
    assert caught.value.problems == (
        "report_recovery:preflight_official_evidence_insufficient",
    )
