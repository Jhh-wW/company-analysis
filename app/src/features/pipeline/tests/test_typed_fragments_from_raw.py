"""장별 packet 없이도 조각의 typed 신원이 살아서 넘어가는지 본다.

★ 왜 필요한가 — 부분 보고서 갈래는 아홉 장·독립 문서 하한을 못 채워서 열린
  길이라 장별 packet을 만들 수 없다. 그래서 지금까지 작성기에 raw dict를 그대로
  넘겼고, raw dict 어댑터는 종류·원문·출처·문서명·원문위치만 읽어 발행처·문서일·
  문서 종류·의미 칸·장 선언을 통째로 버렸다. 보조 문서로 만드는 표는 소유 장을
  잃어 「부분 보고서에서만」 구조적으로 만들어지지 않았다.

★ 여기서 못 박는 것: 평면 변환기는 packet 빌더와 «같은» 검증을 지나고(같은 raw면
  같은 조각), 다른 것은 장별 묶음과 문서 하한뿐이다.

★ AI·네트워크 0회.
"""

from __future__ import annotations

import pytest

from src.core import news_intake_switch
from src.features.news_intake import constants as news_constants
from src.features.news_intake.models import NewsEvidenceFragment
from src.features.pipeline import real
from src.features.pipeline.evidence_transport import (
    RAW_EVIDENCE_SECTION_IDS_KEY,
    RAW_EVIDENCE_SLOT_IDS_KEY,
    EvidenceTransportError,
    build_section_evidence_packet_set,
    typed_fragments_from_raw,
)
from src.features.pipeline.tests.test_evidence_transport import (
    _CORP_ID,
    _FILING_META,
    _GENERATION,
    _all_legacy_frags,
    _typed_raw,
)
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID,
)
from src.shared.report_evidence.constants import SOURCE_KIND_NEWS
from src.shared.report_generation.models import exact_text_sha256

#: 뉴스 조각이 실린 문서의 원문 지문. 값 자체는 시험 표본이라 자리표시자다.
_NEWS_DOCUMENT_SHA256 = "c" * 64
_NEWS_TEXT = "가나다전자는 물류 제품군을 현장에 적용한다고 밝혔다."


@pytest.fixture(autouse=True)
def _reset_news_switch(monkeypatch: pytest.MonkeyPatch):
    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001
    monkeypatch.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, "1")
    yield
    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001


def _news_raw() -> dict[str, object]:
    """생산 경로와 «같은» 함수로 뉴스 raw 조각을 만든다.

    손으로 dict를 지어 넣으면 상류가 필드를 안 실어 주게 바뀌어도 이 시험이
    안 깨진다. 그래서 real.py의 정본 변환기를 그대로 부른다.
    """

    fragment = NewsEvidenceFragment(
        fragment_id="news-fragment-1",
        text=_NEWS_TEXT,
        text_sha256=exact_text_sha256(_NEWS_TEXT),
        section_ids=("portfolio",),
        supported_claim_slots=("portfolio:product_role",),
        document_id="news-article-1",
        published_on="2026-09-01",
        source_kind=SOURCE_KIND_NEWS,
        origin=news_constants.ORIGIN_NEWS_INTAKE,
        publisher="media.example",
        title="가나다전자 물류 제품군 적용",
        url="https://news.example/articles/1",
    )
    return real._news_raw_fragment(  # noqa: SLF001
        fragment,
        corp_id=_CORP_ID,
        collected_on="2026-09-05",
        document_content_sha256=_NEWS_DOCUMENT_SHA256,
    )


def _flat(frags):
    return typed_fragments_from_raw(
        corp_id=_CORP_ID, frags=frags, filing_meta=_FILING_META
    )


def _packets(frags):
    return build_section_evidence_packet_set(
        corp_id=_CORP_ID,
        source_generation_sha256=_GENERATION,
        frags=frags,
        filing_meta=_FILING_META,
    )


def _by_id(fragments) -> dict[str, object]:
    return {fragment.fragment_id: fragment for fragment in fragments}


# ══════════════════════════════════════════════════════════
# ① 보조(뉴스) 조각의 typed 메타가 평면 변환에서도 살아 온다
# ══════════════════════════════════════════════════════════


def test_뉴스조각은_평면변환에서도_매체와_발행일과_의미칸을_지킨다() -> None:
    """raw dict 어댑터가 버리던 다섯 값을 하나씩 본다."""

    frags = {**_all_legacy_frags(), 99: _news_raw()}

    fragment = _by_id(_flat(frags))["99"]

    assert fragment.formal_source_kind == SOURCE_KIND_NEWS
    assert fragment.source_publisher == "media.example"
    assert fragment.document_date == "2026-09-01"
    assert fragment.supported_claim_slots == ("portfolio:product_role",)
    assert fragment.counts_toward_document_floor is False
    assert fragment.location == "기사 본문 · news-fragment-1"


def test_공식_typed조각도_문서종류와_의미칸을_그대로_싣는다() -> None:
    frags = {**_all_legacy_frags(), 98: _typed_raw()}

    fragment = _by_id(_flat(frags))["98"]

    assert fragment.formal_source_kind == _typed_raw()["종류"]
    assert fragment.supported_claim_slots == ("portfolio:product_role",)
    assert fragment.counts_toward_document_floor is True


# ══════════════════════════════════════════════════════════
# ② legacy 조각은 packet 빌더와 같은 규칙을 그대로 따른다
# ══════════════════════════════════════════════════════════


def test_legacy조각은_packet빌더와_같은_규칙으로_변환된다() -> None:
    frags = _all_legacy_frags()

    flat = _by_id(_flat(frags))

    for public_id, raw in frags.items():
        fragment = flat[str(public_id)]
        # legacy는 종류→장 범위밖에 모른다. 의미 칸을 추측하지 않는 것이 규칙이다.
        assert fragment.supported_claim_slots == ()
        assert fragment.formal_source_kind == ""
        assert fragment.kind == raw["종류"]
        assert fragment.counts_toward_document_floor is True


# ══════════════════════════════════════════════════════════
# ③ 같은 raw면 packet 조각과 평면 조각이 «같은 것»이다
# ══════════════════════════════════════════════════════════


def test_같은_raw면_packet조각과_평면조각이_id별로_같다() -> None:
    """두 경로가 갈라지면 부분 보고서와 FULL이 다른 근거를 보게 된다."""

    frags = {**_all_legacy_frags(), 99: _news_raw(), 98: _typed_raw()}

    flat = _by_id(_flat(frags))
    packeted = _by_id(
        fragment
        for packet in _packets(frags).packets
        for fragment in packet.fragments
    )

    assert set(flat) == set(packeted)
    for fragment_id, fragment in flat.items():
        assert fragment == packeted[fragment_id], fragment_id


# ══════════════════════════════════════════════════════════
# ④ 두 함수의 «차이»는 장별 묶음과 하한뿐이다
# ══════════════════════════════════════════════════════════


def test_빈_장이_있어도_평면변환은_통과하고_packet빌더는_막는다() -> None:
    """부분 보고서가 열리는 바로 그 조건에서 갈라지는지 본다."""

    frags = {1: _typed_raw(), 2: _news_raw()}

    fragments = _flat(frags)
    assert [fragment.fragment_id for fragment in fragments] == ["1", "2"]

    with pytest.raises(EvidenceTransportError) as caught:
        _packets(frags)

    assert str(caught.value) == "아홉 장 중 근거 조각이 없는 장이 있습니다"
    assert caught.value.detail_code == FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID


def test_조각이_0개면_예외가_아니라_빈_튜플이다() -> None:
    """부분 보고서는 조각이 0개일 수도 있다 — 그건 계약 손상이 아니다."""

    assert _flat({}) == ()


# ══════════════════════════════════════════════════════════
# ⑤ 잘못된 조각은 packet 빌더와 같은 사유로 그대로 막는다
# ══════════════════════════════════════════════════════════


def test_잘못된_typed조각은_같은_사유코드로_막는다() -> None:
    broken = _typed_raw()
    broken[RAW_EVIDENCE_SECTION_IDS_KEY] = ("있을수없는장",)

    with pytest.raises(EvidenceTransportError) as caught:
        _flat({1: broken})

    assert caught.value.detail_code == FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID


def test_장과_의미칸_소유권이_어긋나면_막는다() -> None:
    broken = _typed_raw()
    broken[RAW_EVIDENCE_SLOT_IDS_KEY] = ("identity:corporate_identity",)

    with pytest.raises(EvidenceTransportError) as caught:
        _flat({1: broken})

    assert caught.value.detail_code == FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID


def test_공개번호가_양의_정수가_아니면_막는다() -> None:
    with pytest.raises(EvidenceTransportError) as caught:
        _flat({0: _typed_raw()})

    assert str(caught.value) == "공개 근거 번호는 양의 정수여야 합니다"


def test_회사식별자_형식이_틀리면_막는다() -> None:
    with pytest.raises(EvidenceTransportError) as caught:
        typed_fragments_from_raw(
            corp_id="틀린값", frags={1: _typed_raw()}, filing_meta=_FILING_META
        )

    assert str(caught.value) == "근거 transport 회사 식별자가 올바르지 않습니다"


def test_다른_회사의_typed조각은_섞을수_없다() -> None:
    other_company = _typed_raw(company_id="00000001")

    with pytest.raises(EvidenceTransportError) as caught:
        _flat({1: other_company})

    assert str(caught.value) == "다른 회사의 typed 근거를 섞을 수 없습니다"


def test_같은_origin을_두_공개번호로_만들수_없다() -> None:
    """묶음 단위 중복 검사가 평면 변환에서도 살아 있는지 본다."""

    with pytest.raises(EvidenceTransportError) as caught:
        _flat({1: _typed_raw(), 2: _typed_raw()})

    assert str(caught.value) == (
        "같은 typed origin 조각을 둘 이상의 공개 번호로 만들 수 없습니다"
    )
