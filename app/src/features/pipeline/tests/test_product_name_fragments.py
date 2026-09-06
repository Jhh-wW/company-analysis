import json
from pathlib import Path

import pytest

from src.features.composer.constants import SECTION_IDS
from src.features.composer.logic import compose_sections
from src.features.composer.port import filing_meta_from_raw
from src.features.pipeline import real
from src.features.product_names.constants import SUBJECT_IP
from src.features.product_names.logic import (
    collect_name_candidates,
    collect_name_candidates_from_tables,
)
from src.features.product_names.tables import read_filing_tables
from src.shared.report_evidence.constants import SOURCE_KIND_DART_BUSINESS_REPORT
from src.shared.report_evidence.legacy_fragment_kinds import LEGACY_FRAGMENT_KINDS
from src.shared.report_generation.models import exact_text_sha256


FIXTURES = (
    Path(__file__).resolve().parents[2]
    / "product_names"
    / "tests"
    / "fixtures"
)
CORP_ID = "00126380"
RCEPT_NO = "20260315000123"
GENERATION_SHA256 = "b" * 64


def _typed_anchor() -> dict[str, object]:
    source_url = (
        "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + RCEPT_NO
    )
    return {
        "종류": SOURCE_KIND_DART_BUSINESS_REPORT,
        "원문": "공시에서 이미 검증된 원문 조각이다.",
        "출처": source_url,
        "문서ID": f"{SOURCE_KIND_DART_BUSINESS_REPORT}:{RCEPT_NO}",
        "문서명": "사업보고서 (2025.12)",
        "문서일": "2026-03-15",
        "원문위치": "사업의 내용",
        "company_id": CORP_ID,
        "_evidence_section_ids": ("identity",),
        "_evidence_slot_ids": ("identity:legal_entity",),
        "_evidence_origin_fragment_ids": ("dart:anchor",),
        "_evidence_document_identity": f"document:dart.fss.or.kr:{RCEPT_NO}",
        "_evidence_document_content_sha256": "a" * 64,
        "_evidence_identity_binding": "fixture_identity_binding",
        "_evidence_publisher": "금융감독원 전자공시시스템",
        "_evidence_collected_on": "2026-03-15",
        "_evidence_domain_attestation_source_id": "",
        "_evidence_domain_attestation_evidence": "",
        "_evidence_reporting_period": "",
        "_evidence_attachment_url": "",
        "_evidence_ir_metadata_verification": "",
        "_evidence_domain_redirect_verification": "",
        "_evidence_domain_redirect_from_host": "",
        "_evidence_domain_redirect_to_host": "",
    }


def _legacy_fragments() -> dict[int, dict[str, object]]:
    return {
        number: {
            "종류": kind,
            "원문": f"테스트 회사의 {kind} 공식 근거다.",
        }
        for number, kind in enumerate(sorted(LEGACY_FRAGMENT_KINDS), start=1)
    }


def _filing() -> dict[str, str]:
    return {
        "rcept_no": RCEPT_NO,
        "report_nm": "사업보고서 (2025.12)",
        "rcept_dt": "20260315",
    }


def _kakao_text() -> str:
    return (FIXTURES / "kakao_product_services.txt").read_text(encoding="utf-8")


def _hybe_artist_path() -> Path:
    return FIXTURES / "hybe_artist_contracts.xml"


def test_이름조각은_packet과_가짜작가의_3장카드를_통과한다() -> None:
    before = _legacy_fragments()
    steps: list[dict[str, object]] = []
    frags, added = real._attach_name_candidate_fragments(  # noqa: SLF001
        before,
        filing_text=_kakao_text(),
        filing_meta=_filing(),
        corp_id=CORP_ID,
        typed_fragments=(_typed_anchor(),),
        steps=steps,
    )

    assert added > 0
    assert set(before) < set(frags)
    assert min(set(frags) - set(before)) > max(before)
    name_number = next(
        number
        for number, raw in frags.items()
        if raw.get("_evidence_slot_ids") == ("portfolio:product_role",)
        and "카카오톡" in str(raw.get("원문") or "")
    )
    packets = real._full_section_evidence_packets(  # noqa: SLF001
        corp_id=CORP_ID,
        source_identity_digest=GENERATION_SHA256,
        frags=frags,
        filing_meta=filing_meta_from_raw(_filing()),
    )
    portfolio_packet = next(
        packet for packet in packets.packets if packet.section_id == "portfolio"
    )
    name_fragment = next(
        fragment
        for fragment in portfolio_packet.fragments
        if fragment.fragment_id == str(name_number)
    )
    assert name_fragment.supported_claim_slots == ("portfolio:product_role",)
    candidate = next(
        item
        for item in collect_name_candidates(
            _kakao_text(), source_kind=SOURCE_KIND_DART_BUSINESS_REPORT
        )
        if item.name == "카카오톡"
    )
    assert name_fragment.text == candidate.excerpt
    assert exact_text_sha256(name_fragment.text) == candidate.excerpt_sha256

    calls = 0

    def fake_writer(_prompt: str) -> str:
        nonlocal calls
        section_id = SECTION_IDS[calls]
        calls += 1
        flow_rows = []
        if section_id == "portfolio":
            flow_rows = [
                {
                    "칸": ["카카오톡", "", "", ""],
                    "인용": [str(name_number)],
                }
            ]
        return json.dumps(
            {"문장들": [], "경로표": flow_rows}, ensure_ascii=False
        )

    composed = compose_sections(
        "테스트 회사",
        {},
        None,
        fake_writer,
        section_evidence_packets=packets,
    )
    portfolio = next(
        section for section in composed.sections if section.section_id == "portfolio"
    )

    assert calls == len(SECTION_IDS)
    assert len(portfolio.flow_rows) == 1
    assert portfolio.flow_rows[0].cells[0] == "카카오톡"
    assert portfolio.flow_rows[0].citations == (str(name_number),)


def test_typed신원이_없어도_후보수와_단계는_남고_조각은_없다() -> None:
    before = _legacy_fragments()
    steps: list[dict[str, object]] = []

    frags, added = real._attach_name_candidate_fragments(  # noqa: SLF001
        before,
        filing_text=_kakao_text(),
        filing_meta=_filing(),
        corp_id=CORP_ID,
        typed_fragments=(),
        steps=steps,
    )

    assert added == 0
    assert frags == before
    assert steps == [
        {
            "step": "7_이름후보",
            "후보": 8,
            "조각": 0,
            "종류별": {"segment": 2, "product": 6},
            "상한적용": False,
            "입력": real.NAME_INPUT_TEXT,
            "표수": 0,
        }
    ]


# ─────────────────────────────────────────────────────────────────────
# 표 구조 입력 — filing_text는 공백이 접힌 평문이라 표를 읽을 수 없다.
# 원문 파일 경로를 넘겨야 이름이 나온다.
# ─────────────────────────────────────────────────────────────────────

# 로컬에만 두는 실측 원문(공개 DART 사업보고서 사본). 없으면 그 시험만 건너뛴다.
LOCAL_HYBE_FILINGS = sorted(
    Path(
        "C:/Users/jh-wo/.claude/workspace/기업분석2/app/.local_evaluation_runs"
    ).glob("*/analysis_engine/pilot/raw_filings/20260320000802.xml")
)


def test_공백접힌_평문으로는_아티스트_이름을_못_읽는다() -> None:
    """이 시험이 초록이면 표 파서가 필요 없다는 뜻이다 — 실제로는 0건이다."""

    collapsed = " ".join(_hybe_artist_path().read_text(encoding="utf-8").split())

    assert collect_name_candidates(collapsed, source_kind="사업보고서") == ()


def test_원문경로를_넘기면_표에서_대표IP_조각이_생긴다() -> None:
    before = _legacy_fragments()
    steps: list[dict[str, object]] = []

    frags, added = real._attach_name_candidate_fragments(  # noqa: SLF001
        before,
        filing_text="",
        filing_meta=_filing(),
        corp_id=CORP_ID,
        typed_fragments=(_typed_anchor(),),
        steps=steps,
        raw_path=str(_hybe_artist_path()),
    )

    assert added > 0
    assert set(before) < set(frags)
    step = steps[-1]
    assert step["입력"] == real.NAME_INPUT_TABLE
    assert step["표수"] == 1
    assert step["종류별"] == {SUBJECT_IP: 18}
    ip_texts = [
        str(raw.get("원문") or "")
        for raw in frags.values()
        if str(raw.get("원문위치") or "").endswith(" · 대표 IP")
    ]
    # 이 픽스처에는 대표 IP 한 종류뿐이라 남는 자리까지 IP가 채운다.
    assert len(ip_texts) == 16
    assert any("방탄소년단" in text for text in ip_texts)
    assert any("뉴진스" in text for text in ip_texts)


def test_표에서_만든_이름조각도_transport_사전검증을_통과한다() -> None:
    frags, added = real._attach_name_candidate_fragments(  # noqa: SLF001
        _legacy_fragments(),
        filing_text="",
        filing_meta=_filing(),
        corp_id=CORP_ID,
        typed_fragments=(_typed_anchor(),),
        steps=[],
        raw_path=str(_hybe_artist_path()),
    )
    packets = real._full_section_evidence_packets(  # noqa: SLF001
        corp_id=CORP_ID,
        source_identity_digest=GENERATION_SHA256,
        frags=frags,
        filing_meta=filing_meta_from_raw(_filing()),
    )
    portfolio_packet = next(
        packet for packet in packets.packets if packet.section_id == "portfolio"
    )
    name_fragments = [
        fragment
        for fragment in portfolio_packet.fragments
        if fragment.supported_claim_slots == ("portfolio:product_role",)
    ]

    assert len(name_fragments) == added
    assert any("뉴진스" in fragment.text for fragment in name_fragments)


def test_원문파일이_없으면_평문_파서로_되돌아간다(tmp_path: Path) -> None:
    steps: list[dict[str, object]] = []

    _, added = real._attach_name_candidate_fragments(  # noqa: SLF001
        _legacy_fragments(),
        filing_text=_kakao_text(),
        filing_meta=_filing(),
        corp_id=CORP_ID,
        typed_fragments=(_typed_anchor(),),
        steps=steps,
        raw_path=str(tmp_path / "없는파일.xml"),
    )

    assert added > 0
    assert steps[-1]["입력"] == real.NAME_INPUT_TEXT
    assert steps[-1]["표수"] == 0


@pytest.mark.skipif(
    not LOCAL_HYBE_FILINGS, reason="로컬 공시 원문 사본이 없는 환경입니다"
)
def test_실측_하이브_사업보고서_전체에서_대표IP를_읽는다() -> None:
    tables = read_filing_tables(LOCAL_HYBE_FILINGS[0])
    candidates = collect_name_candidates_from_tables(
        tables, source_kind="사업보고서"
    )
    names = {candidate.name for candidate in candidates}
    ip_names = {
        candidate.name
        for candidate in candidates
        if candidate.subject_kind == SUBJECT_IP
    }

    assert len(tables) > 100
    assert len(candidates) >= 10
    assert len(ip_names) >= 8
    assert {"방탄소년단", "뉴진스", "세븐틴", "르세라핌"} <= ip_names
    # 멤버(사람) 이름이 후보로 새면 안 된다.
    assert not ({"김남준", "김석진", "황민현"} & names)
