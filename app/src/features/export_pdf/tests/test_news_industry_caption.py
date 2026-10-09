"""공식·보도 사업 표제를 같은 로컬 PDF와 Notion 블록에서 대조한다."""

from io import BytesIO
from types import SimpleNamespace
import json

from pypdf import PdfReader
from reportlab.platypus import SimpleDocTemplate

from src.features.composer.tests.test_news_business_industry_context import rendered_news, enabled_news
from src.features.composer.tests.test_official_industry_context import _render_official
from src.features.export_pdf.logic import _add_industry_contexts, _register_fonts, _styles
from src.features.export_notion.logic import _v2_section_blocks
from src.shared.business_challenge_context import IndustryContextDisplay


def mixed_displays():
    news = rendered_news().sections[4].industry_contexts[0]
    formal = _render_official()[0].sections[4].industry_contexts[0]
    return (IndustryContextDisplay(news, 1, 2), IndustryContextDisplay(formal, 3, 4))


def test_mixed_pdf_caption_and_formal_lines_remain_exact():
    displays = mixed_displays()
    _register_fonts()
    story = []
    _add_industry_contexts(story, SimpleNamespace(industry_contexts=displays), _styles())
    output = BytesIO()
    SimpleDocTemplate(output).build(story)
    text = "\n".join(page.extract_text() for page in PdfReader(output).pages)
    assert "검증된 보도 사업과 관련된 산업 과제 · 해석" in text
    assert "공식 자료에 나온 사업과 관련된 산업 과제 · 해석" in text
    assert "보도 사업 근거" in text and "공식 사업 근거" in text
    assert "인수한 사업의 관련성" in text


def test_local_notion_blocks_preserve_mixed_source_kind_captions():
    display = SimpleNamespace(cell="current_challenges", display_number="5", title="현재 과제", tag="",
                              guidance_lines=(), paragraphs=(), period_summary=None, tables=(), visuals=(),
                              industry_contexts=mixed_displays())
    text = json.dumps(_v2_section_blocks(display), ensure_ascii=False)
    assert "검증된 보도 사업과 관련된 산업 과제 · 해석" in text
    assert "공식 자료에 나온 사업과 관련된 산업 과제 · 해석" in text
