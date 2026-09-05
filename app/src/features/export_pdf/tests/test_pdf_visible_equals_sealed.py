"""PDF 본문에 «봉인 블록에 없는 문단»이 한 줄도 섞이지 않는지 본다.

★ 이 파일이 막는 것
  채널이 자기만 아는 문장을 한 줄 얹는 일이다. 「추가 조언」·「참고」 같은
  한 문단이 사용자 정본(PDF)에 끼어들어도 지금까지의 시험은 전부 초록이었다.
  기존 시험들은 «봉인 문장이 나왔는가»(있음)와 «금지 문구가 없는가»(없음)만
  봤을 뿐, «그 밖에 아무것도 없는가»(정확히 같음)를 본 적이 없기 때문이다.

★ 그래서 이 파일은 «정확히 같음»을 본다
  ① 렌더가 배치한 번호 문단 목록 == 봉인 블록 문단 목록 (순서·번호·글자 모두)
  ② 뽑아낸 PDF 글자에서도 한 장의 문단들이 봉인 순서 그대로 «붙어» 나온다

  ①만으로는 「PDF에 정말 그렇게 찍혔나」를 못 보고, ②만으로는 장 끝에 덧붙인
  한 줄을 못 본다(쪽이 바뀌면 쪽 머리글·꼬리글이 사이에 끼어들어 장 전체를 한
  덩어리로 맞댈 수 없다). 그래서 둘을 같이 둔다.

★ 재료는 «장당 두 문단» 이상이다
  장마다 문단이 하나뿐인 재료로는 「뒤 문단이 통째로 빠짐」도 「뒤에 한 줄 더
  붙임」도 드러나지 않는다. 그래서 인용이 바뀌는 문장 넷으로 장마다 두 문단을
  만든다. 세 채널 시험이 이 재료를 같이 쓴다 — 재료가 갈라지면 「같은 봉인에서
  같은 글자가 나왔다」를 말할 수 없다.

★ 실제 AI·네트워크를 쓰지 않는다. 보고서는 결정론적 재료로 만든다.
"""

from __future__ import annotations

import io
import re
from dataclasses import replace

import pdfplumber
import pytest

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
)
from src.features.composer.render import COMPOSITION_TABLE_SECTION_ID, render_report
from src.features.export_pdf import logic as pdf_logic

# ★ 재료의 «표·조각·감사 장부» 부분은 봉인 PDF 시험이 이미 지어 두었다. 같은
#   틀을 다시 짓지 않고 그대로 가져와, 이 파일은 «문단을 둘로 나누는 부분»만
#   더한다.
from src.features.export_pdf.tests.test_v2_public_projection import (
    _PRIVATE_SCOPE,
    _composition_table,
    _fragments,
    _performance_table,
    _sealed,
    _squeezed,
    _with_facts,
)
from src.features.pipeline.port import Report
from src.features.report_standard.public_projection import build_public_projection
from src.shared.report_generation.public_projection import PublicReportProjection


def _section_sentences(order: int) -> tuple[ComposedSentence, ...]:
    """한 장에 들어갈 문장 넷.

    앞 둘과 뒤 둘이 «다른 자료»를 인용하므로 봉인이 문단을 둘로 나눈다
    (``composer.render._paragraph_breaks`` — 인용이 바뀌면 문단이 바뀐다).
    장 번호를 글자에 넣어 장마다 다른 문단이 되게 한다.
    """

    return (
        ComposedSentence(
            text=f"{order}장 앞 문단은 공식 자료로 확인한 사실이다.",
            citations=("2",),
            grade=GRADE_CONFIRMED,
        ),
        ComposedSentence(
            text=f"{order}장 앞 문단의 뜻은 이렇게 읽힌다.",
            citations=("2",),
            grade=GRADE_INTERPRETED,
        ),
        ComposedSentence(
            text=f"{order}장 뒤 문단은 다른 자료로 확인한 사실이다.",
            citations=("1",),
            grade=GRADE_CONFIRMED,
        ),
        ComposedSentence(
            text=f"{order}장 뒤 문단의 뜻은 이렇게 읽힌다.",
            citations=("1",),
            grade=GRADE_INTERPRETED,
        ),
    )


def _composed_with_two_paragraphs() -> ComposedReport:
    sections = []
    for order, section_id in enumerate(SECTION_IDS, start=1):
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
                sentences=_section_sentences(order),
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


def sealed_two_paragraph_report() -> Report:
    """장마다 봉인 문단이 «둘»인 v2 FULL 보고서. 세 채널 시험이 같이 쓴다."""

    rendered = _sealed(
        render_report(
            "가나다전자",
            _composed_with_two_paragraphs(),
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


def sealed_paragraph_rows(
    projection: PublicReportProjection,
) -> list[tuple[str, str, str]]:
    """봉인 블록의 본문 문단을 (장, 문단번호, 글자)로 «순서 그대로» 펼친다."""

    return [
        (block.display.cell, ordinal, text)
        for block in projection.sections
        for ordinal, text in block.display.paragraphs
    ]


def assert_two_paragraphs_per_section(projection: PublicReportProjection) -> None:
    """재료가 «장당 두 문단»을 유지하는지 먼저 확인한다.

    ★ 이 확인이 없으면 재료가 한 문단으로 줄어든 날 그물이 조용히 헐거워진다.
      「뒤 문단 누락」과 「뒤에 한 줄 덧붙임」은 둘째 문단이 있어야 드러난다.
    """

    for block in projection.sections:
        assert (
            len(block.display.paragraphs) >= 2
        ), f"{block.display.cell} 재료가 한 문단뿐이다 — 이 시험이 헐거워진다"


#: 본문 문자열에 박힌 인용 표식 ``[1]``.
_CITATION_MARKER_RE = re.compile(r"\[(\d+)\]")

#: 문장 끝 «해석» 표지와, 화면이 배지로 바꿔 그릴 때 남는 글자.
_INTERPRETATION_MARK_IN_TEXT = " — 해석"
_INTERPRETATION_MARK_ON_SCREEN = " 해석"


def channel_neutral(text: str) -> str:
    """채널 «모양» 차이를 걷어내고 «글자»만 남긴다.

    ★ 정규화 규칙
      - 인용 표식 ``[1]``과 문장 끝 ``— 해석``은 **글자의 일부**다. 통째로
        사라지면 이 시험이 깨져야 한다 — 그래서 지우지 않고 «모양만» 맞춘다.
      - 채널 고유 스타일은 허용한다. 화면은 ``[1]``을 위첨자 링크 ``1``로,
        ``— 해석``을 둥근 배지 ``해석``으로 그린다. PDF·노션은 글자를 그대로
        쓴다. 그래서 «대괄호»와 «표지 하이픈»만 걷어 낸 모양으로 모은다.
      - 줄바꿈·자간·머리기호도 채널 마음이다. 그래서 공백은 하나로 줄인다.
    """

    without_brackets = _CITATION_MARKER_RE.sub(r"\1", text)
    unmarked = without_brackets.replace(
        _INTERPRETATION_MARK_IN_TEXT, _INTERPRETATION_MARK_ON_SCREEN
    )
    return re.sub(r"\s+", " ", unmarked).strip()


# ══════════════════════════════════════════════════════════
# ① 렌더가 배치한 번호 문단 목록 == 봉인 문단 목록
# ══════════════════════════════════════════════════════════


def test_PDF_번호문단_목록은_봉인_문단_목록과_정확히_같다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """봉인이 준 문단만, 봉인이 준 번호로, 봉인이 준 순서대로 배치한다.

    ``_numbered_paragraph``는 봉인 갈래가 본문 문단을 만드는 «유일한» 자리다
    (봉인 없는 갈래는 ``number_text`` 없이 부르므로 섞이지 않는다). 그 호출을
    순서대로 받아 적어 봉인 목록과 «길이까지» 맞댄다 — 한 줄이라도 더 배치하면
    길이가 달라져 깨진다.
    """

    report = sealed_two_paragraph_report()
    projection = report.public_projection
    assert projection is not None
    assert_two_paragraphs_per_section(projection)

    placed: list[tuple[str, str]] = []
    original = pdf_logic._numbered_paragraph

    def recording(position, text, styles, width, *, number_text=""):
        placed.append((number_text, text))
        return original(position, text, styles, width, number_text=number_text)

    monkeypatch.setattr(pdf_logic, "_numbered_paragraph", recording)

    pdf_logic.build_pdf(report)

    assert placed == [
        (ordinal, text) for _cell, ordinal, text in sealed_paragraph_rows(projection)
    ]


# ══════════════════════════════════════════════════════════
# ② 뽑아낸 PDF 글자에도 봉인 문단만, 붙어서, 순서대로
# ══════════════════════════════════════════════════════════


def _body_text_without_page_furniture(pdf_bytes: bytes) -> str:
    """쪽 머리글·꼬리글을 걷어낸 본문 글자.

    ★ 왜 걷어내나: 머리글(제목·기준일)과 꼬리글(회사명·쪽번호)은 «보고서 내용»이
      아니라 쪽마다 다시 찍히는 장식이다(``logic._page_furniture``, 표지 다음
      쪽부터). 한 장이 쪽 경계를 걸치면 그 장식이 문단 «사이»에 끼어들어, 내용은
      한 글자도 안 바뀌었는데 「덩어리가 끊겼다」로 보인다.
    ★ 걷어내도 이 시험이 지키는 것은 그대로다 — 장식이 아닌 «보고서 글자»가
      문단 사이에 끼면 여전히 덩어리가 끊어진다.
    ★ 자리로 지운다(첫 줄·마지막 줄). 글자로 지우면 본문에 우연히 같은 글자가
      있을 때 본문까지 지운다.
    """

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as document:
        page_texts = [page.extract_text() or "" for page in document.pages]

    kept: list[str] = []
    for position, page_text in enumerate(page_texts):
        lines = page_text.splitlines()
        # 표지(첫 쪽)에는 머리글·꼬리글이 없다.
        if position > 0 and len(lines) >= 2:
            lines = lines[1:-1]
        kept.append("\n".join(lines))
    return "\n".join(kept)


def test_PDF_글자에서_한_장의_문단들은_봉인_순서대로_붙어_나온다() -> None:
    """한 장의 문단 사이에 «다른 글자»가 끼면 깨진다.

    한 장의 문단들은 번호와 함께 잇달아 인쇄되므로, 공백을 걷어낸 글자에서
    ``번호+문단+번호+문단``이 **한 덩어리**로 나온다. 사이에 한 줄이 끼면
    덩어리가 끊어진다. 쪽 머리글·꼬리글은 보고서 내용이 아니라 쪽마다 다시
    찍히는 장식이라 먼저 걷어내고(``_body_text_without_page_furniture``),
    덩어리끼리는 앞에서 뒤로만 찾아 순서를 지킨다.
    """

    report = sealed_two_paragraph_report()
    projection = report.public_projection
    assert projection is not None
    assert_two_paragraphs_per_section(projection)

    printed = _squeezed(_body_text_without_page_furniture(pdf_logic.build_pdf(report)))

    cursor = 0
    for block in projection.sections:
        display = block.display
        run = _squeezed("".join(ordinal + text for ordinal, text in display.paragraphs))
        found = printed.find(run, cursor)
        assert found >= 0, f"{display.cell} 문단 덩어리가 끊겼거나 사라졌다"
        cursor = found + len(run)
