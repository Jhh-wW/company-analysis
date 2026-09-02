"""P11~P25 무과금 shadow 하네스가 저장 자료만 세는지 확인한다.

★ 이 시험은 생산 임계값(40·0.50·8)을 import해 그대로 비교하지 않는다.
  관측값은 생산 평가기(`observe_generation`)가 만들고, 최종 게이트 사유는
  생산 분류기(`classify_v2_validation_final_gate_reason`)를 시험에서 독립으로
  다시 돌려 대조한다. 하네스가 세어 놓은 숫자가 저장된 관측값과 같은지만
  단정한다.

★ 회사 자료를 흉내내지 않는다. 아래 후보 값은 모두 합성 입력이며, 실제
  P11~P25 회사에서 온 사실이 아니다.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from src.features.pilot_evaluation.manifest import (
    APPROVED_PAID_CASE_IDS,
    CANONICAL_PILOT_CASES,
)
from src.features.pilot_evaluation.shadow_harness import (
    SHADOW_MATERIAL_NONE,
    SHADOW_MATERIAL_OBSERVED,
    SHADOW_MATERIAL_UNASSESSABLE,
    LegacyFixtureIndex,
    ShadowHarnessReport,
    build_shadow_report,
    load_legacy_pilot_fixture_index,
    observation_rows,
    summarize_quality_floor,
)
from src.features.pilot_evaluation.shadow_store import (
    SHADOW_CASE_IDS,
    ShadowObservationRecord,
)
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_REASON_PUBLISH_BLOCKED,
    FINAL_GATE_REASON_PUBLISH_BLOCKED_QUALITY_FLOOR,
    QUALITY_FLOOR_PROBLEM_CODES,
    classify_v2_validation_final_gate_reason,
)
from src.shared.report_claim_policy import CLAIM_SECTION_IDS, CLAIM_SLOTS_BY_SECTION
from src.shared.report_quality.dto import (
    ClaimFact,
    ReportCandidate,
    ReportSectionCandidate,
    SourceDocument,
)
from src.shared.report_quality.generation import (
    GenerationQualityObservation,
    observe_generation,
)


_RECORDED_AT = "2026-09-02T00:00:00Z"
_APP_SRC = Path(__file__).resolve().parents[3]
_REPO_ROOT = Path(__file__).resolve().parents[5]
#: 저장소에 git으로 추적되는 옛 파일럿 실행 기록(무과금·이미 저장된 자료).
_LEGACY_RUNS_PATH = _REPO_ROOT / "analysis_engine" / "data" / "pilot" / "runs.jsonl"


def _evidence_hash(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _full_candidate(*, unbound_section: str = "") -> ReportCandidate:
    """9장을 모두 채운 합성 후보. 숫자 토큰이 없어 수치 결속을 요구하지 않는다."""

    sections: list[ReportSectionCandidate] = []
    facts: list[ClaimFact] = []
    sources: list[SourceDocument] = []
    for section_id in CLAIM_SECTION_IDS:
        source_id = f"source-{section_id}"
        identity = f"합성 문서 {section_id}"
        hashes = tuple(
            _evidence_hash(f"{section_id}:{slot}")
            for slot in CLAIM_SLOTS_BY_SECTION[section_id]
        )
        sources.append(
            SourceDocument(
                source_id=source_id,
                document_identity=identity,
                exact_evidence_hashes=hashes,
            )
        )
        fact_ids: list[str] = []
        for slot, evidence in zip(CLAIM_SLOTS_BY_SECTION[section_id], hashes):
            fact_id = f"fact-{slot}"
            fact_ids.append(fact_id)
            facts.append(
                ClaimFact(
                    fact_id=fact_id,
                    section_owner=section_id,
                    source_id=source_id,
                    source_identity=identity,
                    verification_state="verified",
                    claim_slot=slot,
                    evidence_binding_valid=True,
                    claim=f"합성 시험 문장 {slot}",
                    supporting_source_ids=(source_id,),
                    supporting_source_identities=(identity,),
                    supporting_evidence_hashes=(evidence,),
                )
            )
        sections.append(
            ReportSectionCandidate(
                section_id=section_id,
                fact_ids=tuple(fact_ids),
                has_unbound_public_content=section_id == unbound_section,
            )
        )
    return ReportCandidate(
        sections=tuple(sections), facts=tuple(facts), sources=tuple(sources)
    )


def _thin_candidate(*, verification_states: tuple[str, ...]) -> ReportCandidate:
    """한 장만 채운 합성 후보. 장 수·출처 수가 모자란 저품질 관측을 만든다."""

    slots = CLAIM_SLOTS_BY_SECTION["identity"][: len(verification_states)]
    source_id = "source-thin"
    identity = "합성 문서 얇음"
    facts = tuple(
        ClaimFact(
            fact_id=f"thin-{slot}",
            section_owner="identity",
            source_id=source_id,
            source_identity=identity,
            verification_state=state,
            claim_slot=slot,
            evidence_binding_valid=True,
            claim=f"합성 시험 문장 {slot}",
        )
        for slot, state in zip(slots, verification_states)
    )
    return ReportCandidate(
        sections=(
            ReportSectionCandidate(
                section_id="identity",
                fact_ids=tuple(fact.fact_id for fact in facts),
            ),
        ),
        facts=facts,
        sources=(SourceDocument(source_id=source_id, document_identity=identity),),
    )


def _observations() -> dict[str, GenerationQualityObservation]:
    """네 가지 판정 모양을 생산 평가기로 실제로 만들어 둔다."""

    return {
        # 통과: 9장을 채우고 전부 검증됨.
        "P11": observe_generation(_full_candidate()),
        # 통과하지만 품질 하한 코드는 붙음(장·출처가 모자람).
        "P12": observe_generation(
            _thin_candidate(verification_states=("verified", "verified"))
        ),
        # 막힘 + 품질 하한 코드.
        "P19": observe_generation(
            _thin_candidate(
                verification_states=("verified", "unverified", "unverified")
            )
        ),
        # 막힘이지만 품질 하한이 아닌 구조 결속 문제.
        "P20": observe_generation(_full_candidate(unbound_section="culture")),
    }


def _records(
    observations: dict[str, GenerationQualityObservation],
) -> tuple[ShadowObservationRecord, ...]:
    category_by_case_id = {
        case.case_id: case.category.value for case in CANONICAL_PILOT_CASES
    }
    return tuple(
        ShadowObservationRecord(
            case_id=case_id,
            category=category_by_case_id[case_id],
            fixture_ref=f"synthetic-{case_id}",
            recorded_at=_RECORDED_AT,
            observation=observation,
        )
        for case_id, observation in observations.items()
    )


def _module_imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return tuple(modules)


def _module_path(module: str) -> Path | None:
    relative = Path(*module.split(".")[1:])
    for candidate in (
        _APP_SRC / relative.with_suffix(".py"),
        _APP_SRC / relative / "__init__.py",
    ):
        if candidate.is_file():
            return candidate
    return None


def _transitive_imports(entry: Path) -> frozenset[str]:
    """`src.` 모듈만 따라가며 import 이름을 전부 모은다."""

    seen: set[str] = set()
    pending = [entry]
    visited_paths: set[Path] = set()
    while pending:
        path = pending.pop()
        if path in visited_paths:
            continue
        visited_paths.add(path)
        for module in _module_imports(path):
            seen.add(module)
            if module.startswith("src."):
                resolved = _module_path(module)
                if resolved is not None:
                    pending.append(resolved)
    return frozenset(seen)


def test_P11_25만_읽고_provider_호출_0(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core.provider_gateway import gateway

    calls: list[object] = []

    def _spy(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        raise AssertionError("shadow 하네스는 유료 provider를 부르면 안 됩니다")

    monkeypatch.setattr(gateway, "call_once", _spy)

    index = load_legacy_pilot_fixture_index(_LEGACY_RUNS_PATH)
    report = build_shadow_report(
        _records(_observations()),
        unassessable_fixture_refs={"P14": ("재수집-p014",)},
    )

    assert calls == []
    assert index.matched_record_count >= 0
    assert tuple(material.case_id for material in report.materials) == SHADOW_CASE_IDS
    assert not (
        {material.case_id for material in report.materials} & APPROVED_PAID_CASE_IDS
    )


def test_하네스_모듈은_provider_network를_import하지_않는다() -> None:
    entry = _APP_SRC / "features" / "pilot_evaluation" / "shadow_harness.py"
    # `urllib.parse`는 막지 않는다 — 문자열만 다루는 주소 정규화이고 실제로
    # 생산 평가기(report_quality.source_identity)가 쓴다. 망을 탈 수 있는
    # `urllib.request`·`urllib.error`만 금지한다.
    forbidden_prefixes = (
        "src.core.provider_gateway",
        "src.features.composer",
        "src.features.pipeline",
        "src.features.business_candidate",
        "src.web",
        "socket",
        "ssl",
        "http",
        "urllib.request",
        "urllib.error",
        "requests",
        "httpx",
        "anthropic",
        "openai",
        "google",
    )

    modules = _transitive_imports(entry)

    assert [
        module
        for module in sorted(modules)
        if module.startswith(forbidden_prefixes)
    ] == []


def test_3코드_분포_표가_결정론적이다() -> None:
    observations = _observations()
    records = _records(observations)
    reversed_records = tuple(reversed(records))

    first = build_shadow_report(records)
    second = build_shadow_report(reversed_records)

    assert first == second
    # 표는 항상 세 코드를 모두 보여준다 — 0건도 0으로 적는다.
    assert tuple(
        code for code, _count in first.quality_floor.quality_floor_code_counts
    ) == tuple(sorted(QUALITY_FLOOR_PROBLEM_CODES))
    # 세어진 값이 저장된 관측값과 같은가.
    expected_counts = tuple(
        (
            code,
            sum(
                1
                for observation in observations.values()
                if code in observation.quality_problem_codes
            ),
        )
        for code in sorted(QUALITY_FLOOR_PROBLEM_CODES)
    )
    assert first.quality_floor.quality_floor_code_counts == expected_counts
    assert first.summary.total_count == len(observations)
    assert first.summary.release_blocked_count == sum(
        1 for observation in observations.values() if not observation.release_allowed
    )


def test_최종게이트_사유는_생산_분류기와_같고_막힌_관측값에만_붙는다() -> None:
    observations = _observations()

    report = build_shadow_report(_records(observations))

    expected: dict[str, int] = {}
    for observation in observations.values():
        if observation.release_allowed:
            continue
        reason = classify_v2_validation_final_gate_reason(
            observation.quality_problem_codes
        )
        expected[reason] = expected.get(reason, 0) + 1
    assert report.quality_floor.blocked_final_gate_reason_counts == tuple(
        (key, expected[key]) for key in sorted(expected)
    )
    # 막힌 관측값 수와 사유 건수 합이 같다 — 통과한 보고서에는 사유가 없다.
    assert sum(expected.values()) == report.summary.release_blocked_count
    assert set(expected) == {
        FINAL_GATE_REASON_PUBLISH_BLOCKED,
        FINAL_GATE_REASON_PUBLISH_BLOCKED_QUALITY_FLOOR,
    }
    # 통과했지만 품질 하한 코드가 붙은 관측값이 실제로 있다(사유 0으로 세는 근거).
    assert any(
        observation.release_allowed
        and set(observation.quality_problem_codes) & QUALITY_FLOOR_PROBLEM_CODES
        for observation in observations.values()
    )


def test_fixture_없는_후보는_자료없음으로_센다_발명_0() -> None:
    observations = _observations()

    report = build_shadow_report(
        _records(observations),
        unassessable_fixture_refs={"P14": ("재수집-p014",)},
    )

    assert report.candidate_count == len(SHADOW_CASE_IDS)
    assert report.observed_count == len(observations)
    assert report.unassessable_count == 1
    assert report.no_material_count == len(SHADOW_CASE_IDS) - len(observations) - 1
    # 자료가 없는 후보를 관측값처럼 세지 않는다.
    assert report.summary.total_count == len(observations)
    assert report.summary.release_blocked_count <= len(observations)
    states = {
        material.case_id: material.material_state for material in report.materials
    }
    assert states["P11"] == SHADOW_MATERIAL_OBSERVED
    assert states["P14"] == SHADOW_MATERIAL_UNASSESSABLE
    assert states["P13"] == SHADOW_MATERIAL_NONE
    assert sorted(states) == sorted(SHADOW_CASE_IDS)
    # 판정 불가 자료는 회사 유형 분해에서도 관측값으로 새지 않는다.
    by_category = {row.category: row for row in report.by_category}
    assert by_category["unlisted_disclosure"].unassessable_count == 1
    assert by_category["unlisted_disclosure"].summary.total_count == 2


def test_회사유형별_분해는_P11_25에_실제로_있는_유형만_보여준다() -> None:
    report = build_shadow_report(_records(_observations()))

    categories = tuple(row.category for row in report.by_category)
    expected_categories = tuple(
        sorted(
            {
                case.category.value
                for case in CANONICAL_PILOT_CASES
                if case.case_id in set(SHADOW_CASE_IDS)
            }
        )
    )
    assert categories == expected_categories
    assert "listed" not in categories
    assert sum(row.candidate_count for row in report.by_category) == len(
        SHADOW_CASE_IDS
    )
    assert sum(row.summary.total_count for row in report.by_category) == (
        report.summary.total_count
    )


def test_P01_P10_기록은_하네스가_거절한다() -> None:
    observation = observe_generation(
        _thin_candidate(verification_states=("verified", "verified"))
    )
    paid_record = ShadowObservationRecord(
        case_id="P01",
        category="listed",
        fixture_ref="synthetic",
        recorded_at=_RECORDED_AT,
        observation=observation,
    )

    with pytest.raises(ValueError):
        build_shadow_report((paid_record,))

    with pytest.raises(ValueError):
        build_shadow_report((), unassessable_fixture_refs={"P01": ("x",)})


def test_같은_후보를_관측값과_판정불가에_동시에_넣으면_거절한다() -> None:
    records = _records(_observations())

    with pytest.raises(ValueError):
        build_shadow_report(records, unassessable_fixture_refs={"P11": ("x",)})

    with pytest.raises(ValueError):
        build_shadow_report(records + records[:1])


def test_요약기_입력행은_후보_신원을_그대로_옮긴다() -> None:
    observations = _observations()
    records = _records(observations)

    rows = observation_rows(records)

    legal_name_by_case_id = {
        case.case_id: case.expected_legal_name for case in CANONICAL_PILOT_CASES
    }
    assert tuple(row[0] for row in rows) == tuple(
        record.case_id for record in records
    )
    assert tuple(row[1] for row in rows) == tuple(
        legal_name_by_case_id[record.case_id] for record in records
    )
    assert tuple(row[2] for row in rows) == tuple(
        record.recorded_at for record in records
    )
    # 네 번째 칸은 계약 판(v1/FULL)이다 — 저장된 관측값이 스스로 들고 있는 값.
    assert tuple(row[3] for row in rows) == tuple(
        record.observation.contract_version for record in records
    )


def test_품질하한_집계는_관측값_목록만으로도_같은_값을_준다() -> None:
    observations = tuple(_observations().values())

    direct = summarize_quality_floor(observations)
    through_report = build_shadow_report(_records(_observations())).quality_floor

    assert direct == through_report


def test_저장소_옛_파일럿_기록은_이름이_정확히_같은_후보만_센다() -> None:
    """git으로 추적되는 옛 파일럿 기록 10건 중 P11~P25와 이름이 같은 것만 센다.

    자료가 늘거나 줄면 이 시험이 실패한다. 그때는 숫자를 고치는 게 아니라
    사람이 다시 세어 보라는 뜻이다.
    """

    index = load_legacy_pilot_fixture_index(_LEGACY_RUNS_PATH)

    assert dict(index.refs_by_case_id) == {
        "P11": ("재수집-p003",),
        "P12": ("재수집-p005",),
        "P14": ("재수집-p014",),
        "P16": ("재수집-p031",),
        "P18": ("재수집-p039",),
        "P25": ("재수집-p034",),
    }
    assert index.scanned_record_count == 10
    assert index.matched_record_count == 6
    assert index.unmatched_record_count == 4


def test_옛_기록_이름이_한_글자라도_다르면_세지_않는다(tmp_path: Path) -> None:
    runs_path = tmp_path / "runs.jsonl"
    runs_path.write_text(
        "\n".join(
            json.dumps(payload, ensure_ascii=False)
            for payload in (
                {"id": "run-1", "input": {"company": "우리엔"}},
                {"id": "run-2", "input": {"company": "우리엔 "}},
                {"id": "run-3", "input": {"company": "주식회사 우리엔"}},
                # P01~P10 유료 후보는 이름이 같아도 세지 않는다.
                {"id": "run-4", "input": {"company": "삼성전자"}},
                {"id": "run-5", "input": {}},
            )
        )
        + "\n",
        encoding="utf-8",
    )

    index = load_legacy_pilot_fixture_index(runs_path)

    assert dict(index.refs_by_case_id) == {"P11": ("run-1",)}
    assert index.scanned_record_count == 5
    assert index.matched_record_count == 1
    assert index.unmatched_record_count == 4


def test_한_후보에_옛_기록이_여럿이면_모두_정렬해_담는다(tmp_path: Path) -> None:
    runs_path = tmp_path / "runs.jsonl"
    runs_path.write_text(
        "\n".join(
            json.dumps(payload, ensure_ascii=False)
            for payload in (
                {"id": "run-b", "input": {"company": "우리엔"}},
                {"id": "run-a", "input": {"company": "우리엔"}},
            )
        )
        + "\n",
        encoding="utf-8",
    )

    index = load_legacy_pilot_fixture_index(runs_path)

    assert dict(index.refs_by_case_id) == {"P11": ("run-a", "run-b")}
    assert index.matched_record_count == 2


def test_옛_기록이_깨졌으면_조용히_넘기지_않는다(tmp_path: Path) -> None:
    runs_path = tmp_path / "runs.jsonl"
    runs_path.write_text(
        '{"id": "run-1", "input": {"company": "우리엔"}}\n깨진 줄\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_legacy_pilot_fixture_index(runs_path)


def test_옛_기록이_비어_있으면_전부_자료없음이_된다(tmp_path: Path) -> None:
    runs_path = tmp_path / "runs.jsonl"
    runs_path.write_text("", encoding="utf-8")

    index = load_legacy_pilot_fixture_index(runs_path)
    report = build_shadow_report(
        (), unassessable_fixture_refs=dict(index.refs_by_case_id)
    )

    assert index == LegacyFixtureIndex(
        refs_by_case_id=(),
        scanned_record_count=0,
        matched_record_count=0,
        unmatched_record_count=0,
    )
    assert isinstance(report, ShadowHarnessReport)
    assert report.no_material_count == len(SHADOW_CASE_IDS)
    assert report.summary.total_count == 0
    assert report.summary.release_blocked_ratio is None
    assert report.quality_floor.quality_floor_observation_count == 0
