"""공식 뉴스 목록 HTML만으로 출처 메타데이터를 보존한다."""
from pathlib import Path
from urllib.parse import unquote

from src.features.homepage.wide_fragments import build_fragments, extract_list_items
from src.features.homepage.tests.test_wide_fragments import _document
from src.features.homepage.wide_evidence_mapping import to_evidence_mappings
from src.features.homepage.wide_types import WideCollectionResult
from src.web.official_evidence_adapter import _classified_evidence_location_bindings


def test_목록의_세_항목이_제목_날짜_실제_URL을_보존한다():
    raw = (Path(__file__).parent / "fixtures/wrtn_news_list.html").read_text(encoding="utf-8")
    items = extract_list_items(raw, "https://wrtn.io/news/")
    assert len(items) == 3
    assert [item[2] for item in items] == ["2026-09-08", "2026-09-02", "2026-08-26"]
    assert items[1][1] == "뤼튼테크놀로지스, 상장 주관사 선정 착수"
    assert unquote(items[1][3]) == "https://wrtn.io/news/뤼튼테크놀로지스-상장-주관사-선정-착수/"
    document = _document("https://wrtn.io/news/", tuple(item[0] for item in items), list_items=items)
    fragments = build_fragments(document, company_id="c1")
    assert len(fragments) == 3
    assert {fragment.item_url for fragment in fragments} == {item[3] for item in items}
    for fragment in fragments:
        assert fragment.location == fragment.item_url
        assert (fragment.text, fragment.item_title, fragment.item_published_on, fragment.item_url) in items
    envelope = to_evidence_mappings(result=WideCollectionResult(company_id="c1", documents=(document,), attempts=()), fragments=fragments)
    _classified_evidence_location_bindings(envelope, company_id="c1")
    assert {item["item_url"] for item in envelope["fragments"]} == {item[3] for item in items}


def test_목록_구조가_없는_본문은_메타데이터를_추측하지_않는다():
    assert extract_list_items("<main><h1>회사 소개</h1><p>2026.09.02 설립 이후 주요 사업을 운영한다.</p></main>", "https://wrtn.io/") == ()
    document = _document("https://wrtn.io/about", ("2021년 설립 이후 주요 사업을 운영한다.",))
    for fragment in build_fragments(document, company_id="c1"):
        assert not fragment.item_title and not fragment.item_url and not fragment.item_published_on
        assert "#" not in fragment.location


def test_항목_하나인_목록과_깨진_링크도_추가_조회_없이_처리한다():
    raw = '<ul><li><a href="/news/ipo/">상장 주관사 선정 착수</a><time>2026.09.02</time></li></ul><a href="http://[">깨진 주소</a>'
    items = extract_list_items(raw, "https://wrtn.io/news/")
    assert len(items) == 1
    assert items[0][1:] == ("상장 주관사 선정 착수", "2026-09-02", "https://wrtn.io/news/ipo/")
    broken_card = '<ul><li><a href="/news/ipo/">상장 주관사 선정 착수</a><a href="http://[">깨진 주소</a><time>2026.09.02</time></li></ul>'
    assert extract_list_items(broken_card, "https://wrtn.io/news/") == ()
