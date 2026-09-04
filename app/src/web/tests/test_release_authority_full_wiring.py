"""ReleaseAuthority를 FULL 완료 거래 안에 실제로 배선한다 (P1-1·P1-2·생산 배선).

★ 지키는 것: FULL(release_mode=FULL) 출고는 raw content·delivery·PDF
  artifact·자동승인·charge·LINK/PUBLIC binding과 같은 SQLite 거래 안에서
  ReleaseAuthority를 발급·저장한다. 거래를 여는 쪽은
  ``routers.reports.finalize_new_report_delivery``이고, ``report_completion``
  모듈은 그 거래 안에서 쓰이는 순수 지문 대조 함수만 제공한다. 발급 전에 회사 ID
  3자(정규화 corp_id·output_report.company_id·evidence.company_id)를 exact
  비교하고, epoch는 evidence.build_identity_sha256과
  frozen_build_identity.epoch_digest를 blob 생성 전에, 그리고 저장된
  Content.engine_epoch_digest까지 포함해 발급 직전에 다시 exact 비교한다
  (`docs/출력물 기준/90_공통_규칙/런타임_출고_계약.md` §6 출고 게이트).

★ 이 시험의 FULL Report는 ``composer.pipeline.run_v2``를 ``release_mode=FULL``로
  실제로 돌려 만든다. company_id·evidence 해시를 손으로 지어내면
  ``assert_report_matches_generation_evidence``가 판정하는 실제 결속을
  시험하지 못한다 — 예측식이 아니라 실제 판정 로직으로 검증한다.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.features.admin_dashboard import store as dashboard_store
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.composer.constants import GRADE_CONFIRMED, SECTION_IDS
from src.features.composer.pipeline import run_v2
from src.features.composer.port import (
    FilingMeta,
    PerformanceTable,
    SectionEvidencePacketSet,
    filing_meta_from_raw,
    performance_table_from_report_table,
)
from src.features.company_comparison.official_sources import (
    dart_profile_attestation_material,
)
from src.features.company_comparison.v2_bridge import (
    attach_comparison_program_evidence,
)
from src.features.pipeline import real
from src.features.pipeline.evidence_transport import build_section_evidence_packet_set
from src.features.pipeline.official_evidence_transport_adapter import (
    merge_official_evidence_fragments,
)
from src.features.pipeline.port import Report
from src.features.report_delivery import authority as authority_store
from src.features.report_delivery.cache_identity import CacheLookupKey, CacheNamespace
from src.features.report_delivery.singleflight import LeaseKey
from src.features.report_delivery.source_identity import SourceSnapshot
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.shared import engine_build_identity as build_identity_contract
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionRequest
from src.shared.report_source_identity import (
    ReportSourceIdentity,
    financial_payload_digest,
)
from src.web import report_completion, report_delivery_adapter, report_publication
from src.web.main import app
from src.web.official_evidence_adapter import ProductionOfficialEvidenceCollector
from src.web.routers import reports as reports_router
from src.web.tests.test_public_boundary_full_evidence_e2e import (
    _HOME as _PRODUCTION_FIXTURE_HOME,
    _MAIN_RECEIPT as _PRODUCTION_FIXTURE_RECEIPT,
    _install_actual_official_collector_with_fake_http,
    _install_production_engine_with_fake_external_services,
    _section_sentences,
)

_COMPANY_ID = "00126380"
_BUILD_IDENTITY_SHA256 = "b" * 64
_DART_RECEIPT = "20260828000123"
_FINANCIAL_DIGEST = financial_payload_digest(
    {"status": "000", "list": [{"account_nm": "매출액", "thstrm_amount": "100"}]}
)
_OFFICIAL_SNAPSHOT_DIGEST = "c" * 64
_PREFLIGHT_IDENTITY_DIGEST = ReportSourceIdentity(
    dart_receipt_numbers=(_DART_RECEIPT,),
    financial_payload_digest=_FINANCIAL_DIGEST,
).cache_digest_with_official_snapshot(_OFFICIAL_SNAPSHOT_DIGEST)


def _production_collected_evidence(
    *,
    company_id: str,
    evidence_generation_sha256: str,
) -> tuple[
    dict[int, dict[str, object]],
    SectionEvidencePacketSet,
    PerformanceTable,
    FilingMeta,
]:
    """가짜 외부 응답만 두고 운영 수집→transport→packet 경로를 그대로 돈다.

    문서 hash·Source·도메인 attester를 시험에서 조립하지 않는다. DART 기업개황
    원문을 생산 함수로 봉인하고, ``ProductionOfficialEvidenceCollector``가 만든
    문서·조각을 production merge/builder가 숫자 인용 packet으로 바꾼다.
    """

    with tempfile.TemporaryDirectory(prefix="release-authority-evidence-") as root:
        with pytest.MonkeyPatch.context() as patch:
            engine, external = _install_production_engine_with_fake_external_services(
                patch,
                Path(root),
            )
            _install_actual_official_collector_with_fake_http(patch)
            counter = SimpleNamespace(tick=lambda *_args, **_kwargs: None)
            profile = external.get_json(
                "company.json",
                {"corp_code": company_id},
                counter,
            )
            source_id, evidence = dart_profile_attestation_material(
                profile=profile,
                corp_code=company_id,
                company_name="가나다전자",
            )
            assert source_id and evidence
            official = ProductionOfficialEvidenceCollector().collect(
                OfficialEvidenceCollectionRequest(
                    company_id=company_id,
                    company_name="가나다전자",
                    company_aliases=(str(profile.get("corp_name_eng") or ""),),
                    root_homepage_url=_PRODUCTION_FIXTURE_HOME,
                    company_registration_numbers=(
                        str(profile.get("bizr_no") or ""),
                        str(profile.get("jurir_no") or ""),
                    ),
                    official_candidate_urls=(),
                    as_of_date=dt.date(2026, 9, 4),
                    dart_document_cache_dir=Path(engine.RAW_DIR),
                    dart_counter=counter,
                    dart_get_json=engine.get_json,
                    dart_download_document=engine.download_document,
                    domain_attestation_source_id=source_id,
                    domain_attestation_evidence=evidence,
                )
            )
            financials, financial_years = engine.fetch_financials(
                company_id,
                counter,
                business_date=dt.date(2026, 9, 4),
            )
            assert financials is not None and len(financial_years) == 1
            filing = engine.latest_report_rcept(
                company_id,
                "상장사",
                counter,
                business_date=dt.date(2026, 9, 4),
            )
            assert filing is not None
            assert str(filing.get("rcept_no") or "") == _PRODUCTION_FIXTURE_RECEIPT
            filing_path = engine.download_document(
                str(filing["rcept_no"]),
                engine.RAW_DIR,
                counter,
                require_official_url_sidecar=True,
            )
            legacy_fragments = engine.make_fragments(
                engine.read_filing_text(filing_path),
                financials,
            )
            fragments, _added = merge_official_evidence_fragments(
                legacy_fragments,
                official,
            )
            packets = build_section_evidence_packet_set(
                corp_id=company_id,
                source_generation_sha256=evidence_generation_sha256,
                frags=fragments,
                filing_meta=filing_meta_from_raw(filing),
            )
            # 후보목록도 CORPCODE 외부 fixture를 실제 parser에 통과시킨 결과다.
            # 이름·법인번호를 이 시험이 직접 비교 근거로 주입하지 않는다.
            catalog_path = external.download_corpcode(engine.CORPCODE_DIR, counter)
            catalog = real.parse_dart_company_records(catalog_path)
            patch.setattr(real, "_company_catalog", lambda: catalog)
            comparison = real._prepare_v2_comparison_result(  # noqa: SLF001
                engine=engine,
                counter=counter,
                profile=profile,
                official_evidence=official,
                corp_code=company_id,
                company_name="가나다전자",
                corp_type="상장사",
                financials=financials,
                filing=filing,
                business_date=dt.date(2026, 9, 4),
                dart_download_document=engine.download_document,
            )
            packets = attach_comparison_program_evidence(packets, comparison)
            financial_cite = real._first_fragment_cite(  # noqa: SLF001
                fragments,
                kind="재무",
                text_prefix="주요계정(DART API):",
            )
            assert financial_cite
            report_table = real.build_three_year_table(
                financials,
                cite=financial_cite,
            )
            assert report_table is not None
            performance_table = performance_table_from_report_table(report_table)
            filing_meta = filing_meta_from_raw(filing)
    return fragments, packets, performance_table, filing_meta


class _CompleteWriter:
    """9개 장 모두 5문장씩 검증 가능한 confirmed 문장으로 채우는 가짜 작가."""

    def __init__(self, packets: SectionEvidencePacketSet) -> None:
        self.prompts: list[str] = []
        self.section_calls = 0
        self._packets = {
            packet.section_id: packet for packet in packets.packets
        }

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        section_index = self.section_calls
        section_id = SECTION_IDS[section_index]
        self.section_calls += 1
        packet = self._packets[section_id]
        rows: list[dict[str, object]] = []
        for index, sentence in enumerate(_section_sentences(section_id)):
            matches = tuple(
                fragment for fragment in packet.fragments if sentence in fragment.text
            )
            assert matches, f"{section_id} 운영 packet에서 exact 원문이 사라졌습니다"
            fragment = matches[0]
            supported = tuple(
                slot_id
                for slot_id in fragment.supported_claim_slots
                if slot_id in CLAIM_SLOTS_BY_SECTION[section_id]
            )
            assert supported, f"{section_id} 운영 packet의 주장 슬롯이 비었습니다"
            rows.append(
                {
                    "글": sentence,
                    "인용": [fragment.fragment_id],
                    "등급": GRADE_CONFIRMED,
                    "주장슬롯": supported[index % len(supported)],
                }
            )
        return json.dumps(
            {"문장들": rows},
            ensure_ascii=False,
        )


class _FakeReviewer:
    """판정 프롬프트의 문장 번호 전부를 「참」으로 돌려주는 가짜 검수."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
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


def _build_full_report(
    *,
    company_id: str = _COMPANY_ID,
    build_identity_sha256: str = _BUILD_IDENTITY_SHA256,
    evidence_generation_sha256: str = _PREFLIGHT_IDENTITY_DIGEST,
) -> Report:
    """FULL producer evidence를 실제로 계산해 붙인 Report 한 벌을 만든다."""

    fragments, packets, performance_table, filing_meta = _production_collected_evidence(
        company_id=company_id,
        evidence_generation_sha256=evidence_generation_sha256,
    )
    writer = _CompleteWriter(packets)
    reviewer = _FakeReviewer()
    output = run_v2(
        "가나다전자",
        fragments,
        performance_table,
        writer_ask=writer,
        reviewer_ask=reviewer,
        corp_type="상장사",
        generated_at="2026-09-04",
        as_of_date="2026-09-04",
        analysis_period="2023~2025",
        latest_performance_period="2025",
        filing_meta=filing_meta,
        release_mode=ReleaseMode.FULL,
        section_evidence_packets=packets,
        company_id=company_id,
        build_identity_sha256=build_identity_sha256,
    )
    assert output.report.release_mode == ReleaseMode.FULL.value
    assert output.report.generation_evidence is not None
    return output.report


def test_고정입력은_실제로_FULL_생산증거를_가진_보고서를_만든다():
    """구현 착수 전 전제 확인 — 이 fixture 자체가 실제 판정 로직을 통과하는지."""

    report = _build_full_report()
    evidence = report.generation_evidence
    assert evidence is not None
    assert evidence.company_id == _COMPANY_ID
    assert evidence.build_identity_sha256 == _BUILD_IDENTITY_SHA256
    assert evidence.evidence_generation_sha256 == _PREFLIGHT_IDENTITY_DIGEST
    assert report.company_id == _COMPANY_ID
    assert report.grade.value == "완성"


def _frozen_identity() -> build_identity_contract.EngineBuildIdentity:
    return build_identity_contract.process_engine_build_identity()


def _full_delivery_identity(
    report: Report,
    frozen: build_identity_contract.EngineBuildIdentity,
    *,
    preflight_identity_digest: str = _PREFLIGHT_IDENTITY_DIGEST,
) -> dict[str, object]:
    """FULL producer packet과 cache/single-flight가 공유하는 출처 세대 한 벌."""

    revision, image = report_delivery_adapter._release_identity(frozen)
    namespace = CacheNamespace.create(
        product="company-analysis",
        schema_version=report.schema_version or "legacy-report-schema",
        deployment_revision=revision,
        image_digest=image,
        requested_models={"pipeline": "deterministic-full-wiring"},
        output_settings={"temperature": 0},
    )
    return {
        "dart_receipt_numbers": (_DART_RECEIPT,),
        "financial_payload_digest": _FINANCIAL_DIGEST,
        "cache_namespace": namespace,
        "preflight_identity_digest": preflight_identity_digest,
        "cache_eligible": True,
    }


def test_FULL_출고는_같은거래안에서_ReleaseAuthority를_발급하고_저장한다(
    monkeypatch, tmp_path: Path
):
    frozen = _frozen_identity()
    report = _build_full_report(build_identity_sha256=frozen.epoch_digest)
    report_id = uuid.uuid4().hex
    monkeypatch.setenv("APP_DATA_ROOT", str(tmp_path / "art"))

    public_delivery = reports_router.finalize_new_report_delivery(
        report_id=report_id,
        corp_id=_COMPANY_ID,
        billing_bucket_id="release-wiring-bucket",
        report=report,
        actual_models=("deterministic-full-wiring",),
        reused_from_cache=False,
        engine_build_identity=frozen,
        **_full_delivery_identity(report, frozen),
    )

    assert public_delivery.artifact is not None
    with storage_db.connect_readonly_existing() as conn:
        assert conn is not None
        authority = authority_store.load_release_authority_by_public_id(
            conn, report_id
        )
    assert authority is not None
    assert authority.kind is authority_store.ReleaseAuthorityKind.OWNER
    assert authority.public_id == report_id
    assert authority.company_id == _COMPANY_ID
    assert authority.content_snapshot_id == public_delivery.content.content_id
    assert authority.artifact_id == public_delivery.artifact.artifact_id
    assert authority.build_identity_sha256 == frozen.epoch_digest
    assert report.generation_evidence is not None
    # ★ 뒤집힌 단정 — 예전에는 pre-render 공개 content
    #   봉인(지문 A)을 실었다. 출고 권위가 가리켜야 하는 것은 「사람이 실제로 받은
    #   공개본」이고, 그건 화면 글자와 감사 장부를 함께 덮는 공개 봉인 projection의
    #   지문이다. 지문 A는 렌더 이전 기대값이라 장부 바꿔치기를 못 본다.
    assert (
        authority.public_content_sha256
        == report.generation_evidence.public_projection_sha256
    )
    # 두 지문이 «다른 값»임을 함께 못 박는다 — 같아지면 이 시험이 아무것도
    # 가르지 못하게 된다(둘 중 무엇을 실어도 통과한다).
    assert (
        report.generation_evidence.public_projection_sha256
        != report.generation_evidence.public_content_sha256
    )
    assert report.public_projection is not None


def test_신규FULL은_delivery가남아도_ReleaseAuthority유실시_모든공개채널을닫는다(
    monkeypatch, tmp_path: Path
):
    """PUBLISHED 사건이 있는 신규 FULL은 옛 FULL 호환 예외를 쓸 수 없다."""

    frozen = _frozen_identity()
    report = _build_full_report(build_identity_sha256=frozen.epoch_digest)
    report_id = uuid.uuid4().hex
    monkeypatch.setenv("APP_DATA_ROOT", str(tmp_path / "art"))
    _store_report_row(report_id, report, frozen)
    with storage_db.connect() as conn:
        dashboard_store.stage_report(
            conn,
            report_id=report_id,
            corp_type=report.corp_type,
            now_iso="2026-09-04T10:00:00+09:00",
            payload_json=report_store.report_to_json(report),
        )
    reports_router.finalize_new_report_delivery(
        report_id=report_id,
        corp_id=_COMPANY_ID,
        billing_bucket_id="release-wiring-bucket",
        report=report,
        actual_models=("deterministic-full-wiring",),
        reused_from_cache=False,
        engine_build_identity=frozen,
        **_full_delivery_identity(report, frozen),
    )
    with storage_db.connect() as conn:
        assert report_publication.report_is_published_or_legacy(conn, report_id)
        conn.execute("DROP TRIGGER report_release_authorities_no_delete")
        conn.execute(
            f"DELETE FROM {authority_store.TABLE_RELEASE_AUTHORITIES} "
            "WHERE public_id=?",
            (report_id,),
        )
        assert not report_publication.report_is_published_or_legacy(conn, report_id)

    session = auth_logic.create_session(
        "admin@example.com", True, subject=f"test:authority-read:{report_id}"
    )
    with TestClient(app) as client:
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        csrf = auth_logic.csrf_token_for_session(session.token)
        result = client.get(f"/result/{report_id}", follow_redirects=False)
        pdf = client.get(f"/download/pdf/{report_id}", follow_redirects=False)
        notion = client.post(
            f"/notion/{report_id}",
            data={"csrf_token": csrf},
            follow_redirects=False,
        )
    assert result.status_code == pdf.status_code == notion.status_code == 409
    assert report.company not in result.text
    assert report.company not in pdf.text
    assert report.company not in notion.text


def test_회사ID_불일치는_출고전체를_거절하고_아무것도_남기지_않는다(
    monkeypatch, tmp_path: Path
):
    """P1-2 — corp_id가 evidence·본문의 company_id와 다르면 blob도 만들지 않는다."""

    frozen = _frozen_identity()
    report = _build_full_report(build_identity_sha256=frozen.epoch_digest)
    report_id = uuid.uuid4().hex
    monkeypatch.setenv("APP_DATA_ROOT", str(tmp_path / "art"))

    with pytest.raises(Exception, match="회사 ID"):
        reports_router.finalize_new_report_delivery(
            report_id=report_id,
            corp_id="99999999",  # evidence.company_id(_COMPANY_ID)와 다름
            billing_bucket_id="release-wiring-bucket",
            report=report,
            actual_models=("deterministic-full-wiring",),
            reused_from_cache=False,
            engine_build_identity=frozen,
            **_full_delivery_identity(report, frozen),
        )

    assert report_delivery_adapter.load_public_delivery(report_id) is None
    with storage_db.connect_readonly_existing() as conn:
        assert conn is not None
        assert (
            authority_store.load_release_authority_by_public_id(conn, report_id)
            is None
        )


def test_생성근거와_preflight_출처세대가_다르면_blob전에_출고를_닫는다(
    monkeypatch, tmp_path: Path
):
    """packet 세대 A를 cache/lease 세대 B의 결과로 출고할 수 없다."""

    frozen = _frozen_identity()
    report = _build_full_report(build_identity_sha256=frozen.epoch_digest)
    mismatched_preflight = "d" * 64
    assert mismatched_preflight != _PREFLIGHT_IDENTITY_DIGEST
    report_id = uuid.uuid4().hex
    artifact_root = tmp_path / "art"
    monkeypatch.setenv("APP_DATA_ROOT", str(artifact_root))

    with pytest.raises(
        report_completion.ReleaseIdentityMismatch,
        match="생성 근거 세대.*생성 전 출처 지문",
    ):
        reports_router.finalize_new_report_delivery(
            report_id=report_id,
            corp_id=_COMPANY_ID,
            billing_bucket_id="release-wiring-bucket",
            report=report,
            actual_models=("deterministic-full-wiring",),
            reused_from_cache=False,
            engine_build_identity=frozen,
            **_full_delivery_identity(
                report,
                frozen,
                preflight_identity_digest=mismatched_preflight,
            ),
        )

    assert report_delivery_adapter.load_public_delivery(report_id) is None
    with storage_db.connect_readonly_existing() as conn:
        assert conn is not None
        assert (
            authority_store.load_release_authority_by_public_id(conn, report_id)
            is None
        )
    assert not any(artifact_root.rglob("*.pdf"))


def test_승인된_FULL_링크는_현재_source계약으로_소급차단하지_않는다(
    monkeypatch, tmp_path: Path
):
    """과거 승인본 조회는 승인 당시 권위를 쓰고 현재 생성 규칙을 재실행하지 않는다.

    ``preflight_identity_digest`` 열이 없던 저장소를 올리면 기존 source 행에는
    빈 값이 들어간다. 새 FULL 생성·cache 재사용은 그 행을 새 권위로 쓰지 않아야
    하지만, 이미 저장된 최초 PDF와 출고 권위까지 받은 공개 링크를 오늘의 생성
    규칙으로 다시 심사하면 정상 발급 링크가 배포 순간 모두 닫힌다.
    """

    frozen = _frozen_identity()
    report = _build_full_report(build_identity_sha256=frozen.epoch_digest)
    report_id = uuid.uuid4().hex
    monkeypatch.setenv("APP_DATA_ROOT", str(tmp_path / "art"))
    _store_report_row(report_id, report, frozen)
    approved = reports_router.finalize_new_report_delivery(
        report_id=report_id,
        corp_id=_COMPANY_ID,
        billing_bucket_id="release-wiring-bucket",
        report=report,
        actual_models=("deterministic-full-wiring",),
        reused_from_cache=False,
        engine_build_identity=frozen,
        **_full_delivery_identity(report, frozen),
    )

    current_contract_calls: list[str] = []

    def forbidden_current_source_recheck(**_kwargs):
        current_contract_calls.append("called")
        raise AssertionError("승인된 공개 링크에 현재 source 계약을 소급 적용했습니다")

    monkeypatch.setattr(
        report_completion,
        "assert_release_stored_source_identity",
        forbidden_current_source_recheck,
    )

    loaded = report_delivery_adapter.load_public_delivery(report_id)

    assert loaded is not None
    assert loaded.content.content_id == approved.content.content_id
    assert loaded.artifact is not None
    assert approved.artifact is not None
    assert loaded.artifact.artifact_id == approved.artifact.artifact_id
    assert current_contract_calls == []


@pytest.mark.parametrize("mismatch", ("source", "cache", "lease"))
def test_저장출처와_cache_lease도_생성근거와_같은세대여야한다(mismatch: str):
    """사전 인자만 맞고 저장·재사용 배선이 갈라져도 마지막 거래가 닫힌다."""

    frozen = _frozen_identity()
    report = _build_full_report(build_identity_sha256=frozen.epoch_digest)
    evidence = report.generation_evidence
    assert evidence is not None
    wrong_digest = "d" * 64
    source_digest = (
        wrong_digest if mismatch == "source" else _PREFLIGHT_IDENTITY_DIGEST
    )
    source = SourceSnapshot.capture(
        dart_receipt_nos=(_DART_RECEIPT,),
        financial_payload=None,
        financial_payload_sha256=_FINANCIAL_DIGEST,
        captured_at=dt.datetime(2026, 8, 28, tzinfo=dt.timezone.utc),
        source_as_of=dt.date(2026, 8, 28),
        preflight_identity_digest=source_digest,
    )
    identity = _full_delivery_identity(report, frozen)
    namespace = identity["cache_namespace"]
    assert isinstance(namespace, CacheNamespace)
    cache_key = CacheLookupKey.from_preflight(
        billing_bucket_id="release-wiring-bucket",
        corp_id=_COMPANY_ID,
        namespace=namespace,
        preflight_identity_digest=(
            wrong_digest if mismatch == "cache" else _PREFLIGHT_IDENTITY_DIGEST
        ),
        preflight_cache_usable=True,
        engine_epoch_digest=frozen.epoch_digest,
    )
    lease_key = LeaseKey(
        billing_bucket_id="release-wiring-bucket",
        corp_id=_COMPANY_ID,
        cache_namespace_id=namespace.namespace_id,
        source_identity_digest=(
            wrong_digest if mismatch == "lease" else _PREFLIGHT_IDENTITY_DIGEST
        ),
        engine_epoch_digest=frozen.epoch_digest,
    )

    with pytest.raises(report_completion.ReleaseIdentityMismatch, match="세대"):
        report_completion.assert_release_stored_source_identity(
            evidence=evidence,
            source=source,
            cache_key=cache_key,
            reuse_singleflight_key=lease_key,
        )


def test_epoch_불일치는_출고전체를_거절하고_아무것도_남기지_않는다(
    monkeypatch, tmp_path: Path
):
    """P1-1 — evidence의 build_identity_sha256이 현재 완료 engine epoch와 다르면 닫는다."""

    frozen = _frozen_identity()
    # 실제 완료 epoch와 다른 값을 evidence에 심는다 — 다른 배포·다른 세대의
    # 내용이 이번 완료의 authority로 발급되려는 상황을 흉내낸다.
    mismatched_epoch = "f" * 64
    assert mismatched_epoch != frozen.epoch_digest
    report = _build_full_report(build_identity_sha256=mismatched_epoch)
    report_id = uuid.uuid4().hex
    monkeypatch.setenv("APP_DATA_ROOT", str(tmp_path / "art"))

    with pytest.raises(Exception, match="epoch"):
        reports_router.finalize_new_report_delivery(
            report_id=report_id,
            corp_id=_COMPANY_ID,
            billing_bucket_id="release-wiring-bucket",
            report=report,
            actual_models=("deterministic-full-wiring",),
            reused_from_cache=False,
            engine_build_identity=frozen,
            **_full_delivery_identity(report, frozen),
        )

    assert report_delivery_adapter.load_public_delivery(report_id) is None
    with storage_db.connect_readonly_existing() as conn:
        assert conn is not None
        assert (
            authority_store.load_release_authority_by_public_id(conn, report_id)
            is None
        )


def _store_report_row(report_id: str, report: Report, frozen) -> None:
    """운영과 같은 순서로 보고서 본문(과 공개 봉인)을 먼저 저장한다.

    ★ 왜 필요한가 — 운영에서 `_finalize_report_delivery`는
      `report_saved`가 참일 때만 돈다. 즉 delivery를 확정할 때 `reports` 행과
      공개 봉인 행이 이미 있다. 이 시험들이 그 단계를 건너뛰면 「생성 증거는
      봉인을 가리키는데 봉인이 없다」는, 운영에는 없는 상태가 만들어져
      `load_public_delivery`가 fail-closed로 닫는다.
    """

    with storage_db.connect() as conn:
        assert report_store.insert_new(
            conn,
            report_id,
            _COMPANY_ID,
            "분석",
            report,
            engine_epoch_digest=frozen.epoch_digest,
        )


def test_COMPLETE_재시도는_저장된_ReleaseAuthority를_다시검증한다(
    monkeypatch, tmp_path: Path
):
    """P1-2 — 응답 유실 뒤 재시도도 저장된 권위를 exact 재확인하고 같은 값을 돌려준다."""

    frozen = _frozen_identity()
    report = _build_full_report(build_identity_sha256=frozen.epoch_digest)
    report_id = uuid.uuid4().hex
    monkeypatch.setenv("APP_DATA_ROOT", str(tmp_path / "art"))
    _store_report_row(report_id, report, frozen)

    first = reports_router.finalize_new_report_delivery(
        report_id=report_id,
        corp_id=_COMPANY_ID,
        billing_bucket_id="release-wiring-bucket",
        report=report,
        actual_models=("deterministic-full-wiring",),
        reused_from_cache=False,
        engine_build_identity=frozen,
        **_full_delivery_identity(report, frozen),
    )

    # 응답만 잃은 재시도 — 같은 인자로 다시 부른다. intent가 이미 COMPLETE다.
    second = reports_router.finalize_new_report_delivery(
        report_id=report_id,
        corp_id=_COMPANY_ID,
        billing_bucket_id="release-wiring-bucket",
        report=report,
        actual_models=("deterministic-full-wiring",),
        reused_from_cache=False,
        engine_build_identity=frozen,
        **_full_delivery_identity(report, frozen),
    )

    assert second.content.content_id == first.content.content_id
    assert second.artifact is not None and first.artifact is not None
    assert second.artifact.artifact_id == first.artifact.artifact_id
    with storage_db.connect_readonly_existing() as conn:
        assert conn is not None
        authority = authority_store.load_release_authority_by_public_id(
            conn, report_id
        )
    assert authority is not None
    assert authority.content_snapshot_id == first.content.content_id


def test_FULL_재사용출고는_REUSE_authority를_발급하고_COMPLETE_재시도도_검증한다(
    monkeypatch, tmp_path: Path
):
    """waiter는 OWNER의 생성 원본을 상속하되 새 전달·청구 권위를 갖는다."""

    frozen = _frozen_identity()
    report = _build_full_report(build_identity_sha256=frozen.epoch_digest)
    owner_report_id = uuid.uuid4().hex
    waiter_report_id = uuid.uuid4().hex
    monkeypatch.setenv("APP_DATA_ROOT", str(tmp_path / "art"))
    _store_report_row(owner_report_id, report, frozen)
    _store_report_row(waiter_report_id, report, frozen)
    # 재사용 경로(persist_reused_delivery)는 owner·waiter가 같은 DART 출처와
    # 같은 정식 캐시 namespace/preflight 지문을 대야 한다(cache_key 재사용
    # 경로) — 이 시험의 목적(authority 없는 재시도)과는 무관한 별도
    # 검증이라 test_일반캐시_hit은...(test_report_delivery_integration.py)와
    # 같은 방식으로 owner·waiter 양쪽에 같은 값을 그대로 맞춘다.
    revision, image = report_delivery_adapter._release_identity(frozen)
    namespace = CacheNamespace.create(
        product="company-analysis",
        schema_version=report.schema_version or "legacy-report-schema",
        deployment_revision=revision,
        image_digest=image,
        requested_models={"pipeline": "deterministic-full-wiring"},
        output_settings={"temperature": 0},
    )
    preflight_digest = _PREFLIGHT_IDENTITY_DIGEST

    owner = reports_router.finalize_new_report_delivery(
        report_id=owner_report_id,
        corp_id=_COMPANY_ID,
        billing_bucket_id="release-wiring-owner-bucket",
        report=report,
        actual_models=("deterministic-full-wiring",),
        reused_from_cache=False,
        engine_build_identity=frozen,
        dart_receipt_numbers=(_DART_RECEIPT,),
        financial_payload_digest=_FINANCIAL_DIGEST,
        cache_namespace=namespace,
        preflight_identity_digest=preflight_digest,
        cache_eligible=True,
    )
    assert owner.artifact is not None
    with storage_db.connect_readonly_existing() as conn:
        assert conn is not None
        stored_source = report_delivery_adapter.delivery_store.load_source_snapshot(
            conn, owner.content.source_snapshot_id
        )
    assert stored_source is not None
    assert stored_source.preflight_identity_digest == preflight_digest
    assert report.generation_evidence is not None
    assert (
        stored_source.preflight_identity_digest
        == report.generation_evidence.evidence_generation_sha256
    )

    reuse_kwargs = dict(
        report_id=waiter_report_id,
        corp_id=_COMPANY_ID,
        billing_bucket_id="release-wiring-owner-bucket",
        report=report,
        actual_models=("deterministic-full-wiring",),
        reused_from_cache=True,
        reuse_content_snapshot_id=owner.content.content_id,
        reuse_artifact_id=owner.artifact.artifact_id,
        engine_build_identity=frozen,
        dart_receipt_numbers=(_DART_RECEIPT,),
        financial_payload_digest=_FINANCIAL_DIGEST,
        cache_namespace=namespace,
        preflight_identity_digest=preflight_digest,
        cache_eligible=True,
    )
    first_waiter = reports_router.finalize_new_report_delivery(**reuse_kwargs)
    assert first_waiter.content.content_id == owner.content.content_id

    # 응답만 잃은 재시도 — 같은 waiter report_id로 다시 부른다.
    second_waiter = reports_router.finalize_new_report_delivery(**reuse_kwargs)

    assert second_waiter.content.content_id == owner.content.content_id
    with storage_db.connect_readonly_existing() as conn:
        assert conn is not None
        waiter_authority = authority_store.load_release_authority_by_public_id(
            conn, waiter_report_id
        )
        owner_authority = authority_store.load_release_authority_by_public_id(
            conn, owner_report_id
        )
    assert waiter_authority is not None
    assert owner_authority is not None
    assert waiter_authority.kind is authority_store.ReleaseAuthorityKind.REUSE
    assert waiter_authority.origin_authority_id == owner_authority.authority_id
    assert waiter_authority.public_id != owner_authority.public_id
    assert waiter_authority.delivery_id != owner_authority.delivery_id
    assert waiter_authority.charge_run_id != owner_authority.charge_run_id
    assert waiter_authority.content_snapshot_id == owner_authority.content_snapshot_id
    assert waiter_authority.artifact_id == owner_authority.artifact_id
    assert (
        waiter_authority.producer_evidence_sha256
        == owner_authority.producer_evidence_sha256
    )
    assert (
        waiter_authority.automatic_release_sha256
        == owner_authority.automatic_release_sha256
    )

    # COMPLETE라고 해서 자기 REUSE 권위의 손상을 건너뛰지 않는다. 기존 공개
    # GET은 아래 current-contract 재검증을 소급하지 않지만, 출고 최종화 재시도는
    # 저장된 자기 권위를 exact하게 다시 확인해야 한다.
    with storage_db.connect() as conn:
        conn.execute("DROP TRIGGER report_release_authorities_no_update")
        conn.execute(
            f"UPDATE {authority_store.TABLE_RELEASE_AUTHORITIES} "
            "SET producer_evidence_sha256 = ? WHERE public_id = ?",
            ("f" * 64, waiter_report_id),
        )
    with pytest.raises(authority_store.ReleaseAuthorityCorrupt):
        reports_router.finalize_new_report_delivery(**reuse_kwargs)
