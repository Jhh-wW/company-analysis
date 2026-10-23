"""같은 XML의 명시 기간만 비용·특수관계자 조각으로 보존한다."""

import re

import pytest

from src.features.filingclean.litigation_period import attach_litigation_period


XML = '''<DOCUMENT><COMPANY-NAME AREGCIK="01492697">토스페이먼츠 주식회사</COMPANY-NAME>
<TU AUNIT="PERIODFROM" AUNITVALUE="20250101">2025년 01월 01일</TU>
<TU AUNIT="PERIODTO" AUNITVALUE="20251231">2025년 12월 31일</TU>
<P>당기와 전기 중 지급수수료는 다음과 같습니다. 당기 200 전기 100</P>
<P>보고기간종료일 현재 회사는 차입금 관련 담보 및 지급보증을 제공받고 있습니다.</P></DOCUMENT>'''
COST = "당기와 전기 중 지급수수료는 다음과 같습니다. 당기 200 전기 100"
GUARANTEE = "보고기간종료일 현재 회사는 차입금 관련 담보 및 지급보증을 제공받고 있습니다."


def attach(tmp_path, xml=XML, **changes):
    path = tmp_path / "20260331000296.xml"
    path.write_text(xml, encoding="utf-8")
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", xml)).strip()
    args = dict(filing_text=text, raw_path=str(path), receipt_number="20260331000296", corp_code="01492697",
        document_title="감사보고서 (2025.12)", disclosed_at="2026-03-31")
    args.update(changes)
    fragments = {5: {"종류": "판관비", "원문": COST}, 4: {"종류": "특수관계자", "원문": GUARANTEE},
                 7: {"종류": "홈페이지", "원문": COST}}
    return fragments, attach_litigation_period(fragments, **args)


def test_same_document_cost_and_guarantee_keep_original_bytes_with_explicit_period(tmp_path):
    before, after = attach(tmp_path)
    for number in (4, 5):
        assert after[number]["원문"] == before[number]["원문"]
        assert after[number]["보고기간"] == "2025-01-01/2025-12-31"
        assert after[number]["원문위치"] == before[number]["종류"]
    assert after[7] == before[7]


@pytest.mark.parametrize("changes", [{"corp_code": "00000001"}, {"receipt_number": "20250331000329"},
    {"filing_text": COST}, {"xml": XML.replace("20251231", "20241231")},
    {"xml": XML.replace('AUNIT="PERIODTO"', 'AUNIT="OTHER"')},
    {"xml": XML.replace("</DOCUMENT>", '<TU AUNIT="PERIODTO" AUNITVALUE="20241231">2024년 12월 31일</TU></DOCUMENT>')}])
def test_other_company_document_conflicting_missing_or_ambiguous_period_is_not_transported(tmp_path, changes):
    before, after = attach(tmp_path, **changes)
    assert after == before
