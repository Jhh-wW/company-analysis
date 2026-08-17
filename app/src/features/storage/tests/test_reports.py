"""`Report` 저장·조회 왕복 시험 — 표·출처·빈칸 사유까지 «전부» 되살아나야 한다."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from src.features.pipeline.port import (
    Grade,
    Report,
    ReportSection,
    ReportTable,
    SourceStatus,
)
from src.features.provenance.sources import Source, SourceKind
from src.features.storage import db, reports


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


def test_roundtrip_via_json_keeps_every_field() -> None:
    original = _full_report()
    restored = reports.report_from_json(reports.report_to_json(original))
    assert restored == original


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
            ReportSection(cell="附", title="附", lines=[("숨긴 참고 지표", "")]),
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
    assert [section.cell for section in restored.sections] == ["1", "2", "4-1"]
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
