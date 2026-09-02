"""웹 v2 결과 화면이 «봉인 블록만» 소비하는지 지킨다 (티켓 D-S5 · 설계 §7 S5).

★ 이 파일이 지키는 것
  ① v2 갈래(``report.public_projection`` 있음)는 화면을 그리는 동안 표시 파생
     순수 함수(``table_visualization``·``cover_metrics``·``section_content_blocks``·
     ``source_verification_label``·``period_summary_from_table``)를 **한 번도**
     부르지 않는다. 다섯 함수를 전부 예외로 바꿔도 페이지가 정상으로 나온다.
  ② 부록에 「사실 검증」 열이 PDF와 «같은 라벨»로 나온다 (설계 §01-6 G4 해소).
  ③ 화면이 그린 글자는 저장된 봉인의 ``display_sha256``이 덮는 그 블록에서
     나왔다 — 저장층이 들고 있는 값과 대조한다.
  ④ 감사 장부(``ledger``)만 바꾼 저장본은 화면 HTML이 «바이트 동일»하다.
     장부 원자료(``subject_scope`` 등)는 화면에 한 글자도 나오지 않는다(F-S2p2).
  ⑤ v1·legacy 갈래의 HTML은 base 커밋과 바이트가 같다 — 봉인 도입이 옛 화면을
     한 글자도 바꾸지 않았음을 golden 파일로 못 박는다.

★ 왜 손으로 지은 ``Report``를 쓰지 않나 — ``render_report()``가 「인용 번호를
  언제 숨기는가」·「문장을 어떻게 문단으로 묶는가」를 정하기 때문이다. 그 규칙을
  건너뛴 가짜 보고서로는 봉인과 화면이 같은 값을 쓰는지 확인할 수 없다.
  (``report_standard/tests/test_public_projection_builder.py``와 같은 이유·같은 재료.)

★ 실제 AI·네트워크를 쓰지 않는다. 보고서는 전부 결정론적 재료로 만든다.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.core.citations import split_citation_markers, split_interpretation_marker
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.composer.constants import (
    GRADE_CONFIRMED,
    GRADE_INTERPRETED,
    IDENTITY_TABLE_SECTION_ID,
    SECTION_IDS,
)
from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FlowRow,
    PerformanceTable,
)
from src.features.composer.render import COMPOSITION_TABLE_SECTION_ID, render_report
from src.features.pipeline.canonical_demo import build_demo_report
from src.features.pipeline.port import FactRecord, Report
from src.features.report_standard.public_projection import build_public_projection
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.features.storage.constants import TABLE_REPORT_PUBLIC_PROJECTIONS
from src.shared import engine_build_identity as build_identity_contract
from src.shared.report_generation.canonical import table_public_projection
from src.shared.report_generation.models import canonical_sha256
from src.shared.report_generation.public_projection import build_report_digest
from src.web import job_runtime, request_helpers
from src.web import main as web_main
from src.web.tests.report_route_support import serve_legacy_report_snapshot
from src.web.tests.test_release_authority_full_wiring import (
    _COMPANY_ID,
    _build_full_report,
)


# ══════════════════════════════════════════════════════════
# 재료 — render_report를 실제로 통과한 v2 보고서 + 봉인
# ══════════════════════════════════════════════════════════

_원문_문장용 = "회사는 2024년에 매출 100억원을 기록했으며, 이는 직전 회계연도보다 늘어난 수치다."
_원문_표용 = "회사는 스스로를 글로벌 콘텐츠 기업으로 규정하며 음악·영상 사업을 영위한다."

_확인_문장 = "회사는 음악·영상 사업을 영위한다."
_해석_문장 = "이는 업계 평균을 웃도는 성과로 해석된다."

#: 장부에만 있고 화면에는 «절대» 나오면 안 되는 감사용 원자료(F-S2p2).
_장부_전용_범위 = "내부 감사용 범위 — 공개 화면에 나오면 안 된다"
_장부_전용_관계 = "내부 감사용 관계 — 공개 화면에 나오면 안 된다"


def _fragments() -> dict[int, dict[str, str]]:
    return {
        1: {"종류": "사업내용", "원문": _원문_문장용},
        2: {"종류": "사업내용", "원문": _원문_표용},
    }


def _composed() -> ComposedReport:
    sections = []
    for section_id in SECTION_IDS:
        flow_rows: tuple[FlowRow, ...] = ()
        if section_id == IDENTITY_TABLE_SECTION_ID:
            # 1장 칸 이름은 카드 도식(kind="card")을 부른다.
            flow_rows = (
                FlowRow(cells=("글로벌 콘텐츠 기업", "음악·영상", "해석 없음"), citations=("2",)),
            )
        if section_id == COMPOSITION_TABLE_SECTION_ID:
            # 2장 칸 이름은 화살표 흐름 도식(kind="flow")을 부른다.
            flow_rows = (
                FlowRow(cells=("음악 자산", "음반", "구독", "반복 수익"), citations=("1",)),
            )
        sections.append(
            ComposedSection(
                section_id=section_id,
                sentences=(
                    ComposedSentence(text=_확인_문장, citations=("1",), grade=GRADE_CONFIRMED),
                    ComposedSentence(text=_해석_문장, citations=("1",), grade=GRADE_INTERPRETED),
                ),
                flow_rows=flow_rows,
            )
        )
    return ComposedReport(
        sections=tuple(sections),
        summary=(
            ComposedSentence(text="콘텐츠 기업이다.", citations=("1",), grade=GRADE_CONFIRMED),
            ComposedSentence(text="해외를 넓힌다.", citations=("1",), grade=GRADE_CONFIRMED),
            ComposedSentence(
                text="성장 국면으로 읽힌다.", citations=("1",), grade=GRADE_INTERPRETED
            ),
        ),
    )


def _performance_table() -> PerformanceTable:
    """4장 실적표 — 추이 도식·표지 지표 띠·3개년 변화 띠가 전부 여기서 나온다."""

    return PerformanceTable(
        caption="완료 사업연도 주요 재무",
        headers=("사업연도", "매출액", "영업이익", "당기순이익"),
        rows=(
            ("2025", "5,940", "1,550", "1,200"),
            ("2024", "5,800", "1,400", "-34"),
            ("2023", "5,665", "1,700", "1,834"),
        ),
        unit="억원",
        cite="조각 1·사업내용",
    )


def _composition_table() -> PerformanceTable:
    """2장 구성표 — 100% 구성 도식(kind="composition")을 부른다."""

    return PerformanceTable(
        caption="2025년 부문별 매출 구성",
        headers=("부문", "매출 비중"),
        rows=(("음반·음원", "31.4"), ("매니지먼트", "48.6"), ("기타", "20.0")),
        unit="",
        cite="조각 1·사업내용",
    )


def _fill_manifest_refs(report: Report) -> Report:
    """표에 지문 B가 붙였을 ``manifest_ref``의 «모양»만 채운다.

    ``render_report``는 FULL의 ``public_structure_seal``을 받았을 때만 이 값을
    붙인다. 이 시험은 seal 배선이 아니라 «화면»을 보므로, 표 글자는 한 글자도
    바꾸지 않고 참조 값만 같은 규칙으로 채운다.
    """

    sections = [
        replace(
            section,
            tables=[
                replace(table, manifest_ref=canonical_sha256(table_public_projection(table)))
                for table in section.tables
            ],
        )
        for section in report.sections
    ]
    return replace(report, sections=sections)


def _with_facts(report: Report, *, scope: str, relationship: str) -> Report:
    """장부를 실은 보고서 — 1장에 두 건, 4장에 한 건."""

    facts = [
        FactRecord(
            fact_id=fact_id,
            legal_entity="가나다전자",
            subject_scope=scope,
            relationship_or_action=relationship,
            claim=_확인_문장,
            claim_type="identity_summary",
            section_owner=owner,
        )
        for fact_id, owner in (
            ("fact-identity-1", "identity"),
            ("fact-identity-2", "identity"),
            ("fact-past-1", "past_changes"),
        )
    ]
    owners = {
        "identity": ["fact-identity-1", "fact-identity-2"],
        "past_changes": ["fact-past-1"],
    }
    sections = [
        replace(section, fact_ids=list(owners.get(section.cell, [])))
        for section in report.sections
    ]
    return replace(report, sections=sections, fact_records=facts)


def _sealed_v2_report(
    *,
    scope: str = _장부_전용_범위,
    relationship: str = _장부_전용_관계,
) -> Report:
    """봉인이 붙은 v2 보고서 — 도식 네 종류·3개년 띠·표지 띠·부록이 모두 있다."""

    rendered = render_report(
        "가나다전자",
        _composed(),
        _fragments(),
        _performance_table(),
        table_presentation="trend",
        composition_tables=(_composition_table(),),
        as_of_date="2026-09-01",
        analysis_period="2023~2025 완료 회계연도",
        latest_performance_period="2026년 2분기 잠정",
    )
    report = _with_facts(
        _fill_manifest_refs(rendered), scope=scope, relationship=relationship
    )
    return replace(report, public_projection=build_public_projection(report))


# ══════════════════════════════════════════════════════════
# 화면 — 실제 공개 GET 계약을 그대로 탄다
# ══════════════════════════════════════════════════════════

_ARTICLE_START = '<article class="report-paper">'
_ARTICLE_END = '<aside class="report-support ui-only"'


def _render(report: Report, monkeypatch: pytest.MonkeyPatch, *, report_id: str) -> str:
    job_runtime._JOBS.pop(report_id, None)
    serve_legacy_report_snapshot(monkeypatch, report, report_id=report_id)
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
    session = auth_logic.create_session("admin@example.com", True)
    with TestClient(web_main.app) as client:
        response = client.get(
            f"/result/{report_id}",
            cookies={auth_constants.SESSION_COOKIE_NAME: session.token},
        )
    assert response.status_code == 200, response.text[:500]
    return response.text


def _article(body: str) -> str:
    """보고서 본문(표지·장·부록)만 잘라 낸다 — 화면 장식은 이 시험의 대상이 아니다."""

    return body[body.index(_ARTICLE_START) : body.index(_ARTICLE_END)]


def _visible(body: str) -> str:
    return re.sub(r"\s+", "", re.sub(r"<[^>]+>", " ", body))


#: 화면에 찍힌 문단 번호(`.pno`)를 순서대로 뽑는다 — `result.html`의 span 그대로다.
_PNO_RE = re.compile(r'<span class="pno" aria-hidden="true">([^<]*)</span>')


def _prose_fragments(text: str) -> list[str]:
    """봉인 문장에서 «화면에 글자 그대로 나와야 하는 조각»만 남긴다.

    본문 속 ``[n]``은 위첨자 링크로, 끝의 «— 해석»은 둥근 배지로 «모양만» 바뀐다
    (``result.html``의 ``inline_refs``). 그래서 조각 사이의 표기까지 시험이 다시
    지어내지 않고, 제품이 쓰는 «같은 분해기»로 글 조각만 꺼내 대조한다.
    """

    body, _interpreted = split_interpretation_marker(text)
    return [
        part.text
        for part in split_citation_markers(body)
        if not part.number and part.text.strip()
    ]


def _assert_sealed_prose_on_screen(article: str, display) -> None:
    """한 장의 봉인 문단이 «번호까지» 화면에 그대로 나왔는지 본다."""

    visible = _visible(article)
    for _ordinal, text in display.paragraphs:
        for fragment in _prose_fragments(text):
            assert _visible(fragment) in visible, f"{display.cell} 문단 누락: {fragment}"


# ══════════════════════════════════════════════════════════
# ① v2 화면은 표시 파생 순수 함수를 한 번도 부르지 않는다
# ══════════════════════════════════════════════════════════

#: 봉인 뒤에는 화면이 부르면 «안 되는» 표시 파생 순수 함수들.
_FORBIDDEN_GLOBALS = (
    "table_visualization",
    "cover_metrics",
    "section_content_blocks",
    "source_verification_label",
    "period_summary_from_table",
)


def _forbid_display_globals(monkeypatch: pytest.MonkeyPatch) -> None:
    """다섯 순수 함수를 전부 «부르면 터지는» 함수로 바꾼다.

    ★ ``period_summary_from_table``만 ``templates.env.globals``에 import 시점에
      «값으로» 박혀 있다. 모듈 속성만 바꾸면 틀은 옛 함수를 계속 부르므로
      두 자리를 모두 바꿔야 실제 음성 대조가 된다.
    """

    def boom(name: str):
        def _raise(*_args, **_kwargs):
            raise AssertionError(f"v2 화면이 봉인 밖 순수 함수를 불렀습니다: {name}")

        return _raise

    for name in _FORBIDDEN_GLOBALS:
        monkeypatch.setattr(request_helpers, name, boom(name))
    monkeypatch.setitem(
        request_helpers.templates.env.globals,
        "period_summary_from_table",
        boom("period_summary_from_table(env.globals)"),
    )


def test_웹_v2_결과페이지는_전역_순수함수를_호출하지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _sealed_v2_report()
    projection = report.public_projection
    assert projection is not None

    _forbid_display_globals(monkeypatch)
    body = _render(report, monkeypatch, report_id="s5-globals-forbidden")

    article = _article(body)
    visible = _visible(article)
    # 본문·도식·표지 띠·요약·부록이 «실제로» 그려졌는지 함께 확인한다.
    # 그러지 않으면 「아무것도 안 그려서 안 불렀다」도 통과해 버린다.
    첫장 = projection.sections[0].display
    assert 첫장.paragraphs, "재료가 잘못됐다 — 봉인에 문단이 없다"
    _assert_sealed_prose_on_screen(article, 첫장)
    # 문단 번호도 봉인 값 그대로여야 한다 — 화면이 다시 세면 PDF와 어긋난다.
    assert _PNO_RE.findall(article) == [
        ordinal
        for block in projection.sections
        for ordinal, _text in block.display.paragraphs
    ]
    실적장 = next(
        block.display for block in projection.sections if block.display.period_summary
    )
    assert _visible(실적장.period_summary.items[0][0]) in visible
    assert projection.cover_metrics is not None
    assert _visible(projection.cover_metrics.title) in visible
    assert _visible(projection.summary[0].text) in visible
    assert _visible(projection.citations[0].label_display) in visible
    도식 = [visual for block in projection.sections for visual in block.display.visuals]
    assert {visual.kind for visual in 도식} >= {"composition", "trend", "flow", "card"}
    for visual in 도식:
        if visual.reading:
            assert _visible(visual.reading) in visible


# ══════════════════════════════════════════════════════════
# ② 부록 「사실 검증」 열 — PDF와 같은 라벨 (G4)
# ══════════════════════════════════════════════════════════

#: PDF 부록 머리글(``export_pdf/logic.py`` ``_source_rows``)이 쓰는 그 글자.
#: 생산 상수를 import해 비교하면 양쪽이 «같이» 틀려도 초록이 되므로 리터럴로 둔다.
_VERIFICATION_COLUMN_LABEL = "사실 검증"


def test_웹_v2_부록에_사실검증_열이_PDF와_같은_라벨로_나온다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _sealed_v2_report()
    projection = report.public_projection
    assert projection is not None

    body = _render(report, monkeypatch, report_id="s5-verification-column")
    article = _article(body)

    assert f'<th scope="col">{_VERIFICATION_COLUMN_LABEL}</th>' in article
    assert projection.citations, "재료가 잘못됐다 — 봉인에 부록 행이 없다"
    for row in projection.citations:
        assert row.verification_label
        assert f"<td>{row.verification_label}</td>" in article
        assert f"<td>{row.status_display}</td>" in article
        assert f"<td>{row.location}</td>" in article
        assert f"<td>{row.used_in_display}</td>" in article


# ══════════════════════════════════════════════════════════
# ③ 화면은 저장된 봉인의 display_sha256이 덮는 블록에서 나왔다
# ══════════════════════════════════════════════════════════


def test_웹과_PDF는_같은_display_sha256의_블록에서_나왔다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """저장층이 들고 있는 ``display_sha256``과 화면이 쓴 블록이 같은 값인지 본다.

    ★ PDF 메타 digest와의 직접 비교는 S4(PDF) 통합 뒤에 붙는다. 지금은 «저장된
      봉인 값»과만 대조한다 — 비교할 PDF 값이 아직 없기 때문이고, 그 사실을
      숨기지 않는다.
    """

    report_id = uuid.uuid4().hex
    frozen = build_identity_contract.process_engine_build_identity()
    original = _build_full_report(build_identity_sha256=frozen.epoch_digest)
    assert original.public_projection is not None
    with storage_db.connect() as conn:
        assert report_store.insert_new(
            conn,
            report_id,
            _COMPANY_ID,
            "분석",
            original,
            engine_epoch_digest=frozen.epoch_digest,
        )
        row = conn.execute(
            f"SELECT projection_json, display_sha256 FROM {TABLE_REPORT_PUBLIC_PROJECTIONS}"
            " WHERE report_id = ?",
            (report_id,),
        ).fetchone()
    assert row is not None, "봉인이 저장되지 않았다"

    # 화면이 그리는 보고서는 «저장본에서 되살린» 것이어야 한다.
    loaded = job_runtime._load_saved_report(report_id)
    assert loaded is None or loaded.public_projection is not None
    rendered_from = loaded if loaded is not None else original
    projection = rendered_from.public_projection
    assert projection is not None
    assert build_report_digest(projection).display_sha256 == str(row["display_sha256"])
    assert json.loads(str(row["projection_json"]))["sections"][0]["display_sha256"] == (
        projection.sections[0].display_sha256
    )

    article = _article(_render(rendered_from, monkeypatch, report_id=report_id))
    for block in projection.sections:
        _assert_sealed_prose_on_screen(article, block.display)
    assert _PNO_RE.findall(article) == [
        ordinal
        for block in projection.sections
        for ordinal, _text in block.display.paragraphs
    ]
    # 부록 행도 «그 블록»에서 나와야 한다. 이 세 값은 봉인 전 화면이 각자
    # 만들던 문자열과 달라서(예: 자료 상태의 fact_status), 블록을 읽지 않으면
    # 화면에 나올 수 없다.
    assert projection.citations
    for row in projection.citations:
        assert f"<td>{row.status_display}</td>" in article
        assert f"<td>{row.verification_label}</td>" in article
        assert f"<td>{row.used_in_display}</td>" in article


# ══════════════════════════════════════════════════════════
# ④ 장부만 바꾼 저장본은 화면 HTML이 바이트 동일
# ══════════════════════════════════════════════════════════


def test_FactRecord만_바꾼_저장본은_웹_HTML_본문이_바이트_동일하다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _sealed_v2_report()
    second = _sealed_v2_report(
        scope="완전히 다른 감사 범위 문자열", relationship="완전히 다른 감사 관계 문자열"
    )

    assert first.public_projection is not None
    assert second.public_projection is not None
    # 장부는 실제로 달라야 한다 — 안 다르면 이 시험이 아무것도 안 지킨다.
    assert first.public_projection.sections[0].ledger != (
        second.public_projection.sections[0].ledger
    )
    assert first.public_projection.sections[0].block_sha256 != (
        second.public_projection.sections[0].block_sha256
    )
    assert first.public_projection.sections[0].display_sha256 == (
        second.public_projection.sections[0].display_sha256
    )

    report_id = "s5-ledger-only-change"
    before = _article(_render(first, monkeypatch, report_id=report_id))
    after = _article(_render(second, monkeypatch, report_id=report_id))

    assert before.encode("utf-8") == after.encode("utf-8")


def test_웹_v2는_ledger를_렌더하지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """감사 장부 원자료가 화면에 한 글자도 새지 않는다 (F-S2p2)."""

    report = _sealed_v2_report()
    body = _render(report, monkeypatch, report_id="s5-no-ledger")

    assert report.fact_records
    for fact in report.fact_records:
        assert fact.fact_id not in body
        assert fact.subject_scope not in body
        assert fact.relationship_or_action not in body
    assert "fact_records" not in body
    assert "subject_scope" not in body
    assert 'data-fact-ids' not in body


# ══════════════════════════════════════════════════════════
# ⑤ v1 화면은 바이트 불변
# ══════════════════════════════════════════════════════════

#: base 커밋(0acf798)의 템플릿이 그린 v1 보고서 본문. 봉인 도입이 옛 화면을
#: 한 글자도 바꾸지 않았음을 증명한다.
_V1_GOLDEN = Path(__file__).with_name("result_v1_article_golden.html")


def _golden_bytes() -> bytes:
    """golden 파일을 «내려받은 줄끝»과 무관하게 읽는다.

    이 저장소는 ``core.autocrlf=true``라 텍스트 파일이 내려받힐 때 줄끝이
    CRLF로 바뀐다. 반면 Jinja는 틀의 줄끝을 항상 ``\\n``으로 고르므로
    (``Environment.newline_sequence`` 기본값) 화면 결과에는 CR가 없다.
    그래서 golden 쪽의 CR만 걷어내고 나머지는 «한 바이트도» 봐준다 —
    비교를 느슨하게 만드는 것이 아니라, 내려받기 변환만 되돌리는 것이다.
    """

    return _V1_GOLDEN.read_bytes().replace(b"\r\n", b"\n")


def test_v1_결과페이지_HTML은_바이트_불변이다(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _render(build_demo_report(), monkeypatch, report_id="v1-article-golden")

    rendered = _article(body).encode("utf-8")
    assert b"\r" not in rendered, "화면 결과에 CR가 생기면 golden 대조 전제가 깨진다"
    assert rendered == _golden_bytes()
