# -*- coding: utf-8 -*-
"""FULL 공개 안전 차단 한 줄(`8_공개안전_차단유형`) — 유형 표 전수와 run_v2 배선을 지킨다.

(1) 유형 표 전수 — 안전 판정의 문장 틀마다 기대 유형을 리터럴로 맞대고, 같은 표본을
    summarize_safety_problems 유형(id·장 이름 지운 문장)으로 세어도 같은지 본다.
(2) 실제 판정 — 손으로 만든 후보를 «실제» 안전 판정에 넣고, 나온 문장으로 줄을 만든다.
(3) 배선 — run_v2 의 1차·보충 판정이 막으면 줄 1건, 정상 출고·품질 하한 중단은 0건,
    줄을 못 만들어도 출고 검증 차단은 그대로.

정화기·분류기의 닫힌 계약과 문구 틀 표류 감시는 shared/report_quality/tests/
test_safety_block_diagnostic.py 에 있다.

★ 기대값은 리터럴이다 — 생산 상수를 import 해 기대값으로 쓰지 않는다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace

import pytest

from src.features.composer import pipeline as pipeline_module
from src.features.composer import quality_observation_log
from src.features.composer.pipeline import run_v2
from src.features.composer.quality_observation_log import (
    safety_block_record,
    summarize_safety_problems,
)
from src.features.composer.tests.test_evidence_available_report import _StrictThinWriter
from src.features.composer.tests.test_pipeline import (
    _FakeReviewer,
    _strict_fragments,
    _strict_packet_set,
)
from src.features.composer.tests.test_release_mode_diagnostic import _full
from src.features.composer.tests.test_section_public_manifest import (
    _BoundGroupedReviewer,
    _FirstRowBrokenReviewer,
    _GlobalFailureReviewer,
    _NoDiagram,
    _RecoveringPacketWriter,
    _packets,
)
from src.features.composer.validate import V2ValidationError
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_quality.assessment import assess_generation
from src.shared.report_quality.composition_diagnostics import observed_composition_steps
from src.shared.report_quality.constants import (
    STRICT_QUALITY_CONTRACT_VERSION,
    VERIFIED_PROSE_CLAIM_TYPE,
)
from src.shared.report_quality.dto import (
    ClaimFact,
    ReportCandidate,
    ReportSectionCandidate,
    SourceDocument,
)
from src.shared.report_quality.models import QualityGrade, ReleaseDecision
from src.shared.report_quality.safety_problem_kinds import safety_problem_kind

_STEP = "8_공개안전_차단유형"
_PROSE_ID = "v2-prose-" + "0123456789abcdef" * 2
_COMPARE_ID = "fact-compare-0123456789abcdef"
_SOURCE_ID = "src-dart-1"


def _safety_lines(diagnostics: list[dict]) -> list[dict]:
    return [record for record in diagnostics if record.get("step") == _STEP]


# ══════════════════════════════════════════════════════════
# (1) 유형 표 전수 — 문장 틀 → 유형
# ══════════════════════════════════════════════════════════

#: assessment.py 의 문장 틀을 실제 값 모양(산문 id·비교 id·장 이름)으로 채운 표본.
_TEMPLATE_KINDS = (
    ("빈 fact_id가 있습니다", "fact_registry"),
    (f"fact_id {_PROSE_ID}가 중복됐습니다", "fact_registry"),
    ("출처의 source_id 또는 독립 문서 identity가 비었습니다", "source_registry"),
    (f"source_id {_SOURCE_ID}가 중복됐습니다", "source_registry"),
    (f"source_id {_SOURCE_ID}의 공식 문서 계수 표식이 bool이 아닙니다", "source_registry"),
    (f"source_id {_SOURCE_ID}의 뉴스 출처가 공식 문서 수에 포함됐습니다", "source_registry"),
    (f"source_id {_SOURCE_ID}의 원문 조각 해시가 손상됐습니다", "source_registry"),
    (f"source_id {_SOURCE_ID}의 문서 전체 해시가 손상됐습니다", "source_registry"),
    ("빈 section_id가 있습니다", "section_structure"),
    ("section_id culture가 중복됐습니다", "section_structure"),
    ("portfolio장에 fact_id와 결속되지 않은 공개 내용이 있습니다", "unbound_public_content"),
    ("culture장이 안내문 전용인데 fact_id도 함께 있습니다", "section_structure"),
    ("identity장 안에서 fact_id가 중복됐습니다", "duplicate_public_fact"),
    ("identity장에 빈 fact_id가 있습니다", "section_structure"),
    (
        f"fact_id {_PROSE_ID}가 identity장과 culture장에 중복 공개됐습니다",
        "duplicate_public_fact",
    ),
    ("공개할 원자 claim이 없습니다", "no_public_claim"),
    ("요약에 본문 fact_id와 결속되지 않은 공개 내용이 있습니다", "summary_binding"),
    ("요약 fact_id가 중복됐습니다", "summary_binding"),
    (f"요약 fact_id {_PROSE_ID}가 검증 본문의 부분집합이 아닙니다", "summary_binding"),
    ("비교 프로그램: 비교 프로그램의 동일 조건 구조가 완전하지 않습니다", "comparison_program"),
    (f"공개 fact_id {_PROSE_ID}가 사실 장부에 없습니다", "fact_registry"),
    # 실제 문장은 AI 가 쓴 claim_type 값을 뒤에 붙인다 — 쌍점이 든 값이어도 같은 유형.
    (f"{_PROSE_ID}의 공개 claim_type을 알 수 없습니다: guess: 추정", "claim_type_unknown"),
    (f"{_PROSE_ID}의 소유 장과 공개 장이 일치하지 않습니다", "section_owner_mismatch"),
    (f"{_PROSE_ID}의 계획된 claim slot이 비었습니다", "claim_slot"),
    (f"{_PROSE_ID}의 claim slot이 culture장 정책에 없습니다", "claim_slot"),
    (f"{_PROSE_ID}의 공개 claim이 비었습니다", "claim_empty"),
    (
        f"culture장의 같은 원자 claim을 {_COMPARE_ID}와 {_PROSE_ID}가 중복 공개했습니다",
        "duplicate_public_fact",
    ),
    (f"{_PROSE_ID}의 원문·주장 결속 지문이 유효하지 않습니다", "evidence_binding_invalid"),
    (f"{_PROSE_ID}의 검증 상태를 알 수 없습니다", "verification_state_unknown"),
    (f"{_PROSE_ID}가 존재하지 않는 source_id를 참조합니다", "source_reference"),
    (f"{_PROSE_ID}의 독립 문서 identity가 출처 장부와 다릅니다", "source_reference"),
    (f"{_PROSE_ID}의 정확한 원문 조각 결속이 비었습니다", "exact_evidence"),
    (f"{_PROSE_ID}의 다중 출처 결속 열 길이가 다릅니다", "exact_evidence"),
    (f"{_PROSE_ID}의 다중 출처 source_id가 중복됐습니다", "exact_evidence"),
    (f"{_PROSE_ID}의 대표 출처가 다중 출처 첫 항목과 다릅니다", "exact_evidence"),
    (
        f"{_PROSE_ID}가 존재하지 않는 보조 source_id {_SOURCE_ID}를 참조합니다",
        "source_reference",
    ),
    (f"{_PROSE_ID}의 보조 출처 {_SOURCE_ID} 문서 identity가 다릅니다", "source_reference"),
    (f"{_PROSE_ID}의 보조 출처 {_SOURCE_ID} 원문 조각 해시가 다릅니다", "exact_evidence"),
    (
        f"{_COMPARE_ID}의 과거 실적 종류에 versioned NumericBinding이 없습니다",
        "numeric_binding_missing",
    ),
    (f"{_PROSE_ID}의 구조화 수치 이름표가 비었습니다", "numeric_labels_missing"),
    (f"{_PROSE_ID}의 수치에 versioned NumericBinding이 없습니다", "numeric_binding_missing"),
    # 하위 검사기(뉴스 산문·비교·수치 검산)가 붙인 «fact_id: 세부 문구»는 한 유형이다.
    (f"{_PROSE_ID}: 뉴스 산문의 숫자·단위가 정확 원문과 다릅니다", "claim_detail"),
    (f"{_PROSE_ID}: 뉴스 산문은 법인 정체·공식 비교를 대체할 수 없습니다", "claim_detail"),
    (
        f"{_COMPARE_ID}: 프로그램 비교 맥락 문장의 근거어가 claim과 원문 양쪽에 없습니다: 가,나",
        "claim_detail",
    ),
    (f"{_COMPARE_ID}: 차이가 없는 영업이익률을 우열 문장으로 공개했습니다", "claim_detail"),
    (
        f"{_COMPARE_ID}: 저장 계산값이 원시 피연산자와 공식 재계산 결과에 맞지 않습니다",
        "claim_detail",
    ),
    ("검증하지 못한 공개 claim이 있습니다", "unverified_claim"),
    ("거절된 claim이 공개 후보에 남아 있습니다", "rejected_claim"),
    # 표에 없는 문장은 지어내지 않고 «other» 로 센다. 공백 든 머리말 뒤 쌍점은
    # 하위 검사기 꼴(«fact_id: 세부 문구»)로 읽지 않는다.
    ("처음 보는 안전 문제 문장입니다", "other"),
    ("새 검사 이름: 공백 든 머리말 뒤의 쌍점", "other"),
)


@pytest.mark.parametrize(("problem", "expected"), _TEMPLATE_KINDS)
def test_안전_판정_문장_틀마다_닫힌_유형이_정해져_있다(problem: str, expected: str) -> None:
    assert safety_problem_kind(problem) == expected


@pytest.mark.parametrize(("problem", "expected"), _TEMPLATE_KINDS)
def test_부분보고서_경고_요약의_유형으로_세어도_같은_유형이다(problem: str, expected: str) -> None:
    """줄의 유형 건수는 summarize_safety_problems 유형(id·장 이름 지운 문장)으로 센다."""

    ((type_text, count),) = summarize_safety_problems([problem])
    assert count == 1
    assert safety_problem_kind(type_text) == expected


# ══════════════════════════════════════════════════════════
# (2) 실제 안전 판정 문장으로 만든 줄
# ══════════════════════════════════════════════════════════

_HASH = "a" * 64


def _source() -> SourceDocument:
    return SourceDocument(
        source_id="src-1",
        document_identity="doc-1",
        exact_evidence_hashes=(_HASH,),
        publisher="가나다전자",
        source_kind="dart",
    )


def _fact(fact_id: str, section: str, **changes: object) -> ClaimFact:
    fact = ClaimFact(
        fact_id=fact_id,
        section_owner=section,
        source_id="src-1",
        source_identity="doc-1",
        verification_state="verified",
        claim_slot=CLAIM_SLOTS_BY_SECTION[section][0],
        evidence_binding_valid=True,
        claim=f"{fact_id} 공식 자료 사실 문장이다",
        claim_type=VERIFIED_PROSE_CLAIM_TYPE,
        supporting_source_ids=("src-1",),
        supporting_source_identities=("doc-1",),
        supporting_evidence_hashes=(_HASH,),
    )
    return replace(fact, **changes)


def _blocked_candidate() -> ReportCandidate:
    """장 넷·요약·장 없는 문제에 걸친 안전 문제 일곱 건을 가진 후보.

    숫자가 든 문장은 쓰지 않는다 — 산문 수치 결속 판정은 다른 작업이 바꾸는 중이라,
    이 시험이 그 판정의 결과에 기대지 않게 한다.
    """

    facts = (
        _fact("fact-a", "identity"),
        _fact("fact-e", "identity", verification_state="unverified"),
        _fact("fact-b", "portfolio", evidence_binding_valid=False),
        _fact("fact-c", "culture", claim_slot="없는_칸"),
        _fact(
            "fact-d", "culture",
            supporting_source_ids=(),
            supporting_source_identities=(),
            supporting_evidence_hashes=(),
        ),
    )
    sections = (
        ReportSectionCandidate(section_id="identity", fact_ids=("fact-a", "fact-e")),
        ReportSectionCandidate(section_id="business_model", fact_ids=("ghost-1",)),
        ReportSectionCandidate(section_id="portfolio", fact_ids=("fact-b",)),
        ReportSectionCandidate(
            section_id="past_changes", fact_ids=(), has_unbound_public_content=True,
        ),
        ReportSectionCandidate(section_id="culture", fact_ids=("fact-c", "fact-d")),
    )
    return ReportCandidate(
        sections=sections,
        facts=facts,
        sources=(_source(),),
        has_unbound_summary_content=True,
    )


def _fact_sections(candidate: ReportCandidate) -> dict[str, str]:
    return {fact.fact_id: fact.section_owner for fact in candidate.facts}


def test_실제_안전_판정_문장으로_유형과_장별_건수를_센다() -> None:
    candidate = _blocked_candidate()
    assessment = assess_generation(
        candidate, contract_version=STRICT_QUALITY_CONTRACT_VERSION,
    )

    record = safety_block_record(
        assessment.safety.problems, _fact_sections(candidate), validation_round="1차",
    )

    assert record == {
        "step": _STEP,
        "회차": "1차",
        "문제수": 7,
        "유형별": {
            "unbound_public_content": 1,
            "summary_binding": 1,
            "unverified_claim": 1,
            "claim_slot": 1,
            "evidence_binding_invalid": 1,
            "fact_registry": 1,
            "exact_evidence": 1,
        },
        # 장을 가릴 수 없는 두 건(장부에 없는 공개 id·미검증 claim 집계)은 장별에 없다.
        "장별": {"portfolio": 1, "past_changes": 1, "culture": 2, "summary": 1},
    }
    # 유형은 닫힌 코드 목록 순서, 장은 보고서 장 순서(요약은 끝).
    assert list(record["유형별"]) == [
        "unbound_public_content", "summary_binding", "unverified_claim",
        "claim_slot", "evidence_binding_invalid", "fact_registry", "exact_evidence",
    ]
    assert list(record["장별"]) == ["portfolio", "past_changes", "culture", "summary"]
    assert observed_composition_steps([record]) == (record,)
    text = json.dumps(record, ensure_ascii=False)
    for secret in ("fact-", "ghost-1", "src-1", "doc-1", "문장이다", "결속되지 않은"):
        assert secret not in text


def test_fact_id_앞부분만_겹친_다른_사실로_장을_세지_않는다() -> None:
    fact_sections = {"fact-1": "identity", "fact-10": "culture"}
    problems = (
        "fact-10의 원문·주장 결속 지문이 유효하지 않습니다",
        "fact-1: 뉴스 산문의 숫자·단위가 정확 원문과 다릅니다",
        "fact-1가 존재하지 않는 source_id를 참조합니다",
    )

    record = safety_block_record(problems, fact_sections, validation_round="보충")

    assert record["장별"] == {"identity": 2, "culture": 1}
    assert record["유형별"] == {
        "claim_detail": 1, "evidence_binding_invalid": 1, "source_reference": 1,
    }


# ══════════════════════════════════════════════════════════
# (3) run_v2 배선 — 판정이 막으면 예외 «전»에 한 줄
# ══════════════════════════════════════════════════════════


def _blocked_full(diagnostics: list[dict]) -> None:
    """첫 검수 행이 깨지고 재요청 자리가 전역 장애로 멈춰 아홉 장이 안내문만 남는 실행."""

    _full(
        initial_reviewer_ask=_FirstRowBrokenReviewer(),
        initial_retry_reviewer_ask=_GlobalFailureReviewer(),
        preserve_on_ask_failure=True,
        composition_diagnostics_sink=diagnostics,
    )


def test_1차_공개안전_차단은_예외_전에_줄_하나를_남기고_정화기를_그대로_지난다() -> None:
    diagnostics: list[dict] = []
    with pytest.raises(
        V2ValidationError, match="report_recovery:post_validation_safety_blocked",
    ):
        _blocked_full(diagnostics)

    # 공개할 claim 이 하나도 없다는 문제는 어느 장의 것도 아니라 장별이 비어 있다.
    expected = {
        "step": _STEP,
        "회차": "1차",
        "문제수": 1,
        "유형별": {"no_public_claim": 1},
        "장별": {},
    }
    assert _safety_lines(diagnostics) == [expected]
    assert _safety_lines(list(observed_composition_steps(diagnostics))) == [expected]


def test_정상_FULL_출고에는_공개안전_차단_줄이_없다() -> None:
    diagnostics: list[dict] = []
    output = _full(
        initial_reviewer_ask=_BoundGroupedReviewer(),
        initial_retry_reviewer_ask=_BoundGroupedReviewer(),
        composition_diagnostics_sink=diagnostics,
    )

    assert output.effective_release_mode == "FULL"
    assert _safety_lines(diagnostics) == []


def test_품질_하한_중단에는_공개안전_차단_줄이_없다() -> None:
    diagnostics: list[dict] = []
    with pytest.raises(
        V2ValidationError, match="report_recovery:too_many_underfilled_sections",
    ):
        run_v2(
            "가나다전자",
            _strict_fragments(),
            None,
            writer_ask=_StrictThinWriter(),
            reviewer_ask=_FakeReviewer(),
            release_mode=ReleaseMode.FULL,
            section_evidence_packets=_strict_packet_set(),
            company_id="00123456",
            build_identity_sha256="b" * 64,
            composition_diagnostics_sink=diagnostics,
        )

    assert _safety_lines(diagnostics) == []


def test_보충_회차의_공개안전_차단도_보충_줄_하나를_남긴다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 보충 회차의 «두 번째» 평가에만 안전 문제 세 건을 더해, 실제 회복 정책이
    # post_supplement_safety_blocked 로 닫게 한다(판정·영수증은 운영 코드 그대로).
    original = pipeline_module.assess_and_observe_generation
    original_problems: list[tuple[str, ...]] = []

    def second_round_blocked(candidate, **kwargs):
        assessment, observation = original(candidate, **kwargs)
        original_problems.append(assessment.safety.problems)
        if len(original_problems) != 2:
            return assessment, observation
        owner_to_fact = {fact.section_owner: fact.fact_id for fact in candidate.facts}
        injected = (
            f"{owner_to_fact['identity']}의 수치에 versioned NumericBinding이 없습니다",
            f"{owner_to_fact['culture']}의 구조화 수치 이름표가 비었습니다",
            "검증하지 못한 공개 claim이 있습니다",
        )
        blocked = replace(
            assessment,
            safety=replace(
                assessment.safety,
                decision=ReleaseDecision.BLOCKED,
                problems=(*assessment.safety.problems, *injected),
            ),
            publication_grade=QualityGrade.INCOMPLETE,
        )
        return blocked, observation

    monkeypatch.setattr(
        pipeline_module, "assess_and_observe_generation", second_round_blocked,
    )
    diagnostics: list[dict] = []
    with pytest.raises(
        V2ValidationError, match="report_recovery:post_supplement_safety_blocked",
    ):
        run_v2(
            "가나다전자",
            (),
            None,
            writer_ask=_RecoveringPacketWriter(("identity",)),
            reviewer_ask=_BoundGroupedReviewer(),
            diagram_ask=_NoDiagram(),
            release_mode=ReleaseMode.FULL,
            section_evidence_packets=_packets(),
            company_id="00123456",
            build_identity_sha256="b" * 64,
            composition_diagnostics_sink=diagnostics,
        )

    # 두 회차의 원래 판정에는 안전 문제가 없었다 — 1차 줄은 없고, 보충 줄의 건수는
    # 넣은 세 건 그대로다.
    assert original_problems == [(), ()]
    assert _safety_lines(diagnostics) == [{
        "step": _STEP,
        "회차": "보충",
        "문제수": 3,
        "유형별": {
            "numeric_labels_missing": 1,
            "numeric_binding_missing": 1,
            "unverified_claim": 1,
        },
        "장별": {"identity": 1, "culture": 1},
    }]


def test_진단_줄을_못_만들어도_출고_검증_차단은_그대로다(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    def broken(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("비공개 원문이 든 오류 문구")

    monkeypatch.setattr(quality_observation_log, "safety_block_record", broken)
    diagnostics: list[dict] = []
    logger_name = pipeline_module.logger.name
    with caplog.at_level(logging.WARNING, logger=logger_name), pytest.raises(
        V2ValidationError, match="report_recovery:post_validation_safety_blocked",
    ):
        _blocked_full(diagnostics)

    assert _safety_lines(diagnostics) == []
    warnings = [
        record.getMessage() for record in caplog.records
        if record.name == logger_name and "진단 줄" in record.getMessage()
    ]
    assert warnings == ["FULL 공개 안전 차단 진단 줄을 만들지 못했습니다(RuntimeError)"]
