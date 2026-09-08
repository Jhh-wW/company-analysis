"""기사 본문과 발행일의 구조 경계를 확인한다."""

from src.features.news_intake.fetch import extract_article_published_on, extract_article_text
from src.features.news_intake import constants as c


BODY = "가나다전자는 산업설비 제조 사업을 바탕으로 기업 거래처에 새로운 자동화 설비를 공급했다."


def test_명시된_발행일만_읽고_수정일은_사용하지_않는다():
    assert extract_article_published_on('<meta property="article:modified_time" content="2026-09-08">') == ""
    assert extract_article_published_on('<meta property="article:published_time" content="2026-09-01T10:00:00+09:00">') == "2026-09-01"


def test_JSON_LD_기사타입_발행일과_메타발행일이_충돌하면_추정하지_않는다():
    assert extract_article_published_on('<script type="application/ld+json">{"@type":"NewsArticle","datePublished":"2026-09-01"}</script>') == "2026-09-01"
    assert extract_article_published_on('<meta property="article:published_time" content="2026-09-01"><meta name="pubdate" content="2026-09-02">') == ""
    assert extract_article_published_on('<script type="application/ld+json">{"@type":"Organization","datePublished":"2026-09-01"}</script>') == ""


def test_기사_구획이_있으면_제목과_일반추출결과보다_먼저_쓴다():
    text, stage = extract_article_text('<title>가나다전자 > 회사소식 > 최신기사 > 사업정보 안내</title><article>' + BODY + '</article>',
                                       primary_extract=lambda html: "가나다전자 > 잘못된 페이지 제목을 먼저 읽은 결과입니다.")
    assert text == BODY and stage == c.BODY_STAGE_ARTICLE_TAG


def test_여러_기사본문을_합치거나_가장긴_추천기사를_선택하지_않는다():
    raw = '<article>' + BODY + '</article><article>다른 회사는 금융 거래처와 다양한 사업을 진행하고 있다.</article>'
    assert extract_article_text(raw, primary_extract=lambda html: BODY + " 다른 기사 내용") == ("", "")


def test_일반추출에_넘기는_HTML에서도_제목과_양식을_제거한다():
    seen = []
    def primary(html):
        seen.append(html)
        return BODY
    extract_article_text('<title>가짜 제목</title><form>회원가입 안내</form><p>' + BODY + '</p>', primary_extract=primary)
    assert "가짜 제목" not in seen[0] and "회원가입 안내" not in seen[0]
