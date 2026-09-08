"""기사 본문 수집의 실패 사유 세분화와 주소·본문 폴백 (네트워크 없음).

가짜 전송 계층만 끼워 넣고 실제 접속은 하지 않는다. 사유 코드 문자열은
운영 로그에 그대로 찍히는 값이라 **리터럴로** 적는다 — 생산 상수를 그대로
가져다 쓰면 이름이 바뀌어도 시험이 초록불이라 회귀를 못 잡는다.
"""

from __future__ import annotations

import itertools
import logging
from urllib import robotparser

import pytest

from src.features.homepage.constants import TIMEOUT_SEC as HOMEPAGE_TIMEOUT_SEC
from src.features.homepage.constants import USER_AGENT
from src.features.homepage.wide_fetch import (
    WideRawResponse,
    WideRobotsPolicy,
    WideTransportError,
)
from src.features.news_intake import constants as news_c
from src.features.pipeline import real


ARTICLE_URL = "https://www.media.example/news/1"
ARTICLE_HTML = (
    "<html><head><title>기사</title></head><body><article><p>"
    "가나다전자가 고객 업무를 잇는 새 제품군을 공개했다고 6일 밝혔다. "
    "회사는 하반기에 공급을 시작한다고 덧붙였다."
    "</p></article></body></html>"
)


def _policy(*, blocked: bool = False, robots_text: str = "") -> WideRobotsPolicy:
    parser = robotparser.RobotFileParser()
    parser.parse(robots_text.splitlines())
    return WideRobotsPolicy(
        host="www.media.example",
        parser=parser,
        outcome="blocked" if blocked else "proceed_parsed",
        reason_code="robots_unreachable" if blocked else "robots_ok",
    )


def _response(*, status: int = 200, text: str = ARTICLE_HTML) -> WideRawResponse:
    return WideRawResponse(
        status=status,
        text=text,
        effective_url=ARTICLE_URL,
        content_type="text/html",
    )


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    transport,
    policy_for=lambda _robots_url: _policy(),
) -> list[str]:
    """robots·전송을 가짜로 갈아 끼우고 «요청한 주소 순서»를 돌려준다."""

    requested: list[str] = []

    def fake_policy(*, robots_url: str, host: str, fetch, url_allowed):
        return policy_for(robots_url)

    def fake_transport(url: str, url_allowed=None):
        requested.append(url)
        return transport(url)

    monkeypatch.setattr(real, "load_robots_policy", fake_policy)
    monkeypatch.setattr(real, "default_wide_transport", fake_transport)
    return requested


# ------------------------------------------------------- 사유 코드 세분화


def test_공개_웹_주소가_아니면_origin_denied로_남는다() -> None:
    결과 = real._fetch_news_article_text("file:///etc/passwd")

    assert 결과.succeeded is False
    assert 결과.reason_code == "fetch_origin_denied"


def test_실제_읽은_주소와_기사_발행일을_근거_수집에_전달한다(monkeypatch):
    html = ARTICLE_HTML.replace(
        "</head>",
        '<meta property="article:published_time" content="2026-09-07T09:00:00+09:00"></head>',
    )
    _wire(monkeypatch, transport=lambda _url: _response(text=html))

    result = real._fetch_news_article_text(ARTICLE_URL)

    assert result.succeeded
    assert result.effective_url == ARTICLE_URL
    assert result.published_on == "2026-09-07"


def test_robots가_막으면_요청조차_하지_않고_robots_blocked로_남는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    요청 = _wire(
        monkeypatch,
        transport=lambda _url: _response(),
        policy_for=lambda _robots_url: _policy(blocked=True),
    )

    결과 = real._fetch_news_article_text(ARTICLE_URL)

    assert 결과.reason_code == "fetch_robots_blocked"
    assert 요청 == []


def test_robots가_경로를_막아도_robots_blocked로_남는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    요청 = _wire(
        monkeypatch,
        transport=lambda _url: _response(),
        policy_for=lambda _robots_url: _policy(
            robots_text=f"User-agent: {USER_AGENT}\nDisallow: /news/"
        ),
    )

    결과 = real._fetch_news_article_text(ARTICLE_URL)

    assert 결과.reason_code == "fetch_robots_blocked"
    assert 요청 == []


@pytest.mark.parametrize("status", [403, 404, 429, 500])
def test_비200_응답은_상태를_그대로_담은_사유가_된다(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    _wire(monkeypatch, transport=lambda _url: _response(status=status))

    결과 = real._fetch_news_article_text(ARTICLE_URL)

    assert 결과.reason_code == f"fetch_http_{status}"


def test_200인데_본문이_비면_empty_body로_남는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    빈문서 = "<html><body></body></html>"
    _wire(monkeypatch, transport=lambda _url: _response(text=빈문서))

    결과 = real._fetch_news_article_text(ARTICLE_URL)

    assert 결과.reason_code == "fetch_empty_body"


def test_해독이_깨진_응답은_조각을_만들지_않고_decode_error로_남는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    원문 = "가나다전자가 고객사와 공급 계약을 체결했다고 6일 밝혔다." * 4
    깨진글자 = 원문.encode("cp949").decode("utf-8", "replace")
    깨진html = f"<article><p>{깨진글자}</p></article>"
    _wire(monkeypatch, transport=lambda _url: _response(text=깨진html))

    결과 = real._fetch_news_article_text(ARTICLE_URL)

    assert 결과.reason_code == "fetch_decode_error"
    assert 결과.text == ""


def test_시간_초과는_그_밖의_전송_실패와_따로_센다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def 시간초과(_url: str):
        raise WideTransportError("응답 시간이 초과됐습니다") from TimeoutError()

    _wire(monkeypatch, transport=시간초과)

    assert real._fetch_news_article_text(ARTICLE_URL).reason_code == "fetch_timeout"


def test_시간_제한만큼_걸렸으면_원인_없이도_시간_초과로_본다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """전송 계층은 마감 초과를 «원인 없는» 예외로 던지는 경로가 있다."""

    시각 = itertools.cycle([0.0, float(real.NEWS_BODY_FETCH_TIMEOUT_SEC)])
    monkeypatch.setattr(real.time, "monotonic", lambda: next(시각))

    def 마감초과(_url: str):
        raise WideTransportError("응답 시간이 초과됐습니다")

    _wire(monkeypatch, transport=마감초과)

    assert real._fetch_news_article_text(ARTICLE_URL).reason_code == "fetch_timeout"


def test_연결_실패는_transport_error로_남는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def 연결거부(_url: str):
        raise WideTransportError("ConnectionRefusedError: 거부") from (
            ConnectionRefusedError()
        )

    _wire(monkeypatch, transport=연결거부)

    결과 = real._fetch_news_article_text(ARTICLE_URL)

    assert 결과.reason_code == "fetch_transport_error"


def test_본문_시간제한_상수는_전송_계층이_실제로_쓰는_값과_같다() -> None:
    """두 값이 갈라지면 「시간 초과」 판정이 조용히 틀어진다."""

    assert real.NEWS_BODY_FETCH_TIMEOUT_SEC == HOMEPAGE_TIMEOUT_SEC


# ------------------------------------------------------------- 주소 폴백


def test_http_주소는_https_정식표기로_다시_요청해_되살린다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실측 원인: ``http://``는 언론사가 301로 https에 돌려보내는데,
    같은 origin만 허용하는 리다이렉트 방어가 그 경로를 막아 robots 확인
    단계부터 실패한다. 우회가 아니라 «정식 주소로 새로 요청»해서 푼다.
    """

    def transport(url: str):
        if url.startswith("http://"):
            raise WideTransportError("UnsafeHomepageUrlError: 허용 규칙이 막았습니다")
        return _response()

    요청 = _wire(monkeypatch, transport=transport)

    결과 = real._fetch_news_article_text("http://www.media.example/news/1")

    assert 결과.succeeded is True
    assert 요청 == [
        "http://www.media.example/news/1",
        "https://www.media.example/news/1",
    ]


def test_www_없는_주소는_www_표기로_한_번_더_시도한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def transport(url: str):
        if "://www." not in url:
            return _response(status=301)
        return _response()

    요청 = _wire(monkeypatch, transport=transport)

    결과 = real._fetch_news_article_text("http://media.example/news/1")

    assert 결과.succeeded is True
    assert 요청[-1] == "https://www.media.example/news/1"


def test_변형이_모두_실패하면_첫_주소의_사유를_돌려준다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    상태 = {"http://media.example/news/1": 403}

    def transport(url: str):
        return _response(status=상태.get(url, 404))

    _wire(monkeypatch, transport=transport)

    결과 = real._fetch_news_article_text("http://media.example/news/1")

    assert 결과.reason_code == "fetch_http_403"


def test_robots가_막은_사이트를_다른_표기로_우회하지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """변형마다 그 origin의 robots.txt를 «새로» 확인한다.

    전부 막혀 있으면 어떤 표기로도 본문을 못 가져와야 한다.
    """

    요청 = _wire(
        monkeypatch,
        transport=lambda _url: _response(),
        policy_for=lambda _robots_url: _policy(blocked=True),
    )

    결과 = real._fetch_news_article_text("http://media.example/news/1")

    assert 결과.reason_code == "fetch_robots_blocked"
    assert 요청 == []


def test_음성대조_주소_변형_순서를_비우면_http_주소를_못_살린다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """주소 변형의 정본은 ``URL_VARIANT_ORDER``다.

    ``https_upgrade``·``www_toggle``을 빼면 http 주소는 예전처럼 그대로
    실패해야 한다 — 그렇지 않다면 그 변형이 일을 하지 않았다는 뜻이다.
    """

    def transport(url: str):
        if url.startswith("http://"):
            raise WideTransportError("UnsafeHomepageUrlError: 허용 규칙이 막았습니다")
        return _response()

    monkeypatch.setattr(
        news_c, "URL_VARIANT_ORDER", (news_c.URL_VARIANT_AS_GIVEN,)
    )
    요청 = _wire(monkeypatch, transport=transport)

    결과 = real._fetch_news_article_text("http://www.media.example/news/1")

    assert 결과.succeeded is False
    assert 요청 == ["http://www.media.example/news/1"]


# ------------------------------------------------------------- 본문 폴백


def test_본문_구간이_비어도_메타_설명_한_문장으로_되살린다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = (
        "<html><head>"
        '<meta property="og:description" '
        'content="가나다전자가 고객사와 공급 계약을 체결했다고 밝혔다.">'
        "</head><body><div>더보기</div></body></html>"
    )
    _wire(monkeypatch, transport=lambda _url: _response(text=html))

    결과 = real._fetch_news_article_text(ARTICLE_URL)

    assert 결과.succeeded is True
    assert 결과.stage == "meta_description"
    assert 결과.text == "가나다전자가 고객사와 공급 계약을 체결했다고 밝혔다."


def test_본문_구간을_읽었으면_첫_겹으로_남는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    긴본문 = "가나다전자가 고객 업무를 잇는 새 제품군을 공개했다고 6일 밝혔다." * 2
    긴html = f"<html><body><p>{긴본문}</p></body></html>"
    _wire(monkeypatch, transport=lambda _url: _response(text=긴html))

    결과 = real._fetch_news_article_text(ARTICLE_URL)

    assert 결과.succeeded is True
    assert 결과.stage == "usable_ranges"


def test_음성대조_본문_폴백을_모두_끄면_구간이_빈_기사는_실패한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = (
        '<meta property="og:description" '
        'content="가나다전자가 고객사와 공급 계약을 체결했다고 밝혔다.">'
    )
    monkeypatch.setattr(
        news_c,
        "BODY_EXTRACTION_STAGE_ORDER",
        (news_c.BODY_STAGE_USABLE_RANGES,),
    )
    _wire(monkeypatch, transport=lambda _url: _response(text=html))

    assert real._fetch_news_article_text(ARTICLE_URL).reason_code == "fetch_empty_body"


# ----------------------------------------------- 총괄 실패 코드와 로그


@pytest.mark.parametrize(
    ("제외", "기대"),
    [
        ({"fetch_robots_blocked": 3, "fetch_empty_body": 1}, "fetch_robots_blocked"),
        ({"fetch_empty_body": 2, "fetch_robots_blocked": 2}, "fetch_empty_body"),
        ({"fetch_http_403": 2, "fetch_robots_blocked": 2}, "fetch_http_403"),
        ({"stock_article": 5}, None),
        ({}, None),
    ],
)
def test_총괄_실패_코드는_가장_많은_본문_사유_하나다(
    제외: dict[str, int],
    기대: str | None,
) -> None:
    assert real._dominant_news_body_failure(제외) == 기대


def test_사유_요약에는_주소도_원문도_담기지_않는다() -> None:
    요약 = real._news_reason_summary(
        {"fetch_http_403": 2, "fetch_empty_body": 1, "무시": 0}
    )

    assert 요약 == "fetch_empty_body:1,fetch_http_403:2"
    assert "http://" not in 요약 and "https://" not in 요약


def test_총괄_로그에_사유별_수가_함께_남는다(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger(real.__name__)
    with caplog.at_level(logging.WARNING, logger=real.__name__):
        logger.warning(
            "언론 보조 근거 수집 일부를 완료하지 못했습니다 code=%s reasons=%s",
            "fetch_empty_body",
            real._news_reason_summary({"fetch_empty_body": 2}),
        )

    기록 = caplog.text
    assert "code=fetch_empty_body" in 기록
    assert "reasons=fetch_empty_body:2" in 기록
