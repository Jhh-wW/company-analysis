# -*- coding: utf-8 -*-
"""FULL 안전 판정의 공식 원문 산문 숫자 예외(ADR 0005) — 실제 run_v2 끝-끝 시험.

2026-09-24 7차 FULL 실측은 날짜·개수 든 확인 등급 공식 원문 산문 15건 때문에
«구조화 수치 결속 없음»으로 통째로 막혔다. 여기서는 같은 모양(숫자 든 확인 산문
15건, 숫자는 인용 조각 원문에 그대로)을 실제 run_v2 에 넣는다. 모든 문장·원문은 가짜다.

음성의 주 단정은 assess_safety 단위 시험(``shared/report_quality/tests``)이 맡는다.
끝-끝에서 원문에 없는 숫자는 대개 앞 단계 숫자 필터가 게이트 전에 뺀다 — 여기서는
«게이트 전 제외 + 나머지 FULL 출고»와, 앞 단계가 값으로 통과시키는 표기 변경이
«게이트에서 막힘»을 함께 본다.
"""

from __future__ import annotations

import json

import pytest

from src.features.composer.pipeline import run_v2
from src.features.composer.tests import test_section_public_manifest as full_fixture
from src.features.composer.tests.test_section_public_manifest import (
    _BoundGroupedReviewer,
    _CompletePacketWriter,
    _NoDiagram,
    _packets,
    _RecoveringPacketWriter,
)
from src.features.composer.validate import V2ValidationError
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_quality import assessment as assessment_module
from src.shared.report_quality.constants import (
    OFFICIAL_PROSE_EXACT_TEXT_KEY,
    STRICT_QUALITY_CONTRACT_VERSION,
)
from src.shared.report_quality.numeric_detection import has_public_numeric_token

#: (장, 문장 번호) → 문장에 끼워 넣는 숫자 문구. 인용 조각 원문에도 같은 글자로
#: 들어간다. 7차 실측처럼 날짜가 대부분이고 개수가 일부다 — 합계 15건.
_NUMERIC_PHRASES: dict[tuple[str, int], str] = {
    ("identity", 0): "2019년 3월 15일 기준",
    ("identity", 1): "2021년 1월 기준",
    ("identity", 2): "12개 항목 기준",
    ("identity", 3): "2020년 7월 1일 기준",
    ("identity", 4): "3개 부문 기준",
    ("business_model", 1): "2022년 기준",
    ("business_model", 3): "4개 항목 기준",
    ("portfolio", 1): "5개 제품군 기준",
    ("portfolio", 3): "2018년 4월 기준",
    ("past_changes", 1): "2017년 기준",
    ("past_changes", 3): "2023년 12월 기준",
    ("current_challenges", 1): "2024년 기준",
    ("current_challenges", 3): "2개 과제 기준",
    ("operations_partners", 1): "7곳 기준",
    ("operations_partners", 3): "2016년 기준",
}
_LABELS = "의 구조화 수치 이름표가 비었습니다"
_BINDING = "의 수치에 versioned NumericBinding이 없습니다"


def _install_numeric_sentences(
    monkeypatch: pytest.MonkeyPatch,
    *,
    source_phrases: dict[tuple[str, int], str] | None = None,
    writer_only: dict[tuple[str, int], str] | None = None,
) -> None:
    """작가 문장과 조각 원문에 같은 숫자 문구를 넣는다.

    ``source_phrases`` 는 «조각 원문»에만 대신 쓰는 문구(작가가 표기를 바꿔 옮김),
    ``writer_only`` 는 «작가 문장»에만 넣는 문구(원문에 없는 숫자)다.
    """

    original = full_fixture._section_sentence
    phrases = {**_NUMERIC_PHRASES, **(writer_only or {})}

    def with_numbers(section_id: str, mark: str, ending_index: int, ending: str) -> str:
        sentence = original(section_id, mark, ending_index, ending)
        owner = full_fixture.SECTION_IDS[full_fixture._MARKS.index(mark)]
        key = (owner, ending_index)
        phrase = phrases.get(key)
        # 조각 원문을 만들 때는 장 이름 없이 부른다(``_fragment_text``).
        building_fragment = not section_id
        if phrase is None or (building_fragment and writer_only and key in writer_only):
            return sentence
        if building_fragment and source_phrases and key in source_phrases:
            phrase = source_phrases[key]
        return sentence.replace(f"{ending} ", f"{ending} {phrase} ", 1)

    monkeypatch.setattr(full_fixture, "_section_sentence", with_numbers)


def _run(release_mode: ReleaseMode):
    arguments: dict[str, object] = {
        "writer_ask": _CompletePacketWriter(),
        "reviewer_ask": _BoundGroupedReviewer(),
        "diagram_ask": _NoDiagram(),
        "release_mode": release_mode,
        "section_evidence_packets": _packets(),
    }
    if release_mode is not ReleaseMode.SHADOW:
        arguments.update(company_id="00123456", build_identity_sha256="b" * 64)
    return run_v2("가나다전자", (), None, **arguments)


def _spy_safety(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, tuple[str, ...]]]:
    """실제 assess_safety 를 그대로 부르고 (계약 버전, 문제 목록)만 기록한다."""

    calls: list[tuple[str, tuple[str, ...]]] = []
    original = assessment_module.assess_safety

    def spy(candidate, contract):
        result = original(candidate, contract)
        calls.append((contract.version, tuple(result.problems)))
        return result

    monkeypatch.setattr(assessment_module, "assess_safety", spy)
    return calls


def _carries_official_text(fact) -> bool:
    try:
        manifest = json.loads(fact.state_evidence or "[]")
    except ValueError:
        return False  # 구조화 사실의 증거는 목록 JSON이 아니다.
    return isinstance(manifest, list) and any(
        isinstance(record, dict) and OFFICIAL_PROSE_EXACT_TEXT_KEY in record
        for record in manifest
    )


def _numeric_prose_facts(report) -> list:
    return [
        fact for fact in report.fact_records
        if fact.fact_id.startswith("v2-prose-") and has_public_numeric_token(fact.claim)
    ]


def test_7차_모양_숫자든_확인산문_15건이_원문에_그대로면_FULL로_나간다(monkeypatch):
    _install_numeric_sentences(monkeypatch)

    output = _run(ReleaseMode.FULL)

    assert output.effective_release_mode == "FULL"
    observation = output.quality_observation
    assert observation is not None
    assert observation.release_allowed is True
    assert observation.safety_problems == ()
    # 숫자 든 확인 산문 15건이 «실제로» 공개본에 실렸고 모두 인용 원문을 싣는다 —
    # 앞 단계에서 빠졌다면 이 시험은 아무것도 증명하지 못한다.
    numeric = _numeric_prose_facts(output.report)
    assert len(numeric) == 15
    assert all(_carries_official_text(fact) for fact in numeric)


def test_원문_증명을_끄면_같은_실행이_7차처럼_막힌다(monkeypatch):
    """음성 대조 — 새 예외 한 곳만 끄면 15건 × 예전 두 문구로 통째로 막힌다."""

    _install_numeric_sentences(monkeypatch)
    monkeypatch.setattr(
        assessment_module, "_official_prose_numbers_proven", lambda *_args: False,
    )
    calls = _spy_safety(monkeypatch)

    with pytest.raises(V2ValidationError, match="post_validation_safety_blocked"):
        _run(ReleaseMode.FULL)

    full_problems = [
        problems for version, problems in calls
        if version == STRICT_QUALITY_CONTRACT_VERSION
    ]
    assert len(full_problems) == 1
    (problems,) = full_problems
    assert len(problems) == 30
    assert sum(problem.endswith(_LABELS) for problem in problems) == 15
    assert sum(problem.endswith(_BINDING) for problem in problems) == 15


def test_작가가_원문_날짜_표기를_바꿔_옮기면_게이트에서_막힌다(monkeypatch):
    """값이 같아 앞 단계 숫자 대조는 통과한다 — «원문 그대로» 겹만 막는다(음성)."""

    _install_numeric_sentences(
        monkeypatch, source_phrases={("identity", 0): "2019.03.15 기준"},
    )
    calls = _spy_safety(monkeypatch)

    with pytest.raises(V2ValidationError, match="post_validation_safety_blocked"):
        _run(ReleaseMode.FULL)

    ((version, problems),) = calls
    assert version == STRICT_QUALITY_CONTRACT_VERSION
    assert len(problems) == 2
    assert problems[0].startswith("v2-prose-") and problems[0].endswith(_LABELS)
    assert problems[1].startswith("v2-prose-") and problems[1].endswith(_BINDING)


def test_원문에_없는_연도는_게이트_전에_빠지고_나머지는_FULL로_나간다(monkeypatch):
    """끝-끝 음성의 기대값 — 게이트 전 제외 1 + 나머지 출고(fail-open 아님)."""

    _install_numeric_sentences(
        monkeypatch, writer_only={("identity", 5): "1987년 기준"},
    )
    # 작가는 그 연도를 쓰고, 인용 조각 원문에는 없다 — 대역이 실제로 갈렸는지 먼저 본다.
    mark = full_fixture._MARKS[full_fixture.SECTION_IDS.index("identity")]
    ending = full_fixture._ENDINGS[5]
    assert "1987년" in full_fixture._section_sentence("identity", mark, 5, ending)
    assert "1987년" not in full_fixture._section_sentence("", mark, 5, ending)

    output = _run(ReleaseMode.FULL)

    assert output.effective_release_mode == "FULL"
    assert output.quality_observation.safety_problems == ()
    claims = [fact.claim for fact in output.report.fact_records]
    prose = " ".join(
        str(line) for section in output.report.sections for line in section.prose_lines
    )
    assert not any("1987년" in claim for claim in claims)
    assert "1987년" not in prose
    assert len(_numeric_prose_facts(output.report)) == 15


def test_FULL과_SHADOW에서_숫자_산문_fact_id가_같다(monkeypatch):
    """fact_id 불변 — FULL만 원문을 싣지만 ID 입력에서는 원문을 뺀다."""

    _install_numeric_sentences(monkeypatch)

    full = _run(ReleaseMode.FULL)
    shadow = _run(ReleaseMode.SHADOW)

    full_ids = {fact.claim: fact.fact_id for fact in _numeric_prose_facts(full.report)}
    shadow_ids = {
        fact.claim: fact.fact_id for fact in _numeric_prose_facts(shadow.report)
    }
    assert len(full_ids) == 15
    assert full_ids == shadow_ids


def test_SHADOW는_사실_바이트와_관측_판정_표지가_예전과_같다(monkeypatch):
    """부분 보고서(관측 전용)에는 예외도 원문 동봉도 없다(ADR 0005 결정 (가))."""

    _install_numeric_sentences(monkeypatch)

    output = _run(ReleaseMode.SHADOW)

    numeric = _numeric_prose_facts(output.report)
    assert len(numeric) == 15
    assert not any(_carries_official_text(fact) for fact in numeric)
    observation = output.quality_observation
    for fact in numeric:
        assert f"{fact.fact_id}{_LABELS}" in observation.safety_problems
        assert f"{fact.fact_id}{_BINDING}" in observation.safety_problems
    assert observation.release_allowed is False
    assert output.report.publication_policy == "legacy-shadow-exception-v1"


def test_ENFORCE_NO_PARTIAL은_예전처럼_숫자_산문을_막는다(monkeypatch):
    _install_numeric_sentences(monkeypatch)

    with pytest.raises(V2ValidationError) as caught:
        _run(ReleaseMode.ENFORCE_NO_PARTIAL)

    problems = caught.value.problems
    labels = [problem for problem in problems if problem.endswith(_LABELS)]
    binding = [problem for problem in problems if problem.endswith(_BINDING)]
    assert len(labels) == 15
    assert len(binding) == 15


def test_P4_빈_장_복구_문장도_FULL_판정에서_인용_원문을_싣고_통과한다(monkeypatch):
    """복구 문장도 같은 렌더 경로로 사실이 된다 — 따로 새는 길이 없다."""

    from src.features.composer.constants import GRADE_CONFIRMED
    from src.features.composer.tests import test_empty_section_recovery as recovery
    from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION

    dated = "가나다전자는 2021년부터 구성원 역량 개발을 위해 직무 교육을 운영한다."
    monkeypatch.setattr(recovery, "CULTURE_TEXT", dated)
    captured: list[tuple[str, object, tuple[str, ...]]] = []
    original = assessment_module.assess_safety

    def spy(candidate, contract):
        result = original(candidate, contract)
        captured.append((contract.version, candidate, tuple(result.problems)))
        return result

    monkeypatch.setattr(assessment_module, "assess_safety", spy)

    def recovery_writer(_prompt: str) -> str:
        return recovery._response(
            {"culture": [(dated, recovery._RECOVERY_FRAGMENT_ID, GRADE_CONFIRMED)]},
            claim_slot=CLAIM_SLOTS_BY_SECTION["culture"][0],
        )

    recovery._run_full(
        recovery._full_packets(culture_owns_recovery_fragment=True),
        recovery_writer=recovery_writer,
        diagnostics=[],
    )

    full_calls = [
        (candidate, problems) for version, candidate, problems in captured
        if version == STRICT_QUALITY_CONTRACT_VERSION
    ]
    assert full_calls, "FULL 판정이 한 번도 돌지 않았다"
    candidate, problems = full_calls[0]
    (recovered,) = [fact for fact in candidate.facts if fact.claim == dated]
    assert OFFICIAL_PROSE_EXACT_TEXT_KEY in recovered.state_evidence
    assert not any(problem.startswith(recovered.fact_id) for problem in problems)


def test_숫자_산문_FULL이_품질_하한에_걸리면_안전_차단_대신_부분_보고서로_내려간다(monkeypatch):
    """D 검토 F7(효과 범위) — 기준 코드에서 같은 입력은 post_validation_safety_blocked였다.

    숫자 산문이 안전을 통과하면 품질 하한(얇은 장)이 다음 관문이 되고, 운영(real.py는
    FULL에 preserve_on_ask_failure=True)에서는 보고서 없는 중단 대신 확보 근거 부분
    보고서로 내려간다.
    """

    _install_numeric_sentences(monkeypatch)

    output = run_v2(
        "가나다전자",
        (),
        None,
        writer_ask=_RecoveringPacketWriter(("business_model",), remain_thin=True),
        reviewer_ask=_BoundGroupedReviewer(),
        diagram_ask=_NoDiagram(),
        release_mode=ReleaseMode.FULL,
        section_evidence_packets=_packets(),
        company_id="00123456",
        build_identity_sha256="b" * 64,
        preserve_on_ask_failure=True,
    )

    assert output.effective_release_mode == "SHADOW"
    assert output.downgraded_from_release_mode == "FULL"
