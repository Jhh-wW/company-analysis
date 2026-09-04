"""홈페이지 수집기에 «어떤 주소»를 넘기는지 지키는 시험.

★ 왜 이 파일이 따로 있나 — 같은 곳에서 사고가 났다.

  사고: `workable_url()`(실제로 열어 보고 열리는 주소를 주는 함수)의 결과를
  **조건 없이** 수집기에 넘겼더니, 루트에서 다른 host로 튕기는 회사에서
  **남의 회사 사이트가 「이 회사 공식 웹」으로** 들어왔다. 게다가 그 조각에
  후보출처검증="https_exact_dart_host"(= DART가 적은 host와 정확히 같음) 도장까지
  찍혔다 — 거짓말이 근거로 박힌 것이다.

  그때 시험 180개가 **전부 초록불이었다.** cross-host를 지키는 못이 0개였다.
  이 파일이 그 못이다.

실측 — 회사 7곳의 hm_url을 실제로 `workable_url`에 넣은 결과:
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

★ 적대 검수 — 잠금이 **조각 수집 한 곳에만** 걸려 있었다.
  같은 `workable_url()`을 부르는 **화면 경로 3곳**(후보 목록·확인 카드 2개)은
  잠금을 건너뛰고 있었다. 그래서 사용자는 후보 목록에서 **남의 회사 주소가
  글자 그대로 인쇄된 화면을 보고 회사를 골랐다.** §3이 그 못이다.

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
    결과 = real._homepage_url_same_host_only(raw)
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

    assert real._homepage_url_same_host_only(raw) == raw
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


# ══════════════════════════════════════════════════════════
# 3. ★ 못 — 화면 경로 3곳도 같은 잠금을 거치는가
#    (수집기만 잠그면 사용자는 «남의 회사 주소»를 보고 회사를 고른다)
# ══════════════════════════════════════════════════════════

#: 후보 검색용 1곳짜리 회사 목록. tuple 하나를 재사용해야 후보 색인이 매번
#: 다시 만들어지지 않는다 — `real._company_candidate_index`가 tuple 자체를
#: 열쇠로 캐시하기 때문이다.
_카탈로그 = ((CORP_ID, "가나다전자", "", "000001", "20260819"),)

#: 사고 재현값 — 현대차 루트는 남의 회사(hyundaimotorgroup.com)로 튕긴다.
_사고_원본 = "www.hyundai.co.kr"
_사고_리다이렉트 = "https://www.hyundaimotorgroup.com/ko/main/mainRecommend"
#: 잠금을 통과하면 화면에는 DART 원본이 «링크로 걸 수 있는 모양»으로 남는다.
_사고_기대값 = "https://www.hyundai.co.kr"


def _가짜_기업개황(hm_url: str) -> dict[str, Any]:
    """DART 기업개황 응답 모양. 화면 3곳이 전부 이 dict만 보고 카드를 만든다."""
    return {
        "status": "000",
        "corp_code": CORP_ID,
        "corp_name": "가나다전자",
        "adres": "서울특별시 강남구 테헤란로 1",
        "ceo_nm": "홍길동",
        "est_dt": "20000101",
        "hm_url": hm_url,
    }


def _화면_준비(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
    *,
    hm_url: str,
    workable: str,
) -> None:
    """DART가 `hm_url`을 주고, 접속하면 `workable`로 튕긴다고 치고 판을 깐다."""
    monkeypatch.setattr(real.homepage_link, "workable_url", lambda _raw: workable)
    monkeypatch.setattr(engine, "get_json", lambda *_a, **_k: _가짜_기업개황(hm_url))


def _사용자입력() -> UserInput:
    return UserInput(
        company="가나다전자",
        job=JOB,
        region="서울 강남구",
        posting_text=POSTING,
    )


def _후보목록_홈페이지(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
    *,
    hm_url: str,
    workable: str,
) -> str:
    """후보 목록 빌더를 **실제로 돌려** 화면에 인쇄될 「homepage」를 돌려준다.

    `company_candidates.html`은 이 값을 링크 주소로도 쓰고 **눈에 보이는 글자로도
    그대로 인쇄**한다.
    """
    _화면_준비(engine, monkeypatch, hm_url=hm_url, workable=workable)
    monkeypatch.setattr(real, "_company_catalog", lambda: _카탈로그)
    rows = real.RealPipeline().search_business_candidates(
        company="가나다전자", address_hint="서울 강남구", limit=3, timeout_sec=8.0
    )
    assert len(rows) == 1, f"후보가 1개가 아니라 {len(rows)}개입니다"
    return str(rows[0]["homepage"])


def _확인카드_고유번호_홈페이지(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
    *,
    hm_url: str,
    workable: str,
) -> str:
    """사람이 후보를 고른 뒤 다시 확인하는 경로(`find_company_by_ref_metered`)."""
    _화면_준비(engine, monkeypatch, hm_url=hm_url, workable=workable)
    결과 = real.RealPipeline().find_company_by_ref_metered(_사용자입력(), CORP_ID)
    assert 결과.card is not None, "확인 카드를 만들지 못했습니다"
    return 결과.card.homepage_url


def _확인카드_이름식별_홈페이지(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
    *,
    hm_url: str,
    workable: str,
) -> str:
    """이름으로 바로 확인 카드를 만드는 경로(`find_company_metered`)."""
    _화면_준비(engine, monkeypatch, hm_url=hm_url, workable=workable)
    # `_company_index`는 `@lru_cache`라 진짜를 부르면 다른 시험과 값을 나눠 쓴다.
    monkeypatch.setattr(real, "_company_index", lambda: {})
    monkeypatch.setattr(engine, "identify", lambda *_a, **_k: CORP_ID, raising=False)
    결과 = real.RealPipeline().find_company_metered(_사용자입력())
    assert 결과.card is not None, "확인 카드를 만들지 못했습니다"
    return 결과.card.homepage_url


#: 사용자가 실제로 보는 화면 3곳. 셋 다 같은 잠금을 거쳐야 한다.
_화면경로 = [
    pytest.param(_후보목록_홈페이지, id="후보목록"),
    pytest.param(_확인카드_고유번호_홈페이지, id="확인카드_고유번호"),
    pytest.param(_확인카드_이름식별_홈페이지, id="확인카드_이름식별"),
]


@pytest.mark.parametrize("화면", _화면경로)
def test_화면_3곳은_남의_회사로_튕겨도_DART_원본만_보여준다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch, 화면: Any
) -> None:
    """★ 사고를 막는 못 — 이게 빨간불이면 남의 회사 주소가 화면에 인쇄된다.

    후보 목록은 이 글자를 **눈에 보이는 글자로도** 찍는다. 사용자는 그 글자를
    보고 회사를 «고른다» — 판단 근거가 통째로 오염된다.
    """
    보인_주소 = 화면(engine, monkeypatch, hm_url=_사고_원본, workable=_사고_리다이렉트)
    assert (
        "hyundaimotorgroup" not in 보인_주소
    ), f"남의 회사 host가 화면으로 새어 나갔습니다: {보인_주소}"
    assert 보인_주소 == _사고_기대값


@pytest.mark.parametrize("화면", _화면경로)
def test_화면_3곳은_같은_회사면_열리는_주소를_그대로_링크로_건다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch, 화면: Any
) -> None:
    """(주)진영 대역 — https 인증서가 죽어 http로 열린다. 회사는 그대로다.

    ★ 이게 깨지면 링크를 눌렀을 때 브라우저 경고창이 뜨고, 사용자는
      「이 회사가 맞나」를 의심한다 (실제로 벌어졌던 일이다).
    """
    assert (
        화면(
            engine,
            monkeypatch,
            hm_url="www.jyp21.co.kr",
            workable="http://www.jyp21.co.kr/en/",
        )
        == "http://www.jyp21.co.kr/en/"
    )


@pytest.mark.parametrize("화면", _화면경로)
def test_화면_3곳은_스킴없는_주소를_링크주소에_그대로_넣지_않는다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch, 화면: Any
) -> None:
    """★ DART 원본은 대개 "www.foo.co.kr"처럼 앞머리(스킴)가 없다.

    그 글자를 링크 주소에 그대로 넣으면 브라우저는 **우리 사이트 안의 상대
    경로**로 읽는다. 잠금이 원본을 돌려줄 때도 링크로 걸 수 있는 모양이어야 한다.
    """
    보인_주소 = 화면(engine, monkeypatch, hm_url=_사고_원본, workable=_사고_리다이렉트)
    assert 보인_주소.startswith(
        ("https://", "http://")
    ), f"링크로 걸 수 없는 모양입니다: {보인_주소}"


@pytest.mark.parametrize("화면", _화면경로)
def test_화면_3곳은_링크로_만들_수_없는_주소를_빈_문자열로_둔다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch, 화면: Any
) -> None:
    """빈 문자열이어야 화면이 링크 대신 「홈페이지 미확인」 글자만 보여 준다."""
    assert (
        화면(
            engine,
            monkeypatch,
            hm_url="javascript:alert(1)",
            workable="https://evil.example",
        )
        == ""
    )


def test_후보_점수도_남의_회사_도메인으로_흔들리지_않는다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 화면만의 문제가 아니다 — 이 값은 후보 «순서»에도 들어간다.

    `score_business_candidate(homepage=...)`는 도메인에 입력한 영문명이 있으면
    가점을 준다. 리다이렉트된 남의 host가 들어가면 남의 도메인으로 점수가 붙어
    후보 순서가 흔들린다.
    """
    from src.features.business_candidate import logic as 후보점수  # noqa: PLC0415

    진짜_점수 = 후보점수.score_business_candidate
    넘어간_홈페이지: list[str] = []

    def 스파이(**kwargs: Any) -> Any:
        넘어간_홈페이지.append(str(kwargs.get("homepage", "")))
        return 진짜_점수(**kwargs)

    monkeypatch.setattr(후보점수, "score_business_candidate", 스파이)

    _후보목록_홈페이지(engine, monkeypatch, hm_url=_사고_원본, workable=_사고_리다이렉트)

    assert 넘어간_홈페이지 == [
        _사고_기대값
    ], f"점수 계산에 넘어간 홈페이지가 잘못됐습니다: {넘어간_홈페이지}"


# ── 잠금 껍데기 자체 ──────────────────────────────────────


def test_화면용_주소는_잠금을_통과한_뒤_링크_모양으로_바뀐다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_homepage_url_for_display`가 하는 일은 «잠금 + 링크 모양» 둘뿐이다."""
    monkeypatch.setattr(
        real.homepage_link, "workable_url", lambda _raw: _사고_리다이렉트
    )
    assert real._homepage_url_for_display(_사고_원본) == _사고_기대값

    monkeypatch.setattr(
        real.homepage_link, "workable_url", lambda _raw: "http://www.jyp21.co.kr/en/"
    )
    assert (
        real._homepage_url_for_display("www.jyp21.co.kr")
        == "http://www.jyp21.co.kr/en/"
    )


def test_수집기는_화면과_달리_DART_원본_글자를_그대로_받는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 둘을 같은 값으로 만들면 안 된다 — 수집기 쪽은 원본 글자가 필요하다.

    `collect_homepage_fragments(allow_dart_www_alias=True)`가 DART가 적은 글자
    그대로를 기준으로 www 이동을 증명하고 조각에 흔적을 남긴다. 화면용으로
    앞머리를 붙여 버리면 그 기준이 달라진다.
    """
    monkeypatch.setattr(
        real.homepage_link, "workable_url", lambda _raw: _사고_리다이렉트
    )
    assert real._homepage_url_same_host_only(_사고_원본) == _사고_원본
    assert real._homepage_url_for_display(_사고_원본) != _사고_원본
