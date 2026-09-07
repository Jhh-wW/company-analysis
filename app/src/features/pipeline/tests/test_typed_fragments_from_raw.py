"""장별 packet 없이도 조각의 typed 신원이 살아서 넘어가는지 본다.

★ 왜 필요한가 — 부분 보고서 갈래는 아홉 장·독립 문서 하한을 못 채워서 열린
  길이라 장별 packet을 만들 수 없다. 그래서 지금까지 작성기에 raw dict를 그대로
  넘겼고, raw dict 어댑터는 종류·원문·출처·문서명·원문위치만 읽어 발행처·문서일·
  문서 종류·의미 칸·장 선언을 통째로 버렸다. 보조 문서로 만드는 표는 소유 장을
  잃어 「부분 보고서에서만」 구조적으로 만들어지지 않았다.

★ 여기서 못 박는 것 두 가지.
  ① 조각 하나의 검증은 packet 빌더와 «같은» 검증을 지난다(같은 raw면 같은 조각).
  ② 그런데 «묶음 전체»를 포기하지는 않는다. 조각 하나가 계약을 못 채우면 그
     조각만 옛 어댑터 모양으로 싣고 사유를 세며, 같은 묶음의 정상 조각은 typed
     신원을 그대로 지킨다. 이 관용이 없던 판에서는 「감사보고서 재무」 조각 하나
     때문에 묶음 전체가 raw로 되돌아가 보도표가 0건이 됐다(운영 실측).

★ AI·네트워크 0회.
"""

from __future__ import annotations

import pytest

from src.core import news_intake_switch
from src.features.news_intake import constants as news_constants
from src.features.news_intake.models import NewsEvidenceFragment
from src.features.pipeline import real
from src.features.pipeline.evidence_transport import (
    CARRIED_RAW_KIND_MAX_LENGTH,
    CARRIED_RAW_KIND_OUT_OF_FORM,
    CARRIED_RAW_REASON_MAX_LENGTH,
    CARRIED_RAW_UNKNOWN_KIND,
    RAW_EVIDENCE_SECTION_IDS_KEY,
    RAW_EVIDENCE_SLOT_IDS_KEY,
    EvidenceTransportError,
    FlatFragmentConversion,
    _carried_raw_kind_label,
    _carried_raw_reason,
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
from src.shared.report_evidence.constants import (
    FORMAL_DOCUMENT_SOURCE_KINDS,
    SOURCE_KIND_NEWS,
    SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE,
    SUPPLEMENTARY_DOCUMENT_SOURCE_KINDS,
)
from src.shared.report_evidence.legacy_fragment_kinds import LEGACY_FRAGMENT_KINDS
from src.shared.report_evidence.source_kind_policy import (
    FORMAL_DOCUMENT_WRITER_TRUST_BY_SOURCE_KIND,
)
from src.shared.report_generation.models import exact_text_sha256

#: 뉴스 조각이 실린 문서의 원문 지문. 값 자체는 시험 표본이라 자리표시자다.
_NEWS_DOCUMENT_SHA256 = "c" * 64
_NEWS_TEXT = "가나다전자는 물류 제품군을 현장에 적용한다고 밝혔다."

#: 정본 등록표에 «없는» 종류. 운영에서 실제로 「감사보고서 재무」가 이 자리에
#: 있었다. 시험은 특정 이름이 아니라 「모르는 이름이 와도 묶음을 안 버린다」를
#: 지켜야 하므로 지어낸 이름을 쓴다.
_UNREGISTERED_KIND = "미등록종류"
_OTHER_UNREGISTERED_KIND = "또다른미등록"
#: ★ 사유 열쇠는 「종류: 메시지」다. 메시지 하나(예: 「등록되지 않은 수집 조각
#:   종류입니다」)에 스무 가지 넘는 생산자가 걸려서, 메시지만 보면 운영에서
#:   «어느 생산자의 조각»이 걸렸는지 못 가른다.
#: 문자열은 생산 상수를 import하지 않고 리터럴로 적는다. 상수를 끌어다 쓰면
#: 문구가 조용히 바뀌어도 시험이 같이 따라가 아무것도 못 지킨다.
_UNREGISTERED_REASON = "미등록종류: 등록되지 않은 수집 조각 종류입니다"
_OTHER_UNREGISTERED_REASON = "또다른미등록: 등록되지 않은 수집 조각 종류입니다"
#: 정본 문서 종류 이름도 리터럴로 고정한다 — 이름이 바뀌면 실행 기록을 읽는
#: 사람의 눈에 먼저 띄어야 한다.
_TYPED_KIND = "official_ir_pdf"
#: packet 빌더가 «예외로» 내는 문장에는 종류 접두가 없다. 평면 변환기의 사유
#: 열쇠와 자리가 달라서다.
_DUPLICATE_ORIGIN_MESSAGE = (
    "같은 typed origin 조각을 둘 이상의 공개 번호로 만들 수 없습니다"
)
_DUPLICATE_ORIGIN_REASON = f"{_TYPED_KIND}: {_DUPLICATE_ORIGIN_MESSAGE}"
#: 형식을 벗어난 종류가 실리는 사유 열쇠. 종류 자리가 통째로 자리표시자로
#: 바뀌므로, 서로 다른 «형식 밖» 값들은 한 칸에 모여 세어진다.
_OUT_OF_FORM_REASON = "(형식 밖 종류): 등록되지 않은 수집 조각 종류입니다"
#: 이 모듈에서 가장 긴 닫힌 사유 메시지(쉰두 자). 가장 긴 정본 종류 이름
#: (서른다섯 자)과 만나면 여든아홉 자가 되어 상한에 걸린다.
_LONGEST_TRANSPORT_MESSAGE = (
    "typed 근거의 도메인 attestation Source ID와 exact 원문이 갈렸습니다"
)
#: 정본 종류 이름의 수 하한(2026-09-07 실측 31개 = legacy 21 + 공식·보조 문서
#: 종류). 표를 못 읽어 «빈 묶음»을 도는 시험이 조용히 통과하지 않게 한다.
_CANONICAL_KIND_COUNT_FLOOR = 31


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


def _legacy_raw(kind: str, *, text: str = "") -> dict[str, object]:
    """옛 수집기 모양의 조각 하나를 만든다(종류·원문뿐)."""

    return {"종류": kind, "원문": text or f"가나다전자의 {kind} 공식 근거다."}


def _flat(frags) -> FlatFragmentConversion:
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

    conversion = _flat(frags)
    fragment = _by_id(conversion.fragments)["99"]

    assert fragment.formal_source_kind == SOURCE_KIND_NEWS
    assert fragment.source_publisher == "media.example"
    assert fragment.document_date == "2026-09-01"
    assert fragment.supported_claim_slots == ("portfolio:product_role",)
    assert fragment.counts_toward_document_floor is False
    assert fragment.location == "기사 본문 · news-fragment-1"
    assert conversion.supplementary_count == 1


def test_공식_typed조각도_문서종류와_의미칸을_그대로_싣는다() -> None:
    frags = {**_all_legacy_frags(), 98: _typed_raw()}

    conversion = _flat(frags)
    fragment = _by_id(conversion.fragments)["98"]

    assert fragment.formal_source_kind == _typed_raw()["종류"]
    assert fragment.supported_claim_slots == ("portfolio:product_role",)
    assert fragment.counts_toward_document_floor is True
    # 공식 문서는 보조가 아니다 — 「보조」 수가 문서 하한 계산과 갈리면 안 된다.
    assert conversion.supplementary_count == 0


# ══════════════════════════════════════════════════════════
# ② legacy 조각은 packet 빌더와 같은 규칙을 그대로 따른다
# ══════════════════════════════════════════════════════════


def test_legacy조각은_packet빌더와_같은_규칙으로_변환된다() -> None:
    frags = _all_legacy_frags()

    conversion = _flat(frags)
    flat = _by_id(conversion.fragments)

    for public_id, raw in frags.items():
        fragment = flat[str(public_id)]
        # legacy는 종류→장 범위밖에 모른다. 의미 칸을 추측하지 않는 것이 규칙이다.
        assert fragment.supported_claim_slots == ()
        assert fragment.formal_source_kind == ""
        assert fragment.kind == raw["종류"]
        assert fragment.counts_toward_document_floor is True
    assert conversion.legacy_count == len(frags)
    assert conversion.typed_count == 0
    assert conversion.carried_raw_count == 0


def test_감사보고서_재무조각은_legacy로_변환되고_원형으로_남지_않는다() -> None:
    """운영에서 묶음 전체를 되돌린 바로 그 종류를 못 박는다.

    비상장 외감 회사는 사업보고서가 없어 이 종류가 유일한 숫자 근거다. 정본
    등록표에 없으면 이 조각 하나가 「등록되지 않은 종류」로 거절된다.
    """

    frags = {1: _legacy_raw("사업내용"), 2: _legacy_raw("감사보고서 재무")}

    conversion = _flat(frags)

    assert conversion.carried_raw_count == 0
    assert conversion.carried_raw_reasons == ()
    assert conversion.legacy_count == 2
    audit = _by_id(conversion.fragments)["2"]
    assert audit.kind == "감사보고서 재무"
    assert audit.formal_source_kind == ""
    assert audit.supported_claim_slots == ()


# ══════════════════════════════════════════════════════════
# ③ 같은 raw면 packet 조각과 평면 조각이 «같은 것»이다
# ══════════════════════════════════════════════════════════


def test_같은_raw면_packet조각과_평면조각이_id별로_같다() -> None:
    """두 경로가 갈라지면 부분 보고서와 FULL이 다른 근거를 보게 된다."""

    frags = {**_all_legacy_frags(), 99: _news_raw(), 98: _typed_raw()}

    flat = _by_id(_flat(frags).fragments)
    packeted = _by_id(
        fragment
        for packet in _packets(frags).packets
        for fragment in packet.fragments
    )

    assert set(flat) == set(packeted)
    for fragment_id, fragment in flat.items():
        assert fragment == packeted[fragment_id], fragment_id


# ══════════════════════════════════════════════════════════
# ④ 두 함수의 «차이»는 장별 묶음·하한과 조각별 관용이다
# ══════════════════════════════════════════════════════════


def test_빈_장이_있어도_평면변환은_통과하고_packet빌더는_막는다() -> None:
    """부분 보고서가 열리는 바로 그 조건에서 갈라지는지 본다."""

    frags = {1: _typed_raw(), 2: _news_raw()}

    conversion = _flat(frags)
    assert [fragment.fragment_id for fragment in conversion.fragments] == ["1", "2"]

    with pytest.raises(EvidenceTransportError) as caught:
        _packets(frags)

    assert str(caught.value) == "아홉 장 중 근거 조각이 없는 장이 있습니다"
    assert caught.value.detail_code == FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID


def test_조각이_0개면_예외가_아니라_빈_결과다() -> None:
    """부분 보고서는 조각이 0개일 수도 있다 — 그건 계약 손상이 아니다."""

    conversion = _flat({})

    assert conversion.fragments == ()
    assert conversion.typed_count == 0
    assert conversion.legacy_count == 0
    assert conversion.carried_raw_count == 0
    assert conversion.skipped_empty_count == 0
    assert conversion.carried_raw_reasons == ()


# ══════════════════════════════════════════════════════════
# ⑤ 조각 하나가 계약을 어겨도 묶음을 버리지 않는다
# ══════════════════════════════════════════════════════════


def test_미등록_종류_하나가_같은_묶음의_typed조각을_망치지_않는다() -> None:
    """이 시험이 없던 판에서 보도표가 구조적으로 0건이었다.

    세 조각이 한 묶음에 있다 — legacy 하나, 모르는 종류 하나, typed 뉴스 하나.
    모르는 종류 하나 때문에 뉴스의 typed 신원까지 잃으면 안 된다.
    """

    frags = {
        1: _legacy_raw("사업내용"),
        2: _legacy_raw(_UNREGISTERED_KIND),
        3: _news_raw(),
    }

    conversion = _flat(frags)
    by_id = _by_id(conversion.fragments)

    # 세 조각 모두 결과에 있다 — 하나도 버려지지 않았다.
    assert sorted(by_id) == ["1", "2", "3"]
    news = by_id["3"]
    assert news.formal_source_kind == SOURCE_KIND_NEWS
    assert news.source_publisher == "media.example"
    assert news.document_date == "2026-09-01"
    assert news.supported_claim_slots == ("portfolio:product_role",)
    # 모르는 종류는 옛 어댑터 모양 그대로 실린다 — 이름은 지키고 신원은 비운다.
    carried = by_id["2"]
    assert carried.kind == _UNREGISTERED_KIND
    assert carried.formal_source_kind == ""
    assert carried.supported_claim_slots == ()

    assert conversion.typed_count == 1
    assert conversion.legacy_count == 1
    assert conversion.carried_raw_count == 1
    assert conversion.skipped_empty_count == 0
    assert conversion.carried_raw_reasons == ((_UNREGISTERED_REASON, 1),)


def test_실패조각이_있어도_성공조각의_결과는_그대로다() -> None:
    """관용이 «성공한 조각»의 모양을 바꾸지 않는지 본다."""

    clean = {1: _legacy_raw("사업내용"), 3: _news_raw()}
    mixed = {**clean, 2: _legacy_raw(_UNREGISTERED_KIND)}

    baseline = _by_id(_flat(clean).fragments)
    tolerant = _by_id(_flat(mixed).fragments)

    assert set(baseline) < set(tolerant)
    for fragment_id, fragment in baseline.items():
        assert tolerant[fragment_id] == fragment, fragment_id


def test_같은_origin을_두_공개번호가_주장하면_둘째만_원형으로_남는다() -> None:
    """묶음 단위 중복 검사는 살아 있되, 첫째 조각까지 잃지는 않는다."""

    frags = {1: _typed_raw(), 2: _typed_raw()}

    conversion = _flat(frags)
    by_id = _by_id(conversion.fragments)

    assert by_id["1"].formal_source_kind == _typed_raw()["종류"]
    assert by_id["1"].supported_claim_slots == ("portfolio:product_role",)
    assert by_id["2"].formal_source_kind == ""
    assert conversion.typed_count == 1
    assert conversion.carried_raw_count == 1
    assert conversion.carried_raw_reasons == ((_DUPLICATE_ORIGIN_REASON, 1),)

    # packet 빌더는 여전히 엄격하다 — FULL 출고 계약은 하나도 안 봐준다.
    with pytest.raises(EvidenceTransportError) as caught:
        _packets(frags)

    assert str(caught.value) == _DUPLICATE_ORIGIN_MESSAGE


def test_실패한_조각의_origin은_뒤_조각의_typed신원을_뺏지_않는다() -> None:
    """실패한 조각이 origin을 «선점»하면 뒤 조각이 억울하게 거절된다.

    도달 경로 — typed 조각은 typed 메타 단계에서 origin을 묶음 상태에 등록한
    뒤, 그 «다음» 단계인 ``문서명`` 형식 검사에서 터질 수 있다(정식 typed
    조각은 ``문서명``을 typed 메타 단계에서 읽지 않는다). 실패한 조각의 origin이
    묶음 상태에 남으면 같은 origin을 공유하는 뒤 조각이 「같은 typed origin」
    으로 잘못 거절돼, 고칠 것 없는 조각의 typed 신원까지 사라진다.

    변환기는 사본을 넘겨 «성공했을 때만» 되돌려 받는 방식으로 이걸 막는데,
    그 사본을 별칭으로 되돌려도 다른 시험은 전부 통과했다(2026-09-07 실측).
    """

    orphaned = _typed_raw()
    # 상류가 문서명을 목록으로 실어 보낸 모양. origin 등록 «뒤»에 터지는 자리다.
    orphaned["문서명"] = ["가나다전자 제품 소개"]

    conversion = _flat({1: orphaned, 2: _typed_raw()})
    by_id = _by_id(conversion.fragments)

    # 두 조각 모두 남고, 뒤 조각은 typed 신원을 그대로 지킨다.
    assert sorted(by_id) == ["1", "2"]
    assert by_id["1"].formal_source_kind == ""
    assert by_id["2"].formal_source_kind == _TYPED_KIND
    assert by_id["2"].supported_claim_slots == ("portfolio:product_role",)
    assert conversion.typed_count == 1
    assert conversion.carried_raw_count == 1
    # 사유는 「중복 origin」이 아니라 «진짜 원인»인 문서명 형식이어야 한다.
    assert conversion.carried_raw_reasons == (
        (f"{_TYPED_KIND}: 근거 조각의 문서명 형식이 올바르지 않습니다", 1),
    )


def test_두_조각이_실패하면_사유별로_따로_센다() -> None:
    """사유가 하나로 뭉치면 운영에서 원인을 못 가른다."""

    frags = {
        1: _legacy_raw(_UNREGISTERED_KIND),
        2: _legacy_raw(_OTHER_UNREGISTERED_KIND),
        3: _typed_raw(),
        4: _typed_raw(),
    }

    conversion = _flat(frags)

    assert conversion.carried_raw_count == 3
    # ★ 사유 메시지는 두 조각이 같지만 종류가 달라 «따로» 센다. 이게 열쇠에
    #   종류를 넣은 이유다 — 합쳐 세면 어느 생산자가 걸렸는지 사라진다.
    assert conversion.carried_raw_reasons == (
        (_DUPLICATE_ORIGIN_REASON, 1),
        (_OTHER_UNREGISTERED_REASON, 1),
        (_UNREGISTERED_REASON, 1),
    )
    # 사유 문자열은 정렬돼 있어야 실행 기록 비교가 흔들리지 않는다.
    assert [reason for reason, _count in conversion.carried_raw_reasons] == sorted(
        reason for reason, _count in conversion.carried_raw_reasons
    )


def test_잘못된_typed조각은_묶음을_막지_않고_원형으로_실린다() -> None:
    """typed 메타가 깨진 조각도 버리지 않는다 — 원문은 여전히 근거다."""

    broken = _typed_raw()
    broken[RAW_EVIDENCE_SECTION_IDS_KEY] = ("있을수없는장",)

    conversion = _flat({1: broken, 2: _legacy_raw("사업내용")})

    assert conversion.carried_raw_count == 1
    assert conversion.legacy_count == 1
    assert _by_id(conversion.fragments)["1"].formal_source_kind == ""
    assert conversion.carried_raw_reasons == (
        (f"{_TYPED_KIND}: typed 근거에 알 수 없는 장 식별자가 있습니다", 1),
    )


def test_장과_의미칸_소유권이_어긋나면_원형으로_실린다() -> None:
    broken = _typed_raw()
    broken[RAW_EVIDENCE_SLOT_IDS_KEY] = ("identity:corporate_identity",)

    conversion = _flat({1: broken})

    assert conversion.carried_raw_count == 1
    assert conversion.carried_raw_reasons == (
        (f"{_TYPED_KIND}: typed 근거의 장과 의미 칸 소유권이 일치하지 않습니다", 1),
    )


def test_다른_회사의_typed조각도_원형으로만_실린다() -> None:
    """★ 여기서 지키는 것은 「typed 신원을 안 준다」이지 「버린다」가 아니다.

    회사가 다른 조각의 typed 신원을 인정하면 남의 회사 근거가 우리 장을
    소유하게 된다. 그건 막는다. 다만 원문 자체는 이 관용 이전에도 raw dict
    되돌아가기로 작성기에 그대로 갔으므로, 원형 유지가 더 나빠진 것은 아니다.
    """

    conversion = _flat({1: _typed_raw(company_id="00000001")})
    fragment = _by_id(conversion.fragments)["1"]

    assert conversion.carried_raw_count == 1
    assert conversion.typed_count == 0
    assert fragment.formal_source_kind == ""
    assert fragment.supported_claim_slots == ()
    assert conversion.carried_raw_reasons == (
        (f"{_TYPED_KIND}: 다른 회사의 typed 근거를 섞을 수 없습니다", 1),
    )


def test_원형유지_사유는_여든자를_넘지_않는다() -> None:
    """실행 기록·로그에 긴 문장이 통째로 실리지 않게 한다."""

    assert CARRIED_RAW_REASON_MAX_LENGTH == 80
    broken = _typed_raw(source_kind="관측된적없는문서종류")

    conversion = _flat({1: broken})

    assert conversion.carried_raw_count == 1
    assert conversion.carried_raw_reasons == (
        ("관측된적없는문서종류: 등록되지 않은 수집 조각 종류입니다", 1),
    )


def test_종류가_아주_길면_사유에_안_싣고_자리표시자로_바꾼다() -> None:
    """종류는 생산자가 정하는 값이라 길이를 우리가 못 정한다.

    2026-09-07 이전에는 백 자짜리 종류가 사유에 그대로 실렸다가 여든 자에서
    잘렸다. 잘라 실으면 앞부분(URL이면 host)이 그대로 남으므로, 이제는 길이가
    상한을 넘는 순간 통째로 자리표시자로 바꾼다.
    """

    conversion = _flat({1: _typed_raw(source_kind="가" * 100)})

    assert conversion.carried_raw_count == 1
    assert conversion.carried_raw_reasons == ((_OUT_OF_FORM_REASON, 1),)


def test_종류가_비면_자리표시자_접두가_붙는다() -> None:
    """빈 접두를 두면 「: 메시지」가 되어 안 적은 건지 빈 건지 못 가른다."""

    conversion = _flat(
        {1: {"종류": "   ", "원문": "가나다전자의 종류 없는 근거다."}}
    )

    assert CARRIED_RAW_UNKNOWN_KIND == "(종류 없음)"
    assert conversion.carried_raw_count == 1
    assert conversion.carried_raw_reasons == (
        ("(종류 없음): 근거 조각의 종류 형식이 올바르지 않습니다", 1),
    )
    # 종류를 못 읽어도 원문은 근거다 — 버리지 않는다.
    assert _by_id(conversion.fragments)["1"].text == (
        "가나다전자의 종류 없는 근거다."
    )


# ══════════════════════════════════════════════════════════
# ⑥ 원문이 빈 조각은 옛 어댑터와 같은 규칙으로 건너뛴다
# ══════════════════════════════════════════════════════════


def test_원문이_빈_조각은_건너뛰고_따로_센다() -> None:
    """인용해도 대조할 원문이 없으면 근거가 못 된다(옛 어댑터와 같은 규칙)."""

    frags = {
        1: _legacy_raw("사업내용"),
        2: {"종류": "사업내용", "원문": "   "},
        3: {"종류": _UNREGISTERED_KIND, "원문": ""},
    }

    conversion = _flat(frags)

    assert [fragment.fragment_id for fragment in conversion.fragments] == ["1"]
    assert conversion.skipped_empty_count == 2
    assert conversion.carried_raw_count == 0
    assert conversion.carried_raw_reasons == ()


# ══════════════════════════════════════════════════════════
# ⑦ 묶음 자체가 잘못된 입력은 여전히 예외다
# ══════════════════════════════════════════════════════════


def test_공개번호가_양의_정수가_아니면_막는다() -> None:
    with pytest.raises(EvidenceTransportError) as caught:
        _flat({0: _typed_raw()})

    assert str(caught.value) == "공개 근거 번호는 양의 정수여야 합니다"
    assert caught.value.detail_code == FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID


def test_회사식별자_형식이_틀리면_막는다() -> None:
    with pytest.raises(EvidenceTransportError) as caught:
        typed_fragments_from_raw(
            corp_id="틀린값", frags={1: _typed_raw()}, filing_meta=_FILING_META
        )

    assert str(caught.value) == "근거 transport 회사 식별자가 올바르지 않습니다"


def test_조각_묶음이_Mapping이_아니면_막는다() -> None:
    with pytest.raises(EvidenceTransportError) as caught:
        _flat([_typed_raw()])

    assert str(caught.value) == "근거 transport 조각 묶음이 비었습니다"


def test_조각이_Mapping이_아니면_막는다() -> None:
    """이건 한 조각의 품질 문제가 아니라 호출자의 자료형 계약 위반이다."""

    with pytest.raises(EvidenceTransportError) as caught:
        _flat({1: "조각이 아니라 문자열"})

    assert str(caught.value) == "근거 조각은 Mapping이어야 합니다"


# ══════════════════════════════════════════════════════════
# ⑧ 사유 열쇠에는 «형식이 맞는» 종류 이름만 실린다
# ══════════════════════════════════════════════════════════
#
# ★ 왜 — 이 관용 경로는 애초에 「정본이 모르는 종류」를 위해 열린 길인데,
#   정작 그 종류 필드만 아무 검사 없이 실행 기록(``v2_조각_typed전달.
#   원형유지_사유별``)과 경고 로그의 열쇠가 됐다. 사유 열쇠는 «어느 생산자가
#   걸렸나»를 세는 자리지 URL·자유 문장을 옮기는 자리가 아니다.


def test_종류에_URL이_오면_사유열쇠에_주소가_실리지_않는다() -> None:
    """상류가 종류 칸에 주소를 담아 보내도 실행 기록에는 남지 않는다."""

    url_kind = "https://news.example/articles/1"

    conversion = _flat({1: {"종류": url_kind, "원문": _NEWS_TEXT}})

    assert conversion.carried_raw_count == 1
    assert conversion.carried_raw_reasons == ((_OUT_OF_FORM_REASON, 1),)
    # ★ 여기서 좁히는 것은 «실행 기록의 열쇠»뿐이다. 조각 자체는 이 관용 이전과
    #   똑같이 옛 어댑터 규칙으로 실린다 — 원문은 여전히 근거이기 때문이다.
    assert _by_id(conversion.fragments)["1"].kind == url_kind


def test_종류에_줄바꿈이나_콜론이_섞이면_자리표시자로_바꾼다() -> None:
    """열쇠가 「종류: 메시지」라서 콜론·줄바꿈이 섞이면 읽는 자리가 어긋난다."""

    conversion = _flat(
        {1: {"종류": "사업내용\n담당: 아무개", "원문": "가나다전자의 근거 문장이다."}}
    )

    assert conversion.carried_raw_count == 1
    assert conversion.carried_raw_reasons == ((_OUT_OF_FORM_REASON, 1),)


def test_종류_길이_상한은_마흔자이고_경계까지는_그대로_싣는다() -> None:
    """상한을 넘는 자유 문장만 가리고, 경계 안의 이름은 그대로 둔다."""

    assert CARRIED_RAW_KIND_MAX_LENGTH == 40
    assert CARRIED_RAW_KIND_OUT_OF_FORM == "(형식 밖 종류)"
    at_limit = "가" * 40
    over_limit = "가" * 41

    kept = _flat({1: _legacy_raw(at_limit)})
    masked = _flat({1: _legacy_raw(over_limit)})

    assert kept.carried_raw_reasons == (
        (f"{at_limit}: 등록되지 않은 수집 조각 종류입니다", 1),
    )
    assert masked.carried_raw_reasons == ((_OUT_OF_FORM_REASON, 1),)


def test_정본_종류_이름은_모두_사유열쇠에_그대로_실린다() -> None:
    """정제가 정본 이름을 가리면 실행 기록이 쓸모없어진다.

    상한(마흔 자)·허용 글자는 실측으로 정한 값이라, 새 정본 종류 이름이 그
    범위를 벗어나면 여기서 먼저 깨져 사람이 값을 다시 정하게 만든다.
    """

    canonical_kinds = (
        set(LEGACY_FRAGMENT_KINDS)
        | set(FORMAL_DOCUMENT_SOURCE_KINDS)
        | set(SUPPLEMENTARY_DOCUMENT_SOURCE_KINDS)
        | set(FORMAL_DOCUMENT_WRITER_TRUST_BY_SOURCE_KIND)
    )

    # 표를 못 읽어 «빈 묶음»을 도는 시험은 아무것도 안 지킨다.
    assert len(canonical_kinds) >= _CANONICAL_KIND_COUNT_FLOOR
    for kind in sorted(canonical_kinds):
        assert _carried_raw_kind_label(kind) == kind, kind


def test_사유가_여든자를_넘으면_여전히_자르고_잘림표시를_붙인다() -> None:
    """종류를 형식으로 좁혀도 «메시지가 긴» 사유는 그대로 상한에 걸린다.

    가장 긴 정본 종류 이름(서른다섯 자)과 이 모듈의 가장 긴 닫힌 메시지(쉰두
    자)가 만나면 여든아홉 자가 된다. 끝에서 끝까지 재현 대신 사유를 «만드는»
    자리를 직접 부르는 이유는, 이 조합을 조각으로 만들려면 도메인 attestation
    fixture가 따로 필요해서다.
    """

    reason = _carried_raw_reason(
        EvidenceTransportError(
            _LONGEST_TRANSPORT_MESSAGE,
            detail_code=FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID,
        ),
        kind=SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE,
    )

    assert len(reason) == 80
    assert reason.startswith(f"{SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE}: ")
    # 잘렸다는 사실이 보여야 읽는 사람이 「이게 전부」라고 오해하지 않는다.
    assert reason.endswith("…")
