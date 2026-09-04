"""FULL 근거 결속의 AI 이전 차단 경계를 확인하는 통합시험.

과거 성공 시험은 매출표의 ``evidence_rows``와 비교 근거를 시험 안에서 직접
채워 생산 배선이 끊겨도 초록불이었다. 이 파일은 그 성공을 재현하지 않는다.
매출표·행별 원문 범위·표 인용은 실제 ``revenuemix``와 파이프라인 결속 함수가
만들게 하되, 공식 typed 결과는 고정 입력으로 직접 만든다. 그래서 실제 비교
생산물을 거치지 않은 낮은 수준 FULL 호출이 작가를 부르기 전에 닫히는 것만
보장한다. 공개 worker부터 봉인·저장까지의 성공 경로는
``web/tests/test_public_boundary_full_evidence_e2e.py``가 별도로 소유한다.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import replace

import pytest

from src.core import deployment_identity
from src.features.budget import provider_budget
from src.features.composer.constants import GRADE_CONFIRMED, SECTION_IDS
from src.features.composer.port import filing_meta_from_raw
from src.features.company_comparison.official_sources import (
    dart_profile_attestation_material,
)
from src.features.pipeline import real
from src.features.pipeline.official_evidence_preflight import (
    assess_official_evidence,
)
from src.features.pipeline.official_evidence_transport_adapter import (
    merge_official_evidence_fragments,
)
from src.features.pipeline.port import Outcome
from src.features.pipeline.tests.test_real_cache import FakeEngine
from src.features.revenuemix.logic import build as build_revenue_mix
from src.shared import engine_build_identity as build_identity_contract
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_evidence.constants import (
    EvidenceReadiness,
    ReleaseMode,
    SOURCE_KIND_DART_BUSINESS_REPORT,
    SOURCE_KIND_OFFICIAL_WEB_PAGE,
    SourceRequirement,
    SourceTier,
)
from src.shared.report_evidence.models import (
    ChapterEvidenceCandidates,
    CollectedEvidenceDocument,
    DocumentTextRange,
    EvidenceFragment,
)
from src.shared.report_evidence.policy import (
    REQUIRED_EVIDENCE_SECTION_IDS,
    collector_slots_for,
)
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult
from src.shared.report_source_identity import ReportSourceIdentity
from src.shared.revenue_table_provenance import revenue_table_source_excerpt


_COMPANY_ID = "00123456"
_BUSINESS_DATE = dt.date(2026, 9, 4)
_FILING = {
    "rcept_no": "20260331000123",
    "rcept_dt": "20260331",
    "report_nm": "사업보고서 (2025.12)",
}
_FILING_TEXT = (
    "제품별 매출액 구 분 2025년 제1기 매 출 액 비 중 "
    "제품가 5,000 50.00% 제품나 3,000 30.00% 제품다 2,000 20.00% "
    "합계 10,000 100.00% "
    "지역별 매출액 구 분 2025년 제1기 매 출 액 비 중 "
    "국내 5,000 50.00% 아시아 3,000 30.00% 미주 2,000 20.00% "
    "합계 10,000 100.00%"
)
_MARKS = "가나다라마바사아자"
_ENDINGS = ("첫째", "둘째", "셋째", "넷째", "다섯째")
_REVIEW_ITEM_RE = re.compile(
    r"\[(\d+)\] \(장: ([^,]+), 종류: ([^,]+), 인용: ([^)]+)\)"
)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _section_sentences(section_index: int) -> tuple[str, ...]:
    mark = _MARKS[section_index]
    return tuple(
        f"{mark} 회사 사업 고객 제품 전략 운영 문화 경쟁 과제 대응 협력 실적 "
        f"{ending} 공식 자료에서 확인했다."
        for ending in _ENDINGS
    )


def _official_evidence() -> OfficialEvidenceCollectionResult:
    """아홉 독립 공식 문서의 exact 원문을 typed 계약으로 만든다."""

    attestation_source_id, attestation_evidence = dart_profile_attestation_material(
        profile={
            "status": "000",
            "corp_code": _COMPANY_ID,
            "corp_name": "가나다회사",
            "hm_url": "https://official.example/",
        },
        corp_code=_COMPANY_ID,
        company_name="가나다회사",
    )
    assert attestation_source_id and attestation_evidence
    candidates: list[ChapterEvidenceCandidates] = []
    for index, section_id in enumerate(REQUIRED_EVIDENCE_SECTION_IDS):
        slots = collector_slots_for(section_id)
        text = " ".join(_section_sentences(index))
        text_sha256 = _sha256(text)
        if index == 0:
            receipt_number = "20260330000001"
            source_kind = SOURCE_KIND_DART_BUSINESS_REPORT
            document_id = f"{source_kind}:{receipt_number}"
            canonical_url = (
                "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="
                + receipt_number
            )
            publisher = "금융감독원"
        else:
            source_kind = SOURCE_KIND_OFFICIAL_WEB_PAGE
            document_id = f"official-document-{index + 1}"
            canonical_url = f"https://official.example/evidence/{index + 1}"
            publisher = "가나다회사"
        document = CollectedEvidenceDocument(
            company_id=_COMPANY_ID,
            document_id=document_id,
            canonical_url=canonical_url,
            source_tier=SourceTier.TIER_1_OFFICIAL,
            source_kind=source_kind,
            publisher=publisher,
            title=f"공식 자료 {index + 1}",
            published_on=_BUSINESS_DATE.isoformat(),
            collected_at=_BUSINESS_DATE.isoformat(),
            content_sha256=_sha256(f"문서 전체 {index + 1}: {text}"),
            exact_evidence_hashes=(text_sha256,),
            identity_binding="dart_profile_homepage",
            usable_ranges=(DocumentTextRange(0, len(text)),),
            collector_version="fake-e2e-collector-v1",
            parser_version="fake-e2e-parser-v1",
            requirement=SourceRequirement.REQUIRED,
            domain_attestation_source_id=(
                attestation_source_id
                if source_kind == SOURCE_KIND_OFFICIAL_WEB_PAGE
                else ""
            ),
            domain_attestation_evidence=(
                attestation_evidence
                if source_kind == SOURCE_KIND_OFFICIAL_WEB_PAGE
                else ""
            ),
        )
        fragment = EvidenceFragment(
            company_id=_COMPANY_ID,
            fragment_id=f"official-fragment-{section_id}",
            document_id=document_id,
            location=f"section:{section_id}",
            text_sha256=text_sha256,
            text=text,
            section_id=section_id,
            slot_id=slots[0],
            covered_slot_ids=slots,
            score_millis=1000,
            reason_codes=("exact_fixture_match",),
        )
        candidates.append(
            ChapterEvidenceCandidates(
                company_id=_COMPANY_ID,
                section_id=section_id,
                documents=(document,),
                fragments=(fragment,),
                attempts=(),
                candidate_readiness=EvidenceReadiness.READY,
                reason_codes=(),
                estimated_tokens=max(1, len(text) // 4),
                max_chars=10_000,
                max_estimated_tokens=10_000,
            )
        )
    return OfficialEvidenceCollectionResult(
        company_id=_COMPANY_ID,
        candidates=tuple(candidates),
    )


def test_같은_formal문서ID가_서로다른_URL을_가리키면_merge전에_거절한다() -> None:
    normal = _official_evidence()
    candidates = list(normal.candidates)
    first_document_id = candidates[0].documents[0].document_id
    second = candidates[1]
    candidates[1] = replace(
        second,
        documents=(
            replace(second.documents[0], document_id=first_document_id),
        ),
        fragments=(
            replace(second.fragments[0], document_id=first_document_id),
        ),
    )

    with pytest.raises(ValueError, match="같은 문서 식별자"):
        OfficialEvidenceCollectionResult(
            company_id=_COMPANY_ID,
            candidates=tuple(candidates),
        )


class _ExactPacketWriter:
    """자기 장 packet에 실제로 실린 exact 문장만 돌려주는 가짜 작가."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        matching_sections = tuple(
            section_id
            for section_id in SECTION_IDS
            if f"{section_id}:" in prompt
        )
        assert len(matching_sections) == 1
        section_id = matching_sections[0]
        section_index = SECTION_IDS.index(section_id)
        self.prompts.append(prompt)
        sentences = _section_sentences(section_index)
        source_text = " ".join(sentences)
        source_position = prompt.find(source_text)
        assert source_position >= 0, "typed 원문이 자기 장 작가 packet에서 사라졌습니다"
        preceding = re.findall(r"\[조각 (\d+)\]", prompt[:source_position])
        assert preceding
        fragment_id = preceding[-1]
        supported_match = re.search(
            rf"\[조각 {re.escape(fragment_id)}\] \([^\n]*"
            r"지원 주장슬롯: ([^)]+)\)",
            prompt,
        )
        assert supported_match, "FULL 작가 prompt에서 typed 지원 슬롯이 사라졌습니다"
        slots = tuple(
            slot.strip()
            for slot in supported_match.group(1).split(",")
            if slot.strip() in CLAIM_SLOTS_BY_SECTION[section_id]
        )
        assert slots, "작가가 현재 장에서 사용할 수 있는 지원 슬롯이 없습니다"
        return json.dumps(
            {
                "문장들": [
                    {
                        "글": sentence,
                        "인용": [fragment_id],
                        "등급": GRADE_CONFIRMED,
                        "주장슬롯": slots[index % len(slots)],
                    }
                    for index, sentence in enumerate(sentences)
                ]
            },
            ensure_ascii=False,
        )


class _ExactBundledReviewer:
    """실제 bundled 검수 prompt가 지정한 장·인용만 승인한다."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        items = _REVIEW_ITEM_RE.findall(prompt)
        assert items, "bundled 검수 prompt에 판정할 문장이 없습니다"
        return json.dumps(
            {
                "판정": [
                    {
                        "번호": int(number),
                        "장": section_id,
                        "근거": re.findall(r"조각 (\d+)", citations),
                        "결과": "참",
                    }
                    for number, section_id, _kind, citations in items
                ]
            },
            ensure_ascii=False,
        )


@pytest.fixture
def _frozen_full_runtime(monkeypatch: pytest.MonkeyPatch):
    """운영과 같은 V2/FULL/build 신원을 시험 한 건에만 고정한다."""

    real.engine_mode._reset_process_engine_mode_for_tests()  # noqa: SLF001
    build_identity_contract._reset_process_engine_build_identity_for_tests()  # noqa: SLF001
    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    monkeypatch.setenv(real.ENGINE_V2_ENV_NAME, real.ENGINE_V2_ENV_ON)
    monkeypatch.setenv(real.REPORT_RELEASE_MODE_ENV_NAME, ReleaseMode.FULL.value)
    generation_mode = real.engine_mode.freeze_process_engine_mode(
        real.engine_mode.EngineMode.V2
    )
    build_identity = build_identity_contract.freeze_process_engine_build_identity()
    assert build_identity.cache_usable
    yield generation_mode, build_identity
    real.engine_mode._reset_process_engine_mode_for_tests()  # noqa: SLF001
    build_identity_contract._reset_process_engine_build_identity_for_tests()  # noqa: SLF001


def test_생산_매출원문은_보존하되_비교생산물없는_직접_FULL은_AI전에_닫힌다(
    _frozen_full_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generation_mode, build_identity = _frozen_full_runtime

    # ① 표 값과 행별 원문 증거를 오직 생산 파서가 만든다. 시험 코드가
    # ``evidence_rows``를 생성·수정·추가하지 않는다.
    produced_tables = build_revenue_mix(_FILING_TEXT)
    assert len(produced_tables) == 2
    assert all(table["evidence_rows"] for table in produced_tables)

    # ② 표마다 실제 공시 문서·exact excerpt를 가진 전용 숫자 cite를 붙인다.
    revenue_fragments, bound_tables = real._bind_revenue_table_evidence_fragments(
        {}, produced_tables, filing=_FILING, filing_text=_FILING_TEXT
    )
    assert len(revenue_fragments) == len(bound_tables) == 2
    assert len({table["cite"] for table in bound_tables}) == 2
    for fragment, table in zip(revenue_fragments.values(), bound_tables):
        assert fragment["원문"] == revenue_table_source_excerpt(
            table["evidence_rows"]
        )
        assert fragment["원문"] in _FILING_TEXT

    # ③ 공식 근거는 typed 사전검사를 통과한 뒤 운영 merge를 거친다.
    official = _official_evidence()
    preflight = assess_official_evidence(official)
    assert preflight.can_call_ai
    assert preflight.independent_document_count == 9
    merged_fragments, added = merge_official_evidence_fragments(
        revenue_fragments, official
    )
    assert added == 9

    source_identity = ReportSourceIdentity.capture(
        filing=_FILING,
        financial_payload={"status": "000", "list": []},
    )
    generation_sha256 = source_identity.cache_digest_with_official_snapshot(
        official.source_snapshot_sha256
    )
    assert generation_sha256

    # ④ legacy 종류 추측과 typed 장 메타데이터를 한 경계에서 검증해 정책 순서
    # 아홉 packet으로 고정한다. 두 매출표 cite는 모두 2장 packet 안에 있어야 한다.
    filing_meta = filing_meta_from_raw(_FILING)
    packets = real._full_section_evidence_packets(
        corp_id=_COMPANY_ID,
        source_identity_digest=generation_sha256,
        frags=merged_fragments,
        filing_meta=filing_meta,
    )
    assert tuple(packet.section_id for packet in packets.packets) == SECTION_IDS
    business_packet = next(
        packet for packet in packets.packets if packet.section_id == "business_model"
    )
    revenue_ids = {
        str(number) for number in revenue_fragments
    }
    assert revenue_ids <= {
        fragment.fragment_id for fragment in business_packet.fragments
    }

    # ⑤ 이 낮은 수준 직접 호출은 실제 양사 비교 생산기를 거치지 않았다.
    # 시험이 comparison facts·evidence를 손으로 보충해 성공을 가장하지 않고,
    # 운영 계약대로 작가를 한 번도 부르기 전에 닫히는지를 확인한다. 성공 종단은
    # 실제 비교 생산 경로까지 소유한 runtime E2E가 별도로 검증한다.
    writer = _ExactPacketWriter()
    reviewer = _ExactBundledReviewer()
    diagram_calls: list[str] = []

    def fake_ask_factory(_engine, _client, *, stage: str, max_tokens: int):
        assert max_tokens > 0
        if stage == "v2_compose":
            return writer
        if stage == "v2_review":
            return reviewer

        def forbidden_diagram(_prompt: str) -> str:
            diagram_calls.append(stage)
            raise AssertionError("FULL 구성 도식은 별도 AI를 부르면 안 됩니다")

        return forbidden_diagram

    monkeypatch.setattr(real, "_v2_ask_via_provider", fake_ask_factory)
    monkeypatch.setattr(real, "_v2_cache_save", lambda **_kwargs: None)
    metered = real._MeteredEngine(FakeEngine())
    with provider_budget.activate(100_000.0):
        result = real._run_v2_composer(
            engine=metered,
            client=object(),
            company_name="가나다회사",
            corp_type="상장사",
            frags=merged_fragments,
            financials=None,
            filing=_FILING,
            revenue_tables=bound_tables,
            sources=[],
            business_date=_BUSINESS_DATE,
            model="가짜모델",
            steps=[],
            corp_id=_COMPANY_ID,
            current_fiscal_year=2025,
            source_identity_digest=generation_sha256,
            build_identity=build_identity,
            generation_mode=generation_mode,
        )

    assert result.outcome is Outcome.GATE_STOPPED
    assert result.report is None
    assert writer.prompts == []
    assert reviewer.prompts == []
    assert diagram_calls == []
