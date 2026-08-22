"""`Report` 저장·조회 왕복 시험 — 표·출처·빈칸 사유까지 «전부» 되살아나야 한다."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from src.features.pipeline.port import (
    FactRecord,
    Grade,
    Report,
    ReportSection,
    ReportTable,
    SourceStatus,
    SummaryItem,
)
from src.features.export_pdf.automatic_release import report_sha256
from src.features.provenance.sources import (
    Source,
    SourceKind,
    evidence_text_hash,
    exact_evidence_text_hash,
    has_valid_provenance_seal,
    seal_collected_source,
)
from src.features.report_standard.constants import CANONICAL_SCHEMA_VERSION
from src.features.storage import db, reports
from src.shared.comparison_candidate_basis import (
    COMPARISON_BASIS_VERSION,
    encode_comparison_basis_v1,
    parse_comparison_basis_v1,
)


def _full_report() -> Report:
    """왕복 시험에 쓸, 있을 수 있는 모양을 다 채운 보고서 하나."""
    return Report(
        company="가나다전자",
        job="영업",
        corp_type="상장사",
        grade=Grade.PARTIAL,
        sections=[
            ReportSection(
                cell="1",
                title="뭘 팔아서 돈 버나",
                lines=[("반도체를 만들어 판다", "[1]"), ("최근 해외 매출이 늘었다", "[2]")],
                prose_lines=[
                    ("가나다전자는 반도체를 만들며 해외 매출도 늘고 있다.", "[1]")
                ],
                tables=[
                    ReportTable(
                        caption="전자공시 주요 재무계정",
                        headers=["구분", "당기", "전기"],
                        rows=[["매출액", "1,000", "900"]],
                        cite="전자공시 재무 API",
                        numeric=True,
                    )
                ],
            ),
            ReportSection(
                cell="4-1",
                title="지금 뭐가 문제인가",
                empty_reason="이 회사의 공개 자료에 해당 내용이 없습니다",
            ),
        ],
        requirements=["관련 전공자 우대", "3년 이상 경력"],
        sources=[
            SourceStatus(name="전자공시", state="ok", detail="사업보고서 · 조각 12개"),
            SourceStatus(name="뉴스", state="none", detail="검색 3건 · 채택 조건 통과 0건"),
        ],
        citations=[
            Source(
                number=1,
                kind=SourceKind.FILING,
                label="사업보고서 재무제표 주석",
                disclosed_at="2026-03-15",
                collected_at="2026-08-15",
            ),
            Source(
                number=2,
                kind=SourceKind.NEWS,
                label="가나다전자, 해외 매출 확대",
                published_at="2026-05-01",
                domain="example.co.kr",
            ),
        ],
        cells={"1": True, "4-1": False},
        shortfall_reasons=["채워진 항목이 3개입니다 (4개 이상 필요)"],
        generated_at="2026-08-15",
    )


def test_경쟁사후보_v1은_report_dict_json_왕복에서_그대로_보존된다() -> None:
    basis = encode_comparison_basis_v1(
        {
            "version": COMPARISON_BASIS_VERSION,
            "candidate_fact_id": "fact-origin-01",
            "candidate_source_id": "source-origin-01",
            "candidate_corp_code": "00000002",
            "candidate_name": "주식회사 베타",
            "filing_document_id": "20260315000001",
            "evidence_sha256": "a" * 64,
            "overlap_dimension": "시장 겹침",
        }
    )
    original = Report(
        company="주식회사 알파",
        job="",
        corp_type="상장사",
        grade=Grade.COMPLETE,
        sections=[],
        fact_records=[
            FactRecord(
                fact_id="fact-compare-01",
                comparison_basis=basis,
            )
        ],
    )

    dict_restored = reports.report_from_dict(reports.report_to_dict(original))
    json_restored = reports.report_from_json(reports.report_to_json(original))

    assert dict_restored == original
    assert json_restored == original
    assert parse_comparison_basis_v1(
        json_restored.fact_records[0].comparison_basis
    ) == parse_comparison_basis_v1(basis)


def test_기본_citation_JSON과_report_digest는_legacy_키집합을_유지한다() -> None:
    report = _full_report()
    payload = reports.report_to_dict(report)

    assert all(
        "provenance_role" not in citation for citation in payload["citations"]
    )
    assert all(
        "exact_evidence_hashes" not in citation
        for citation in payload["citations"]
    )
    expected = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert report_sha256(report) == expected


def test_attestation_only는_DB_재로드뒤_역할과_HMAC을_보존한다(
    tmp_path: Path,
) -> None:
    attester = seal_collected_source(
        Source(
            number=3,
            kind=SourceKind.FILING,
            label="OpenDART 기업개황",
            collected_at="2026-08-23",
            source_id="dart-company-profile-00000001",
            title="OpenDART 기업개황",
            publisher="주식회사 알파",
            host="opendart.fss.or.kr",
            url="https://opendart.fss.or.kr/api/company.json?corp_code=00000001",
            document_id="00000001",
            location="corp_code/corp_name/hm_url",
            source_type="규제기관 공식 자료",
            fact_status="기준일 현재 확인",
            evidence_hashes=[evidence_text_hash("봉인된 기업개황")],
            exact_evidence_hashes=[
                exact_evidence_text_hash("봉인된 기업개황")
            ],
            domain_attestation_evidence="봉인된 기업개황",
            provenance_role="attestation_only",
        )
    )
    original = replace(
        _full_report(),
        schema_version=CANONICAL_SCHEMA_VERSION,
        citations=[*_full_report().citations, attester],
    )
    payload = reports.report_to_dict(original)

    assert "provenance_role" not in payload["citations"][0]
    assert payload["citations"][-1]["provenance_role"] == "attestation_only"
    assert payload["citations"][-1]["exact_evidence_hashes"] == (
        attester.exact_evidence_hashes
    )
    target = tmp_path / "attestation-role.db"
    with db.connect(target) as conn:
        reports.save(
            conn,
            "report-attestation-role",
            "00000001",
            "",
            original,
            created_at="2026-08-23T01:00:00+09:00",
        )
        restored = reports.load(conn, "report-attestation-role")

    assert restored is not None
    restored_attester = restored.citations[-1]
    assert restored_attester.provenance_role == "attestation_only"
    assert restored_attester.exact_evidence_hashes == attester.exact_evidence_hashes
    assert has_valid_provenance_seal(restored_attester)


def test_roundtrip_via_json_keeps_every_field() -> None:
    original = _full_report()
    restored = reports.report_from_json(reports.report_to_json(original))
    assert restored == original


def test_활용_질문은_사실문장과_분리된_필드로_왕복한다() -> None:
    original = _full_report()
    use = ReportSection(
        cell="활용",
        title="자기소개서·면접 활용 포인트",
        lines=[("해외 매출이 늘었다", "[2]")],
        guidance_lines=["활용 질문 — 이 사실의 기준 기간을 확인했는가?"],
    )
    original = replace(original, job="", requirements=[], sections=[*original.sections, use])

    restored = reports.report_from_json(reports.report_to_json(original))

    assert restored.sections[-1].lines == use.lines
    assert restored.sections[-1].guidance_lines == use.guidance_lines


def test_옛_payload에_활용질문_필드가_없어도_빈목록으로_읽는다() -> None:
    data = reports.report_to_dict(_full_report())
    for section in data["sections"]:
        section.pop("guidance_lines", None)

    restored = reports.report_from_dict(data)

    assert all(section.guidance_lines == [] for section in restored.sections)


def test_옛_저장값에_표시용글이_없어도_원문보고서를_연다() -> None:
    data = reports.report_to_dict(_full_report())
    for section in data["sections"]:
        section.pop("prose_lines", None)
        section["prose"] = "출처 대응이 없어 살리면 안 되는 옛 문자열"

    restored = reports.report_from_dict(data)

    assert restored.sections[0].prose_lines == []
    assert restored.sections[0].lines == _full_report().sections[0].lines


def test_깨진_표시용글은_버리고_정상_문장만_되살린다() -> None:
    data = reports.report_to_dict(_full_report())
    data["sections"][0]["prose_lines"] = [
        ["정상 문장", "[1]"],
        ["출처 없음", ""],
        ["열 하나"],
        123,
    ]

    restored = reports.report_from_dict(data)

    assert restored.sections[0].prose_lines == [("정상 문장", "[1]")]


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    original = _full_report()
    target = tmp_path / "storage.db"

    with db.connect(target) as conn:
        reports.save(conn, "r1", "CORP-001", "영업", original)

    with db.connect(target) as conn:
        restored = reports.load(conn, "r1")

    assert restored == original


def test_옛_보고서는_로드할_때만_현재_6칸_규칙으로_재계산한다(tmp_path: Path) -> None:
    """저장 원본은 보존하고 화면·워드·노션에 넘길 객체만 A-1을 적용한다."""
    legacy = Report(
        company="옛보고서주식회사",
        job="영업",
        corp_type="상장사",
        # 옛 7칸 규칙에서는 1·2·4-1·9 = 4칸이라 완성이었다.
        grade=Grade.COMPLETE,
        sections=[
            ReportSection(cell="1", title="1", lines=[("사업 구조", "[1]")]),
            ReportSection(cell="2", title="2", lines=[("핵심 경쟁력", "[1]")]),
            ReportSection(cell="4-1", title="4-1", lines=[("당면 과제", "[2]")]),
            ReportSection(cell="6", title="6", lines=[("숨긴 직무 블록", "")]),
            ReportSection(cell="7", title="7", lines=[("숨긴 공고 블록", "")]),
            ReportSection(cell="9", title="9", lines=[("주요 거래처", "[3]")]),
            ReportSection(cell="附", title="附", lines=[("참고 지표", "")]),
        ],
        cells={"1": True, "2": True, "4-1": True, "6": True, "7": True, "9": True, "附": True},
        shortfall_reasons=[],
        generated_at="2026-08-15",
    )
    target = tmp_path / "storage.db"

    with db.connect(target) as conn:
        reports.save(conn, "legacy", "CORP-OLD", "영업", legacy)
        raw_before = conn.execute(
            "SELECT payload_json FROM reports WHERE report_id = 'legacy'"
        ).fetchone()["payload_json"]
        restored = reports.load(conn, "legacy")
        raw_after = conn.execute(
            "SELECT payload_json FROM reports WHERE report_id = 'legacy'"
        ).fetchone()["payload_json"]

    assert restored is not None
    assert [section.cell for section in restored.sections] == ["1", "2", "4-1", "9", "附"]
    assert set(restored.cells) <= {"1", "2", "3", "4-1", "4-2", "4-3"}
    assert restored.filled_count == 3
    assert restored.grade is Grade.PARTIAL
    assert any("4개 이상" in reason for reason in restored.shortfall_reasons)
    assert raw_after == raw_before, "로드하면서 옛 JSON을 덮어썼습니다"

    raw_report = reports.report_from_json(raw_before)
    assert raw_report.grade is Grade.COMPLETE
    assert any(section.cell == "9" for section in raw_report.sections)


def test_load_missing_report_returns_none(tmp_path: Path) -> None:
    with db.connect(tmp_path / "storage.db") as conn:
        assert reports.load(conn, "없는-아이디") is None


def test_v3_roundtrip_preserves_semantic_metadata_fact_ledger_and_sources() -> None:
    source = Source(
        number=1,
        kind=SourceKind.FILING,
        label="2025 사업보고서",
        disclosed_at="2026-03-18",
        source_id="src-1",
        title="2025 사업보고서",
        publisher="가나다전자",
        host="DART",
        url="https://dart.example/report/1",
        document_id="202603180001",
        location="PDF p.12 사업의 내용",
        source_type="공식 공시",
        fact_status="실제",
        used_in=["identity"],
        evidence_hashes=[evidence_text_hash("사업의 내용")],
        domain_attestation_source_id="src-domain-proof",
        domain_attestation_evidence=(
            "사업보고서 회사 개요: 홈페이지 https://company.example"
        ),
    )
    fact = FactRecord(
        fact_id="fact-identity",
        legal_entity="가나다전자 주식회사",
        subject_scope="전사",
        relationship_or_action="공식 자기정의",
        claim="가나다전자는 산업용 센서를 설계하고 판매한다.",
        claim_type="공식 사실",
        section_owner="identity",
        time_state="standing",
        as_of="2026-03-18",
        source_id="src-1",
        source_type="공식 공시",
        source_title="2025 사업보고서",
        source_publisher="가나다전자",
        location="PDF p.12",
        status="verified",
        fact_status="actual",
        verification_status="verified",
        state_evidence="사업의 내용",
        source_date="2026-03-18",
        evidence_support_terms=["산업용", "센서"],
        evidence_binding="a" * 64,
        market_priority="국내 핵심",
        basis_fact_ids=["fact-basis"],
        numeric_checks=["1|1|0|1"],
    )
    original = Report(
        company="가나다전자",
        job="",
        corp_type="상장사",
        grade=Grade.COMPLETE,
        sections=[
            ReportSection(
                cell="identity",
                title="임시 제목",
                display_number="1",
                fact_ids=[fact.fact_id],
            ),
            # v3에서는 숫자 5~8을 레거시라고 추정해 삭제하면 안 된다.
            ReportSection(cell="5", title="보존 확인", fact_ids=[]),
        ],
        citations=[source],
        schema_version=CANONICAL_SCHEMA_VERSION,
        summary_items=[
            SummaryItem(
                "회사의 핵심 정체성이 분명하다",
                "identity",
                fact_ids=[fact.fact_id],
                evidence_text=f"{fact.fact_id}: {fact.claim}",
                verification_status="independently_verified",
                verification_binding="b" * 64,
                support_terms=["회사", "정체성"],
            )
        ],
        fact_records=[fact],
        as_of_date="2026-08-19",
        analysis_period="2023~2025 완료 회계연도",
        latest_performance_period="2026년 2분기 잠정",
    )

    restored = reports.report_from_json(reports.report_to_json(original))

    assert restored == original


def test_v3_load_does_not_apply_legacy_hidden_cell_normalization(tmp_path: Path) -> None:
    original = Report(
        company="가나다전자",
        job="",
        corp_type="상장사",
        grade=Grade.COMPLETE,
        sections=[ReportSection(cell=str(number), title=str(number)) for number in range(5, 9)],
        cells={str(number): True for number in range(5, 9)},
        schema_version=CANONICAL_SCHEMA_VERSION,
    )
    target = tmp_path / "storage.db"

    with db.connect(target) as conn:
        reports.save(conn, "v3", "CORP-001", "", original)
        restored = reports.load(conn, "v3")

    assert restored == original


def test_save_same_id_twice_overwrites_not_duplicates(tmp_path: Path) -> None:
    target = tmp_path / "storage.db"
    first = _full_report()
    second = replace(first, grade=Grade.COMPLETE, shortfall_reasons=[])

    with db.connect(target) as conn:
        reports.save(conn, "same-id", "CORP-001", "영업", first)
        reports.save(conn, "same-id", "CORP-001", "영업", second)
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM reports WHERE report_id = 'same-id'"
        ).fetchone()["n"]
        restored = reports.load(conn, "same-id")

    assert count == 1
    assert restored is not None
    assert restored.grade is Grade.COMPLETE


def test_insert_new_same_id_preserves_existing_public_report(tmp_path: Path) -> None:
    target = tmp_path / "storage.db"
    first = _full_report()
    replacement = replace(first, company="덮어쓰면 안 되는 회사")

    with db.connect(target) as conn:
        reports.save(conn, "public-id", "CORP-001", "영업", first)
        inserted = reports.insert_new(
            conn, "public-id", "CORP-999", "개발", replacement
        )
        restored = reports.load(conn, "public-id")

    assert inserted is False
    assert restored == first


def test_db_file_never_contains_raw_posting_text(tmp_path: Path) -> None:
    """S2 — 공고 원문이 DB 파일 바이트 안에 없는지 직접 확인한다.

    ★ `Report`에는 애초에 공고 원문 필드가 없어 이 마커 문자열을 저장 함수에
      «넘길 방법 자체가 없다». 그 사실을 실제 파일 바이트를 뒤져 증명한다 —
      "필드가 없다"는 주장을 코드 리뷰가 아니라 시험으로 고정한다.
    """
    marker = "이건-절대-저장되면-안-되는-채용공고-원문-마커-XYZ123"
    report = _full_report()  # marker는 여기 어디에도 없다 — Report엔 자리가 없다
    target = tmp_path / "storage.db"

    with db.connect(target) as conn:
        reports.save(conn, "r1", "CORP-001", "영업", report)

    raw_bytes = target.read_bytes()
    assert marker.encode("utf-8") not in raw_bytes
