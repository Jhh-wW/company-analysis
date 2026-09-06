"""기사 본문 읽기의 주소 후보·폴백 사다리·해독 건강도 (네트워크 없음)."""

from __future__ import annotations

import pytest

from src.features.news_intake import constants as c
from src.features.news_intake.fetch import (
    article_url_variants,
    body_fetch_urls,
    decode_looks_broken,
    extract_article_text,
    http_status_code,
    normalize_body_result,
    rebind_candidate_to_url,
)
from src.features.news_intake.models import NewsBodyFetchResult, NewsCandidate


#: 첫 겹(본문 구간) 추출기 대역. 파이프라인은 공식 웹 수집기의
#: ``extract_usable_ranges``를 주입하지만, 여기서는 「본문 구간을 찾았다/
#: 못 찾았다」만 흉내 내면 사다리를 다 잠글 수 있다.
_BODY_MARK = "<p class=\"usable\">"
_PRIMARY_TEXT = "가나다전자가 고객 업무를 잇는 새 제품군을 공개했다고 밝혔다."


def _primary(raw_html: str) -> str:
    return _PRIMARY_TEXT if _BODY_MARK in raw_html else ""


def _candidate(
    *, source_url: str, originallink: str = "", link: str = ""
) -> NewsCandidate:
    return NewsCandidate(
        id="news-001",
        title="가나다전자 새 제품군 공개",
        description="요약",
        originallink=originallink,
        link=link,
        published_on="2026-08-01",
        publisher="media.example",
        priority=4,
        source_url=source_url,
    )


# ------------------------------------------------------------- 주소 후보


def test_이미_https면_scheme_변형은_만들지_않는다() -> None:
    """https 주소에는 «승격»할 게 없다. 남는 변형은 호스트 표기뿐이다."""

    url = "https://www.media.example/news/1"

    variants = article_url_variants(url)

    assert variants[0] == url
    assert all(candidate.startswith("https://") for candidate in variants)
    assert len(variants) < c.MAX_URL_VARIANTS


def test_http_주소는_https_승격과_www_토글까지_만든다() -> None:
    variants = article_url_variants("http://media.example/news/1")

    assert variants == (
        "http://media.example/news/1",
        "https://media.example/news/1",
        "https://www.media.example/news/1",
    )
    assert len(variants) <= c.MAX_URL_VARIANTS


def test_www_주소는_www를_떼는_쪽으로_토글한다() -> None:
    variants = article_url_variants("https://www.media.example/news/1")

    assert variants == (
        "https://www.media.example/news/1",
        "https://media.example/news/1",
    )


def test_https를_http로_낮추는_변형은_만들지_않는다() -> None:
    for variant in article_url_variants("https://www.media.example/news/1"):
        assert variant.startswith("https://")


def test_경로와_질의는_변형에서도_그대로_남는다() -> None:
    variants = article_url_variants("http://media.example/read?no=7&x=1")

    assert all(candidate.endswith("/read?no=7&x=1") for candidate in variants)


def test_공개_웹_주소가_아니면_후보가_없다() -> None:
    assert article_url_variants("javascript:alert(1)") == ()
    assert article_url_variants("") == ()
    assert article_url_variants("file:///etc/passwd") == ()


def test_주소칸_순서는_상수가_정하고_같은_주소는_한_번만_쓴다() -> None:
    candidate = _candidate(
        source_url="https://media.example/1",
        originallink="https://media.example/1",
        link="https://search.example/1",
    )

    assert body_fetch_urls(candidate) == (
        "https://media.example/1",
        "https://search.example/1",
    )
    assert c.BODY_FETCH_URL_FIELD_ORDER[0] == "source_url"


def test_주소칸이_하나뿐이면_한_주소만_시도한다() -> None:
    candidate = _candidate(
        source_url="https://media.example/1",
        originallink="https://media.example/1",
        link="",
    )

    assert body_fetch_urls(candidate) == ("https://media.example/1",)


# ------------------------------------------------------------ 폴백 사다리


def test_본문구간이_있으면_첫_겹에서_멈춘다() -> None:
    html = f"<html><body>{_BODY_MARK}본문</p></body></html>"

    text, stage = extract_article_text(html, primary_extract=_primary)

    assert text == _PRIMARY_TEXT
    assert stage == c.BODY_STAGE_USABLE_RANGES


def test_본문구간이_비면_JSON_LD_articleBody로_내려간다() -> None:
    html = (
        '<html><head><script type="application/ld+json">'
        '{"@type":"NewsArticle","articleBody":'
        '"가나다전자가 새 제품군을 공개했다고 6일 밝혔다."}'
        "</script></head><body></body></html>"
    )

    text, stage = extract_article_text(html, primary_extract=_primary)

    assert text == "가나다전자가 새 제품군을 공개했다고 6일 밝혔다."
    assert stage == c.BODY_STAGE_JSON_LD


def test_JSON_LD가_graph로_한겹_감싸도_본문을_찾는다() -> None:
    html = (
        '<script type="application/ld+json">'
        '{"@graph":[{"@type":"WebPage"},'
        '{"@type":"NewsArticle","articleBody":"가나다전자가 계약을 체결했다고 밝혔다."}]}'
        "</script>"
    )

    text, stage = extract_article_text(html, primary_extract=_primary)

    assert text == "가나다전자가 계약을 체결했다고 밝혔다."
    assert stage == c.BODY_STAGE_JSON_LD


def test_JSON_LD가_깨져_있으면_다음_겹으로_넘어간다() -> None:
    html = (
        '<script type="application/ld+json">{잘못된 json}</script>'
        "<article><p>가나다전자가 물류 자동화 설비를 도입했다고 밝혔다.</p></article>"
    )

    text, stage = extract_article_text(html, primary_extract=_primary)

    assert text == "가나다전자가 물류 자동화 설비를 도입했다고 밝혔다."
    assert stage == c.BODY_STAGE_ARTICLE_TAG


def test_article_태그_본문에서_스크립트와_boilerplate는_빠진다() -> None:
    html = (
        "<article>"
        "<nav>메뉴 메뉴 메뉴 메뉴 메뉴 메뉴 메뉴 메뉴 메뉴 메뉴</nav>"
        "<script>var advertisement = '광고 광고 광고 광고 광고';</script>"
        "<p>가나다전자가 신규 공장을 준공했다고 6일 밝혔다.</p>"
        "</article>"
    )

    text, stage = extract_article_text(html, primary_extract=_primary)

    assert text == "가나다전자가 신규 공장을 준공했다고 6일 밝혔다."
    assert stage == c.BODY_STAGE_ARTICLE_TAG
    assert "메뉴" not in text
    assert "광고" not in text


def test_article_태그_밖의_글자는_본문으로_세지_않는다() -> None:
    html = "<body><p>배너 문구가 여기에 아주 길게 들어가 있습니다 정말로</p></body>"

    text, stage = extract_article_text(html, primary_extract=_primary)

    assert (text, stage) == ("", "")


def test_마지막_겹은_og_description_한_문장이라도_받는다() -> None:
    html = (
        "<html><head>"
        '<meta property="og:description" '
        'content="가나다전자가 고객사와 공급 계약을 체결했다고 밝혔다.">'
        "</head><body></body></html>"
    )

    text, stage = extract_article_text(html, primary_extract=_primary)

    assert text == "가나다전자가 고객사와 공급 계약을 체결했다고 밝혔다."
    assert stage == c.BODY_STAGE_META_DESCRIPTION


def test_og가_없으면_일반_description_메타를_쓴다() -> None:
    html = (
        "<html><head>"
        '<meta name="description" '
        'content="가나다전자가 상반기 신규 채용을 마쳤다고 밝혔다.">'
        "</head></html>"
    )

    text, stage = extract_article_text(html, primary_extract=_primary)

    assert text == "가나다전자가 상반기 신규 채용을 마쳤다고 밝혔다."
    assert stage == c.BODY_STAGE_META_DESCRIPTION


def test_og가_한_마디뿐이면_더_긴_description_메타로_넘어간다() -> None:
    """실측: 사진 기사에서 ``og:description``은 사진 설명 한 마디뿐이고
    기사 요약은 일반 ``description``에만 들어 있었다. 우선순위 값에서
    그냥 멈추면 그런 기사는 본문 0자가 된다.
    """

    html = (
        '<meta property="og:description" content="사진 한 마디">'
        '<meta name="description" '
        'content="가나다전자가 고객사와 공급 계약을 체결했다고 6일 밝혔다.">'
    )

    text, stage = extract_article_text(html, primary_extract=_primary)

    assert text == "가나다전자가 고객사와 공급 계약을 체결했다고 6일 밝혔다."
    assert stage == c.BODY_STAGE_META_DESCRIPTION


def test_너무_짧은_글자는_본문으로_받지_않는다() -> None:
    html = f'<meta name="description" content="{"짧" * (c.BODY_MIN_CHARS - 1)}">'

    assert extract_article_text(html, primary_extract=_primary) == ("", "")


def test_어느_겹에서도_못_얻으면_빈_결과다() -> None:
    assert extract_article_text("<html></html>", primary_extract=_primary) == ("", "")


@pytest.mark.parametrize(
    ("stage", "html"),
    [
        (
            c.BODY_STAGE_JSON_LD,
            '<script type="application/ld+json">'
            '{"@type":"NewsArticle","articleBody":"가나다전자가 제품을 공개했다고 밝혔다."}'
            "</script>",
        ),
        (
            c.BODY_STAGE_ARTICLE_TAG,
            "<article><p>가나다전자가 설비를 도입했다고 6일 밝혔다.</p></article>",
        ),
        (
            c.BODY_STAGE_META_DESCRIPTION,
            '<meta property="og:description" content="가나다전자가 계약을 체결했다고 밝혔다.">',
        ),
    ],
)
def test_음성대조_그_겹을_순서에서_빼면_본문을_못_얻는다(
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    html: str,
) -> None:
    """폴백 사다리의 정본은 ``BODY_EXTRACTION_STAGE_ORDER``다.

    한 겹을 순서에서 빼면 그 픽스처는 곧바로 본문 0자가 되어야 한다.
    그렇지 않다면 그 겹이 실제로는 일을 하지 않고 있었다는 뜻이다.
    """

    얻은본문, 얻은단계 = extract_article_text(html, primary_extract=_primary)
    assert (얻은본문, 얻은단계) != ("", "")
    assert 얻은단계 == stage

    monkeypatch.setattr(
        c,
        "BODY_EXTRACTION_STAGE_ORDER",
        tuple(item for item in c.BODY_EXTRACTION_STAGE_ORDER if item != stage),
    )

    assert extract_article_text(html, primary_extract=_primary) == ("", "")


# ----------------------------------------------------------- 해독 건강도


def test_정상_글자는_해독_깨짐이_아니다() -> None:
    assert decode_looks_broken("가나다전자가 새 제품군을 공개했다.") is False
    assert decode_looks_broken("") is False


def test_cp949_문서를_utf_8로_읽으면_해독_깨짐으로_본다() -> None:
    """실제 전송 계층과 같은 방식(``errors='replace'``)으로 잘못 해독해 본다."""

    원문 = "가나다전자가 고객사와 공급 계약을 체결했다고 6일 밝혔다." * 3
    깨진글자 = 원문.encode("cp949").decode("utf-8", errors="replace")

    assert decode_looks_broken(깨진글자) is True


def test_대체문자가_한두_개면_깨짐으로_보지_않는다() -> None:
    긴글 = "가나다전자가 새 제품군을 공개했다고 밝혔다. " * 20

    assert decode_looks_broken(긴글 + "�") is False


# --------------------------------------------------- 사유 코드·결과 정규화


def test_상태별_사유코드는_상태를_그대로_담는다() -> None:
    assert http_status_code(403) == f"{c.FETCH_HTTP_CODE_PREFIX}403"
    assert http_status_code(429) == f"{c.FETCH_HTTP_CODE_PREFIX}429"


def test_사유를_모르는_주입_함수의_실패는_예전_코드로_센다() -> None:
    assert normalize_body_result(None).reason_code == c.EXCLUDED_FETCH_FAILED
    assert normalize_body_result("   ").reason_code == c.EXCLUDED_FETCH_FAILED


def test_글자만_돌려주는_주입_함수는_주입_단계로_남는다() -> None:
    result = normalize_body_result("가나다전자가 제품을 공개했다.")

    assert result.succeeded
    assert result.stage == c.BODY_STAGE_PROVIDED


def test_수집기가_준_결과는_그대로_통과한다() -> None:
    original = NewsBodyFetchResult(
        text="가나다전자가 제품을 공개했다.", stage=c.BODY_STAGE_JSON_LD
    )

    assert normalize_body_result(original) is original


def test_본문_결과는_글자와_사유를_동시에_담을_수_없다() -> None:
    with pytest.raises(ValueError):
        NewsBodyFetchResult(text="본문", reason_code=c.EXCLUDED_FETCH_EMPTY_BODY)
    with pytest.raises(ValueError):
        NewsBodyFetchResult()
    with pytest.raises(ValueError):
        NewsBodyFetchResult(text="본문")


# ------------------------------------------- 읽은 주소로 출처 다시 묶기


def test_같은_주소면_후보를_그대로_둔다() -> None:
    candidate = _candidate(source_url="https://media.example/1")

    assert (
        rebind_candidate_to_url(candidate, "https://media.example/1") is candidate
    )


def test_다른_주소에서_읽었으면_출처와_발행처가_그_주소로_바뀐다() -> None:
    """근거의 계약은 「이 글자는 이 주소의 문서에서 나왔다」이다.

    다른 데서 읽고 언론사 원문만 적으면 그 주소에 없는 문장을 가리키게 된다.
    """

    candidate = _candidate(source_url="https://media.example/1")

    바뀐후보 = rebind_candidate_to_url(candidate, "https://search.example/read/1")

    assert 바뀐후보.source_url == "https://search.example/read/1"
    assert 바뀐후보.publisher == "search.example"
    assert 바뀐후보.id == candidate.id
    assert 바뀐후보.title == candidate.title


def test_주소를_정규화하지_못하면_후보를_건드리지_않는다() -> None:
    candidate = _candidate(source_url="https://media.example/1")

    assert rebind_candidate_to_url(candidate, "javascript:alert(1)") is candidate
    assert rebind_candidate_to_url(candidate, "") is candidate


def test_다시_묶은_주소는_정규화된_꼴이다() -> None:
    """조각의 문서 신원이 정규화 주소로 만들어지므로 여기서도 맞춰야 한다."""

    candidate = _candidate(source_url="https://media.example/1")

    바뀐후보 = rebind_candidate_to_url(
        candidate, "https://search.example/read/1/?utm_source=x#top"
    )

    assert 바뀐후보.source_url == "https://search.example/read/1"
