"""실제 공시의 소송 앞 문맥·동일 문서 기간을 보존하는 무료 회귀."""

from pathlib import Path
import re

import pytest

from src.features.filingclean.extra import add_to
from src.features.filingclean.litigation_period import attach_litigation_period


XML = (Path(__file__).parent / "fixtures/sixth_litigation.xml").read_text(encoding="utf-8")
RECEIPT = "20260331000296"
CORP = "01492697"


def _plain(xml):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", xml))


def _run(tmp_path, xml=XML, *, text=None, receipt=RECEIPT, corp=CORP, fragment_document=None):
    path = tmp_path / f"{RECEIPT}.xml"
    path.write_text(xml, encoding="utf-8")
    text = _plain(xml) if text is None else text
    fragments, _ = add_to({}, text)
    if fragment_document:
        fragments = {n: {**f, "문서ID": fragment_document} for n, f in fragments.items()}
    return fragments, attach_litigation_period(
        fragments, filing_text=text, raw_path=str(path), receipt_number=receipt,
        corp_code=corp, document_title="감사보고서 (2025.12)", disclosed_at="2026-03-31",
    )


def test_actual_inline_litigation_keeps_date_and_defendant_without_changing_text(tmp_path):
    before, after = _run(tmp_path)
    fragment = next(f for f in after.values() if f['종류'] == '소송·분쟁')
    assert fragment['원문'].startswith('(4) 당기말 현재 회사가 피고로 계류중인 소송사건은 9건')
    assert fragment['원문'] in _plain(XML)
    assert len(fragment['원문']) <= 1200
    assert fragment['보고기간'] == '2025-01-01/2025-12-31'
    assert fragment['발행주체'] == '토스페이먼츠 주식회사'
    assert fragment['문서ID'] == RECEIPT
    assert fragment['문서일'] == '2026-03-31'
    assert all('보고기간' not in f for f in before.values())


@pytest.mark.parametrize('changes', [
    {'corp': '00000001'},
    {'receipt': '20260331000297'},
    {'fragment_document': '20250331000296'},
    {'text': _plain(XML).replace('9건', '10건')},
    {'xml': XML.replace('AUNIT="PERIODTO"', 'AUNIT="UNKNOWN"')},
    {'xml': XML.replace('AUNITVALUE="20251231"', 'AUNITVALUE="20241231"')},
    {'xml': XML.replace('</DOCUMENT>', '<TU AUNIT="PERIODTO" AUNITVALUE="20241231">2024년 12월 31일</TU></DOCUMENT>')},
    {'xml': XML.replace('AREGCIK="01492697"', 'AREGCIK="00000001"')},
])
def test_different_company_document_text_or_ambiguous_period_is_not_attached(tmp_path, changes):
    before, after = _run(tmp_path, **changes)
    assert before
    assert after == before
    assert all('보고기간' not in f for f in after.values())
