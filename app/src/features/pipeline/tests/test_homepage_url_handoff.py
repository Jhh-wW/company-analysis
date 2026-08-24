"""홈페이지 수집기에 «어떤 주소»를 넘기는지 지키는 시험.

★ 왜 이 파일이 따로 있나 — 2026-08-25에 같은 곳에서 사고가 났다.

  사고: `workable_url()`(실제로 열어 보고 열리는 주소를 주는 함수)의 결과를
  **조건 없이** 수집기에 넘겼더니, 루트에서 다른 host로 튕기는 회사에서
  **남의 회사 사이트가 「이 회사 공식 웹」으로** 들어왔다. 게다가 그 조각에
  후보출처검증="https_exact_dart_host"(= DART가 적은 host와 정확히 같음) 도장까지
  찍혔다 — 거짓말이 근거로 박힌 것이다.

  그때 시험 180개가 **전부 초록불이었다.** cross-host를 지키는 못이 0개였다.
  이 파일이 그 못이다.

실측(2026-08-25) — 회사 7곳의 hm_url을 실제로 `workable_url`에 넣은 결과:
  www.jyp21.co.kr     -> http://www.jyp21.co.kr/en/                              같음
  www.hyundai.co.kr   -> https://www.hyundaimotorgroup.com/ko/main/mainRecommend 다름
  www.hyosung.co.kr   -> https://www.hyosung.com/kr/index                        다름
  www.hanjin.co.kr    -> https://www.hanjin.com:443/kor/Main.do                  다름
  www.samsung.com/sec -> https://www.samsung.com/sec/                            같음
  www.jype.com        -> https://www.jype.com                                    같음
  hybecorp.com        -> https://hybecorp.com                                    같음

같은 날 `collect_homepage_fragments`까지 실제로 돌린 결과:
  진영  전(오늘) state=failed 조각 0개 (robots.txt 확인 실패, 자체서명 인증서)
        후(변경) state=ok     조각 5개, 출처host 전부 www.jyp21.co.kr
  현대  전(오늘) state=failed "안전 정책 위반: 공식 IR URL 허용 규칙이 이 경로를 차단"
        후(변경) **전과 완전히 같음**
        만약 조건이 없었다면 state=ok 조각 6개 — 출처host www.hyundaimotorgroup.com,
        게다가 6개 전부 후보출처검증="https_exact_dart_host" 도장이 찍혔다.

★ 이 파일은 네트워크에 나가지 않는다. `workable_url`을 가짜로 바꿔 끼워
  실측에서 본 «결과 모양»만 재현한다.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.features.pipeline import real
from src.features.pipeline.port import UserInput

# 가짜 엔진·회사목록 준비물은 파이프라인 시험이 이미 갖고 있다. 같은 것을 두 벌
# 만들면 한쪽만 고쳐져 조용히 어긋난다.
from src.features.pipeline.tests.test_real_cache import (  # noqa: F401
    CORP_ID,
    JOB,
    POSTING,
    FakeEngine,
    engine,  # pytest fixture — 이름 그대로 가져와야 쓸 수 있다
)

# ══════════════════════════════════════════════════════════
# 1. 주소 고르기 규칙 자체
# ══════════════════════════════════════════════════════════


def _고른다(monkeypatch: pytest.MonkeyPatch, raw: str, workable: str) -> str:
    """`workable_url`이 `workable`을 준다고 치고, 수집기에 넘길 주소를 돌려준다."""
    불린: list[str] = []

    def 가짜_workable_url(값: str) -> str:
        불린.append(값)
        return workable

    monkeypatch.setattr(real.homepage_link, "workable_url", 가짜_workable_url)
    결과 = real._homepage_url_for_collector(raw)
    # 원래 주소를 그대로 물려줘야 캐시가 회사 확인 화면과 같은 열쇠로 맞는다.
    assert 불린 in ([], [raw]), f"workable_url을 이상한 값으로 불렀습니다: {불린}"
    return 결과


def test_host가_같으면_열리는_주소를_쓴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """(주)진영 실측 — https 인증서가 죽어 있어 http로 열린다. 회사는 그대로다.

    ★ 이게 깨지면 홈페이지 조각이 0개가 되고 8장 인재상·경영철학 재료가 빈다.
    """
    assert (
        _고른다(
            monkeypatch,
            raw="www.jyp21.co.kr",
            workable="http://www.jyp21.co.kr/en/",
        )
        == "http://www.jyp21.co.kr/en/"
    )


def test_host가_다르면_원래_주소를_그대로_넘긴다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 사고 재현 — 현대차 루트는 hyundaimotorgroup.com 으로 튕긴다.

    이걸 채택하면 남의 회사 본문이 「이 회사 공식 웹」 조각으로 들어오고
    후보출처검증 도장까지 붙는다. 원래 주소를 그대로 넘겨야 한다.
    """
    assert (
        _고른다(
            monkeypatch,
            raw="www.hyundai.co.kr",
            workable="https://www.hyundaimotorgroup.com/ko/main/mainRecommend",
        )
        == "www.hyundai.co.kr"
    )


@pytest.mark.parametrize(
    ("raw", "workable"),
    [
        # 실측 3건 — 브랜드는 같아 보여도 host가 다르면 다른 사이트다.
        ("www.hyosung.co.kr", "https://www.hyosung.com/kr/index"),
        ("www.hanjin.co.kr", "https://www.hanjin.com:443/kor/Main.do"),
        ("www.hyundai.co.kr", "https://www.hyundaimotorgroup.com/ko/main"),
    ],
)
def test_실측_다른host_3건은_모두_원래_주소로_되돌아간다(
    monkeypatch: pytest.MonkeyPatch, raw: str, workable: str
) -> None:
    assert _고른다(monkeypatch, raw=raw, workable=workable) == raw


@pytest.mark.parametrize(
    ("raw", "workable"),
    [
        # 실측 4건 — 경로·꼬리 슬래시가 붙어도 host가 같으면 같은 사이트다.
        ("www.samsung.com/sec", "https://www.samsung.com/sec/"),
        ("www.jype.com", "https://www.jype.com"),
        ("hybecorp.com", "https://hybecorp.com"),
        ("www.jyp21.co.kr", "http://www.jyp21.co.kr/en/"),
    ],
)
def test_실측_같은host_4건은_모두_열리는_주소를_쓴다(
    monkeypatch: pytest.MonkeyPatch, raw: str, workable: str
) -> None:
    assert _고른다(monkeypatch, raw=raw, workable=workable) == workable


# ── 경계 사례 ────────────────────────────────────────────


def test_443포트가_붙어도_host가_같으면_채택한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """포트는 «어느 회사냐»가 아니라 «어떻게 연결하냐»다.

    실측에서 한진처럼 :443이 그대로 붙어 돌아온다. 포트까지 같이 비교하면
    같은 사이트를 다른 회사로 오판한다.
    """
    assert (
        _고른다(
            monkeypatch,
            raw="www.hanjin.co.kr",
            workable="https://www.hanjin.co.kr:443/kor/Main.do",
        )
        == "https://www.hanjin.co.kr:443/kor/Main.do"
    )


def test_스킴이_없는_원래_주소에서도_host를_제대로_뽑는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 함정 — `urlsplit("www.hanjin.com:443/kor")`은 host가 아니라
    **scheme을 "www.hanjin.com"으로 읽는다**(실측 확인).

    스킴 없는 `hm_url`에 포트가 붙어 있어도 host 비교가 무너지면 안 된다.
    """
    assert real._homepage_compare_host("www.hanjin.com:443/kor/Main.do") == (
        "www.hanjin.com"
    )
    assert (
        _고른다(
            monkeypatch,
            raw="www.hanjin.com:443/kor/Main.do",
            workable="https://www.hanjin.com/kor/Main.do",
        )
        == "https://www.hanjin.com/kor/Main.do"
    )


def test_대소문자와_끝점과_한글도메인은_같은_host로_본다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """host는 DNS 규칙상 대소문자를 안 가리고(RFC 4343), 끝의 "."은 같은 이름이다.

    한글 도메인은 punycode로 적으면 같은 이름이다.
    """
    assert (
        _고른다(
            monkeypatch,
            raw="WWW.Hybecorp.COM.",
            workable="https://www.hybecorp.com/ko",
        )
        == "https://www.hybecorp.com/ko"
    )
    assert real._homepage_compare_host("https://한글.kr") == "xn--bj0bj06e.kr"
    assert (
        _고른다(monkeypatch, raw="한글.kr", workable="https://xn--bj0bj06e.kr/about")
        == "https://xn--bj0bj06e.kr/about"
    )


def test_www_유무는_다른_host로_본다(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 일부러 이렇게 한다 — apex→www는 이미 «증거를 남기는» 다른 길이 있다.

    `collect_homepage_fragments(allow_dart_www_alias=True)`는 별도 probe로
    이동을 증명하고 조각에 이동 흔적(IR_DART_WWW_REDIRECT_*)을 남긴다.
    여기서 www를 말없이 같은 것으로 쳐 버리면 **그 흔적이 사라진 채로** 통과한다.
    """
    assert (
        _고른다(monkeypatch, raw="hyosung.co.kr", workable="https://www.hyosung.co.kr/")
        == "hyosung.co.kr"
    )
    assert (
        _고른다(monkeypatch, raw="www.hyosung.co.kr", workable="https://hyosung.co.kr/")
        == "www.hyosung.co.kr"
    )


def test_열리는_주소를_못_찾으면_원래_주소를_그대로_넘긴다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 빈 문자열을 넘기면 수집기가 state="none"(주소 없음)으로 답한다.

    그러면 «접속 실패»가 «자료 없음»으로 둔갑한다 — 「이 회사는 자료가 없다」는
    거짓 결론이 보고서에 들어간다.
    """
    assert (
        _고른다(monkeypatch, raw="https://www.down.example", workable="")
        == "https://www.down.example"
    )


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "javascript:alert(1)",
        "http://127.0.0.1",
        "https://user:pw@a.example",
    ],
)
def test_링크로_만들_수_없는_주소는_접속조차_시도하지_않는다(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    """host를 못 뽑으면 비교할 대상이 없다 — fail-closed로 원래 값을 그대로 둔다."""
    불린: list[str] = []

    def 가짜_workable_url(값: str) -> str:
        불린.append(값)
        return "https://evil.example"

    monkeypatch.setattr(real.homepage_link, "workable_url", 가짜_workable_url)

    assert real._homepage_url_for_collector(raw) == raw
    assert 불린 == [], f"열 수 없는 주소로 접속을 시도했습니다: {불린}"


# ══════════════════════════════════════════════════════════
# 2. ★ 못 — `_collect`가 정말 이 규칙을 거쳐서 수집기를 부르는가
#    (규칙만 있고 호출부가 안 쓰면 사고는 그대로 다시 난다)
# ══════════════════════════════════════════════════════════


def _수집기가_받은_홈페이지주소(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
    *,
    hm_url: str,
    workable: str,
) -> str:
    """`_collect`를 한 번 돌리고 홈페이지 수집기가 실제로 받은 주소를 돌려준다."""
    받은: list[str] = []

    monkeypatch.setattr(real, "_collect_news", lambda *_a, **_k: [])
    monkeypatch.setattr(real.homepage_link, "workable_url", lambda _raw: workable)
    monkeypatch.setattr(
        real,
        "collect_official_ir_fragments",
        lambda *_a, **_k: SimpleNamespace(
            state="none",
            fragments=[],
            detail="IR PDF 없음",
            attempted_documents=0,
            downloaded_pdf_bytes=0,
            candidate_scope_complete=True,
        ),
    )

    def 가짜_수집(url: str, **_kwargs: Any) -> SimpleNamespace:
        받은.append(url)
        return SimpleNamespace(
            state="none",
            fragments=[],
            detail="분석에 쓸 본문 없음",
            candidate_scope_complete=True,
        )

    monkeypatch.setattr(real, "collect_homepage_fragments", 가짜_수집)

    counter = engine.UsageCounter()
    financials, years = engine.fetch_financials(CORP_ID, counter)
    real._collect(
        engine,
        engine._client(),
        {
            "status": "000",
            "corp_code": CORP_ID,
            "corp_name": "가나다전자",
            "corp_name_eng": "GANADA ELECTRONICS CO., LTD.",
            "hm_url": hm_url,
        },
        UserInput(
            company="가나다전자",
            job=JOB,
            region="서울 강남구",
            posting_text=POSTING,
        ),
        counter,
        [],
        financials=financials,
        fin_years=years,
        filing=None,
    )
    assert len(받은) == 1, f"홈페이지 수집기를 {len(받은)}번 불렀습니다"
    return 받은[0]


def test_collect는_같은_회사일_때만_열리는_주소를_수집기에_넘긴다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(주)진영 대역 — host가 같으니 http로 열리는 주소가 그대로 수집기에 간다."""
    assert (
        _수집기가_받은_홈페이지주소(
            engine,
            monkeypatch,
            hm_url="www.jyp21.co.kr",
            workable="http://www.jyp21.co.kr/en/",
        )
        == "http://www.jyp21.co.kr/en/"
    )


def test_collect는_다른_회사로_튕기면_원래_hm_url을_수집기에_넘긴다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 사고를 막는 못 — 현대차 대역.

    이 시험이 빨간불이면 남의 회사 본문이 「이 회사 공식 웹」 조각으로 들어가고,
    거기에 후보출처검증="https_exact_dart_host" 도장이 찍힌다.
    """
    assert (
        _수집기가_받은_홈페이지주소(
            engine,
            monkeypatch,
            hm_url="www.hyundai.co.kr",
            workable="https://www.hyundaimotorgroup.com/ko/main/mainRecommend",
        )
        == "www.hyundai.co.kr"
    )
