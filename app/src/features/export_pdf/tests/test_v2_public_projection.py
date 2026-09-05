"""엔진 v2의 웹·PDF 공개 본문과 내부 감사 장부를 분리한다.

★ 이 파일이 지키는 것
    - v2 FULL(``report.public_projection`` 있음) PDF는 **봉인 블록만** 배치한다.
      문단·문단 번호·도식·읽는 법·3개년 띠·표지 띠·핵심 요약·부록 행을 렌더
      시점에 다시 «계산»하지 않는다. 그래서 ``table_visualization`` ·
      ``cover_metrics`` · ``source_verification_label`` · ``section_content_blocks`` ·
      ``summary_topic`` 다섯 전역을 예외로 바꿔도 PDF가 정상으로 나와야 한다.
    - PDF 메타 지문은 옛 ``content_manifest``가 아니라
      ``PublicReportDigest.content_sha256``이다(옛 지문 C를 대체한다).
    - ``.ledger``(감사 장부)는 어디에도 그리지 않는다 — 장부만 바꾸면 글자와
      ``display_sha256``은 그대로이고 ``content_sha256``만 달라져야 한다.
    - v1(canonical) PDF는 **한 바이트도** 바뀌지 않는다.

★ 왜 손으로 지은 Report를 안 쓰나 — S2 시험이 남긴 교훈과 같다. ``render_report``를
  실제로 통과시킨 v2 보고서라야 인용 번호 숨김·문단 나눔·표 표현 같은 «진짜 규칙»이
  재현된다. 손으로 지은 문자열은 그 규칙을 비켜 가 그물이 헐거워진다.
"""

from __future__ import annotations

import io
import re
from dataclasses import replace

import pdfplumber
from pypdf import PdfReader

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
from src.features.composer.render import (
    COMPOSITION_TABLE_SECTION_ID,
    ENGINE_V2_SCHEMA_VERSION,
    render_report,
)
from src.features.export_pdf import logic as pdf_logic
from src.features.export_pdf.content_manifest import (
    PDF_MANIFEST_SHA256_KEY,
    PDF_MANIFEST_VERSION_KEY,
    public_content_manifest_sha256,
)
from src.features.pipeline.canonical_demo import build_demo_report
from src.features.pipeline.port import (
    FactRecord,
    Grade,
    Report,
    ReportSection,
    SummaryItem,
)
from src.features.report_standard.public_projection import build_public_projection
from src.shared.report_generation.canonical import (
    public_content_digests,
    table_public_projection,
)
from src.shared.report_generation.models import canonical_sha256
from src.shared.report_generation.public_projection import (
    PUBLIC_PROJECTION_VERSION,
    build_report_digest,
)


_PUBLIC_SENTENCE = "공식 자료로 확인한 공개 본문 문장이다."
_PRIVATE_SCOPE = "PDF에 나오면 안 되는 내부 감사 범위"

_확인_문장 = "회사는 음악·영상 사업을 영위한다."
_해석_문장 = "이는 업계 평균을 웃도는 성과로 해석된다."

_원문_문장용 = "회사는 2024년에 매출 100억원을 기록했으며, 이는 직전 회계연도보다 늘어난 수치다."
_원문_표용 = "회사는 스스로를 글로벌 콘텐츠 기업으로 규정하며 음악·영상 사업을 영위한다."


# ══════════════════════════════════════════════════════════
# 재료 ① — 옛 시험이 쓰던 «봉인 없는» v2 보고서 (legacy 갈래)
# ══════════════════════════════════════════════════════════


def _report(*, private_scope: str = _PRIVATE_SCOPE) -> Report:
    fact = FactRecord(
        fact_id="audit-fact-1",
        subject_scope=private_scope,
        relationship_or_action="PDF에 나오면 안 되는 내부 감사 관계",
        claim=_PUBLIC_SENTENCE,
        claim_type="identity_summary",
        section_owner="identity",
    )
    section = ReportSection(
        cell="identity",
        title="기업 정체성",
        lines=[(_PUBLIC_SENTENCE, "")],
        prose_lines=[(_PUBLIC_SENTENCE, "")],
        prose_paragraphs=[_PUBLIC_SENTENCE],
        display_number="1",
        fact_ids=[fact.fact_id],
    )
    return Report(
        company="가나다전자",
        job="",
        corp_type="비상장 외감",
        grade=Grade.PARTIAL,
        sections=[section],
        summary_items=[
            SummaryItem(text=f"핵심 요약 {number}이다.", section_id="identity")
            for number in range(1, 4)
        ],
        fact_records=[fact],
        generated_at="2026-09-01",
        schema_version=ENGINE_V2_SCHEMA_VERSION,
    )


# ══════════════════════════════════════════════════════════
# 재료 ② — render_report를 «실제로» 통과한 v2 FULL 보고서 + 봉인
# ══════════════════════════════════════════════════════════


def _fragments() -> dict[int, dict[str, str]]:
    return {
        1: {"종류": "사업내용", "원문": _원문_문장용},
        2: {"종류": "사업내용", "원문": _원문_표용},
    }


def _composed() -> ComposedReport:
    """아홉 장 전부에 «확인 + 해석» 두 문장. 1장은 카드, 2장은 흐름 도식을 부른다."""

    sections = []
    for section_id in SECTION_IDS:
        flow_rows: tuple[FlowRow, ...] = ()
        if section_id == IDENTITY_TABLE_SECTION_ID:
            flow_rows = (
                FlowRow(
                    cells=("글로벌 콘텐츠 기업", "음악·영상", "해석 없음"),
                    citations=("2",),
                ),
            )
        if section_id == COMPOSITION_TABLE_SECTION_ID:
            flow_rows = (
                FlowRow(
                    cells=("음악 자산", "음반", "구독", "반복 수익"),
                    citations=("1",),
                ),
            )
        sections.append(
            ComposedSection(
                section_id=section_id,
                sentences=(
                    ComposedSentence(
                        text=_확인_문장, citations=("1",), grade=GRADE_CONFIRMED
                    ),
                    ComposedSentence(
                        text=_해석_문장, citations=("1",), grade=GRADE_INTERPRETED
                    ),
                ),
                flow_rows=flow_rows,
            )
        )
    return ComposedReport(
        sections=tuple(sections),
        summary=(
            ComposedSentence(
                text="콘텐츠 기업이다.", citations=("1",), grade=GRADE_CONFIRMED
            ),
            ComposedSentence(
                text="해외를 넓힌다.", citations=("1",), grade=GRADE_CONFIRMED
            ),
            ComposedSentence(
                text="성장 국면으로 읽힌다.", citations=("1",), grade=GRADE_INTERPRETED
            ),
        ),
    )


def _performance_table() -> PerformanceTable:
    """4장 실적표 — 추이 도식·표지 실적 띠·3개년 변화 띠가 전부 여기서 나온다."""

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


def _sealed(report: Report) -> Report:
    """모든 표에 ``manifest_ref``를 채운다(지문 B가 붙였을 «모양»만 흉내).

    ``render_report``는 FULL의 ``public_structure_seal``을 받았을 때만 이 값을
    붙인다. seal 없이 부른 보고서의 표는 빈 문자열이고 S1 ``PublicTableBlock``이
    64자리 SHA-256을 요구해 닫힌다. 표 자체는 한 글자도 바꾸지 않는다.
    """

    sections = [
        replace(
            section,
            tables=[
                replace(
                    table,
                    manifest_ref=canonical_sha256(table_public_projection(table)),
                )
                for table in section.tables
            ],
        )
        for section in report.sections
    ]
    return replace(report, sections=sections)


def _fact(fact_id: str, section_owner: str, *, scope: str) -> FactRecord:
    return FactRecord(
        fact_id=fact_id,
        legal_entity="가나다전자",
        subject_scope=scope,
        relationship_or_action="내부 감사용 관계",
        claim=_확인_문장,
        claim_type="identity_summary",
        section_owner=section_owner,
    )


def _with_facts(report: Report, *, suffix: str, scope: str) -> Report:
    """장부를 실은 보고서 — 1장에 두 건, 4장에 한 건."""

    facts = [
        _fact(f"fact-identity-a-{suffix}", "identity", scope=scope),
        _fact(f"fact-identity-b-{suffix}", "identity", scope=scope),
        _fact(f"fact-past-{suffix}", "past_changes", scope=scope),
    ]
    owners = {
        "identity": [f"fact-identity-a-{suffix}", f"fact-identity-b-{suffix}"],
        "past_changes": [f"fact-past-{suffix}"],
    }
    sections = [
        replace(section, fact_ids=list(owners.get(section.cell, [])))
        for section in report.sections
    ]
    return replace(report, sections=sections, fact_records=facts)


def _v2_full_report(*, suffix: str = "1", scope: str = _PRIVATE_SCOPE) -> Report:
    """봉인(``public_projection``)까지 붙은 v2 FULL 보고서."""

    rendered = _sealed(
        render_report(
            "가나다전자",
            _composed(),
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
    with_facts = _with_facts(rendered, suffix=suffix, scope=scope)
    return replace(
        with_facts, public_projection=build_public_projection(with_facts)
    )


def _visible_text(pdf_bytes: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as document:
        return "\n".join(page.extract_text() or "" for page in document.pages)


def _squeezed(value: str) -> str:
    """줄바꿈·자간 공백을 지운 «글자만» 남긴다.

    PDF 글자 추출은 줄 끝에서 문장을 자르고 표 셀 사이에 공백을 넣는다. 봉인된
    문장이 «글자 단위로» 그대로 나왔는지 보려면 공백을 걷어내고 비교해야 한다.
    """

    return re.sub(r"\s+", "", value)


def _forbid(name: str):
    def forbidden(*_args, **_kwargs):
        raise AssertionError(f"v2 PDF가 블록 밖 전역 {name}을(를) 호출했습니다")

    return forbidden


# ══════════════════════════════════════════════════════════
# ① 블록 밖 문자열 금지
# ══════════════════════════════════════════════════════════


def test_v2_PDF는_감사용_사실카드_생성기를_호출하지_않는다(monkeypatch):
    monkeypatch.setattr(
        pdf_logic, "section_content_blocks", _forbid("section_content_blocks")
    )

    text = _visible_text(pdf_logic.build_pdf(_report()))

    assert _PUBLIC_SENTENCE in text
    assert _PRIVATE_SCOPE not in text


def test_v2_PDF는_블록_밖_문자열을_만들지_않는다(monkeypatch):
    """봉인 블록만 배치한다 — 파생 문구를 만드는 전역 넷을 전부 막아도 정상."""

    report = _v2_full_report()
    assert report.public_projection is not None

    for name in (
        "table_visualization",
        "cover_metrics",
        "source_verification_label",
        "section_content_blocks",
        # ★ 다섯째는 반대 경우 시험이 찾아낸 구멍이다 — 핵심 요약을 봉인 대신 다시
        #   계산해도(주제어를 여기서 고르면) 위 넷만으로는 아무 시험도 깨지지
        #   않았다. 요약 주제어는 이 함수만 만들 수 있으므로 같이 막는다.
        "summary_topic",
    ):
        monkeypatch.setattr(pdf_logic, name, _forbid(name))

    text = _squeezed(_visible_text(pdf_logic.build_pdf(report)))

    assert _squeezed(_확인_문장) in text
    assert _squeezed(_PRIVATE_SCOPE) not in text
    # 부록 「사실 검증」 라벨도 봉인값에서 나와야 한다(전역을 막았으므로).
    labels = {row.verification_label for row in report.public_projection.citations}
    assert labels
    for label in labels:
        assert _squeezed(label) in text


#: PDF가 «표지에서만» 쓰던 짧은 사본이다. 어디에도 나오면 안 된다.
#: ★ 생산 상수를 import하지 않고 글자를 그대로 적는다. import로 묶으면 사본이
#:   되살아나도 시험이 같이 따라가 아무것도 못 잡는다.
_PDF_ONLY_COVER_COPY = "공식 근거로 확인된 항목만 수록했습니다."

#: 2026-09-05 이전에 «이미 봉인되어 저장된» 보고서의 고지 글자. 새 보고서는
#: 빈 고지를 봉인하지만, 옛 저장본의 봉인은 지금도 이 글자를 들고 있다.
#: 그래서 「봉인이 비었으니 안 나온다」가 아니라 「봉인에 글자가 있어도 PDF가
#: 읽지 않는다」를 확인해야 한다.
_이미_봉인된_옛_고지 = (
    "안전 확인 중인 임시 부분 보고서",
    "확인되지 않은 숫자 문장은 제외했지만 모든 문장·표·도식의 새 "
    "검증은 아직 끝나지 않았습니다. 아래에 남은 이유를 표시합니다.",
)

#: 실제 파이프라인이 만드는 미제공 사유 모양의 표본.
_사유_표본 = [
    "표와 도식은 아직 하나씩 확인하지 못했습니다. "
    "숫자를 그대로 인용하기 전에 부록의 원문을 함께 확인해 주세요.",
]


def test_v2_부분보고서_고지는_표지에도_본문에도_그리지_않는다():
    """출시된 보고서에 만드는 과정 이야기를 싣지 않는다 (사용자 결정, 2026-09-05).

    ★ 재료를 «옛 저장본»으로 만든다 — 봉인에 고지 글자를 도로 심어 두고 PDF를
      낸다. 이렇게 해야 「봉인이 비어서 안 나온 것」과 「PDF가 안 읽어서 안 나온
      것」이 갈린다. 앞의 것만 확인하면 이미 발행된 보고서는 계속 새어 나간다.
    """

    report = _v2_full_report()
    assert report.public_projection is not None
    # 새로 만든 봉인은 이제 고지를 담지 않는다.
    assert report.public_projection.grade_notice == ("", "")

    # 옛 저장본 재현 — 사유를 담아 봉인을 다시 만들고, 그 봉인에 옛 고지를 심는다.
    사유_있는_보고서 = replace(report, shortfall_reasons=list(_사유_표본))
    옛_저장본 = replace(
        사유_있는_보고서,
        public_projection=replace(
            build_public_projection(사유_있는_보고서),
            grade_notice=_이미_봉인된_옛_고지,
        ),
    )
    notice_title, notice_detail = 옛_저장본.public_projection.grade_notice
    assert notice_title and notice_detail, "재료에 옛 고지가 없다 — 시험이 무의미해진다"
    reasons = 옛_저장본.public_projection.header["shortfall_reasons"]
    assert reasons, "재료에 미제공 사유가 없다 — 사유 누출 시험이 무의미해진다"

    text = _squeezed(_visible_text(pdf_logic.build_pdf(옛_저장본)))

    assert _squeezed(notice_title) not in text
    assert _squeezed(notice_detail) not in text
    for reason in reasons:
        assert _squeezed(str(reason)) not in text
    # PDF 표지 전용 사본도 그대로 사라진 상태다.
    assert _squeezed(_PDF_ONLY_COVER_COPY) not in text
    # 본문은 그대로 나온다 — 고지 블록만 빠졌다.
    assert _squeezed("핵심 요약") in text


# ══════════════════════════════════════════════════════════
# ② 메타 지문 — 옛 content_manifest가 아니라 새 digest
# ══════════════════════════════════════════════════════════


def test_v2_PDF_메타키는_PublicReportDigest_content_sha256이다():
    report = _v2_full_report()
    assert report.public_projection is not None
    digest = build_report_digest(report.public_projection)

    metadata = PdfReader(io.BytesIO(pdf_logic.build_pdf(report))).metadata or {}

    assert str(metadata.get(PDF_MANIFEST_VERSION_KEY)) == PUBLIC_PROJECTION_VERSION
    assert str(metadata.get(PDF_MANIFEST_SHA256_KEY)) == digest.content_sha256
    # 옛 지문(PDF 전용 별도 직렬화기)이 남아 있으면 교체가 안 된 것이다.
    assert str(
        metadata.get(PDF_MANIFEST_SHA256_KEY)
    ) != public_content_manifest_sha256(report)


# ══════════════════════════════════════════════════════════
# ③ 장부만 바꾼 보고서 — 글자·display는 같고 content 지문만 다르다
# ══════════════════════════════════════════════════════════


def test_v2_감사장부와_fact_id가_바뀌어도_공개PDF_글자는_같다():
    original = _v2_full_report(suffix="1", scope=_PRIVATE_SCOPE)
    changed = _v2_full_report(suffix="2", scope="또 다른 비공개 감사 문자열")
    assert original.public_projection is not None
    assert changed.public_projection is not None

    original_digest, _ = public_content_digests(original)
    changed_digest, _ = public_content_digests(changed)
    original_seal = build_report_digest(original.public_projection)
    changed_seal = build_report_digest(changed.public_projection)
    original_text = _visible_text(pdf_logic.build_pdf(original))
    changed_text = _visible_text(pdf_logic.build_pdf(changed))

    assert original_digest == changed_digest
    assert original_text == changed_text
    assert _PRIVATE_SCOPE not in original_text
    assert "또 다른 비공개 감사 문자열" not in changed_text
    # 표시(display)는 불변, 장부를 덮는 content 지문만 달라져야 한다.
    assert original_seal.display_sha256 == changed_seal.display_sha256
    assert original_seal.content_sha256 != changed_seal.content_sha256


# ══════════════════════════════════════════════════════════
# ④ 읽는 법·3개년 띠 — 웹에만 있던 두 가지를 PDF도 블록에서 그린다(D5)
# ══════════════════════════════════════════════════════════


def test_v2_PDF는_reading과_3개년띠를_블록에서_그린다():
    report = _v2_full_report()
    projection = report.public_projection
    assert projection is not None

    readings = [
        visual.reading
        for block in projection.sections
        for visual in block.display.visuals
        if visual.reading.strip()
    ]
    bands = [
        block.display.period_summary
        for block in projection.sections
        if block.display.period_summary is not None
    ]
    assert readings, "재료가 «읽는 법»을 만들지 못했다 — 시험이 무의미해진다"
    assert bands, "재료가 3개년 띠를 만들지 못했다 — 시험이 무의미해진다"

    text = _squeezed(_visible_text(pdf_logic.build_pdf(report)))

    for reading in readings:
        assert _squeezed(reading) in text
    for band in bands:
        assert _squeezed(band.title) in text
        for item in band.items:
            label, _base_period, _base_value, _latest_period = item[0], item[1], item[2], item[3]
            assert _squeezed(label) in text
            assert _squeezed(item[6]) in text  # change


# ══════════════════════════════════════════════════════════
# ⑤ 본문 글자 — 봉인된 문단과 «글자 단위»로 같다
# ══════════════════════════════════════════════════════════


def test_v2_PDF_텍스트는_display_paragraphs와_글자_단위로_같다():
    report = _v2_full_report()
    projection = report.public_projection
    assert projection is not None

    text = _squeezed(_visible_text(pdf_logic.build_pdf(report)))

    checked = 0
    for block in projection.sections:
        for ordinal, paragraph in block.display.paragraphs:
            assert _squeezed(f"{ordinal}{paragraph}") in text
            checked += 1
    assert checked >= len(SECTION_IDS)

    # 핵심 요약도 봉인된 번호·주제어·문장 그대로다.
    for row in projection.summary:
        assert _squeezed(row.ordinal) in text
        assert _squeezed(row.text) in text


# ══════════════════════════════════════════════════════════
# ⑥ v1(canonical) PDF는 승인된 디자인 토큰 v1 바이트로 고정된다
# ══════════════════════════════════════════════════════════

#: 디자인 토큰 v1 적용 뒤 실측한 v1 데모 보고서 PDF의 SHA-256과 길이다.
#: ★ 왜 상수를 시험에 «리터럴»로 적나 — 생산 코드에서 같은 값을 import하면
#:   v1 경로가 바뀌어도 양쪽이 함께 움직여 시험이 못 잡는다. 이 값은 «바뀌면
#:   안 되는 사실»이므로 시험이 직접 들고 있어야 한다.
#: PDF bytes는 결정적이다 — ``/CreationDate``가 고정 토큰이고 trailer ID도
#: 내용 기반이라 프로세스·실행이 달라도 같은 bytes가 나온다(실측).
_V1_DEMO_PDF_SHA256 = "17fbaf1f06025df9ff66d86cebf21b34e3046369c48511845c8ed94c2e05bc8a"
_V1_DEMO_PDF_LENGTH = 93330


def test_v1_PDF는_바이트_불변이다():
    """디자인 토큰 v1 적용 뒤 canonical(v1) 산출물이 흔들리지 않아야 한다."""

    import hashlib  # noqa: PLC0415 - 이 시험 하나만 쓰는 표준 라이브러리

    report = replace(build_demo_report(), generated_at="2026-08-19")
    assert report.schema_version != ENGINE_V2_SCHEMA_VERSION
    assert report.public_projection is None

    pdf = pdf_logic.build_pdf(report)

    assert len(pdf) == _V1_DEMO_PDF_LENGTH
    assert hashlib.sha256(pdf).hexdigest() == _V1_DEMO_PDF_SHA256
    # v1은 옛 지문을 그대로 쓴다(옛 경로는 그대로 둔다).
    metadata = PdfReader(io.BytesIO(pdf)).metadata or {}
    assert str(metadata.get(PDF_MANIFEST_SHA256_KEY)) == public_content_manifest_sha256(
        report
    )
