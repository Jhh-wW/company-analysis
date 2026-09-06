from __future__ import annotations

import datetime as dt
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.core import news_intake_switch
from src.features.composer.port import filing_meta_from_raw
from src.features.news_intake import constants as news_constants
from src.features.news_intake.constants import (
    NON_EXTENDABLE_SECTIONS as NON_EXTENDABLE_SECTION_IDS,
)
from src.features.news_intake.models import NewsBodyFetchResult
from src.features.news_intake.select import news_trigger
from src.features.pipeline import real
from src.features.pipeline.evidence_transport import (
    RAW_EVIDENCE_COLLECTED_ON_KEY,
    RAW_EVIDENCE_DOCUMENT_CONTENT_SHA256_KEY,
    RAW_EVIDENCE_PUBLISHER_KEY,
    RAW_EVIDENCE_SECTION_IDS_KEY,
    RAW_EVIDENCE_SLOT_IDS_KEY,
)
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS
from src.shared.report_evidence.constants import (
    GenerationGateStatus,
    ReleaseMode,
    ReportExecutionOutcome,
    SOURCE_KIND_DART_BUSINESS_REPORT,
)
from src.shared.report_generation.models import exact_text_sha256
from src.features.pipeline.tests.test_official_evidence_runtime import (
    _Collector,
    _freeze_runtime,
    _official_result,
    _request,
    _wire_runtime,
    FakeEngine,
)


AS_OF = dt.date(2026, 9, 6)
ARTICLE_URL = "https://media.example/news/company-strategy"
ARTICLE_BODY = "가나다전자는 고객 업무를 잇는 새 제품군을 중심 사업으로 운영한다."
#: 기사당 조각 상한을 넘기려고 쓰는 본문 — 조건에 걸리지 않는 문장 네 개다.
MULTI_SENTENCE_ARTICLE_BODY = "\n".join(
    (
        "가나다전자는 고객 업무를 잇는 새 제품군을 중심 사업으로 운영한다.",
        "가나다전자는 물류 제품군을 현장에 적용한다.",
        "가나다전자는 상담 제품군을 함께 공급한다.",
        "가나다전자는 설비 제품군도 운영한다.",
    )
)
#: 증권 칼럼 시험용 기사 URL.
STOCK_COLUMN_URL = "https://media.example/news/weekend-money-column"
#: 공식 웹 문서가 「있다」는 쪽을 뜻하는 시험용 문서 수. 발동 문턱(0)을 넘는
#: 값이면 무엇이든 같은 뜻이라 경계 바로 위 값을 쓴다.
WEB_DOCUMENTS_PRESENT = 1
WEB_DOCUMENTS_ZERO = 0
#: 기본 창(1년) 밖·확장 창(3년) 안에 있는 기사 날짜. 어느 창으로 열렸는지
#: 출력 문자열이 아니라 실제 선별 결과로 가른다.
OLD_ARTICLE_URL = "https://media.example/news/company-old-release"
OLD_ARTICLE_PUBLISHED_ON = "2024-10-01"
#: DART 문서 신원은 접수번호 14자리와 URL 질의값이 맞아야 만들어진다.
DART_RECEIPT_BASE = 20260315000100
DART_DOCUMENT_URL_PREFIX = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="


@pytest.fixture(autouse=True)
def _reset_news_switch(monkeypatch: pytest.MonkeyPatch):
    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001
    monkeypatch.delenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, raising=False)
    yield
    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001


def _ready(*, portfolio: bool = False) -> dict[str, bool]:
    return {
        section_id: section_id != "portfolio" or portfolio
        for section_id in REQUIRED_EVIDENCE_SECTION_IDS
    }


def _item() -> SimpleNamespace:
    return SimpleNamespace(
        title="가나다전자 새 제품군 공개",
        originallink=ARTICLE_URL,
        link=ARTICLE_URL,
        description="가나다전자가 고객 업무를 잇는 제품군을 공개했다.",
        pubDate="2026-09-01",
    )


def _stock_column_item() -> SimpleNamespace:
    """증권 칼럼 한 건 — 연재 꼬리표와 종목 추천 어휘를 함께 가진 기사."""

    return SimpleNamespace(
        title="[머니플러스] 가나다전자 수혜주는",
        originallink=STOCK_COLUMN_URL,
        link=STOCK_COLUMN_URL,
        description="가나다전자를 두고 증권가가 종목을 꼽았다.",
        pubDate="2026-09-01",
    )


def _old_item() -> SimpleNamespace:
    """기본 창(1년) 밖, 확장 창(3년) 안에 있는 기사 한 건."""

    return SimpleNamespace(
        title="가나다전자 물류 자동화 계약 체결",
        originallink=OLD_ARTICLE_URL,
        link=OLD_ARTICLE_URL,
        description="가나다전자가 물류 자동화 계약을 체결했다.",
        pubDate=OLD_ARTICLE_PUBLISHED_ON,
    )


def _dart_only_official_result():
    """공식 웹 문서가 0건인 수집 결과 — 웹 문서를 DART 문서로 바꾼다.

    자바스크립트로 화면을 만드는 회사 홈페이지처럼 공식 웹에서 아무 문장도
    못 읽은 회사를 흉내 낸다. DART 문서는 접수번호와 URL이 서로 맞아야
    typed 신원이 만들어지므로 문서 ID·URL도 함께 바꾼다.
    """

    base = _official_result()
    web_document_ids = sorted(
        {
            document.document_id
            for candidate in base.candidates
            for document in candidate.documents
        }
    )
    receipts = {
        document_id: f"{DART_RECEIPT_BASE + index:014d}"
        for index, document_id in enumerate(web_document_ids)
    }

    def _as_dart(document):
        receipt = receipts[document.document_id]
        return replace(
            document,
            source_kind=SOURCE_KIND_DART_BUSINESS_REPORT,
            document_id=f"{SOURCE_KIND_DART_BUSINESS_REPORT}:{receipt}",
            canonical_url=f"{DART_DOCUMENT_URL_PREFIX}{receipt}",
        )

    candidates = tuple(
        replace(
            candidate,
            documents=tuple(_as_dart(document) for document in candidate.documents),
            fragments=tuple(
                replace(
                    fragment,
                    document_id=(
                        f"{SOURCE_KIND_DART_BUSINESS_REPORT}:"
                        f"{receipts[fragment.document_id]}"
                    ),
                )
                for fragment in candidate.fragments
            ),
        )
        for candidate in base.candidates
    )
    return replace(base, candidates=candidates)


def _result(
    *,
    state: str = "success",
    reason_code: str = "news_search_ok",
    items: list[object] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        state=state,
        reason_code=reason_code,
        items=[] if items is None else items,
        elapsed_ms=1,
    )


def _collect(
    *,
    search_news,
    classify=lambda _prompt: (
        '{"items":[{"id":"news-001","sections":["portfolio"],'
        '"kind":"press_release"}]}'
    ),
    fetch_text=lambda _url: ARTICLE_BODY,
    section_ready: dict[str, bool] | None = None,
    official_web_documents: int = WEB_DOCUMENTS_PRESENT,
):
    steps: list[dict[str, object]] = []
    fragments = real._collect_news_intake(
        search_news=search_news,
        classify=classify,
        fetch_text=fetch_text,
        company_name="가나다전자",
        company_aliases=("가나다",),
        company_domain="https://company.example",
        executive_names=("김대표",),
        corp_id="00123456",
        section_ready=section_ready or _ready(),
        official_web_documents=official_web_documents,
        as_of=AS_OF,
        collected_on=AS_OF.isoformat(),
        steps=steps,
    )
    return fragments, steps


def test_news_intake_wires_one_classification_and_typed_supplementary_fragment() -> None:
    calls = {"search": 0, "classify": 0, "fetch": 0}

    def search_news(_query: str, **kwargs: object) -> SimpleNamespace:
        calls["search"] += 1
        assert kwargs["display"] == real.NEWS_SEARCH_PAGE_SIZE
        assert kwargs["start"] in {1, 21}
        return _result(items=[_item()] if kwargs["start"] == 1 else [])

    def classify(prompt: str) -> str:
        calls["classify"] += 1
        assert "가나다전자 새 제품군 공개" in prompt
        return (
            '{"items":[{"id":"news-001","sections":["portfolio"],'
            '"kind":"press_release"}]}'
        )

    def fetch_text(url: str) -> str:
        calls["fetch"] += 1
        assert url == ARTICLE_URL
        return ARTICLE_BODY

    fragments, steps = _collect(
        search_news=search_news,
        classify=classify,
        fetch_text=fetch_text,
    )

    assert calls == {"search": 2, "classify": 1, "fetch": 1}
    assert len(fragments) == 1
    fragment = fragments[0]
    assert fragment["종류"] == "news"
    assert fragment["출처"] == ARTICLE_URL
    assert fragment["발행처"] == "media.example"
    assert fragment["문서명"] == "가나다전자 새 제품군 공개"
    assert fragment["문서일"] == "2026-09-01"
    assert fragment[RAW_EVIDENCE_SECTION_IDS_KEY] == ("portfolio",)
    assert fragment[RAW_EVIDENCE_SLOT_IDS_KEY]
    assert fragment[RAW_EVIDENCE_PUBLISHER_KEY] == "media.example"
    assert fragment[RAW_EVIDENCE_COLLECTED_ON_KEY] == AS_OF.isoformat()
    assert fragment[RAW_EVIDENCE_DOCUMENT_CONTENT_SHA256_KEY] == exact_text_sha256(
        ARTICLE_BODY
    )

    assert len(steps) == 1
    step = steps[0]
    assert step["step"] == "5b_뉴스_수집"
    assert step["창"] == "확장"
    assert step["검색"] == 1
    assert step["선별"] == 1
    assert step["분류AI호출"] == 1
    assert step["본문읽기"] == 1
    assert step["조각"] == 1
    assert step["실패"] is None
    assert step["검색호출"] == 2
    assert step["분류프롬프트글자"] > 0
    assert step["조각글자"] <= real.NEWS_FRAGMENT_TOTAL_CHARS_LIMIT


def test_news_intake_makes_no_calls_when_every_extendable_section_is_ready() -> None:
    def unexpected(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("뉴스 호출이 발생하면 안 됩니다")

    fragments, steps = _collect(
        search_news=unexpected,
        classify=unexpected,
        fetch_text=unexpected,
        section_ready=_ready(portfolio=True),
        official_web_documents=WEB_DOCUMENTS_PRESENT,
    )

    assert fragments == []
    assert steps[0]["창"] == real.NEWS_WINDOW_LABEL_DEFAULT
    assert steps[0]["검색호출"] == 0
    assert steps[0]["분류AI호출"] == 0
    assert steps[0]["본문읽기"] == 0


def test_zero_official_web_documents_open_news_though_every_section_is_ready() -> None:
    calls = {"search": 0, "classify": 0, "fetch": 0}

    def search_news(_query: str, **kwargs: object) -> SimpleNamespace:
        calls["search"] += 1
        return _result(items=[_item()] if kwargs["start"] == 1 else [])

    def classify(prompt: str) -> str:
        calls["classify"] += 1
        assert "가나다전자 새 제품군 공개" in prompt
        return (
            '{"items":[{"id":"news-001","sections":["portfolio"],'
            '"kind":"press_release"}]}'
        )

    def fetch_text(_url: str) -> str:
        calls["fetch"] += 1
        return ARTICLE_BODY

    fragments, steps = _collect(
        search_news=search_news,
        classify=classify,
        fetch_text=fetch_text,
        section_ready=_ready(portfolio=True),
        official_web_documents=WEB_DOCUMENTS_ZERO,
    )

    assert calls == {"search": 2, "classify": 1, "fetch": 1}
    assert len(fragments) == 1
    assert fragments[0]["종류"] == "news"
    step = steps[0]
    assert step["창"] == real.NEWS_WINDOW_LABEL_WEB_ZERO
    assert step["검색호출"] == real.NEWS_SEARCH_CALL_LIMIT
    assert step["분류AI호출"] == 1
    assert step["조각"] == 1
    assert step["실패"] is None


def test_web_zero_backfill_keeps_the_one_year_window() -> None:
    """웹 0건 보강은 기간을 늘리지 않는다 — 1년 밖 기사는 그대로 버린다."""

    def search_news(_query: str, **kwargs: object) -> SimpleNamespace:
        return _result(
            items=[_item(), _old_item()] if kwargs["start"] == 1 else []
        )

    _fragments, steps = _collect(
        search_news=search_news,
        section_ready=_ready(portfolio=True),
        official_web_documents=WEB_DOCUMENTS_ZERO,
    )

    step = steps[0]
    assert step["창"] == real.NEWS_WINDOW_LABEL_WEB_ZERO
    assert step["검색"] == 2
    assert step["선별"] == 1
    assert step["제외"][news_constants.EXCLUDED_OUTSIDE_WINDOW] == 1


def test_empty_section_keeps_the_three_year_window_even_with_zero_web_documents() -> None:
    """빈 장이 있으면 웹 0건이어도 기존 확장 창 그대로다."""

    def search_news(_query: str, **kwargs: object) -> SimpleNamespace:
        return _result(
            items=[_item(), _old_item()] if kwargs["start"] == 1 else []
        )

    _fragments, steps = _collect(
        search_news=search_news,
        section_ready=_ready(),
        official_web_documents=WEB_DOCUMENTS_ZERO,
    )

    step = steps[0]
    assert step["창"] == real.NEWS_WINDOW_LABEL_EXTENDED
    assert step["선별"] == 2
    assert news_constants.EXCLUDED_OUTSIDE_WINDOW not in step["제외"]


def test_only_chapter_five_and_six_gap_opens_news_in_the_default_window() -> None:
    """5·6장만 미달이면 공식 웹 문서가 있어도 뉴스를 열고, 기간은 1년이다.

    확장 창 판정은 5·6장을 보지 않아 「확장」이 아니고, 공식 웹 문서가 있으니
    「웹0건보강」도 아니다. 그래서 창은 「기본」이다.
    """

    ready = {
        section_id: section_id not in NON_EXTENDABLE_SECTION_IDS
        for section_id in REQUIRED_EVIDENCE_SECTION_IDS
    }

    def search_news(_query: str, **kwargs: object) -> SimpleNamespace:
        return _result(
            items=[_item(), _old_item()] if kwargs["start"] == 1 else []
        )

    fragments, steps = _collect(
        search_news=search_news,
        section_ready=ready,
        official_web_documents=WEB_DOCUMENTS_PRESENT,
    )

    step = steps[0]
    assert step["창"] == real.NEWS_WINDOW_LABEL_DEFAULT
    assert step["검색호출"] == real.NEWS_SEARCH_CALL_LIMIT
    assert step["분류AI호출"] == 1
    # 기간은 1년이므로 1년 밖 기사는 선별에서 빠진다.
    assert step["선별"] == 1
    assert step["제외"][news_constants.EXCLUDED_OUTSIDE_WINDOW] == 1
    # 대상은 5·6장뿐이라 portfolio로 분류된 기사는 자리를 얻지 못한다.
    assert fragments == []
    assert step["제외"][news_constants.EXCLUDED_READY_SECTION] == 1


def test_chapter_five_six_gap_with_zero_web_documents_is_the_default_window() -> None:
    """5·6장만 미달 + 공식 웹 0건 — 이유는 「미달」이므로 창은 「기본」이다.

    공식 웹 문서 수를 창 이름 판정에서 다시 읽으면 「웹0건보강」이 찍힌다.
    기간은 1년이므로 1년 밖 기사는 선별에서 빠져야 한다.
    """

    ready = {
        section_id: section_id not in NON_EXTENDABLE_SECTION_IDS
        for section_id in REQUIRED_EVIDENCE_SECTION_IDS
    }
    eligible, reason = news_trigger(ready, official_web_documents=WEB_DOCUMENTS_ZERO)

    assert eligible == frozenset(NON_EXTENDABLE_SECTION_IDS)
    assert reason == news_constants.NEWS_TRIGGER_UNREADY

    def search_news(_query: str, **kwargs: object) -> SimpleNamespace:
        return _result(items=[_item(), _old_item()] if kwargs["start"] == 1 else [])

    _fragments, steps = _collect(
        search_news=search_news,
        section_ready=ready,
        official_web_documents=WEB_DOCUMENTS_ZERO,
    )

    step = steps[0]
    assert step["창"] == real.NEWS_WINDOW_LABEL_DEFAULT
    assert step["검색호출"] == real.NEWS_SEARCH_CALL_LIMIT == 2
    assert step["선별"] == 1
    assert step["제외"][news_constants.EXCLUDED_OUTSIDE_WINDOW] == 1


def test_every_section_ready_with_zero_web_documents_is_the_web_zero_window() -> None:
    """미달 장이 하나도 없어야 「웹0건보강」이다."""

    ready = {section_id: True for section_id in REQUIRED_EVIDENCE_SECTION_IDS}
    eligible, reason = news_trigger(ready, official_web_documents=WEB_DOCUMENTS_ZERO)

    assert reason == news_constants.NEWS_TRIGGER_WEB_ZERO
    assert eligible

    def search_news(_query: str, **kwargs: object) -> SimpleNamespace:
        return _result(items=[_item()] if kwargs["start"] == 1 else [])

    _fragments, steps = _collect(
        search_news=search_news,
        section_ready=ready,
        official_web_documents=WEB_DOCUMENTS_ZERO,
    )

    assert steps[0]["창"] == real.NEWS_WINDOW_LABEL_WEB_ZERO


def test_extendable_section_gap_is_the_extended_window() -> None:
    """5·6장 밖의 장이 미달이면 창은 「확장」이고 3년 전 기사도 받는다."""

    ready = {
        section_id: section_id in NON_EXTENDABLE_SECTION_IDS
        for section_id in REQUIRED_EVIDENCE_SECTION_IDS
    }
    eligible, reason = news_trigger(ready, official_web_documents=WEB_DOCUMENTS_PRESENT)

    assert reason == news_constants.NEWS_TRIGGER_UNREADY
    # 9장은 미달이어도 대상이 아니다.
    assert "competitive_position" not in eligible

    def search_news(_query: str, **kwargs: object) -> SimpleNamespace:
        return _result(items=[_item(), _old_item()] if kwargs["start"] == 1 else [])

    _fragments, steps = _collect(
        search_news=search_news,
        section_ready=ready,
        official_web_documents=WEB_DOCUMENTS_PRESENT,
    )

    step = steps[0]
    assert step["창"] == real.NEWS_WINDOW_LABEL_EXTENDED
    assert step["선별"] == 2
    assert news_constants.EXCLUDED_OUTSIDE_WINDOW not in step["제외"]


def test_official_web_document_count_reads_only_official_web_kinds() -> None:
    """장마다 붙은 같은 문서를 한 번만 세고, 웹 종류만 센다."""

    # 아홉 장이 서로 다른 웹 문서를 하나씩 가진 결과.
    assert real._official_web_document_count(_official_result()) == 9
    # 같은 웹 문서 세 건을 아홉 장이 나눠 가진 결과 — 문서 수는 3이다.
    assert real._official_web_document_count(_official_result(document_count=3)) == 3
    assert real._official_web_document_count(_dart_only_official_result()) == 0
    assert real._official_web_document_count(None) == 0


def test_stock_column_article_is_dropped_before_the_classifier_is_called() -> None:
    """증권 칼럼은 선별에서 빠져 AI 분류에도 가지 않는다."""

    classify_calls = 0

    def classify(_prompt: str) -> str:
        nonlocal classify_calls
        classify_calls += 1
        return '{"items":[]}'

    def search_news(_query: str, **kwargs: object) -> SimpleNamespace:
        return _result(items=[_stock_column_item()] if kwargs["start"] == 1 else [])

    fragments, steps = _collect(search_news=search_news, classify=classify)

    assert fragments == []
    assert classify_calls == 0
    step = steps[0]
    assert step["검색"] == 1
    assert step["선별"] == 0
    assert step["제외"][news_constants.EXCLUDED_STOCK_ARTICLE] == 1


def test_chapter_nine_gap_alone_never_opens_the_news_path() -> None:
    """9장만 비어 있으면 뉴스를 아예 열지 않는다 — 검색조차 하지 않는다."""

    ready = {section_id: True for section_id in REQUIRED_EVIDENCE_SECTION_IDS}
    ready["competitive_position"] = False
    calls = {"search": 0}

    def search_news(_query: str, **_kwargs: object) -> SimpleNamespace:
        calls["search"] += 1
        return _result(items=[_item()])

    fragments, steps = _collect(
        search_news=search_news,
        section_ready=ready,
        official_web_documents=WEB_DOCUMENTS_PRESENT,
    )

    assert fragments == []
    assert calls["search"] == 0
    assert steps[0]["검색호출"] == 0
    assert steps[0]["분류AI호출"] == 0


def test_chapter_nine_is_refused_even_when_the_classifier_asks_for_it() -> None:
    """9장은 회사가 밝힌 것만 싣는 장이라 분류가 지목해도 자리를 주지 않는다."""

    ready = _ready()
    ready["competitive_position"] = False

    def search_news(_query: str, **kwargs: object) -> SimpleNamespace:
        return _result(items=[_item()] if kwargs["start"] == 1 else [])

    fragments, steps = _collect(
        search_news=search_news,
        classify=lambda _prompt: (
            '{"items":[{"id":"news-001","sections":["competitive_position"],'
            '"kind":"press_release"}]}'
        ),
        section_ready=ready,
    )

    assert fragments == []
    assert steps[0]["분류AI호출"] == 1
    assert steps[0]["제외"][news_constants.EXCLUDED_READY_SECTION] == 1


def test_one_article_gives_at_most_two_fragments() -> None:
    """조각 여섯 개 자리를 기사 한 건이 다 채우지 못한다."""

    def search_news(_query: str, **kwargs: object) -> SimpleNamespace:
        return _result(items=[_item()] if kwargs["start"] == 1 else [])

    fragments, steps = _collect(
        search_news=search_news,
        fetch_text=lambda _url: MULTI_SENTENCE_ARTICLE_BODY,
    )

    assert len(fragments) == news_constants.MAX_FRAGMENTS_PER_ARTICLE
    step = steps[0]
    assert step["조각"] == news_constants.MAX_FRAGMENTS_PER_ARTICLE
    assert step["제외"][news_constants.EXCLUDED_ARTICLE_FRAGMENT_LIMIT] == 2


def test_missing_news_credentials_are_a_normal_no_news_result() -> None:
    fragments, steps = _collect(
        search_news=lambda *_args, **_kwargs: _result(
            state="skipped",
            reason_code=real.NEWS_SEARCH_NOT_CONFIGURED_CODE,
        )
    )

    assert fragments == []
    assert steps[0]["실패"] is None
    assert steps[0]["검색호출"] == 1
    assert steps[0]["분류AI호출"] == 0


@pytest.mark.parametrize(
    "reason_code",
    [
        "news_search_authentication_failed",
        "news_search_rate_limited",
        "news_search_temporarily_unavailable",
        "news_search_daily_cap",
    ],
)
def test_search_failures_keep_the_report_path_open(reason_code: str) -> None:
    fragments, steps = _collect(
        search_news=lambda *_args, **_kwargs: _result(
            state="failed",
            reason_code=reason_code,
        )
    )

    assert fragments == []
    assert steps[0]["실패"] == reason_code
    assert steps[0]["분류AI호출"] == 0
    assert steps[0]["본문읽기"] == 0


def test_classification_parse_failure_is_recorded_without_fetching_body() -> None:
    fetch_calls = 0

    def fetch_text(_url: str) -> str:
        nonlocal fetch_calls
        fetch_calls += 1
        return ARTICLE_BODY

    fragments, steps = _collect(
        search_news=lambda _query, **kwargs: _result(
            items=[_item()] if kwargs["start"] == 1 else []
        ),
        classify=lambda _prompt: "not-json",
        fetch_text=fetch_text,
    )

    assert fragments == []
    assert fetch_calls == 0
    assert steps[0]["분류AI호출"] == 1
    assert steps[0]["실패"] == real.NEWS_CLASSIFICATION_INVALID_CODE


def test_body_failure_is_recorded_after_one_classification() -> None:
    fragments, steps = _collect(
        search_news=lambda _query, **kwargs: _result(
            items=[_item()] if kwargs["start"] == 1 else []
        ),
        fetch_text=lambda _url: None,
    )

    assert fragments == []
    assert steps[0]["분류AI호출"] == 1
    assert steps[0]["본문읽기"] == 0
    assert steps[0]["실패"] == real.NEWS_BODY_FETCH_FAILED_CODE


def test_full_runtime_adds_news_after_official_preflight_before_composer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, "1")
    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    engine = FakeEngine()
    collector = _Collector([_official_result()])
    calls = _wire_runtime(monkeypatch, engine=engine)
    original_assess = real.assess_official_evidence

    def assess_with_portfolio_gap(result):
        base = original_assess(result)
        decision = replace(
            base.decision,
            status=GenerationGateStatus.STOP_INSUFFICIENT_EVIDENCE,
            outcome=ReportExecutionOutcome.INSUFFICIENT_EVIDENCE,
            ready_section_ids=tuple(
                section_id
                for section_id in base.decision.ready_section_ids
                if section_id != "portfolio"
            ),
            insufficient_section_ids=("portfolio",),
            reason_codes=("fixture_portfolio_gap",),
        )
        return replace(
            base,
            decision=decision,
            dart_partial_fallback=True,
            dart_partial_reason="insufficient_with_ready_sections",
        )

    monkeypatch.setattr(real, "assess_official_evidence", assess_with_portfolio_gap)

    search_calls = 0

    def search_news(_query: str, **kwargs: object) -> SimpleNamespace:
        nonlocal search_calls
        search_calls += 1
        return _result(items=[_item()] if kwargs["start"] == 1 else [])

    user_input, card = _request()
    result = real.RealPipeline(
        official_evidence_collector=collector,
        news_search=search_news,
        news_classify=lambda _prompt: (
            '{"items":[{"id":"news-001","sections":["portfolio"],'
            '"kind":"press_release"}]}'
        ),
        news_fetch_text=lambda _url: ARTICLE_BODY,
    ).run(user_input, card)

    assert result.outcome is real.Outcome.REPORT
    assert search_calls == 2
    assert len(calls.composers) == 1
    composer = calls.composers[0]
    news_fragments = [
        fragment
        for fragment in composer["frags"].values()
        if fragment.get("종류") == "news"
    ]
    assert len(news_fragments) == 1
    assert news_fragments[0]["발행처"] == "media.example"
    packets = real._full_section_evidence_packets(
        corp_id=composer["corp_id"],
        source_identity_digest=composer["source_identity_digest"],
        frags=composer["frags"],
        filing_meta=filing_meta_from_raw(composer["filing"]),
    )
    transported_news = [
        fragment
        for packet in packets.packets
        for fragment in packet.fragments
        if fragment.formal_source_kind == "news"
    ]
    assert len(transported_news) == 1
    assert transported_news[0].counts_toward_document_floor is False
    assert transported_news[0].source_publisher == "media.example"
    assert transported_news[0].document_date == "2026-09-01"
    news_step = next(
        step for step in composer["steps"] if step.get("step") == "5b_뉴스_수집"
    )
    assert news_step["분류AI호출"] == 1
    assert news_step["본문읽기"] == 1
    assert not any(
        step.get("step") == "6_수집_뉴스" for step in composer["steps"]
    )


def test_full_runtime_opens_news_when_official_web_documents_are_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """모든 장이 READY여도 공식 웹 문서가 0건이면 뉴스를 연다."""

    monkeypatch.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, "1")
    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    engine = FakeEngine()
    collector = _Collector([_dart_only_official_result()])
    calls = _wire_runtime(monkeypatch, engine=engine)

    search_calls = 0

    def search_news(_query: str, **kwargs: object) -> SimpleNamespace:
        nonlocal search_calls
        search_calls += 1
        return _result(items=[_item()] if kwargs["start"] == 1 else [])

    user_input, card = _request()
    result = real.RealPipeline(
        official_evidence_collector=collector,
        news_search=search_news,
        news_classify=lambda _prompt: (
            '{"items":[{"id":"news-001","sections":["portfolio"],'
            '"kind":"press_release"}]}'
        ),
        news_fetch_text=lambda _url: ARTICLE_BODY,
    ).run(user_input, card)

    assert result.outcome is real.Outcome.REPORT
    assert search_calls == real.NEWS_SEARCH_CALL_LIMIT
    composer = calls.composers[0]
    news_step = next(
        step for step in composer["steps"] if step.get("step") == "5b_뉴스_수집"
    )
    assert news_step["창"] == real.NEWS_WINDOW_LABEL_WEB_ZERO
    assert news_step["분류AI호출"] == 1
    assert [
        fragment
        for fragment in composer["frags"].values()
        if fragment.get("종류") == "news"
    ]


def test_full_runtime_keeps_web_documents_from_opening_news(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """공식 웹 문서가 있고 모든 장이 READY면 예전처럼 검색을 하지 않는다."""

    monkeypatch.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, "1")
    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    engine = FakeEngine()
    collector = _Collector([_official_result()])
    calls = _wire_runtime(monkeypatch, engine=engine)

    def unexpected(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("공식 웹 문서가 있으면 뉴스를 찾지 않습니다")

    user_input, card = _request()
    result = real.RealPipeline(
        official_evidence_collector=collector,
        news_search=unexpected,
        news_classify=unexpected,
        news_fetch_text=unexpected,
    ).run(user_input, card)

    assert result.outcome is real.Outcome.REPORT
    composer = calls.composers[0]
    news_step = next(
        step for step in composer["steps"] if step.get("step") == "5b_뉴스_수집"
    )
    assert news_step["창"] == real.NEWS_WINDOW_LABEL_DEFAULT
    assert news_step["검색호출"] == 0
    assert not any(
        fragment.get("종류") == "news"
        for fragment in composer["frags"].values()
    )


def test_full_runtime_off_never_calls_news_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    engine = FakeEngine()
    collector = _Collector([_official_result()])
    calls = _wire_runtime(monkeypatch, engine=engine)

    def unexpected(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("NEWS_INTAKE OFF에서 뉴스 의존성을 호출하면 안 됩니다")

    user_input, card = _request()
    result = real.RealPipeline(
        official_evidence_collector=collector,
        news_search=unexpected,
        news_classify=unexpected,
        news_fetch_text=unexpected,
    ).run(user_input, card)

    assert result.outcome is real.Outcome.REPORT
    assert len(calls.composers) == 1
    assert not any(
        step.get("step") == "5b_뉴스_수집"
        for step in calls.composers[0]["steps"]
    )
    assert not any(
        raw.get("종류") == "news"
        for raw in calls.composers[0]["frags"].values()
    )


def test_full_runtime_off_ignores_zero_web_documents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """스위치가 꺼져 있으면 공식 웹 문서가 0건이어도 뉴스를 찾지 않는다."""

    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    engine = FakeEngine()
    collector = _Collector([_dart_only_official_result()])
    calls = _wire_runtime(monkeypatch, engine=engine)

    def unexpected(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("NEWS_INTAKE OFF에서 뉴스 의존성을 호출하면 안 됩니다")

    user_input, card = _request()
    result = real.RealPipeline(
        official_evidence_collector=collector,
        news_search=unexpected,
        news_classify=unexpected,
        news_fetch_text=unexpected,
    ).run(user_input, card)

    assert result.outcome is real.Outcome.REPORT
    steps = calls.composers[0]["steps"]
    assert not any(step.get("step") == "5b_뉴스_수집" for step in steps)
    assert not any(
        raw.get("종류") == "news"
        for raw in calls.composers[0]["frags"].values()
    )


# ------------------------------- 본문 실패 사유 세분화와 본문 단계 (n12)


def test_본문_실패는_사유별로_제외에_남고_총괄_실패도_그_사유다() -> None:
    """예전에는 무슨 이유든 ``fetch_failed`` 하나로 뭉개져 고칠 곳을 못 찾았다."""

    fragments, steps = _collect(
        search_news=lambda _query, **kwargs: _result(
            items=[_item()] if kwargs["start"] == 1 else []
        ),
        fetch_text=lambda _url: NewsBodyFetchResult(
            reason_code=news_constants.EXCLUDED_FETCH_ROBOTS_BLOCKED
        ),
    )

    assert fragments == []
    assert steps[0]["본문읽기"] == 0
    assert steps[0]["제외"]["fetch_robots_blocked"] == 1
    assert steps[0]["실패"] == "fetch_robots_blocked"
    assert "fetch_failed" not in steps[0]["제외"]


def test_상태_코드는_그대로_제외_사유에_남는다() -> None:
    _fragments, steps = _collect(
        search_news=lambda _query, **kwargs: _result(
            items=[_item()] if kwargs["start"] == 1 else []
        ),
        fetch_text=lambda _url: NewsBodyFetchResult(reason_code="fetch_http_403"),
    )

    assert steps[0]["제외"]["fetch_http_403"] == 1
    assert steps[0]["실패"] == "fetch_http_403"


def test_본문을_어느_겹에서_얻었는지_steps에_남는다() -> None:
    _fragments, steps = _collect(
        search_news=lambda _query, **kwargs: _result(
            items=[_item()] if kwargs["start"] == 1 else []
        ),
        fetch_text=lambda _url: NewsBodyFetchResult(
            text=ARTICLE_BODY, stage=news_constants.BODY_STAGE_META_DESCRIPTION
        ),
    )

    assert steps[0]["본문단계"] == {"meta_description": 1}
    assert steps[0]["실패"] is None


def test_뉴스_단계_기록은_어느_경로에서도_같은_열쇠를_가진다() -> None:
    """열쇠가 경로마다 다르면 그 값을 읽는 화면·진단이 조용히 깨진다."""

    _fragments, 대상없음 = _collect(
        search_news=lambda _query, **_kwargs: _result(items=[]),
        section_ready=_ready(),
        official_web_documents=WEB_DOCUMENTS_PRESENT,
    )
    _fragments2, 정상 = _collect(
        search_news=lambda _query, **kwargs: _result(
            items=[_item()] if kwargs["start"] == 1 else []
        ),
    )

    assert set(대상없음[0]) == set(정상[0])
    assert "본문단계" in 대상없음[0]
