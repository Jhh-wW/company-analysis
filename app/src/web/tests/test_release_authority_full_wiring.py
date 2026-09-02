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

import json
import re
import uuid
from pathlib import Path

import pytest

from src.features.composer.constants import GRADE_CONFIRMED, SECTION_IDS
from src.features.composer.pipeline import run_v2
from src.features.composer.port import (
    CollectedFragment,
    SectionEvidencePacket,
    SectionEvidencePacketSet,
)
from src.features.pipeline.port import Report
from src.features.report_delivery import authority as authority_store
from src.features.report_delivery.cache_identity import CacheNamespace
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.shared import engine_build_identity as build_identity_contract
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_quality.source_identity import document_identity_from_parts
from src.shared.report_source_identity import (
    ReportSourceIdentity,
    financial_payload_digest,
)
from src.web import report_delivery_adapter
from src.web.routers import reports as reports_router

_COMPANY_ID = "00123456"
_BUILD_IDENTITY_SHA256 = "b" * 64


def _strict_fragments() -> dict[int, dict[str, str]]:
    document_marks = ("가람", "나래", "다솜", "라온", "마루", "바다", "사랑", "아람")
    return {
        number: {
            "종류": "공식 홈페이지",
            "원문": (
                "가나다전자는 공식 자료에서 회사 사업 고객 제품 전략 운영 문화 "
                f"경쟁 과제 대응 협력 실적을 설명한다. 문서 표지는 {document_marks[number - 1]}이다."
            ),
            "출처": f"https://www.ganada.example/document/{number}",
            "문서명": f"공식 자료 {number}",
        }
        for number in range(1, 9)
    }


def _strict_packet_set(*, evidence_texts: tuple[str, ...] = ()) -> SectionEvidencePacketSet:
    """composer/tests/test_pipeline.py의 FULL 고정 입력을 그대로 재현한다."""

    fragments = tuple(
        CollectedFragment(
            fragment_id=str(number),
            kind=str(raw["종류"]),
            text=" ".join((str(raw["원문"]), *evidence_texts)).strip(),
            source_url=str(raw["출처"]),
            document_title=str(raw["문서명"]),
            document_identity=document_identity_from_parts(url=str(raw["출처"])),
        )
        for number, raw in _strict_fragments().items()
    )
    generation = "a" * 64
    return SectionEvidencePacketSet(
        company_id=_COMPANY_ID,
        evidence_generation_sha256=generation,
        packets=tuple(
            SectionEvidencePacket(
                company_id=_COMPANY_ID,
                evidence_generation_sha256=generation,
                section_id=section_id,
                fragments=fragments,
            )
            for section_id in SECTION_IDS
        ),
    )


class _CompleteWriter:
    """9개 장 모두 5문장씩 검증 가능한 confirmed 문장으로 채우는 가짜 작가."""

    _TOPICS = (
        "법인 정체성과 설립 목적 및 공식 사업 범위",
        "고객 유형별 수익 방식과 판매 채널 및 가치 교환",
        "제품 묶음별 역할과 고객 적합성 및 사업 연결",
        "과거 완료 실행과 실적 변화 및 확인할 한계",
        "현재 해결 과제와 대응 행동 및 남은 점검 항목",
        "향후 발표 전략과 실행 시점 및 필요한 선행 조건",
        "공급 생산 유통 협력 관계와 회사의 운영 역할",
        "리더십 업무 원칙 의사결정 방식과 검증 사례",
        "비교 대상 지표 기준 범위와 경쟁 판단의 한계",
    )
    _ENDINGS = (
        "첫째 의미를 공식 자료에서 확인했다.",
        "둘째 대상을 공식 자료에서 확인했다.",
        "셋째 경로를 공식 자료에서 확인했다.",
        "넷째 범위를 공식 자료에서 확인했다.",
        "다섯째 근거를 공식 자료에서 확인했다.",
    )

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.section_calls = 0

    @property
    def expected_sentences(self) -> tuple[str, ...]:
        return tuple(
            f"가나다전자는 {topic}의 {ending}"
            for topic in self._TOPICS
            for ending in self._ENDINGS
        )

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        section_index = self.section_calls
        section_id = SECTION_IDS[section_index]
        self.section_calls += 1
        slots = CLAIM_SLOTS_BY_SECTION[section_id]
        return json.dumps(
            {
                "문장들": [
                    {
                        "글": f"가나다전자는 {self._TOPICS[section_index]}의 {ending}",
                        "인용": [str((section_index * 5 + index) % 8 + 1)],
                        "등급": GRADE_CONFIRMED,
                        "주장슬롯": slots[index],
                    }
                    for index, ending in enumerate(self._ENDINGS)
                ]
            },
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
    *, company_id: str = _COMPANY_ID, build_identity_sha256: str = _BUILD_IDENTITY_SHA256
) -> Report:
    """FULL producer evidence를 실제로 계산해 붙인 Report 한 벌을 만든다."""

    writer = _CompleteWriter()
    reviewer = _FakeReviewer()
    output = run_v2(
        "가나다전자",
        _strict_fragments(),
        None,
        writer_ask=writer,
        reviewer_ask=reviewer,
        release_mode=ReleaseMode.FULL,
        section_evidence_packets=_strict_packet_set(
            evidence_texts=writer.expected_sentences
        ),
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
    assert report.company_id == _COMPANY_ID
    assert report.grade.value == "완성"


def _frozen_identity() -> build_identity_contract.EngineBuildIdentity:
    return build_identity_contract.process_engine_build_identity()


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
    # ★ 뒤집힌 단정(2026-09-02, root 결정 D4-a) — 예전에는 pre-render 공개 content
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
            corp_id="99999999",  # evidence.company_id("00123456")와 다름
            billing_bucket_id="release-wiring-bucket",
            report=report,
            actual_models=("deterministic-full-wiring",),
            reused_from_cache=False,
            engine_build_identity=frozen,
        )

    assert report_delivery_adapter.load_public_delivery(report_id) is None
    with storage_db.connect_readonly_existing() as conn:
        assert conn is not None
        assert (
            authority_store.load_release_authority_by_public_id(conn, report_id)
            is None
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

    ★ 왜 필요한가(S3f, 2026-09-02) — 운영에서 `_finalize_report_delivery`는
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


def test_FULL_재사용출고의_COMPLETE_재시도는_owner_authority가_없어도_성공한다(
    monkeypatch, tmp_path: Path
):
    """single-flight waiter/캐시 재사용 delivery는 자기 자신의 authority가
    없다(owner만 발급한다, 이번 커밋의 명시적 스코프). 그 재사용 delivery의
    COMPLETE 재시도가 "저장된 출고 권위가 없다"며 깨지면 안 된다 — 이건
    owner 완료 재시도(위 시험)와는 다른 시나리오다."""

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
    receipt = "20260828000123"
    finance_digest = financial_payload_digest(
        {"status": "000", "list": [{"account_nm": "매출액", "thstrm_amount": "100"}]}
    )
    revision, image = report_delivery_adapter._release_identity(frozen)
    namespace = CacheNamespace.create(
        product="company-analysis",
        schema_version=report.schema_version or "legacy-report-schema",
        deployment_revision=revision,
        image_digest=image,
        requested_models={"pipeline": "deterministic-full-wiring"},
        output_settings={"temperature": 0},
    )
    preflight_digest = ReportSourceIdentity(
        dart_receipt_numbers=(receipt,),
        financial_payload_digest=finance_digest,
    ).cache_digest

    owner = reports_router.finalize_new_report_delivery(
        report_id=owner_report_id,
        corp_id=_COMPANY_ID,
        billing_bucket_id="release-wiring-owner-bucket",
        report=report,
        actual_models=("deterministic-full-wiring",),
        reused_from_cache=False,
        engine_build_identity=frozen,
        dart_receipt_numbers=(receipt,),
        financial_payload_digest=finance_digest,
        cache_namespace=namespace,
        preflight_identity_digest=preflight_digest,
        cache_eligible=True,
    )
    assert owner.artifact is not None

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
        dart_receipt_numbers=(receipt,),
        financial_payload_digest=finance_digest,
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
    # 명시적 스코프: owner만 authority를 갖는다. waiter는 아직 없다(따라온
    # 다음 커밋 몫) — 그렇더라도 재시도 자체는 깨지지 않아야 한다.
    assert waiter_authority is None
    assert owner_authority is not None
