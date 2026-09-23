"""4차 도식 — 새 «4칸 원표 / 3칸 표시»가 실제 저장 왕복과 웹·PDF 소비까지 이어진다.

★ 왜 이 파일이 있나
  원행 4칸 보존·표시 3칸 축소·구형 「미확인」 불변은 각각 따로 시험돼 있다
  (visualization·flow_unconfirmed_cell·public_projection_builder·
  public_projection_storage). 그런데 «끝 칸이 빈 4칸 원표»가 실제 FULL
  파이프라인으로 봉인된 뒤 DB에 저장되고, 다시 읽혀 digest를 통과하고, 웹·PDF가
  그 저장된 봉인을 소비하는 «한 줄로 이어진 양성»은 없었다. 이 파일이 그것만 본다.

★ 재료는 위조하지 않는다
  기존 정상 fake 공급자(``_CompletePacketWriter``·``_BoundGroupedReviewer``·
  ``_NoDiagram``)와 실제 ``run_v2``를 그대로 쓴다. 작성기 응답에서 2장 경로표의
  «끝 칸 글자»만 바꾼다 — 검수 결속·영수증·manifest·봉인·생성 증거는 전부 생산
  경로가 만든다. 해시나 승인을 손으로 만들지 않는다.
  ②(구형 「미확인」 4칸 모양)만은 지금 생산 경로가 더는 만들지 않으므로 기존
  봉인 채널 시험의 검수 fixture 경로를 쓴다 — 이유는 ``_legacy_shape_report``.

★ 저장은 pytest 임시 폴더의 SQLite만 쓴다. 유료 호출·네트워크·운영 DB 없음.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path

import pdfplumber
import pytest

from src.features.composer.constants import (
    BUSINESS_FLOW_SECTION_ID,
    FLOW_HEADERS_BY_SECTION,
    FLOW_UNCONFIRMED_CELL,
    SECTION_IDS,
)
from src.features.auth import constants as auth_constants
from src.features.composer.pipeline import run_v2
from src.features.composer.port import FlowRow
from src.features.composer.render import render_report
from src.features.composer.tests.flow_fixtures import reviewed_flow_fixture
from src.features.composer.tests.test_section_public_manifest import (
    _BoundGroupedReviewer,
    _CompletePacketWriter,
    _NoDiagram,
    _packets,
)
from src.features.composer.validate import v2_validation_problems
from src.features.export_pdf import logic as pdf_logic
from src.features.export_pdf.automatic_release import report_sha256
from src.features.export_pdf.tests.test_pdf_visible_equals_sealed import (
    _composed_with_two_paragraphs,
)
from src.features.export_pdf.tests.test_v2_public_projection import (
    _PRIVATE_SCOPE,
    _composition_table,
    _fragments,
    _performance_table,
    _sealed,
    _with_facts,
)
from src.features.pipeline.port import Report
from src.features.report_standard.public_projection import build_public_projection
from src.features.storage import db, reports
from src.features.storage.constants import (
    TABLE_REPORT_PUBLIC_PROJECTIONS,
    TABLE_REPORTS,
)
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_generation.public_projection import (
    build_report_digest,
    public_report_projection_from_dict,
)
from src.web import request_helpers
from src.web.tests.test_three_channels_share_sealed_blocks import (
    _render_from_stored_delivery,
)

_CORP_ID = "00123456"

#: 2장(사업 흐름) 머리말 — 원표 폭(4칸)의 기준. 리터럴로 적어 상수가
#: 바뀌면 이 시험이 먼저 깨지게 한다.
_BUSINESS_HEADERS = ("핵심 자산", "제품·서비스", "고객 행동·과금", "반복·확장 수익")

#: 기존 정상 fake 작성기가 2장에 내는 두 행의 «앞 세 칸».
_FRONT_ROWS = (
    ("핵심 자산", "핵심 제품", "기업 고객 과금"),
    ("보조 자산", "보조 제품", "소비자 과금"),
)


class _TailCellWriter(_CompletePacketWriter):
    """정상 fake 작성기의 2장 경로표 «끝 칸»만 지정한 글자로 바꾼다.

    ★ 4차 실측 모양 그대로다 — 작가가 앞 세 칸만 채우고 끝 칸을 비운 행.
      나머지 장·문장·인용은 원래 작성기 응답 그대로다.
    """

    def __init__(self, tail: str) -> None:
        super().__init__(flow=True)
        self.tail = tail

    def __call__(self, prompt: str) -> str:
        section_id = SECTION_IDS[self.calls]
        answer = super().__call__(prompt)
        if section_id != BUSINESS_FLOW_SECTION_ID:
            return answer
        payload = json.loads(answer)
        for row in payload["경로표"]:
            row["칸"][-1] = self.tail
        return json.dumps(payload, ensure_ascii=False)


def _full_report(tail: str) -> Report:
    """실제 FULL 파이프라인을 통과한 봉인 보고서 — 2장 끝 칸만 ``tail``."""

    output = run_v2(
        "가나다전자",
        (),
        None,
        writer_ask=_TailCellWriter(tail),
        reviewer_ask=_BoundGroupedReviewer(),
        diagram_ask=_NoDiagram(),
        release_mode=ReleaseMode.FULL,
        section_evidence_packets=_packets(two_flow_sources=True),
        company_id=_CORP_ID,
        build_identity_sha256="b" * 64,
    )
    report = output.report
    assert report.public_projection is not None, "봉인이 붙지 않았다 — 재료가 틀렸다"
    assert report.generation_evidence is not None
    return report


def _business_flow(report: Report):
    """2장 흐름표와 그 표의 봉인 표시 블록·도식 블록을 함께 꺼낸다."""

    section = next(s for s in report.sections if s.cell == BUSINESS_FLOW_SECTION_ID)
    index = next(i for i, t in enumerate(section.tables) if t.presentation == "flow")
    block = next(
        b for b in report.public_projection.sections
        if b.display.cell == BUSINESS_FLOW_SECTION_ID
    )
    visual = next(v for v in block.display.visuals if v.table_index == index)
    return section.tables[index], block.display.tables[index], visual


def _stored_and_reloaded(tmp_path: Path, report: Report, report_id: str):
    """임시 DB에 저장하고 «두 입구»로 다시 읽는다.

    ① ``reports.load`` — 저장소 기본 입구.
    ② payload 문자열 → ``attach_public_projection`` — 공개 결과 화면·승인
       snapshot이 실제로 쓰는 입구(``report_delivery_adapter``).
    """

    path = tmp_path / f"{report_id}.sqlite3"
    with db.connect(path) as conn:
        reports.save(conn, report_id, _CORP_ID, "분석", report)
    with db.connect(path) as conn:
        stored = conn.execute(
            f"""SELECT projection_json, content_sha256, display_sha256
            FROM {TABLE_REPORT_PUBLIC_PROJECTIONS} WHERE report_id = ?""",
            (report_id,),
        ).fetchone()
        payload = conn.execute(
            f"SELECT payload_json FROM {TABLE_REPORTS} WHERE report_id = ?",
            (report_id,),
        ).fetchone()[0]
        loaded = reports.load(conn, report_id)
        attached = reports.attach_public_projection(
            conn, report_id, reports.report_from_json(payload)
        )
    return stored, loaded, attached


class _FlowRowCollector(HTMLParser):
    """화면의 ``ol.flow-row``를 (머리말, 값, 미확인 표식) 줄로 순서대로 모은다."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[tuple[str, str, bool]]] = []
        self._in_row = False
        self._cell: list[str] | None = None
        self._part = ""
        self._head: list[str] = []
        self._value: list[str] = []
        self._unconfirmed = False

    def handle_starttag(self, tag, attrs) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "ol" and "flow-row" in classes:
            self._in_row = True
            self.rows.append([])
        elif self._in_row and tag == "li":
            self._head, self._value = [], []
            self._unconfirmed = attributes.get("data-flow-cell") == "unconfirmed"
            self._cell = []
        elif self._cell is not None and tag in ("small", "span"):
            self._part = tag

    def handle_endtag(self, tag) -> None:
        if tag in ("small", "span"):
            self._part = ""
        elif tag == "li" and self._cell is not None:
            self.rows[-1].append(
                ("".join(self._head).strip(), "".join(self._value).strip(), self._unconfirmed)
            )
            self._cell = None
        elif tag == "ol" and self._in_row:
            self._in_row = False

    def handle_data(self, data: str) -> None:
        if self._part == "small":
            self._head.append(data)
        elif self._part == "span":
            self._value.append(data)


def _business_section_html(body: str) -> str:
    """결과 화면에서 2장 ``section`` 하나만 잘라 낸다."""

    start = body.index(f'data-report-cell="{BUSINESS_FLOW_SECTION_ID}"')
    end = body.find('class="report-section"', start + 1)
    return body[start:] if end < 0 else body[start:end]


def _screen_flow_rows(body: str) -> list[list[tuple[str, str, bool]]]:
    parser = _FlowRowCollector()
    parser.feed(_business_section_html(body))
    return parser.rows


def _render_as_admin(
    report: Report, monkeypatch: pytest.MonkeyPatch, *, report_id: str
) -> str:
    """저장본 결과 화면을 관리자 로그인으로 그린다.

    ★ ``web/tests/conftest.py``의 웹 준비는 web 폴더 시험에만 걸린다. 여기서는
      베타 차단을 «끄지 않고» 켠 채, 헬퍼가 로그인하는 ``admin@example.com``을
      관리자 목록에 올리는 것만 한다 — 초대 화면 대신 실제 결과 화면이 나온다.
    """

    monkeypatch.setenv(auth_constants.ENV_ADMIN_EMAILS, "admin@example.com")
    return _render_from_stored_delivery(report, monkeypatch, report_id=report_id)


def _forbid_recompute(monkeypatch: pytest.MonkeyPatch) -> None:
    """웹·PDF가 원표에서 도식을 «다시 계산하면» 터지게 한다.

    봉인 갈래는 저장된 도식 블록만 옮겨야 한다. 여기서 터지면 채널이 봉인이
    아니라 새 코드의 재계산 결과를 그린 것이다.
    """

    def boom(*_args, **_kwargs):
        raise AssertionError("봉인 갈래가 table_visualization을 다시 불렀다")

    monkeypatch.setattr(request_helpers, "table_visualization", boom)
    monkeypatch.setattr(pdf_logic, "table_visualization", boom)


def _record_pdf_flow_graphics(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """PDF가 실제로 그린 흐름 도식의 (칸들, 머리말) 입력을 받아 적는다."""

    drawn: list[tuple] = []
    original = pdf_logic._FlowGraphic

    class _Recording(original):  # type: ignore[misc, valid-type]
        def __init__(self, visual, headers, width) -> None:
            drawn.append((visual.flows, tuple(headers)))
            super().__init__(visual, headers, width)

    monkeypatch.setattr(pdf_logic, "_FlowGraphic", _Recording)
    return drawn


def _pdf_text(pdf: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf)) as document:
        pages = [page.extract_text() or "" for page in document.pages]
    return re.sub(r"\s+", "", "\n".join(pages))


# ══════════════════════════════════════════════════════════
# ① 새 4칸 원표(끝 빈 칸) → 3칸 표시 → 저장 → 재로드 → 웹·PDF
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "writer_tail",
    (
        "",
        # 작가가 끝 칸에 자리표시 「미확인」을 직접 쓴 경우 — 생산 일반어 가드
        # (diagram_review_constants의 일반어 집합)가 빈 칸으로 비운다. 새
        # 「미확인」 노드가 검수 없이 봉인되지 않는지 같은 사슬로 본다.
        FLOW_UNCONFIRMED_CELL,
    ),
    ids=("blank-tail", "writer-unconfirmed-tail"),
)
def test_끝_빈칸_4칸_원표가_3칸_도식으로_봉인되어_저장_왕복과_웹_PDF까지_이어진다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer_tail: str
) -> None:
    report = _full_report(tail=writer_tail)
    assert v2_validation_problems(report) == ()
    assert tuple(FLOW_HEADERS_BY_SECTION[BUSINESS_FLOW_SECTION_ID]) == _BUSINESS_HEADERS

    # ── 생성 직후: 원표는 4칸 그대로, 영수증도 4칸 폭 그대로 ──
    table, sealed_table, visual = _business_flow(report)
    expected_rows = [[*front, ""] for front in _FRONT_ROWS]
    assert tuple(table.headers) == _BUSINESS_HEADERS
    assert table.rows == expected_rows
    assert all(FLOW_UNCONFIRMED_CELL not in row for row in table.rows)
    assert len(table.row_binding_refs) == len(expected_rows)
    assert [len(refs) for refs in table.cell_binding_refs] == [4, 4]
    assert re.fullmatch(r"[0-9a-f]{64}", table.manifest_ref)
    # 공개 구조 manifest(지문 B)도 원행 4칸 그대로다 — 「미확인」 채움 없음.
    manifest_rows = [
        item["rows"]
        for item in json.loads(report.public_structure_manifest)["tables"]
        if item["section_id"] == BUSINESS_FLOW_SECTION_ID and item["presentation"] == "flow"
    ]
    assert manifest_rows == [expected_rows]

    # ── 봉인: 표 블록은 4칸, 도식 블록은 원래 앞 3칸 ──
    assert sealed_table.headers == _BUSINESS_HEADERS
    assert sealed_table.rows == tuple(tuple(row) for row in expected_rows)
    assert visual.kind == "flow"
    assert visual.flows == _FRONT_ROWS
    assert visual.cards == ()
    assert len(visual.row_cites) == len(_FRONT_ROWS)
    # 읽는 법은 실제 마지막 표시 칸 기준 — 잘린 넷째 머리말 뜻을 붙이지 않는다.
    assert "반복·확장 수익" not in visual.reading

    # ── 저장 → 재로드 → digest ──
    stored, loaded, attached = _stored_and_reloaded(tmp_path, report, "fourth-flow")
    digest = build_report_digest(report.public_projection)
    assert stored["content_sha256"] == digest.content_sha256
    assert stored["display_sha256"] == digest.display_sha256
    assert json.loads(stored["projection_json"]) == reports.public_projection_payload(
        report.public_projection
    )
    assert report.generation_evidence.public_projection_sha256 == digest.content_sha256
    for restored in (loaded, attached):
        assert restored is not None
        assert restored.public_projection == report.public_projection
        assert build_report_digest(restored.public_projection) == digest
        assert reports.report_to_json(restored) == reports.report_to_json(report)
        assert report_sha256(restored) == report_sha256(report)
        assert v2_validation_problems(restored) == ()
        r_table, r_sealed, r_visual = _business_flow(restored)
        assert r_table == replace(table, evidence_rows=[])
        assert r_sealed == sealed_table
        assert r_visual == visual

    # ── 웹·PDF: 저장본에서 다시 붙인 봉인만 소비한다(재계산 금지) ──
    _forbid_recompute(monkeypatch)
    drawn = _record_pdf_flow_graphics(monkeypatch)

    body = _render_as_admin(attached, monkeypatch, report_id="fourth-flow")
    screen = _screen_flow_rows(body)
    assert screen == [
        [(header, value, False) for header, value in zip(_BUSINESS_HEADERS, front)]
        for front in _FRONT_ROWS
    ], "화면 흐름이 봉인된 앞 3칸·원래 앞 3 머리말과 다르다"
    business_html = _business_section_html(body)
    assert 'data-flow-cell="unconfirmed"' not in business_html
    assert FLOW_UNCONFIRMED_CELL not in business_html

    pdf = pdf_logic.build_pdf(attached)
    pdf_path = tmp_path / "fourth-flow-roundtrip.pdf"
    pdf_path.write_bytes(pdf)
    # 첫 표는 소개 문단과 묶을 수 있는지 재 보느라 도식 객체를 한 번 더 만들 수
    # 있다(``_bounded_intro_with_table``). 횟수가 아니라 «만든 것 전부»가 봉인된
    # 앞 3칸 + 원래 머리말 4개(앞 3개만 찍힌다)인지 본다.
    business_graphics = {item for item in drawn if item[0][0][0] == _FRONT_ROWS[0][0]}
    assert business_graphics == {(_FRONT_ROWS, _BUSINESS_HEADERS)}, drawn
    for flows, _headers in drawn:
        assert all(value and value != FLOW_UNCONFIRMED_CELL for row in flows for value in row)
    printed = _pdf_text(pdf)
    for front in _FRONT_ROWS:
        for value in front:
            assert re.sub(r"\s+", "", value) in printed
    # 넷째 노드가 그려졌다면 그 머리말이 흐름 위에 찍힌다.
    assert "반복·확장수익" not in printed


# ══════════════════════════════════════════════════════════
# ② 구형 모양 — 검수된 실제 「미확인」 4칸 봉인은 재계산·절단 없이 소비된다
# ══════════════════════════════════════════════════════════


#: 구형 봉인 모양의 2장 원행 — 넷째 칸이 검수된 실제 「미확인」 글자.
_LEGACY_ROW = ("음악 자산", "음반", "구독", FLOW_UNCONFIRMED_CELL)


def _legacy_shape_report() -> Report:
    """기존 봉인 채널 시험 재료에서 2장 원행만 구형 「미확인」 4칸으로 바꾼다.

    ★ 왜 FULL 파이프라인이 아닌가 — 지금 생산 경로는 작가의 「미확인」을
      일반어 가드로 비우므로(①의 writer-unconfirmed-tail) 이 모양을 더는 만들지
      않는다. 옛 render가 채워 저장한 진짜 구형 FULL 저장본을 다시 만들려면
      생성 증거·해시를 손으로 지어야 해서 금지다. 그래서 기존 채널 시험이 쓰는
      검수 fixture(``reviewed_flow_fixture``)·``_sealed``·``build_public_projection``
      경로로 «같은 봉인 모양»만 만든다.
    """

    composed = _composed_with_two_paragraphs()
    sections = tuple(
        replace(section, flow_rows=(FlowRow(cells=_LEGACY_ROW, citations=("1",)),))
        if section.section_id == BUSINESS_FLOW_SECTION_ID
        else section
        for section in composed.sections
    )
    rendered = _sealed(
        render_report(
            "가나다전자",
            reviewed_flow_fixture(
                replace(composed, sections=sections), _fragments(),
                baseline_date="2026-09-01",
            ),
            _fragments(),
            _performance_table(),
            table_presentation="trend",
            composition_tables=(_composition_table(),),
            generated_at="2026-09-01",
            as_of_date="2026-09-01",
            analysis_period="2023~2025 완료 회계연도",
            latest_performance_period="2026년 2분기 잠정",
        )
    )
    with_facts = _with_facts(rendered, suffix="1", scope=_PRIVATE_SCOPE)
    return replace(with_facts, public_projection=build_public_projection(with_facts))


def test_구형_미확인_4칸_봉인은_wire_왕복과_웹_PDF에서_재계산_없이_그대로_쓰인다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _legacy_shape_report()
    table, sealed_table, visual = _business_flow(report)
    assert table.rows == [list(_LEGACY_ROW)]
    assert sealed_table.rows == (_LEGACY_ROW,)
    # 검수된 실제 글자는 빈 칸으로 «추정»해 자르지 않는다 — 4칸 그대로 봉인.
    assert visual.kind == "flow"
    assert visual.flows == (_LEGACY_ROW,)

    # ── wire 왕복: 저장 표에 쓰는 JSON 모양 그대로 되살려도 digest 불변 ──
    wire = json.loads(
        json.dumps(reports.public_projection_payload(report.public_projection), ensure_ascii=False)
    )
    revived = public_report_projection_from_dict(wire)
    assert revived == report.public_projection
    assert build_report_digest(revived) == build_report_digest(report.public_projection)

    # ── 채널: 봉인 값만 옮긴다(재계산하면 터진다) ──
    _forbid_recompute(monkeypatch)
    drawn = _record_pdf_flow_graphics(monkeypatch)
    served = replace(report, public_projection=revived)

    body = _render_as_admin(served, monkeypatch, report_id="legacy-shape")
    assert _screen_flow_rows(body) == [
        [
            *((header, value, False) for header, value in zip(_BUSINESS_HEADERS, _LEGACY_ROW[:3])),
            (_BUSINESS_HEADERS[-1], FLOW_UNCONFIRMED_CELL, True),
        ]
    ]

    pdf_logic.build_pdf(served)
    assert ((_LEGACY_ROW,), _BUSINESS_HEADERS) in drawn, drawn
