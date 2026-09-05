"""근거 재판정의 수집→작성→봉인 전 사슬을 무과금으로 재현한다.

실제 DART 감사보고서 XML 모양의 로컬 픽스처를 엔진 수집기와 serializer에
통과시키고, 홈페이지·provider만 메모리 가짜로 바꾼다. 따라서 문지기 채점,
재판정, preflight, composer 조립과 검증은 생산 코드의 같은 경계를 지난다.
"""

from __future__ import annotations

import base64
import gzip
import importlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from xml.etree import ElementTree

import pytest

from src.core import deployment_identity
from src.core import evidence_reclassify_switch as reclassify_switch
from src.core.provider_gateway import attempt_context
from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks
from src.features.budget import provider_budget
from src.features.composer.constants import SECTION_GUIDES
from src.features.composer.tests.test_e2e_offline import (
    COMPANY_NAME,
    _JypFakeClient,
    _JypFakeEngine,
    _JypFakeMessages,
)
from src.features.pipeline import real
from src.features.pipeline.evidence_reclassify_step import RECLASSIFY_STEP_NAME
from src.features.pipeline.official_evidence_preflight import (
    DART_PARTIAL_REASON_INSUFFICIENT_WITH_READY_SECTIONS,
    DART_PARTIAL_REASON_TOO_FEW_DOCUMENTS_FOR_FULL,
)
from src.features.pipeline.port import CompanyCard, Outcome, RunResult, UserInput
from src.features.pipeline.tests.test_real_cache import CORP_ID, JOB, POSTING
from src.shared.report_evidence.constants import EvidenceReadiness, ReleaseMode
from src.shared.report_evidence.policy import collector_slots_for
from src.shared.report_evidence.release_mode import parse_release_mode
from src.web import official_evidence_adapter


_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "reclassify"
_INIZY_FIXTURE = "inizy_like_audit_report.xml"
_HIVE_FIXTURE = "hive_like_audit_report.xml"
_GOLDEN_FIXTURE = _FIXTURE_DIR / "off_result_golden.json"
_RECEIPT_NUMBER = "20260315000123"
_DEPLOYMENT_COMMIT = "e" * 40

_PRODUCT_SENTENCES = (
    "당사는 산업용 인공지능 예측 솔루션을 고객사에 공급한다.",
    "이 솔루션은 설비 이상을 미리 알리는 분석 기능을 맡는다.",
    "고객사는 예측 결과를 생산 계획과 유지보수 판단에 사용한다.",
)
_REVENUE_SENTENCES = (
    "개발 대가와 정부 과제 수입은 해당 솔루션 계약에서 발생한다.",
    "고객과 맺은 계약 금액은 개발 단계의 성과물 인도에 따라 인식된다.",
    "정부 지원 과제 수입도 같은 분석 역량의 수행에서 나온다.",
)
_HIVE_PORTFOLIO_SENTENCES = (
    "핵심 제품인 산업용 인공지능 예측 솔루션은 고객사의 설비 이상을 미리 알린다.",
    "이 솔루션은 현장 분석 기능을 맡는다.",
    "고객사는 예측 결과를 유지보수 판단에 사용한다.",
    "개발 계약 매출 기여는 성과물 인도에서 확인된다.",
    "정부 지원 수입도 같은 분석 역량의 수행에서 나온다.",
    "두 수익 경로는 하나의 분석 기술을 함께 사용한다.",
)
_ACCOUNTING_BOILERPLATE = (
    "한국채택국제회계기준 전환에 따른 회계정책 변경은 현재 해결 과제로 관리한다."
)
_FRAGMENT_BLOCK_RE = re.compile(
    r"\[조각 ([^\]]+)\].*?(?=\n\[조각 |\Z)",
    re.DOTALL,
)
_CANDIDATE_BLOCK_RE = re.compile(
    r"\[후보 문단 ([^\]]+)\].*?(?=\n\[후보 문단 |\Z)",
    re.DOTALL,
)


def _audit_plain_text(name: str) -> str:
    """XML의 제목·문단 순서를 실제 수집기가 받는 평문 모양으로 편다."""

    root = ElementTree.parse(_FIXTURE_DIR / name).getroot()
    blocks: list[str] = []
    for section in root.findall(".//SECTION"):
        blocks.append(str(section.attrib["title"]).strip())
        blocks.extend(
            text
            for paragraph in section.findall("P")
            if (text := "".join(paragraph.itertext()).strip())
        )
    return "\n\n".join(blocks)


def _fragment_id_containing(prompt: str, sentence: str) -> str:
    for matched in _FRAGMENT_BLOCK_RE.finditer(prompt):
        if sentence in matched.group(0):
            return matched.group(1)
    raise AssertionError(f"작가 프롬프트에서 고정 원문을 찾지 못했습니다: {sentence}")


def _candidate_id_containing(prompt: str, sentence: str) -> str:
    for matched in _CANDIDATE_BLOCK_RE.finditer(prompt):
        if sentence in matched.group(0):
            return matched.group(1)
    raise AssertionError(f"재판정 프롬프트에서 고정 원문을 찾지 못했습니다: {sentence}")


class _FixtureMessages(_JypFakeMessages):
    """재판정과 3장만 감사보고서 픽스처에 맞춰 답하는 가짜 provider."""

    def __init__(self, *, reclassify_mode: str) -> None:
        super().__init__()
        self.reclassify_mode = reclassify_mode
        self.reclassify_calls = 0

    def _portfolio_response(self, prompt: str) -> str:
        if _PRODUCT_SENTENCES[0] in prompt:
            groups = (
                (
                    _PRODUCT_SENTENCES,
                    _fragment_id_containing(prompt, _PRODUCT_SENTENCES[0]),
                ),
                (
                    _REVENUE_SENTENCES,
                    _fragment_id_containing(prompt, _REVENUE_SENTENCES[0]),
                ),
            )
        elif _HIVE_PORTFOLIO_SENTENCES[0] in prompt:
            fragment_id = _fragment_id_containing(
                prompt, _HIVE_PORTFOLIO_SENTENCES[0]
            )
            groups = ((_HIVE_PORTFOLIO_SENTENCES, fragment_id),)
        else:
            return json.dumps({"문장들": []}, ensure_ascii=False)
        sentences = [
            {"글": sentence, "인용": [fragment_id], "등급": "확인"}
            for group, fragment_id in groups
            for sentence in group
        ]
        return json.dumps({"문장들": sentences}, ensure_ascii=False)

    def _route(self, prompt: str) -> str | None:
        if SECTION_GUIDES["portfolio"] in prompt:
            return self._portfolio_response(prompt)
        return super()._route(prompt)

    def _reclassify_payload(self, prompt: str) -> dict[str, object]:
        # 9장 재정의 뒤에는 자기 선언 차별점이 없는 회사의 9장도 재판정 대상이 된다.
        # 3장이 빈 장 목록에 없으면(9장만 묻는 프롬프트) 배정 없음으로 답한다.
        if "portfolio:product_role" not in prompt:
            return {"assignments": [], "removals": []}
        product_id = _candidate_id_containing(prompt, _PRODUCT_SENTENCES[0])
        revenue_id = _candidate_id_containing(prompt, _REVENUE_SENTENCES[0])
        accounting_id = _candidate_id_containing(prompt, _ACCOUNTING_BOILERPLATE)
        quotes = (
            (_PRODUCT_SENTENCES[0], _REVENUE_SENTENCES[0])
            if self.reclassify_mode == "valid"
            else ("원문에 없는 제품 문장", "원문에 없는 매출 문장")
        )
        return {
            "assignments": [
                {
                    "paragraph_id": product_id,
                    "section_id": "portfolio",
                    "slot_id": "portfolio:product_role",
                    "quote": quotes[0],
                },
                {
                    "paragraph_id": revenue_id,
                    "section_id": "portfolio",
                    "slot_id": "portfolio:revenue_link",
                    "quote": quotes[1],
                },
            ],
            "removals": (
                [
                    {
                        "paragraph_id": accounting_id,
                        "section_id": "current_challenges",
                        "reason": "장 목적과 무관한 회계 기준 상투문구",
                    }
                ]
                if self.reclassify_mode == "valid"
                else []
            ),
        }

    def create(self, **kwargs: Any) -> SimpleNamespace:
        response = super().create(**kwargs)
        output_format = kwargs.get("output_config", {}).get("format", {})
        schema = output_format.get("schema", {})
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        if set(properties) == {"assignments", "removals"}:
            self.reclassify_calls += 1
            prompt = str((kwargs.get("messages") or [{}])[0].get("content") or "")
            response.content = [
                SimpleNamespace(
                    text=json.dumps(
                        self._reclassify_payload(prompt),
                        ensure_ascii=False,
                    )
                )
            ]
        return response


class _FixtureClient(_JypFakeClient):
    def __init__(self, *, reclassify_mode: str) -> None:
        super().__init__()
        self.messages = _FixtureMessages(reclassify_mode=reclassify_mode)


class _FixtureEngine(_JypFakeEngine):
    def __init__(self, *, reclassify_mode: str) -> None:
        super().__init__()
        self.client = _FixtureClient(reclassify_mode=reclassify_mode)


@dataclass
class _RunObservations:
    metered: list[Any] = field(default_factory=list)
    reclassify: list[dict[str, object]] = field(default_factory=list)
    preflights: list[tuple[Any, Any]] = field(default_factory=list)


@pytest.fixture(autouse=True)
def _paid_provider_budget_context():
    """실제 계량 경계를 열되 모든 provider 응답은 메모리 가짜로 끝낸다."""

    callbacks = ProviderAttemptCallbacks(
        lambda _provider, _operation, _reserved: object(),
        lambda _token: None,
        lambda _token: None,
        lambda _token, _observation: None,
    )
    with provider_budget.activate(100_000.0), attempt_context.activate(callbacks):
        yield


@pytest.fixture(autouse=True)
def _reset_reclassify_switch() -> None:
    reset_switch = getattr(
        reclassify_switch,
        "_reset_process_evidence_reclassify_switch_for_tests",
    )
    reset_switch()
    yield
    reset_switch()


def _wire_audit_collector(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fixture_name: str,
) -> official_evidence_adapter.ProductionOfficialEvidenceCollector:
    collect_module, _fetcher_module, serialize_module = (
        official_evidence_adapter._typed_dart_collector_modules()
    )
    filing_module = importlib.import_module(
        "features.evidence_collection.filing_select"
    )
    row = filing_module.RawFilingRow(
        _RECEIPT_NUMBER,
        "감사보고서 (2025.12)",
        "20260315",
        CORP_ID,
        COMPANY_NAME,
    )
    plain_text = _audit_plain_text(fixture_name)

    class FakeDartRuntimeFetcher:
        def __init__(self, **_kwargs: Any) -> None:
            self.list_calls: list[str] = []
            self.document_calls = 0

        def fetch_filing_list(self, company_id: str, pblntf_ty: str):
            assert company_id == CORP_ID
            self.list_calls.append(pblntf_ty)
            return filing_module.FilingListResult(
                state="OK",
                rows=(row,) if pblntf_ty == "F" else (),
            )

        def fetch_document_text(self, receipt_number: str):
            assert receipt_number == _RECEIPT_NUMBER
            self.document_calls += 1
            return filing_module.DocumentFetchResult(
                state="OK",
                text=plain_text,
                elapsed_ms=1,
                bytes_downloaded=len(plain_text.encode("utf-8")),
                corp_code=CORP_ID,
            )

    monkeypatch.setattr(
        official_evidence_adapter,
        "_typed_dart_collector_modules",
        lambda: (
            collect_module,
            SimpleNamespace(DartRuntimeFetcher=FakeDartRuntimeFetcher),
            serialize_module,
        ),
    )
    monkeypatch.setattr(
        official_evidence_adapter,
        "collect_official_web_documents",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        official_evidence_adapter,
        "build_fragments_for_collection",
        lambda _result: (),
    )
    monkeypatch.setattr(
        official_evidence_adapter,
        "to_evidence_mappings",
        lambda **_kwargs: {
            "company_id": CORP_ID,
            "documents": [],
            "fragments": [],
            "attempts": [
                {
                    "company_id": CORP_ID,
                    "attempt_id": "official-homepage-not-configured",
                    "source_kind": "official_web_page",
                    "requirement": "REQUIRED",
                    "state": "MISSING",
                    "slot_ids": [
                        slot_id
                        for section_id in (
                            "identity",
                            "portfolio",
                            "future_strategy",
                            "culture",
                            "competitive_position",
                        )
                        for slot_id in collector_slots_for(section_id)
                    ],
                    "reason_code": "official_homepage_not_configured",
                    "elapsed_ms": 0,
                    "bytes_downloaded": 0,
                    "documents_seen": 0,
                }
            ],
        },
    )
    return official_evidence_adapter.ProductionOfficialEvidenceCollector()


def _wire_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    reclassify_enabled: bool,
    reclassify_mode: str = "valid",
) -> tuple[_FixtureEngine, _RunObservations]:
    engine = _FixtureEngine(reclassify_mode=reclassify_mode)
    observations = _RunObservations()
    monkeypatch.setattr(real, "_engine", lambda: engine)
    monkeypatch.setattr(
        real,
        "_company_catalog",
        lambda: ((CORP_ID, COMPANY_NAME, "", "000001", "20260819"),),
    )
    monkeypatch.setenv(real.ENGINE_V2_ENV_NAME, real.ENGINE_V2_ENV_ON)
    monkeypatch.setenv(real.REPORT_RELEASE_MODE_ENV_NAME, ReleaseMode.FULL.value)
    monkeypatch.setenv("RENDER_GIT_COMMIT", _DEPLOYMENT_COMMIT)
    monkeypatch.setenv(
        reclassify_switch.EVIDENCE_RECLASSIFY_ENV_NAME,
        "1" if reclassify_enabled else "0",
    )
    for name in deployment_identity.COMMIT_ENV_NAMES:
        if name != "RENDER_GIT_COMMIT":
            monkeypatch.delenv(name, raising=False)

    actual_metered_engine = real._MeteredEngine  # noqa: SLF001

    def capture_metered(raw_engine: Any) -> Any:
        metered = actual_metered_engine(raw_engine)
        observations.metered.append(metered)
        return metered

    monkeypatch.setattr(real, "_MeteredEngine", capture_metered)
    actual_reclassify = real.reclassify_official_evidence

    def capture_reclassify(official_evidence: Any, **kwargs: Any) -> Any:
        metered = observations.metered[-1]
        before = int(metered._provider_call_count)  # noqa: SLF001
        result = actual_reclassify(official_evidence, **kwargs)
        after = int(metered._provider_call_count)  # noqa: SLF001
        step = next(
            (
                dict(item)
                for item in reversed(kwargs["steps"])
                if item.get("step") == RECLASSIFY_STEP_NAME
            ),
            None,
        )
        observations.reclassify.append(
            {"before": before, "after": after, "step": step, "result": result}
        )
        return result

    monkeypatch.setattr(real, "reclassify_official_evidence", capture_reclassify)
    actual_preflight = real.assess_official_evidence

    def capture_preflight(official_evidence: Any) -> Any:
        preflight = actual_preflight(official_evidence)
        observations.preflights.append((official_evidence, preflight))
        return preflight

    monkeypatch.setattr(real, "assess_official_evidence", capture_preflight)
    return engine, observations


def _run(collector: Any) -> RunResult:
    result = real.RealPipeline(official_evidence_collector=collector).run(
        UserInput(
            company=COMPANY_NAME,
            job=JOB,
            region="서울 강남구",
            posting_text=POSTING,
        ),
        CompanyCard(
            legal_name=COMPANY_NAME,
            typed_name=COMPANY_NAME,
            address="서울특별시 강남구 테헤란로 1",
            ceo="홍길동",
            founded="20000101",
            ref=CORP_ID,
        ),
    )
    assert result.outcome is Outcome.REPORT, result.message
    assert result.report is not None
    return result


def _portfolio_candidate(official_evidence: Any) -> Any:
    return next(
        candidate
        for candidate in official_evidence.candidates
        if candidate.section_id == "portfolio"
    )


def _stable_result_payload(result: RunResult) -> dict[str, object]:
    """날짜·세션 값을 뺀 최종 공개 결과의 바이트 골든 투영."""

    assert result.report is not None
    report = result.report
    return {
        "outcome": result.outcome.value,
        "charged": result.charged,
        "corp_type": result.corp_type,
        "cache_hit": result.cache_hit,
        "metrics": {
            "fragments_collected": result.fragments_collected,
            "fragments_cited": result.fragments_cited,
            "sentences_made": result.sentences_made,
            "sentences_passed": result.sentences_passed,
        },
        "report": {
            "company": report.company,
            "corp_type": report.corp_type,
            "grade": report.grade.value,
            "schema_version": report.schema_version,
            "release_mode": report.release_mode,
            "sections": [
                {
                    "cell": section.cell,
                    "title": section.title,
                    "empty_reason": section.empty_reason,
                    "prose_lines": section.prose_lines,
                    "guidance_lines": section.guidance_lines,
                    "tables": [
                        {
                            "caption": table.caption,
                            "headers": table.headers,
                            "rows": table.rows,
                            "cite": table.cite,
                            "presentation": table.presentation,
                        }
                        for table in section.tables
                    ],
                }
                for section in report.sections
            ],
            "summary_items": [
                {"text": item.text, "section_id": item.section_id}
                for item in report.summary_items
            ],
            "shortfall_reasons": report.shortfall_reasons,
        },
    }


def _stable_result_bytes(result: RunResult) -> bytes:
    return json.dumps(
        _stable_result_payload(result),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def test_스위치ON이면_재판정뒤_3장이_READY이고_SHADOW로_끝까지_봉인한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, observations = _wire_pipeline(
        monkeypatch,
        reclassify_enabled=True,
    )
    collector = _wire_audit_collector(monkeypatch, fixture_name=_INIZY_FIXTURE)

    result = _run(collector)

    official, preflight = observations.preflights[-1]
    portfolio = _portfolio_candidate(official)
    assert (
        portfolio.candidate_readiness is EvidenceReadiness.READY
    ), observations.reclassify
    assert "portfolio" in preflight.decision.ready_section_ids
    assert preflight.dart_partial_fallback is True
    # 9장 재정의(자기 선언 차별점 필수) 뒤 감사보고서만 있는 회사는 9장이 미달이라
    # 「준비된 장이 있는 부분 보고서」 사유로 내려간다.
    assert (
        preflight.dart_partial_reason
        == DART_PARTIAL_REASON_INSUFFICIENT_WITH_READY_SECTIONS
    )
    assert result.report is not None
    assert parse_release_mode(result.report.release_mode) is ReleaseMode.SHADOW

    measured = observations.reclassify[-1]
    assert measured["after"] == measured["before"] + 1
    step = measured["step"]
    assert isinstance(step, dict)
    assert step["채택"] == 2
    assert step["폐기"] == 0
    assert step["빼기"] == 1
    assert step["AI호출"] == 1
    assert engine.client.messages.reclassify_calls == 1
    current = next(
        candidate
        for candidate in official.candidates
        if candidate.section_id == "current_challenges"
    )
    assert all(_ACCOUNTING_BOILERPLATE not in item.text for item in current.fragments)
    portfolio_text = " ".join(
        text for section in result.report.sections if section.cell == "portfolio"
        for text, _cite in section.prose_lines
    )
    assert _PRODUCT_SENTENCES[0] in portfolio_text
    assert _REVENUE_SENTENCES[0] in portfolio_text


def test_스위치OFF면_3장_부족과_최종결과_바이트골든을_그대로_유지한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _engine, observations = _wire_pipeline(
        monkeypatch,
        reclassify_enabled=False,
    )
    collector = _wire_audit_collector(monkeypatch, fixture_name=_INIZY_FIXTURE)

    result = _run(collector)

    official, preflight = observations.preflights[-1]
    assert (
        _portfolio_candidate(official).candidate_readiness
        is EvidenceReadiness.INSUFFICIENT
    )
    assert "portfolio" not in preflight.decision.ready_section_ids
    assert preflight.dart_partial_fallback is True
    assert (
        preflight.dart_partial_reason
        == DART_PARTIAL_REASON_INSUFFICIENT_WITH_READY_SECTIONS
    )
    measured = observations.reclassify[-1]
    assert measured["after"] == measured["before"]
    assert measured["step"] is None
    actual = _stable_result_bytes(result)
    golden = json.loads(_GOLDEN_FIXTURE.read_text(encoding="utf-8"))
    assert golden["byte_count"] == len(actual)
    encoded = str(golden["gzip_base64"])
    assert gzip.decompress(base64.b64decode(encoded)) == actual


def test_같은_감사보고서를_두번_돌리면_재판정_캐시가_AI호출을_막는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, observations = _wire_pipeline(
        monkeypatch,
        reclassify_enabled=True,
    )
    collector = _wire_audit_collector(monkeypatch, fixture_name=_INIZY_FIXTURE)

    first = _run(collector)
    second = _run(collector)

    assert first.report == second.report
    assert engine.client.messages.reclassify_calls == 1
    assert len(observations.reclassify) == 2
    first_step = observations.reclassify[0]["step"]
    second_step = observations.reclassify[1]["step"]
    assert isinstance(first_step, dict) and first_step["캐시"] == "miss"
    assert isinstance(second_step, dict) and second_step["캐시"] == "hit"
    assert "실패" not in first_step
    assert "캐시저장실패" not in first_step
    assert (
        observations.reclassify[0]["after"]
        == observations.reclassify[0]["before"] + 1
    )
    assert observations.reclassify[1]["after"] == observations.reclassify[1]["before"]
    assert second_step["AI호출"] == 0


def test_원문에_없는_인용은_둘다_폐기하고_3장_부족인_보고서를_계속_만든다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _engine, observations = _wire_pipeline(
        monkeypatch,
        reclassify_enabled=True,
        reclassify_mode="invalid",
    )
    collector = _wire_audit_collector(monkeypatch, fixture_name=_INIZY_FIXTURE)

    result = _run(collector)

    official, preflight = observations.preflights[-1]
    assert result.outcome is Outcome.REPORT
    assert (
        _portfolio_candidate(official).candidate_readiness
        is EvidenceReadiness.INSUFFICIENT
    )
    assert "portfolio" not in preflight.decision.ready_section_ids
    step = observations.reclassify[-1]["step"]
    assert isinstance(step, dict)
    assert step["채택"] == 0
    assert step["폐기"] == 2, step
    assert step["폐기사유"] == {"quote_not_found": 2}
    assert step["AI호출"] == 1


def test_문지기만으로_3장이_READY면_재판정을_호출하지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, observations = _wire_pipeline(
        monkeypatch,
        reclassify_enabled=True,
    )
    collector = _wire_audit_collector(monkeypatch, fixture_name=_HIVE_FIXTURE)

    result = _run(collector)

    official, preflight = observations.preflights[-1]
    assert result.outcome is Outcome.REPORT
    assert (
        _portfolio_candidate(official).candidate_readiness
        is EvidenceReadiness.READY
    )
    assert "portfolio" in preflight.decision.ready_section_ids
    measured = observations.reclassify[-1]
    # 3장은 문지기만으로 READY라 빈 장 목록에 없다. 9장(자기 선언 차별점 없음)만
    # 재판정을 묻고, 배정이 없으니 3장 결과는 그대로다.
    step = measured["step"]
    assert isinstance(step, dict), step
    assert step["빈장"] == ["competitive_position"]
    assert step["채택"] == 0
    assert engine.client.messages.reclassify_calls == 1
