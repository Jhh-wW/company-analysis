"""장 끝 보도표가 «세 채널 전부»에 실제로 그려지는지 FULL 실행으로 본다.

★ 왜 필요한가 — 자료 층에 표 하나를 더해 놓고도 화면·PDF·노션 중 한 곳이라도
  그 표를 안 그리면 우리는 「실었다」고 믿는데 독자는 못 본다. 그리고 이 표가
  진짜로 위험한 자리는 FULL 봉인이다 — 인용이 장별 근거 소유권을 벗어나거나
  pre-render manifest가 표를 못 읽으면 «보고서 전체»가 막힌다. SHADOW로
  끝내면 그 경로를 한 번도 안 태우고 운영에서 처음 터진다.

★ AI·네트워크 0회. 가짜 작가는 뉴스 조각을 한 문장도 인용하지 않는다 —
  2026-09-06 운영 실측에서 실제 작가가 한 그대로다.
"""

from __future__ import annotations

import io
import re
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
from pypdf import PdfReader

from src.core import news_intake_switch
from src.features.composer.constants import SECTION_IDS
from src.features.composer.news_block import (
    NEWS_BLOCK_HEADERS,
    NEWS_BLOCK_MAX_ROWS,
    news_block_caption,
)
from src.features.composer.port import CollectedFragment
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_evidence.constants import SOURCE_KIND_NEWS
from src.shared.report_generation.models import exact_text_sha256
from src.shared.report_quality.source_identity import document_identity_from_parts


#: 가공 회사·매체 — 실존 회사·언론사를 시험에 박지 않는다.
_PUBLISHER = "가나다경제"
_ARTICLE_TITLE = "가나다전자 물류 자동화 확대"
_ARTICLE_URL = "https://media.example/news/logistics"
_COLLECTED_ON = "2026-09-06"

#: 장 → (조각 id, 발행일, 보도 문장). 숫자·퍼센트를 한 글자도 넣지 않는다
#: (상류 매핑이 숫자 있는 문장을 아예 조각으로 만들지 않는다).
_NEWS_BY_SECTION: dict[str, tuple[tuple[str, str, str], ...]] = {
    "identity": (
        ("41", "2026-08-11", "가나다전자는 물류 자동화 제품군을 넓히고 있다."),
        ("42", "2026-09-02", "가나다전자는 현장 설비 회사로 알려져 있다."),
        ("43", "2026-07-05", "가나다전자는 수도권 물류 현장에 제품을 넣었다."),
        ("44", "2026-06-01", "가나다전자는 협력사와 설비를 함께 만든다."),
    ),
    "business_model": (
        ("45", "2026-08-20", "가나다전자는 기업 고객에게 설비를 직접 공급한다."),
    ),
    "current_challenges": (
        (
            "46",
            "2026-08-30",
            '김대표는 "인력을 더 늘려야 하는 것이 지금의 과제"라고 말했다.',
        ),
    ),
}
_NEWS_SECTIONS = tuple(_NEWS_BY_SECTION)
_ARTIFACT_DIR = Path(__file__).resolve().parents[5] / ".local-artifacts" / "n13"


def _news_fragment(fragment_id: str, published_on: str, text: str) -> CollectedFragment:
    """운영 transport(`real._news_raw_fragment`)가 만드는 모양 그대로 만든다."""

    identity = document_identity_from_parts(
        document_id=_ARTICLE_URL,
        host="media.example",
        url=_ARTICLE_URL,
    )
    assert identity, "시험 조각의 문서 신원을 만들지 못했습니다"
    return CollectedFragment(
        fragment_id=fragment_id,
        # typed transport의 ``kind``는 불투명한 지문이다. 뉴스라는 사실은
        # ``formal_source_kind``에만 선언된다 — 운영과 같은 모양을 쓴다.
        kind="typed-evidence-v1:" + "0" * 8,
        text=text,
        source_url=_ARTICLE_URL,
        document_title=_ARTICLE_TITLE,
        location=f"기사 본문 · news-fragment-{fragment_id}",
        document_date=published_on,
        document_identity=identity,
        document_content_sha256=exact_text_sha256(text),
        counts_toward_document_floor=False,
        formal_source_kind=SOURCE_KIND_NEWS,
        source_document_id=_ARTICLE_URL,
        source_publisher=_PUBLISHER,
        source_collected_on=_COLLECTED_ON,
    )


def _packets_with_news():
    """정상 FULL packet에 뉴스 조각만 덧붙인다(공식 조각은 그대로)."""

    from src.features.composer.tests.test_section_public_manifest import _packets

    base = _packets(two_flow_sources=True)
    rebuilt = []
    for packet in base.packets:
        extra = _NEWS_BY_SECTION.get(packet.section_id, ())
        if not extra:
            rebuilt.append(packet)
            continue
        rebuilt.append(
            replace(
                packet,
                fragments=packet.fragments
                + tuple(
                    _news_fragment(fragment_id, published_on, text)
                    for fragment_id, published_on, text in extra
                ),
            )
        )
    return replace(base, packets=tuple(rebuilt))


@pytest.fixture(scope="module")
def FULL_실행결과():
    from src.features.composer.tests.test_section_public_manifest import _run_full

    with pytest.MonkeyPatch.context() as mp:
        # 보조 언론 출처의 공개 provenance 검사는 이 스위치가 켜져 있어야 한다.
        news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001
        mp.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, "1")
        output, _writer, _reviewer, _diagram = _run_full(
            flow=True, packets=_packets_with_news()
        )
    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001
    return output


@pytest.fixture(scope="module")
def 보고서(FULL_실행결과):
    return FULL_실행결과.report


def _news_table(report, section_id: str):
    section = next(
        section for section in report.sections if section.cell == section_id
    )
    return next(
        (
            table
            for table in section.tables
            if table.headers == list(NEWS_BLOCK_HEADERS)
        ),
        None,
    )


# ══════════════════════════════════════════════════════════
# ① 자료 층 — 조각을 받은 장마다 표가 있고 행이 원문 그대로다
# ══════════════════════════════════════════════════════════


def test_조각을_받은_세_장에_보도표가_붙는다(보고서) -> None:
    붙은_장 = [
        section.cell
        for section in 보고서.sections
        if _news_table(보고서, section.cell) is not None
    ]

    assert 붙은_장 == list(_NEWS_SECTIONS)


def test_작가는_뉴스를_한_문장도_인용하지_않았다(보고서) -> None:
    """이 시험이 없으면 「작가가 인용해서 실린 것」과 구분이 안 된다."""

    본문_인용 = {
        citation
        for section in 보고서.sections
        for _text, citation in section.prose_lines
        if citation
    }
    뉴스_번호 = {
        fragment_id
        for rows in _NEWS_BY_SECTION.values()
        for fragment_id, _published_on, _text in rows
    }

    assert not (본문_인용 & 뉴스_번호)
    본문 = " ".join(
        text for section in 보고서.sections for text, _cite in section.prose_lines
    )
    for _rows in _NEWS_BY_SECTION.values():
        for _fragment_id, _published_on, text in _rows:
            assert text not in 본문


def test_행은_최신순으로_상한까지만_실린다(보고서) -> None:
    table = _news_table(보고서, "identity")

    assert table.caption == news_block_caption(NEWS_BLOCK_MAX_ROWS)
    assert [row[0] for row in table.rows] == ["2026-09-02", "2026-08-11", "2026-07-05"]
    assert {row[1] for row in table.rows} == {_PUBLISHER}


def test_보도_문장은_조각_원문_그대로다(보고서) -> None:
    글자_by_id = {
        fragment_id: text
        for rows in _NEWS_BY_SECTION.values()
        for fragment_id, _published_on, text in rows
    }
    실린_글자 = {
        row[2]
        for section_id in _NEWS_SECTIONS
        for row in _news_table(보고서, section_id).rows
    }

    assert 실린_글자 <= set(글자_by_id.values())
    # 5장은 인용문 조각만 온다 — 따옴표까지 그대로여야 한다.
    인용문 = _news_table(보고서, "current_challenges").rows[0][2]
    assert 인용문 == 글자_by_id["46"]
    assert '"인력을 더 늘려야 하는 것이 지금의 과제"' in 인용문


def test_보도표는_그_장의_맨_끝에_온다(보고서) -> None:
    """2장은 흐름표를 먼저 그리고 보도표를 뒤에 붙인다."""

    section = next(
        section for section in 보고서.sections if section.cell == "business_model"
    )

    assert len(section.tables) >= 2
    assert section.tables[-1].headers == list(NEWS_BLOCK_HEADERS)
    assert section.tables[0].headers != list(NEWS_BLOCK_HEADERS)


def test_9장에는_보도표가_없다(보고서) -> None:
    assert _news_table(보고서, "competitive_position") is None


def test_실행_기록용_수치가_결과에_실린다(FULL_실행결과) -> None:
    assert dict(FULL_실행결과.news_block_row_counts_by_section) == {
        "identity": NEWS_BLOCK_MAX_ROWS,
        "business_model": 1,
        "current_challenges": 1,
    }


# ══════════════════════════════════════════════════════════
# ② 부록 — 표에 찍힌 번호가 부록에도 있다 (인용-부록 1:1)
# ══════════════════════════════════════════════════════════


def test_표에_실린_조각이_부록에_기사로_오른다(보고서) -> None:
    실린_id = {
        str(number)
        for section_id in _NEWS_SECTIONS
        for number in re.findall(
            r"\[(\d+)\]", " ".join(_news_table(보고서, section_id).source_cites)
        )
    }
    부록 = {str(source.number): source for source in 보고서.citations}

    assert 실린_id, "표에 출처 번호가 하나도 없습니다"
    assert 실린_id <= set(부록)
    for number in sorted(실린_id, key=int):
        source = 부록[number]
        assert source.source_type == "언론 보도", source
        assert source.publisher == _PUBLISHER
        assert source.location.startswith("기사 본문 · news-fragment-")


def test_부록의_사용_장이_그_장을_가리킨다(보고서) -> None:
    부록 = {str(source.number): source for source in 보고서.citations}
    for section_id in _NEWS_SECTIONS:
        numbers = re.findall(
            r"\[(\d+)\]", " ".join(_news_table(보고서, section_id).source_cites)
        )
        for number in numbers:
            assert section_id in 부록[number].used_in, (section_id, number)


# ══════════════════════════════════════════════════════════
# ③ FULL 봉인 — pre-render manifest가 이 표를 읽는다
# ══════════════════════════════════════════════════════════


def test_봉인과_투영이_보도표를_그대로_통과시킨다(보고서) -> None:
    assert 보고서.public_structure_manifest
    assert 보고서.public_projection is not None
    table = _news_table(보고서, "identity")
    # manifest 참조가 비면 저장·재로드가 fail-closed 된다.
    assert table.manifest_ref
    assert len(table.row_binding_refs) == len(table.rows)
    assert len(table.cell_binding_refs) == len(table.rows)


def test_저장_재로드_뒤에도_보도표_결속이_남는다(보고서) -> None:
    from src.features.composer.public_manifest import assert_stored_strict_manifest
    from src.features.storage.reports import report_from_json, report_to_json

    다시 = report_from_json(report_to_json(보고서))
    assert_stored_strict_manifest(다시)

    assert _news_table(다시, "identity").rows == _news_table(보고서, "identity").rows


# ══════════════════════════════════════════════════════════
# ④ 화면(result.html)
# ══════════════════════════════════════════════════════════


def test_화면이_보도표를_일반_표로_그린다(보고서) -> None:
    from fastapi.testclient import TestClient

    from src.features.auth import constants as auth_constants
    from src.features.auth import logic as auth_logic
    from src.web import job_runtime
    from src.web.main import app
    from src.web.routers import reports as reports_router
    from src.web.tests.report_route_support import serve_legacy_report_snapshot

    job_id = f"n13-news-block-{uuid.uuid4().hex}"
    job_runtime._JOBS.pop(job_id, None)  # noqa: SLF001

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "0")
        mp.setenv(auth_constants.ENV_ADMIN_EMAILS, "admin@example.com")
        job_runtime._start_job_runtime()  # noqa: SLF001
        serve_legacy_report_snapshot(mp, 보고서, report_id=job_id)
        mp.setattr(job_runtime, "_link_expired", lambda _report: False)
        mp.setattr(
            reports_router, "_release_state", lambda **_kwargs: (object(), None)
        )
        mp.setattr(reports_router, "is_notion_configured", lambda: True)
        session = auth_logic.create_session("admin@example.com", True)
        with TestClient(app) as client:
            response = client.get(
                f"/result/{job_id}",
                cookies={auth_constants.SESSION_COOKIE_NAME: session.token},
            )

    assert response.status_code == 200, response.text[:400]
    body = response.text
    assert news_block_caption(NEWS_BLOCK_MAX_ROWS) in body
    # ★ 도식이 아니라 «표»로 떨어졌는가 — 머리글 셀 태그까지 본다.
    #   글자만 찾으면 본문 산문에 우연히 같은 낱말이 있어도 통과한다.
    머리글줄 = "".join(f'<th scope="col">{header}</th>' for header in NEWS_BLOCK_HEADERS)
    assert 머리글줄 in body, 머리글줄
    # 숫자 표가 아니라 글자 표여야 첫 칸이 한 글자 폭으로 찌그러지지 않는다.
    assert 'class="texts"' in body
    for _fragment_id, published_on, text in _NEWS_BY_SECTION["identity"][:3]:
        assert f"<td>{published_on}</td>" in body
        assert f"<td>{text}</td>" in body
    # 상한을 넘겨 뺀 행은 화면에도 없어야 한다.
    빠진_행 = _NEWS_BY_SECTION["identity"][3]
    assert 빠진_행[2] not in body


# ══════════════════════════════════════════════════════════
# ⑤ PDF — 실제 렌더 바이트와 그림 한 장
# ══════════════════════════════════════════════════════════


def test_PDF가_보도표를_실제로_그린다(보고서) -> None:
    import pypdfium2 as pdfium

    from src.features.export_pdf import release as pdf_release

    candidate = pdf_release.prepare_pdf_release(보고서)
    pdf_bytes = candidate.pdf_bytes
    assert pdf_bytes.startswith(b"%PDF-")

    text = "".join(
        "".join((page.extract_text() or "").splitlines())
        for page in PdfReader(io.BytesIO(pdf_bytes)).pages
    )

    def _loose(value: str) -> str:
        # CJK는 글자 사이에 공백이 낄 수 있어 느슨하게 찾는다.
        return r"\s*".join(map(re.escape, value))

    assert re.search(_loose("최근 보도 (보조"), text)
    assert re.search(_loose(_PUBLISHER), text)
    첫_문장 = _NEWS_BY_SECTION["identity"][1][2]
    assert re.search(_loose(첫_문장), text), 첫_문장

    document = pdfium.PdfDocument(pdf_bytes)
    try:
        page_index = next(
            index
            for index in range(len(document))
            if re.search(
                _loose(_PUBLISHER),
                "".join(
                    (
                        PdfReader(io.BytesIO(pdf_bytes)).pages[index].extract_text()
                        or ""
                    ).splitlines()
                ),
            )
        )
        _ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        document[page_index].render(scale=2.0).to_pil().save(
            _ARTIFACT_DIR / "news_block.png"
        )
    finally:
        document.close()


# ══════════════════════════════════════════════════════════
# ⑥ 노션
# ══════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════
# ⑤-b 봉인이 없는 실행 모드 — 보고서를 통째로 막지 않는다
# ══════════════════════════════════════════════════════════
#
# ★ 왜 FULL만으로 안 되나 (실측 재현 2건) —
#   · SHADOW: 봉인이 없어 표의 `source_cites`를 아무도 채워 주지 않으면,
#     2·3행이 부록에만 있고 본문 어디에도 없는 번호가 되어 출고 검증이
#     「부록에 있는 번호를 본문 어디에서도 인용하지 않았습니다: [42], [43]」로
#     보고서 «전체»를 막았다.
#   · ENFORCE_NO_PARTIAL: pre-render manifest 자체가 없어 표를 결속하지 못하고,
#     엄격 품질 계약이 그 장을 「fact_id와 결속되지 않은 공개 내용」으로 막았다.
#     그래서 그 모드에서는 아예 안 붙이고 사유만 남긴다.


def _run_mode(release_mode, *, flow: bool):
    from src.features.composer.pipeline import run_v2
    from src.features.composer.tests.test_section_public_manifest import (
        _BoundGroupedReviewer,
        _CompletePacketWriter,
        _NoDiagram,
    )

    with pytest.MonkeyPatch.context() as mp:
        news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001
        mp.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, "1")
        output = run_v2(
            "가나다전자",
            (),
            None,
            writer_ask=_CompletePacketWriter(flow=flow),
            reviewer_ask=_BoundGroupedReviewer(),
            diagram_ask=_NoDiagram(),
            release_mode=release_mode,
            section_evidence_packets=_packets_with_news(),
            company_id="00123456",
            build_identity_sha256="b" * 64,
        )
    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001
    return output


def test_봉인_없는_SHADOW에서도_보도표가_출고검증을_통과한다() -> None:
    from src.shared.report_evidence.constants import ReleaseMode

    output = _run_mode(ReleaseMode.SHADOW, flow=True)

    table = _news_table(output.report, "identity")
    assert table is not None
    부록 = {source.number for source in output.report.citations}
    실린 = {
        int(number)
        for number in re.findall(r"\[(\d+)\]", " ".join(table.source_cites))
    }
    assert len(실린) == NEWS_BLOCK_MAX_ROWS, table.source_cites
    # 표가 가리킨 번호가 부록에 있고, 그 번호를 표가 «본문처럼» 들고 있어야
    # 출고 검증의 인용-부록 1:1이 성립한다.
    assert 실린 <= 부록


def test_결속_못하는_모드에서는_표를_안_붙이고_사유만_남긴다() -> None:
    from src.shared.report_evidence.constants import ReleaseMode

    output = _run_mode(ReleaseMode.ENFORCE_NO_PARTIAL, flow=False)

    assert all(
        _news_table(output.report, section_id) is None
        for section_id in _NEWS_SECTIONS
    )
    assert output.news_block_row_counts_by_section == ()
    사유 = dict(output.news_block_blocked_counts_by_reason)
    assert list(사유) == ["release_mode_cannot_bind_structures"], 사유


# ══════════════════════════════════════════════════════════
# ⑤-c 3장에 이름 표와 보도표가 «함께» 실린다
# ══════════════════════════════════════════════════════════
#
# ★ 왜 필요한가 — 두 표는 서로 다른 사람이 서로 다른 시점에 만들었고, 3장은
#   둘 다 붙을 수 있는 유일한 장이다. 각자 자기 시험만 있으면 「같은 장에
#   둘이 함께 있을 때」의 순서·index를 아무도 안 지킨다. 순서가 renderer와
#   봉인에서 어긋나면 그 회사 보고서가 통째로 막힌다.


_NEWS_IN_PORTFOLIO = (
    ("51", "2026-08-15", "가나다전자는 물류 제품군을 현장에 적용한다."),
    ("52", "2026-07-01", "가나다전자는 상담 제품군을 함께 공급한다."),
)


def _packets_with_names_and_news():
    """이름 조각(3장)과 뉴스 조각(3장)을 한 packet에 함께 넣는다."""

    from src.features.composer.constants import PORTFOLIO_TABLE_SECTION_ID
    from src.features.composer.tests.test_portfolio_name_table_channels import (
        _full_packets_with_names,
    )

    base = _full_packets_with_names()
    rebuilt = []
    for packet in base.packets:
        if packet.section_id != PORTFOLIO_TABLE_SECTION_ID:
            rebuilt.append(packet)
            continue
        rebuilt.append(
            replace(
                packet,
                fragments=packet.fragments
                + tuple(
                    _news_fragment(fragment_id, published_on, text)
                    for fragment_id, published_on, text in _NEWS_IN_PORTFOLIO
                ),
            )
        )
    return replace(base, packets=tuple(rebuilt))


@pytest.fixture(scope="module")
def 이름표와_보도표_실행결과():
    from src.features.composer.tests.test_section_public_manifest import _run_full

    with pytest.MonkeyPatch.context() as mp:
        news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001
        mp.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, "1")
        output, _writer, _reviewer, _diagram = _run_full(
            packets=_packets_with_names_and_news()
        )
    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001
    return output


def test_3장에_이름_표와_보도표가_함께_실린다(이름표와_보도표_실행결과) -> None:
    from src.features.composer.constants import PORTFOLIO_TABLE_SECTION_ID
    from src.features.composer.portfolio_name_table import (
        NAME_TABLE_CAPTION_PREFIX,
        NAME_TABLE_HEADERS,
    )

    output = 이름표와_보도표_실행결과
    # 두 기능이 «둘 다 실제로 돌았는가»부터 확인한다 — 하나가 0이면 이 시험은
    # 나머지 하나의 시험을 한 번 더 도는 것일 뿐이다.
    assert output.portfolio_name_table_name_count == 3, (
        output.portfolio_name_table_blocked_reason
    )
    assert dict(output.news_block_row_counts_by_section) == {
        PORTFOLIO_TABLE_SECTION_ID: len(_NEWS_IN_PORTFOLIO)
    }

    section = next(
        section
        for section in output.report.sections
        if section.cell == PORTFOLIO_TABLE_SECTION_ID
    )
    캡션들 = [table.caption for table in section.tables]
    assert len(section.tables) == 2, 캡션들
    # 순서: 이름 표(회사 공식 자료) → 보도표(보조). 작가 카드가 있으면 그
    # 카드가 맨 앞에 오고 이 둘의 앞뒤 순서는 그대로다.
    assert section.tables[0].headers == list(NAME_TABLE_HEADERS)
    assert 캡션들[0].startswith(NAME_TABLE_CAPTION_PREFIX)
    assert section.tables[1].headers == list(NEWS_BLOCK_HEADERS)
    assert 캡션들[1] == news_block_caption(len(_NEWS_IN_PORTFOLIO))


def test_두_표가_같은_장에_있어도_봉인이_통과한다(이름표와_보도표_실행결과) -> None:
    """표 index가 renderer와 봉인에서 어긋나면 보고서가 통째로 막힌다."""

    from src.features.composer.constants import PORTFOLIO_TABLE_SECTION_ID
    from src.features.composer.public_manifest import assert_stored_strict_manifest
    from src.features.storage.reports import report_from_json, report_to_json

    report = 이름표와_보도표_실행결과.report
    assert report.public_structure_manifest
    section = next(
        section
        for section in report.sections
        if section.cell == PORTFOLIO_TABLE_SECTION_ID
    )
    # 두 표 모두 자기 manifest 항목을 가리켜야 한다(비면 재로드가 막힌다).
    assert all(table.manifest_ref for table in section.tables)
    assert_stored_strict_manifest(report_from_json(report_to_json(report)))


def test_두_표의_인용이_모두_부록에_있다(이름표와_보도표_실행결과) -> None:
    from src.features.composer.constants import PORTFOLIO_TABLE_SECTION_ID

    report = 이름표와_보도표_실행결과.report
    section = next(
        section
        for section in report.sections
        if section.cell == PORTFOLIO_TABLE_SECTION_ID
    )
    부록 = {source.number for source in report.citations}
    for table in section.tables:
        번호 = {
            int(value)
            for value in re.findall(r"\[(\d+)\]", " ".join(table.source_cites))
        }
        assert 번호, table.caption
        assert 번호 <= 부록, (table.caption, sorted(번호))
    # 보도표 쪽 번호가 이름 표 쪽과 섞이지 않았는지도 본다.
    보도표_번호 = {
        int(value)
        for value in re.findall(
            r"\[(\d+)\]", " ".join(section.tables[1].source_cites)
        )
    }
    assert 보도표_번호 == {
        int(fragment_id) for fragment_id, _date, _text in _NEWS_IN_PORTFOLIO
    }


class _ThinThenFullWriter:
    """1장만 첫 회차에 얇게 쓰고, 승인받은 재호출에서 채우는 가짜 작가.

    ★ 옆 파일의 `_RecoveringPacketWriter`는 ``assert len(fragment_ids) == 1``로
      «packet에 조각이 딱 하나»를 요구한다. 뉴스 조각을 넣은 packet은 조각이
      여럿이라 그 단정에서 죽는다. 그래서 같은 모양의 도구를 여기 둔다.
    """

    _THIN_SECTION = "identity"

    def __init__(self) -> None:
        from src.features.composer.constants import GRADE_CONFIRMED
        from src.features.composer.tests.test_section_public_manifest import (
            _ENDINGS,
            _MARKS,
        )

        self._grade = GRADE_CONFIRMED
        self._endings = _ENDINGS
        self._marks = _MARKS
        self.section_calls: dict[str, int] = {}

    def __call__(self, prompt: str) -> str:
        import json

        fragment_ids = re.findall(r"\[조각 (\d+)\] \(", prompt)
        assert fragment_ids
        first = int(fragment_ids[0])
        section_id = SECTION_IDS[first - 1]
        mark = self._marks[first - 1]
        section_call = self.section_calls.get(section_id, 0) + 1
        self.section_calls[section_id] = section_call
        slots = CLAIM_SLOTS_BY_SECTION[section_id]
        thin = section_id == self._THIN_SECTION and section_call == 1
        endings = (self._endings[0],) if thin else self._endings
        return json.dumps(
            {
                "문장들": [
                    {
                        "글": (
                            f"{mark} 회사 사업 고객 제품 전략 운영 문화 경쟁 과제 "
                            f"대응 협력 실적 {ending} 공식 자료에서 확인했다."
                        ),
                        "인용": [fragment_ids[0]],
                        "등급": self._grade,
                        "주장슬롯": slots[index % len(slots)],
                    }
                    for index, ending in enumerate(endings)
                ]
            },
            ensure_ascii=False,
        )


def test_보충_회차가_그_장을_다시_써도_보도표가_남는다() -> None:
    """보충 대상 장은 통째로 갈린다 — 그때 표가 사라지면 안 된다.

    ★ 이 시험이 없으면 보충이 도는 회사에서만 보도표가 조용히 없어진다.
      첫 후보에서는 붙었으니 어떤 시험도 안 깨지고, 운영에서만 안 보인다.
    """

    from src.features.composer.pipeline import run_v2
    from src.features.composer.tests.test_section_public_manifest import (
        _BoundGroupedReviewer,
        _NoDiagram,
    )
    from src.shared.report_evidence.constants import ReleaseMode

    writer = _ThinThenFullWriter()
    with pytest.MonkeyPatch.context() as mp:
        news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001
        mp.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, "1")
        output = run_v2(
            "가나다전자",
            (),
            None,
            writer_ask=writer,
            reviewer_ask=_BoundGroupedReviewer(),
            diagram_ask=_NoDiagram(),
            release_mode=ReleaseMode.FULL,
            section_evidence_packets=_packets_with_news(),
            company_id="00123456",
            build_identity_sha256="b" * 64,
        )
    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001

    # ★ 보충 회차가 «실제로 돌았는가»부터 확인한다 — 안 돌았으면 이 시험은
    #   기본 경로를 한 번 더 도는 것일 뿐 아무것도 안 지킨다.
    assert writer.section_calls["identity"] == 2, writer.section_calls
    table = _news_table(output.report, "identity")
    assert table is not None, "보충 뒤 보도표가 사라졌습니다"
    assert [row[0] for row in table.rows] == [
        "2026-09-02",
        "2026-08-11",
        "2026-07-05",
    ]


def test_노션_블록에_보도_행이_있다(보고서) -> None:
    from src.features.export_notion.logic import build_blocks

    rows = [
        [
            "".join(part.get("text", {}).get("content", "") for part in cell)
            for cell in block["table_row"]["cells"]
        ]
        for table in build_blocks(보고서)
        if table.get("type") == "table"
        for block in table["table"]["children"]
        if block.get("type") == "table_row"
    ]

    assert list(NEWS_BLOCK_HEADERS) in rows, rows[:5]
    최신 = _NEWS_BY_SECTION["identity"][1]
    assert [최신[1], _PUBLISHER, 최신[2]] in rows, rows[:8]
